"""contracts：数据契约层（SPEC-1 唯一真源：枚举 / DDL / pydantic / 配置 schema）。

写口纪律（SPEC-1 §6-2）：ledger/cells/findings.state/clusters.verdict_* 只允许
kernel 包写入；本包只定义契约，不做状态变更。
"""

from importlib.resources import files

from .config import (
    DEFAULT_PARAMS_PATH,
    DEFAULT_TAXONOMY_PATH,
    BudgetParams,
    DecayParams,
    DemoteParams,
    DepositsParams,
    ExamParams,
    ExploreParams,
    FamilySpec,
    PanelParams,
    ParamsConfig,
    PromoteParams,
    RosterParams,
    ShrinkParams,
    TaxonomyConfig,
    TiltParams,
    load_params,
    load_taxonomy,
)
from .enums import (
    AgentStatus,
    BlockingKind,
    CellStatus,
    DepositOrigin,
    ExamItemSource,
    ExamPurpose,
    Family,
    FindingState,
    Grade,
    L0Channel,
    LedgerKind,
    PoolClass,
    Role,
    RunMode,
    RunStatus,
    Severity,
    SignalTier,
    VerifyErrorKind,
    VerifyOutcome,
)
from .models import (
    PAYLOAD_SCHEMA,
    ArbitrationDraft,
    Assertion,
    AuditorFinding,
    BlockingEvidence,
    ClusterMergePayload,
    ClusterSplitPayload,
    ClusterSuggestion,
    DepositPayload,
    DemotionPayload,
    DispatchContract,
    DispatchPayload,
    ExamDepositPayload,
    ExamResultPayload,
    EvidenceNode,
    FindingPayload,
    HumanVerdictPayload,
    PromotionPayload,
    ReDispatchPayload,
    ReRoutePayload,
    ReviewerVote,
    RollbackPayload,
    ScalingPayload,
    ScoutReport,
    VerifierReport,
    VerifyResultPayload,
    VotePayload,
)

__all__ = [
    # 枚举
    "AgentStatus", "BlockingKind", "CellStatus", "DepositOrigin", "ExamItemSource",
    "ExamPurpose", "Family", "FindingState", "Grade", "L0Channel", "LedgerKind",
    "PoolClass", "Role", "RunMode", "RunStatus", "Severity", "SignalTier",
    "VerifyErrorKind", "VerifyOutcome",
    # 蜂 I/O 契约
    "ArbitrationDraft", "Assertion", "AuditorFinding", "BlockingEvidence",
    "ClusterSuggestion", "DispatchContract",
    "EvidenceNode", "ReviewerVote", "ScoutReport", "VerifierReport",
    # 账本载荷
    "ClusterMergePayload", "ClusterSplitPayload", "DepositPayload", "DemotionPayload",
    "DispatchPayload", "ExamDepositPayload", "ExamResultPayload", "FindingPayload",
    "HumanVerdictPayload", "PAYLOAD_SCHEMA", "PromotionPayload", "ReDispatchPayload",
    "ReRoutePayload", "RollbackPayload", "ScalingPayload", "VerifyResultPayload",
    "VotePayload",
    # 配置
    "DEFAULT_PARAMS_PATH", "DEFAULT_TAXONOMY_PATH", "BudgetParams", "DecayParams",
    "DemoteParams", "DepositsParams", "ExamParams", "ExploreParams", "FamilySpec",
    "PanelParams", "ParamsConfig", "PromoteParams", "RosterParams", "ShrinkParams",
    "TaxonomyConfig", "TiltParams", "load_params", "load_taxonomy",
]


def schema_sql() -> str:
    """返回内置 DDL（schema.sql）全文；建库方（kernel）按依赖序 executescript。"""
    return files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")
