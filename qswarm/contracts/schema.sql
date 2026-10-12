-- SPEC-1 §2 SQLite DDL（逐字对照 docs/spec/SPEC-1_data_contracts.md）。
-- 落位说明：SPEC 正文写 ledger/schema.sql，M0 实现随 contracts 包分发（见 DECISIONS.md）。

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
  kind      TEXT NOT NULL,                   -- evidence|report|poc_log|seed|snapshot|exam_log|misc
                                             --   exam_log（M2，SPEC-6 §3.4/S9）：考试原始响应缓存工件
                                             --   （判分独立复核与 runner --replay 位级复现的真源载体）
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
