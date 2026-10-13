"""pydantic 契约——蜂 I/O 与账本载荷（SPEC-1 §4）。

字段名/类型/语义逐条对照 §4.1–§4.3；§4.3 注释中列出的其余 kind 载荷最小字段
固化为 *Payload 模型，并由 PAYLOAD_SCHEMA 按 LedgerKind 注册（内核落账本前校验）。
SPEC 未覆盖处的默认值取舍见 docs/spec/DECISIONS.md。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import (
    BlockingKind,
    CellStatus,
    ExamPurpose,
    Family,
    FindingState,
    LedgerKind,
    Role,
    Severity,
    SignalTier,
    DepositOrigin,
    VerifyErrorKind,
    VerifyOutcome,
)


# ── 4.1 派单契约（内核→蜂，§4.3 四件套）─────────────────────────

class DispatchContract(BaseModel):
    task_id: str
    role: Role
    goal: str                                  # ① 目标（一句话）
    output_schema_ref: str                     # ② 输出 schema 名（§4.2 各蜂输出）
    tool_bounds: list[str]                     # ③ 工具/信息源边界，如 ['repo:ro','repomap:ro']
    task_bounds: str                           # ④ 做什么/不做什么（文本）
    context_refs: list[str] = []               # artifact:// 或 repo 路径 + 行窗
    taxonomy_node: str                         # 'family[/type]'
    hop_budget: int = 0                        # 深挖跳数（§2.2 档位参数）
    deadline_s: int


# ── 4.2 蜂输出契约（schema 不合格直接退回，§4.3）────────────────

class EvidenceNode(BaseModel):
    kind: str                                  # source|propagation|sink|sanitizer
    where_path: str
    where_line: int
    found: bool | None = None
    ref: str                                   # artifact://


class ScoutReport(BaseModel):                  # L1：候选点分诊
    candidate_id: str
    suspicious: bool
    guess_family: Family | None = None
    guess_type: str | None = None
    reason_ref: str                            # artifact://
    injection_flag: bool = False               # §11.6-3：代码含指令式可疑文本


class AuditorFinding(BaseModel):               # L2：含证据链（对应 findings+evidence_nodes）
    title: str
    severity: Severity
    cwe: str | None
    family: Family
    type: str | None
    evidence_chain: list[EvidenceNode]         # ≥3 节点，含 source/sink；sanitizer 必出现
    confidence: float = Field(ge=0, le=1)
    disagreement_note: str | None = None
    artifacts: list[str]


class ReviewerVote(BaseModel):                 # 四维（§10.3）
    dim_completeness: float = Field(ge=0, le=1)
    dim_reachability: float = Field(ge=0, le=1)
    dim_exploitability: float = Field(ge=0, le=1)
    dim_fp_suspicion: float = Field(ge=0, le=1)
    direction: int = Field(ge=-1, le=1)
    comment_ref: str


class Assertion(BaseModel):
    expect: str
    observed: bool
    log: str


class BlockingEvidence(BaseModel):
    kind: BlockingKind
    where: str
    ref: str


class VerifierReport(BaseModel):               # S2（§6.3/§10.4；对应 verify_results）
    cluster_id: str
    outcome: VerifyOutcome
    env_base: str
    deps_ref: str
    trigger_path: list[str]
    assertions: list[Assertion]
    blocking: BlockingEvidence | None = None   # verified_fp 时必填，否则内核改判
    error_kind: VerifyErrorKind | None = None  # verify_error 时必填
    attempts: int
    duration_s: float
    logs_ref: str


class PreVerdictSlip(BaseModel):
    """S2 预裁决单（D29；SPEC-8 §2.6）——verifier v0 对精聚类簇的纸面预判，
    不执行 PoC、不写 verify_results（该表是 S2 真验证专用面）、零存款
    （verifier 存款只由 S3 回校，§6.5 ④）。与 VerifierReport 的语义边界：
    inconclusive=纸面无法预判；verify_error=S2 执行失败（M5 面，互斥不混）。
    落工件 kind="pre_verdict"，供仲裁/人工与 M5 沙箱排程。"""

    cluster_id: str
    expected_verdict: Literal["confirmed", "refuted", "inconclusive"]
    poc_shape: str
    env_requirements: list[str]


# ── 4.3 账本载荷 schema（按 kind）───────────────────────────────

class DepositPayload(BaseModel):               # kind=deposit（§3.2/§6.5 归属矩阵）
    finding: str | None
    cluster: str | None
    agent: str
    role: Role
    taxonomy_path: list[str]                   # ['injection','sqli']
    signal_tier: SignalTier
    direction: int = Field(ge=-1, le=1)        # ±1
    weight: float                              # 源权重 × 角色系数（1.0/0.5/0.2/0.25）
    origin: DepositOrigin = DepositOrigin.PROD
    # M1 新增可选字段（SPEC-2 §5.1，DECISIONS 2026-10-10-D6）：派生存款的源事件
    # seq——propagate_verdict 幂等键 (cluster, tier, src_seq) 与 §6.3 崩溃窗口修复
    # （propagate_pending）的唯一判据。M0 历史事件无此字段（=None）；聚合器
    # （kernel.cells._fold）不读该字段，M0 轨迹与重放逐位不受影响。
    src_seq: int | None = None


class ExamDepositPayload(DepositPayload):      # §8.4：origin=exam，T_half 减半由聚合器处理
    exam_id: str


class DispatchPayload(BaseModel):              # kind=dispatch
    task_id: str
    role: Role
    node: str
    assignee: str | None = None
    hop_budget: int = 0


class FindingPayload(BaseModel):               # kind=finding
    finding_id: str
    reporter: str
    cluster: str | None = None
    state: FindingState = FindingState.OPEN


class VotePayload(BaseModel):                  # kind=vote
    finding_id: str
    reviewer: str
    direction: int = Field(ge=-1, le=1)


class VerifyResultPayload(BaseModel):          # kind=verify_result
    cluster_id: str
    outcome: VerifyOutcome
    error_kind: VerifyErrorKind | None = None


class HumanVerdictPayload(BaseModel):          # kind=human_verdict
    cluster_id: str
    direction: int = Field(ge=-1, le=1)
    actor: str
    source: str = "panel"


class ExamResultPayload(BaseModel):            # kind=exam_result
    exam_id: str
    purpose: ExamPurpose
    agent: str
    node: str
    correct: bool
    score: float | None = None


class PromotionPayload(BaseModel):             # kind=promotion
    agent: str
    role: Role
    node: str
    exam_id: str
    from_status: CellStatus
    to_status: CellStatus


class DemotionPayload(BaseModel):              # kind=demotion
    agent: str
    role: Role
    node: str
    reason: Literal["decay", "dormant", "eliminate"]


class ScalingPayload(BaseModel):               # kind=scaling
    pool: str                                  # Role × PoolClass 组合标识（SPEC-2 收紧）
    delta: int
    reason: str


class RollbackPayload(BaseModel):              # kind=rollback
    from_snap: str
    to_snap: str
    reason: str


class ClusterMergePayload(BaseModel):          # kind=cluster_merge
    cluster_id: str
    members: list[str]
    level: Literal["coarse", "fine"]


class ClusterSplitPayload(BaseModel):          # kind=cluster_split
    cluster_id: str
    new_clusters: list[str]


class ReRoutePayload(BaseModel):               # kind=re_route
    finding_id: str
    from_node: str
    to_node: str
    reason: str


class ReDispatchPayload(BaseModel):            # kind=re_dispatch
    task_id: str
    from_agent: str
    to_agent: str
    reason: Literal["timeout", "schema_fail"]


# ── 蜂后顾问契约面（SPEC-3 §6；M2 只定契约不实装，生产消费随 M3 蜂后顾问接线）──


class ClusterSuggestion(BaseModel):
    """簇处置建议（§16.7-9 建议工件的最小单元；action 词表 merge|split|escalate）。"""

    cluster_id: str
    action: str                                  # "merge"|"split"|"escalate"
    rationale: str


class ArbitrationDraft(BaseModel):
    """蜂后顾问产出（§4.2/§16.7-9：建议工件，内核代落账）。

    M2 只定契约不实装（生产流量 M3 起，SPEC-3 §6）；顾问永不进入派单状态机
    （ROLE_OUTPUT_SCHEMA 仅登记 schema 名防 M3 契约漂移）。summary_ref 为
    artifact://（摘要全文工件）。"""

    run_id: str
    cluster_ids: list[str]
    suggestions: list[ClusterSuggestion]
    summary_ref: str


#: LedgerKind → 载荷模型注册表（kind=deposit 的考试播种用 ExamDepositPayload 子类）。
PAYLOAD_SCHEMA: dict[LedgerKind, type[BaseModel]] = {
    LedgerKind.DISPATCH: DispatchPayload,
    LedgerKind.FINDING: FindingPayload,
    LedgerKind.VOTE: VotePayload,
    LedgerKind.VERIFY_RESULT: VerifyResultPayload,
    LedgerKind.HUMAN_VERDICT: HumanVerdictPayload,
    LedgerKind.EXAM_RESULT: ExamResultPayload,
    LedgerKind.PROMOTION: PromotionPayload,
    LedgerKind.DEMOTION: DemotionPayload,
    LedgerKind.SCALING: ScalingPayload,
    LedgerKind.ROLLBACK: RollbackPayload,
    LedgerKind.CLUSTER_MERGE: ClusterMergePayload,
    LedgerKind.CLUSTER_SPLIT: ClusterSplitPayload,
    LedgerKind.RE_ROUTE: ReRoutePayload,
    LedgerKind.RE_DISPATCH: ReDispatchPayload,
    LedgerKind.DEPOSIT: DepositPayload,
}

__all__ = [
    "Assertion",
    "ArbitrationDraft",
    "AuditorFinding",
    "BlockingEvidence",
    "ClusterMergePayload",
    "ClusterSplitPayload",
    "ClusterSuggestion",
    "DepositPayload",
    "DemotionPayload",
    "DispatchContract",
    "DispatchPayload",
    "ExamDepositPayload",
    "ExamResultPayload",
    "EvidenceNode",
    "FindingPayload",
    "HumanVerdictPayload",
    "PAYLOAD_SCHEMA",
    "PromotionPayload",
    "ReDispatchPayload",
    "ReRoutePayload",
    "ReviewerVote",
    "RollbackPayload",
    "ScalingPayload",
    "ScoutReport",
    "VerifierReport",
    "PreVerdictSlip",
    "VerifyResultPayload",
    "VotePayload",
]
