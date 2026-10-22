# P0 交付报告：角色资格与配置身份（SPEC-10 v0.1 §1–§5/§10）

日期：2026-10-22 · 依据：DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022（方案）
§3 工作包 A + §10-P0 · 决策：DECISIONS 2026-10-22 D46 行（负责人「按这个来」/
「按建议值」拍板）· 规格：docs/spec/SPEC-10_evidence_and_evaluation.md v0.1

## §0 门禁自检（方案 P0 退出条件）

| 退出条件 | 结果 |
|---|---|
| 资格门禁通过 | ✅ 五类不合格态（无行/pending/failed/stale/revoked）+ profile 失配 + 排队后 revoked 全部拒派（tests/test_role_qualification.py 15 例全绿）；合格帽正常入池 |
| 能回答"谁可运行、按哪个 profile、哪张卷、哪个指标" | ✅ 谁可运行 = role_qualifications effective passed（按 agent×role×profile_id×policy_version 键）；profile = 规范化哈希（provider/model/role/prompt 内容哈希/ctx-v1/contract/温度锚 0.2）；卷与判据 = bank_version/scoring_version 逐行留档；指标 = SPEC-10 §9（假警报容忍 15/100 起锚，正式非劣界 pilot 后冻结） |
| 没有任何新线上调用是未经批准的 | ✅ P0 全程零线上调用（测试全部离线 FakeAdapter/注入 opener）；enforce 缺省 False，生产行为与 M5 逐位一致 |
| 历史报告勘误 | ✅ M5 联调报告追加 §8（"两蜂同卷"勘误为 38/60 重合；R3 强推断降级为不可比观察），原文保留 |

## §1 交付面

**新文件**
- `docs/spec/SPEC-10_evidence_and_evaluation.md` — v0.1（P0 精度面 §1–§5/§10；oracle 分级与校准门锚点 §6；B/C/D/E 骨架 §7–§9）
- `qswarm/kernel/qualification.py` — 只读资格面：`profile_id`（SPEC-10 §2.1 公式逐字）、`current_prompt_hash`、`QualificationGate`（effective_row/filter_pairs/recheck/latest_row）、`gate_from_params`（缺省 None=零行为差）
- `tests/test_role_qualification.py` — 15 例（SPEC-10 §10 判据 1–9 对应）
- `out/_p0_final/migration_dryrun_manifest.json` — 生产账本副本 dry-run 清单（8 帽对）

**修改**
- `docs/spec/SPEC-1_data_contracts.md` — §1 QualificationStatus（第 20 枚举）+ §2 role_qualifications DDL（IF NOT EXISTS，与 schema.sql 逐字同步）
- `qswarm/contracts/{enums.py, schema.sql, config.py, __init__.py}` — 枚举/DDL/`QualificationParams`（enforce=False、enforce_roles=["verifier"]、qp-v1、production_temp=0.2）/导出
- `qswarm/kernel/write_guard.py` — 写口词表 15→16 表；role_qualifications 唯一写口 = exams/roster.py
- `qswarm/exams/roster.py` — 写口三函数 `record_qualification`（乱序写守卫 + supersede）/`revoke_qualification`/`migrate_qualifications`（dry-run 缺省、恒判 stale(legacy_unverifiable)、永不降级真资格、温度锚对账）+ `_ensure_rq_table` 既有账本自愈建表
- `qswarm/exams/seeding.py` — seed_entry 开考写 pending → QC 后 passed/failed 终态；报告 `agent_status` / `role_qualification` 分列（qc.status 降级为考试计算值）
- `qswarm/kernel/{consult.py, verify.py, loop.py}` — 蜂池统一资格门（gate=None 缺省零过滤）；run_verify 生产 dispatch 前复验（`gate_revoked_midrun` fail-closed 计数）；报告漏斗新增 verifiers_pool/verifiers_qualified/gate_revoked_midrun（consult：gate_filtered）
- `qswarm/supply/adapter.py` — `complete_with_meta`：served_model 供应商回显原样留档、不回显 unknown（D46 红线配套；complete() 协议不变）
- `configs/params.yaml`、`tests/{test_config,test_enums,test_kernel_write_guard,test_exam_runner}.py`、`tests/golden/exams/seeding_report_schema.json` — 契约扩展的钉值同步
- `docs/spec/DECISIONS.md` — D46 行（拍板）+ P0 实施行；`docs/spec/README.md` — SPEC-10 索引行；`out/_m5_final/M5_E2E_INTEGRATION_20261021.md` — §8 勘误

## §2 判据结果

| 判据 | 结果 |
|---|---|
| 全量 pytest | **769 收集 = 768 绿 + 1 失败**；失败为 `test_m5_sandbox.py::test_container_runner_real_docker_confirm`——**既有环境失败**（本机 docker 不可用，干净树 git stash 复验同红，非本轮回归；754 基线 + 15 新增全绿） |
| 写口审计 | `python -m qswarm.kernel.write_guard --strict` → **PASS**（违规 0 / 漂移 0；role_qualifications 写语句全部落于 exams/roster.py） |
| 迁移 dry-run（生产账本副本 `out/m4-seeding-v3/prod.db` → 副本执行） | 8 帽对全部 `would_write`（stale, reason=legacy_unverifiable）；零写入对账（RQ 行数=0）；温度锚对账 mismatch=False（0.2 vs 0.2）；manifest 已落 `out/_p0_final/migration_dryrun_manifest.json` |
| 黄金兼容 | `enforce` 缺省 False：全部既有测试零行为改动通过；黄金 manifest_params（缺 qualification 节）回落缺省逐位不变 |

## §3 环境披露（如实）

- 本机 docker 不可用（M5 评估已知项）：container 档 runner 的真机判据测试在本环境不可执行，与本轮改动无关。
- semgrep 经 `.venv/bin`（D30 必选件在 venv 内）：跑全量测试需 `PATH="$PWD/.venv/bin:$PATH"`。
- 生产账本（`out/m4-seeding-v3/prod.db`）未在本轮触碰：dry-run 在副本执行；**生产库启用序 = 先 `migrate_qualifications(dry_run=True→False)`（建表 + stale 化）→ 再把 `params.qualification.enforce` 置 true**（读侧不建表，顺序不可倒置——SPEC-10 §4/§5）。

> **§3 追记（2026-10-23，环境更新）**：docker 真机环境已恢复——引擎存活（server 29.2.1），但 Ubuntu 发行版官方集成通道断（陈旧 socket + 代理进程死 + 文件共享断），经桥接方案实测可用：`DOCKER_HOST=unix:///mnt/wsl/docker-desktop/shared-sockets/host-services/docker.proxy.sock` + `/mnt/host/wsl` bind mount 桥接 `/mnt/wsl` + `TMPDIR`/`--basetemp` 落 `docker-desktop-bind-mounts/Ubuntu/`（配方与复建命令见 DECISIONS 2026-10-23 行；bind mount 不跨 WSL 重启）。**§2 判据更新：全量 pytest 769/769 全绿**（container 档真机判据实测通过，五项红线在真机复验）。原文保留，不改动。

## §4 下一步（P1 前置，逐门待批）

1. 负责人审阅本报告 + SPEC-10，批准 P1（工作包 D 两家族 oracle + B 只读证据投影 + C 漏斗 trace + E0 仪器检查）；
2. D44 正式了断（建议按方案 §13.5 口径：不再重考凑线，合格 verifier 经新资格体系考取或引入跨族供给——供给变更需另行批准）；
3. E0 裁判校准集（20–30 例人工判）与跨族主裁判选型（GLM 族禁用，红线见 D46）；
4. 实验硬预算 ¥500 / 4 周墙钟自本报告起算方式与消耗登记面（E 的 manifest）随 P1 首批落位。
