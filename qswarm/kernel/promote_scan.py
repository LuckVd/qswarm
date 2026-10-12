"""kernel.promote_scan：专长孵化状态机（设计 §7.3；SPEC-4 §0 仿真器直接复用本体）。

对每格 ``(agent, role, node)`` 幂等执行一次扫描，按 §7.3 生命周期推动状态流转：

    不存在 ──干活──→ shadow ──四判据全满足──→ candidate ──暗考通过──→ active
                        ↑                                       │
                        └─────衰退(连续2窗) / 休眠(60天无任务)─────┘
          dormant（休眠保留历史账）──再流行──→ n_eff≥5 快速重激活──→ active

M0 定稿 v2 增补两条通道（默认关闭，DECISIONS 2026-10-09 v2 条）：

- 转换② v2（孵化直晋收官，exam.ENTRY_GRACE_DAYS / ENTRY_CORROB_S2S3）：§8.4 v2
  席位提名的 candidate（exam_id 前缀 ``entry-fast:``）按「生产佐证 → 宽限等待 →
  §8.2 暗考兜底」收官——佐证 = n_eff_s2s3 达门槛且纯生产 C* 高于同节点 LOO 生产
  基线（免暗考直转 active）；宽限期内不落暗考、给生产证据长入的时间；期满仍未
  佐证才落 §8.2 暗考。转换①另设 §8.2「冷却期后换题重考」的重入路（提名格暗考
  失败退 shadow、冷却期满重入 candidate 重考；ENTRY_TOP_FRAC=0 时恒 inert）。
- 转换⑥（周期复核熄灭，demote.FC_DAYS 与 ``for_cause`` 回调）：active 格每
  FC_DAYS 天传唤一场周期复核考（相位/趋势基线/连续不达标计数在回调侧），
  连续不达标 → demotion(reason=decay) 熄灭（§8.1「淘汰仍只看后续周期考」的
  落地；§7.3 双窗衰退判据不变，本通道是它的快速前置）。

转换① shadow → candidate（四判据**全满足**，任一不满足即不晋升）：
  ① 样本量硬门槛：``cell.n_eff ≥ promote.N(=10) 且 cell.n_eff_s2s3 ≥ promote.N_S2S3(=3)``
    （n_eff_s2s3 只计 S2/S3 当量，堵住纯 S1 堆量刷候选，§7.3）；
  ② 优势：``Wilson_LB(C_raw, z=1.64) > LOO 基线均值 + promote.DELTA(=0.10)``
    （LOO 剔自身防小编织自证；基线无定义——地板击穿——即不满足）；
  ③ 类型肥度：该节点 90 天窗（``promote.M_window_days``）内**达 S1+ verdict 的簇数**
    ``≥ promote.M(=30)``，原始计数非加权（v0.3 口径；全组织＝不限角色）；
  ④ 无在途考试/冷却中：同格暗考失败后退回 shadow 起的 ``exam.COOLDOWN_DAYS`` 内
    不再触发（§8.2 冷却；从账本 promotion 事件 ``candidate→shadow`` 推导，可重放）。

转换② candidate → active：由**注入的考试回调**（``exam`` 参数，仿真侧为
simulator.exam.ExamCenter）的结果驱动——过 → active；不过 → 退回 shadow 并进入
冷却（判据④的依据）。回调返回 ``None``（无卷/回调内冷却拒绝）时格子保持
candidate 等待下一次扫描收官。

转换③ active → shadow（衰退熄灭，§7.3「防吃老本」）：以 ``window_days``（默认
``SLIDING_WINDOW_DAYS`` = 7 天，M0 调参定稿）为窗、``demote.WINDOWS(=2)`` 为窗数，
**连续**每一窗的
``Wilson_UB(窗内格子质量) < 同窗 LOO 基线均值`` 才熄灭。两处保守化压误熄灭率
（SPEC-4 度量 ≤0.05）：窗整段须落在本次 activation 之后（刚激活的格子没有"在任
衰退"证据）；窗内无数据/基线地板击穿视为未跌破。

转换④ active → dormant（休眠）：该节点最近一笔生产存款距今 ``> demote.DORMANT_DAYS
(=60)`` 天——"60 天无该类型任务"。历史账保留（demotion 事件 reason=dormant，
cells 重放语义见 kernel.cells）。

转换⑤ dormant → active（快速重激活）：``cell.n_eff ≥ demote.REACTIVATE_N(=5)``
**且**节点流量回潮（最近生产存款 ≤ 60 天，§7.3"再流行"）——只查样本量不做全 gauntlet
（"曾经证明过"）。再流行门槛同时是幂等性的关键：纯老账的 dormant 格不会在连续
扫描间反复 activation/休眠振荡。

口径与落库纪律（逐条可溯源）：
- Wilson 区间按 §7.3 原式（z=1.64）：
  ``LB/UB = ( p̂ ± z·√(p̂(1−p̂)/n + z²/4n²) + z²/2n ) / (1 + z²/n)``；
  二项计数以加权伪计数代入：``p̂ = pos_w/(pos_w+neg_w)``、``n = n_eff = 2×(pos_w+neg_w)``
  （§7.3 统计口径声明"连续 n_eff 代入"；与门槛 N 同量纲）。总质量为 0 时区间无定义。
- 账本事件语义：**promotion 事件承载考试通道的全部流转**（shadow→candidate、
  candidate→active、考试不过 candidate→shadow——to_status 如实记录），demotion
  仅用于机制性熄灭（decay 衰退 / dormant 休眠）；dormant→active 重激活走 promotion
  （exam_id 填 ``reactivate:…`` 轨迹号，§7.3 快速通道不经考试）。
- 同事务落库：每格一次状态迁出 = **恰一条**本模块追加的账本事件；cells 表在扫描
  收尾由 agg_cells 从账本整表重放重建——cells.status 是事件的重放投影，结构上不
  存在无账本事件的状态变更（SPEC-1 §2/§6-6）。中途故障的已追加事件是既成事实，
  重跑扫描按同一账本收敛到同一状态（幂等）。
- 幂等性来源（同一账本重复扫描 = 零新事件、cells 不变）：转换后的状态不再满足
  触发前件——active 格的衰退窗须整段落在 activation 之后、考试失败的冷却门、
  dormant 格的再流行门槛、candidate 格不重复晋升（已是 candidate）。
- §8.4 纪律：origin=exam 的考试存款**不进入**流量簇计数（③）、休眠活动钟与衰退
  窗口（淘汰侧判据）；晋升判据①②读 cells 全量聚合（含考试播种，§8.4 允许其
  入格推动入考，暗考闸门兜底质量）。
- §7.4 护栏 K=3（单类型专员上限）属路由侧约束，不在本扫描判定内。
- 相位分离：先纯读评估全部格子（无读后写），再统一执行事件追加与考试回调，
  同一扫描内评估不依赖本扫描写入的事件。

离线批处理（§7.3 护栏）：promote_scan 夜间/每 N 笔存款跑，不在热路径。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from qswarm.contracts import (
    CellStatus,
    DemotionPayload,
    DepositOrigin,
    LedgerKind,
    ParamsConfig,
    PromotionPayload,
    Role,
    TaxonomyConfig,
    load_params,
    load_taxonomy,
)

from .cells import (
    ROOT_PRIOR,
    CellKey,
    CellView,
    agg_cells,
    decay_factor,
    node_prefixes,
    view_cells,
)
from .ledger import KernelLedger, _coerce_utc

__all__ = [
    "SLIDING_WINDOW_DAYS",
    "WILSON_Z",
    "CriteriaSnapshot",
    "ExamRunner",
    "Transition",
    "effective_M",
    "evaluate_candidate_criteria",
    "promote_scan",
    "wilson_bounds",
    "wilson_lb",
    "wilson_ub",
]

#: Wilson 区间分位数（§7.3：z=1.64，90% 单侧）。
WILSON_Z = 1.64

#: 衰退滑动窗窗长（天）。§7.4 参数表未钉死窗长，M0 仿真器定稿（promote_scan 伪码
#: 签名 ``W=SLIDING_WINDOW`` 的落地值）：**7 天**。依据 sim_report 调参轮实证
#: （results/tune-round1..3）：W=30 时「连续 2 整窗落在任期内」在 30 日仿真地平线
#: 内恒不可满足（衰减熄灭在仿真中不可达，度量⑤无观测）；W=1 探测窗过窄，
#: 误熄灭率破 0.05 上限（实测 0.07–0.27）。W=7（2 窗 = 14 天 ≤ 任期）使转换③
#: 在地平线内可观测且 ④=0 保持（DECISIONS 2026-10-08）。
SLIDING_WINDOW_DAYS = 7.0

_PROIOD = DepositOrigin.PROD


# ── 判据③的类型肥度门槛（M 的节点有效值）────────────────────────

def effective_M(
    node: str,
    *,
    params: ParamsConfig,
    taxonomy: TaxonomyConfig | None = None,
) -> int:
    """判据③对该节点的有效 M（§7.3 类型肥度门槛的 M0 定稿口径）。

    - ``promote.M_PRORATE=False``（§7.3 逐字）：恒为 ``promote.M``；
    - ``promote.M_PRORATE=True``（M0 定稿，DECISIONS 2026-10-09）：按节点的
      **期望簇份额**等比缩放——家族 ``share = v1_weight(f)/max_f v1_weight``，
      类型 ``share = (v1_weight(f)/n_types(f))/max_g(v1_weight(g)/n_types(g))``；
      最高份额节点保持原阈值 M，其余同比例下调（下取整、≥1）。依据：簇到达率
      ∝ v1_weight/扇出，等比缩放使「到达 M 的等待时长」跨节点一致——否则
      低权重族/高扇出族的结构性不可达（500 任务窗内 type 节点流量 < M=30）
      使判据③退化为对这些节点恒 False 的死门槛（sim_report §1/②）。
    """
    m = int(params.promote.M)
    if not params.promote.M_PRORATE:
        return m
    tax = taxonomy if taxonomy is not None else load_taxonomy()
    per_type = {f: w.v1_weight / len(w.types) for f, w in tax.families.items()}
    max_per_type = max(per_type.values())
    family, _, typ = node.partition("/")
    spec = tax.families.get(family)
    if spec is None:
        return m                       # 未知家族（容错）：不缩放
    if typ:
        share = (per_type.get(family, 0.0) / max_per_type) if max_per_type > 0 else 1.0
    else:
        max_w = max(w.v1_weight for w in tax.families.values())
        share = (spec.v1_weight / max_w) if max_w > 0 else 1.0
    return max(1, min(m, int(m * share)))


# ── Wilson 区间（§7.3 原式）─────────────────────────────────────

def wilson_bounds(
    pos_w: float, neg_w: float, *, z: float = WILSON_Z
) -> tuple[float, float] | None:
    """加权伪计数的 Wilson LB/UB（§7.3 原式）。

    ``p̂ = pos/(pos+neg)``、``n = 2×(pos+neg)``（n_eff 恒等式 §6.1）；总质量 ≤ 0
    时区间无定义返回 None。返回 ``(lb, ub)``。
    """
    total = pos_w + neg_w
    if total <= 0.0:
        return None
    n = 2.0 * total
    p = pos_w / total
    z2 = z * z
    denom = 1.0 + z2 / n
    center = p + z2 / (2.0 * n)
    spread = z * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    return (center - spread) / denom, (center + spread) / denom


def wilson_lb(pos_w: float, neg_w: float, *, z: float = WILSON_Z) -> float | None:
    """Wilson 下界（晋升判据②入口）；无定义返回 None。"""
    b = wilson_bounds(pos_w, neg_w, z=z)
    return None if b is None else b[0]


def wilson_ub(pos_w: float, neg_w: float, *, z: float = WILSON_Z) -> float | None:
    """Wilson 上界（衰退判据入口）；无定义返回 None。"""
    b = wilson_bounds(pos_w, neg_w, z=z)
    return None if b is None else b[1]


# ── 扫描结果结构 ────────────────────────────────────────────────

_CRITERIA_NAMES = ("hard", "advantage", "traffic", "gate")


@dataclass(frozen=True)
class CriteriaSnapshot:
    """转换①四判据的逐条快照（任一 False 即不晋升；供测试/仿真报告取证）。"""

    n_eff: float
    n_eff_s2s3: float
    hard_ok: bool                     # ① n_eff ≥ N 且 n_eff_s2s3 ≥ N_S2S3
    wilson_lb: float | None           # ② LB（None = 格子无质量）
    baseline: float | None            # ② LOO 基线均值（None = 基线地板击穿）
    advantage_ok: bool
    traffic: int                      # ③ 90 天窗达 S1+ verdict 的簇数
    traffic_ok: bool
    gate_ok: bool                     # ④ 无在途考试/冷却中
    m_eff: int | None = None          # ③ 该节点有效 M（M_PRORATE 定稿口径；诊断用）
    window_advantage_ok: bool | None = None   # ② 多窗稳定性读数（关闭时 None）

    @property
    def all_ok(self) -> bool:
        return self.hard_ok and self.advantage_ok and self.traffic_ok and self.gate_ok

    def failed(self) -> tuple[str, ...]:
        """被证伪的判据名（harden 测试断言「恰一条被证伪」用）。"""
        return tuple(
            name
            for name, ok in zip(
                _CRITERIA_NAMES,
                (self.hard_ok, self.advantage_ok, self.traffic_ok, self.gate_ok),
            )
            if not ok
        )


@dataclass(frozen=True)
class Transition:
    """一次状态流转记录（与所落账本事件一一对应）。"""

    agent_id: str
    role: str
    node: str
    from_status: str
    to_status: str
    reason: str                       # candidate | exam_pass | exam_fail | decay | dormant | reactivate
    kind: str                         # promotion | demotion
    seq: int                          # 账本事件 seq
    exam_id: str | None = None
    criteria: CriteriaSnapshot | None = None   # 仅 shadow→candidate 携带四判据快照

    @property
    def key(self) -> CellKey:
        return CellKey(self.agent_id, self.role, self.node)


class ExamRunner(Protocol):
    """晋升暗考回调协议（§7.3 转换②；仿真侧 simulator.exam.ExamCenter 兼容）。

    约定：``__call__(*, agent_id, role, node, from_status)`` 返回
    ``None``（本轮不考，格子保持 candidate）或带属性的结果对象——
    ``passed: bool``、``exam_id: str``，可选 ``score: float`` 与
    ``seq_promotion: int | None``（回调已自行落 promotion 事件时给出其 seq，
    promote_scan 不再重复落；None 时由 promote_scan 落 candidate→active 事件）。
    考试不过的 candidate→shadow 事件**始终**由 promote_scan 落（回调只考试不写格）。
    """

    def __call__(
        self, *, agent_id: str, role: str, node: str, from_status: Any
    ) -> Any | None: ...


ExamRunnerCallback = Callable[..., Any]


# ── 账本预索引（单遍；只读相位用）───────────────────────────────

def _window_index(ts: datetime, as_of: datetime, w_days: float, windows: int) -> int | None:
    """ts ∈ (as_of−(i+1)W, as_of−iW] → 窗号 i（0 = 最近窗）；全窗外返回 None。"""
    for i in range(windows):
        start = as_of - timedelta(days=w_days * (i + 1))
        end = as_of - timedelta(days=w_days * i)
        if start < ts <= end:
            return i
    return None


class _Index:
    """deposit/promotion 事件的只读预索引：簇计数、节点活动钟、衰退窗质量。"""

    def __init__(
        self,
        ledger: KernelLedger,
        as_of: datetime,
        params: ParamsConfig,
        window_days: float,
        taxonomy: TaxonomyConfig | None = None,
    ) -> None:
        self.as_of = as_of
        self.params = params
        self.window_days = window_days
        self.windows = params.demote.WINDOWS
        self._taxonomy = taxonomy
        self._m_eff: dict[str, int] = {}
        self.clusters: dict[str, set[str]] = {}          # node → M 窗内 prod 簇号
        self.last_activity: dict[str, datetime] = {}     # node → 最近 prod 存款时刻
        self._cell_win: dict[tuple, tuple[float, float]] = {}
        self._node_agent_win: dict[tuple, tuple[float, float]] = {}
        self._node_win_total: dict[tuple, tuple[float, float]] = {}
        # 生产佐证（exam.ENTRY_CORROB_S2S3 > 0）：全期**纯生产**质量分解——
        # 考试存款（§8.4 入格推动）不进本视图，佐证的 C* 对比必须同源
        # （quiz 存款把混合 LOO 基线整体抬到 ~0.9，混合口径下佐证永不触发）。
        self._cell_prod: dict[tuple, tuple[float, float]] = {}
        self._node_agent_prod: dict[tuple, tuple[float, float]] = {}
        self._node_prod_total: dict[tuple, tuple[float, float]] = {}

        for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
            ts = _coerce_utc(ev.ts)
            if ts > as_of:
                continue                      # 晚于 as_of 的事件不属于该时点（同 agg_cells）
            payload: dict = ev.payload
            origin = DepositOrigin(payload.get("origin", _PROIOD))
            if origin is not _PROIOD:
                continue                      # §8.4：考试存款不进流量/休眠/衰退窗判据
            nodes = node_prefixes(payload["taxonomy_path"])
            if not nodes:
                continue
            agent = str(payload["agent"])
            role = str(payload["role"])
            w_eff = float(payload["weight"]) * decay_factor(
                origin, (as_of - ts).total_seconds(), decay=params.decay
            )
            signed = w_eff * int(payload["direction"])
            pos = signed if signed > 0 else 0.0
            neg = -signed if signed < 0 else 0.0

            # 纯生产质量分解（佐证用；与窗索引同源同挥发口径，全期累计）
            for nd in nodes:
                ck = (agent, role, nd)
                p0, n0 = self._cell_prod.get(ck, (0.0, 0.0))
                self._cell_prod[ck] = (p0 + pos, n0 + neg)
                nk = (role, nd, agent)
                p1, n1 = self._node_agent_prod.get(nk, (0.0, 0.0))
                self._node_agent_prod[nk] = (p1 + pos, n1 + neg)
                tk = (role, nd)
                p2, n2 = self._node_prod_total.get(tk, (0.0, 0.0))
                self._node_prod_total[tk] = (p2 + pos, n2 + neg)

            # 节点活动钟（休眠判据：最近生产存款）
            for nd in nodes:
                prev = self.last_activity.get(nd)
                if prev is None or ts > prev:
                    self.last_activity[nd] = ts

            # M 窗簇计数（判据③：达 S1+ verdict 的簇，原始计数）
            cluster = payload.get("cluster")
            if cluster and ts > as_of - timedelta(days=params.promote.M_window_days):
                for nd in nodes:
                    self.clusters.setdefault(nd, set()).add(str(cluster))

            # 衰退窗质量：格内 + (role, node) × agent 分解（LOO 用）
            win = _window_index(ts, as_of, window_days, self.windows)
            if win is None:
                continue
            for nd in nodes:
                ck = (agent, role, nd, win)
                p0, n0 = self._cell_win.get(ck, (0.0, 0.0))
                self._cell_win[ck] = (p0 + pos, n0 + neg)
                nk = (role, nd, agent, win)
                p1, n1 = self._node_agent_win.get(nk, (0.0, 0.0))
                self._node_agent_win[nk] = (p1 + pos, n1 + neg)
                tk = (role, nd, win)
                p2, n2 = self._node_win_total.get(tk, (0.0, 0.0))
                self._node_win_total[tk] = (p2 + pos, n2 + neg)

        # promotion/demotion 轨迹：冷却门（判据④）从 candidate→shadow 事件推导；
        # 直晋提名（exam_id 前缀 entry-fast:）记录提名时点（转换②宽限/佐证依据）。
        self._exam_fail_at: dict[CellKey, datetime] = {}
        self._nominated_at: dict[CellKey, datetime] = {}
        for ev in ledger.read(kinds=(LedgerKind.PROMOTION,)):
            ts = _coerce_utc(ev.ts)
            if ts > as_of:
                continue                      # 晚于 as_of 的事件不属于该时点
            payload = ev.payload
            if (
                str(payload["from_status"]) == CellStatus.CANDIDATE.value
                and str(payload["to_status"]) == CellStatus.SHADOW.value
            ):
                key = CellKey(
                    str(payload["agent"]), str(payload["role"]), str(payload["node"])
                )
                prev = self._exam_fail_at.get(key)
                if prev is None or ts > prev:
                    self._exam_fail_at[key] = ts
            if (
                str(payload["to_status"]) == CellStatus.CANDIDATE.value
                and str(payload.get("exam_id", "")).startswith("entry-fast:")
            ):
                key = CellKey(
                    str(payload["agent"]), str(payload["role"]), str(payload["node"])
                )
                prev = self._nominated_at.get(key)
                if prev is None or ts < prev:
                    self._nominated_at[key] = ts

    # -- 读取 ──

    def traffic(self, node: str) -> int:
        """该节点 M 窗内达 S1+ verdict 的簇数（全组织、原始计数，§7.3 判据③）。"""
        return len(self.clusters.get(node, ()))

    def m_eff(self, node: str) -> int:
        """该节点判据③的有效 M（M_PRORATE 定稿口径的每扫描一次缓存）。"""
        cached = self._m_eff.get(node)
        if cached is None:
            cached = effective_M(node, params=self.params, taxonomy=self._taxonomy)
            self._m_eff[node] = cached
        return cached

    def node_idle_days(self, node: str) -> float | None:
        """该节点最近生产存款距今的天数；该节点从无生产存款 → None。"""
        last = self.last_activity.get(node)
        return None if last is None else (self.as_of - last).total_seconds() / 86400.0

    def cooldown_blocked(self, key: CellKey) -> bool:
        """判据④：该格暗考失败退回 shadow 后仍在 exam.COOLDOWN_DAYS 冷却期内。"""
        fail_at = self._exam_fail_at.get(key)
        if fail_at is None:
            return False
        return (self.as_of - fail_at) < timedelta(
            days=self.params.exam.COOLDOWN_DAYS
        )

    def nominated_at(self, key: CellKey) -> datetime | None:
        """直晋提名时点（exam_id 前缀 entry-fast: 的 shadow→candidate 事件）。"""
        return self._nominated_at.get(key)

    def prod_advantage(self, key: CellKey, eps_floor: float) -> bool:
        """佐证的优势半判据：纯生产 C_raw 严格高于同 (role,node) LOO 生产基线。

        基线 = 同节点全体剔自身的生产质量份额；无同侪生产数据（地板击穿）时取
        中性先验 ROOT_PRIOR（§5.1 收底语义）。自身无生产质量 → False。
        """
        pos, neg = self._cell_prod.get(
            (key.agent_id, key.role, key.node), (0.0, 0.0)
        )
        if pos + neg <= 0.0:
            return False
        own = pos / (pos + neg)
        tp, tn = self._node_prod_total.get((key.role, key.node), (0.0, 0.0))
        op, on = self._node_agent_prod.get(
            (key.role, key.node, key.agent_id), (0.0, 0.0)
        )
        ppos, pneg = tp - op, tn - on
        base = (
            ppos / (ppos + pneg) if ppos + pneg >= eps_floor and (ppos + pneg) > 0
            else ROOT_PRIOR
        )
        return own > base

    def cell_window_mass(self, key: CellKey, win: int) -> tuple[float, float]:
        """格子第 win 窗的 (pos, neg) 生产质量（as_of 锚定挥发）。"""
        return self._cell_win.get((key.agent_id, key.role, key.node, win), (0.0, 0.0))

    def baseline_window_mean(
        self, key: CellKey, win: int, eps_floor: float
    ) -> float | None:
        """第 win 窗的 LOO 基线均值（同 (role, node) 剔自身）；地板击穿 → None。"""
        tp, tn = self._node_win_total.get((key.role, key.node, win), (0.0, 0.0))
        op, on = self._node_agent_win.get(
            (key.role, key.node, key.agent_id, win), (0.0, 0.0)
        )
        pos, neg = tp - op, tn - on
        if pos + neg < eps_floor:
            return None
        return pos / (pos + neg)


# ── 转换①判据评估（纯读）───────────────────────────────────────

def evaluate_candidate_criteria(
    ledger: KernelLedger,
    as_of: str | datetime,
    key: CellKey,
    *,
    params: ParamsConfig | None = None,
    window_days: float = SLIDING_WINDOW_DAYS,
    taxonomy: TaxonomyConfig | None = None,
) -> CriteriaSnapshot:
    """诊断入口：对一格评估转换①四判据（§7.3），返回逐条快照。

    只读（不写任何状态/事件，也不触发 cells 物理表重写——视图走
    :func:`view_cells` 只读重放路径，与 agg_cells 同一折叠/status 重放实现，
    仅省略整表落库）；:func:`promote_scan` 内部走同一条评估路径。
    供测试逐条证伪（``snapshot.failed()``）与仿真报告取证。
    """
    p = params or load_params()
    view = view_cells(ledger, as_of, params=p)
    index = _Index(ledger, _coerce_utc(as_of), p, window_days, taxonomy=taxonomy)
    return _evaluate(view, key, index)


def _evaluate(view: CellView, key: CellKey, index: _Index) -> CriteriaSnapshot:
    cell = view.cells[key]
    p = view.params

    n_eff_ok = cell.n_eff >= p.promote.N and cell.n_eff_s2s3 >= p.promote.N_S2S3
    c_raw = view.c_raw(key.agent_id, key.role, key.node)
    lb = None if c_raw is None else wilson_lb(cell.pos_w, cell.neg_w)
    base = view.baseline_mean(key.role, key.node, exclude_agent=key.agent_id)
    adv_ok = lb is not None and base is not None and lb > base + p.promote.DELTA

    # 多窗优势稳定性（promote.WINDOW_ADVANTAGE，DECISIONS 2026-10-09）：
    # 最近 K 个衰退窗逐窗 Wilson_LB > 同窗 LOO 基线 + DELTA；窗内无数据或基线
    # 地板击穿 → 不满足（与转换③同一保守方向：无证据 ≠ 优势）。
    # 不满足时并入判据②（advantage_ok）——all_ok 单一出口，failed() 语义不变。
    window_advantage_ok: bool | None = None
    k_win = int(p.promote.WINDOW_ADVANTAGE)
    if k_win >= 1:
        window_advantage_ok = True
        for win in range(min(k_win, index.windows)):
            wpos, wneg = index.cell_window_mass(key, win)
            wlb = wilson_lb(wpos, wneg)
            wbase = index.baseline_window_mean(key, win, p.decay.EPS_FLOOR)
            if (
                wlb is None or wbase is None
                or not (wlb > wbase + p.promote.DELTA)
            ):
                window_advantage_ok = False
                break
        adv_ok = adv_ok and window_advantage_ok

    traffic = index.traffic(key.node)
    m_eff = index.m_eff(key.node)
    traffic_ok = traffic >= m_eff

    gate_ok = not index.cooldown_blocked(key)

    return CriteriaSnapshot(
        n_eff=cell.n_eff,
        n_eff_s2s3=cell.n_eff_s2s3,
        hard_ok=n_eff_ok,
        wilson_lb=lb,
        baseline=base,
        advantage_ok=adv_ok,
        traffic=traffic,
        traffic_ok=traffic_ok,
        gate_ok=gate_ok,
        m_eff=m_eff,
        window_advantage_ok=window_advantage_ok,
    )


# ── 裁量复核（转换⑥）与直晋佐证（转换② v2）判定 ────────────────


def _entry_in_window(
    index: _Index,
    key: CellKey,
    as_of_dt: datetime,
    window_days: int,
) -> bool:
    """直晋兑现窗判定（exam.ENTRY_WINDOW_DAYS；0 = 不设窗恒在窗内）。

    提名格的佐证/暗考/重考兑现只发生在窗内（§8.4 孵化的冷启动时间域）；
    窗闭后提名格冻结（candidate 悬置，不进 ① 分母，见转换②的窗闭分支）。
    """
    if window_days <= 0:
        return True
    nom = index.nominated_at(key)
    return nom is not None and (as_of_dt - nom) < timedelta(days=window_days)


def _entry_corroborated(
    view: CellView,
    key: CellKey,
    index: _Index,
) -> bool:
    """直晋候选人的生产佐证判定（exam.ENTRY_CORROB_S2S3，M0 定稿 v2）。

    提名（§8.4 v2 席位直晋）只给了考试面证据；佐证要求纯生产 S2/S3 当量
    （n_eff_s2s3，§8.4 考试存款天然不计）达门槛 **且** 纯生产 C_raw 严格高于
    同 (role,node) LOO 生产基线（:meth:`_Index.prod_advantage`；考试存款同时
    排除在两侧之外——quiz 把混合基线整体抬正，混合口径对比无判别力）。
    """
    threshold = float(view.params.exam.ENTRY_CORROB_S2S3)
    if threshold <= 0.0:
        return False
    cell = view.cells[key]
    if cell.n_eff_s2s3 < threshold:
        return False
    return index.prod_advantage(key, view.params.decay.EPS_FLOOR)


# ── 衰退（转换③）与休眠/重激活（转换④⑤）判据 ─────────────────


def _decay_below_baseline(
    key: CellKey,
    index: _Index,
    *,
    activated_at: str | None,
) -> bool:
    """连续 WINDOWS 窗 Wilson_UB < 同窗 LOO 基线（§7.3 转换③）。

    保守化：任一窗整段早于 activation（任期不足）、窗内无数据、基线地板击穿
    ——一律视为未跌破（压误熄灭率，SPEC-4 §2）。
    """
    if activated_at is None:
        return False                     # 无 activation 记录：无在任衰退证据
    act = _coerce_utc(activated_at)
    for win in range(index.windows):
        start = index.as_of - timedelta(days=index.window_days * (win + 1))
        if start < act:
            return False                 # 窗不整段落在任期内：不算"吃老本"证据
        pos, neg = index.cell_window_mass(key, win)
        ub = wilson_ub(pos, neg)
        if ub is None:
            return False                 # 窗内无质量：无衰退证据
        base = index.baseline_window_mean(key, win, index.params.decay.EPS_FLOOR)
        if base is None or not (ub < base):
            return False                 # 基线无定义或本窗未跌破：不连续
    return True


def _dormant_due(key: CellKey, index: _Index) -> bool:
    """60 天无该类型任务（节点级生产存款活动钟；§7.3 转换④）。"""
    idle = index.node_idle_days(key.node)
    return idle is None or idle > index.params.demote.DORMANT_DAYS


def _reactivation_live(key: CellKey, index: _Index) -> bool:
    """再流行门槛：节点流量回潮（最近生产存款 ≤ DORMANT_DAYS 天，§7.3 转换⑤）。"""
    idle = index.node_idle_days(key.node)
    return idle is not None and idle <= index.params.demote.DORMANT_DAYS


# ── 主入口 ──────────────────────────────────────────────────────

def promote_scan(
    ledger: KernelLedger,
    as_of: str | datetime,
    *,
    params: ParamsConfig | None = None,
    exam: ExamRunnerCallback | None = None,
    window_days: float = SLIDING_WINDOW_DAYS,
    run_id: str | None = None,
    taxonomy: TaxonomyConfig | None = None,
    for_cause: ExamRunnerCallback | None = None,
) -> list[Transition]:
    """对账本执行一轮孵化扫描（幂等；§7.3 状态机 + 同事务账本事件）。

    - ``as_of``：扫描时点（内核无隐藏时钟，显式注入；与 agg_cells 同一口径）；
    - ``exam``：晋升暗考回调（None = 只晋 candidate，考试由外部流程收官）；
    - ``window_days``：衰退滑动窗窗长（默认 ``SLIDING_WINDOW_DAYS`` = 7 天，
      M0 调参定稿，见模块头注与 DECISIONS 2026-10-08）；
    - ``for_cause``：周期复核考回调（§8.1 淘汰通道，M0 定稿 v2；None 或
      ``demote.FC_DAYS = 0`` = 关闭，§7.3 逐字）；
    - 返回本轮全部状态流转（与所落账本事件一一对应）；无流转时返回 []。

    执行序：agg_cells 重建视图（复用内核聚合与 status 重放）→ 只读评估 →
    事件追加/考试回调 → agg_cells 收尾整表重写 cells（重放含新事件，
    「状态变更必有账本事件」在结构上成立）。
    """
    p = params or load_params()
    as_of_dt = _coerce_utc(as_of)
    if window_days <= 0:
        raise ValueError(f"window_days 必须 > 0：{window_days}")
    fc_enabled = for_cause is not None and float(p.demote.FC_DAYS) > 0
    grace_days = int(p.exam.ENTRY_GRACE_DAYS)
    corrob_on = float(p.exam.ENTRY_CORROB_S2S3) > 0.0
    lane_retry = float(p.exam.ENTRY_TOP_FRAC) > 0.0
    entry_window = int(p.exam.ENTRY_WINDOW_DAYS)

    view = agg_cells(ledger, as_of_dt, params=p)
    index = _Index(ledger, as_of_dt, p, window_days, taxonomy=taxonomy)

    transitions: list[Transition] = []

    # 同族唯一专员守卫（promote.UNIQUE_PER_FAMILY，DECISIONS 2026-10-09）：
    # 家族占用集 = 扫描前 active/candidate 格的 (role, family, agent) ∪ 本轮已
    # 新晋格；同族已有**他人**席位时不晋升（同一 agent 自己的家族树格不占用——
    # 专员身份按 agent 计，其 family/type 格是同一专员的足迹，激活它们才能让
    # §5.2 TILT 在类型级路由节点生效）。
    unique_guard = p.promote.UNIQUE_PER_FAMILY
    occupied: set[tuple[str, str, str]] = {
        (key.role, key.node.partition("/")[0], key.agent_id)
        for key, cell in view.cells.items()
        if cell.status in (CellStatus.ACTIVE.value, CellStatus.CANDIDATE.value)
    }

    def _family_taken(key: CellKey) -> bool:
        if not unique_guard:
            return False
        family = key.node.partition("/")[0]
        return any(
            r == key.role and f == family and a != key.agent_id
            for r, f, a in occupied
        )

    for key in sorted(view.cells):
        cell = view.cells[key]
        status = CellStatus(cell.status)

        # ── 转换①：shadow → candidate（四判据全满足；v2 直晋格另有 §8.2 重考路）──
        if status is CellStatus.SHADOW:
            # v2 提名格（§8.4 席位直晋）不受同族唯一守卫约束：提名发生在播种夜
            # （席位 = (role,node) 的 top-⌈0.2n⌉，先于任何在任专员落座），重考/
            # 佐证收官是既定席位的兑现而非新增拥挤；守卫继续约束 §7.3 证据路径。
            lane_cell = lane_retry and index.nominated_at(key) is not None
            if _family_taken(key) and not lane_cell:
                continue
            # §8.2 换题重考（v2 孵化通道，DECISIONS 2026-10-09 v2；ENTRY_TOP_FRAC=0
            # 时不存在提名格，分支恒 inert）：直晋提名的格暗考失败退回 shadow 后，
            # 冷却期满由孵化通道重新推入 candidate 换题重考——「不挂零容忍重考，
            # 冷却期后换题重考」的字面落地；重考次数由窗口内的冷却节奏自然封顶。
            if (
                lane_cell
                and _entry_in_window(index, key, as_of_dt, entry_window)
                and key in index._exam_fail_at
                and not index.cooldown_blocked(key)
            ):
                retry_id = f"entry-retry:{key.agent_id}:{key.role}:{key.node}"
                ev = ledger.append(
                    LedgerKind.PROMOTION,
                    PromotionPayload(
                        agent=key.agent_id,
                        role=Role(key.role),
                        node=key.node,
                        exam_id=retry_id,
                        from_status=CellStatus.SHADOW,
                        to_status=CellStatus.CANDIDATE,
                    ),
                    run_id=run_id,
                )
                transitions.append(
                    Transition(
                        agent_id=key.agent_id,
                        role=key.role,
                        node=key.node,
                        from_status=CellStatus.SHADOW.value,
                        to_status=CellStatus.CANDIDATE.value,
                        reason="candidate",
                        kind="promotion",
                        seq=ev.seq,
                        exam_id=retry_id,
                    )
                )
                status = CellStatus.CANDIDATE
                if exam is not None:
                    status = _run_exam(
                        exam, key, status, ledger, transitions, run_id=run_id
                    )
                continue

            snap = _evaluate(view, key, index)
            if not snap.all_ok:
                continue
            occupied.add((key.role, key.node.partition("/")[0], key.agent_id))
            sched_id = f"sched:{key.agent_id}:{key.role}:{key.node}"
            ev = ledger.append(
                LedgerKind.PROMOTION,
                PromotionPayload(
                    agent=key.agent_id,
                    role=Role(key.role),
                    node=key.node,
                    exam_id=sched_id,
                    from_status=CellStatus.SHADOW,
                    to_status=CellStatus.CANDIDATE,
                ),
                run_id=run_id,
            )
            transitions.append(
                Transition(
                    agent_id=key.agent_id,
                    role=key.role,
                    node=key.node,
                    from_status=CellStatus.SHADOW.value,
                    to_status=CellStatus.CANDIDATE.value,
                    reason="candidate",
                    kind="promotion",
                    seq=ev.seq,
                    exam_id=sched_id,
                    criteria=snap,
                )
            )
            status = CellStatus.CANDIDATE
            if exam is not None:         # 同扫描收官暗考（§7.3 schedule_exam 落点）
                status = _run_exam(
                    exam, key, status, ledger, transitions, run_id=run_id
                )

        # ── 转换②（存量 candidate）：在途考试的收官 ──
        elif status is CellStatus.CANDIDATE:
            # 直晋提名格（exam_id 前缀 entry-fast:）在兑现窗闭后冻结：不再佐证、
            # 不再暗考、不再重考——§8.4 孵化是冷启动机制，窗闭后格回归 §7.3 常规
            # 判据（悬置 candidate 不是 active，不进 ① 的分母）。非提名格照旧。
            if (
                lane_retry
                and index.nominated_at(key) is not None
                and not _entry_in_window(index, key, as_of_dt, entry_window)
            ):
                continue
            if exam is not None:
                # 直晋候选人 v2 收官序（exam_id 前缀 entry-fast:；M0 定稿）：
                #   1) 生产佐证达标 → 免暗考直转 active（考试提名 + 生产佐证双闸）；
                #   2) 宽限期内（ENTRY_GRACE_DAYS）→ 等下一夜，先攒生产数据；
                #   3) 宽限期满仍未佐证 → §8.2 暗考兜底（及格线逐字不动）。
                # 关闭态（GRACE=0 且 CORROB=0）与旧口径逐步等价：直接落暗考。
                nominated = index.nominated_at(key) is not None
                fast_lane = (
                    nominated
                    and (grace_days > 0 or corrob_on)
                    and _entry_in_window(index, key, as_of_dt, entry_window)
                )
                if fast_lane and _entry_corroborated(view, key, index):
                    corrob_id = f"entry-corroborated:{key.agent_id}:{key.role}:{key.node}"
                    ev = ledger.append(
                        LedgerKind.PROMOTION,
                        PromotionPayload(
                            agent=key.agent_id,
                            role=Role(key.role),
                            node=key.node,
                            exam_id=corrob_id,
                            from_status=CellStatus.CANDIDATE,
                            to_status=CellStatus.ACTIVE,
                        ),
                        run_id=run_id,
                    )
                    transitions.append(
                        Transition(
                            agent_id=key.agent_id,
                            role=key.role,
                            node=key.node,
                            from_status=CellStatus.CANDIDATE.value,
                            to_status=CellStatus.ACTIVE.value,
                            reason="exam_pass",
                            kind="promotion",
                            seq=ev.seq,
                            exam_id=corrob_id,
                        )
                    )
                elif fast_lane and grace_days > 0:
                    nom_at = index.nominated_at(key)
                    if nom_at is not None and (
                        as_of_dt - nom_at
                    ) < timedelta(days=grace_days):
                        pass                    # 宽限期内：不考，等佐证
                    else:
                        _run_exam(
                            exam, key, status, ledger, transitions, run_id=run_id
                        )
                else:
                    _run_exam(exam, key, status, ledger, transitions, run_id=run_id)

        # ── 转换③/④：active → shadow（周期复核｜衰退）| dormant（休眠）──
        elif status is CellStatus.ACTIVE:
            # 转换⑥（周期复核熄灭，§8.1 淘汰通道；M0 定稿 v2）：FC_DAYS>0 时每
            # active 格按周期传唤复核考（相位与趋势判定在回调侧），连续不达标
            # → 熄灭（reason=decay，④⑤ 度量如实计数）。
            if fc_enabled:
                outcome = for_cause(
                    agent_id=key.agent_id,
                    role=key.role,
                    node=key.node,
                    from_status=status,
                )
                if outcome is not None and bool(getattr(outcome, "decayed", False)):
                    ev = ledger.append(
                        LedgerKind.DEMOTION,
                        DemotionPayload(
                            agent=key.agent_id,
                            role=Role(key.role),
                            node=key.node,
                            reason="decay",
                        ),
                        run_id=run_id,
                    )
                    transitions.append(
                        Transition(
                            agent_id=key.agent_id,
                            role=key.role,
                            node=key.node,
                            from_status=CellStatus.ACTIVE.value,
                            to_status=CellStatus.SHADOW.value,
                            reason="decay",
                            kind="demotion",
                            seq=ev.seq,
                            exam_id=str(getattr(outcome, "exam_id", "") or None),
                        )
                    )
                    continue
            if _decay_below_baseline(key, index, activated_at=cell.activated_at):
                ev = ledger.append(
                    LedgerKind.DEMOTION,
                    DemotionPayload(
                        agent=key.agent_id,
                        role=Role(key.role),
                        node=key.node,
                        reason="decay",
                    ),
                    run_id=run_id,
                )
                transitions.append(
                    Transition(
                        agent_id=key.agent_id,
                        role=key.role,
                        node=key.node,
                        from_status=CellStatus.ACTIVE.value,
                        to_status=CellStatus.SHADOW.value,
                        reason="decay",
                        kind="demotion",
                        seq=ev.seq,
                    )
                )
            elif _dormant_due(key, index):
                ev = ledger.append(
                    LedgerKind.DEMOTION,
                    DemotionPayload(
                        agent=key.agent_id,
                        role=Role(key.role),
                        node=key.node,
                        reason="dormant",
                    ),
                    run_id=run_id,
                )
                transitions.append(
                    Transition(
                        agent_id=key.agent_id,
                        role=key.role,
                        node=key.node,
                        from_status=CellStatus.ACTIVE.value,
                        to_status=CellStatus.DORMANT.value,
                        reason="dormant",
                        kind="demotion",
                        seq=ev.seq,
                    )
                )

        # ── 转换⑤：dormant → active（n_eff ≥ 5 快速重激活，需流量回潮）──
        elif status is CellStatus.DORMANT:
            if (
                cell.n_eff >= p.demote.REACTIVATE_N
                and not _family_taken(key)
                and _reactivation_live(key, index)
            ):
                occupied.add((key.role, key.node.partition("/")[0], key.agent_id))
                react_id = f"reactivate:{key.agent_id}:{key.role}:{key.node}"
                ev = ledger.append(
                    LedgerKind.PROMOTION,
                    PromotionPayload(
                        agent=key.agent_id,
                        role=Role(key.role),
                        node=key.node,
                        exam_id=react_id,
                        from_status=CellStatus.DORMANT,
                        to_status=CellStatus.ACTIVE,
                    ),
                    run_id=run_id,
                )
                transitions.append(
                    Transition(
                        agent_id=key.agent_id,
                        role=key.role,
                        node=key.node,
                        from_status=CellStatus.DORMANT.value,
                        to_status=CellStatus.ACTIVE.value,
                        reason="reactivate",
                        kind="promotion",
                        seq=ev.seq,
                        exam_id=react_id,
                    )
                )

    # 收尾：把本轮事件重放进 cells 表（整表重建，SPEC-1 §2 重放等价语义）。
    agg_cells(ledger, as_of_dt, params=p)
    return transitions


def _run_exam(
    exam: ExamRunnerCallback,
    key: CellKey,
    from_status: CellStatus,
    ledger: KernelLedger,
    transitions: list[Transition],
    *,
    run_id: str | None,
) -> CellStatus:
    """执行一场晋升暗考并落状态事件；返回考后格状态（§7.3 转换②）。

    - 回调返回 None（无卷/回调内冷却拒绝）→ 保持 candidate；
    - 过 → active：回调已落 promotion 事件（``seq_promotion`` 非 None）时不重复落；
    - 不过 → shadow：candidate→shadow 事件由本函数落（回调只考试不写格），
      该事件同时是判据④冷却门的账本依据（§8.2）。
    """
    outcome = exam(
        agent_id=key.agent_id,
        role=key.role,
        node=key.node,
        from_status=from_status,
    )
    if outcome is None:
        return from_status
    passed = bool(getattr(outcome, "passed", False))
    exam_id = str(getattr(outcome, "exam_id", "") or f"sched:{key.agent_id}:{key.role}:{key.node}")
    if passed:
        seq = getattr(outcome, "seq_promotion", None)
        if seq is None:
            ev = ledger.append(
                LedgerKind.PROMOTION,
                PromotionPayload(
                    agent=key.agent_id,
                    role=Role(key.role),
                    node=key.node,
                    exam_id=exam_id,
                    from_status=from_status,
                    to_status=CellStatus.ACTIVE,
                ),
                run_id=run_id,
            )
            seq = ev.seq
        transitions.append(
            Transition(
                agent_id=key.agent_id,
                role=key.role,
                node=key.node,
                from_status=from_status.value,
                to_status=CellStatus.ACTIVE.value,
                reason="exam_pass",
                kind="promotion",
                seq=int(seq),
                exam_id=exam_id,
            )
        )
        return CellStatus.ACTIVE
    ev = ledger.append(
        LedgerKind.PROMOTION,
        PromotionPayload(
            agent=key.agent_id,
            role=Role(key.role),
            node=key.node,
            exam_id=exam_id,
            from_status=from_status,
            to_status=CellStatus.SHADOW,
        ),
        run_id=run_id,
    )
    transitions.append(
        Transition(
            agent_id=key.agent_id,
            role=key.role,
            node=key.node,
            from_status=from_status.value,
            to_status=CellStatus.SHADOW.value,
            reason="exam_fail",
            kind="promotion",
            seq=ev.seq,
            exam_id=exam_id,
        )
    )
    return CellStatus.SHADOW
