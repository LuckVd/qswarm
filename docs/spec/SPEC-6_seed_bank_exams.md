# SPEC-6 种子题库 v0 与考试播种（M2 实现级规格）

> 来源：设计文档 v0.3.4 §8.1–§8.4（双轨考核/题库/runner/播种）、§1 P3（BYO 接入门禁 = 入职考）、§16.6 依赖序第 4 位（种子题库 v0 + 考试播种，组织出生前置件）、§16.7-2/-3/-8（agent 身份/PatchEval 污染缓解/考试 temp=0）、§11.5（考试通过率监控）、§9.2（编制分类）；SPEC-1 §2（agents/hats/exam_items/exam_results DDL，**已落地冻结**）、§4.3（ExamDepositPayload/ExamResultPayload）、§5.1（models.yaml 形态）；SPEC-3 §5（考试提示词变体与答案信封）；SPEC-4 §1.5 / `simulator.exam`（播种机制数学，M0 定稿）。
> 读者：AI 编码代理。本 SPEC 唯一真源；发现矛盾以本文件为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 硬纪律：**机制层零网络零新依赖不破**——全仓第三方闭集仍为 {pydantic, yaml, pytest}；网络只存在于 `qswarm/supply/adapter` 边缘实现（stdlib urllib，E6/S5 决策）；题库构建流水线是外网侧数据工程脚本（`scripts/build_seeds/`），不进 `qswarm` 包、不进 pytest 依赖。
> 状态：**定稿 v1.0（2026-10-11，规格导出）**——本轮决策 S1–S11（§7 决策记录，已逐条同步 `DECISIONS.md`）。实现落位由 M2 实现子任务按本文件执行；M2 门禁见 §8（SPEC-5 M2 卷据此导出）。

---

## 0. 范围、模块落位与来源映射

### 0.1 机制块 ↔ 设计出处（验收对映表）

| 机制块 | 设计文档出处 | 本文件 |
|---|---|---|
| 题库 item 契约 / 存储 / 版本化轮换 / 金丝雀 | §8.2 JSON 样例 + 防污染三层 | §1 |
| 题库三源构建流水线（40 CVE 回滚 + 60 变异） | §8.2 三源、§16.6 第 4 位、§16.7-3 | §2 |
| 考试 runner（出卷 / 调用 / 判分 / 落账） | §8.2 及格线、§8.4 播种机制 | §3 |
| 考试播种流程 + 编制注册 + 供给质检 | §8.4、§1 P3 接入门禁、§9.2 | §4 |
| 供给适配层（models.yaml / adapter / 温度） | §1.1 档位、§16.7-8、§9.3 互备 | §5 |

不在本 SPEC 范围：暗考/周期考的**执行**（promotion/periodic 出卷调度的生产化随 M3 promote_scan 接线；判分函数本 SPEC 定稿）、S2 沙箱（依赖序第 8 位）、直晋/席位孵化通道的生产化（S8）、多供应商健康度监控（§11.5，M3+）。

### 0.2 模块落位（M2 新增）

| 模块 | 状态 | 内容 |
|---|---|---|
| `qswarm/exams/__init__.py` | **新增** | 导出播种与题库公共面 |
| `qswarm/exams/bank.py` | **新增** | `SeedItem`/`SeedBank` pydantic、装载与校验器、canary 检查（§1） |
| `qswarm/exams/paper.py` | **新增** | `draw_paper` 确定性出卷（§3.1） |
| `qswarm/exams/judge.py` | **新增** | 逐角色判分纯函数 + 及格线函数（§3.3） |
| `qswarm/exams/runner.py` | **新增** | 考试执行编排（调用/解析/判分/缓存，§3.4） |
| `qswarm/exams/seeding.py` | **新增** | `seed_entry` 播种总编排（§4.2） |
| `qswarm/exams/roster.py` | **新增** | 候选集展开 + agents/hats 注册与 QC 流转（§4.1） |
| `qswarm/exams/store.py` | **新增** | exam_items/exam_results 表**唯一写口**（§4.3） |
| `qswarm/supply/__init__.py` | **新增** | 导出 ModelsConfig 与 adapter 面 |
| `qswarm/supply/config.py` | **新增** | `ModelsConfig` pydantic（models.yaml 装载，§5.1） |
| `qswarm/supply/adapter.py` | **新增** | `CandidateAdapter` 协议 + stdlib `UrllibAdapter`（§5.2，**全仓唯一网络落点**） |
| `qswarm/kernel/exam_math.py` | **新增** | `centered_exam_weight` 上移（自 `simulator.exam` 纯移动 + re-export，S5） |
| `scripts/build_seeds/` | **新增**（外网侧） | 三源构建流水线（§2；不进包、不进 CI 依赖） |
| `qswarm/prompts/` | SPEC-3 新增 | 考试包装节 `render_exam`/`EXAM_TEMPERATURE` 消费方 |

写口纪律扩展（SPEC-2 §6.1 表经 S4 修订）：`agents` / `hats` / `exam_items` / `exam_results` 四表唯一写口 = `qswarm/exams`（考试与编制子域）；`scripts/audit_write_ports.py` 词表与 `tests/test_kernel_write_guard.py` 同步扩展；`ledger`/`cells` 写口不变（播种存款走 `KernelLedger.append` + `agg_cells`，与 M0 世界侧同路径）。

---

## 1. 题库数据面（`bank.py`）

### 1.1 `SeedItem` 契约（§8.2 JSON 逐字段定稿）

```python
class SeedItem(BaseModel):
    """一道种子考题（盘上 item.json 的 schema；入库映射到 SPEC-1 §2 exam_items
    列——snapshot_files 只随快照清单工件（repo_snapshot_ref 指向）承载，不入表）。"""
    item_id: str                 # "SEED-<4位序号>"，如 SEED-0417；全库唯一
    taxonomy_path: list[str]     # [family] 或 [family, type]；family ∈ taxonomy.yaml
    lang: str                    # 快照主语言（free-form 标签，非路由键）
    repo_snapshot_ref: str       # "artifact://seeds/<item_id>/"（§1.3）
    planted: bool                # True = 代码经人工植入/变换（变异埋雷、变异真题）
    expected: str                # "real" | "fp"——考官答案：real=应报出漏洞；fp=应拒报
    difficulty: str              # "easy" | "medium" | "hard"
    version: int                 # ≥1；变异轮换时 +1
    canary: str                  # 金丝雀串（§1.4）；全库唯一
    source: str                  # ExamItemSource：cve_rollback|patcheval_mutated|
                                 #   juliet_mutation|owasp_mutation（枚举已冻结）
    snapshot_files: list[str]    # 相对 snapshot/ 的文件名清单（快照清单工件承载）
```

- 词表全部复用已冻结枚举（`Family`/`ExamItemSource`）；`expected="real"` 的题 `planted` 可真可假（CVE 回滚=原漏洞代码 planted=False；Juliet 变异真题 planted=True）——**expected 才是判分依据**（S1 决策注）。
- `taxonomy_path` 长度 1（家族级）为 v0 主体；类型级题（长度 2）允许存在但 v0 播种只按家族级判分。
- 校验器（`validate_bank`，装载时全量执行，任一失败 → ValueError 带题号）：
  1. `item_id`/`canary` 全库唯一；`taxonomy_path[0]` ∈ taxonomy.yaml families；
  2. 快照文件在盘上存在且非空；`snapshot_files` 与目录实际内容一致；
  3. canary 串在快照文本中恰好出现 ≥1 次（§1.4）；
  4. **原版直通红线**（§16.7-3 逐字）：`source=cve_rollback` 的题必须附 `upstream` 元数据（CVE 编号 + fix commit sha）且 commit 日期 > 模型知识截止线（构建器校验，装载器复核字段在位）；PatchEval 来源的题 `source` 必须为 `patcheval_mutated`（原版不入考题）；
  5. 每家族 real/fp 配额满足 §1.2 配额表。

### 1.2 v0 题库配额（S1 拍板，100 题；v0.2 修订 2026-10-11，DECISIONS 同日行）

| 家族 | CVE 回滚(真) | Juliet/OWASP 真 | 变异雷(fp) | 小计 |
|---|---|---|---|---|
| injection | 2 | 16 | 10 | 28 |
| crypto | 0 | 12 | 8 | 20 |
| authz | 5 | 7 | 8 | 20 |
| config_dep | 0 | 12 | 8 | 20 |
| mem_safety | 0 | 8 | 4 | 12 |
| **合计** | **7** | **55** | **38** | **100** |

- **v0.2 修订**：CVE 列按全池实测供给定稿——扩池 99 包穷尽挖掘后，符合全部红线（post-cutoff 修复 + GitHub 可溯源 + ≤8 文件 ≤500 行 + 家族签名）的真实修复共 **7 条**（injection 2 + authz 5；142 候选死于知识截止线/文件数，证据链 `results/_rerun_w4_build_token*.log`）。缺口 27 题由 Juliet/OWASP 真面回补；(real, fp) 聚合与各家族出卷下限不变。v0.1（deser 退出、六→五家族）承前有效。
- **v0.1 修订**：deser 家族退出 v0 题库（六→五家族）。原因：Juliet Java v1.3 官方发行版不含 CWE502（zip 内 changelog "Total number of CWEs covered: 112"，五源查证一致）、OWASP Benchmark 无 deser 类目——变异雷只能取自「漏洞面/修复面对」语料的修复面，deser 的 fp 配额在现有两类源下无解；SARD 散题 CWE-502 覆盖未能证实（搜索接口不过滤）。deser 家族仍属 §7.2 家族树，其格子冷启动走生产存款通道（无考试加速与周期复核兜底——负责人已拍板接受该 trade-off）。
- 全库 real=62 / fp=38（与 v0 相同）；每家族 real ≥ 8、fp ≥ 4——满足 §3.1 出卷下限（mem_safety 真题 8 = 单卷全出，v0 已知局限，轮换版本扩充）。
- mem_safety 配额压低承 §16.2.5（内存安全家族 v1 降权）；§7.2 家族树序即本表序（deser 列保留在家族树，仅不在 v0 题库出题）。

### 1.3 盘上布局与工件库关系

```
seeds/
  bank.yaml                     # bank_version、生成时间、items[]: {item_id, sha256(题目录)}
  items/<item_id>/item.json     # SeedItem 序列化
  items/<item_id>/snapshot/...  # 快照文件（漏洞文件全文 + 必要邻接文件）
```

- 快照规模约束（构建器执行，装载器复核）：每题 ≤ 8 文件、单文件 ≤ 500 行——考试上下文成本与审计可达性的 v0 平衡（S2）。
- `bank loader` 装载时把每份快照文件写入内容寻址工件库（kind=`seed`，artifacts 表索引行；`item.json` 同入库），`repo_snapshot_ref` 指向该题快照清单工件。考试侧经工件库读取，不直接依赖 seeds/ 盘上路径（部署形态解耦）。
- `bank_version` 单调递增（构建器签发）；出卷与判分记录钉在 `exam_id`/`exam_results.model_ref` 之外的播种报告里（§4.4），跨版本不可混卷。

### 1.4 金丝雀与轮换

- canary 形如 `qsw-canary-<item_id>-<8位十六进制>`，以无害注释或标识符形态植入快照（构建器随机选形态与位置）——§8.2「泄漏可溯源」：模型输出中出现 canary 串 = 该题可能进入过预训练语料，触发该题下机（`retired_at` 置位）并换版本重造。
- 轮换：任一题泄漏/疑似污染 → 该题 `version+1` 重新变异（canary 换新）或 retire 补位；`bank_version+1`。轮换由构建流水线执行（§2），机制层只消费版本号。

## 2. 题库构建流水线（`scripts/build_seeds/`，外网侧）

三段式，全部确定性（种子注入），产物入库前过 §1.1 校验器：

1. **取材**：①CVE 回滚——CVEfixes 类语料取 fix commit 的 **parent** 快照（`expected=real`，`planted=False`）；②Juliet/OWASP——取测试用例对（漏洞面/修复面），变异后分别作真/雷（§1.2 配额）。网络只在此段；缓存后可离线重放。
2. **变异**（§16.7-3 逐字缓解）：标识符重命名、语句重排、死代码注入、结构变换四族；每题独立种子；变异不改变真/雷属性（变异后用固定规则集复核 expected 标注，不一致 → 弃题）。PatchEval 取材题**必须**过变异且只取 post-cutoff 子集。
3. **定稿**：canary 植入 → `item.json`/快照落盘 → `bank.yaml` 签发（sha256 清单）→ 校验器全绿。

- 流水线脚本不入 `qswarm` 包、无 pytest 依赖（CI 以 `--offline` 跳过）；其正确性由 §1.1 校验器 + M2 门禁（§8）机器可检。
- 语言覆盖：跟随取材语料（PatchEval = Go/JS/Python；Juliet = C/C++/Java 等）；`lang` 仅作标签（cells 无语言维度，§7.1 层级不含 lang）。

## 3. 考试 runner（`paper.py` / `judge.py` / `runner.py`）

### 3.1 出卷（`draw_paper`，确定性）

```python
def draw_paper(
    bank: SeedBank,
    *,
    agent_id: str,
    role: Role,
    family: str,
    purpose: ExamPurpose,          # M2 只实现 ENTRY；其余 raise NotImplementedError
    bank_version: int,
    seed: int,
) -> list[SeedItem]:
    """入职考出卷（§8.4「8–12 题（含埋雷）」）：每 (agent, family) 一卷。

    - 卷型（S3 拍板）：ENTRY = 12 题 = 8 真 + 4 雷——对齐 M0 定稿仿真口径
      （ExamWorldParams.entry_items=12）；真/雷分别从该家族 expected 池抽取；
    - 确定性：随机源 = derive_seed(seed, "exam", "entry-v1", agent_id, family,
      bank_version)（SPEC-4 rng 纪律同型）；池内不重复（池不足 → ValueError，
      §1.2 配额保证不触发）；真池/雷池各自洗牌后按序取（洗牌 = Fisher-Yates on
      派生 rng，禁内建 hash）；
    - 题序：真雷交错固定模式 [真,真,雷,真,真,雷,…]（12 题位次固定，防「后段全是雷」
      的位置指纹）；同 (bank_version, agent_id, family) 重放逐位同卷。"""
```

### 3.2 考题呈现与信封（SPEC-3 消费）

- 每题数据块 = 题面头（`item_id`、lang、"判断以下快照是否存在漏洞" 一句话）+ 快照文件按 `snapshot_files` 顺序注入（`ContextBlock`）；**题面不给 family/type**（S4：给了类型，真题判分退化为"复述假设"；信任自由分析 + 家族对齐判分）。
- 调用温度 = `prompts.EXAM_TEMPERATURE`（0.0，§16.7-8）；只走该 agent 的 primary 模型，**不走 fallback**（S6：fallback 是生产可用性机制；考试考的是这个模型的契约能力，换模型作答无效——primary 不可用按基建失败处理，§3.4）。
- 输出经 `prompts.render_exam`/`parse` 提取信封 `{"answers": [...]}`；信封解析失败按该题 schema 失败计（§3.4）。

### 3.3 判分（`judge.py`，纯函数）

```python
def judge_answer(
    item: SeedItem, role: Role, parsed: ScoutReport | AuditorFinding | None,
) -> bool:
    """单题判对与否（§8.2 考官答案对齐；v0 覆盖 scout/auditor 两角色）。

    - **scout**：parsed=None（信封 declined 或 output 缺失）= 未标记任何候选，
      按 suspicious=False / guess_family=None 判定（real → 判错；fp → 判对，
      对埋雷拒报是正确行为）；产出时：
      real 判对 = suspicious=True ∧ guess_family == item.taxonomy_path[0]；
      fp 判对 = suspicious=False 或 guess_family 为 None（未猜出家族=没有可路由
      猜测，与拒报同效）；injection_flag 不参与判分（记录进播种报告）；
    - **auditor**：real 判对 = 产出 finding ∧ family == item.taxonomy_path[0]
      （type 不判——家族级播种）；fp 判对 = declined（output=null）；
      产出但家族错 → 判错；产出多题任一 family 命中不放宽（信封单 finding 形态，
      SPEC-3 §5）；
    - reviewer/verifier：NotImplementedError（S4：v0 不播种——reviewer 需 staged
      finding 工件面（M3 随 L2/评审开工）、verifier 需沙箱（依赖序第 8 位）；
      两角色 cells 冷启动走家族级收缩（§5.1 既有机制））。"""
```

- 及格线函数（本 SPEC 定稿，执行随对应通道）：
  - `promotion_exam_passes(real_correct, fp_rejected)`：真题全对（3/3）且拒雷 ≥1（§8.2 逐字；实现上移随 M3，见 S5 范围注）；
  - `periodic_exam_passes(real_correct, fp_rejected, total, params)`：总分 ≥ `PERIODIC_PASS`（0.75）且真题侧 ≥ 2/3 且雷题侧 ≥ 1/2（§8.2 双侧下限）；
  - `entry_qc_pass(accuracy, min_pass)`：`accuracy ≥ min_pass`（供给质检线，§4.1）。

### 3.4 调用失败语义（S7 拍板）

| 失败 | 处理 |
|---|---|
| 网络超时/5xx/429（重试 1 次后仍失败） | **基建失败**：该题不写 exam_results 行、不计入判分；同 (agent, family) 基建失败题数 > 2 → 该格考试**整体无效**（零存款、零 exam_result 行、播种报告标注 `infra_invalid`） |
| 输出解析失败（含信封畸形），重试 1 次后仍失败 | **判错**（correct=0，播种报告记 `schema_fail` 计数）——考试考的就是「按契约产出」，契约失能是真实能力缺陷信号；§4.3「不合格直接退回」的考试形态 |
| 调用成功但输出含金丝雀串 | 判分照常；该题标 `canary_hit` 进报告（泄漏溯源通道 §1.4，触发轮换复盘） |

- 每次调用的原始响应全文写工件库（kind=`exam_log`，producer=agent_id，artifacts 表索引行；DDL kind 注释同步修订）——判分可独立复核、replay 可复现（§3.5）。
- `exam_results` 行 `correct` 全量落定后，每 (agent, family) 计算真题正确率 `accuracy = real_correct / real_n`，经 `kernel.exam_math.centered_exam_weight` 映射为存款 `(direction, magnitude)`。

### 3.5 位级可复现（S9 拍板）

- 考试结果的最终真源是**响应缓存**（exam_log 工件）；`runner --replay` 从缓存重放判分与落账，不经网络——同缓存两次 replay：exam_results 行、deposit 事件流（seq 顺序 + payload）**逐字节一致**（M2 门禁判据，§8）。
- 在线调用的原始输出天然非确定（供应商侧）；确定性纪律的边界 = 缓存之后的一切（出卷、解析、判分、落账、折叠）零随机源零时钟漂移（时钟注入三时钟不变）。

## 4. 考试播种（`roster.py` / `seeding.py` / `store.py`）

### 4.1 编制注册（`roster.py`）

```python
def expand_candidates(models: ModelsConfig) -> list[Candidate]:
    """候选集展开（S10 拍板）：角色 → 档位映射（§4.1 五型分工逐字）
    scout→cheap / auditor→strong / reviewer→mid / verifier→engineering；
    每档 primary + fallbacks 去重展开为候选模型；同 (role, model_ref) 唯一
    candidate（§16.7-2「agent_id ≡ (模型, 契约版本)」的直接推论——同模型同契约
    不可能是两只蜂）。
    params.roster 是 M0 仿真规模（§16.7-7「仿真器 M0 校准规模」），不约束真实
    编制数；真实编制 = 供给面展开结果。参考 models.yaml 下：cheap×2 + strong×2 +
    mid×1 + engineering×1 = 候选模型 6 个 → v0 候选 = scout×2 + auditor×2
    （reviewer/verifier 注册不参考）。"""

def register_roster(candidates, ledger, *, clock) -> list[str]:
    """agents + hats 注册（编排在本模块，SQL 经 store.py 唯一写口落库，S4）：
    - agents: agent_id = f"{model_ref}@{CONTRACT_VERSION}"（SPEC-3 §1）、
      status='provisioning'、model_ref、contract_version=CONTRACT_VERSION；
    - hats: (agent_id, role, pool_class)——scout=temp（临时工，§9.2）、
      auditor/reviewer/verifier=formal（verifier v0 取骨干口径）；
    - 幂等：agent_id 已存在且字段全等 → 跳过；存在但字段不等 → ValueError
      （身份冲突，人工介入）。"""
```

### 4.2 播种总编排（`seed_entry`，§8.4 逐字落法）

```
for agent in 候选编制（注册序 = agents 表插入序）:
    for family in 家族树（taxonomy.yaml 序）:
        出卷（§3.1）→ 逐题调用/解析/判分（§3.2–3.4）→ exam_results 行（store）
        → 按 (agent, role, node=family) 写一笔考试存款（§4.3）
```

- **存款形态（M0 逐位同形）**：每格恰一笔 `ExamDepositPayload`——`agent`、`role`、`taxonomy_path=[family]`、`signal_tier=S1`、`direction`/`weight` = `centered_exam_weight(accuracy, w_low=0.2, w_high=0.5)`（幅值域常量与 M0 一致）、`origin=exam`、`exam_id=f"entry:{agent_id}:{family}"`；经 `KernelLedger.append` 落账（事件序 = 编排序，确定性）。
- **只供血不孵化**（S8）：M2 播种不写 promotion 事件、不动格子 status——`ENTRY_DIRECT`/`ENTRY_TOP_FRAC`/`TYPE_ENTRY` 直晋通道的生产化随 M3（promote_scan 夜批接线）落地；播种后首批 candidate 由 M3 夜批从存量考试存款自然扫描产生。
- 全部格存款落账后调用 `kernel.cells.agg_cells` 折叠（唯一 cells 写口不变）；考试存款 `T_HALF_DAYS_EXAM=15` 半衰由聚合器按 `origin=exam` 处理（SPEC-1 §4.3 既有语义）。
- **双轨制不破**（§8.4 逐字）：考试存款只影响路由；S1 档使其天然不进晋升硬门槛 n_eff(S2+S3)——机制继承，无新增代码。
- 无效格（§3.4 infra_invalid）零存款零行；整场播种无效格占比 > 10% → 播种报告标记 `degraded`，门禁不通过（§8）。

### 4.3 表写口（`store.py`，S4）

- `exam_items`：bank loader 装载时按 item_id UPSERT——同 item_id 载入更高 `version` → 该行全列 UPDATE（内容换新，DDL 主键即 item_id，一题一行；`retired_at` 只在题被移出题库时置位，轮换换版不清位）；`created_at`/`retired_at` 由注入时钟签发。
- `exam_results`：`id = f"ER-{seq:06d}"`，seq = 库内 `ER-` 前缀 max+1（D20 同型口径）；`model_ref` = **实际服务模型**（§3.2 只走 primary，故恒 = agent.model_ref，字段留痕防未来放宽）；`ts` = 注入时钟。
- 写口顺序与存款事件同事务性：单格内 exam_results 行先落、deposit 事件后落，两者之间崩溃 → 该格按「部分作答」重跑（出卷确定 ⇒ 重跑覆盖同批行，行级幂等键 = (exam_id 承载于 id 序 + (agent_id, purpose, item_id)) 查重跳过）。

### 4.4 播种报告（§8.4「组织出生证明」）

`out/seeding_report.{json,md}`：逐 agent 逐家族 accuracy / direction / weight / 判分明细（含 schema_fail、infra 失败、canary_hit 计数）+ QC 判定（§4.1）+ bank_version + 模型调用统计。JSON 结构钉死（黄金形态回归）；报告中无任何 api_key 或网络端点凭据（§11.6-5）。

### 4.5 供给质检（§1 P3「接入门禁 = 入职考」）

- 判据：agent 的**全家族真题总正确率** < models.yaml `entry_exams.<tier>.min_pass` → QC 不通过（S11：per-agent 总口径；`families: all` 逐字）。
- 处置：不通过 → agents.status 留 `provisioning` + 报告告警（**拒绝入编**）；通过 → `active`（`last_active_at` 置位）。§8.4「某 agent 全家族垫底 → 预算层不扩编」由同一线承载。
- 质检线是**入编闸门**，不是播种开关：无论 QC 结果，全部有效格的考试存款照写（路由供血先于入编裁决，两者独立——落选者的历史存款随 dormant 语义衰减）。

## 5. 供给适配层（`supply/`）

### 5.1 `ModelsConfig`（models.yaml 契约定稿，pydantic）

```python
class ModelEntry(BaseModel):
    provider: str                # "openai_compatible" | "anthropic"（线格式族，§5.2）
    model: str                   # 供应商模型名
    api_key_env: str             # 环境变量名（值永不入账本/工件/日志，§11.6-5）

class TierConfig(BaseModel):
    primary: ModelEntry
    fallbacks: list[ModelEntry] = []

class EntryExamGate(BaseModel):
    min_pass: float              # 该档入编线（§4.5）
    families: str                # v0 恒 "all"（逐字 SPEC-1 §5.1）

class RuntimeConfig(BaseModel):
    rpm_limit: int = 600
    tpm_limit: int = 4_000_000
    batch_mode_for_full: bool = True
    production_temp: float = 0.2 # 新键（SPEC-3 §5 温度纪律的配置承载；≤0.2 红线）

class ModelsConfig(BaseModel):   # extra="forbid" 纪律同 params
    tiers: dict[str, TierConfig]           # 键 ∈ {cheap, strong, mid, engineering}
    entry_exams: dict[str, EntryExamGate]  # 键同 tiers 子集
    runtime: RuntimeConfig
```

- `load_models(path)`：文件缺失 / 档位键越词表 / min_pass ∉ [0,1] → ValueError；`api_key_env` 对应环境变量在**调用时**才读取（装载不触 env）。
- RPM 限速：adapter 维护最小调用间隔 `60/rpm_limit` 秒（令牌桶 v0 简化形）；播种量级（候选×家族×12 题）远低于限，间隔逻辑只为纪律在位。

### 5.2 `CandidateAdapter` 协议与 stdlib 实现

```python
class CandidateAdapter(Protocol):
    def complete(self, *, model_ref: str, messages: list[Message],
                 temperature: float, timeout_s: float = 120.0) -> str: ...
    """一次补全调用。实现只认 models.yaml 反查到的条目（S6）；网络失败/非 2xx →
    AdapterError（含 provider/model/状态码，**不含 key**）；重试与 fallback
    编排归调用方（考试侧不走 fallback，§3.2；生产侧 M3 再接）。"""

class UrllibAdapter:      # 全仓唯一网络落点（S5/E6）
    """stdlib urllib.request 实现；线格式两族：
    - openai_compatible（provider ∈ {deepseek, openai, …}）：POST {base}/chat/
      completions，messages 直传，response_format 不用（提示词已约束 JSON）；
    - anthropic：POST {base}/v1/messages，system 单列、user 直传；
    温度逐字透传；max_output_tokens 上限 4096（考试面足够，防失控账单）。
    **传输入口可注入**（构造参数 opener，缺省 urllib.request.urlopen）：测试以
    fake opener 离线验证线格式与错误映射，禁真实网络测试（SPEC-5 M2 卷 A 组判据）。"""
```

- **零新第三方依赖**（S5）：不用任何 LLM SDK/httpx——stdlib 足够（两个线格式族 × JSON 编解码）；第三方闭集 {pydantic, yaml, pytest} 不破。
- **网络判据修订**（随 S5 留痕）：SPEC-5 §7.1「无网络 grep」范围修订为——`qswarm/` 全包 grep 网络库词根，**豁免 `qswarm/supply/adapter.py`**（该文件仅允许 `urllib.request`/`urllib.error`/`json`/`os.environ` 白名单根，逐文件 AST 白名单钉住）；机制层（contracts/kernel/simulator/prompts/exams）零命中不变。

## 6. 与既有里程碑的兼容性声明

- **M0 轨迹零影响**（S5 的硬前提）：`kernel/exam_math.py` 为纯移动（`centered_exam_weight` 函数体逐字节不变），`simulator/exam.py` 改为 `from ..kernel.exam_math import centered_exam_weight`（re-export 保 import 面兼容）；全量 pytest + 黄金 digest 66/66 + `--full` 位级复跑三关验证。
- M1 内核零改动（exam/dispatch/promote_scan 不触）；本 SPEC 全部新代码落在 `qswarm/exams`、`qswarm/supply`、`qswarm/prompts`（SPEC-3）、`kernel/exam_math`（纯移动）四域。
- pytest 收集面新增测试不触既有用例名（SPEC-5 §1 最低用例集只增不改）。

## 7. 本 SPEC 决策记录（已同步 DECISIONS.md）

| # | 决策 | 理由 |
|---|---|---|
| S1 | v0 配额 = 100 题（40 CVE 回滚真 + 22 变异真 + 38 变异雷），逐家族配额表（§1.2） | §8.4 入学考 8–12 题×全家族×全员 + §3.1 卷型的池下限反推；mem_safety 压低承 §16.2.5 降权 |
| S2 | 快照规模 ≤ 8 文件 / 单文件 ≤ 500 行 | 考试上下文成本 vs 审计可达性的 v0 平衡；全仓快照不可行（CVEfixes 父提交是整仓） |
| S3 | ENTRY 卷 = 12 题 = 8 真 + 4 雷，真雷位次固定交错 | 对齐 M0 定稿 entry_items=12（仿真↔现实同口径）；位置指纹防「后段全雷」应答漂移 |
| S4 | 题面不给 family/type；判分 = 产出 family 对齐 item 家族；v0 播种只覆盖 scout/auditor | 给类型使真题退化为复述假设；reviewer 需 staged finding 面（M3）、verifier 需沙箱（第 8 位）——角色随其生产面补播，机制上由家族级收缩兜底 |
| S5 | `centered_exam_weight` 纯移动上移 `kernel/exam_math.py`（simulator re-export）；供给适配层 stdlib urllib 零新依赖 | 单一实现纪律（SPEC-4 §0）指向 kernel 圣化层；SDK 引入即破第三方闭集，stdlib 足以承载两族线格式 |
| S6 | 考试调用只走 primary，不走 fallback | §16.7-2 agent 身份 ≡ 模型：换模型作答 = 考的不是这只蜂；fallback 是生产可用性机制非考试代理 |
| S7 | 基建失败不判分（无行；>2 题该格无效零存款）；schema 两连败判错并计数 | 基建噪音不得变成能力负分污染存款；契约产出能力本身是考项（§4.3 退回纪律的考试形态） |
| S8 | 播种只写存款不孵化（直晋/席位通道生产化延后 M3） | 直晋是 promote_scan/孵化机制的生产接线，独立子任务；混入 M2 会把「组织出生」与「孵化机制上线」两件事的验收耦死 |
| S9 | 响应缓存工件化（kind=exam_log）+ `--replay` 位级复现；确定性边界 = 缓存之后 | 在线输出供应商侧非确定；缓存后全链零随机才是本仓可验收的确定性口径（对齐 §2.3 位级纪律的精神） |
| S10 | 候选集 = models.yaml 档位条目去重展开 × 角色档位映射；同 (role, model_ref) 唯一 agent；params.roster 是仿真规模不约束真实编制 | §16.7-2 的直接推论；roster 6/5/4/3 是 §16.7-7 明文「仿真器 M0 校准规模」 |
| S11 | 入编线 = 全家族真题总正确率 ≥ entry_exams.min_pass；不过 → provisioning + 告警；存款照写 | §1 P3 接入门禁逐字；「入编裁决」与「路由供血」解耦，落选者历史账随 dormant 衰减（§8.4 垫底不扩编同线承载） |

## 8. M2 验收门禁（SPEC-5 M2 卷导出依据）

- [ ] **题库**：100 题入库，配额表逐格相符（§1.2）；校验器全绿（含原版直通 0、canary 在位）；`bank.yaml` sha256 清单自洽；bank_version=1。
- [ ] **出卷**：同 (seed, bank_version, agent, family) 重放逐位同卷；池不足 ValueError 路径有测试。
- [ ] **判分**：judge 负例矩阵（real 误报/fp 漏报/家族错配/declined）全绿；及格线三函数对 §8.2 逐字。
- [ ] **播种**：每有效格恰一笔 origin=exam 存款（M0 同形：S1/±[0.2,0.5]/exam_id 同构）；exam_results 行数 = Σ有效卷题数；agg_cells 折叠后考试份额在位；无效格零存款。
- [ ] **复现**：同缓存两次 `--replay` 的 exam_results 行 + deposit 事件流逐字节一致；跨 PYTHONHASHSEED 一致。
- [ ] **质检**：min_pass 线执行留痕（provisioning/active 流转 + 告警清单）；models.yaml 缺档/越词表拒绝装载。
- [ ] **写口**：`agents/hats/exam_items/exam_results` 四表 SQL 全部落在 `qswarm/exams`；`audit_write_ports.py --strict` 含新词表零漂移；kernel 写口词表不增。
- [ ] **约束**：第三方闭集 {pydantic, yaml, pytest} 不破；网络 grep 按修订口径（supply/adapter 白名单文件）零命中；M0 黄金 digest 66/66 + 全量 pytest 绿（exam_math 移动三关验证）。
- [ ] **留痕**：DECISIONS.md 同步（S1–S11 + 写口/网络判据两处 SPEC 修订）；播种报告 JSON 形态黄金回归。
