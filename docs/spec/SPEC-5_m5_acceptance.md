# SPEC-5 M5 验收卷（S2 沙箱与真验证）

> v1.0（2026-10-21）。判据全部机器可检；对应测试文件与实测结果如实标注。
> 基线：全量 pytest **754 passed**（2026-10-21，712 基线零回归 + M5 新增 42 例）。

## A 组：沙箱执行面（G1；tests/test_m5_sandbox.py）

| # | 判据 | 测试 |
|---|---|---|
| A1 | 红线词表守卫：tier 双档、network 只收 none、越值 ValueError | test_sandbox_config_redline_vocabulary |
| A2 | 红线逐条落档：redline_report() 含 network/user/read_only_root/limits/timeout/docker_sock_mounted=False | test_sandbox_config_from_params_and_redline_report |
| A3 | env 身份确定性：同 (base,deps) 恒等、序不敏感、内容敏感 | test_env_key_deterministic_and_dep_sensitive |
| A4 | D39 双围栏：extract_poc 取最后一个 ```python 围栏；harness 断言行解析 | test_extract_poc_takes_last_python_fence / test_parse_assertions_harness_contract |
| A5 | 家族模板：python 注入/穿越可生成；他族如实 None | test_template_poc_families_and_unsupported |
| A6 | process 档真执行：注入靶 PASS / 干净靶 FAIL（近似红线逐键如实记录） | test_process_runner_real_execution_confirm_and_fail |
| A7 | 墙钟超时 → error_kind=timeout | test_process_runner_timeout_classification |
| A8 | env 缓存复用（env_key 恒等 + 标记文件） | test_process_runner_env_cache_reuse |
| A9 | container 档 docker 缺失 → env_build_fail（拒绝降级） | test_container_runner_docker_missing_is_env_build_fail |
| A10 | 档位守卫：runner 构造与配置档强一致 | test_make_runner_tier_guard |
| A11 | 真 docker container 档：禁网/非特权/只读根全施加 + 注入确认 + 超时收割（docker 缺失跳过） | test_container_runner_real_docker_confirm（2026-10-21 实跑通过） |

## B 组：配置与词表（tests/test_config.py / test_kernel_dispatch.py / test_m3_invariants.py）

| # | 判据 | 测试 |
|---|---|---|
| B1 | sandbox/verify 节逐键钉值 + exam.DARK_EXAM_ENABLED=False | test_m5_sandbox_verify_knobs_pinned |
| B2 | 缺节回落规格缺省（黄金兼容）+ 越键拒绝（extra=forbid） | test_params_sandbox_verify_sections_optional_with_spec_defaults / _extra_forbidden |
| B3 | ROLE_* 三表 advisor 行落位 + build_contract 对 advisor 仍显式拒绝 | test_role_literal_tables / test_build_contract_four_pieces_pure（M4 既有，更新钉值） |
| B4 | ARTIFACT_KINDS 扩 4 类且审计词表不变 | test_m3_invariants（既有 ≤ 断言零改动通过） |
| B5 | taxonomy types 面补齐（command_injection / path_traversal） | test_load_default_taxonomy |
| B6 | M0 effective_M 按 M5 扇出重推导（injection 10 / authz 15） | test_m0_final_knobs（更新推导值，判据③端到端随之 10/9） |

## C 组：run_verify 双档与 reconciliation（tests/test_m5_verify.py）

| # | 判据 | 测试 |
|---|---|---|
| C1 | D43-a 写口门：非 container 触碰生产面 ValueError | test_assert_production_tier_gate |
| C2 | reconciliation 全分支数值钉（无 PoC/env/timeout/绿×3/红×3/bee=None） | test_reconcile_all_branches |
| C3 | 选取面：全员 voted ∧ 无 VR 行；谱系回读（scouts/votes） | test_eligible_clusters_selection_face |
| C4 | **process 档零账本写**（FakeRunner：事件/任务/VR/cells 全等对照）+ 试跑工件面 + match 判定 | test_process_tier_zero_ledger_writes_with_fake_runner |
| C5 | **G2 已知答案试跑（真沙箱真执行）**：m3 仓注入靶 verified_real、match 全 True、R7-② 披露面在位、零 VR 行 | test_process_tier_real_sandbox_known_answer |
| C6 | container 档生产全链：VR 行 + verify_result 事件 + §2.4 映射（verified/L3）+ S2 存款 ×0.5 逐角色权重 + 工件三件套 + 断言 log 回填 + on_cost 入账 + D39 蜂调用面 | test_container_tier_full_chain_verified_real |
| C7 | env 构建失败 → verify_error(env_build_fail)：inconclusive 映射、零存款 | test_container_tier_env_build_fail_zero_deposits |
| C8 | verified_fp + blocking → rejected 映射 + S2 反向存款 | test_container_tier_verified_fp_with_blocking |
| C9 | **R7-① quota_s2 实编**：预算 9 cents × 1 蜂 → 配额 0 → 全 skipped_budget、零任务零 VR | test_container_tier_quota_by_verifier_headcount |
| C10 | 无 active verifier → fail-closed 如实缺席 | test_container_tier_no_verifier_failclosed |
| C11 | 幂等：首轮全验后选取面为空、VR 行数不变 | test_verify_idempotent_rerun |

## D 组：试跑 CLI（qswarm/sandbox/trial.py）

| # | 判据 | 实测 |
|---|---|---|
| D1 | 真实素材试跑：E 组 smoke（out/m4-seeding-v3/prod.db × out/m4-smoke/repo）2 voted 簇 → 模板 PoC → process 档真执行 → verified_real × confirmed | **match 2/2**（out/m5-trial/，2026-10-21；期望源 s1_projection——smoke 轮无 pv 工件，回退面如实标注） |
| D2 | 判读输出：逐簇 expected/observed/poc_source/match + 沙箱墙钟（非 LLM）披露 | CLI stderr 判读块 + verify_report.{json,md} |

## E 组：仲裁 / 暗考 / 清账（tests/test_m5_g6.py）

| # | 判据 | 测试 |
|---|---|---|
| E1 | D42-a 最小仲裁件：摘要+草稿工件（summary_ref 回填）+ tasks/DISPATCH 锚 + 零存款 + verify_results 零行 + 幂等 | test_run_arbitrate_artifact_and_task_anchor |
| E2 | 解析失败 → schema_fail、任务留 dispatched；未知簇如实计数 | test_run_arbitrate_schema_fail_and_unknown |
| E3 | 暗考默认关（开关联动 bank 双前提）+ dark_exam_enabled 标志 | test_dark_exam_default_off / test_dark_exam_wiring_requires_bank |
| E4 | 暗考回调单元：ace 卷 passed、存款零重复、故障 fail-open | test_dark_exam_callback_unit |
| E5 | rules_runner 生产显式装配（真 semgrep 通道 C 实跑非缺席） | test_rules_runner_default_assembly |
| E6 | 通道 C 路径归一（绝对路径/./ 前缀 → 相对 posix） | test_rule_candidate_path_normalization |
| E7 | pv 判据三态（空池/预算截面/正常）+ 面板同门控 | test_pv_skip_semantics_three_states |
| E8 | 存款值版本化刷新：补偿存款使净值收敛 + 重跑幂等 | test_exam_deposit_versioned_refresh |
| E9 | 注入防线显式断言（伪造分隔行 → 第三条 system） | test_injection_guard_explicit_on_slice_content |
| E10 | M4 缺口①：monotonic 注入 → wall_clock_exceeded 机器可检 + 零付费任务 | test_wall_clock_injectable_consult |
| E11 | M4 缺口②：重跑账本事件流逐位 payload 对照 | test_consult_bitwise_idempotent_rerun |
| E12 | M4 缺口③：repomap 缓存复用（文件集 name/size/mtime 零增长） | test_repomap_cache_reuse |

## 全量门禁

- pytest **754 passed**（含 A–E 全部新判据；M0–M4 基线零回归）；
- 写口审计 `--strict` PASS（零新表、零账本直写）；
- 黄金回归（M0 轨迹 digest）零变化（taxonomy 扇出仅影响 M0 knob 推导测试的期望值
  更新，机制参数与黄金逐位面零触碰）。
