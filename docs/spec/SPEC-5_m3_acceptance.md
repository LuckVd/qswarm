# SPEC-5 M3 验收卷（感知层 L0 v2 + L1 侦察路由 v1）

> 依据：SPEC-7_perception.md（草案 v0.1）；格式沿 M1/M2 卷。判定总则同 M2 卷 §0：
> 每条判据 = 测试文件 + 最低用例名集（或脚本 + 断言），机器可检；人只审门禁结论。
> 状态：随 SPEC-7 拍板升 v1.0。

## 0. 判定总则

- 判据分五组：A 功能（模块行为）/ B 不变量（纪律与写口）/ C 可复现与等价 / D 端到端 smoke / E 补播与 QC；
- 每组全绿才算过组；全组过 = M3 门禁过；
- 既有 611 例零回归是全程前置（任何分组开始前先跑全量）。

## 1. A 组：功能验收

### 1.1 perception.candidates（SPEC-7 §1.1/§1.5）
- `test_candidate_merge_overlapping`：span 重叠 ≤5 行同 kind 合并，channels 并集、priority 递增、span 并集；
- `test_candidate_order_deterministic`：乱序输入两跑产出 `(path, span)` 字典序一致；
- `test_candidate_id_content_addressed`：同内容同 id、内容变 id 变。

### 1.2 perception.channels（SPEC-7 §1.2–1.4）
- `test_channel_a_tier1_callpoints`：tier-1 语言样例收出 sink/source 调用点（clues 表命中）；
- `test_channel_a_no_grammar_absent`：无 grammar 语言通道 A 缺席且计数 `channel_a_absent`，不抛错；
- `test_channel_b_scout_identity`：`l0scan:` 任务走 temp 池派单、候选工件记 agent_id、账本零 deposit（D26）；
- `test_channel_b_schema_fail_retry_once`：schema 畸形重试 1 次后分片通道 B 缺席计数；
- `test_channel_c_mandatory_gate`（D30）：semgrep 装通 + tier-1 语言样例实跑出 rule_hit；
- `test_channel_c_runtime_timeout_absent`：运行期超时 → `rule_channel_skipped` 计数，漏斗不停摆。

### 1.3 perception.indexer（SPEC-7 §1.2）
- `test_index_content_hash_skip`：文件未变走缓存（解析计数不变），内容变重析；
- `test_index_incremental_diff_mode`：diff 档只析变更文件 ± 1 跳邻域。

### 1.4 perception.scan（SPEC-7 §1.6）
- `test_scan_fast_topk`：fast 档按 priority 降序取 Top-3；
- `test_scan_pr_neighborhood`：pr 档候选 = 变更文件 ± 1 跳；
- `test_scan_full_shards_by_dir`：full 档目录级分片，树字典序、大小稳定。

### 1.5 kernel.loop + L1 接线（SPEC-7 §2/§3）
- `test_loop_l0_to_l1_shard_contract`：候选分片 → DispatchContract(role=scout) 装配字段逐项断言；
- `test_loop_scout_least_loaded`：temp 池 least-loaded 分派、过载剔除；
- `test_loop_deposit_x02`：L1 verdict 存款权重 ×0.2 落账（fast 档 reporter=侦察蜂）；
- `test_loop_promote_scan_shadow_inert`：挂接后状态账有扫描痕、ENTRY_TOP_FRAC=0 下零晋升事件。

### 1.6 exams 补播面（SPEC-7 §4）
- `test_seeding_roles_flag`：`--roles reviewer,verifier` 生效；缺省仍 (scout, auditor) 零扰动；
- `test_judge_reviewer`：ENDORSE/REJECT × real/fp 四象限 + 家族对齐口径 + parsed=None 判错；
- `test_judge_verifier`：CONFIRMED/REFUTED/INCONCLUSIVE 三值（弃权恒判错）。

## 2. B 组：不变量验收

- `test_write_ports_audit_m3`：写口审计扩展——perception 只写工件库/缓存，零账本直写（M2 卷 §2.1 模式）；
- `test_purity_perception`：clues/归并/模板渲染零 IO 零随机（import 钩子断言，M2 卷 §2.3 模式）；
- `test_network_single_port`：L0-B 与补播调用全经 UrllibAdapter（socket 钩子断言）；
- `test_params_extra_forbid`：`perception` 节新键通过 contracts/config 校验，越键拒绝。

## 3. C 组：可复现与等价

- `test_scan_replay_bitwise`：同仓库快照两跑候选集工件逐位一致；
- `test_bootstrap_import`：空库导入 4 active + 20 考试存款，事件 payload 逐字等值、`src_seq` 溯源在位，route() 据此可选出非均匀目标（D28）；
- `test_bootstrap_idempotent`：二次导入零重复行零重复存款；
- `test_route_untouched`：route/route_thompson/promote_scan 与 M1 黄金逐位回归（黄金文件不改）。

## 4. D 组：端到端 smoke（FakeAdapter，不出网）

- `test_e2e_fast_lane`：种子小仓 → L0 候选 → L1 初筛 → 存款落账 → 扫描报告（计数：channels 缺席/schema_fail/候选数）全链绿；
- `test_e2e_idempotent_rerun`：同仓库重跑零新候选、账本零重复存款。

## 5. E 组：补播执行（可选在线，负责人放行时）

- reviewer/verifier 在线入职考一轮（智谱四蜂同纪律：新 DB、缓存修剪、S9 重放逐位）；
- QC ≥ 0.8 → active；未达线 → 该档蜂保持 provisioning，M3 门禁不因此阻塞（E 组判据 = 「考试执行与判分链路正确」，非「必须及格」）。

## 6. 门禁结论形态

`out/_m3_final/M3_DELIVERY_REPORT.md`：分组判定表 + 新旋钮回填清单 + D25–D30 落位记录 + 已知限制（语言覆盖实测、semgrep 可用性）。
