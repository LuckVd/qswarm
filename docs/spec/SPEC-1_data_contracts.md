# SPEC-1 数据契约（实现级，无歧义版）

> 来源：设计文档 v0.3.4 §3/§4.5/§6/§8/§16.7。本文件是唯一真源：DDL、枚举、pydantic、配置 schema。
> 读者：AI 编码代理。实现时字段名/类型/语义不得偏离；发现矛盾以本文件为准并记 DECISIONS.md。
> 存储：SQLite（WAL）+ 内容寻址文件库。所有时间戳 = UTC ISO-8601 字符串，由内核时钟统一签发。

---

## 1. 枚举（`contracts/enums.py`，单一真源）

```python
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
```

---

## 2. SQLite DDL（`ledger/schema.sql`，按依赖序）

```sql
-- Pragmas（连接时设置）
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── 运行与任务 ──────────────────────────────────────────────
CREATE TABLE runs (
  run_id        TEXT PRIMARY KEY,            -- 'R-<date>-<seq>'
  mode          TEXT NOT NULL,               -- RunMode
  repo_url      TEXT NOT NULL,
  commit_sha    TEXT,
  diff_ref      TEXT,                        -- pr 档: merge-base..HEAD
  budget_cents  INTEGER NOT NULL,            -- 预算熔断阈值（§2.4），美分
  spent_cents   INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL,               -- RunStatus
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  meta_json     TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE tasks (
  task_id       TEXT PRIMARY KEY,            -- 'T-<run>-<seq>'
  run_id        TEXT NOT NULL REFERENCES runs(run_id),
  role          TEXT NOT NULL,               -- Role
  node          TEXT NOT NULL,               -- 'family' 或 'family/type'
  shard_json    TEXT NOT NULL,               -- 分片规格（路径列表/候选点列表/簇 id）
  state         TEXT NOT NULL DEFAULT 'queued',  -- queued|dispatched|done|failed
  assignee      TEXT,                        -- agent_id
  attempts      INTEGER NOT NULL DEFAULT 0,  -- §16.7-8 重试上限 1
  dispatched_at TEXT,
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_tasks_queue ON tasks(state, role, node);

-- ── L0 候选点（三通道产物）─────────────────────────────────
CREATE TABLE candidates (
  candidate_id  TEXT PRIMARY KEY,            -- 'CD-<run>-<seq>'
  run_id        TEXT NOT NULL REFERENCES runs(run_id),
  path          TEXT NOT NULL,
  line_start    INTEGER NOT NULL,
  line_end      INTEGER NOT NULL,
  channels      TEXT NOT NULL,               -- L0Channel 逗号串，如 'struct,rule'
  channel_score REAL NOT NULL DEFAULT 0,     -- 多通道命中加权（§2.1）
  rule_id       TEXT,                        -- 通道 C 命中的规则 id（可空）
  guess_family  TEXT,                        -- 侦察前 L0 初始猜测（可空）
  guess_type    TEXT,
  triaged       INTEGER NOT NULL DEFAULT 0,  -- 侦察蜂处理后置 1
  created_at    TEXT NOT NULL
);
CREATE INDEX idx_candidates_run ON candidates(run_id, triaged);

-- ── Finding 与证据链（§3.1）────────────────────────────────
CREATE TABLE findings (
  id            TEXT PRIMARY KEY,            -- 'F-<date>-<seq>'
  run_id        TEXT NOT NULL REFERENCES runs(run_id),
  reporter      TEXT NOT NULL,               -- agent_id
  reporter_role TEXT NOT NULL,               -- Role（fast 档=scout，§16.7-1）
  family        TEXT NOT NULL,               -- Family
  type          TEXT,                        -- 家族内细分（可空=家族级）
  lang          TEXT NOT NULL,
  repo          TEXT NOT NULL,
  path          TEXT NOT NULL,
  func          TEXT,
  line_start    INTEGER NOT NULL,
  line_end      INTEGER NOT NULL,
  commit_sha    TEXT NOT NULL,
  cluster_id    TEXT REFERENCES clusters(cluster_id),
  title         TEXT NOT NULL,
  severity      TEXT NOT NULL,               -- Severity
  cwe           TEXT,                        -- 'CWE-89'
  confidence    REAL NOT NULL DEFAULT 0.5,   -- [0,1]，S1 共识写入（§10.3）
  grade         TEXT NOT NULL,               -- Grade
  state         TEXT NOT NULL DEFAULT 'open',-- FindingState
  disagreement_json TEXT,                    -- 分歧记录（§10.3），可空
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX idx_findings_state ON findings(run_id, state);

CREATE TABLE evidence_nodes (                -- 证据链四类节点，必填（§3.1）
  id            TEXT PRIMARY KEY,            -- finding 内自增序即可 'EV-<finding>-<n>'
  finding_id    TEXT NOT NULL REFERENCES findings(id),
  ord           INTEGER NOT NULL,            -- 链内顺序
  kind          TEXT NOT NULL,               -- source|propagation|sink|sanitizer
  where_path    TEXT NOT NULL,
  where_line    INTEGER NOT NULL,
  found         BOOLEAN,                     -- 仅 sanitizer 用：false=无消毒
  ref           TEXT NOT NULL                -- 'artifact://<sha256>'
);
CREATE INDEX idx_evidence_finding ON evidence_nodes(finding_id, ord);

-- ── 聚类归并（§3.4 两级）───────────────────────────────────
CREATE TABLE clusters (
  cluster_id    TEXT PRIMARY KEY,            -- 'C-<date>-<seq>'
  key_path      TEXT NOT NULL,
  key_cwe       TEXT,
  key_lang      TEXT NOT NULL,
  line_window   TEXT NOT NULL,               -- 归并时的行窗描述
  embedding_sim REAL,                        -- 精聚类时的相似度（可空）
  level         TEXT NOT NULL,               -- 'coarse'|'fine'
  verdict_tier  TEXT,                        -- SignalTier（结论来源，可空）
  verdict_dir   INTEGER,                     -- +1|-1（可空）
  verdict_at    TEXT,
  created_at    TEXT NOT NULL
);
CREATE TABLE cluster_members (
  cluster_id    TEXT NOT NULL REFERENCES clusters(cluster_id),
  finding_id    TEXT NOT NULL REFERENCES findings(id),
  merged_at     TEXT NOT NULL,
  PRIMARY KEY (cluster_id, finding_id)
);

-- ── 评审投票（§10.3 四维）──────────────────────────────────
CREATE TABLE votes (
  vote_id       TEXT PRIMARY KEY,            -- 'V-<finding>-<seq>'
  finding_id    TEXT NOT NULL REFERENCES findings(id),
  reviewer      TEXT NOT NULL,               -- agent_id（≠报告者，纪律2）
  dim_completeness  REAL NOT NULL,           -- [0,1] 证据链完整性
  dim_reachability  REAL NOT NULL,           -- [0,1] 可达性
  dim_exploitability REAL NOT NULL,          -- [0,1] 可利用性
  dim_fp_suspicion  REAL NOT NULL,           -- [0,1] 误报嫌疑（高=像误报）
  direction     INTEGER NOT NULL,            -- +1|-1 共识方向
  comment_ref   TEXT,                        -- artifact:// 意见全文
  created_at    TEXT NOT NULL,
  UNIQUE (finding_id, reviewer)
);

-- ── S2 验证结果（§6.3 + §10.4 blocking_evidence）──────────
CREATE TABLE verify_results (
  id            TEXT PRIMARY KEY,            -- 'VR-<cluster>-<attempt>'
  cluster_id    TEXT NOT NULL REFERENCES clusters(cluster_id),
  outcome       TEXT NOT NULL,               -- VerifyOutcome
  env_base      TEXT NOT NULL,               -- 如 'python:3.11'
  deps_ref      TEXT NOT NULL,               -- artifact://lockfile
  trigger_path_json TEXT NOT NULL,           -- ['entry: views.py:88', ...]
  assertions_json   TEXT NOT NULL,           -- [{expect, observed, log_ref}]
  blocking_kind TEXT,                        -- outcome=verified_fp 时必填（内核强制）
  blocking_where TEXT,
  blocking_ref  TEXT,
  error_kind    TEXT,                        -- outcome=verify_error 时必填
  attempts      INTEGER NOT NULL,
  duration_s    REAL NOT NULL,
  logs_ref      TEXT NOT NULL,               -- artifact://
  created_at    TEXT NOT NULL
);
-- 内核校验规则：verified_fp 缺 blocking_* → 强制改判 verify_error（§10.4），不落库。

-- ── 账本（唯一真源，append-only，仅内核写 §4.5）───────────
CREATE TABLE ledger (
  seq       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts        TEXT NOT NULL,
  kind      TEXT NOT NULL,                   -- LedgerKind
  run_id    TEXT,                            -- 可空（考试/孵化事件无 run）
  payload_json TEXT NOT NULL                 -- 见 §4.3 载荷 schema
);
-- 数据库级强制 append-only（防内核 bug）：
CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;
CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger
BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;

-- ── 口碑格子（物化视图，可随时 DROP 重建 §3.3/§4.5）────────
CREATE TABLE cells (
  agent_id    TEXT NOT NULL,
  role        TEXT NOT NULL,
  node        TEXT NOT NULL,                 -- 'family' 或 'family/type'
  pos_w       REAL NOT NULL DEFAULT 0,       -- 挥发加权正存款
  neg_w       REAL NOT NULL DEFAULT 0,
  n_eff       REAL NOT NULL DEFAULT 0,       -- ≡ 2×(pos_w+neg_w)（§6.1）
  n_eff_s2s3  REAL NOT NULL DEFAULT 0,       -- 晋升硬门槛（§7.3）
  status      TEXT NOT NULL DEFAULT 'shadow',-- CellStatus
  activated_at TEXT,
  last_event_seq INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (agent_id, role, node)
);
CREATE INDEX idx_cells_node ON cells(node, status);   -- 路由读 active 格子

-- ── 口碑快照（§11 闸2 硬护栏）─────────────────────────────
CREATE TABLE snapshots (
  snap_id       TEXT PRIMARY KEY,            -- 'SNAP-<date>-<seq>'
  created_at    TEXT NOT NULL,
  cells_ref     TEXT NOT NULL,               -- artifact:// 全量 cells JSON
  reason        TEXT NOT NULL,
  parent_snap_id TEXT REFERENCES snapshots(snap_id)
);

-- ── 编制（agents + 帽子）───────────────────────────────────
CREATE TABLE agents (
  agent_id        TEXT PRIMARY KEY,          -- ≡ '<model_ref>@<contract_version>'（§16.7-2）
  model_ref       TEXT NOT NULL,             -- models.yaml 中的档位条目 id
  contract_version TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'provisioning',  -- AgentStatus
  created_at      TEXT NOT NULL,
  last_active_at  TEXT
);
CREATE TABLE hats (                          -- 角色是帽子（§4.1）
  agent_id  TEXT NOT NULL REFERENCES agents(agent_id),
  role      TEXT NOT NULL,
  pool_class TEXT NOT NULL,                  -- PoolClass
  PRIMARY KEY (agent_id, role)
);

-- ── 题库与考试（§8.2/§8.4）────────────────────────────────
CREATE TABLE exam_items (
  item_id     TEXT PRIMARY KEY,              -- 'SEED-<seq>'
  taxonomy_json TEXT NOT NULL,               -- ["injection","sqli"]
  lang        TEXT NOT NULL,
  repo_snapshot_ref TEXT NOT NULL,           -- artifact://seeds/...
  planted     BOOLEAN NOT NULL,
  expected    TEXT NOT NULL,                 -- 'real'|'fp'
  difficulty  TEXT NOT NULL,                 -- easy|medium|hard
  version     INTEGER NOT NULL DEFAULT 1,
  canary      TEXT NOT NULL,                 -- 泄漏溯源标记串
  source      TEXT NOT NULL,                 -- ExamItemSource
  created_at  TEXT NOT NULL,
  retired_at  TEXT
);
CREATE TABLE exam_results (
  id          TEXT PRIMARY KEY,              -- 'ER-<seq>'
  purpose     TEXT NOT NULL,                 -- ExamPurpose
  agent_id    TEXT NOT NULL,
  role        TEXT NOT NULL,
  node        TEXT NOT NULL,
  item_id     TEXT NOT NULL REFERENCES exam_items(item_id),
  correct     BOOLEAN NOT NULL,
  score       REAL,                          -- 置信分（可空）
  model_ref   TEXT NOT NULL,
  ts          TEXT NOT NULL
);
CREATE INDEX idx_exam_agent ON exam_results(agent_id, purpose);

-- ── 工件索引（内容寻址库的目录）───────────────────────────
CREATE TABLE artifacts (
  hash      TEXT PRIMARY KEY,                -- sha256 hex
  size      INTEGER NOT NULL,
  mime      TEXT NOT NULL,
  kind      TEXT NOT NULL,                   -- evidence|report|poc_log|seed|snapshot|misc
  producer  TEXT,                            -- agent_id 或 'kernel'
  created_at TEXT NOT NULL
);

-- ── 组织健康指标时序（§11.5，可重算，缓存性质）───────────
CREATE TABLE org_metrics (
  ts    TEXT NOT NULL,
  metric TEXT NOT NULL,                      -- 如 'verify_error_rate','s3_overturn_rate'
  value REAL NOT NULL,
  PRIMARY KEY (ts, metric)
);
```

**重放重建规则**：`cells` / `clusters.verdict_*` / `findings.state` 均可由 `ledger` 重算。重建工具 `kernel replay --from 0` 全量重建，是崩溃恢复与快照回滚（§11 闸2）的实现基础。

**事务边界**（§4.5 纪律1）：`findings` 落库与其首条 `deposit` 同事务；`clusters` 合并与 `cluster_merge` 事件同事务；任何状态机流转与对应账本事件同事务。**不存在无账本事件的状态变更。**

---

## 3. 内容寻址工件库（文件系统，非表）

- 路径：`data/artifacts/<hash[0:2]>/<hash>`；`hash = sha256(content)`。
- 写入幂等：同内容同 hash，重复写无害（§4.5）。
- 引用一律写 `artifact://<hash>`；`artifacts` 表只是索引（大小/mime/生产者），非必需不阻止读取。
- 只进不出（保留策略后置：被任何 finding/cluster/exam 引用的不可清）。

---

## 4. pydantic 契约（`contracts/models.py`，蜂 I/O 与账本载荷）

### 4.1 派单契约（内核→蜂，§4.3 四件套）

```python
from pydantic import BaseModel, Field
from datetime import datetime

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
```

### 4.2 蜂输出契约（schema 不合格直接退回，§4.3）

```python
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

class EvidenceNode(BaseModel):
    kind: str                                  # source|propagation|sink|sanitizer
    where_path: str; where_line: int
    found: bool | None = None
    ref: str                                   # artifact://

class ReviewerVote(BaseModel):                 # 四维（§10.3）
    dim_completeness: float = Field(ge=0, le=1)
    dim_reachability: float = Field(ge=0, le=1)
    dim_exploitability: float = Field(ge=0, le=1)
    dim_fp_suspicion: float = Field(ge=0, le=1)
    direction: int = Field(ge=-1, le=1)
    comment_ref: str

class VerifierReport(BaseModel):               # S2（§6.3/§10.4；对应 verify_results）
    cluster_id: str
    outcome: VerifyOutcome
    env_base: str; deps_ref: str
    trigger_path: list[str]
    assertions: list[Assertion]
    blocking: BlockingEvidence | None = None   # verified_fp 时必填，否则内核改判
    error_kind: VerifyErrorKind | None = None  # verify_error 时必填
    attempts: int; duration_s: float; logs_ref: str

class Assertion(BaseModel):
    expect: str; observed: bool; log: str

class BlockingEvidence(BaseModel):
    kind: BlockingKind; where: str; ref: str
```

### 4.3 账本载荷 schema（按 kind）

```python
class DepositPayload(BaseModel):               # kind=deposit（§3.2/§6.5 归属矩阵）
    finding: str | None; cluster: str | None
    agent: str; role: Role
    taxonomy_path: list[str]                   # ['injection','sqli']
    signal_tier: SignalTier
    direction: int                             # ±1
    weight: float                              # 源权重 × 角色系数（1.0/0.5/0.2/0.25）
    origin: DepositOrigin = DepositOrigin.PROD

class ExamDepositPayload(DepositPayload):      # §8.4：origin=exam，T_half 减半由聚合器处理
    exam_id: str

# 其余 kind 的 payload 最小字段（内核事件，自描述）：
# dispatch      {task_id, role, node, assignee, hop_budget}
# finding       {finding_id, reporter, cluster?, state}
# vote          {finding_id, reviewer, direction}
# verify_result {cluster_id, outcome, error_kind?}
# human_verdict {cluster_id, direction, actor, source='panel'}
# exam_result   {exam_id, purpose, agent, node, correct, score}
# promotion     {agent, role, node, exam_id, from_status, to_status}
# demotion      {agent, role, node, reason='decay'|'dormant'|'eliminate'}
# scaling       {pool, delta, reason}
# rollback      {from_snap, to_snap, reason}
# cluster_merge {cluster_id, members, level}
# cluster_split {cluster_id, new_clusters}
# re_route      {finding_id, from_node, to_node, reason}
# re_dispatch   {task_id, from_agent, to_agent, reason='timeout'|'schema_fail'}
```

---

## 5. 配置文件

### 5.1 `models.yaml`（BYO 档位映射，§1 P3）

```yaml
tiers:
  cheap:                                       # L1 侦察
    primary: {provider: deepseek, model: deepseek-chat, api_key_env: DEEPSEEK_API_KEY}
    fallbacks:
      - {provider: openai, model: gpt-4.1-nano, api_key_env: OPENAI_API_KEY}
  strong:                                      # L2 审查（不降档红线：档内可换供应商）
    primary: {provider: anthropic, model: claude-sonnet-4-5, api_key_env: ANTHROPIC_API_KEY}
    fallbacks:
      - {provider: openai, model: gpt-5.2, api_key_env: OPENAI_API_KEY}
  mid:                                         # 评审投票 / 蜂后顾问
    primary: {provider: anthropic, model: claude-haiku-4-5, api_key_env: ANTHROPIC_API_KEY}
  engineering:                                 # 验证蜂（PoC 工程）
    primary: {provider: openai, model: gpt-5-mini, api_key_env: OPENAI_API_KEY}

entry_exams:                                   # 入职考门禁 = 供给质检（§1 P3）
  strong:  {min_pass: 0.60, families: all}     # 映射到强档的模型考不过 → 拒绝入编
  mid:     {min_pass: 0.55, families: all}
  cheap:   {min_pass: 0.45, families: all}

runtime:
  rpm_limit: 600; tpm_limit: 4_000_000         # 全局并发闸（§9.3）
  batch_mode_for_full: true                    # §2.4
```

> **【M2 实现注记，2026-10-11】** 本节 yaml 为形态注释稿；pydantic 契约真源 =
> `qswarm/supply/config.py`（ModelsConfig 全家，extra=forbid 纪律同 params）。
> 实现期增补：① `runtime.production_temp: float = 0.2`（生产调用温度上限；
> §16.7-8 考试 temp=0 落 SPEC-3 常量 EXAM_TEMPERATURE，不进配置——DECISIONS
> 2026-10-11 models.yaml 契约面扩键行）；② provider = 供应商名（§5.2 操作性
> 口径，base URL 由 supplier 映射表定稿），非线格式族字面。参考配置落位
> `configs/models.yaml`。

### 5.2 `params.yaml`（机制参数；初值=设计默认，仿真器 M0 定稿后回填）

```yaml
promote:  {N: 10, N_S2S3: 3, DELTA: 0.10, M: 30, M_window_days: 90}
tilt:     {T_BASE: 0.70, GAMMA: 1.0, T_MIN: 0.50, T_MAX: 0.90}
shrink:   {LAMBDA: 5}                          # = 2.5 个 S3 当量（§5.1）
decay:    {T_HALF_DAYS: 30, T_HALF_DAYS_EXAM: 15, EPS_FLOOR: 0.5}
demote:   {WINDOWS: 2, DORMANT_DAYS: 60, REACTIVATE_N: 5}
exam:     {PROMO_ITEMS: 5, PROMO_REAL: 3, PROMO_MIN_REJECT_FP: 1,
           PERIODIC_ITEMS: 10, PERIODIC_PASS: 0.75, COOLDOWN_DAYS: 14}
explore:  {UNIFORM_FLOOR: 0.20}                # 探索通道 20% 均匀保底（§5.2）
budget:   {PR_CENTS: 100, FULL_CENTS: 600, L2_QUOTA_CAP: 45, RESCAN_WEEKLY: 0.07}
deposits: {SCOUT_X: 0.2, REVIEWER_X: 0.5, VERIFIER_CONFLICT: 0.5, VERIFIER_CONFIRM: 0.25}
panel:    {SIZE: 3, MIN_FAMILIES: 2}           # 评审团（§16.7-6）
roster:   {scout: 6, auditor: 5, reviewer: 4, verifier: 3}
```

### 5.3 `taxonomy.yaml`（家族树，§7.2）

```yaml
families:
  injection: {types: [sqli, cmdi, xss, ssrf, xxe]}
  deser:     {types: [java_gadget, py_pickle]}
  crypto:    {types: [weak_algo, hardcoded_key, weak_random]}
  authz:     {types: [idor, session_fixation, logic_bypass]}
  config_dep:{types: [secret_leak, outdated_dep, misconfig]}
  mem_safety:{types: [overflow, uaf], v1_weight: 0.3}   # §16.2.5 降权
```

---

## 6. 一致性规则（编码 AI 必须遵守）

1. **命名**：id 前缀 R-/T-/CD-/F-/EV-/C-/V-/VR-/ER-/SNAP-/SEED-，日期段 `YYYYMMDD`；
2. **写口**：`ledger/cells/findings.state/clusters.verdict_*` 只允许 `kernel/` 包写入；蜂模块 import 即静态检查（CI 断言）；
3. **温度**：考试调用 temp=0，生产 temp≤0.2（§16.7-8）；
4. **时钟**：一切 ts 由内核签发，蜂无时间源；
5. **密钥**：api_key_env 只引用环境变量名，任何表/工件/账本不得出现密钥值（§11.6-5）；
6. **重放等价**：任意时点 `kernel replay` 重建的 cells 与线上一致（M0 的回归测试项）。
