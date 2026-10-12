# SPEC-5 M1 验收测试与门禁清单（机器可检）

> 来源：SPEC-2 §0–§9（唯一机制输入）；设计文档 §16.6（每步验收机器可检、人只审里程碑）与 §16.7-8；SPEC-4 §5（门禁四项模式）；SPEC-1 §6（写口/重放/幂等不变量）；父任务合同 ac_mv1s39t26l1to9he…ac_mv1s39t2luneoms3（M0 四项技术债、重放等价 3 种子×≥3 检查点、矩阵复跑并发 3、无网络/无新增依赖约束）。
> 读者：AI 编码代理与验收执行者。每条验收 = **【判据】可执行命令或 pytest 用例名 + 【期望】精确输出**；判据通过 = 命令退出码 0 且输出匹配期望模式。发现本文件与 SPEC-2 矛盾，以 SPEC-2 为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 基线快照（2026-10-10，SPEC-5 导出时点，M0 终态）：全量 `python -m pytest` → **338 passed, 1 skipped**（`--collect-only` 339 项；唯一 skip = 技术债③ tests/test_m0_final_knobs.py 的 ENTRY_DIRECT 边界格）；黄金回归集 34 例（route 9 / route_thompson 9 / promote_scan 8 / agg_cells 8，manifest seed=7）；官方矩阵 `results/experiments-full` n_units=n_rows=507、162 组全达标（out/group_targets.csv qualifies 全真）。已知基线红：黄金溯源再生成 `freeze_golden.py --check` 失败（manifest 钉 v2 前旧参数 vs 当前 params.yaml v2，promote-006 transitions 21≠20）——处置见 §4.1，不属 M1 门禁除非触发 sanctioned 重冻。
> 状态：**定稿 v1.0（2026-10-10，规格门歧义拍板）**——本轮定稿修订：§5.4 命令补 `--workers 3`（D25，tune_round 缺省 workers=4 与 §7.3 冲突）、§9.3 免审直跑执行记录（D26）、新增 §11 决策记录（五字段，已逐条同步 `docs/spec/DECISIONS.md`）；SPEC-2 同日定稿（其 D19–D24 见该文件 §10.1）。实现期发现的进一步未定细节按滚动协议记 `DECISIONS.md`。

---

## 0. 判定总则

- **命令口径**：pytest 统一 `python -m pytest <路径> -q`（pyproject `addopts=-q`，全量直接 `python -m pytest`）；工作目录 = 仓根 `/data/Qswarm`；exit 0 = 通过。
- **期望输出形态**：pytest 期望 `N passed`（不得出现 `failed`/`error`；门禁终态另要求 `0 skipped`）；比对类脚本期望文中所列字面行（如 `ALL_EQUAL=true`）。
- **位级纪律**：凡标注「逐位/逐字段/逐字节」的判据一律 `==` 全等，无容差、无近似（SPEC-4 §6、kernel.replay `diff_cells` 同一纪律）。
- **期望计数**：文中给出的 N（用例数、行数、组数）为导出时点基线；实现新增测试使 N 只增不减，「≥ 基线」即为判据。
- **证据留痕**：所有判据命令的输出摘录进交付报告；矩阵/重放级证据按节落盘 `results/`。
- **两 SPEC 冻结约束**（ac_mv1s39t26l1to9he）：SPEC-2 与本文件定稿落盘之前，`qswarm/` 与 `tests/` 不得出现任何 M1 新增或修改代码（含技术债修复）。机器可检验证：交付报告附两 SPEC 的落盘时刻戳与最早 M1 变更文件的 mtime 对照（`find qswarm tests -name '*.py' -newer <SPEC 路径>` 的反向佐证）；无 git 仓库，以文件时间为准。

---

## 1. A 组：M1 四块功能验收（判据 = 测试文件 + 最低用例名集）

四块机制 ↔ SPEC-2 章节：聚类归并 §1、委托契约与派单状态机 §2、re_route 与猜测格弱存款 §3、配额与预算熔断 §4；并发纪律 §7 为横切（§1.5）。测试文件为 M1 新增；下列用例名为**门禁最低集**（实现可增不可缺）。

### 1.1 聚类归并 `kernel/cluster.py`（SPEC-2 §1）

【判据】`python -m pytest tests/test_kernel_cluster.py -q`
【期望】全 passed；且 `--collect-only` 含以下最低用例名：

| 用例名 | 钉住的判据（SPEC-2 出处） |
|---|---|
| `test_line_overlap_intersection_over_min` | 交集/min；子窗含于大窗 = 1.0；空窗 / start>end = 0.0（§1.2/D1） |
| `test_coarse_mergeable_requires_path_lang_cwe_overlap` | 同 path ∧ 同 lang ∧ CWE 相等 ∧ 重叠 ≥ 0.5；CWE 双 None 退 (family,type)、恰一侧 None → False（§1.2/D3） |
| `test_fine_mergeable_never_merges_without_sim_fn` | sim_fn=None 恒 False；阈值 0.9 含等号边界（§1.2/D4） |
| `test_cluster_findings_all_pairwise_no_transitive_closure` | A~B~C 而 A≁C 不并 C（§1.2/D2） |
| `test_cluster_findings_deterministic_order_first_fit` | 候选序 (path,line_start,line_end,finding_id) 字典序；一 finding 至多一提案；assigned 已归属不参与（§1.2） |
| `test_apply_merges_cluster_id_and_event_same_transaction` | cluster_id=`C-{YYYYMMDD}-{seq:06d}`；clusters/cluster_members 行 + cluster_merge 事件同事务；members=本次并入集（§1.3/D5） |
| `test_apply_merges_idempotent_zero_events` | 幂等键 (level, frozenset(members)) 已存在 → 零新事件返回 []（§1.3/§6.4） |
| `test_split_cluster_zero_deposit_and_verdict_reset` | 拆分零存款、verdict_* 投影清空、groups<2 或并集≠全集 ValueError、簇不存在/已拆分 ValueError（§1.3） |
| `test_propagate_verdict_expansion_order_and_weights` | 展开序=成员(reporter_id 字典序)→侦察→评审→验证；scout×SCOUT_X、评审同向+/反向−×REVIEWER_X、S3 验证行 VERIFIER_CONFIRM/CONFLICT；每笔携带 src_seq（§1.4/§5.2/D7） |
| `test_propagate_verdict_idempotent_by_cluster_tier_src_seq` | 同 (cluster,tier,src_seq) 重复 → 零新事件（§1.4/§6.4） |
| `test_propagate_verdict_conflict_appends_not_rolls_back` | S3 与 S2/S1 冲突补记更强反向存款，不回滚（§1.4/§6.2） |
| `test_propagate_verdict_zero_direction_no_deposit` | direction=0 / verify_error → 零存款零投影更新（§1.4） |
| `test_propagate_pending_repairs_crash_window` | 「源事件已落、派生存款未落」构造场景逐个补齐；lineage→None 的簇入 skipped 不报错（§1.4/§6.3） |
| `test_apply_human_verdict_three_way_and_terminal_guard` | +1 全员 CONFIRMED、−1 REJECTED、0=落事件零存款零状态；终态再裁 ValueError（§1.4/§12） |
| `test_aggregate_votes_majority_tie_disagreement` | 方向=符号和多数、平票 0、confidence=多数票四维均值之平均、反向票 → disagreement=True（§1.5/D22） |
| `test_close_panel_state_s1_propagation_and_guards` | open→voted；confidence/disagreement_json 写入；direction≠0 → S1 存款（S1_SOURCE_W×角色系数）；=0 零存款；票数不足 ValueError；已 voted 零事件（§1.5） |

### 1.2 委托契约与派单状态机 `kernel/dispatch.py`（SPEC-2 §2）

【判据】`python -m pytest tests/test_kernel_dispatch.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_role_literal_tables` | ROLE_OUTPUT_SCHEMA / ROLE_TOOL_BOUNDS / ROLE_TASK_BOUNDS / FINDING_TRANSITIONS 逐字表（§2.2/§2.4） |
| `test_hop_budget_by_mode_and_unknown_raises` | fast=0/pr=1/full=2；未知档 ValueError（§2.2） |
| `test_build_contract_four_pieces_pure` | ①goal ②output_schema_ref ③tool_bounds ④task_bounds + hop_budget + taxonomy_node 直出；纯函数零 IO（§2.2/§4.3） |
| `test_dispatch_guard_rejects_non_queued` | tasks.state≠queued → ValueError（§2.3/§6.4） |
| `test_dispatch_empty_pool_audits_stays_queued` | assignee=null 的 DISPATCH 事件落账、tasks 留 queued、attempts 不消耗（§2.3/D10） |
| `test_re_dispatch_attempts_limit_then_failed` | attempts<2 重派（attempts+1、state=dispatched）+ RE_DISPATCH 事件；≥2 → failed 且 ValueError、零存款（§2.3/§16.7-8） |
| `test_finding_transitions_table_and_terminal_guard` | §10.2 迁移表逐字 + D8 直达边；非法迁移/终态流转 ValueError（§2.4） |
| `test_transition_finding_event_carries_state_zero_deposit` | kind=finding 事件 payload.state=新状态；从不产 deposit（§2.4/D9） |
| `test_submit_scout_triage_and_guess_backfill` | candidates.triaged=1、guess 回填；injection_flag 候选保持候选态标高优先（§2.5/§11.6-3） |
| `test_submit_auditor_issues_id_and_evidence_chain_guard` | finding_id=`F-{date}-{seq:06d}` run 内自增；证据链 <3 节点或缺 source/sink/sanitizer → SchemaRejected 零落库（§2.5/SPEC-1 §4.2） |
| `test_submit_auditor_mismatch_auto_reroute` | 产出 (family,type) 与契约节点错配 → 自动 re_route + 猜测格弱存款（§2.5/§3.1） |
| `test_submit_reviewer_self_vote_rejected_and_auto_close_panel` | reviewer==reporter → ValueError（纪律 2）；票满 params.panel.SIZE 自动 close_panel（§2.5/§4.4） |
| `test_submit_verifier_fp_without_blocking_forced_verify_error` | §10.4 改判守卫：verified_fp 缺 blocking_evidence → 强制 verify_error(error_kind=poc_fail)（§2.4/§2.5） |
| `test_submit_verifier_outcome_mapping_and_s2_deposit` | verified_real→verified+tier=S2 dir=+1、verified_fp→rejected+dir=−1、verify_error→inconclusive 零存款零投影（§2.4 表） |
| `test_submit_rejects_task_not_dispatched` | task 非 dispatched → ValueError（§2.5） |
| `test_expire_run_zero_deposit_terminal_idempotent` | 非终态 finding→expired（零存款）、未完任务→failed；已终态 → []（§2.3） |

【修订留痕 2026-10-11 终检（DECISIONS 同日终检行）】上表 17 例中 7 例 `test_submit_*` 全仓无承载：SPEC-2 §2.5 `submit()` 统一代落账入口未落位（dispatch 切片行预声明归接线子任务，接线按 §0.4 窄口接通后未回补），语义面已分散落位（scout 弱存款→test_kernel_reroute、auditor 建档→open_finding、reviewer/verifier 收口→cluster.close_panel/propagate_verdict + test_verdict_deposits）。门禁 A 组「最低用例名集齐」本文件口径按 40/47 如实记录，处置待负责人明示（补实现或修订判据）；交付报告遗留问题 L1。其余 40 例全部按名落位且全绿。

【处置留痕 2026-10-11（DECISIONS 同日 L1 处置行，方案 a）】负责人明示补实现：`submit()` 四分支落位 `kernel/dispatch.py`（SchemaRejected 退回 / candidates 三角化回填 / 错配自动 re_route / 票满自动 close_panel / §10.4 改判守卫 + S2 映射），七例 `test_submit_*` 按名落位 `tests/test_kernel_dispatch.py`，A 组最低用例名集齐 **47/47**；实现期装配面三处未定细节（谱系身份=task.assignee、finding_id/建档字段=shard_json 载体、scout 分支零事件与 §11.6-3 标高优先形态）记 DECISIONS 同日行。本表「run 内自增」措辞按 D20 库内同前缀 max+1 口径执行（单 run 独库逐位同值），不改用例名。

### 1.3 re_route 与猜测格弱存款 `kernel/reroute.py`（SPEC-2 §3）

【判据】`python -m pytest tests/test_kernel_reroute.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_is_mismatch_rules` | 家族段不同=错配；同族双类型段不同=错配；家族级→类型级细化=不算（§3.1/D12） |
| `test_re_route_finding_weak_deposit_standard_form` | 每 scout 一笔：S1 / −1 / 0.05 / taxonomy_path=from_node 路径段 / origin=prod / src_seq=re_route 事件 seq（§3.2/D13） |
| `test_re_route_finding_at_most_once` | 同 finding 已有 re_route 事件 → 零新事件（§3.2/§6.4） |
| `test_guess_taxonomy_ranking_deterministic` | 票数 → 猜测格 C* 之和（无格 ROOT_PRIOR）→ 字典序；无猜测 None；纯函数零 rng（§3.2） |

### 1.4 配额与预算熔断 `kernel/budget.py`（SPEC-2 §4）

【判据】`python -m pytest tests/test_kernel_budget.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_budget_for_mode_table` | fast→FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS（§4.2） |
| `test_quota_l2_boundaries` | min(cap, ⌊remaining/cost⌋)；cost≤0 或 remaining≤0 → 0（§4.2） |
| `test_quota_s2_formula_and_zero_cost` | ⌊存活数×remaining/cost⌋；cost≤0 → 0（§4.2） |
| `test_s2_rank_key_lexicographic` | (SEVERITY_RANK, scarcity, confidence) 字典序降序；scarcity=1/(1+簇数)；type_node=None 按空串；不取乘积（§4.2/D15） |
| `test_budget_state_trips_at_equal_spent` | spent ≥ budget 含等号 → degraded=True（D16）；tier 沿 full→pr→fast 到底；quota 按剩余重算（§4.2/§4.3） |
| `test_on_cost_first_trip_scaling_event_degraded_status` | 首次 trip：SCALING(pool=f"mode:{mode}", delta=−1, reason="budget_breaker") + runs.status='degraded' 同事务；重复 trip 零新事件（§4.2/D17） |
| `test_batch_eligible_full_only` | batch_eligible 仅 full 为 True（§4.1） |
| `test_expire_run_after_breaker_zero_deposit` | 熔断收尾接 expire_run：expired 路径不产任何 deposit（负断言：全账本 deposit 计数不变）（§4.3/§2.4） |

### 1.5 并发纪律横切 `kernel/ledger.transaction()`（SPEC-2 §7）

【判据】`python -m pytest tests/test_kernel_transaction.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_transaction_commits_or_rolls_back_atomically` | BEGIN IMMEDIATE；正常退出 COMMIT、异常 ROLLBACK；回滚后账本与表零残留（§7.1） |
| `test_append_inside_transaction_defers_commit` | conn.in_transaction 为真时 append 不逐条 commit，由上下文边界统一提交（§7.1） |
| `test_append_without_context_unchanged` | 无上下文行为与 M0 逐条提交逐位一致（§7.1；M0 兼容由全量回归双保险） |

---

## 2. B 组：不变量与确定性验收

### 2.1 写口静态扫描扩展（SPEC-2 §6.1/§9-1）

【判据 1（测试）】`python -m pytest tests/test_kernel_ledger.py -q` 全 passed，其中 `test_write_port_static_scan` 的词表已扩展为 SPEC-2 §6.1 全表：
`ledger, cells, findings, evidence_nodes, votes, verify_results, clusters, cluster_members, tasks, candidates, runs`（写语句正则仍为 `INSERT INTO|REPLACE INTO|UPDATE|DELETE FROM`）。
【判据 2（独立 grep 审计，交付报告粘贴输出）】

```bash
grep -rnE "\b(INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|UPDATE|DELETE[[:space:]]+FROM)[[:space:]]+(ledger|cells|findings|evidence_nodes|votes|verify_results|clusters|cluster_members|tasks|candidates|runs)\b" \
  qswarm/ --include="*.py" | grep -v "^qswarm/kernel/"
```
【期望】输出为空（管道无行），全链路退出码非零（grep 无命中）＝无越界。

### 2.2 幂等双跑（SPEC-2 §6.4/§9-3）

【判据】`python -m pytest tests/test_kernel_cluster.py tests/test_kernel_dispatch.py tests/test_kernel_budget.py tests/test_kernel_reroute.py -k "idempotent or guard or at_most_once or zero_events or zero_deposit" -q`
【期望】全 passed，收集数 ≥ 8（§6.4 表八行逐行有测试承载：apply_merges / split/dispatch/re_dispatch 守卫 / propagate / close_panel / re_route / on_cost trip / expire_run）。
【界线钉死】读侧幂等（重复业务事实）→ 跳过返回 []；写侧非法（状态机不允许）→ ValueError——两侧用例都须存在（`test_*_idempotent_zero_events` 与 `test_*_guard_rejects`/`_then_failed` 命名即区分）。

### 2.3 确定性（SPEC-2 §6.5/§9-4）

【判据 1】`python -m pytest tests/test_kernel_determinism.py -q` 全 passed，最低用例：
- `test_m1_event_stream_bit_identical_same_seed`：同 (种子, 输入事件序, params) 两次运行事件流 sha256 全等；
- `test_event_stream_identical_across_pythonhashseed`：`PYTHONHASHSEED` 取不同值的两子进程事件流逐位一致（对标 tests/test_world_taskgen.py 跨解释器模式）。

【修订留痕 2026-10-11 终检（DECISIONS 同日 §2.3 行）】上列文件与用例名未按名落位，判据语义由既有测试等效承载，按以下映射执行：`test_sim_cluster_wiring.py::test_same_seed_attribution_bit_identical` + `::test_attribution_identical_across_pythonhashseed`（簇归属与轨迹 digest，子进程跨 PYTHONHASHSEED）、`test_verdict_deposits.py::test_verdict_flow_bit_identical_same_seed` + `::test_verdict_flow_cross_process_bit_identical`（M1 事件流同种子位级 + 跨进程）、`test_kernel_cluster.py::test_attribute_findings_deterministic_and_hashseed_free`、`test_kernel_reroute.py::test_re_route_deterministic_event_stream`——全部用例在全量 pytest（528 例 0 skip）内。
【判据 2（M1 新函数零随机源）】

```bash
grep -nE "\brng\b|\brandom\b" qswarm/kernel/cluster.py qswarm/kernel/dispatch.py qswarm/kernel/reroute.py qswarm/kernel/budget.py
```
【期望】0 命中（内核唯一随机消费点仍是 route/route_thompson）。

### 2.4 M0 复用面零重写（SPEC-2 §0.3）

【判据】`python -m pytest tests/test_kernel_route.py tests/test_kernel_route_thompson.py tests/test_kernel_cells.py tests/test_kernel_ledger.py tests/test_kernel_promote_scan.py tests/test_kernel_replay.py tests/test_golden_regression.py -q`
【期望】全 passed——M0 六面的既有行为测试与黄金回归全绿即「原样复用」的可检表达；M1 新模块 diff 不得出现对上述模块逻辑的复制（评审项：交付报告对账表逐行「调用/未复制」声明）。

---

## 3. C 组：重放等价三投影（父任务 ac_mv1s39t231p9yxml 判据化）

### 3.1 单元级：rebuild/diff 扩展（SPEC-2 §6.3/§9-2）

【判据】`python -m pytest tests/test_kernel_replay.py -q` 全 passed，新增最低用例：

| 用例名 | 钉住的判据 |
|---|---|
| `test_rebuild_clusters_from_events` | clusters/cluster_members/verdict_* ← cluster_merge/cluster_split + verify_result/human_verdict 事件折叠；members 真源=事件增量（§6.3） |
| `test_rebuild_findings_last_state_wins` | findings.state 取每 finding 最后一条 finding 事件 payload.state；grade/confidence/disagreement 同法重建（§6.3/D9） |
| `test_diff_clusters_diff_findings_bit_exact` | 字段 `==` 全等无容差；重建 vs 在线投影 diff 为空（§6.3） |
| `test_rebuild_three_projections_arbitrary_prefix` | 多个 upto_seq 前缀（含 0/中途/最新）三投影 diff 全空（「任意时点」判据） |
| `test_ledger_rebuild_returns_three_projection_summary` | `ledger.rebuild()` 三投影摘要 dict；`rebuild_cells` 单投影入口保留（§6.3） |

### 3.2 轨迹级专项：3 种子 × ≥3 检查点 + 收尾全账本 + cells 物理表比对

【判据】新增独立取证脚本 `experiments/verify_ac_replay_m1.py`（对标 scripts/verify_ac_*.py 模式）：

```bash
python experiments/verify_ac_replay_m1.py --seeds 11,22,33 --n-days 30 --replay-every 10 \
  2>&1 | tee results/_replay_m1_ac.log; echo "EXIT=$?"
```
【方案（脚本必须实现并在输出中逐行留痕）】
1. 逐种子跑 engine（seeds 11/22/33，n_days=30，replay_every=10 与官方运行同口径）；
2. **每种子 ≥3 个检查点**（day 10 / day 20 / 收尾 day 30）：在检查点 seq 处 `rebuild_cells` vs 在线 cells → `diff_cells` 空；`rebuild_clusters` vs 在线簇投影 → `diff_clusters` 空；`rebuild_findings` vs 在线 finding 投影 → `diff_findings` 空；
3. **收尾全账本重放**：从空账整段重放至临时库；
4. **cells 物理表逐字段比对**：临时库 vs 原库 `SELECT * FROM cells`（全列，按 (agent,role,node,family,type) 主键序）逐行逐列 `==` 全等；clusters/cluster_members/findings（含 state/grade/confidence/disagreement 列）同法比对；
5. 输出：逐检查点一行 `PASS <seed> <checkpoint> diff_cells=0 diff_clusters=0 diff_findings=0`；收尾一行 `PASS <seed> physical-tables cells/clusters/findings ALL EQUAL`；末行 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY`。
【期望】退出码 0；`results/_replay_m1_ac.log` 含末行 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY` 且无 FAIL。

### 3.3 引擎内环校验不回退（M0 ⑨ 承载）

【判据】`python -m pytest tests/test_sim_engine.py -q` 全 passed（`test_run_produces_all_event_kinds_and_nine_metrics` 断言 replay_report.passed 且 replay_day_reports 全过）；矩阵级 ⑨=100% 由 E 组全量复核。

### 3.4 崩溃窗口修复

【判据】`python -m pytest tests/test_kernel_cluster.py -k "propagate_pending" -q` 全 passed（§1.1 `test_propagate_pending_repairs_crash_window`——构造「源事件已落、派生存款未落」，运行后按 (cluster,tier,src_seq) 比对派生存款集合，窗口收敛为零）。

---

## 4. D 组：黄金回归与突变门（digest 比对方式）

### 4.1 M0 黄金回归全绿（digest 逐位不变）

【判据 A（回归绿，基线判据）】`python -m pytest tests/test_golden_regression.py -q`
【期望】`37 passed`（导出时点基线 = 34 例夹具参数化〔route 9 / route_thompson 9 / promote_scan 8 / agg_cells 8〕+ 3 项元断言〔manifest counts 门 / 溯源 seed+digest / slices 前缀性〕；M1 新类冻结后总数只增不减，M0 四类 34 例不得减少）。夹具参数以 manifest.json 钉死（`manifest_params`），不随 configs/params.yaml 漂移——这是黄金门的有效比对面。

【判据 B（夹具字节不变，基线判据）】导出时点已固化基线清单 `results/_golden_m0_baseline.sha256`（57 文件：34 case + manifest + slices + README）：

```bash
sha256sum -c results/_golden_m0_baseline.sha256 --quiet && echo SHA256_MANIFEST_ALL_OK
```
【期望】`SHA256_MANIFEST_ALL_OK`，退出码 0——「既有 M0 黄金用例 digest 逐位不变」的可检形态。凡 M1 实现需要改任何 tests/golden/ 文件，必须先走 §5.1 sanctioned 判定。

【digest 比对方式与 `--check` 的基线事实】
- manifest.json 的 `trajectory.digest`（seed=7，sha256）+ `regen_command`/`verify_command` 字段为溯源锚点（比对命令：`python3 -c "import json;print(json.load(open('tests/golden/manifest.json'))['trajectory'])"`，输出不得变化）；
- **`python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check` 在 SPEC-5 导出时点为红（基线即红，非 M1 造成）**：夹具冻结早于 params.yaml v2 定稿——manifest 钉旧参数（promote N=10/DELTA=0.1、无 FC_*/ENTRY_* 键），重生成走当前 v2 参数，自检在 promote-006 失败（transitions expected=21 got=20，已实测留痕）。该工具的恢复属 M1「冻结新黄金」子任务：凡发生 §5.1 sanctioned 变更并按 manifest.regen_command 重冻（以**当时** configs/params.yaml 全量重生成，M0 34 例一并重冻、清单更新并记 DECISIONS），重冻后 `--check` **必须恢复退出码 0** 并升格为 D 组终判据；若 M1 未触发 sanctioned 变更，`--check` 修复不列入 M1 门禁、如实记遗留问题（禁止静默当绿）。

【跌标处置】M1 实现致判据 A/B 任一红 = 内核行为变更：非 sanctioned 立即停止并修复回归；属 sanctioned 变更走 §5 矩阵复跑流程并按上述重冻（重冻记入 DECISIONS.md 与交付报告变更清单，禁止静默重冻）。

### 4.2 突变门

【判据】`python -m pytest tests/test_golden_mutation_gate.py -q` → 全 passed（既有公式突变必红自证不回退）。

### 4.3 M1 新增黄金四类并纳入突变门（父任务 ac_mv1s39t2jayfvi09）

【判据】
- `ls tests/golden/` 含 `cluster/ dispatch/ budget/ re_route/` 四个新目录，各 ≥3 个夹具（沿用 M0 命名 `<kind>-NNN.json`、glob `*.json`；`find tests/golden/{cluster,dispatch,budget,re_route} -name '*.json' | wc -l` ≥ 12）；
- manifest.json `counts` 增四类且各 ≥3；每夹具含输入账本切片/生效参数/种子/期望输出与溯源（regen 扩展命令入 manifest）；
- `python -m pytest tests/test_golden_regression.py -q` 参数化覆盖四新类（总数 ≥ 37+12=49 passed；若触发 sanctioned 重冻致 M0 四类计数变化，则以重冻后 manifest `counts` 重新固化 `results/_golden_m0_baseline.sha256` 与总数基线，约束改为「M0 四类各 ≥6、新四类各 ≥3」，并在交付报告声明重冻）；
- `tests/test_golden_mutation_gate.py` 对四新类各 ≥1 代表公式钉「突变必红」：`CLUSTER_OVERLAP_MIN`、quota 公式（quota_l2/quota_s2 任一）、`FINDING_TRANSITIONS` 任一边、`RE_ROUTE_WEAK_W`。

---

## 5. E 组：官方矩阵复跑（父任务 ac_mv1s39t2m47uutjo；并发 3）

### 5.1 触发条件

仿真路径因 M1 接线变更时（verdict 归属接真实聚类、仿真注入 BudgetPolicy、引擎调用 kernel.cluster/dispatch 等 sanctioned 变更）**必跑本组**；判据：交付报告声明接线面 + §4.1 黄金/digest 是否变化。若六件套与基线逐位一致（digest 全等）可免复跑，但 §5.4 位级复核仍必做。

### 5.2 命令（统一并发 3，禁 4 或更高）

```bash
python -m qswarm.simulator.experiments --full --workers 3 --out results/experiments-full-m1 \
  > results/full-run-m1.log 2>&1
python -m qswarm.simulator.report --from results/experiments-full-m1 --out out
```

### 5.3 达标判据（162/162 组 9/9）

```bash
python3 - <<'EOF'
import csv, json
m = json.load(open("results/experiments-full-m1/manifest.json"))
assert m["workers"] == 3 and m["n_units"] == 507 and m["n_rows"] == 507, m
assert m["seeds"] == [11, 22, 33] and m["n_days"] == 30
assert m["wall_s"] <= 7200, m["wall_s"]
assert m["per_run_elapsed_s"]["max"] <= 300, m["per_run_elapsed_s"]
rows = list(csv.DictReader(open("out/group_targets.csv")))
assert len(rows) == 162 and all(r["qualifies"] in ("True", "true") for r in rows)
assert all(int(r["n_unmet"]) == 0 and int(r["n_nodata"]) == 0 for r in rows)
print("MATRIX_M1 162/162 9/9 PASS")
EOF
```
【期望】末行 `MATRIX_M1 162/162 9/9 PASS`，退出码 0。辅助核验：`timing_by_group.csv` 全行 `within_budget_5min=True`；`out/sim_report.md` §1 推荐组 `N06-D0.05-T0.60-E0.30-V0.20` 九行全 ✅；`starvation.csv` ⑧ 判定与 summary 一致。

### 5.4 位级复跑 ALL_EQUAL 证据

```bash
python experiments/tune_round.py --round 52 --workers 3   # 同管道复跑推荐组（种子 11/22/33，--replay-every 10，显式钉全部定稿旋钮；D25：tune_round 缺省 workers=4，必须显式 3，见 §11）
python experiments/compare_rerun.py \
  --base results/experiments-full-m1/results.csv \
  --rerun results/tune-round52/results.csv \
  --group "N06-D0.05-T0.60-E0.30-V0.20" | tee results/_gate_compare_round52.log; echo "EXIT=$?"
```
【期望】`metrics_equal=true digest_equal=true ALL_EQUAL=true`，退出码 0（SPEC-4 §6 位级复现；基线对照模式见 results/_gate_compare_round51.log 先例）。

### 5.5 归档与引用更新

- 【判据】M0 原矩阵目录原样保留：`ls results/experiments-full/manifest.json results/experiments-full/results.csv` 存在且 `python3 -c "import csv;print(sum(1 for _ in csv.DictReader(open('results/experiments-full/results.csv'))))"` 输出 `507`（不删除不覆盖）；
- 【判据】`results/M0_GATE_FINAL.md` 追加「M1 复核」节，引用 `results/experiments-full-m1`、`results/_gate_compare_round52.log` 与新 sim_report 路径；原文四项结论保留可考（`grep -c "M1 复核" results/M0_GATE_FINAL.md` ≥ 1 且原文标题行未改动）；
- （建议，非门）旧 `out/` 三产物快照至 `results/_archive-m1-out/` 后再覆盖。

### 5.6 跌标即停（硬条款）

`group_targets.csv` 出现任一 `qualifies=false`、9 度量任一跌破 §2 表目标、或 `replay_equivalent` < 100% → **立即停止后续步骤**：不再复跑、不调参、不放宽阈值、不重冻 golden 掩盖；原样记录跌破值并如实报告，等待负责人明示口径修订后才可继续。此条款为门禁停止判定，优先级高于本清单其他任何「修复后再验」路径。

---

## 6. F 组：M0 四项技术债清偿（父任务 ac_mv1s39t2j8yssfv6 逐项判据化）

> 每项完成后均须：`python -m pytest`（全量）→ `0 failed`，且 §4.1 判据 A/B 双绿（回归全 passed + `sha256sum -c results/_golden_m0_baseline.sha256` 全 OK，黄金 digest 逐位不变）。技术债修复**不得引入任何行为变更**（黄金不变是硬判据）；实现选择（二择一处）当场记 `DECISIONS.md` 五字段。

### 6.1 债① promote_scan 评估路径的 cells 重写

- **现状位点**：`qswarm/kernel/promote_scan.py::evaluate_candidate_criteria` 经 `agg_cells(...)` 取视图，而 agg_cells 收尾**整表重写 cells**；docstring 声称「只读」与行为不符。
- **清偿口径（二择一，记 DECISIONS）**：
  - (a) **真只读**：评估路径不再触发 cells 写表；或
  - (b) **如实声明**：docstring 明示「会经 agg_cells 重写 cells」并以 grep 限定调用点集合。
- 【判据】
  - 新防回归测试 `test_evaluate_candidate_criteria_read_only_or_scoped`（落 tests/test_kernel_promote_scan.py）：
    - 口径 (a)：断言调用前后 `SELECT * FROM cells` 行集与全列值逐位不变（且 `conn.total_changes` 增量为 0）；
    - 口径 (b)：docstring 含「重写 cells」字样（`grep -n "重写 cells" qswarm/kernel/promote_scan.py` 命中）且 `grep -rn "evaluate_candidate_criteria" qswarm/ --include="*.py"` 的非测试命中 ⊆ {promote_scan.py}；
  - `python -m pytest tests/test_kernel_promote_scan.py tests/test_m0_final_knobs.py -q` 全 passed。

### 6.2 债② ledger._schema_present 触发器校验

- **现状位点**：`qswarm/kernel/ledger.py::_schema_present` 仅查 `ledger` 表存在，不校验 append-only 两触发器（`ledger_no_update`/`ledger_no_delete`）。
- **清偿口径**：校验两触发器存在；缺失即**补建**（executescript 对应 DDL）**或显式报错**（二择一，记 DECISIONS）。
- 【判据】
  - 新测试 `test_reopen_prebuilt_db_missing_triggers`（落 tests/test_kernel_ledger.py）：预建有 ledger 表、无触发器的库文件 → 打开 KernelLedger 后 `sqlite_master` 中两触发器存在（补建口径），或抛出文档明示的异常类型（报错口径）；
  - `python -m pytest tests/test_kernel_ledger.py -q` 全 passed。

### 6.3 债③ ENTRY_DIRECT 边界测试数学可达化（全仓 0 skip）

- **现状位点**：`tests/test_m0_final_knobs.py`（约 ：385）`pytest.skip("种子 42-46 中无 accuracy=0.875 的边界格（世界抽样使然）")`——entry_items=12 时 accuracy 粒度为 k/12，0.875×12=10.5 非整数，精确等值边界**数学不可达**，该分支恒 skip。
- **清偿口径**：按 12 题口径重写为可达断言——阈值上首达格 = ⌈0.875×12⌉/12 = 11/12、末失格 = 10/12 双侧断言（保留 ≥0.875 判据语义），删除 skip 路径。
- 【判据】
  - `python -m pytest tests/test_m0_final_knobs.py -q` → 全 passed、**0 skipped**；
  - `grep -rn "pytest.skip" tests/ --include="*.py"` 输出为空（test_sim_report.py 的 `results/experiments-full 不存在` 条件 skip 分支一并移除或改为显式失败——产物目录在仓内恒存在，该分支属死代码）；
  - 全量 `python -m pytest` 末行无 `skipped` 字样。

### 6.4 债④ test_config 逐键钉住 params.yaml 全部 v2 定稿旋钮

- **现状位点**：`tests/test_config.py` 仅抽查 ~18 键。
- **清偿口径**：对 `configs/params.yaml` **全部叶子键**逐一显式断言期望值（promote 8 / tilt 4 / shrink 1 / decay 3 / demote 8 / exam 14 / explore 1 / budget 4+M1 新增 3 / deposits 4 / panel 2 / roster 4——以实现时点实际叶子集为准；v2 机制旋钮值见 DECISIONS 2026-10-09/10 各定稿行）。
- 【判据】
  - 新测试 `test_config_pins_every_leaf_key`：程序化取 params.yaml 叶子键全集，与测试内显式期望表键集做差集断言 `== ∅`（缺口即红，防未来加键漏钉）；
  - `python -m pytest tests/test_config.py -q` 全 passed。

### 6.5 四债总清偿判据

```bash
python -m pytest 2>&1 | tail -2          # 期望：N passed（无 failed、无 skipped），N ≥ 339 + 新增
sha256sum -c results/_golden_m0_baseline.sha256 --quiet && echo SHA256_MANIFEST_ALL_OK
grep -rn "pytest.skip" tests/ --include="*.py" | wc -l   # 期望 0
```
【期望】全量 pytest 全绿 0 skip；`SHA256_MANIFEST_ALL_OK` 打印；skip 计数 0；四项各留证据行进交付报告。

---

## 7. G 组：约束判据（父任务 ac_mv1s39t2luneoms3）

### 7.1 无网络

```bash
grep -rnE "\b(socket|ssl|urllib|urllib3|http\.client|httpx|requests|aiohttp|ftplib|smtplib|telnetlib)\b" qswarm/ --include="*.py"
```
【期望】0 命中（基线已核验：现仓唯一近似命中为 `subprocess` 本地降级执行器，非网络）。测试进程不访问网络（pytest 全绿本身在离线环境即可复现为旁证）。

### 7.2 无新增第三方依赖

```bash
grep -rhE "^\s*(import|from) " qswarm/ --include="*.py" | grep -vE "^\s*(from|import) \." \
  | sed -E 's/^\s+//; s/^(from|import) ([a-zA-Z_0-9]+).*/\2/' | sort -u \
  | grep -vx -f <(printf '%s\n' __future__ argparse bisect collections concurrent contextlib csv dataclasses datetime enum functools hashlib importlib itertools json math os pathlib pydantic qswarm random re shutil sqlite3 statistics subprocess sys tempfile time types typing yaml)
```
【期望】输出为空（qswarm/ 顶层第三方根 ⊆ {pydantic, yaml}；tests/ 另允许 pytest）。`pyproject.toml` 的 `dependencies`/`optional-dependencies` 不变（交付报告 diff 留痕）。禁网络与依赖白名单同时保证「无 LLM SDK」。
【修订留痕 2026-10-11 终检（DECISIONS 同日 §7.2 行）】白名单补 3 个标准库根 `contextlib`/`functools`/`re`（M1 kernel/replay.py 等新增 import；原清单漏列致字面「输出为空」不可复现，三根均非第三方）；判定结论不变，另以 AST 全量扫描（81 文件，第三方根闭集 {pydantic, yaml, pytest}）作双口径证据。

### 7.3 并发 3

- 【判据】官方矩阵与一切批量验证命令显式 `--workers 3`；§5.3 判据已断言 `manifest.json workers == 3`；（D25：`experiments/tune_round.py` 的 `--workers` **缺省 4**，凡调用必须显式 `--workers 3`，§5.4 命令已按此定稿）；
- 【判据】`grep -rn "workers" results/full_run_v2.sh results/full-run-m1.log 2>/dev/null | head`——新运行记录中不得出现 `--workers 4` 或更高（M0 旧脚本 `full_run_v2.sh` 的 `--workers 4` 属历史留痕仅作对照，M1 起所有新命令为 3）。

### 7.4 规格未覆盖处当场记 DECISIONS

【判据】`docs/spec/DECISIONS.md` 含 M1 实现期新增五字段行（`grep -c "| 2026-10-" docs/spec/DECISIONS.md` ≥ 实现决策数），交付报告附新增行清单；实现与记录一致（评审项）。

---

## 8. H 组：交付对账（终检子任务输入）

交付报告必须含，且每项有证据行：

| # | 项 | 判据/出处 |
|---|---|---|
| 1 | 变更清单 | 文件级（新增/修改/删除）+ 一句话目的 |
| 2 | SPEC-实现对账表 | SPEC-2 §0.2 每行 → 落位文件 + 测试文件 + 黄金类（§0.3 每行标「复用未改」） |
| 3 | 写口 grep 审计证据 | §2.1 判据 2 命令与输出（空输出即证据）粘贴 |
| 4 | DECISIONS 新增行 | §7.4 |
| 5 | 遗留问题 | 逐条（无则明示「无」） |
| 6 | 免审直跑记录 | 见 §9 免审分支；两份 SPEC（SPEC-2、SPEC-5）全文进交付报告 |
| 7 | 重负载并发 3 留痕 | §5.2/§7.3 manifest 与日志 |

---

## 9. 门禁节（M1 → 下一步放行条件；对齐设计 §16.6 模式与 SPEC-4 §5 形态）

### 9.1 通过判定（全部勾选方放行）

- [ ] **A 组四块功能全绿**：§1.1–§1.5 五条 pytest 命令全 passed，最低用例名集齐；
- [ ] **不变量**：写口全词表扫描绿 + §2.1 独立 grep 审计空输出；幂等双跑 ≥8 例；确定性两用例绿 + M1 新函数零随机源 grep 空；
- [ ] **重放等价**：§3.1 单元五用例绿 + §3.2 专项脚本 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY`（3 种子×≥3 检查点 + 收尾全账本 + cells 物理表逐字段全等）；
- [ ] **黄金**：§4.1 判据 A（`37 passed` 基线）+ 判据 B（`sha256sum -c results/_golden_m0_baseline.sha256` 全 OK，digest 逐位不变）；突变门绿；M1 四新类 ≥12 例纳入回归与突变门；（sanctioned 重冻后）`--check` 恢复退出码 0；
- [ ] **矩阵**（如触发 §5.1）：`MATRIX_M1 162/162 9/9 PASS` + 位级复跑 `ALL_EQUAL=true` + 归档与 M0_GATE_FINAL.md 引用更新；
- [ ] **技术债四项**：§6.1–§6.4 逐项判据绿 + §6.5 全量 0 skip 全绿 + `SHA256_MANIFEST_ALL_OK`；
- [ ] **约束**：无网络 grep 空、依赖白名单闭集、并发 3 留痕、DECISIONS 同步；
- [ ] **全量 pytest**：`python -m pytest` → `N passed`、`0 failed`、`0 skipped`（N ≥ 339+新增）；
- [ ] **交付对账**：§8 七项齐备。

**人审物**（§16.6「人只审里程碑」）：门禁节勾选状态 + 交付报告 + results/ 证据文件；人审不重复执行机器判据，只审证据链完整性与跌标处置是否符合 §5.6。

### 9.2 停止判定（跌标即停，任一触发即停）

1. 任一判据命令非零退出 / 期望字面未出现，且按本清单无既定修复路径；
2. §5.6 触发：矩阵任一度量跌破目标 / `qualifies=false` / ⑨<100%；
3. M0 黄金 digest 变化且不属 §5.1 sanctioned 变更；
4. 技术债修复引入行为变更（§4.1 digest 不变判据红）；
5. 需要「静默改 golden、调参掩盖、放宽阈值、跳过判据」才能继续的任何情形。

停止后动作：原样记录触发项与现场输出，如实报告，**不得放行**；等待负责人明示决策（口径修订 / 返工 / 免审）后才可继续。

### 9.3 人工批准 / 免审直跑分支（父任务 ac_mv1s39t26l1to9he）

- **冻结门槛**：SPEC-2 与本文件定稿落盘前，`qswarm/` 与 `tests/` 不得出现任何 M1 新增或修改代码（含技术债修复）；验证方式见 §0 末条（文件时间对照，无 git 仓库以 mtime 为准）。
- **人工介入可用**（环境提供向负责人请示的通道）：两 SPEC 定稿落盘后**停下**，待负责人批准后放行实现子任务；批准记录（时刻/渠道/结论）进交付报告。
- **人工介入不可用**：执行侧**记录免审直跑决定**（记入 `DECISIONS.md` 五字段：日期/模块=SPEC-5/决策=免审直跑/理由=环境无人工介入通道/影响=门禁照常全量执行），且 **SPEC-2 与 SPEC-5 全文进交付报告**；门禁本身不因免审而降档——§9.1 全项照常执行。
- **分支可检判据**：交付报告出现以下之一——(i) 负责人批准记录，或 (ii) 免审直跑的 DECISIONS 行 + 两 SPEC 全文附件。二者缺一 = 门禁不通过。
- **执行记录（2026-10-10，规格门歧义拍板子任务 ac_mv1s9fkj2973617s 落笔）**：本执行环境判定为**人工介入不可用**——taskflow 自治执行会话（工具提权自动放行、无负责人实时审批回路；停止等待将使流水线停摆且会话被看门狗按静默挂起终止）→ 走分支 (ii) **免审直跑**，已按预设五字段记入 `DECISIONS.md`（模块=SPEC-5 / 决策=免审直跑 / 理由=环境无人工介入通道 / 影响=门禁照常全量执行）。**SPEC-2 与本文件全文须进交付报告**；门禁不因免审降档——§9.1 全项、§9.2 停止判定、§5.6 跌标即停照常生效；后续实现类子任务自本记录落盘起放行。

---

## 10. 判据索引（对照父任务验收标准）

| 父任务 ac | 本文件判据节 |
|---|---|
| ac_mv1s39t26l1to9he（SPEC 落盘/冻结/批准分支） | §0、§9.3 |
| ac_mv1s39t2j8yssfv6（技术债四项） | §6.1–§6.5 |
| ac_mv1s39t2jayfvi09（M1 功能实现纪律） | §1（A 组）、§2（B 组）、§4.3（黄金突变门）、§9.1 |
| ac_mv1s39t231p9yxml（digest 不变 + 重放等价 3×≥3） | §4.1、§3（C 组） |
| ac_mv1s39t2m47uutjo（矩阵复跑并发 3 / 162×9/9 / 跌标即停） | §5（E 组）、§9.2 |
| ac_mv1s39t2luneoms3（无网络/无新增依赖/并发 3/DECISIONS/交付） | §7（G 组）、§8（H 组） |

---

## 11. 决策记录（定稿拍板；五字段；已逐条同步 `docs/spec/DECISIONS.md`）

本文件定稿（2026-10-10）时拍板的歧义与最小惊讶项；SPEC-2 侧同轮拍板的 D19–D24 见该文件 §10.1（同样已同步 DECISIONS.md）。实现期新歧义按滚动协议记 DECISIONS.md，不回改本文件编号语义。

| 日期 | 模块 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| 2026-10-10 | SPEC-5 | D25：§5.4 定稿修正——`tune_round.py --round 52` 补显式 `--workers 3`（导出时点遗漏该旗标） | 该工具 `--workers` 缺省 4，照抄原命令即违反 §7.3「一切批量验证命令显式 --workers 3、禁 4 或更高」；并发 3 是父任务合同硬约束，定稿阶段修命令行优于实现期返工 | docs/spec/SPEC-5_m1_acceptance.md §5.4/§7.3；experiments/tune_round.py 代码不改（缺省值留作历史对照） |
| 2026-10-10 | SPEC-5 | D26：免审直跑——环境无人工介入通道（taskflow 自治执行会话，工具提权自动放行、无负责人实时审批回路），两 SPEC 定稿落盘后不停待批准，放行后续实现类子任务 | SPEC-5 §9.3 预设分支 (ii)；自治环境下等待人工批准将使流水线停摆且触发看门狗静默终止 | 门禁不降档：§9.1 全项 + §9.2 停止判定 + §5.6 跌标即停照常执行；**SPEC-2 与 SPEC-5 全文进交付报告**（§9.3 分支 (ii) 可检判据）；执行记录见 §9.3 末条 |

> 滚动协议：本文件为 M1 里程碑的验收快照；实现期发现判据不可执行（命令不存在、期望不可复现）时，按滚动协议记 `DECISIONS.md` 并在本文件对应节修订（修订留痕，不回改编号语义）；SPEC-4 §5 仍是 M0 门禁的原始记录，不被本文件取代。
