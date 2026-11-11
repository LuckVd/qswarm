# R1 语义变更说明与 T0–T8 执行清单（r1-hardening）

日期：2026-10-28 · 授权：负责人「按这个开始做」（对 HANDOVER_GLM_R1_HARDENING_20261028.md）
· 基线：HEAD `6e32fb3` + P1 未提交工作区（基线指纹 `out/r1-hardening/r1-20261028T141250Z/baseline.json`）
· 本文是 §2.1 要求的**短变更说明**：旧语义、新语义、事件/工件版本、兼容策略、
受影响测试、迁移边界。本说明不倒填批准——所有变更以本文件日期起生效，
属 R1 轮显式行为修订（对 SPEC-10 §8「双层结果只加报告不改 outcome」与
SPEC-9 §4.5 reconciliation 语义的**显式偏离**，偏离理由即 HANDOVER §0）。

## 0. 执行清单（T0–T8 × 批次 R1-A→D）

| 批 | 任务 | 交付面 |
|---|---|---|
| R1-A | T0 红测 + T1 执行边界 + T2 可信写入门 | tests/test_r1_*.py（先红后绿）；sandbox runner/envcache/config；kernel/trust.py（新）；verify/dispatch/cluster 接线 |
| R1-B | T3 身份/缓存/恢复 + T4 路由准入 | loop/consult/dispatch/candidates 身份面；indexer 缓存；route 池约束 |
| R1-C | T5 指标纠错 + T6 校准材料 v2 | evidence v3；experiments/design_revision 校准 v2 |
| R1-D | T7 打包 + T8 总验收 | pyproject/资源读取；干净安装验收；R1_HARDENING_DELIVERY 报告 |

## 1. 逐任务语义变更

### T1 执行边界（sandbox.runner/envcache/config）

| 面 | 旧语义 | 新语义 |
|---|---|---|
| process 档输入信任 | 任意 poc_script/repo/deps 均执行 | repo 必须落在**宿主侧可信根**内（打包 fixtures 目录 + 配置显式 trusted_roots），PoC 进入执行前校验；越界 → `UntrustedExecutionError`（ValueError 族），**先于 pip/copytree/Popen** |
| 依赖安装 | `build_process_env` 对模型 deps 字符串直接宿主 `pip install --target` | ① deps 字符串过 `validate_deps_spec` 拒绝矩阵（raw URL/VCS/local path/pip 选项/换行/空段全拒）；② process 档缺省**拒绝运行时安装**——仅复用已预置 env（marker 在），需安装即拒；③ container 档 `docker build` 需显式 `allow_build`（缺省 False），base 镜像本地缺失 → EnvBuildError（**不联网拉取**） |
| 子进程环境 | `dict(os.environ)` 全量继承（API keys/PYTHONPATH 等全入） | 最小 allowlist：PATH/LANG/LC_*/TZ/TMPDIR + runner 注入的 REPO/WORK/PYTHONPATH（仅指向预置 sitedeps，宿主控制值）；假密钥测试钉死不透传 |
| 输出限额 | `communicate()` 全量读入内存后 `_cap` 切片（事后） | 读取时上限：每流至多 OUTPUT_CAP_BYTES+1 字节即停读并收割**进程组**（`start_new_session` + `killpg`）；超限 → 有界诊断 + 不算成功 |
| 隔离失败 | unshare/降权失败如实降级记录后照跑 | `process_require_isolation=True`（缺省）时禁网探针失败 → 拒绝执行（fail-closed）；弱隔离 process 显式标 dev-only（配置 opt-out），不构成生产降级入口 |
| 超时清理 | `proc.kill()` 只杀直接子进程 | killpg 收割后代；清理带超时；清理失败不返回成功 |

### T2 可信验证控制提交/晋级/奖励（新 kernel/trust.py + verify/dispatch/cluster）

| 面 | 旧语义 | 新语义 |
|---|---|---|
| verified_real 判定 | reconcile：执行绿 + 蜂报 real（或无蜂主张）→ verified_real → 全员 L3 + S2 存款 | 高可信 outcome（real/fp）须经**受信授权**（`AuthorizationProof`，kernel/trust.py 铸造）：绑定 policy_version/run/task/cluster/execution_profile_id/repo_key/snapshot_digest/target_digest/poc_digest/oracle_kind+version/observer_version/对照与健康结果/证据引用+摘要/允许 outcome。无授权 → 规范化为 verify_error（error_kind 用既有保守类，oracle reason=`no_trusted_authorization` 如实记），零 L3 零 S2 |
| 授权来源 | （不存在） | 仅内核编排铸造（`mint_authorization`，摘要由宿主对实际工件计算）；模型同名 JSON/布尔/artifact:// 字符串**不构成授权**；submit 侧只做复核（重算摘要/任务匹配/policy 版本/证据引用在库且哈希一致） |
| submit(verifier) | 任意调用方可直接 submit VerifierReport(VERIFIED_REAL) | outcome∈{real,fp} 时 submit 必须携带有效 `authorization`，缺失/失配/未知 policy → ValueError 拒写（唯一写口内强制） |
| propagate 补偿 | propagate_pending 见旧 verify_result(verified_real) 即补 S2 存款 | S2 派生与提交同一 eligibility：源事件无授权标记（新事件 payload 增 `authorized`+`auth_digest` 字段，**旧事件无此字段 → 恒不补发**）；propagate_verdict(tier=S2, source=VERIFY_RESULT) 同验 |
| paired 判据 | 驱动 exit 0 即当「修复版健康」；目标缺依赖退出 1 也可成有效负对照 | 分立记录：driver 状态/目标确实执行/初始化依赖健康/合法输入控制/攻击终态/违规观察/观察器完整性；修复版可 rc=2 拒恶意输入但须过合法控制；缺依赖/未调用/无健康证明不通过 |
| mechanical_single | template + 冻结断言 PASS → supported（报告面） | supported **不自动等于成对独立验证**：不授权 verified_real（决策表 §6.3 行 3）；单次支持仅报告面 |
| 观察器边界 | 驱动自建 obs 目录、同 uid 可伪造 | process 对照显式限定 trusted-fixture instrumentation；观察路径限本次运行私有根、拒越界/symlink/过大文件；无权限/命名空间分离 → 如实「不具备对抗性生产 oracle」，停在安全无结论 |

### T3 身份/缓存/恢复（loop/consult/dispatch/indexer）

| 面 | 旧语义 | 新语义 |
|---|---|---|
| 候选身份 | `candidate_id = blake2b(path,span,kind,clue)` 裸定位键直接作表主键，跨 run/跨仓冲突 | 三身份分立：`local_locator_key`（既有纯感知 hash，保留）→ `candidate_occurrence_id = H(identity_version, scan_run_id, local_locator_key)`（入表主键）→ `exact_snapshot_tracking = (repo_key, snapshot_digest, local_locator_key, file_blob_hash)`（跨 run 去重观察面，不作根因 ID） |
| L1/L2 task_id | `S-<shard_id>:<seq>` / `L2-<cand[:12]>:<seq>` 不含 run → 第二 run 同仓任务被幂等跳过吞掉 | `task_id = H(stage, identity_version, run_id, candidate_occurrence_id [, source_scan_run_id])` 派生，跨 run 各自存在 |
| run 输入冻结 | open_run 只存 mode/budget；换仓/改源码无感知 | `runs.meta_json` 版本化输入 manifest（identity_version/repo_key/snapshot_digest/scope/参数与规则·解析器·prompt·profile 版本）；同 run manifest 变化 → 新写入前拒绝要求新 run；consult 消费源 scan 候选前核验源快照 |
| register/submit 守卫 | 同 id 字段不等才拒；不核 owner run；scout submit 只查候选存在 | register 核 owner run + 不可变输入冲突；_submit_scout 核 output 候选 = task 预期候选 ∧ 属该 scan run；显式 run_id 与 task 归属不符 → 拒 |
| 索引缓存 | key=blake2b(bytes)；同 bytes 不同 path 返回旧 path 实例 | key = H(cache_version, lang, parser/grammar 指纹, bytes)；命中返回**重绑当前 path/lang** 的新实例；旧缓存版本视为 miss |
| 恢复 | task 行存在即跳过（跨 run 误吞） | done→读持久化输出；queued→复用原任务；dispatched→按剩余 attempts；failed→终态不静默重跑；同 run 幂等覆盖 L0 调用/L1/L2/finding/面板/费用/存款 |

### T4 路由准入（route/consult）

| 面 | 旧语义 | 新语义 |
|---|---|---|
| 显式 pool | 只约束探索通道；专员通道仍取全量 active cells（pool=[] 可选中池外专员） | 显式 pool = 本次调用**完整可派范围**：专员/探索/平手/重试全过滤池内；pool=[] → assignee=None(reason=empty_pool)；pool=None → 旧推导语义不变 |
| 资格 recheck | 仅 run_verify 单点 | 启用资格门角色的最终派单与改派前 recheck；撤销/profile 变化 → 零调用，reason 区分 empty_pool/qualification_filtered/revoked_midrun |
| model_ref | `_by_ref` 失配返回空串继续调用 | 空串 → 拒绝并显式失败分类，不记普通 infra |

### T5 指标与真值（evidence → v3）

| 面 | 旧语义 | 新语义 |
|---|---|---|
| precision 总体 | 正向报告与正确拒判混入同一 TP/FP 池（1 错误正向 + 9 正确拒判 → 0.9） | population 先行分立：正向上报 precision = TP/(TP+FP)（TP/FP 仅出自正向报告集）；正确拒判另列 correct_rejection_rate；verify_error 不进 precision 分母也不从覆盖/成本面消失 |
| direction=0 | human_verdict direction=0 → truth_label="refuted" | direction=0 = 忽略：不产生真值记录，不与既有正标签造 disputed |
| 富化 as_of | findings 表当前态富化（未来 finding/merge 改历史投影） | 富化改自 append-only FINDING 事件按 as_of 重建；同 as_of 同输入 → 同哈希（未来事件零影响） |
| 版本 | capability_v2 | **capability_v3/evidence_v3**（新文件名+policy 字段）；v2 历史工件不动，E0 runner/测试随升 |

### T6 校准材料 v2

旧 25 题冻结不动；新增 v2 材料：题面去「漏洞版/修复版/安全版/预期判读」与类别号/路径泄题标记、docstring 泄漏清除、root-cause group 显式登记（1/7/8/9 类同源不重复计）、真值映射单一来源（TRA/PAT 不一致修复）、compute_agreement 边界输入（零判题/空文件/零交集/零分母 → not_estimable 而非 KeyError/除零）。本轮只交付机械验收，不执行裁判（D46 红线维持）。

### T7 打包

pyproject packages 补 exams/prompts/supply/sandbox；默认资源（schema.sql 已然）+ params/taxonomy/semgrep_rules 走 importlib.resources 打包读取（包内 canonical 副本，与 configs/ 冻结件**哈希同步测试**钉死不漂移）；显式路径优先且不存在即报错；semgrep 外部子进程用覆盖生命周期的临时资源路径；wheel 清单不含密钥/DB/缓存/out。核心安装与 perception extra 两档说明。

## 2. 事件/工件版本

- 新增 `policy_version = "r1-tv1"`（信任策略，kernel/trust.py 常量；判据/授权结构变更须升版）。
- `VerifyResultPayload` 增可选字段 `authorized: bool = False`、`auth_digest: str | None = None`
  （**additive**：旧事件无字段读取时取默认；新事件实写。旧事件 JSON 字节零改动）。
- 证据投影升 **evidence_v3 / capability_v3**（新文件名 + policy 字段；v2 为已勘误历史口径）。
- 校准材料 v2（experiments/design_revision/calibration_v2/；旧 25 题原样冻结）。
- oracle 判据 oc-v1 → **oc-v2**（paired 健康分立 + 观察器边界为判据公式变更，须升版）。

## 3. 兼容策略

- 旧账本/旧工件/旧黄金/M0 黄金零触碰；旧 verify_result(verified_real) 事件**保留原义**，但
  propagate_pending 不再对其补发新 S2 存款（§12.2「历史重放 ≠ 现在补奖」）。
- process 档弱隔离模式 = dev-only（显式配置），不构成生产降级入口；D43-a「process 零账本写」维持。
- 旧 candidates/tasks 行不迁移：新 run 起新身份形态入库（内容寻址 id 自明出处），legacy 行只读。
- 显式传参缺省值全部保持旧行为的调用面（route pool=None / gate 缺省 None / enforce=False）。

## 4. 受影响测试（语义变化清单）

- `tests/test_m5_sandbox.py`：process 执行边界收紧后，直执不可信 repo 的用例改走显式可信根；
  模板钉值随 oc-v2；container 真机项 skip 条件修为 **daemon 探针**（CLI 在 ≠ daemon 达；
  开发可选项，skip ≠ 红线验收 PASS）。
- `tests/test_m5_verify.py`：container 全链（无授权）→ verify_error 保守面（零 L3 零 S2）+
  新增 `test_container_tier_authorized_real_full_chain`（授权放行正向链）；fp+蜂自报
  blocking → 保守无结论；process 已知答案比对改 `outcome_raw`/`trust_gate` 面。
- `tests/test_kernel_dispatch.py`：verifier submit real/fp 用例经 m1_harness 正规铸造授权。
- `tests/test_kernel_cluster.py`/`test_kernel_replay*.py`/`test_replay_equivalence_m1.py`：
  S2 源播种改带授权形态（`append_authorized_verify_result` 辅助）。
- `tests/golden/cluster/`：**按官方重冻路径**（`scripts/freeze_golden.py --cluster`）重冻——
  场景 S2 源升带授权形态（golden_loader 播种 verify_auth 工件行，**不豁免新门**）；
  存款展开期望语义不变（002–004 deposits 15/17/17 保持）；004 含一条未授权 vr
  双面钉（ ineligible 不补发）。
- `tests/test_kernel_route.py`：pool=[] 专员越池用例反转。
- `tests/test_evidence_projection.py`：v3 断言（precision 分母、direction=0、as_of 事件富化）。
- `tests/test_config.py`：sandbox 节新四键入 R1 钉值表（缺省全 fail-closed）。
- `tests/test_m2_*` 考试/播种与其余内核基线不受影响（预期零回归）。

## 5. 迁移边界

- **零 DDL 变更**（不新增表/列；occurrence 身份经内容寻址 id 进既有 TEXT 主键；manifest 进
  runs.meta_json 既有列；授权经 artifacts 既有 kind 面落新 kind 值——write_guard 词表为 kind 字符串
  白名单时同步审计注册）。
- 生产库零触碰：所有迁移/重放仅在临时库 dry-run；不自动 apply。
- 超出本说明的生产策略/DDL 需求：单独列差异申请，不夹带。
