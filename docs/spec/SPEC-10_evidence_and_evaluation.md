# SPEC-10 证据与评测体系（P0：角色资格与配置身份；P1：可信测量最小闭环）

> 版本 v0.2（2026-10-24，P1 轮——§6–§9 由骨架升精度面）。依据：
> `docs/DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md`（下称「方案」）与
> DECISIONS 2026-10-22 D46 行（oracle 层级/三参数冻结/P0 放行）+ 2026-10-24
> P1 放行行（负责人「开工」；D47/D48 按评估 §6 建议值 A/A 保守执行，正式拍板
> 点保留至 E1 供给批准时追认）。v0.1（2026-10-22，P0 轮）§1–§5 精度面不变。
> 与现行 SPEC 的关系：不覆盖、不重解释（方案 §9）；SPEC-1 §1/§2 已随 v0.1
> 扩展；SPEC-9 §3 判据面随本卷模板 v2/oc-v1 同步增补。

## 1. 目标与边界

拆开三个概念（方案 §2 表逐字）：

| 概念 | 回答的问题 | 允许影响 |
|---|---|---|
| Qualification（资格） | 这个配置是否被允许执行这个角色？ | 派单资格、安全限制 |
| Finding verdict（判定） | 这个具体漏洞主张是否成立？ | 报告等级、该事实的有效证据 |
| Capability estimate（能力估计） | 这个配置在某类未来任务上的预期表现如何？ | 实验排名；批准后的路由 |

P0 只落资格面：**帽存在 ∧ 本体 active ≠ 具备角色资格**。入编考通过不等于专长
已证实；角色资格失败不影响蜂其他角色；一次 S2 结论不给 verifier 自证加分。

非目标（P0）：不改生产写口语义、不动 M0 黄金、不改 cells/存款、不启用任何
线上调用、不裁 D44/D45。

## 2. 身份模型

### 2.1 execution_profile_id

执行配置的规范化哈希（方案 §3.1 字段面逐字）：

```text
profile_id = "ep-" + sha256(canonical_json({
    provider,          # model_ref "tier:provider/model" 反解的 provider
    model,             # 同上的请求模型名
    role,              # Role.value
    prompt_hash,       # sha256(SYSTEM_BY_ROLE[role] + SCHEMA_FIELDS_BY_ROLE[role]
                       #        + FAMILY_GLOSS json) 前 16 hex——提示词内容哈希
    ctx_strategy,      # 工具/上下文策略版本，P0 常量 "ctx-v1"
    contract_version,  # prompts.CONTRACT_VERSION
    production_temp,   # 采样温度锚 = params.qualification.production_temp（缺省 0.2）
})).hex[:16]
```

- canonical_json = `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))`；
- 采样温度锚单源 = `params.qualification.production_temp`（**不是** models.yaml
  运行时值——models.yaml 调温属运行供给面，资格判据面固定锚定；两源偏差由
  迁移 manifest 对账暴露，不等即 stale，见 §4.3）；
- profile 变更（prompt 改版 / contract 升版 / 策略版本 / 调温）⇒ 新 profile，
  旧资格不自动沿用（方案 §3.2 规则 5）——读侧按当前 profile 查 passed，查无
  即拒派（fail-closed）。

### 2.2 qualification_policy_version

资格判据版本，`params.qualification.policy_version`（缺省 `qp-v1`）。判据版本
独立于考试数据版本（bank_version）与卷 id（paper_id）——后两者逐行留档于资格行
（§3），换判据版本 ⇒ 全体按新键查无 passed ⇒ 拒派待重考。

## 3. 资格记录与状态机

### 3.1 表

`role_qualifications`（DDL 见 SPEC-1 §2，本日扩展行）。行身份 =
`(agent_id, role, profile_id, policy_version)` + 单调 `id`（'RQ-<seq>'）；
`status ∈ QualificationStatus`；`superseded_by` 指向替代行。

**effective 行** = 同键下 `status='passed' ∧ superseded_by IS NULL` 的最新行
（`started_at` 降序、`id` 降序取一）。effective 查无 ⇒ 该 (agent, role) 无资格。

### 3.2 状态与迁移

```text
            ┌────────── 考试开考（seed_entry 逐 candidate）──────────┐
            │                                                        │
       [无行/pending] ──考过 QC──→ [passed]──被新行替代──→ superseded
            │                          │
            │                     管理动作 revoke / 迁移判定 / profile 失配
            ↓                          ↓
        [failed]（QC 未过）       [stale / revoked]
```

写规则（方案 §3.2 规则 1–7 逐字落地）：

1. `attach_hat` 只声明角色，不写资格行——帽与资格分离；
2. 首次考试及强制重认证先写 `pending`（`started_at`=开考时刻）；调用中断、
   参数校验失败不得留下 `passed`（pending/failed 即非 effective，天然拒派）；
3. 纯诊断测评（purpose ∉ {entry}）不写资格行——诊断不改变资格；
4. QC 判定原子写 `passed`/`failed`（与既有 `apply_qc_decision` 同事务边界内
   顺序执行；失败只影响对应 (agent, role)，本体 status 流转不变）；
5. profile/判据版本变更后旧行对新键不可见（键化隔离，无需改写旧行）；
6. 生产派单资格读取统一走 §5 门禁；verifier 生产派单在最终 dispatch 前再查
   （§5.3），阻断排队后 revoked；
7. 播种报告拆 `agent_status`（agents 表现值）与 `role_qualification`（资格
   effective 状态）——考试计算值 `qc.status` 保留但不再冒充数据库本体状态。

### 3.3 乱序写守卫

`record_qualification` 拒绝**旧结果覆盖新资格**：若同键存在 effective `passed`
行且其 `started_at` 严格晚于新行 `started_at` → `ValueError`（并发/重放场景
下旧考试轮不得降级新资格；同刻按 `id` 序）。`supersede`：写终态行
（passed/failed/stale/revoked）时，同键 `status ∈ {pending, passed}` 且未被
替代的旧行一律置 `superseded_by=新行 id`。

### 3.4 写口

唯一写口 = `qswarm/exams/roster.py`（`record_qualification` /
`revoke_qualification` / `migrate_qualifications` 三函数；写口审计白名单
SPEC-2 §6.1 本日扩展行）。生产只读（`kernel/qualification.py`，零 SQL 写）。

## 4. 迁移与回退（方案 §3.3 逐字）

### 4.1 判定规则

历史 agents×hats 逐对判定：**无法确认 profile（历史行无 profile 记录）、完整
考试证据与适用判据者 → `stale`（reason=`legacy_unverifiable`）**。P0 迁移
判定器恒判 stale（不通过正 cells 或既有帽反推合格）；`profile_id` 取当前
配置计算的现值（标记"现配置下已知未证"），`exam_run_id=NULL`。

### 4.2 manifest

`migrate_qualifications(ledger, *, dry_run, params)` 返回：

```json
{
  "dry_run": true, "policy_version": "qp-v1",
  "pairs_total": N, "stale_written": n1, "already_current": n2,
  "rows": [{"agent_id", "role", "profile_id", "status": "stale",
             "reason": "legacy_unverifiable", "action": "would_write|written|skip"}]
}
```

`dry_run=True`（缺省）零写只出清单；`--apply` 才写。逐角色启用计划
（verifier 先行）由 `params.qualification.enforce_roles` 承载。

### 4.3 对账

manifest 逐行附 `profile_temp_anchor`（qualification 节锚）与
`supply_temp`（models.yaml runtime 值，可注入）——两源不等时 manifest 顶层
`temp_anchor_mismatch: true` 如实披露，不静默。

### 4.4 回退

回退只切换路由策略/关 `enforce` 开关；资格行与原始工件保留。安全门不允许
宽松回退（`enforce=True` 时缺记录即拒派，无缺省回退路径）。

## 5. 派单资格门（读侧）

### 5.1 门禁对象

`kernel/qualification.py`（只读）：

- `profile_id(model_ref, role, *, prompt_hash=None, production_temp=…)`；
- `current_prompt_hash(role)`；
- `QualificationGate`（policy_version + enforce_roles + production_temp）：
  - `filter_pairs(ledger, role, pairs)` → `(合格, 过滤数)`；
  - `status_of(ledger, agent_id, role)` → effective 行摘要或 None；
  - `recheck(ledger, agent_id, role)` → bool（dispatch 前复验）；
- `gate_from_params(params)`：`enforce=False` 或 role ∉ enforce_roles →
  `None`（调用方零行为差，M5 逐位兼容）。

### 5.2 蜂池统一过滤

`consult._active_by_role / active_auditors / active_reviewers /
active_verifiers` 与 `loop.active_scouts` 增可选 `gate=None` 参；生产入口
（`run_consult` 三池 / `run_verify` / `run_scan`）传 `gate_from_params(params)`。
被过滤数如实入报告（verify：`verifiers_pool` / `verifiers_qualified`；
consult：`gate_filtered`）。

### 5.3 dispatch 前复验（P0 范围 = verifier 生产路径）

`run_verify._verify_one` 在 production 派单（create_task 前）对选定 verifier
执行 `gate.recheck`——不复验通过 ⇒ 该簇计 `gate_revoked_midrun` 跳过
（fail-closed，零调用零写）。面板/预裁决池的中途复验随 enforce_roles 扩展
（P1）；P0 默认 enforce_roles=("verifier",)（先对 verifier 强制生效）。

### 5.4 配置

`params.qualification`（SPEC-1 §5.2 扩展节；缺节回落缺省 = 关闭 = 黄金兼容）：
`enforce: bool = False`、`enforce_roles: ["verifier"]`、
`policy_version: "qp-v1"`、`production_temp: 0.2`。
**启用后缺记录即拒派，不允许缺配置自动回退到漏洞模式**（方案 §8 安全门例外）。

## 6. oracle 分级与判据（D46 锚点；P1 v0.2 精度面——`qswarm/sandbox/oracle.py`）

**判据版本** `ORACLE_VERSION = "oc-v1"`（判据公式/冻结断言名变更须升版）。

**OracleKind 词表**（D46 四层级 + ① 的无配对降格形态）：

| kind | 层级 | 裁判 | 语义 |
|---|---|---|---|
| `execution_paired` | ① | 观察器（宿主侧机械判读） | 漏洞版/修复版同驱动成对对照 |
| `upstream_truth` | ② | 上游补丁/题库答案键 | Juliet/CVE 修复提交标签 |
| `cross_family_llm` | ③ | 跨族强 LLM（**禁同族**红线） | 预标注/盲审兜底（P1 未启用，D47-A） |
| `blind_mechanical` | ④ | 盲卷 + 机械证据 | 实验臂间判读（E1+） |
| `mechanical_single` | ①降格 | 冻结模板 harness 断言 | 内核 verify 单次执行面（H4） |

**执行式判据（①，成对）**：`validate_pair(vuln, fix)`——漏洞版违规副作用 ∧
修复版不违规 ∧ 修复版执行健康 → `supported`；修复版超时/执行失败 →
`inconclusive(fix_control_invalid)`（**修复版环境失败不算负对照通过**，§6.2
逐字）；双版皆违规 → `inconclusive(poc_not_discriminating)`；漏洞版无违规 →
`inconclusive(violation_not_observed)`——第 ⑨ 类「真实漏洞但 PoC 无效」必须
inconclusive，**绝不 verified_fp**。**refuted 不出自执行判据**（须独立
blocking 证据覆盖输入/环境/权限/可达性前提——内核 §10.4 面）。

**观察器分离**（§6.2 逐字落地）：判据不由被验方自定——冻结驱动脚本
（`build_fixture_driver`）只驱动目标并**原样记录**（金丝雀目录由驱动创建、
目标输出落盘）；机械判定在宿主侧 `observe_injection`（金丝雀存在 ∧ 内容 =
nonce，由目标进程内执行的注入命令产生）/ `observe_traversal`（目标输出含
nonce——内容只在允许根外金丝雀文件里，回显路径不可能命中）。**ASSERT 自报
行仅留档（`ExecutionStatus.assertions`），永不参与判定**——无条件 PASS（第
⑦ 类）结构性不可信。生成者与验证判据不由同一响应自定：模型只可能替换驱动
面，判读面永远在冻结 observe_*。

> **R1 修订（2026-10-28，docs/R1_CHANGE_NOTE.md §1-T2；不倒填）**：本节
> 「不改 M5 三分类 outcome 行为」的承诺自 R1 起显式修订——可信判据开始
> **控制生产结论**：verified_real/verified_fp 提交须携带内核铸造授权
> （`kernel/trust.py`，policy r1-tv1；仅 execution_paired + oc-v2 健康分立
> 对照可铸造）；无授权的高可信主张由编排规范化为 verify_error
> （reason=no_trusted_authorization，零 L3 零 S2 存款），蜂产 PoC 自报与
> mechanical_single 单次支持**不构成**成对独立验证。S2 派生补偿
> （propagate_verdict/propagate_pending）与提交同一 eligibility；旧事件
> （无 authorized 字段）恒不补发。oracle 判据升 **oc-v2**（§6.4 健康分立 +
> §6.5 观察器宿主侧边界）。证据投影升 **evidence_v3/capability_v3**
> （population 分立 precision / direction=0 忽略 / as_of 事件富化——v2
> 工件为已勘误历史口径）。

**内核单次执行面（mechanical_single，H4）**：`assess_single` 判据只认冻结断言
名白名单（`VIOLATION_ASSERTIONS`：injection→`injection_payload_executed`；
authz/config_dep→`path_traversal_read`）+ 对照门（`CONTROL_ASSERTIONS`：
traversal 族 `legal_control` FAIL → 不 supported，只降不升）。模板路径
（poc_source="template"）白名单断言 PASS ∧ 对照过 → supported；蜂产 PoC
（"bee"）断言由蜂脚本自报、非独立判据 → 如实 inconclusive
（judge_not_independent_self_reported）——**不改 M5 三分类 outcome 行为**
（双层结果，方案 §6.1：内部报告增 `claim_validation`/`execution_status`/
`oracle_kind`/`oracle_version`/`control_results`，落 process 档 result.json
`oracle` 节 + VerifyRunReport `claim_validation` 计数与 detail 行）。

**九类验收集**（§6.3 清单逐字；`qswarm/sandbox/fixtures/`，两族×九类）：
①漏洞版 ②修复版 ③仅回显安全版 ④不可达 sink ⑤缺依赖 ⑥超时 ⑦无条件 PASS
⑧目标未调用 ⑨真实漏洞但 PoC 无效。期望表（`NINE_CLASSES`）：①+②成对 →
supported；③④⑤⑥⑦⑧⑨ violation=False 且不入可信面（⑨ 必须 inconclusive）。
**P1 开工前实证仪器缺陷（R1）**：旧模板 echo-marker 断言可被 ③击穿——模板
v2 升级为副作用金丝雀判据（SPEC-9 §3 增补），M5 试跑 match 2/2 的口径据此
重述（历史工件不改）。

**E0 校准门（D46；D47-A 状态）**：负责人人工判 20–30 例量跨族裁判一致率，
阈值 pilot 前冻结——**题目已冻结**（`experiments/design_revision/manifests/
e0_calibration_set.jsonl`，25 项 = 夹具 18 + 题库分层 7）；跨族主裁判选型与
调用**待批**（E1 开工前另行批；GLM 族与施工 AI 本体禁任主裁判/盲审）。

**container 档边界（如实）**：观察目录经 stdout `OBS|` 行回传依赖 process 档
与宿主共享文件系统；container 档跨 ns 观察面（独立挂载/属主归因）随生产面
另行设计。九类夹具 = 人工审核受控合成件，process 档执行（§6.3 许可）；模型
生成 PoC 与不可信仓库首次执行即进受限 container（SPEC-9 红线不变）。

## 7. 证据投影（工作包 B；P1 v0.2 精度面——`qswarm/kernel/evidence.py`）

`evidence_v2` 只读文件投影（零 SQL 写、零新表、零新 LedgerKind）：判定观察源
= 账本 `verify_result` 事件（seq 序可重放）；真值源 = `human_verdict` 事件
（`available_at`=事件 ts；晚于 as_of 不采——时间可见性）；富化 = findings/
clusters 表只读 SELECT（键不上 → unknown）。

**字段**（方案 §4.2 表逐字）：`observation_id` / `logical_evidence_id`
（`lec-prov:<cluster>`——provisional，不自动当跨运行真值键）/ `supersedes` /
`root_cause_group_id` / `repo_commit` / `execution_profile_id` / `role` /
`family` / `claim` / `decision` / `truth_label` / `truth_source` /
`oracle_version` / `adjudication_ref` / `eligible_set` /
`selection_probability` / `selection_reason` / `occurred_at` / `available_at`
/ `correlation_group` / `cost` / `latency` / `failure_kind`。**存量事件缺
记录字段如实 unknown**（execution_profile_id / eligible_set / selection_
probability / oracle_version——选择面无记录则加权估计不可做，分层结果照报）。

**纠错规则**（§4.3）：同逻辑事实后判定 supersedes 前判定（append-only 全留）；
有效视图（`effective_view`）只保留当前适用判断；**独立根因数**
（distinct logical_evidence_id）在重复投票/重复 finding/纠错下不变；两次同级
人工判定冲突 → disputed（truth 归 U，不默认最后写入者胜出）。

**能力汇总**（`capability_v2.json`，不参与派单）：按 family 出 TP/FP/U、
`labeled_precision = TP/(TP+FP)`、全量上报 precision 未知标签界限
`[TP/(TP+FP+U), (TP+U)/(TP+FP+U)]`（分母 0 → not_estimable；界限是未知标签
范围**非统计置信区间**）；disputed 归 U；移入 unknown 不形成上线优势。

**验收**（§4.5）：同 as_of 同事件输入 → 同投影哈希（canonical json sha256）；
未来真值不进历史投影；旧 cells 与 M0 黄金零触碰（legacy_v1 语义保留）。

## 8. 漏斗 trace 与 S2 判据（工作包 C/D；P1 v0.2 精度面——`qswarm/kernel/funnel.py` + oracle 接线）

**七层**（§5.1 逐字）：L0_uncovered / L1_discarded / L2_unreported /
review_rejected / budget_not_run / s2_error / confirmed_reported。每层记录
in/out 计数、原因分解、`per_item_available`（存量报告为总量面——**如实
标注不伪造逐条**）、抽样概率（存量 unknown）、工件引用。装配源 = 既有
scan/consult/verify 报告 dict + 只读账本（评审/S2/确认层直查事件与状态）。

**金标准回查**（§5.2 路径 1）：`first_loss_layer(known_items, ledger)` 按项
定位首次漏失层——可判层（finding 建立/评审/S2/确认）走账本；L0/L1 逐条存量
不可得 → `not_assessable`（无 finding 项记「首次漏失在 L2 或更早」，不伪造
定位）。线上/影子随机审计（§5.2 路径 2，10% 预算预留）属 E1+ pilot，P1 不
实施。

**S2 双层结果**（§6.1）：外部 `VerifyOutcome` 三分类**不变**；内部报告增判
层面（§6 的 `claim_validation` 面）——「能够区分脚本失败 / 主张不成立 / 无
结论」的承载：脚本失败 = execution_failed/timeout（reason 面）、主张不成立
= refuted（仅 §10.4 blocking 路径）、无结论 = inconclusive（含 ⑨ 硬类）。

## 9. 实验协议（工作包 E；P1 v0.2 落地面——`experiments/design_revision/`）

manifest 冻结面（方案 §7.1）模板落 `manifests/e0_manifest_template.json`
（数据哈希/分组/开发-验证-保留集/profile 哈希/paper manifest/预算/采样/重试/
停止规则/主要指标/最小效应/统计/种子/版本/联网审批 + 校准面协议节）。
**E0 确定性判据 runner** = `run_e0_checks.py`（五组：C1 九类负对照真机 /
C2 模板回归 / C3 指标分母 / C4 投影确定性与纠错 / C5 漏斗装配），全过 =
P1 退出条件「E0 通过」的确定性面；报告落 `out/design-revision/e0-<ts>/`。

**预算登记**（D46 冻结值）：硬上限 ¥500 / 4 周墙钟，逐笔登记
`budget/budget_ledger.jsonl`；**起算锚 = 首次获批线上调用日**（D48 建议值
执行、待追认）——P1 离线批零线上调用零消耗。

正式门槛（方案 §7.6）：路由上线 = 质量提升 ≥5pp 且区间下界>0 + precision
护栏（假警报容忍 15/100 起锚，正式非劣界 pilot 方差后冻结）；或质量非劣 +
成本降 ≥20%。**禁止以"测试通过/能跑全链/一次过线"替代效果门槛。**

## 10. 验收判据（P0 卷）

1. 未考帽（无行）、失败帽（failed）、中断帽（pending）、旧 profile passed
   （profile 失配）、排队后 revoked——均不入合格池、不复验通过、零模型调用
   零 PoC 零 verdict 存款；
2. 合格帽（effective passed）正常入池执行；
3. 某角色资格失败不改变其他角色池（per-role 隔离）；
4. 乱序写守卫：旧考试轮不得覆盖新 passed；
5. supersede：同键重考后旧 passed 不再 effective；
6. 迁移 dry-run 零写、manifest 全 stale(reason=legacy_unverifiable)；apply
   后门禁全拒；
7. `enforce` 缺省 False：全部既有测试零改动通过（M5 逐位兼容）；
8. profile_id 确定性与敏感性（同入同出、任一分量变则变）；
9. 播种报告含 `agent_status` 与 `role_qualification` 分列；
10. 写口审计 --strict PASS（role_qualifications 唯一写口 = exams/roster.py）。

## 11. 决策记录

| 日期 | 决策 | 理由 |
|---|---|---|
| 2026-10-22 | P0 迁移判定器恒判 stale（不尝试从 exam_results 反推 passed） | 方案 §3.3「无法证明者为 stale」；历史行无 profile 记录，反推即伪造 |
| 2026-10-22 | 采样温度锚单源 = qualification.production_temp，不读 models.yaml 运行时值 | 资格判据面与运行供给面解耦；两源偏差由 manifest 对账暴露（fail-closed 方向） |
| 2026-10-22 | dispatch 前复验 P0 只落 verifier 生产路径 | 先对 verifier 强制生效（方案 §3.3）；面板池中途复验随 enforce_roles 扩展 |
| 2026-10-22 | 不新增 LedgerKind（资格审计复用 exam_result 事件 + 投影表） | append-only 词表冻结；投影表可随时重建（§3.4），事件溯源已足 |
| 2026-10-24 | P1 判层不扩公共枚举：ClaimValidation/OracleKind 为 sandbox.oracle 模块内类型 | 方案 §6.1「不急着扩展公共枚举」；外部三分类语义冻结 |
| 2026-10-24 | ASSERT 自报行永不参与 oracle 判定（仅留档） | §6.2「生成者与验证判据不能完全由同一响应自定」；⑦ 类结构性不可信 |
| 2026-10-24 | refuted 不出自执行判据（仅 §10.4 blocking 路径） | §6.1：仅某次 PoC 未触发/路径不可达不足以否定原漏洞 |
| 2026-10-24 | 蜂产 PoC 执行绿 → claim=inconclusive（judge_not_independent），outcome 行为不变 | M5 reconcile 语义 D40/D43 已拍板；双层结果只加报告面不改裁定，学习标签面按本卷 §7 记录限制 |
| 2026-10-24 | 投影存量字段如实 unknown（选择面/身份面/oracle_version 不猜测补齐） | §4.3「历史数据不能恢复可靠逻辑键仅作 legacy 描述」；§5.2 加权估计前提不满足 |
| 2026-10-24 | E0 校准题目随 P1 冻结，跨族裁判供给与调用推迟 E1 前另行批 | D47 建议值 A：题目须先过确定性仪器检查；供给变更避免一轮混批 |
| 2026-10-24 | 预算起算锚 = 首次获批线上调用日（D48 建议值执行、待追认） | 离线实现与确定性判据不烧墙钟不烧钱；4 周窗覆盖真正实验消耗期 |

## 12. R2 实施修订（2026-10-29；先于生产接线的严格核验）

负责人本轮明确授权按项目审查结论实施，范围见 [R2 计划](<../R2_IMPLEMENTATION_PLAN_20261029.md>)。本节覆盖此前「只加报告」「只验证工件索引行」的旧行为；不修改历史事件，亦不宣称生产成对观察器已完成。

### 12.1 高可信授权与补偿

- 新授权策略为 `r2-tv1`。新提交/新增奖励不接受旧 `r1-tv1` 或仅 `authorized=True`；旧事件继续允许只读历史聚合，不重新铸造或补发。
- `VerificationContext` 由受信宿主冻结：真实 run/task/assignee、完整实际成员集、profile/repo/可知 commit、实际 snapshot/target/poc/修复 pair 工件引用及 SHA-256、执行策略。未知 commit 记 unknown。context 写入任务 shard 后不可换绑；不得从待验 proof 反建预期上下文。
- 所有 proof、evidence 及上下文工件走 `artifact_get_verified`，验证严格内容寻址、索引大小、普通文件、无跟链路径和有界读取；只在 artifacts 表有一行不够。
- 正规 mint 通过 context、版本白名单、实际配对健康/违规值与观察工件一致性后，冻结任务发行锚（proof 引用与摘要）。独立调用 persist 或重哈希 JSON 不构成新授权。此为受信宿主进程内契约，不是签名系统，不能抵抗任意宿主/数据库写权限。
- `submit` 与 S2 直接派生/补偿显式传 `store_dir`，无库路径不得降级为索引检查。VERIFY_RESULT additive 记录 `task_id/context_digest`；S2 复核源事件与上下文的 run/任务/簇/outcome/方向。S3 只接受真实同簇同向 HUMAN_VERDICT，不能更换 tier 绕过授权。
- 生产 verified_fp 无独立反证协议时继续拒绝；模型 blocking 不足以授权。离线机制 fixture 的负判仅允许精确冻结安全目标字节、fixture 策略/观察器和完整独立 refutation 工件，不代表生产误报验证能力。
- `independent-container-v1` 是保留给真实宿主独立采集接线的版本；白名单存在不等于实现完成。`trusted-fixture-v1` 仅是明确离线夹具机械验收。

### 12.2 历史漏斗与未知信息

- `as_of_seq` 只消费截止内事件，不读当前 findings 状态或身份补历史。历史 FINDING 事件缺 path/family 时逐项定位返回 `not_assessable/unknown`；外部报告无序号谱系时由调用者选择相应历史工件。
- 当前逐项查找必须按给定 family+path 匹配；未给 family 且路径跨多家族时不可评估。
- `candidates_total-l2_dispatched` 只表示未派单缺口，不能当 scout 不疑；原因缺记录就 unknown。E0 C5 同步检验此纠错，不保留旧错误归因来凑全绿。

### 12.3 运行资格与实验边界

库级缺省仍为历史兼容的 enforce=False；新增 CLI 生产入口对四个执行角色强制资格检查，不自动造蜂或降 QC。人工校准、跨族裁判与同预算效果仍须独立证据；工程测试通过不能替代实验结果。
