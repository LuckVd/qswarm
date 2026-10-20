"""kernel.cells：存款聚合器——账本 deposit 事件 → 口碑格子（cells 表）。

``agg_cells(ledger, as_of)`` 是 ``cells`` 物化视图的重建函数（SPEC-1 §2「可随时
DROP 重建」+ §6-6 重放等价）：从账本读出全部 ``deposit`` 事件，按
``(agent, role, node)`` 折叠出 pos_w / neg_w / n_eff / n_eff_s2s3 / status，
整表重写 ``cells``；同时返回 :class:`CellView` 供路由/晋升/仿真侧做层级收缩
（C*）、挥发地板与 LOO 基线读取（SPEC-4 §0：仿真直接复用内核实现）。
只需要视图、不得落表的只读调用（诊断/评估入口）走 :func:`view_cells`
——同一折叠与 status 重放实现，不触发 cells 物理写。

机制语义（设计文档 v0.3.4，逐条可溯源）：
- 指数挥发 §6.4：``w_eff = w·2^(−age/T_half)``；origin=prod 用
  ``decay.T_HALF_DAYS``（默认 30d），origin=exam 用 ``decay.T_HALF_DAYS_EXAM``
  （默认 15d，§8.4：考试信息保鲜期短于生产表现）。晚于 as_of 的事件不属于该
  时点，整笔剔除（age<0 不外推放大）。
- n_eff 恒等式 §6.1：``n_eff ≡ 2×(pos_w+neg_w)``——每笔存款对 n_eff 贡献恰
  ``2×w_eff``（S3: 2/1.0、S2: 1/0.5、S1: 0.1/0.05 当量，λ 与 N 从此同量纲）。
  ``n_eff_s2s3`` 只累计 S2/S3 存款：S2=1、S3=2 当量 ⇔ 同为 ``2×w_eff``（按
  规范权重 S2=0.5、S3=1.0 折算），S1 记 0；即晋升硬门槛 n_eff(S2+S3)（§7.3）
  的量，且 ``n_eff_s2s3 ≡ 2×(S2/S3 存款权和)``。
- 挥发地板 §5.1：``pos_w+neg_w < ε``（decay.EPS_FLOOR，默认 0.5 个 S3 当量）时
  C_raw 无定义，该层 C* 直接取父层 C*——防止全衰减后 0/0。地板是**读取期**
  语义：cells 表仍如实存加权质量。
- 层级收缩 §5.1：``C*(cell) = (n_eff·C_raw + λ·C*_parent)/(n_eff+λ)``，
  λ = shrink.LAMBDA（默认 5）。链条 type→family→(agent, role)→(agent)；
  (agent) 之上以中性先验 ROOT_PRIOR=0.5 收底——新 agent/新类型总有父层可用，
  永不死锁（§5.1 冷启动解）。(agent, role) / (agent) 层是同一批存款的粗化
  聚合：每笔存款在每层只计一次，不随节点前缀重复。
- LOO 基线 §5.1/§7.3：:meth:`CellView.agg_baseline` = 同 ``(role, node)`` 全体
  agent 剔除被评 agent 自身存款（对照 §7.3 promote_scan 伪码
  ``agg(ledger, ALL_minus(agent), role, node, W)``），防小编织自证。
- status 重放：cells.status 由 promotion/demotion 事件按 seq 序重放
  （SPEC-1 §2「不存在无账本事件的状态变更」）：promotion 取 to_status，升到
  active 记 activated_at；demotion 按 reason 映射（dormant→dormant，
  decay/eliminate→shadow）。无事件的格子默认 shadow；未干过活（无 deposit）
  的键不建格（§7.1 lazy allocation）。

写口纪律（SPEC-1 §6-2）：ledger/cells 只允许 kernel 包写——本模块即 cells
写口本体；蜂与仿真器只读（CellView）或经 KernelLedger.append 落事件。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Mapping

from qswarm.contracts import (
    CellStatus,
    DecayParams,
    DepositOrigin,
    LedgerKind,
    ParamsConfig,
    SignalTier,
    load_params,
)

from .ledger import KernelLedger, _coerce_utc, iso_utc

__all__ = [
    "ROOT_PRIOR",
    "Baseline",
    "CellAgg",
    "CellKey",
    "CellView",
    "agg_cells",
    "decay_factor",
    "node_prefixes",
    "view_cells",
]

#: 收缩链根先验：(agent) 层之上的 C* 收底值（§5.1 未指定；取 C_raw 值域中点，
#: 冷启动中性——无任何存款时 C* = 0.5）。
ROOT_PRIOR = 0.5

_SECONDS_PER_DAY = 86400.0

#: 计入 n_eff_s2s3 的信号层（§6.1：S2=1、S3=2 当量；S1 不计入晋升硬门槛）。
_S2S3_TIERS = frozenset({SignalTier.S2, SignalTier.S3})


# ── 挥发（§6.4 / §8.4）──────────────────────────────────────────

def decay_factor(
    origin: DepositOrigin | str,
    age_s: float,
    *,
    decay: DecayParams,
) -> float:
    """挥发因子 ``2^(−age/T_half)``：origin=exam 走 T_HALF_DAYS_EXAM，其余走 T_HALF_DAYS。

    age_s 为存款龄期（秒），调用方须保证 ≥0（agg_cells 剔除晚于 as_of 的事件）。
    """
    t_half_days = (
        decay.T_HALF_DAYS_EXAM
        if DepositOrigin(origin) is DepositOrigin.EXAM
        else decay.T_HALF_DAYS
    )
    return 2.0 ** (-age_s / (t_half_days * _SECONDS_PER_DAY))


def node_prefixes(taxonomy_path: Sequence[str]) -> list[str]:
    """存款路径 → 其覆盖的类型树节点（含全部前缀）。

    ``['injection','sqli']`` → ``['injection', 'injection/sqli']``（影子记账：
    一笔存款同时落家族格与类型格，§3.3/§7.1）。空路径不落任何节点。
    """
    return ["/".join(taxonomy_path[: i + 1]) for i in range(len(taxonomy_path))]


# ── 数据结构 ────────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class CellKey:
    """格子主键（SPEC-1 §2 cells 表 PK）：(agent, role, node)。

    node = '<family>' 或 '<family>/<type>'（路由默认家族级，粒度由流量长出 §7.2）。
    """

    agent_id: str
    role: str
    node: str


@dataclass(frozen=True)
class CellAgg:
    """一格聚合结果（与 cells 表行同构）。n_eff 恒等于 2×(pos_w+neg_w)（§6.1）。"""

    agent_id: str
    role: str
    node: str
    pos_w: float
    neg_w: float
    n_eff: float
    n_eff_s2s3: float
    status: str
    activated_at: str | None
    last_event_seq: int
    updated_at: str

    @property
    def total_w(self) -> float:
        """挥发加权存款总量（地板判据：total_w < ε ⇒ C_raw 无定义，§5.1）。"""
        return self.pos_w + self.neg_w

    @property
    def key(self) -> CellKey:
        return CellKey(self.agent_id, self.role, self.node)


@dataclass(frozen=True)
class Baseline:
    """LOO 基线聚合（同 (role, node) 全体剔除 exclude_agent 自身存款，§5.1）。"""

    role: str
    node: str
    exclude_agent: str
    pos_w: float
    neg_w: float
    n_eff: float
    n_eff_s2s3: float
    deposits: int                          # 计入的存款笔数

    @property
    def total_w(self) -> float:
        return self.pos_w + self.neg_w


@dataclass(frozen=True)
class _Mass:
    """内部层级质量（节点格 / (agent, role) 层 / (agent) 层通用）。"""

    pos_w: float = 0.0
    neg_w: float = 0.0
    n_eff: float = 0.0
    n_eff_s2s3: float = 0.0

    @property
    def total_w(self) -> float:
        return self.pos_w + self.neg_w


_EMPTY_MASS = _Mass()


@dataclass
class _Acc:
    """折叠累加器：pos/neg/s2s3 增量累加，n_eff 由恒等式派生（保持逐位成立）。"""

    pos: float = 0.0
    neg: float = 0.0
    s2s3: float = 0.0
    last_seq: int = 0

    def add(self, w_eff: float, direction: int, s2s3_eff: float, seq: int) -> None:
        signed = w_eff * direction
        if signed > 0:
            self.pos += signed
        elif signed < 0:
            self.neg -= signed
        self.s2s3 += s2s3_eff
        self.last_seq = max(self.last_seq, seq)

    def mass(self) -> _Mass:
        return _Mass(
            pos_w=self.pos,
            neg_w=self.neg,
            n_eff=2.0 * (self.pos + self.neg),  # 恒等式 §6.1
            n_eff_s2s3=self.s2s3,
        )


@dataclass(frozen=True)
class _Rec:
    """一笔挥发加权的存款记录（供 LOO 基线按 (role, node) 重折叠）。"""

    agent: str
    role: str
    nodes: tuple[str, ...]
    w_eff: float
    s2s3_eff: float                        # 2×w_eff（S2/S3 且 direction≠0）否则 0
    direction: int
    seq: int


# ── 聚合主入口 ──────────────────────────────────────────────────

def view_cells(
    ledger: KernelLedger,
    as_of: str | datetime,
    *,
    params: ParamsConfig | None = None,
) -> CellView:
    """agg_cells 的只读形态：同一折叠与 status 重放建视图，**不写 cells 表**。

    与 :func:`agg_cells` 共用同一实现（``_fold`` + ``_replay_status_into``，
    聚合语义逐位一致），仅省略收尾的整表重写——供只读诊断/评估路径复用
    （SPEC-2 写口纪律：名为只读的入口不得触发 cells 物理写）。
    """
    p = params or load_params()
    as_of_dt = _coerce_utc(as_of)
    deposits = ledger.read(kinds=(LedgerKind.DEPOSIT,))
    view = _fold(deposits, as_of_dt, p)
    _replay_status_into(view, ledger.read(kinds=(LedgerKind.PROMOTION, LedgerKind.DEMOTION)), as_of_dt)
    return view


def agg_cells(
    ledger: KernelLedger,
    as_of: str | datetime,
    *,
    params: ParamsConfig | None = None,
) -> CellView:
    """从账本 deposit 事件重建 as_of 时点的全部口碑格子，整表重写 cells。

    - 参数读自 configs/params.yaml（可注入 params 覆盖）：decay 节给半衰期与
      地板 ε，shrink 节给收缩强度 λ（SPEC-1 §5.2）。
    - 返回 :class:`CellView`（内存视图）；cells 表为全量重建（先清后插，单事务），
      任意时点重放与在线一致（SPEC-1 §6-6 / SPEC-4 §2 重放等价）。
    - 只需要视图、不落表的调用用 :func:`view_cells`（同一折叠与重放实现）。
    """
    view = view_cells(ledger, as_of, params=params)
    _write_cells(ledger.conn, view, in_managed_txn=getattr(ledger, "_txn_depth", 0) > 0)
    return view


def _fold(deposits: Sequence, as_of_dt: datetime, params: ParamsConfig) -> "CellView":
    """单遍折叠：node 前缀格 + (agent, role) 层 + (agent) 层 + LOO 记录索引。"""
    cells_acc: dict[CellKey, _Acc] = {}
    role_acc: dict[tuple[str, str], _Acc] = {}
    agent_acc: dict[str, _Acc] = {}
    recs: list[_Rec] = []
    by_node: dict[tuple[str, str], list[int]] = {}

    for ev in deposits:
        ts = _coerce_utc(ev.ts)
        if ts > as_of_dt:
            continue                          # 晚于 as_of 的事件不属于该时点
        payload: Mapping = ev.payload
        agent = str(payload["agent"])
        role = str(payload["role"])
        direction = int(payload["direction"])
        w_eff = float(payload["weight"]) * decay_factor(
            payload.get("origin", DepositOrigin.PROD),
            (as_of_dt - ts).total_seconds(),
            decay=params.decay,
        )
        tier = SignalTier(payload["signal_tier"])
        s2s3_eff = 2.0 * w_eff if (tier in _S2S3_TIERS and direction != 0) else 0.0
        nodes = tuple(node_prefixes(payload["taxonomy_path"]))
        recs.append(
            _Rec(
                agent=agent,
                role=role,
                nodes=nodes,
                w_eff=w_eff,
                s2s3_eff=s2s3_eff,
                direction=direction,
                seq=ev.seq,
            )
        )
        if not nodes:
            continue                          # 无路径标签：无处落格
        for nd in nodes:
            by_node.setdefault((role, nd), []).append(len(recs) - 1)
            cells_acc.setdefault(CellKey(agent, role, nd), _Acc()).add(
                w_eff, direction, s2s3_eff, ev.seq
            )
        role_acc.setdefault((agent, role), _Acc()).add(w_eff, direction, s2s3_eff, ev.seq)
        agent_acc.setdefault(agent, _Acc()).add(w_eff, direction, s2s3_eff, ev.seq)

    now_iso = iso_utc(as_of_dt)
    cells = {
        key: CellAgg(
            agent_id=key.agent_id,
            role=key.role,
            node=key.node,
            pos_w=acc.pos,
            neg_w=acc.neg,
            n_eff=2.0 * (acc.pos + acc.neg),
            n_eff_s2s3=acc.s2s3,
            status=CellStatus.SHADOW.value,
            activated_at=None,
            last_event_seq=acc.last_seq,
            updated_at=now_iso,
        )
        for key, acc in cells_acc.items()
    }
    return CellView(
        as_of=as_of_dt,
        params=params,
        cells=cells,
        _role_mass={k: acc.mass() for k, acc in role_acc.items()},
        _agent_mass={k: acc.mass() for k, acc in agent_acc.items()},
        _recs=recs,
        _by_node=by_node,
    )


def _replay_status_into(view: "CellView", events: Sequence, as_of_dt: datetime) -> None:
    """按 seq 序重放 promotion/demotion，就地回填 cells 的 status / activated_at。"""
    status: dict[CellKey, str] = {}
    activated: dict[CellKey, str] = {}
    for ev in events:
        ts = _coerce_utc(ev.ts)
        if ts > as_of_dt:
            continue
        payload: Mapping = ev.payload
        key = CellKey(str(payload["agent"]), str(payload["role"]), str(payload["node"]))
        if ev.kind is LedgerKind.PROMOTION:
            status[key] = str(payload["to_status"])
            if payload["to_status"] == CellStatus.ACTIVE:
                activated[key] = ev.ts       # 最近一次升 active 的时点
        else:                                # demotion：休眠保留历史账，其余熄回 shadow
            status[key] = (
                CellStatus.DORMANT.value
                if payload["reason"] == "dormant"
                else CellStatus.SHADOW.value
            )
    for key, cell in view.cells.items():
        if key in status:
            view.cells[key] = replace(
                cell, status=status[key], activated_at=activated.get(key)
            )


def _write_cells(conn, view: "CellView", *, in_managed_txn: bool = False) -> int:
    """cells 写口本体（kernel 包内）：全量重建 = 先清后插，单事务提交。

    ``in_managed_txn``（2026-10-20 修复）：调用方处于可重入 ``transaction()``
    （``_txn_depth > 0``）内时沿用外层边界（异常随外层回滚）；顶层调用照旧
    即时提交（DML 的隐式事务必须在此收口，否则连接悬挂事务、后续 BEGIN
    IMMEDIATE 报 cannot start a transaction within a transaction）。"""
    rows = [
        (
            c.agent_id, c.role, c.node, c.pos_w, c.neg_w, c.n_eff, c.n_eff_s2s3,
            c.status, c.activated_at, c.last_event_seq, c.updated_at,
        )
        for key, c in sorted(view.cells.items())
    ]
    conn.execute("DELETE FROM cells")
    conn.executemany(
        "INSERT INTO cells (agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3,"
        " status, activated_at, last_event_seq, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    if not in_managed_txn:
        conn.commit()
    return len(rows)


# ── 读取视图：C_raw / C* / LOO 基线 ────────────────────────────

@dataclass
class CellView:
    """as_of 时点的格子全量视图（机制侧只读；写只能经 agg_cells 重建）。"""

    as_of: datetime
    params: ParamsConfig
    cells: dict[CellKey, CellAgg]
    _role_mass: dict[tuple[str, str], _Mass] = field(default_factory=dict)
    _agent_mass: dict[str, _Mass] = field(default_factory=dict)
    _recs: list[_Rec] = field(default_factory=list)
    _by_node: dict[tuple[str, str], list[int]] = field(default_factory=dict)

    # -- 基本查询 ──

    def get(self, agent_id: str, role: str, node: str) -> CellAgg | None:
        """取一格；格子不存在（该键从未有存款）返回 None。"""
        return self.cells.get(CellKey(agent_id, role, node))

    def _node_mass(self, agent_id: str, role: str, node: str) -> _Mass:
        cell = self.cells.get(CellKey(agent_id, role, node))
        if cell is None:
            return _EMPTY_MASS
        return _Mass(cell.pos_w, cell.neg_w, cell.n_eff, cell.n_eff_s2s3)

    def _mass(self, agent_id: str, role: str, node: str | None) -> _Mass:
        """层质量：node=None → (agent, role) 层；node 给定 → 该节点格。"""
        if node is None:
            return self._role_mass.get((agent_id, role), _EMPTY_MASS)
        return self._node_mass(agent_id, role, node)

    # -- C_raw 与挥发地板（§5.1）──

    def c_raw(self, agent_id: str, role: str, node: str | None = None) -> float | None:
        """原始能力 ``pos/(pos+neg)``；总量 < ε（地板）或无存款时无定义 → None。"""
        m = self._mass(agent_id, role, node)
        if m.total_w < self.params.decay.EPS_FLOOR:
            return None
        return m.pos_w / m.total_w

    # -- 层级收缩（§5.1）──

    def _chain(self, agent_id: str, role: str, node: str | None) -> list[_Mass]:
        """自底向上的收缩链：node→…→family→(agent, role)→(agent)。"""
        levels: list[_Mass] = []
        cur = node
        while cur:
            levels.append(self._mass(agent_id, role, cur))
            cur = cur.rpartition("/")[0] or None
        levels.append(self._mass(agent_id, role, None))
        levels.append(self._agent_mass.get(agent_id, _EMPTY_MASS))
        return levels

    def c_star(
        self,
        agent_id: str,
        role: str,
        node: str | None = None,
        *,
        root_prior: float = ROOT_PRIOR,
    ) -> float:
        """层级收缩能力值（§5.1）：C* = (n_eff·C_raw + λ·C*_parent)/(n_eff+λ)。

        node=None 读 (agent, role) 层。链条逐层向父收缩，地板击穿（总量 < ε）
        的层直接取父层 C*；全部击穿（如全新 agent）→ root_prior（永不死锁）。
        """
        lam = self.params.shrink.LAMBDA
        eps = self.params.decay.EPS_FLOOR
        c = root_prior
        for m in reversed(self._chain(agent_id, role, node)):
            if m.total_w < eps:
                continue                      # 地板：C* 直接取父层（§5.1）
            c = (m.n_eff * (m.pos_w / m.total_w) + lam * c) / (m.n_eff + lam)
        return c

    # -- LOO 基线（§5.1/§7.3）──

    def agg_baseline(self, role: str, node: str, *, exclude_agent: str) -> Baseline:
        """LOO 基线：同 (role, node) 全体存款剔除 exclude_agent 自身后重折叠。"""
        pos = neg = s2s3 = 0.0
        n = 0
        for idx in self._by_node.get((role, node), ()):
            r = self._recs[idx]
            if r.agent == exclude_agent:
                continue
            signed = r.w_eff * r.direction
            if signed > 0:
                pos += signed
            elif signed < 0:
                neg -= signed
            s2s3 += r.s2s3_eff
            n += 1
        return Baseline(
            role=role,
            node=node,
            exclude_agent=exclude_agent,
            pos_w=pos,
            neg_w=neg,
            n_eff=2.0 * (pos + neg),
            n_eff_s2s3=s2s3,
            deposits=n,
        )

    def baseline_mean(self, role: str, node: str, *, exclude_agent: str) -> float | None:
        """基线均值（LOO 聚合的 C_raw）；地板击穿时无定义 → None（§7.3 判据入口）。"""
        b = self.agg_baseline(role, node, exclude_agent=exclude_agent)
        if b.total_w < self.params.decay.EPS_FLOOR:
            return None
        return b.pos_w / b.total_w
