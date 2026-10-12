"""kernel：编排内核（SPEC-2 滚动导出后实现）。

职责占位（SPEC-1 §2/§4.5）：route / dispatch / cluster / budget / 状态机流转；
ledger、cells、findings.state、clusters.verdict_* 的**唯一写口**（纪律 1/2：
状态变更与账本事件同事务；蜂无时间源，一切 ts 由内核时钟签发）。
M0 阶段（SPEC-4 §0）：仿真器直接调用本包实现，不另写一份。

M0 已落地：
- append-only 账本 + 可注入内核时钟（kernel.ledger，本包唯一写口）；
- 存款聚合器 agg_cells：deposit 事件 → cells 表 + CellView（C*/LOO 基线读取，
  kernel.cells）——挥发/地板/收缩/n_eff/LOO 见模块 docstring；
- 自适应 TILT 路由 route()：active 专员截 K=3 + 探索通道 20% 均匀保底
  （kernel.route，设计 §5.2）——随机种子显式注入、不落账，dispatch/re_route
  事件字段由 RouteDecision 携带、调用方落账；
- 重放重建 rebuild_cells/diff_cells（kernel.replay）：窗口截断重放 → 全量重建
  cells，逐字段 diff 判定重放等价（SPEC-1 §2/§6-6）；
- Thompson sampling 对照路由 route_thompson()（kernel.route_thompson，SPEC-4 §3
  附加实验臂）：逐 (role, node) Beta 后验采样选 agent，仅对照组、默认路由不变；
promote_scan 已落地（kernel.promote_scan，设计 §7.3 孵化状态机：四判据 Wilson 门、
暗考回调驱动的 candidate→active、连续 2 窗衰退熄灭、60 天休眠 / n_eff≥5 快速重激活）。
"""

from .cells import (
    ROOT_PRIOR,
    Baseline,
    CellAgg,
    CellKey,
    CellView,
    agg_cells,
    decay_factor,
    node_prefixes,
)
from .exam_math import centered_exam_weight
from .cluster import (
    CLUSTER_EMBED_SIM_MIN,
    CLUSTER_OVERLAP_MIN,
    S1_SOURCE_W,
    S2_SOURCE_W,
    S3_SOURCE_W,
    ClusterProposal,
    FindingFacts,
    MemberFacts,
    RepairReport,
    SimFn,
    VerdictContext,
    VoteConsensus,
    VoteFacts,
    aggregate_votes,
    apply_human_verdict,
    apply_merges,
    attribute_findings,
    close_panel,
    cluster_findings,
    coarse_mergeable,
    fine_mergeable,
    line_overlap,
    propagate_pending,
    propagate_verdict,
    split_cluster,
)
from .ledger import (
    FixedClock,
    KernelClock,
    KernelLedger,
    LedgerEvent,
    SteppedClock,
    WallClock,
    iso_utc,
    transaction,
)
from .budget import (
    BREAKER_REASON,
    DEGRADED_TIER,
    SEVERITY_RANK,
    BudgetPolicy,
    BudgetState,
    budget_for_mode,
    budget_state,
    on_cost,
    quota_l2,
    quota_s2,
    s2_rank_key,
)
from .dispatch import (
    DISPATCH_MAX_ATTEMPTS,
    FINDING_TRANSITIONS,
    HOP_BUDGET_BY_MODE,
    NON_TERMINAL_FINDING_STATES,
    ROLE_OUTPUT_SCHEMA,
    ROLE_TASK_BOUNDS,
    ROLE_TOOL_BOUNDS,
    TASK_STATE_TERMINAL,
    SchemaRejected,
    attach_cluster,
    build_contract,
    create_task,
    dispatch,
    expire_run,
    hop_budget_for,
    open_finding,
    open_run,
    re_dispatch,
    submit,
    transition_finding,
)
from .reroute import (
    RE_ROUTE_WEAK_W,
    guess_taxonomy,
    is_mismatch,
    re_route_finding,
)
from .route import (
    SPEC_CAP,
    RouteDecision,
    active_specialists,
    adaptive_tilt,
    route,
)
from .route_thompson import (
    TS_CHANNEL,
    beta_posterior,
    posterior_mean,
    route_thompson,
)
from .promote_scan import (
    SLIDING_WINDOW_DAYS,
    WILSON_Z,
    CriteriaSnapshot,
    Transition,
    promote_scan,
    wilson_bounds,
    wilson_lb,
    wilson_ub,
)

# kernel.replay 惰性导出（PEP 562）：``python -m qswarm.kernel.replay`` 会先执行包
# __init__，若在此 eager 导入该模块，runpy 将二次执行并报 RuntimeWarning——
# CLI 是 replay 的主入口，故只在首次属性访问时加载。
_REPLAY_EXPORTS = (
    "CLUSTER_DIFF_FIELDS",
    "CellDiff",
    "DEFAULT_DIFF_FIELDS",
    "FINDING_DIFF_FIELDS",
    "ClusterProj",
    "FindingProj",
    "ProjDiff",
    "cluster_projection",
    "diff_cells",
    "diff_clusters",
    "diff_findings",
    "finding_projection",
    "rebuild_cells",
    "rebuild_clusters",
    "rebuild_findings",
    "rebuild_summary",
)


def __getattr__(name: str):
    if name in _REPLAY_EXPORTS:
        from . import replay

        return getattr(replay, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    # kernel.cells：存款聚合与口碑读取
    "ROOT_PRIOR",
    "Baseline",
    "CellAgg",
    "CellKey",
    "CellView",
    "agg_cells",
    "decay_factor",
    "node_prefixes",
    # kernel.exam_math：考试权重映射（SPEC-6 S5 纯移动上移）
    "centered_exam_weight",
    # kernel.ledger：账本与时钟
    "FixedClock",
    "KernelClock",
    "KernelLedger",
    "LedgerEvent",
    "SteppedClock",
    "WallClock",
    "iso_utc",
    "transaction",
    # kernel.budget：三档预算与熔断（SPEC-2 §4）
    "BREAKER_REASON",
    "DEGRADED_TIER",
    "SEVERITY_RANK",
    "BudgetPolicy",
    "BudgetState",
    "budget_for_mode",
    "budget_state",
    "on_cost",
    "quota_l2",
    "quota_s2",
    "s2_rank_key",
    # kernel.dispatch：委托契约与派单/结论双状态机（SPEC-2 §2）
    "DISPATCH_MAX_ATTEMPTS",
    "FINDING_TRANSITIONS",
    "HOP_BUDGET_BY_MODE",
    "NON_TERMINAL_FINDING_STATES",
    "ROLE_OUTPUT_SCHEMA",
    "ROLE_TASK_BOUNDS",
    "ROLE_TOOL_BOUNDS",
    "TASK_STATE_TERMINAL",
    "attach_cluster",
    "build_contract",
    "create_task",
    "dispatch",
    "expire_run",
    "hop_budget_for",
    "open_finding",
    "open_run",
    "re_dispatch",
    "submit",
    "transition_finding",
    # kernel.reroute：re_route 与猜测格弱存款（SPEC-2 §3）
    "RE_ROUTE_WEAK_W",
    "guess_taxonomy",
    "is_mismatch",
    "re_route_finding",
    # kernel.cluster：聚类归并（SPEC-2 §1）
    "CLUSTER_EMBED_SIM_MIN",
    "CLUSTER_OVERLAP_MIN",
    "S1_SOURCE_W",
    "S2_SOURCE_W",
    "S3_SOURCE_W",
    "ClusterProposal",
    "FindingFacts",
    "MemberFacts",
    "RepairReport",
    "SimFn",
    "VerdictContext",
    "VoteConsensus",
    "VoteFacts",
    "aggregate_votes",
    "apply_human_verdict",
    "apply_merges",
    "attribute_findings",
    "SchemaRejected",
    "close_panel",
    "cluster_findings",
    "coarse_mergeable",
    "fine_mergeable",
    "line_overlap",
    "propagate_pending",
    "propagate_verdict",
    "split_cluster",
    # kernel.replay：重放重建与重放等价比对（§2/§6-6；三投影 §6.3）
    "CLUSTER_DIFF_FIELDS",
    "CellDiff",
    "DEFAULT_DIFF_FIELDS",
    "FINDING_DIFF_FIELDS",
    "ClusterProj",
    "FindingProj",
    "ProjDiff",
    "cluster_projection",
    "diff_cells",
    "diff_clusters",
    "diff_findings",
    "finding_projection",
    "rebuild_cells",
    "rebuild_clusters",
    "rebuild_findings",
    "rebuild_summary",
    # kernel.route：自适应 TILT 路由（§5.2）
    "SPEC_CAP",
    "RouteDecision",
    "active_specialists",
    "adaptive_tilt",
    "route",
    # kernel.route_thompson：Thompson sampling 对照路由（SPEC-4 §3 附加实验）
    "TS_CHANNEL",
    "beta_posterior",
    "posterior_mean",
    "route_thompson",
    # kernel.promote_scan：专长孵化状态机（§7.3）
    "SLIDING_WINDOW_DAYS",
    "WILSON_Z",
    "CriteriaSnapshot",
    "Transition",
    "promote_scan",
    "wilson_bounds",
    "wilson_lb",
    "wilson_ub",
]
