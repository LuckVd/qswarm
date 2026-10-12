"""枚举单一真源——逐字对照 docs/spec/SPEC-1_data_contracts.md §1。

成员名与字符串值不得增改；tests/test_enums.py 从 SPEC-1 §1 源码块 ast 解析逐成员断言。
"""

from enum import StrEnum

class RunMode(StrEnum):        # §2.2 三档
    FAST = "fast"; PR = "pr"; FULL = "full"

class RunStatus(StrEnum):
    RUNNING = "running"; DONE = "done"; DEGRADED = "degraded"; FAILED = "failed"

class Role(StrEnum):           # §4.1 五型（queen 不产 finding，见 §4.2 拆分）
    SCOUT = "scout"; AUDITOR = "auditor"; REVIEWER = "reviewer"
    VERIFIER = "verifier"; QUEEN_ADVISOR = "queen_advisor"

class PoolClass(StrEnum):      # §9.2 编制分类
    TEMP = "temp"; FORMAL = "formal"

class AgentStatus(StrEnum):
    PROVISIONING = "provisioning"; ACTIVE = "active"
    DRAINING = "draining"; DORMANT = "dormant"; RETIRED = "retired"

class Family(StrEnum):         # §7.2 种子版六家族；taxonomy.yaml 可扩展
    INJECTION = "injection"; DESER = "deser"; CRYPTO = "crypto"
    AUTHZ = "authz"; CONFIG_DEP = "config_dep"; MEM_SAFETY = "mem_safety"

class Severity(StrEnum):
    CRITICAL = "critical"; HIGH = "high"; MEDIUM = "medium"; LOW = "low"

class Grade(StrEnum):          # §10.1 三级结论
    L1 = "L1"; L2 = "L2"; L3 = "L3"

class FindingState(StrEnum):   # §10.2 状态机
    OPEN = "open"; VOTED = "voted"; VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"; CONFIRMED = "confirmed"
    REJECTED = "rejected"; EXPIRED = "expired"

class SignalTier(StrEnum):     # §6.1
    S1 = "S1"; S2 = "S2"; S3 = "S3"

class CellStatus(StrEnum):     # §7.3
    SHADOW = "shadow"; CANDIDATE = "candidate"; ACTIVE = "active"; DORMANT = "dormant"

class LedgerKind(StrEnum):     # §3.2 全量事件种类
    DISPATCH = "dispatch"; FINDING = "finding"; VOTE = "vote"
    VERIFY_RESULT = "verify_result"; HUMAN_VERDICT = "human_verdict"
    EXAM_RESULT = "exam_result"; PROMOTION = "promotion"; DEMOTION = "demotion"
    SCALING = "scaling"; ROLLBACK = "rollback"
    CLUSTER_MERGE = "cluster_merge"; CLUSTER_SPLIT = "cluster_split"
    RE_ROUTE = "re_route"; RE_DISPATCH = "re_dispatch"; DEPOSIT = "deposit"

class DepositOrigin(StrEnum):  # §8.4 考试播种 vs 生产
    PROD = "prod"; EXAM = "exam"

class VerifyOutcome(StrEnum):  # §6.3 S2 三分类
    VERIFIED_REAL = "verified_real"; VERIFIED_FP = "verified_fp"; VERIFY_ERROR = "verify_error"

class VerifyErrorKind(StrEnum):
    ENV_BUILD_FAIL = "env_build_fail"; POC_FAIL = "poc_fail"
    TIMEOUT = "timeout"; FLAKY = "flaky"

class BlockingKind(StrEnum):   # §10.4 verified_fp 证据标准
    SANITIZER = "sanitizer"; UNREACHABLE = "unreachable"; TYPE_SAFETY = "type_safety"
    AUTH_BOUNDARY = "auth_boundary"; STATIC_SAFE_INPUT = "static_safe_input"

class ExamPurpose(StrEnum):    # §8.2/§8.4
    ENTRY = "entry"; PROMOTION = "promotion"; PERIODIC = "periodic"

class ExamItemSource(StrEnum):
    CVE_ROLLBACK = "cve_rollback"; PATCHEVAL_MUTATED = "patcheval_mutated"
    JULIET_MUTATION = "juliet_mutation"; OWASP_MUTATION = "owasp_mutation"

class L0Channel(StrEnum):      # §2.1 三通道
    STRUCT = "struct"; MODEL = "model"; RULE = "rule"
