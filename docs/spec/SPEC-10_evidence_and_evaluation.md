# SPEC-10 证据与评测体系（P0：角色资格与配置身份）

> 版本 v0.1（2026-10-22，P0 轮）。依据：`docs/DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md`
> （下称「方案」，§3 工作包 A / §4–§7 后续工作包）与 DECISIONS 2026-10-22 D46 行
> （负责人拍板：LLM 辅助 oracle 层级 / 三参数冻结 / P0 放行）。
> 本卷 P0 精度面 = §1–§5（角色资格与配置身份 + oracle 分级锚点）；§6–§9 为
> P1+ 工作包的契约骨架（字段面冻结、公式随各阶段开工前细化升版）。
> 与现行 SPEC 的关系：不覆盖、不重解释（方案 §9）；SPEC-1 §1/§2 已随本卷同步
> 扩展（QualificationStatus 枚举 + role_qualifications 表）。

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

## 6. oracle 分级与校准门（D46 锚点；P1 实施精度）

四层级：① 执行式 oracle（漏洞版/修复版同 PoC 对照，裁判=沙箱执行）② 上游
真值（Juliet/CVE 修复提交）③ 跨族强 LLM 兜底（**禁同族**：现编制为 GLM 单族，
glm-5.3 与施工 AI 均不得任主裁判/盲审；臂含同族模型 → 对照作废或披露）④
盲卷 + 机械证据。E0 校准门：负责人人工判 20–30 例量跨族裁判一致率，阈值
pilot 前冻结。证据结构必填 `truth_source` / `oracle_version`（方案 §4.2 表）。

## 7. 证据投影（工作包 B 骨架；P1 开工前细化）

`evidence_v2` 只读文件投影：`observation_id` / `logical_evidence_id` /
`supersedes` / `occurred_at` / `available_at` / `eligible_set` /
`selection_probability` 等（方案 §4.2 表逐字）。验收：同 as_of 同投影哈希；
重复/纠错不增加独立根因数。旧 cells 与 M0 黄金零触碰（legacy_v1 语义保留）。

## 8. 漏斗 trace 与 S2 判据（工作包 C/D 骨架）

C：分阶段漏斗损失记录（L0 未覆盖/L1 丢弃/L2 未报告/评审拒绝/预算未执行/S2
error/确认上报）+ 10% 随机审计预算锁定。D：`execution_status` /
`claim_validation` / `oracle_kind` 内部双层结果；九类验收集（含漏洞版/修复版
负对照；"真实漏洞但 PoC 无效"必须 inconclusive）；marker/exit 0/模型自述
PASS 皆非充分条件。公式与判据随 P1 开工前升版本卷。

## 9. 实验协议（工作包 E 骨架）

manifest 冻结面（方案 §7.1）：数据哈希/分组切分/开发-验证-保留集/profile 与
prompt 哈希/paper manifest/预算与停止规则/主要指标/最小效应/统计方案/种子/
版本/联网审批。正式门槛（方案 §7.6）：路由上线 = 质量提升 ≥5pp 且区间下界>0
+ precision 护栏（假警报容忍 15/100 起锚，正式非劣界 pilot 方差后冻结）；
或质量非劣 + 成本降 ≥20%。**禁止以"754 测试通过/能跑全链/一次过线"替代效果
门槛。** 预算硬上限 ¥500 / 4 周墙钟（D46 冻结；超限停等再批）。

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
