"""simulator：M0 机制仿真器（SPEC-4 数学规格）。

仿真世界只生成事件流（任务/verdict/存款/考试），喂给真实 kernel 的 route /
promote_scan / agg_cells / 自适应 TILT；度量从内核输出侧采集（§0 关键工程决策：
复用内核实现，防双实现漂移）。纯 Python、无 LLM 依赖、无网络（§6 实现约束）。

当前已交付：
  - rng      ：主种子 → 独立命名子流（§6 可复现）；
  - world    ：合成 agent 池（§1.1，roster + q_f/q_t + 隐藏设定 10/10/5）；
  - taskgen  ：任务流生成器（§1.2，Poisson 到达 + v1_weight 家族混合 +
               Beta(2,3) 难度 + 50/35/15 模式 + RESCAN_WEEKLY 重扫）；
  - exam     ：入学考试播种（§1.5/§8.4，seeding 开关 + ±[0.2,0.5] 中心化映射
               + origin=exam 存款）与晋升暗考回调（§8.2/§7.6 转换②，3真2雷
               及格规则 + 不过退 shadow 冷却 + exam_result/promotion 事件）；
  - verdict  ：三层 verdict 生成（§1.3，判对概率 + S1 相关噪声投票 +
               S2 三分类/饥饿旋钮 + S3 延迟触达与截断 LogNormal lag）；
  - deposits ：存款归属（§1.4/设计 §6.5 全角色矩阵，经 kernel.ledger.append 落账）；
  - metrics  ：SPEC-4 §2 全部 9 项度量（内核输出侧 + WorldTruth 只读真值快照 +
               TaskTimeline 任务时钟 + oracle/regret 基线 + 多种子均值±std 聚合）；
  - engine   ：闭环主循环（§0——任务流喂 kernel.route → verdict/deposits 落账 →
               夜批 promote_scan（ExamCenter 暗考）→ 度量采集 + 可配时点重放等价；
               run(params, seed, n_days, overrides) 与实验矩阵扫描/对照臂注入口）；
  - experiments：SPEC-4 §3 实验矩阵 runner（一条命令跑全矩阵 + 三项附加实验；
               并行可限进程数；种子表显式固定、确定性载荷位级可复现；--smoke
               子集 / --full 全矩阵 / --plan 计划核查）；
  - report   ：SPEC-4 §4 输出物（sim_report.md：九项度量达成表 + 参数敏感性
               CSV + Thompson 对照结论 + params.yaml 定稿建议；§4-1 推荐参数组
               选择器——无达标组时输出最接近组与逐项差距，不静默选组）。
"""

from .deposits import (
    AUDITOR_X,
    DepositRecord,
    append_deposits,
    attribute_deposits,
    deposit_schedule,
    reporter_coefficient,
)
from .engine import (
    DayRecord,
    Router,
    RunResult,
    S2StarvationRun,
    SimClock,
    apply_overrides,
    run,
    run_s2_starvation,
)

#: experiments 惰性导出（PEP 562）：包 __init__ 不急切导入 experiments 子模块，
#: 避免 ``python -m qswarm.simulator.experiments`` 与包初始化双重导入的
#: RuntimeWarning；属性访问时才加载。
#: report 惰性导出（PEP 562，理由同 experiments）：CLI 以
#: ``python -m qswarm.simulator.report`` 运行时避免双重导入的 RuntimeWarning。
_REPORT_EXPORTS = frozenset({
    "KEY_METRICS", "SPEC4_TARGETS", "GroupAssessment", "MetricReading",
    "ReportResult", "Selection", "Target", "assess_group", "build_report",
    "judge", "load_experiment_outputs", "report_main", "relative_gap",
    "render_markdown", "select_group", "sensitivity_rows", "thompson_conclusion",
})

_EXPERIMENTS_EXPORTS = frozenset({
    "FULL_GRID", "FULL_SEEDS", "SMOKE_GRID", "SMOKE_SEEDS", "ExperimentUnit",
    "build_plan", "execute_plan", "experiments_main", "run_experiments",
})


def __getattr__(name: str):
    # 两个 CLI 入口在本模块公开名下带模块前缀（子模块内名 main）
    alias = {"experiments_main": ("experiments", "main"),
             "report_main": ("report", "main")}
    if name in _EXPERIMENTS_EXPORTS or name in _REPORT_EXPORTS:
        from importlib import import_module
        mod_name = "experiments" if name in _EXPERIMENTS_EXPORTS else "report"
        mod = import_module(f".{mod_name}", __name__)
        return getattr(mod, alias[name][1] if name in alias else name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
from .exam import (
    ExamCenter,
    ExamWorldParams,
    EntryExamOutcome,
    PromotionExamOutcome,
    centered_exam_weight,
    exam_item_pass_p,
    promotion_exam_passes,
    seed_entry_exams,
)
from .metrics import (
    CellEpisode,
    CheckpointEquivalence,
    ColdStartComparison,
    MeanStd,
    OracleStep,
    RegretPoint,
    ReplayEquivalenceReport,
    RoutingRecord,
    RunMetrics,
    S2StarvationReport,
    TaskTimeline,
    WorldTruth,
    aggregate_run_metrics,
    aggregate_seeds,
    cell_episodes,
    cold_start_length,
    cold_start_seeded_vs_unseeded,
    collect_run_metrics,
    degradation_response,
    expected_accuracy_series,
    expected_correctness,
    mean_std,
    median_time_to_active,
    oracle_route_stream,
    promotion_precision,
    promotion_recall,
    regret_curve,
    replay_equivalence,
    reporter_role_for_mode,
    routing_records_from_ledger,
    snapshot_world_truth,
    split_node,
    s2_starvation_propagation,
    uniform_accuracy_series,
    wrong_extinguish_rate,
)
from .rng import derive_seed, substream
from .taskgen import TaskGenerator, TaskMix, TaskStreamParams, WorldTask
from .verdict import (
    LAG_LN_MU,
    LAG_LN_SIGMA,
    LAG_MAX_DAYS,
    LAG_MIN_DAYS,
    S1_WEIGHT_MAX,
    S1_WEIGHT_MIN,
    S2_SOURCE_WEIGHT,
    S3_SOURCE_WEIGHT,
    FindingContext,
    S2Record,
    S3Record,
    VerdictParams,
    VerdictResult,
    VoteRecord,
    assign_finding_context,
    generate_finding_verdict,
    latent_correlation,
    reporter_correct_prob,
    run_findings_flow,
    theoretical_lag_mean,
    vote_agree_probability,
)
from .world import (
    AgentPool,
    Archetype,
    GroundTruth,
    SimAgent,
    WorldHiddenParams,
)

__all__ = [
    # rng
    "derive_seed", "substream",
    # engine（§0 闭环主循环）
    "DayRecord", "Router", "RunResult", "S2StarvationRun", "SimClock",
    "apply_overrides", "run", "run_s2_starvation",
    # experiments（§3 实验矩阵 runner）
    "FULL_GRID", "FULL_SEEDS", "SMOKE_GRID", "SMOKE_SEEDS", "ExperimentUnit",
    "build_plan", "execute_plan", "experiments_main", "run_experiments",
    # report（§4 输出物：达成表/敏感性 CSV/Thompson 结论/定稿建议）
    "KEY_METRICS", "SPEC4_TARGETS", "GroupAssessment", "MetricReading",
    "ReportResult", "Selection", "Target", "assess_group", "build_report",
    "judge", "load_experiment_outputs", "report_main", "relative_gap",
    "render_markdown", "select_group", "sensitivity_rows", "thompson_conclusion",
    # world（§1.1）
    "AgentPool", "Archetype", "GroundTruth", "SimAgent", "WorldHiddenParams",
    # taskgen（§1.2）
    "TaskGenerator", "TaskMix", "TaskStreamParams", "WorldTask",
    # exam（§1.5/§8.2/§8.4）
    "ExamCenter", "ExamWorldParams", "EntryExamOutcome", "PromotionExamOutcome",
    "centered_exam_weight", "exam_item_pass_p", "promotion_exam_passes",
    "seed_entry_exams",
    # verdict（§1.3）
    "FindingContext", "S2Record", "S3Record", "VerdictParams", "VerdictResult",
    "VoteRecord", "assign_finding_context", "generate_finding_verdict",
    "latent_correlation", "reporter_correct_prob", "run_findings_flow",
    "theoretical_lag_mean", "vote_agree_probability",
    "LAG_LN_MU", "LAG_LN_SIGMA", "LAG_MAX_DAYS", "LAG_MIN_DAYS",
    "S1_WEIGHT_MAX", "S1_WEIGHT_MIN", "S2_SOURCE_WEIGHT", "S3_SOURCE_WEIGHT",
    # deposits（§1.4/§6.5）
    "AUDITOR_X", "DepositRecord", "append_deposits", "attribute_deposits",
    "deposit_schedule", "reporter_coefficient",
    # metrics（§2 全部 9 项度量）
    "CellEpisode", "CheckpointEquivalence", "ColdStartComparison", "MeanStd",
    "OracleStep", "RegretPoint",
    "ReplayEquivalenceReport", "RoutingRecord", "RunMetrics", "S2StarvationReport",
    "TaskTimeline", "WorldTruth", "aggregate_run_metrics", "aggregate_seeds",
    "cell_episodes", "cold_start_length", "cold_start_seeded_vs_unseeded",
    "collect_run_metrics",
    "degradation_response", "expected_accuracy_series", "expected_correctness",
    "mean_std", "median_time_to_active", "oracle_route_stream",
    "promotion_precision", "promotion_recall", "regret_curve", "replay_equivalence",
    "reporter_role_for_mode", "routing_records_from_ledger", "snapshot_world_truth",
    "split_node",
    "s2_starvation_propagation", "uniform_accuracy_series", "wrong_extinguish_rate",
]
