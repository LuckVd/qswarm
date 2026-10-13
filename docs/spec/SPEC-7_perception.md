# SPEC-7 感知层 L0 v2 + L1 侦察路由 v1（M3 实现级规格）

> 来源：设计文档 v0.3.4 §2.1（L0 三通道/repo map 边界）、§2.2（档位）、§4.1（五型分工）、§5（路由与调度）、§6.5（存款归属）、§16.6（依赖序第 5/6 位）、§16.7-1/2/4/6；SPEC-2（内核复用面）、SPEC-3（scout 提示词）、SPEC-6（supply/exams 复用面）；DECISIONS.md 2026-10-12 全部既有决策（智谱四蜂、思考型模型运行点、v0.3 题库）。
> 读者：AI 编码代理。函数签名 / 字段名 / 语义不得偏离本文件；发现矛盾以本文件为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 硬纪律：**只复用不重写**——M0/M1/M2 已落地的 `kernel.*`（ledger/cells/route/route_thompson/promote_scan/dispatch/budget/cluster/replay）、`contracts`、`prompts`（render/parse）、`supply`（adapter/config）、`exams`（全模块）一律原样复用；本 SPEC 只定义 M3 新增面与必要的最小增量修订。
> 本文件只写规格；实现落位与 `params.yaml` 回填由 M3 实现子任务按本文件执行。
> 状态：**定稿 v1.0（2026-10-12，负责人拍板 D25–D30 全部六项）**——D26/D28/D30 按负责人裁决偏离建议稿，D25 按建议稿（探测式分级）；逐条见 §9。已同步 `DECISIONS.md` 五字段。

---

## 0. 范围、模块落位与来源映射

### 0.1 M3 四块 ↔ 设计章节 ↔ 本文件章节（验收对映表）

| 机制块 | 设计文档出处 | 本文件 |
|---|---|---|
| 感知层 L0 候选收集（三通道，语言无关，规则不卡点） | §2.1、§2.2、§16.6-5 | §1 |
| L1 侦察 v1（分片、任务化、存款、路由接线） | §2.1、§5.1–5.3、§6.5、§16.7-1 | §2 |
| promote_scan 生产接线（影子运行）+ 生产账本续接 | §7.3、§7.6、SPEC-2 §2/§5 | §3 |
| reviewer/verifier 补播（注册、考试、判分扩展、生产面 v0） | §4.1、§8.2、§16.7-6、SPEC-3 §reviewer/verifier | §4 |

### 0.2 M3 新增模块落位

| 模块 | 状态 | 内容 |
|---|---|---|
| `qswarm/perception/`（新包） | **新增** | L0 三通道候选收集：`channels.py`（通道 A/B/C）、`indexer.py`（tree-sitter 调用点索引 + 内容寻址缓存）、`candidates.py`（候选归并/去重/优先级）、`scan.py`（扫描入口：repo 档/diff 档）、`clues.py`（家族→结构线索表，纯数据） |
| `kernel/loop.py` | **新增** | 生产编排循环：L0→L1→（M4 起 L2）流水线驱动、promote_scan 挂接点、账本会话管理（§3） |
| `kernel/bootstrap.py` | **新增** | 生产账本初始化：空 schema 建库 + 播种库编制/考试存款一次性幂等导入（D28，§3.1） |
| `exams/judge.py` | **扩展** | `judge_answer` 增 reviewer/verifier 两角色考试判分（§4.3；沿 S4 留口扩展，词表不改） |
| `exams/seeding.py` | **最小扩展** | `SEEDING_ROLES` 参数化：CLI 增 `--roles`（缺省仍 `(scout, auditor)`，既有行为零扰动） |
| `configs/params.yaml` + `contracts/config.py` | **扩展** | `perception` 节新旋钮（§7；由实现子任务回填，`extra=forbid` 纪律同步） |

### 0.3 复用清单（禁止重写的既有面）

`KernelLedger`（append/transaction/三时钟）、`agg_cells`/`CellView`、`route`/`route_thompson`、`promote_scan`（含 v2 通道）、`dispatch`（契约装配/派单状态机/代落账）、`budget`（配额/熔断）、`cluster`（两级聚类，M4 L2 消费）、`prompts.render_exam/render/parse_output`（scout 模板已有）、`supply.UrllibAdapter/load_models`（含 runtime 三键：32K/600s/thinking）、`exams.*`（bank/paper/judge/roster/runner/seeding/store）。

### 0.4 范围边界（M3 不做）

- **repo map 切片注入**（§2.1 v0.3.4 补）随 L2（依赖序第 7 位，M4）；M3 只落**tree-sitter 调用点索引本体**（L0-A 与 repo map 共享地基，§1.4）。
- **S2 沙箱**（依赖序第 8 位）不建；verifier 生产面收缩为预裁决单（D29）。
- **L2 会诊 / 聚类消费 / 评审投票的生产运行**随 M4；M3 只保证 L0→L1 全链 + 存款记账正确（L1 存活点是 M4 的输入）。
- semgrep 通道 C 为可选增强件，环境不可用不阻塞（D30）。

---

## 1. 感知层 L0 v2（三通道候选收集）

### 1.1 候选契约（`perception/candidates.py`）

```python
@dataclass(frozen=True)
class Candidate:
    candidate_id: str      # 内容寻址：blake2b(path, span, kind, clue) 16B hex（cache_key 同型）
    path: str              # 仓库相对路径（posix）
    span: tuple[int, int]  # 1-based 行闭区间 [start, end]
    kind: str              # source|sink|dangerous_call|serialization|rule_hit|model_flag
    channels: frozenset[str]   # ⊆ {"A","B","C"}（多通道命中归并后为并集）
    priority: int          # 基础 1；每多一通道命中 +1；规则命中再 +1（§1.6）
    taxonomy_hint: str | None  # 可选家族提示（clue 表带出；仅供 L1 参考，不约束）
```

- 候选集 = `list[Candidate]`，全链**确定性**（同仓库内容同参数 → 同候选集，含排序：`(path, span)` 字典序）。
- 序列化工件：候选集整体入工件库（`kind="l0_candidates"`，内容寻址，复用 `exams.store.artifact_put` 形态）。

### 1.2 通道 A：结构（`channels.py` + `clues.py` + `indexer.py`）

- **clues 表（`clues.py`，纯数据）**：taxonomy 家族 → 结构线索。初版：
  - `injection`: sink 函数名集（`system/exec/popen/eval/query/execute...`）+ source 形态（`argv/request/GET/POST/input...`）；
  - `crypto`: 弱算法/硬编码 key 名（`md5/sha1/des/ecb/hardcoded...`）；
  - `authz`: 会话/权限函数（`is_admin/check_permission/session...`）；
  - `config_dep`: secret 形态（`password/api_key/secret = 字面量`）；
  - `mem_safety`: 危险内存函数（`strcpy/gets/memcpy/sprintf...`）。
  - 词表初版按种子题库与常识清单落，标注"v1 词表，允许扩充不收缩"。
- **tree-sitter 分级（D25）**：按 grammar 可用性运行时探测（`import` 成功即用）：tier-1（python/js/go/java/c/c++ 官方 grammar）→ 调用点/函数名精确收集；tier-2（其余已装 grammar）→ 同机；无 grammar → **通道 A 对该语言缺席**（通道 B 兜底，§2.1 设计逐字）。实现不得硬编码语言清单，以探测结果为准。
- **索引（`indexer.py`）**：逐文件解析产出调用点索引（`path → [(行, 函数名, 家族线索)]`），内容寻址缓存：`key = blake2b(file_bytes)`，未变不重析（§2.1 缓存跳过逐字）。索引工件 `kind="l0_index"`。
- 产出：`Candidate(kind∈{source,sink,dangerous_call,serialization}, channels={"A"})`。

### 1.3 通道 B：模型直扫（`channels.py`）

- **执行者（D26 负责人拍板：侦察蜂兼任）**：通道 B 直扫任务由 **scout 蜂**执行——分片装配 `DispatchContract(role="scout", task_id="l0scan:<shard_id>", goal="L0 直扫：结构候选收集")`，经既有派单面（temp 池 least-loaded）调用 `model_ref` 对应蜂；响应解析后候选工件**记 agent_id 归属**（写蜂的工件区，§4.1 权限边界逐字）。
- **存款口径（D26 配套钉死）**：候选收集**无对错语义 → 零口碑存款**（账本零 deposit）；侦察蜂口碑仍只由 L1 初筛（§2.3 ×0.2）与 fast 档上报供血。`task_id` 前缀 `l0scan:` 与 L1 任务 `l1:` 严格区分，防两类工时在格子上混账。
- **提示词**：`perception/prompts.py` 新模板（纯函数零 IO，同 prompts 纪律）：输入文件/diff 文本，输出 JSON 候选列表 `[{path, start_line, end_line, kind, family_hint, reason≤200字}]`；温度沿用 `EXAM_TEMPERATURE`（0）。
- **失败语义**：解析失败/空响应重试 1 次，仍败则**该分片通道 B 缺席**（记计数 `model_channel_fail` + 归属蜂 id 进扫描报告，不抛错——候选收集尽力而为，L1 还有真筛选）。
- **分片**：单文件 ≤ 800 行整文件；超长按函数边界（索引可得）切 ≤ 800 行片；diff 档只扫变更文件 ± 调用邻域（§2.1 逐字）。

### 1.4 通道 C：规则（`channels.py`）

- **必选件（D30 负责人拍板）**：M3 门禁含 semgrep 安装装通 + 家族规则集映射 + tier-1 语言实跑判据；`perception.rules.enabled` 缺省 **true**。命中 → `Candidate(kind="rule_hit", channels={"C"})`。
- **红线（§2.1 逐字）**：命中仅提升候选优先级，**不是准入条件**；运行期单次超时/故障 → 该轮通道 C 缺席并计数 `rule_channel_skipped`（必选指"装通且门禁实跑"，非"每轮强依赖"——漏斗永不因规则通道停摆）。环境安装受阻时走 `DECISIONS.md` 披露，不得静默降级为可选。

### 1.5 归并与优先级（`candidates.py`）

- 同文件 span **重叠或间距 ≤ 5 行**的同 kind 族候选 → 合并：`channels` 并集、`priority = len(channels) + (1 if "C" in channels else 0)`、span 取并集、candidate_id 重算。
- 多通道同时命中 → 候选优先级更高（§2.1 逐字）；同优先级按 `(path, span)` 稳定排序。

### 1.6 档位行为（§2.2 表逐字）

| 档位 | L0 行为 |
|---|---|
| fast | 三通道高置信命中（A 结构线索 + C 规则；两者皆无的语言 = B 直扫）→ 按 priority 降序取 **Top-3 候选**（`l1.fast_top_k`） |
| pr | diff ± 1 调用邻域（索引跳 1 跳）；A/B/C 全开 |
| full | 整仓枚举 + 全部规则；分片供 L1 目录级（§5.3） |

---

## 2. L1 侦察 v1（分片、任务化、存款）

### 2.1 侦察蜂分片（§5.3 逐字）

- fast/pr 档：**文件分片**；full 档：**目录/模块级分片**（同目录共享 import 上下文）。
- 分片确定性：目录树字典序遍历，分片大小 `l1.files_per_shard`（缺省 20 文件/片）。

### 2.2 任务化与派单

- 候选分片 → `DispatchContract`（SPEC-2 §2 四件套）：`role="scout"`、`goal="L1 初筛：可疑点标记 + 初步类型猜测"`、`taxonomy_node=family`（分片内线索多数族；无线索 = None）、`deadline_s` 沿用。
- 侦察蜂选择：**临时工池 least-loaded**（temp 池不按口碑路由——侦察蜂口碑按 §6.5 ×0.2 记账，v1 分派不做 bandit）；`route()` 生产接线用于 M4 的 L2（审查蜂按 (agent×类型) 口碑路由），本 SPEC 只要求 route() 在账本上**可调可测**（存款就位即可路由）。
- 蜂产出走 `dispatch` 代落账（SPEC-2 既有面）；scout 输出解析复用 `prompts.parse.parse_output("scout", ...)`，畸形 = 判弃并记 `l1_schema_fail`（不重试——侦察面宽松，L2 才是精度主战场）。

### 2.3 存款语义（§6.5 + §16.7-1 逐字）

- L1 verdict：可疑点存活（`suspicious=true` + 家族猜测）或明确拦截，按侦察行记 **×0.2** 权重存款；
- fast 档整任务上报时 `reporter=侦察蜂`（fast 档不跑 L2，侦察蜂即报告者，主供血通道）；
- 猜测家族 → L2 路由 taxonomy 初值（§2.1 逐字），由 `kernel.reroute` 猜测格弱存款既有面承接（M4 消费）。

---

## 3. promote_scan 生产接线 + 生产账本续接

### 3.1 账本续接（D28 负责人拍板：新建空账本导入）

- 生产账本 = **全新空账本** `out/prod/ledger.db`（SQLite backup API 初始化空 schema），与实验/播种库**物理隔离**；
- 启动导入（一次性、幂等）：从 `out/m2-seeding-v03/seeds.db` 导入 ①编制行（agents/hats，4 蜂 active）②20 笔考试存款事件（append-only 顺序重放，`src_seq` 映射保留溯源）；**播种原库只读不触碰**；
- `kernel/loop.py` 会话开启时校验：4 agent status=active、考试存款数 ≥ 20，否则拒绝开工（防空账本启动）；
- 导入器落位 `kernel/bootstrap.py`（新增），行为 = SPEC-2 append-only 纪律下的纯重放（事件 payload 逐字复制，seq 重新编配，`src_seq` 记原库序）。

### 3.2 promote_scan 挂接（影子运行，安全阀）

- 每轮扫描循环尾部：`promote_scan(ledger, cells, now=...)` 照常执行（状态流转记账），但 **ENTRY_TOP_FRAC=0 / 直晋通道保持默认关闭**（M0 定稿参数），生产首季无自动晋升——影子期只产账，不动的编制由人审（§16.6-9 影子运行逐字）。
- 暗考触发回调留接口不实现执行（考试执行器已有，等影子期结束由负责人开启）。

---

## 4. reviewer/verifier 补播

### 4.1 注册与考试

- `expand_candidates(models, roles=("reviewer","verifier"))`：reviewer→mid（glm-5.1）、verifier→engineering（glm-5.3），档位已在 `configs/models.zhipu.yaml` 登记；
- `seeding.py --roles reviewer,verifier`（新增 CLI 参数，缺省不变）；出卷/缓存/落账/S9 复用 SPEC-6 全套；
- 补播考试为**纸面考试**：reviewer 卷 = 快照 + 预置 finding（题库 real 面产出 finding 送审；fp 面送审假 finding），verifier 卷 = 预置簇证据链（无沙箱执行，D29）。

### 4.2 判分扩展（`judge_answer`，沿 S4 留口）

- **reviewer**：real 题 = 意见为 `ENDORSE`（该 finding 立得住）；fp 题 = `REJECT`；多维意见中 `family` 维与答案键家族对齐才计对（与 auditor 同口径）。parsed=None = 判错（评审蜂必须表态）。
- **verifier**：real 簇 = `CONFIRMED`；fp 簇 = `REFUTED`；`INCONCLUSIVE` 恒判错（验证蜂考试不含弃权——区分能力与弃权是 §8.1 淘汰机制的根基）。
- 及格线沿用 `entry_exams.min_pass=0.8`；QC 流转复用 `apply_qc_decision`（formal 池）。

### 4.3 生产面 v0

- reviewer：S1 投票意见（M4 消费；§16.7-6 评审团构造——3 人、跨模型族、排除报告者、least-loaded，池不足 2 族降 S1 权重）。
- verifier v0（D29）：**S2 预裁决单**——消费精聚类簇（M4 起有簇；M3 纸面阶段对 L1 存活点产预裁决），输出 `{expected_verdict, poc_shape, env_requirements[]}` 三字段，不执行 PoC；S2 落地（依赖序 8）后升级为真验证，词表不改。

---

## 5. 不变量与纪律

1. **L0 红线**：规则永不卡点；跨函数传播链不在此层（§2.1 逐字）；
2. **写口纪律**：账本唯一写口仍是 kernel；perception 只写工件库与缓存，零账本直写；
3. **纯度**：perception 的 clues/归并/提示词渲染 = 纯函数零 IO 零随机；随机仅限派生（`derive_seed`）；
4. **网络落点唯一**：L0-B / 补播考试全部经 `supply.UrllibAdapter`；
5. **确定性**：同仓库内容 + 同参数 + 同账本 → 同候选集、同分片、同任务序（排序全显式）；
6. **语言无关**：任何语言至少通道 B 可进漏斗；通道缺席必须显式计数，不静默。

---

## 6. 新旋钮清单（`params.yaml` `perception` 节，回填由实现子任务执行）

| 键 | 缺省 | 语义 |
|---|---|---|
| `perception.model.enabled` | true | 通道 B 开关（执行者 = 侦察蜂，D26） |
| `perception.rules.enabled` | true | 通道 C 开关（semgrep，**必选件**，缺省开，D30） |
| `perception.rules.timeout_s` | 120 | semgrep 单次超时 |
| `perception.l0.max_file_lines` | 800 | 通道 B 单片行上限 |
| `perception.l1.fast_top_k` | 3 | fast 档候选数（§2.2 表） |
| `perception.l1.files_per_shard` | 20 | L1 分片大小 |
| `perception.scan.rescan_sample_frac` | 0.05 | 周期重扫抽样率（§16.7-4，v1 只落开关） |

---

## 7. M3 门禁要点（细则见 SPEC-5_m3_acceptance.md）

- 三通道单测各钉 ≥2 例（含无 grammar 语言通道 A 缺席、semgrep 运行期超时静默缺席计数）；
- **semgrep 必选门禁**：环境装通 + 家族规则集映射 + tier-1 语言样例实跑出 rule_hit 候选（D30）；
- 通道 B 侦察蜂兼任：`l0scan:` 任务走 temp 池派单、工件记 agent_id、账本零 deposit（D26）；
- 归并确定性：同输入两跑候选集工件逐位一致；
- 缓存跳过：内容未变不重析（时间/计数断言）；
- L1 端到端 smoke：种子仓库 → 候选 → 分片 → scout 判 → 存款 ×0.2 落账（FakeAdapter）；
- 账本引导：空库导入后校验 4 active + ≥20 存款、二次导入幂等零重复，route() 可选出非均匀目标（D28）；
- 补播：reviewer/verifier 入职考全链绿（FakeAdapter）+ `judge_answer` 新词表用例；
- 全量 pytest 绿 + 既有 611 例零回归。

---

## 8. 决策记录索引（2026-10-12 负责人拍板）

| 编号 | 主题 | 裁决 |
|---|---|---|
| D25 | tree-sitter v1 语言分级 | **按建议拍板：探测式分级**——运行时探测 grammar 可用性（初版六门 tier-1：python/js/go/java/c/c++），不硬编码清单；无 grammar 语言通道 A 缺席、通道 B 兜底 |
| D26 | 通道 B 执行者 | **负责人拍板：侦察蜂兼任**（偏离建议稿）——scout 身份派单、工件记归属、零存款；`l0scan:` 前缀隔离 |
| D27 | L0 候选契约 | **按建议拍板**——Candidate 内容寻址 + 工件库 `l0_candidates`，账本零直写 |
| D28 | 生产账本 | **负责人拍板：新建空账本导入**（偏离建议稿）——空库 + bootstrap 一次性幂等导入编制与考试存款，生产/实验物理隔离 |
| D29 | verifier v0 口径 | **按建议拍板**——S2 预裁决单，不执行 PoC；真验证随依赖序 8 升级，词表不改 |
| D30 | 通道 C 定位 | **负责人拍板：必选件**（偏离建议稿）——缺省开、门禁实跑判据；运行期故障仍静默缺席计数（漏斗不停摆），安装受阻走 DECISIONS 披露 |

五字段已同步 `DECISIONS.md`（2026-10-12 SPEC-7 行）。
