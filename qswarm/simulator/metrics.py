"""simulator.metrics：SPEC-4 §2 全部 9 项度量的采集与聚合。

数据流纪律（§0 关键工程决策：度量从内核输出侧采集）：
- 机制状态只从**账本事件**（deposit / promotion / demotion / dispatch）与
  ``kernel.cells.agg_cells`` / ``kernel.replay.rebuild_cells`` 的重建结果读取——
  与在线机制同一实现，杜绝双实现漂移；
- 「真值」只从仿真世界的隐藏设定读取，且以 :class:`WorldTruth` **只读快照**进入
  度量层（frozen dataclass + MappingProxyType，世界对象不可变）：快照构造后与
  世界池脱钩，机制面永远看不到 archetype / q（§1.1 纪律）；
- 「任务数」口径：§2 的时长度量以任务数为单位（非墙钟）。:class:`TaskTimeline`
  把账本事件 ts（内核时钟签发）映射到全局任务序号坐标——按仿真日累计任务数 +
  日内线性插值。要求落账时钟沿 seq 单调不减（引擎用 SteppedClock 式时钟即满足）。

九项度量 → 函数对照（§2 表，逐项可单测）：
  ① 晋升查准   :func:`promotion_precision`      （≥ 0.90）
  ② 晋升查全   :func:`promotion_recall`          （≥ 0.70，500 任务内）
  ③ 中位晋升时长 :func:`median_time_to_active`    （真专家，≤ 200）
  ④ 误熄灭率   :func:`wrong_extinguish_rate`      （≤ 0.05）
  ⑤ 退化响应   :func:`degradation_response`       （漂移→熄灭任务数中位，≤ 150）
  ⑥ 探索 regret :func:`oracle_route_stream` / :func:`regret_curve`（随任务数的曲线；
     实际侧可经 :func:`routing_records_from_ledger` 从 dispatch 账本事件重建）
  ⑦ 冷启动长度 :func:`cold_start_length` / :func:`cold_start_seeded_vs_unseeded`
     （超均匀基线 +5pp 的任务数；播种 vs 无播种对照，§1.5/§8.4）
  ⑧ S2 饥饿传导 :func:`s2_starvation_propagation`（verify_error 扫描的中位增幅 ≤ 2×）
  ⑨ 重放等价   :func:`replay_equivalence`         （任意时点 rebuild 与在线一致，100%）

多种子聚合：:func:`aggregate_seeds` / :func:`aggregate_run_metrics` 对逐种子结果
产出均值 ± 样本标准差（n≥2 取 ``statistics.stdev``；n=1 时 std=None；None 值跳过）。

口径说明（对 §2 公式的两处工程化裁定，均写入 docstring 供 sim_report 引用）：
- ⑥/⑦ 的「对错」按**期望判对率** ``clip(q·(1−0.3d))``（§1.3 判对概率公式）计，
  不取 verdict 的随机实现——regret 度量的是路由策略质量，world 噪声不属于路由；
  该基线对同一任务流逐任务输出（含 cache_hit 任务），oracle 恒取隐藏 q 的 argmax。
- ② 的查全分母限定「500 任务窗内已有存款流量的 (role, node)」上的前 20% 真值格
  ——零流量格子机制上不可晋升，计入分母会引入与机制无关的系统性低估。
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Callable, Iterable, Mapping, Sequence

from qswarm.contracts import (
    CellStatus,
    LedgerKind,
    ParamsConfig,
    Role,
    RunMode,
)
from qswarm.kernel import (
    CellKey,
    CellView,
    KernelLedger,
    agg_cells,
    diff_cells,
    node_prefixes,
    rebuild_cells,
)
from qswarm.kernel.ledger import _coerce_utc as _to_dt  # 内核统一的 ts 解析（UTC 强制）

from .verdict import DIFFICULTY_PENALTY, P_CORRECT_MAX, P_CORRECT_MIN
from .world import AgentPool, Archetype, WorldHiddenParams

__all__ = [
    "SIM_T0",
    "CheckpointEquivalence",
    "CellEpisode",
    "ColdStartComparison",
    "MeanStd",
    "OracleStep",
    "RegretPoint",
    "ReplayEquivalenceReport",
    "RoutingRecord",
    "RunMetrics",
    "S2StarvationReport",
    "TaskTimeline",
    "WorldTruth",
    "aggregate_run_metrics",
    "aggregate_seeds",
    "cell_episodes",
    "cold_start_length",
    "cold_start_seeded_vs_unseeded",
    "collect_run_metrics",
    "degradation_response",
    "expected_accuracy_series",
    "expected_correctness",
    "mean_std",
    "median_time_to_active",
    "oracle_route_stream",
    "promotion_precision",
    "promotion_recall",
    "regret_curve",
    "regret_late_over_early",
    "replay_equivalence",
    "reporter_role_for_mode",
    "routing_records_from_ledger",
    "snapshot_world_truth",
    "split_node",
    "s2_starvation_propagation",
    "uniform_accuracy_series",
    "wrong_extinguish_rate",
]

#: 仿真日轴原点（与 tests/test_kernel_replay 的 T0 惯例一致；引擎可注入自己的 t0）。
SIM_T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)

_SECONDS_PER_DAY = 86400.0


# ── 真值只读快照（§1.1 隐藏设定；世界对象不可变）────────────────


@dataclass(frozen=True)
class WorldTruth:
    """度量侧的隐藏真值只读快照。

    构造后与 :class:`~qswarm.simulator.world.AgentPool` 脱钩：内层映射以
    ``MappingProxyType`` 包装（改写即 TypeError），字段赋值被 frozen 拒绝——
    「世界对象不可变」在度量层结构成立。``capability`` 与
    ``SimAgent.capability`` 逐位同式（退化漂移按 t 现算、类型级 clip [0.05, 0.95]）。
    """

    agents: tuple[str, ...]
    role: Mapping[str, str]
    archetype: Mapping[str, str]
    family_q: Mapping[str, Mapping[str, float]]
    type_eps: Mapping[str, Mapping[str, float]]
    expert_family: Mapping[str, str | None]
    t_drift: Mapping[str, int | None]
    hidden: WorldHiddenParams

    @property
    def roles(self) -> tuple[str, ...]:
        """roster 中出现过的角色（字典序稳定）。"""
        return tuple(sorted(set(self.role.values())))

    def agents_with_role(self, role: str | Role) -> tuple[str, ...]:
        """某角色的全部 agent（池内插入序 = roster 顺序，稳定）。"""
        r = Role(role).value
        return tuple(a for a in self.agents if self.role[a] == r)

    def capability(
        self, agent: str, family: str, type_: str | None = None, *, t: int = 0
    ) -> float:
        """真值能力：家族级返回 q_f(t)（漂移按 t 现算）；类型级 clip(q_f(t)+ε_t)。"""
        q_f = self.family_q[agent][family]
        if (
            self.archetype[agent] == Archetype.DEGRADED.value
            and self.t_drift[agent] is not None
            and t >= self.t_drift[agent]
        ):
            q_f -= self.hidden.drift_delta
        if type_ is None:
            return q_f
        eps = self.type_eps[agent][f"{family}/{type_}"]
        return min(0.95, max(0.05, q_f + eps))

    def node_capability(self, agent: str, node: str, *, t: int = 0) -> float:
        """格子节点（family 或 family/type）上的真值能力。"""
        family, type_ = split_node(node)
        return self.capability(agent, family, type_, t=t)

    def top_fraction(
        self, role: str | Role, node: str, *, frac: float = 0.2, t: int = 0
    ) -> tuple[str, ...]:
        """(role, node) 上真值能力前 ``frac`` 的 agent（k = max(1, ⌈frac·n⌉)，平手按 id）。

        即 §2 「该类型前 20%」的集合判定器：查准的分母侧命中集 / 查全的分子侧真值格。
        """
        pool = self.agents_with_role(role)
        if not pool:
            return ()
        ranked = sorted(
            pool, key=lambda a: (-self.node_capability(a, node, t=t), a)
        )
        k = max(1, math.ceil(frac * len(ranked)))
        return tuple(ranked[:k])


def snapshot_world_truth(pool: AgentPool) -> WorldTruth:
    """从世界池拍出只读真值快照（深拷贝 + MappingProxyType；此后改池不影响快照）。"""
    role: dict[str, str] = {}
    arch: dict[str, str] = {}
    expert: dict[str, str | None] = {}
    drift: dict[str, int | None] = {}
    family_q: dict[str, Mapping[str, float]] = {}
    type_eps: dict[str, Mapping[str, float]] = {}
    for a in pool.agents:
        role[a.agent_id] = a.role.value
        arch[a.agent_id] = a.ground.archetype.value
        expert[a.agent_id] = a.ground.expert_family
        drift[a.agent_id] = a.ground.t_drift
        family_q[a.agent_id] = MappingProxyType(dict(a.family_q))
        type_eps[a.agent_id] = MappingProxyType(dict(a.ground.type_eps))
    return WorldTruth(
        agents=tuple(a.agent_id for a in pool.agents),
        role=MappingProxyType(role),
        archetype=MappingProxyType(arch),
        family_q=MappingProxyType(family_q),
        type_eps=MappingProxyType(type_eps),
        expert_family=MappingProxyType(expert),
        t_drift=MappingProxyType(drift),
        hidden=pool.hidden_params,
    )


def split_node(node: str) -> tuple[str, str | None]:
    """格子节点 → (family, type|None)：``'injection/sqli'`` → ``('injection','sqli')``。"""
    family, _, type_ = node.partition("/")
    return family, (type_ or None)


def expected_correctness(
    q: float, difficulty: float, *, penalty: float = DIFFICULTY_PENALTY
) -> float:
    """期望判对率 = clip(q·(1 − 0.3·d), 0.01, 0.99)（§1.3 判对概率公式，含概率域夹取）。"""
    return min(P_CORRECT_MAX, max(P_CORRECT_MIN, q * (1.0 - penalty * difficulty)))


# ── 任务时钟：账本 ts → 全局任务序号坐标（§2「任务数」口径）──────


class TaskTimeline:
    """仿真日轴 ↔ 全局任务序号坐标的双向尺。

    - ``task_index(ts)``：事件 ts 的任务坐标 = 该日此前累计任务数 + 日内进度 ×
      当日任务数（线性插值，沿单调时钟严格单调）；
    - ``cum_tasks(day)``：截至 day 日结束（含）累计派发任务数；
    - 坐标早于 t0 夹取为 0，晚于末日夹取为总任务数（度量只做差，单调性保住）。
    """

    def __init__(self, day_counts: Sequence[int], *, t0: datetime = SIM_T0) -> None:
        self.day_counts = tuple(int(c) for c in day_counts)
        if any(c < 0 for c in self.day_counts):
            raise ValueError(f"每日任务数必须非负：{self.day_counts}")
        self.t0 = t0
        cum: list[int] = []
        total = 0
        for c in self.day_counts:
            cum.append(total)
            total += c
        self._cum_before = tuple(cum)
        self.total_tasks = total

    @classmethod
    def from_tasks(cls, tasks: Iterable, *, t0: datetime = SIM_T0) -> "TaskTimeline":
        """从世界任务流构造（按 task.day 计数；缺日补 0，保持日轴连续）。"""
        counts: Counter[int] = Counter(int(getattr(t, "day")) for t in tasks)
        n_days = max(counts, default=-1) + 1
        return cls([counts.get(d, 0) for d in range(n_days)], t0=t0)

    def day_frac(self, ts: str | datetime) -> float:
        """ts → 以天为单位的浮点日坐标（(ts − t0)/86400）。"""
        return (_to_dt(ts) - self.t0).total_seconds() / _SECONDS_PER_DAY

    def task_index(self, ts: str | datetime) -> float:
        """ts → 全局任务序号坐标（见类 docstring）。"""
        f = self.day_frac(ts)
        if f <= 0.0:
            return 0.0
        day = int(f)
        if day >= len(self.day_counts):
            return float(self.total_tasks)
        return self._cum_before[day] + (f - day) * self.day_counts[day]

    def cum_tasks(self, day: int) -> int:
        """截至 day 日结束（含）累计任务数；day<0 → 0，越界 → 总数。"""
        if day < 0:
            return 0
        if day >= len(self.day_counts):
            return self.total_tasks
        return self._cum_before[day] + self.day_counts[day]


def _tasks_done_by(timeline: TaskTimeline, ts: str | datetime) -> float:
    """事件发生时「已完成派发」的任务数（按事件所在仿真日结束的累计数计）。

    promote_scan 是夜间批：格子状态变更发生在当日全部任务之后，故窗口归属与
    时长折算统一取「所在日的累计任务数」——日内秒级偏移不产生半格误差。
    """
    return float(timeline.cum_tasks(int(math.floor(timeline.day_frac(ts)))))


# ── 账本 → 格子轨迹（度量的事件侧输入）──────────────────────────


@dataclass(frozen=True)
class CellEpisode:
    """一格 (agent, role, node) 的孵化/熄灭轨迹（按 seq 序重放账本事件得到）。

    - ``first_deposit_ts``：首笔存款（含 §8.4 origin=exam 播种——播种本就推动入格，
      从影子起算的晋升时长以它为起点）；无存款的格不会出现（§7.1 lazy allocation）；
    - ``activations``：to_status=active 的 promotion 事件 ts（升序，含重激活）；
    - ``decay_demotions`` / ``dormant_demotions``：mechanism 侧 demotion（reason）。
    """

    agent_id: str
    role: str
    node: str
    first_deposit_ts: str | None = None
    activations: tuple[str, ...] = ()
    exam_fails: tuple[str, ...] = ()
    decay_demotions: tuple[str, ...] = ()
    dormant_demotions: tuple[str, ...] = ()

    @property
    def key(self) -> CellKey:
        return CellKey(self.agent_id, self.role, self.node)

    @property
    def first_active_ts(self) -> str | None:
        return self.activations[0] if self.activations else None


def cell_episodes(ledger: KernelLedger) -> dict[CellKey, CellEpisode]:
    """把账本 deposit/promotion/demotion 事件重放成逐格轨迹（度量专用，只读账本）。"""
    first_dep: dict[CellKey, str] = {}
    activations: dict[CellKey, list[str]] = {}
    fails: dict[CellKey, list[str]] = {}
    decay: dict[CellKey, list[str]] = {}
    dormant: dict[CellKey, list[str]] = {}

    for ev in ledger.read(
        kinds=(LedgerKind.DEPOSIT, LedgerKind.PROMOTION, LedgerKind.DEMOTION)
    ):
        p: Mapping = ev.payload
        if ev.kind is LedgerKind.DEPOSIT:
            for nd in node_prefixes(p["taxonomy_path"]):
                key = CellKey(str(p["agent"]), str(p["role"]), nd)
                first_dep.setdefault(key, ev.ts)
        elif ev.kind is LedgerKind.PROMOTION:
            key = CellKey(str(p["agent"]), str(p["role"]), str(p["node"]))
            if str(p["to_status"]) == CellStatus.ACTIVE.value:
                activations.setdefault(key, []).append(ev.ts)
            elif (
                str(p["from_status"]) == CellStatus.CANDIDATE.value
                and str(p["to_status"]) == CellStatus.SHADOW.value
            ):
                fails.setdefault(key, []).append(ev.ts)   # 暗考不过（冷却门依据）
        else:                                             # demotion（reason 决定去向）
            key = CellKey(str(p["agent"]), str(p["role"]), str(p["node"]))
            bucket = decay if str(p["reason"]) == "decay" else dormant
            bucket.setdefault(key, []).append(ev.ts)

    keys = set(first_dep) | set(activations) | set(fails) | set(decay) | set(dormant)
    return {
        key: CellEpisode(
            agent_id=key.agent_id,
            role=key.role,
            node=key.node,
            first_deposit_ts=first_dep.get(key),
            activations=tuple(activations.get(key, ())),
            exam_fails=tuple(fails.get(key, ())),
            decay_demotions=tuple(decay.get(key, ())),
            dormant_demotions=tuple(dormant.get(key, ())),
        )
        for key in keys
    }


def deposit_nodes(ledger: KernelLedger) -> set[str]:
    """账本中出现过的全部存款节点（含 family 与 family/type 两级前缀格）。"""
    nodes: set[str] = set()
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        nodes.update(node_prefixes(ev.payload["taxonomy_path"]))
    return nodes


def _check_frac(frac: float) -> None:
    if not 0.0 < frac <= 1.0:
        raise ValueError(f"frac 必须 ∈ (0, 1]：{frac}")


# ── ① 晋升查准（§2：≥ 0.90）─────────────────────────────────────


def promotion_precision(
    ledger: KernelLedger,
    truth: WorldTruth,
    timeline: TaskTimeline,
    *,
    frac: float = 0.2,
) -> float | None:
    """被晋升为 active 的格子中，真 q_t 位于该 (role, node) 前 ``frac`` 的比例。

    - 格子口径 = 账本内**首次**升 active 的去重 (agent, role, node)（含 dormant 重激活
      的格只计一次；升 active 多次的格以首次为准）；
    - 「真 q_t」按激活时点的真值能力排名（退化漂移在激活时刻生效，t 取激活坐标）；
    - 无任何晋升 → None（样本不足，不计入聚合）。
    """
    _check_frac(frac)
    hits = total = 0
    for key, ep in cell_episodes(ledger).items():
        if not ep.activations:
            continue
        total += 1
        t_act = int(_tasks_done_by(timeline, ep.activations[0]))
        if key.agent_id in truth.top_fraction(
            key.role, key.node, frac=frac, t=int(t_act)
        ):
            hits += 1
    return hits / total if total else None


# ── ② 晋升查全（§2：≥ 0.70，500 任务内）────────────────────────


def promotion_recall(
    ledger: KernelLedger,
    truth: WorldTruth,
    timeline: TaskTimeline,
    *,
    frac: float = 0.2,
    task_window: int = 500,
    nodes: Sequence[str] | None = None,
) -> float | None:
    """真值前 ``frac`` 格子中，在 ``task_window`` 个任务内被晋升的比例。

    分母 = 「窗内已有存款流量的 (role, node)」上的全部前 ``frac`` 真值格（零流量格
    机制上不可晋升，计入会引入与机制无关的系统性低估——见模块 docstring 口径说明）；
    分子 = 其中首次升 active 的任务坐标 ≤ task_window 者。无分母格 → None。
    """
    _check_frac(frac)
    if task_window < 0:
        raise ValueError(f"task_window 必须 ≥ 0：{task_window}")
    traffic_pairs: set[tuple[str, str]] = set()
    node_filter = None if nodes is None else set(nodes)
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        if _tasks_done_by(timeline, ev.ts) > task_window:
            continue
        role = str(ev.payload["role"])
        for nd in node_prefixes(ev.payload["taxonomy_path"]):
            if node_filter is None or nd in node_filter:
                traffic_pairs.add((role, nd))
    top_cells: set[tuple[str, str, str]] = set()
    for role, node in traffic_pairs:
        for agent in truth.top_fraction(role, node, frac=frac, t=task_window):
            top_cells.add((agent, role, node))
    if not top_cells:
        return None
    episodes = cell_episodes(ledger)
    promoted = 0
    for agent, role, node in top_cells:
        ep = episodes.get(CellKey(agent, role, node))
        if (
            ep is not None
            and ep.first_active_ts is not None
            and _tasks_done_by(timeline, ep.first_active_ts) <= task_window
        ):
            promoted += 1
    return promoted / len(top_cells)


# ── ③ 中位晋升时长（§2：真专家 ≤ 200 任务）─────────────────────


def median_time_to_active(
    ledger: KernelLedger,
    truth: WorldTruth,
    timeline: TaskTimeline,
    *,
    only_experts: bool = True,
    family_scoped: bool = True,
) -> float | None:
    """从影子到 active 的任务数中位数（§2 口径：真专家）。

    - 每个有晋升的格贡献一个观测：``t(首次升 active) − t(首笔存款)``（任务坐标差）；
    - ``only_experts=True`` 只取 EXPERT 型 agent 的格；``family_scoped=True`` 再限定
      节点落在其真专家家族（family 或 family/* 子树）；
    - 无观测 → None。
    """
    durations: list[float] = []
    for key, ep in cell_episodes(ledger).items():
        if not ep.activations or ep.first_deposit_ts is None:
            continue
        if only_experts:
            if truth.archetype.get(key.agent_id) != Archetype.EXPERT.value:
                continue
            if family_scoped:
                ef = truth.expert_family.get(key.agent_id)
                if ef is not None and split_node(key.node)[0] != ef:
                    continue
        durations.append(
            _tasks_done_by(timeline, ep.activations[0])
            - _tasks_done_by(timeline, ep.first_deposit_ts)
        )
    return statistics.median(durations) if durations else None


# ── ④ 误熄灭率（§2：≤ 0.05）────────────────────────────────────


def wrong_extinguish_rate(ledger: KernelLedger, truth: WorldTruth) -> float | None:
    """非退化型 active 格子被衰退熄灭（reason=decay）的比例。

    - 分母 = 曾升 active 且 agent 非 DEGRADED 的格；分子 = 其中在**首次激活之后**
      收到 decay demotion 的格（激活前的旧熄灭不算「在任衰退」）；
    - dormant（休眠保留历史账）不是熄灭，不计；无分母 → None。
    """
    ever_active = extinguished = 0
    for key, ep in cell_episodes(ledger).items():
        if not ep.activations:
            continue
        if truth.archetype.get(key.agent_id) == Archetype.DEGRADED.value:
            continue
        ever_active += 1
        act_dt = _to_dt(ep.activations[0])
        if any(_to_dt(ts) > act_dt for ts in ep.decay_demotions):
            extinguished += 1
    return extinguished / ever_active if ever_active else None


# ── ⑤ 退化响应（§2：漂移→熄灭任务数中位 ≤ 150）────────────────


def degradation_response(
    ledger: KernelLedger, truth: WorldTruth, timeline: TaskTimeline
) -> float | None:
    """退化型从 q 漂移到熄灭的任务数中位数。

    - 漂移坐标 = ``truth.t_drift[agent]``（世界隐藏参数，全局任务序号时钟）；
    - 每个退化格取「漂移后第一笔 decay demotion」的响应 = 熄灭坐标 − 漂移坐标
      （漂移前的熄灭与 dormant 休眠都不算响应）；
    - 无观测 → None。
    """
    responses: list[float] = []
    for key, ep in cell_episodes(ledger).items():
        if truth.archetype.get(key.agent_id) != Archetype.DEGRADED.value:
            continue
        t_drift = truth.t_drift.get(key.agent_id)
        if t_drift is None:
            continue
        post = [
            _tasks_done_by(timeline, ts) - t_drift
            for ts in ep.decay_demotions
            if _tasks_done_by(timeline, ts) >= t_drift
        ]
        if post:
            responses.append(min(post))
    return statistics.median(responses) if responses else None


# ── ⑥ 全知 oracle 基线与探索 regret（随任务数的曲线）────────────


def reporter_role_for_mode(task) -> Role:
    """任务的 reporter 角色口径（与 verdict.assign_finding_context 一致）：
    fast 档 → scout，pr/full 档 → auditor（§16.7-1）。"""
    mode = RunMode(getattr(task, "mode", RunMode.FULL))
    return Role.SCOUT if mode is RunMode.FAST else Role.AUDITOR


@dataclass(frozen=True)
class RoutingRecord:
    """引擎侧逐任务派单记录（dispatch 事件的世界侧投影；regret/冷启动的输入）。"""

    task_id: str
    role: str
    node: str
    assignee: str | None
    channel: str | None = None        # specialist | explore | thompson | None


@dataclass(frozen=True)
class OracleStep:
    """全知 oracle 对单个任务的派单与对错基线（按隐藏 q，对同一任务流逐任务输出）。"""

    task_id: str
    t: int
    role: str
    node: str
    agent: str                        # oracle 选中者（隐藏 q 的 argmax）
    q: float                          # 该者真值能力
    p_correct: float                  # oracle 期望判对率（对错基线）
    uniform_p_correct: float          # 同 (role, node) 均匀基线期望判对率


def oracle_route_stream(
    tasks: Sequence,
    truth: WorldTruth,
    *,
    reporter_role: Callable[[object], Role | str] = reporter_role_for_mode,
    penalty: float = DIFFICULTY_PENALTY,
) -> list[OracleStep]:
    """全知 oracle 路由：对同一任务流逐任务输出对错基线（§2 度量⑥的基线侧）。

    oracle = 该任务 reporter 角色池内隐藏 q 的 argmax（平手按 agent_id，确定性）；
    期望判对率按 §1.3 公式 ``clip(q·(1−0.3d))``。同时给出均匀基线（池均值 q）供
    冷启动度量（⑦）作对照序列。
    """
    steps: list[OracleStep] = []
    for task in tasks:
        family, type_, node = _task_node(task)
        role = Role(reporter_role(task)).value
        pool = truth.agents_with_role(role)
        if not pool:
            raise ValueError(
                f"角色 {role!r} 在真值池中无 agent（任务 {getattr(task, 'task_id', '?')}）"
            )
        qs = {a: truth.capability(a, family, type_, t=task.t) for a in pool}
        best = min(qs, key=lambda a: (-qs[a], a))
        steps.append(
            OracleStep(
                task_id=str(getattr(task, "task_id", "")),
                t=int(getattr(task, "t", 0)),
                role=role,
                node=node,
                agent=best,
                q=qs[best],
                p_correct=expected_correctness(qs[best], task.difficulty, penalty=penalty),
                uniform_p_correct=expected_correctness(
                    statistics.fmean(qs.values()), task.difficulty, penalty=penalty
                ),
            )
        )
    return steps


@dataclass(frozen=True)
class RegretPoint:
    """regret 曲线上的一个点：逐任务差距与累计/均值（§2：随任务数单调收窄）。"""

    task_id: str
    t: int
    oracle_p: float
    actual_p: float
    gap: float
    cum_regret: float
    mean_regret: float


def _normalize_assignees(routing) -> dict[str, str | None]:
    """routing 输入归一：Mapping[task_id → assignee] 或 RoutingRecord 序列。"""
    if isinstance(routing, Mapping):
        return {str(k): v for k, v in routing.items()}
    out: dict[str, str | None] = {}
    for r in routing:
        out[str(getattr(r, "task_id"))] = getattr(r, "assignee", None)
    return out


def routing_records_from_ledger(
    ledger: KernelLedger, *, apply_re_dispatch: bool = False
) -> list[RoutingRecord]:
    """从 dispatch 账本事件重建逐任务路由记录（内核输出侧 → ⑥⑦ 的输入桥）。

    度量纪律（模块 docstring）：⑥⑦ 的实际侧不依赖引擎内存态——只凭账本
    （dispatch 事件）+ WorldTruth 即可复算，任意时点可从持久账本重放取证。

    - DISPATCH 按 seq 序读取，同 task_id 多条时**首次为准**（M0 一任务一派单；
      重复条目只可能来自修补场景，取首条与任务到达序对齐）；
    - dispatch 载荷不含 channel（渠道是 RouteDecision 的内存属性，不落账），
      重建记录 channel 恒 None——⑥⑦ 只消费 assignee，不受影响；
    - ``apply_re_dispatch=True``：RE_DISPATCH（task_id/from_agent/to_agent/reason）
      把对应任务的 assignee 改派为 ``to_agent``（改派在后的覆盖在前的）；
      未匹配任何已派单任务的 re_dispatch 忽略（幂等容错）。M0 引擎环内不产生
      该事件，此入口为账本完备性保留。
    """
    kinds = (
        (LedgerKind.DISPATCH, LedgerKind.RE_DISPATCH)
        if apply_re_dispatch
        else (LedgerKind.DISPATCH,)
    )
    by_task: dict[str, RoutingRecord] = {}
    order: list[str] = []
    for ev in ledger.read(kinds=kinds):
        p: Mapping = ev.payload
        tid = str(p["task_id"])
        if ev.kind is LedgerKind.DISPATCH:
            if tid in by_task:
                continue
            assignee = p.get("assignee")
            by_task[tid] = RoutingRecord(
                task_id=tid,
                role=str(p["role"]),
                node=str(p["node"]),
                assignee=str(assignee) if assignee is not None else None,
                channel=None,
            )
            order.append(tid)
        else:                                              # RE_DISPATCH：改派
            rec = by_task.get(tid)
            if rec is not None:
                by_task[tid] = replace(rec, assignee=str(p["to_agent"]))
    return [by_task[tid] for tid in order]


def _task_node(task) -> tuple[str, str | None, str]:
    family = str(task.family)
    raw_type = getattr(task, "type", None)
    type_ = str(raw_type) if raw_type else None
    node = f"{family}/{type_}" if type_ else family
    return family, type_, node


def regret_curve(
    tasks: Sequence,
    truth: WorldTruth,
    routing,
    *,
    penalty: float = DIFFICULTY_PENALTY,
) -> list[RegretPoint]:
    """实际路由 vs 全知 oracle 的差距曲线（§2 度量⑥；逐任务点 + 累计 + 均值）。

    - 两侧都按期望判对率计（见模块 docstring 口径说明）：oracle 侧取隐藏 q 的
      argmax，actual 侧取实际 assignee 的隐藏 q（未派单 → 0，差距即全额 oracle）；
    - ``gap = max(0, p_oracle − p_actual)``（oracle 恒为 argmax ⇒ 逐点 ≥ 0）；
    - routing 接受 Mapping[task_id → assignee] 或 RoutingRecord 序列（后者以
      task_id 对齐任务流，未覆盖的任务按未派单计）。
    """
    assign = _normalize_assignees(routing)
    curve: list[RegretPoint] = []
    cum = 0.0
    for i, task in enumerate(tasks):
        family, type_, _ = _task_node(task)
        role = Role(reporter_role_for_mode(task)).value
        pool = truth.agents_with_role(role)
        q_oracle = (
            max(truth.capability(a, family, type_, t=task.t) for a in pool) if pool else 0.0
        )
        p_oracle = expected_correctness(q_oracle, task.difficulty, penalty=penalty)
        who = assign.get(str(getattr(task, "task_id", "")))
        q_act = truth.capability(who, family, type_, t=task.t) if who is not None else 0.0
        p_actual = expected_correctness(q_act, task.difficulty, penalty=penalty)
        gap = max(0.0, p_oracle - p_actual)
        cum += gap
        curve.append(
            RegretPoint(
                task_id=str(getattr(task, "task_id", "")),
                t=int(getattr(task, "t", 0)),
                oracle_p=p_oracle,
                actual_p=p_actual,
                gap=gap,
                cum_regret=cum,
                mean_regret=cum / (i + 1),
            )
        )
    return curve


# ── ⑦ 冷启动长度（§2：超均匀基线 +5pp 的任务数，播种 < 50）──────


def expected_accuracy_series(
    tasks: Sequence, truth: WorldTruth, routing, *, penalty: float = DIFFICULTY_PENALTY
) -> list[float]:
    """实际路由逐任务的期望准确率序列（隐藏 q 口径；未派单 → 0）。"""
    assign = _normalize_assignees(routing)
    out: list[float] = []
    for task in tasks:
        family, type_, _ = _task_node(task)
        who = assign.get(str(getattr(task, "task_id", "")))
        q = truth.capability(who, family, type_, t=task.t) if who is not None else 0.0
        out.append(expected_correctness(q, task.difficulty, penalty=penalty))
    return out


def uniform_accuracy_series(
    tasks: Sequence,
    truth: WorldTruth,
    *,
    penalty: float = DIFFICULTY_PENALTY,
    reporter_role: Callable[[object], Role | str] = reporter_role_for_mode,
) -> list[float]:
    """均匀路由基线的逐任务期望准确率序列（池均值 q × (1−0.3d)）。"""
    out: list[float] = []
    for task in tasks:
        family, type_, _ = _task_node(task)
        pool = truth.agents_with_role(Role(reporter_role(task)).value)
        mean_q = statistics.fmean(
            [truth.capability(a, family, type_, t=task.t) for a in pool]
        ) if pool else 0.0
        out.append(expected_correctness(mean_q, task.difficulty, penalty=penalty))
    return out


def regret_late_over_early(
    curve: Sequence[RegretPoint], *, parts: int = 3
) -> float | None:
    """⑥ 曲线后段/前段平均差距之比（严格 < 1 判「收窄」；sim_report 可选列口径）。

    - 曲线按 ``parts`` 等分（首尾归属后段：前段 = 前 ``n//parts`` 点，后段 = 末
      ``n//parts`` 点，与中间段互斥）；任一段为空 → None；
    - 前段平均差距为 0（oracle 已达上界、无收窄空间）→ None（无证据 ≠ 收窄）；
    - 曲线为空 → None。
    """
    n = len(curve)
    k = n // parts
    if n == 0 or k == 0:
        return None
    early = statistics.fmean(p.gap for p in curve[:k])
    late = statistics.fmean(p.gap for p in curve[n - k:])
    if early <= 0.0:
        return None
    return late / early


def cold_start_length(
    p_actual: Sequence[float],
    p_baseline: Sequence[float],
    *,
    margin: float = 0.05,
    window: int = 1,
) -> int | None:
    """路由期望准确率首次超过均匀基线 + ``margin`` 的任务数（§2 度量⑦；未超 → None）。

    - 两侧同用尾随 ``window`` 窗滑动均值（window=1 = 不平滑，逐任务判定）；
    - 返回值 = 首次超越处的任务计数（1 起）； 序列等长且非空为前置条件。
    """
    if len(p_actual) != len(p_baseline):
        raise ValueError(
            f"两条序列必须等长：actual={len(p_actual)} vs baseline={len(p_baseline)}"
        )
    if not p_actual:
        return None
    if window < 1:
        raise ValueError(f"window 必须 ≥ 1：{window}")
    if margin < 0:
        raise ValueError(f"margin 必须 ≥ 0：{margin}")
    a = list(p_actual)
    b = list(p_baseline)

    def _roll(xs: list[float], i: int) -> float:
        lo = max(0, i - window + 1)
        return statistics.fmean(xs[lo : i + 1])

    for i in range(len(a)):
        if _roll(a, i) > _roll(b, i) + margin:
            return i + 1
    return None


@dataclass(frozen=True)
class ColdStartComparison:
    """⑦ 冷启动长度的播种 vs 无播种对照（§2 括号口径；§1.5/§8.4 对照组）。

    - ``seeded`` / ``unseeded``：两条实际路由准确率序列各自对同一均匀基线的
      冷启动长度（任务数；未跨越 → None = 观测窗内未起效）；
    - :attr:`delta`：无播种 − 播种（正 = 播种缩短冷启动）；任一侧 None → None；
    - :attr:`seeding_helps`：delta > 0（播种确实更早起效）；
    - :meth:`meets_target`：§2 目标「播种 < 50」（未跨越判不达标）。
    """

    seeded: int | None
    unseeded: int | None
    margin: float
    window: int

    @property
    def delta(self) -> int | None:
        """无播种 − 播种（任务数差；正 = 播种缩短冷启动）。"""
        if self.seeded is None or self.unseeded is None:
            return None
        return self.unseeded - self.seeded

    @property
    def seeding_helps(self) -> bool | None:
        """播种是否比无播种更早起效（delta > 0；证据不足 → None）。"""
        d = self.delta
        return None if d is None else d > 0

    def meets_target(self, target: int = 50) -> bool:
        """§2 目标「播种 < target」：播种侧有跨越且计数严格小于 target。"""
        return self.seeded is not None and self.seeded < target


def cold_start_seeded_vs_unseeded(
    p_actual_seeded: Sequence[float],
    p_actual_unseeded: Sequence[float],
    p_baseline: Sequence[float],
    *,
    margin: float = 0.05,
    window: int = 1,
) -> ColdStartComparison:
    """播种 vs 无播种的冷启动对照（§2 度量⑦括号口径；§8.4 关播种对照组）。

    两条实际路由期望准确率序列对**同一**均匀基线各算冷启动长度（播种组 =
    §8.4 入学考试全开；无播种组 = ``ExamWorldParams(seeding=False)`` 对照臂）；
    任一序列与基线不等长即 ValueError（同任务流对齐是对照的前提）。
    """
    n = len(p_baseline)
    if len(p_actual_seeded) != n or len(p_actual_unseeded) != n:
        raise ValueError(
            "播种/无播种序列必须与基线等长："
            f"seeded={len(p_actual_seeded)} unseeded={len(p_actual_unseeded)} baseline={n}"
        )
    return ColdStartComparison(
        seeded=cold_start_length(p_actual_seeded, p_baseline, margin=margin, window=window),
        unseeded=cold_start_length(p_actual_unseeded, p_baseline, margin=margin, window=window),
        margin=float(margin),
        window=int(window),
    )


# ── ⑧ S2 饥饿传导（§2：verify_error 扫描下中位晋升时长增幅 ≤ 2×）──


@dataclass(frozen=True)
class S2StarvationReport:
    """verify_error ∈ {0.2, 0.35, 0.5} 扫描下的中位晋升时长增幅报告。"""

    medians: Mapping[float, float | None]
    ratios: Mapping[float, float | None]     # 相对 reference 的中位增幅倍数
    reference: float
    limit: float
    passed: bool


def s2_starvation_propagation(
    durations_by_ve: Mapping[float, Sequence[float | None]],
    *,
    reference: float = 0.2,
    limit: float = 2.0,
) -> S2StarvationReport:
    """各 verify_error 档位的中位晋升时长 → 相对基准档位的增幅倍数与达标判定。

    - 中位对 None（该档无晋升观测）跳过；全空档 → median=None → ratio=None；
    - 基准档自身 ratio 恒 1.0（可计算时）；passed = 全部 ratio ≤ limit（含基准档）；
    - 零基准（ref == 0）边界（M0 定稿口径，DECISIONS 2026-10-09）：直晋通道使
      各档中位晋升时长同为 0 → 增幅按 1.0（无传导）；基准档为 0 而他档 >0 时
      增幅不可判（ratio=None，数据不足按未达标计——0→x 的「增幅」无界，不静默放行）。
    """
    medians: dict[float, float | None] = {}
    for ve, vals in durations_by_ve.items():
        obs = [float(v) for v in vals if v is not None]
        medians[float(ve)] = statistics.median(obs) if obs else None
    ref = medians.get(float(reference))
    ratios: dict[float, float | None] = {}
    for ve, m in medians.items():
        if m is None or ref is None:
            ratios[ve] = None
        elif ref == 0.0:
            ratios[ve] = 1.0 if m == 0.0 else None
        else:
            ratios[ve] = m / ref
    if ref not in (None, 0.0):
        ratios[float(reference)] = 1.0
    elif ref == 0.0 and any(m == 0.0 for m in medians.values()):
        ratios[float(reference)] = 1.0
    passed = bool(ratios) and all(r is not None and r <= limit for r in ratios.values())
    return S2StarvationReport(
        medians=MappingProxyType(dict(medians)),
        ratios=MappingProxyType(dict(ratios)),
        reference=float(reference),
        limit=float(limit),
        passed=passed,
    )


# ── ⑨ 重放等价（§2：任意时点 replay 重建与在线一致，100%）───────


@dataclass(frozen=True)
class CheckpointEquivalence:
    """单个时点的重放等价检查结果。"""

    seq: int
    cells: int                                   # 重建格子数
    diffs: int                                   # 逐字段差异数（0 = 等价）
    source: str                                  # captured（在线快照）| as_of（按 ts 重算）
    first_diffs: tuple[str, ...] = ()            # 前 5 条差异（取证）


@dataclass(frozen=True)
class ReplayEquivalenceReport:
    """重放等价总报告：任一时点不一致即 failed（§2 目标 100%，回归项）。"""

    checkpoints: tuple[CheckpointEquivalence, ...]
    cells_compared: int
    field_diffs: int
    table_consistent: bool                       # 末时点 cells 表与视图逐行一致

    @property
    def passed(self) -> bool:
        return self.field_diffs == 0 and self.table_consistent

    @property
    def equivalence_rate(self) -> float:
        """通过时点占比（全部一致 = 100%）。"""
        if not self.checkpoints:
            return 1.0
        ok = sum(1 for c in self.checkpoints if c.diffs == 0)
        return ok / len(self.checkpoints)


def _default_checkpoints(n_events: int, sample: int) -> list[int]:
    """默认检查点：事件数 ≤ sample 全取；否则均匀取 sample 个（含首尾）。"""
    sample = max(2, int(sample))
    if n_events <= sample:
        return list(range(1, n_events + 1))
    step = (n_events - 1) / (sample - 1)
    return sorted({1 + round(i * step) for i in range(sample)})


def _cells_table_matches(conn, view: CellView) -> bool:
    """cells 物理表与视图逐行逐字段比对（写口一致性的在线证据）。"""
    rows = conn.execute(
        "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3,"
        " status, activated_at, last_event_seq FROM cells"
        " ORDER BY agent_id, role, node"
    ).fetchall()
    expect = [
        (
            c.agent_id, c.role, c.node, c.pos_w, c.neg_w, c.n_eff, c.n_eff_s2s3,
            c.status, c.activated_at, c.last_event_seq,
        )
        for _, c in sorted(view.cells.items())
    ]
    return rows == expect


def replay_equivalence(
    ledger: KernelLedger,
    *,
    checkpoints: Sequence[int] | None = None,
    params: ParamsConfig | None = None,
    online: Mapping[int, CellView] | None = None,
    check_table: bool = True,
    sample: int = 12,
) -> ReplayEquivalenceReport:
    """任意时点 replay 重建 cells 与在线状态的一致率（§2 度量⑨；调 kernel.replay 本体）。

    - 每个检查点 seq k：``rebuild_cells(ledger, upto_seq=k)`` 与「在线状态」
      ``diff_cells`` 逐字段比对（浮点逐位相等，不容差，§6-6）；
    - 在线状态优先取 ``online[k]``（引擎在运行时经 ``agg_cells`` 捕获的快照——
      最诚实的口径）；未提供时按 as_of=第 k 条事件 ts 重算（要求时钟沿 seq 单调，
      并列 ts 的时点自动并入同窗，等价性不受 ties 影响）；
    - ``check_table``：末时点再对 cells 物理表逐行比对（写口证据）；
    - 空账本 → 平凡通过（无格子可比）。
    """
    events = ledger.read()
    if not events:
        return ReplayEquivalenceReport((), 0, 0, True)
    latest = events[-1].seq
    by_seq = {ev.seq: ev for ev in events}
    # 并列 ts 归并：seq 升序 ⇒ ts 不减；k_eff = 最后一条 ts ≤ ts_k 的事件 seq
    # （二分定位，避免逐检查点线性扫描）
    ts_list = [_to_dt(ev.ts) for ev in events]
    seq_list = [ev.seq for ev in events]

    def _effective_seq(k: int) -> int:
        pos = bisect_right(ts_list, ts_list[k - 1])
        return seq_list[pos - 1]

    cps = (
        _default_checkpoints(len(events), sample)
        if checkpoints is None
        else sorted(int(c) for c in checkpoints)
    )
    bad = [c for c in cps if c < 1 or c > latest]
    if bad:
        raise ValueError(f"检查点越出账本窗口 [1, {latest}]：{bad}")

    captured = dict(online) if online else {}
    checks: list[CheckpointEquivalence] = []
    cells_total = 0
    diff_total = 0
    last_view: CellView | None = None
    for k in cps:
        ts_k = by_seq[k].ts
        if k in captured:
            snap, source = captured[k], "captured"
            rebuilt = rebuild_cells(ledger, k, params=params)
        else:
            # 并列 ts：as_of 过滤口径下同 ts 的后继事件同属该时点 → 重放窗取其最大 seq
            snap = agg_cells(ledger, ts_k, params=params)
            rebuilt = rebuild_cells(ledger, _effective_seq(k), params=params)
            source = "as_of"
        diffs = diff_cells(snap, rebuilt)
        cells_total += len(snap.cells)
        diff_total += len(diffs)
        checks.append(
            CheckpointEquivalence(
                seq=k,
                cells=len(rebuilt.cells),
                diffs=len(diffs),
                source=source,
                first_diffs=tuple(str(d) for d in diffs[:5]),
            )
        )
        last_view = snap

    table_ok = True
    if check_table and last_view is not None:
        final = agg_cells(ledger, events[-1].ts, params=params)
        table_ok = _cells_table_matches(ledger.conn, final)
    return ReplayEquivalenceReport(
        checkpoints=tuple(checks),
        cells_compared=cells_total,
        field_diffs=diff_total,
        table_consistent=table_ok,
    )


# ── 单次运行度量集与多种子聚合 ─────────────────────────────────


@dataclass(frozen=True)
class RunMetrics:
    """一次仿真运行的 §2 度量集（⑧ S2 饥饿传导为跨运行对比，不在此内）。"""

    promotion_precision: float | None
    promotion_recall: float | None
    median_time_to_active: float | None
    wrong_extinguish_rate: float | None
    degradation_response: float | None
    regret_total: float | None                # 终点累计 regret（曲线 = regret_curve）
    regret_per_task: float | None             # 终点平均 regret（单调收窄的读数）
    cold_start_length: float | None
    regret_late_over_early: float | None = None   # ⑥ 曲线后段/前段平均差距之比
    replay_equivalent: bool | None = None     # 由 replay_equivalence 填入

    def to_dict(self) -> dict[str, float | bool | None]:
        return asdict(self)


def collect_run_metrics(
    ledger: KernelLedger,
    truth: WorldTruth,
    timeline: TaskTimeline,
    tasks: Sequence,
    routing,
    *,
    frac: float = 0.2,
    task_window: int = 500,
    margin: float = 0.05,
    cold_window: int = 1,
    penalty: float = DIFFICULTY_PENALTY,
    replay_equivalent: bool | None = None,
) -> RunMetrics:
    """一次运行的 ①–⑦ + ⑨ 一次采齐（⑧ 由跨 verify_error 运行的报告另行给出）。"""
    curve = regret_curve(tasks, truth, routing, penalty=penalty)
    actual = expected_accuracy_series(tasks, truth, routing, penalty=penalty)
    baseline = uniform_accuracy_series(tasks, truth, penalty=penalty)
    cold = cold_start_length(actual, baseline, margin=margin, window=cold_window)
    return RunMetrics(
        promotion_precision=promotion_precision(ledger, truth, timeline, frac=frac),
        promotion_recall=promotion_recall(
            ledger, truth, timeline, frac=frac, task_window=task_window
        ),
        median_time_to_active=median_time_to_active(ledger, truth, timeline),
        wrong_extinguish_rate=wrong_extinguish_rate(ledger, truth),
        degradation_response=degradation_response(ledger, truth, timeline),
        regret_total=curve[-1].cum_regret if curve else None,
        regret_per_task=curve[-1].mean_regret if curve else None,
        cold_start_length=float(cold) if cold is not None else None,
        regret_late_over_early=regret_late_over_early(curve),
        replay_equivalent=replay_equivalent,
    )


@dataclass(frozen=True)
class MeanStd:
    """一个度量在多种子上的均值 ± 样本标准差（n=1 或全 None 时 std=None）。"""

    key: str
    mean: float | None
    std: float | None
    n: int


def mean_std(values: Iterable[float | None], *, key: str = "") -> MeanStd:
    """跳过 None 后取均值 ± 样本标准差（``statistics.stdev``，n ≥ 2；n=1 → std=None）。"""
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return MeanStd(key=key, mean=None, std=None, n=0)
    return MeanStd(
        key=key,
        mean=statistics.fmean(vals),
        std=statistics.stdev(vals) if len(vals) >= 2 else None,
        n=len(vals),
    )


def aggregate_seeds(
    runs: Sequence[Mapping[str, float | None]],
) -> dict[str, MeanStd]:
    """多种子聚合：逐度量键产出 mean ± std（键序 = 首个 run 的键序，稳定）。"""
    keys: list[str] = []
    for run in runs:
        for k in run:
            if k not in keys:
                keys.append(k)
    return {k: mean_std((run.get(k) for run in runs), key=k) for k in keys}


def aggregate_run_metrics(runs: Sequence[RunMetrics]) -> dict[str, MeanStd]:
    """:class:`RunMetrics` 序列 → 逐字段 mean ± std（bool 按其 0/1 数值参与）。"""
    return aggregate_seeds([run.to_dict() for run in runs])
