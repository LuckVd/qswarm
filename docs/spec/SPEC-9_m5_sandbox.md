# SPEC-9：S2 沙箱与真验证（M5 实现级规格）

> 版本 v1.0（2026-10-21）。依据：设计文档 v0.3.4 §6.3/§10.4/§16.4/§16.6-8/§16.7-5、
> SPEC-1（契约词表）/SPEC-2 §2.4（S2 映射/写口）/SPEC-8 §2.6（预裁决单）、
> `docs/m5_assessment_20261021.md`（开工评估）与 D38–D43 拍板（DECISIONS 同日行）。
> 落位说明：本 SPEC 与实现同轮交付（拍板「直接做完」授权单轮闭环）；实现与本文
> 的任何偏离均记 DECISIONS「M5 实现期」行。

## 0. 交付面（依赖序第 8 位；G1–G6）

| # | 交付面 | 落点 | 摘要 |
|---|---|---|---|
| G1 | 沙箱 runner | `qswarm/sandbox/`（新包） | 双档 runner + env 缓存 + 红线配置 + verify_error 四分类 |
| G2 | PoC 生成与 harness + 前置离线试跑 | `sandbox/pocgen.py` + `sandbox/trial.py` | 模板生成面（零 LLM）+ harness 断言契约 + 已知答案试跑 CLI |
| G3 | verifier 生产派单接线 | `kernel/verify.py` | run_verify 独立入口（D40-b）+ D43 落账口径 + quota_s2 实编 |
| G4 | D34 仲裁件编排 | `kernel/arbitrate.py` | run_arbitrate 人工触发（D42-a 最小仲裁件） |
| G5 | 暗考接线 | `exams/dark.py` + `kernel/loop.py` | promote_scan exam 回调接线、默认关 |
| G6 | 遗留清账 | 多处（§8） | E 组补录遗留六件 + M4 判据缺口四条 |

## 1. 契约面（词表零改纪律）

- 枚举零新增（`VerifyOutcome/VerifyErrorKind/BlockingKind` 既有，tests/test_enums 钉死）；
- `verify_results` DDL 零改——D43-a 选 (a)：process 档不写生产账本，schema 无 tier 列
  需求（container 档为 S2 生产存款唯一口径，档位由编排入口与工件面承载）；
- `PreVerdictSlip/VerifierReport` 契约零改；
- ARTIFACT_KINDS 扩 `{poc_script, poc_log, poc_deps, arbitration}`（exams/store.py；
  artifacts 不在 WRITE_TABLES，写口审计词表不变）；
- params 新节（全部带缺省=规格逐字值，缺节回落黄金兼容）：
  - `sandbox.{tier,timeout_s,mem_mb,cpus,nofile,nproc,tmpfs_mb,network,
    read_only_root,user,env_cache_dir}`；
  - `verify.{budget_cents,max_clusters,poc_attempts,wall_clock_budget_s}`；
  - `exam.DARK_EXAM_ENABLED`（默认 False）。

## 2. 沙箱执行面（G1）

### 2.1 双档（D38）

`SandboxConfig`（`sandbox/config.py`）↔ `SandboxParams`；`make_runner` 按档实装：

- **container 档**（`ContainerRunner`）：docker CLI 单容器——`--network none`、
  `--user`（缺省 nobody）、`--read-only`+`--tmpfs /work:rw,size=`、
  `--memory/--cpus/--ulimit nofile,nproc`、repo 只读挂载 `/repo:ro`、
  子进程墙钟超时 + `docker rm -f` 收割。docker 探针不可达 → SandboxResult
  `error_kind=env_build_fail`（拒绝降级执行）。repo 目录非世界可读（如 0700
  mkdtemp/私有检出，容器内 nobody 不可穿越挂载根，2026-10-21 实测）→ 0755
  staging 副本（原件零触碰，mitigations 如实记 `staged-0755-copy`）。
- **process 档**（`ProcessRunner`，缺省）：subprocess + preexec 内序 = ①
  `unshare(CLONE_NEWNET)`（ctypes，当前特权下调用——wrapper 外挂形态会在降权
  后 EPERM，实测修正）→ ② rlimits（NOFILE/NPROC/AS/CPU）→ ③ setuid nobody
  （root 时；目录 0755/文件 0444 预处理）；仓快照副本（原件零触碰）；隔离失败
  逐项降级并在 `SandboxResult.mitigations` 如实记录（`not-isolated`/`caller`）。

### 2.2 红线落档

`SandboxConfig.redline_report()` 逐条机器可检（network/user/read_only_root/
tmpfs/mem/cpus/nofile/nproc/timeout/docker_sock_mounted=False）——验收卷 A 组。

### 2.3 env 缓存（§16.4）

env 身份 = `env_key_for(base, deps)` = blake2b(base+排序依赖)[:16]；
container 档 = docker 镜像 `qswarm-sb:{key}`（依赖非空时 `docker build`，本地已有
即复用）；process 档 = `{env_cache_dir}/process/{key}/sitedeps`
（pip install --target + `.qswarm-env-ok` 标记）。构建失败 → `EnvBuildError`。

### 2.4 verify_error 四分类落点（§6.3）

runner 侧（`classify_result`）：env 构建失败 → `env_build_fail`、墙钟超时 →
`timeout`。编排层：同 PoC 重执行结果集 >1 → `flaky`；其余执行失败 → `poc_fail`。

## 3. PoC 生成与 harness（G2）

### 3.1 harness 契约（PatchEval 参照）

PoC = 可独立运行 Python 脚本；`REPO`=被审仓库只读副本、`WORK`=可写目录；断言
逐条以行 `ASSERT|<名称>|PASS|<详情>`（或 FAIL）打印 stdout；exit 0 = 断言全过。
内核解析 ASSERT 行回填 `VerifierReport.assertions`（log 由派单方回填真实
artifact 引用）；无 ASSERT 行按退出码合成单条断言。

### 3.2 生成双源（D39）

- **verifier 蜂一体产**：单次响应 = ```json VerifierReport``` + ```python PoC```
  双围栏（`extract_poc` 取最后一个 python 围栏，E3 同形纪律）；
- **家族模板兜底**（`template_poc`，零 LLM）：v0 覆盖 python 注入（双级驱动：
  argv 直跑 → import 驱动〔dup2 捕获 fd1 + sys.argv 补丁 + 拼接/直传双 payload
  变体 × 遍历模块公共函数〕）与 python authz/config_dep 路径穿越（金丝雀读）；他族
  如实返回 None → 编排计 `template_unsupported`，不硬凑。

### 3.3 前置离线试跑（G2 判据）

`python -m qswarm.sandbox.trial --db … --store … --repo … [--consult-run …]`：
voted 簇 → 模板 PoC → process 档真执行 → 与 `expected_verdict` 比对（`match`
列）。期望源双查：预裁决单工件（kind=pre_verdict，**身份口径 = M4 实装的精聚类
锚 finding id，C 簇 id 查不中时按锚 id 兜底**）→ 缺单回退 `clusters.verdict_dir`
投影（expected_source 如实标注 s1_projection）。**零账本写**；样本判读如实记档
不外推。2026-10-21 实跑：E 组 smoke 2 voted 簇（render_tool.py 命令注入）→
verified_real × 2，match 2/2（`out/m5-trial/`）。

## 4. run_verify 生产编排（G3）

### 4.1 入口（D40-b 独立排程）

`kernel.verify.run_verify(ledger, *, repo_root, run_id, consult_run_id=None,
mode="pr", seed, adapter, store_dir, tier=None, runner=None, …)`：

- 选取面 = 全员 voted ∧ 无 verify_results 行的簇（`eligible_clusters`；
  consult_run_id 可限定消费轮）；排序 = `s2_rank_key` 降序（severity 主序 ×
  类型稀缺 × 置信，平手 cluster_id 字典序）；分批补验 `max_clusters`；
- 幂等 = 任务 id `vf:{cluster_id}`（已验簇天然排除）。

### 4.2 D43 双档落账口径（第一纪律）

- `tier="container"`（`PRODUCTION_TIER`）：生产全链——verify_ready → open_run
  （独立预算档）→ create_task/dispatch → 蜂调用 → 沙箱 → submit()（VR 行 +
  verify_result 事件 + §2.4 映射 + propagate_verdict，全走 dispatch 既有写口）；
- `tier="process"`：**零账本写**（不 open_run/不建任务/零表行零事件/不写
  artifacts 索引），只产工件文件（`<store>/trial/<cluster>/`）与报告；
- `assert_production_tier` 机器可检（非 container 触碰生产面即 ValueError）。

### 4.3 蜂调用与重试

verifier 池 least-loaded；蜂调用失败（schema/供给）→ 计 `bee_schema_fail` →
模板兜底（fail-soft，不硬凑重试）。`VerifierReport.attempts` 语义 = 同 PoC
沙箱执行次数（flaky 判据重执行计入）。

### 4.4 reconciliation（内核 owned；纯函数 `reconcile`）

蜂主张 × 沙箱实证：无 PoC → verify_error(poc_fail)；env_build_fail/timeout →
直判；执行绿 → real（或 bee=None 试跑面纯执行语义）／fp 不升格／error 如实保留；
执行红 → fp∧blocking 维持 verified_fp（§10.4 正面证据独立于 PoC 绿）／flaky →
verify_error(flaky)／其余 → verify_error(poc_fail)。verified_fp 无 blocking 由
submit 的 §10.4 强制改判兜底（双层守卫不重复实现）。

### 4.5 预算（R7-①）

独立预算档 `verify.budget_cents`（不与 consult 共享 run 预算）；quota_s2 存活数
= **active verifier 实编**（quota_l2 代理在独立入口下不成立）；
`quota_s2(len(active_verifiers), remaining, COST_PER_S2_CENTS)`；熔断沿用
budget_state/on_cost 既有件。

### 4.6 成本披露（R7-②）

报告 `sandbox_disclosure`：sandbox_wall_s / env_build_s / sandbox_runs + 口径
注记——沙箱轮非 LLM 资源成本与 D33 双轨（LLM 名义价）分列。

## 5. D34 仲裁件（G4，D42-a 最小仲裁件）

`kernel.arbitrate.run_arbitrate(ledger, *, cluster_ids, run_id, repo_root,
adapter, store_dir, …)`：人工触发；簇全量证据块（复用 consult._cluster_block）→
queen_advisor 调用（渲染 ③④ 段随 dispatch ROLE_* 表 advisor 行落位）→
ArbitrationDraft 解析 → 摘要 + 草稿两工件（kind=arbitration；summary_ref 由
pending 回填真实引用）+ tasks 行 + DISPATCH 事件（审计锚）+ close_task_empty
收口（零业务行零存款——建议权，非 verdict 非存款，不入生产口碑）。幂等 =
`arb:{run_id}:{cluster_id}`；解析失败 → schema_fail、任务留 dispatched（§4.3
退回面）；未知簇 → skipped_unknown_cluster。全自动触发（D42-b）推迟 S2 数据
积累后另拍。

## 6. 顾问接线契约面（dispatch ROLE_* 表）

`ROLE_TOOL_BOUNDS["queen_advisor"] = ("finding:ro", "artifact:ro")`、
`ROLE_TASK_BOUNDS["queen_advisor"] = "只按注入的簇材料起草结构化仲裁摘要建议稿；
…（仅建议权，§16.7-9）"`——SPEC-3 §6/E7 预告的落位。`build_contract` 对
queen_advisor **仍显式拒绝**（同一「未知派单角色」守卫：顾问永不进派单状态机，
建议任务由 arbitrate 直装 DispatchContract）。

## 7. 暗考接线（G5）

`exams.dark.make_dark_exam(ledger, *, bank, adapter, seed, …)` →
promote_scan 的 ExamRunner 协议回调：family 取 node 家族段 → `run_entry_cell`
（出卷/调用/判分/行落库既有面）→ `promotion_exam_passes`（§8.2 及格线，params
PROMO_REAL/PROMO_MIN_REJECT_FP）→ DarkExamOutcome(passed, exam_id)；
seq_promotion=None（candidate→active 事件由 promote_scan 落——回调只考试不写格）。
考试故障 → None（fail-open 不误杀）；考试存款幂等使暗考不重复写存款（§8.2
防污染）。接线点 = `loop.run_scan` 尾部 promote 块：`DARK_EXAM_ENABLED=True ∧
bank 注入`双前提（默认关 = 「开启归 D35 批准面」机器化）；ScanLoopReport
增 `dark_exam_enabled` 标志。

## 8. G6 遗留清账（E 组补录 §9.5 / HANDOVER §5 / M4 判据缺口）

| 项 | 处置 |
|---|---|
| rules_runner 生产显式装配 | run_scan：`rules_runner=None ∧ rules.enabled` → `default_rules_runner(params.perception.rules.timeout_s)`（D30 必选件不再静默缺席） |
| rule 候选路径归一 | channel_c_scan：repo_root 前缀剥离 + `./` 归一（Candidate.path 与 A/B 通道同为相对 posix 形；repo_root 形参 M4 已收未用，本轮兑现） |
| pv_skipped 判据复核 | 三态钉死（测试）：空池 → pv_skipped；预算尽（quota_s2=0，与面板同门控）→ pv_budget_skipped；墙钟尽 → pv_skipped。与 §2.7「预算尽停付费调用」一致 |
| taxonomy authz types 面补齐 | `authz += path_traversal`（bank v3 实收题型 CWE-23/36）；`injection += command_injection`（bank v3 实产 type 标签，smoke 实证）；M0 effective_M 推导值随之更新（扇出 injection 5→6、authz 3→4） |
| 存款值版本化刷新 | `exam_deposit_net`（格考试存款带符号净值）+ 重考补偿存款（方向=补偿方向、权重=净差幅值）——§6.2 append-only 不回滚，cells 聚合器零改动（M0 黄金逐位不受影响）而净值收敛到最新重考结果；幂等按收敛判定 |
| 注入防线显式断言 | render 对含伪造分隔行切片内容附 INJECTION_SUSPECT_NOTICE（第三条 system），测试显式钉 |
| M4 缺口① 墙钟停机判据 | run_consult / run_verify 增 `monotonic` 注入形参（缺省真实时钟零行为差），测试机器可检触发 |
| M4 缺口② e2e 位级幂等 | 重跑对照升级为账本事件流逐位 payload 对照（snap1 == snap2） |
| M4 缺口③ repomap 缓存复用 | 同 cache_dir 重跑：缓存文件集（name,size,mtime）零增长测试 |
| M4 缺口④ 注入断言 | 同上第 6 行 |

## 9. 门禁与验收

验收卷 = `SPEC-5_m5_acceptance.md`（A 沙箱红线 / B 配置与词表 / C run_verify
双档与 reconciliation / D 已知答案试跑 / E 仲裁·暗考·清账）；全量 pytest
754 passed（712 基线零回归 + 42 新增）。写口审计（scripts/audit_write_ports.py
--strict）维持 PASS：本里程碑零新表、零新账本直写（kernel.verify /
kernel.arbitrate 全走 dispatch 既有写口）。
