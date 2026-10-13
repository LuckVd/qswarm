# SPEC-5 M4 验收卷（L2 会诊 + repo map 切片 + route() 生产接线 + 影子期指标）

> 依据：SPEC-8_m4_consult.md v1.0；格式沿 M3 卷。判定总则同前：每条判据 = 测试文件 +
> 最低用例名集（或脚本 + 断言），机器可检；人只审门禁结论。
> 状态：v1.0（随 SPEC-8 同日导出；D31–D34/D37 已拍板）。

## 0. 判定总则

- 判据分五组：A 功能 / B 不变量 / C 可复现与等价 / D 端到端 / E 真蜂联调（前置件绑定）；
- 每组全绿才算过组；全组过 = M4 门禁过；
- 既有 673 例零回归是全程前置（任何分组开始前先跑全量）。

## 1. A 组：功能验收

### 1.1 perception.repomap（SPEC-8 §1）
- `test_repomap_name_binding_same_file_first`：被调名同文件有定义 → 仅同文件边；
- `test_repomap_unique_crossfile_edge`：全仓唯一定义 → 跨文件边；多定义歧义 → 不成边；
- `test_repomap_import_edges_regex`：py/c 双语言 import 正则边命中同仓文件、外部模块丢弃；
- `test_repomap_slice_bfs_hops`：anchor ±1 跳（pr）与 ±2 跳（full）的文件集差可观测；fast（0 跳）= 种子文件集；
- `test_repomap_slice_budget_truncate`：max_files/max_lines 截断生效且计数入 RepoSlice；
- `test_repomap_deterministic`：同输入两跑 digest 一致、文件序 = BFS 层序 + 字典序；
- `test_repomap_no_grammar_degrades`：parse_ok=False 文件零调用边、import 邻域仍可切片。

### 1.2 kernel.consult 编排（SPEC-8 §2）
- `test_consult_l2_route_dispatch`：triaged 候选 → route() 决策 → DispatchContract(role=auditor,
  taxonomy_node=guess 初值, context_refs 含 repo_map 工件引用 + 行窗) 逐项断言；
- `test_consult_route_specialist_channel`：有 active 专员格 + rng 命中 tilt → specialist 通道、
  assignee=专员（D28 导入存款可路由，F3 主判据）；
- `test_consult_route_empty_pool_fallback`：零 active 格 → least-loaded 兜底 + `l2_route_empty_pool` 计数；
- `test_consult_auditor_submit_open_finding`：AuditorFinding 产出 → findings+evidence_nodes 行 +
  finding(open) 事件；null 产出 → 任务 done + `l2_clean` 计数 + 零落库；
- `test_consult_schema_fail_retry_once`：畸形重派 1 次后 failed + 计数，零凑数 finding；
- `test_consult_coarse_cluster_panel_vote`：批内同位 finding 归簇 → 每席 vote 任务 →
  votes 行 + open→voted 迁移（close_panel 显式收口，expected_votes=面板人数）；
- `test_consult_panel_reporter_excluded`：报告者不入其 finding 面板（纪律 2 编排面守卫）；
- `test_consult_panel_starved_failclosed`：池 <2 → finding 停 open + `panel_starved` 计数；
- `test_consult_fine_cluster_jaccard`：sim_fn=Jaccard ≥0.9 合并、<0.9 不并、跨 coarse 簇不并；
- `test_consult_preverdict_slip`：每精聚类簇一张 PreVerdictSlip 工件（三字段 schema 校验）+
  verify_results 零行 + 零存款（D29 边界）；
- `test_consult_budget_quota_l2`：quota 尽 → 停新 L2 派单 + `l2_quota_skipped` 计数；
- `test_consult_expire_open_only`：预算耗尽收口 → 未开面板 open finding → expired(零存款)，
  已 voted finding 不动；
- `test_consult_wall_clock_stop`：墙钟超 → 停新派单 + `wall_clock_exceeded=true` + 收口路径。

### 1.3 存款与因子（SPEC-8 §2.4 / D32）
- `test_s1_factor_homogeneous_panel`：3 人同模型 → s1_factor=0.6，S1 行源权重 ×0.6；
- `test_s1_factor_mixed_panel`：3 人两模型 → 0.8；全异 → 1.0；2 人同模型 → 0.6；
- `test_s1_factor_s2s3_unaffected`：S2/S3 行权重不受因子影响（构造 S3 回校用例）；
- `test_reviewer_x_bind_back`：评审回校行 ±REVIEWER_X 落账（M1 既有面在编排下的联通性）；
- `test_factor_default_bitwise`：不传 s1_factor（缺省 1.0）→ M1 黄金存款 payload 逐位不变。

### 1.4 面板构造器（SPEC-8 §3 / D31）
- `test_build_panel_least_loaded_diversity`：负载平手时优先互补真实服务模型；确定性平手 agent_id；
- `test_build_panel_pool_of_two`：池=2 → 2 席收口 + 因子生效；池=1 → panel_starved；
- `test_hat_attach_roster_port`：`--hat-agents` 经 roster 唯一写口注册帽 + 纳入考试流程（§7）。

### 1.5 影子指标与最小报告（SPEC-8 §4/§5）
- `test_shadow_report_readonly`：view_cells 只读形态、零 cells 写（conn.total_changes=0）；
- `test_shadow_report_fields`：状态计数/n_eff 分位/家族覆盖/entry_knobs_zeroed 全字段；
- `test_consult_report_funnel`：md/json 字段一一对应、漏斗计数与账本事件数对账。

## 2. B 组：不变量验收

- `test_write_ports_audit_m4`：写口审计 `--strict` PASS——consult/repomap 零账本直写、
  15 表词表零漂移（votes/verify_results 写口仍唯一在 dispatch）；
- `test_purity_repomap`：repomap 纯函数零 IO 零随机（import 钩子）；
- `test_network_single_port_m4`：consult 全部模型调用经注入 adapter（socket 钩子）；
- `test_params_consult_extra_forbid`：`perception.repomap`/`consult` 节通过校验、越键拒绝、
  缺节回落规格缺省（黄金兼容双测）；
- `test_injection_guard_on_slice`：repo map 切片渲染经 ContextBlock 数据块 + INJECTION_GUARD
  共享段在位（§11.6-1 对切片生效）；
- `test_artifact_kinds_extended`：`repo_map`/`pre_verdict` 入 ARTIFACT_KINDS、misc 越词表仍拒。

## 3. C 组：可复现与等价

- `test_consult_rerun_bitwise`：同 (seed, repo 快照, FakeAdapter 固定脚本) 两跑——事件序列
  payload 逐位一致、报告 json 相等（去时间戳字段）；
- `test_route_golden_untouched`：route/route_thompson/promote_scan M1 黄金逐位回归；
- `test_cluster_golden_with_factor_default`：s1_factor 缺省下 cluster 面存款路径黄金不变
  （D32 增量修订的兼容性终判）；
- `test_repomap_cache_reuse`：切片构建零重析（indexer 缓存命中计数不变）。

## 4. D 组：端到端 smoke（离线 FakeAdapter）

- `test_e2e_scan_to_voted`：seed 仓库快照 → run_scan（L0→L1）→ run_consult（route 派
  auditor → finding → 粗聚类 → 3 蜂面板 → voted + S1 存款含因子 → 精聚类 → 预裁决单 →
  报告落盘）全链断言；
- `test_e2e_idempotent_rerun`：全链重跑零新候选/零新 finding/零重复票/零重复存款；
- `test_e2e_budget_degrade`：预算拧到最小 → full 档行为退化为部分深诊 + open 过期 +
  报告披露，进程零抛错。

## 5. E 组：真蜂联调（前置件绑定，非开发期门禁）

- 前置序列（负责人放行后执行）：①探针 4–8 次调用（请求名 vs 实际服务模型）；②E 组补播考
  （mid=5.3/5.3-flash 双蜂 + `--hat-agents` 帽兼任蜂；新 DB + 全新缓存 + S9 位级重放）；
  ③smoke 真仓 fast→pr 档全链一次（小仓库，名义预算封顶）；
- 判据 = 链路正确（事件面/工件面/报告面齐全），非蜂能力判据；时延/成本实测入 M4 交付
  报告（D33 双轨记账口径首次产出）。

## 6. 交付物

- `qswarm/perception/repomap.py`、`qswarm/kernel/consult.py`、cluster/seeding/store/
  contracts 微扩、params/models 配置回填；
- `out/_m4_final/M4_DELIVERY_REPORT.md`：A–E 组门禁结论 + D35 影子期结束判据建议值 +
  E 组联调证据索引（联调执行后补）。
