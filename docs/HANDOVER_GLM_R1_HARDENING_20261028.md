# GLM 下一轮交接：R1 正确性、安全边界与可交付性修复

日期：2026-10-28（Asia/Hong_Kong）  
工作目录：`/opt/projects/qswarm`  
交接对象：下一轮执行开发的 GLM  
本轮名称：**R1 hardening**。不要与既有 P0/P1/P2 或 E0/E1/E2 实验阶段混用。

## 0. 任务摘要与授权边界

负责人希望继续项目，但先交付可信的实验版本，再决定是否扩大蜂群架构。本轮只修正确性、安全和交付问题；不证明蜂群优越，不开展付费效果实验。

你需要交付：

1. 同一账本能处理不同运行、不同仓库，并正确恢复已完成工作；不串候选、路径、任务或谱系。
2. 可信证据约束验证结论、L3 晋级和 S2 奖励，提交与恢复补偿不能绕过约束。
3. 不可信 PoC、目标仓库和依赖不能进入宽松宿主执行路径。
4. 评估工具计算正确，校准材料不泄漏答案，历史与新版结果分开留档。
5. 项目能从非 editable 安装包运行，不依赖源码目录碰巧存在。
6. 提交可复核的测试结果、失败项、环境限制和下一阶段申请材料。

**本文是任务安排与修订建议，不是已生效 SPEC，也不是生产上线批准。** 负责人后续要求“按本文执行”时，你可以进行本文列明的离线代码、测试和配套规格修复；实际生产库变更、生产启用、联网安装与镜像下载、付费模型调用仍须单独获得明确授权。只收到“阅读/评估”时，不开始实现。

默认约束：

- 线上模型调用预算为 **0**，不读取或使用本机密钥，不把现有订阅视为无限授权。
- 不补考、不换卷刷过线、不降低 QC 线、不裁决 D44/D47/D48。
- 不运行真实外部漏洞 PoC，不下载未知依赖，不执行未知仓库的构建或测试脚本。
- 使用临时数据库、FakeAdapter、纯逻辑测试和经过核验的本地受控夹具。
- 本轮不新增自动晋升、衰退、弹性编制、bandit、组织界面或语言覆盖。
- 不为凑单模型实验而注册假蜂，不降低现行生产 readiness 门槛。
- 安全前提未满足时，保留候选和人工复核入口，暂停自动高可信确认及奖励。

## 1. 接手基线：先保全当前工作区

### 1.1 代码与未提交变更

2026-10-28 核查的 HEAD：

```text
6e32fb3082b105539ac805f850c08db292aa2d45
```

工作区并不干净。既有 P1 实现、文档、夹具和测试有大量未提交内容。本轮审查没有修改这些源码。**不要 reset、clean、覆盖、移动或批量提交既有改动。** 交接时以实时状态为准，不只依赖 HEAD。

已知 tracked 修改：

```text
.gitignore
docs/spec/DECISIONS.md
docs/spec/README.md
docs/spec/SPEC-10_evidence_and_evaluation.md
docs/spec/SPEC-9_m5_sandbox.md
qswarm/kernel/verify.py
qswarm/sandbox/pocgen.py
qswarm/sandbox/runner.py
tests/test_m5_sandbox.py
```

已知 untracked 内容至少包括：

```text
docs/p1_assessment_20261024.md
experiments/design_revision/
out/_p1_final/
out/design-revision/
qswarm/kernel/evidence.py
qswarm/kernel/funnel.py
qswarm/sandbox/fixtures/
qswarm/sandbox/oracle.py
tests/test_evidence_projection.py
tests/test_funnel_trace.py
tests/test_s2_oracle_controls.py
```

本交接文档也属于新增文件。接手后记录 `git status`、HEAD、tracked diff 和本轮相关 untracked 文件清单/哈希。不要收集密钥内容、全量响应缓存或整库私有数据。若需要提交，先分清原有变更与本轮差异；禁止直接 `git add .`。

### 1.2 本次已执行的检查

以下结果来自 2026-10-28 审查，不是下一轮完成后的验收结果：

| 检查 | 结果 | 能说明什么 |
|---|---|---|
| 全量 pytest | **805 passed，1 failed，349.97 秒** | 大部分既有回归通过；不能声称当前 806 全绿 |
| 唯一失败 | `test_container_runner_real_docker_confirm`，读取缺失的 network mitigation 时 KeyError | 当前 container 真机验证未成立 |
| Docker 探针 | CLI 存在；未配置 DOCKER_HOST；默认 socket 不存在，API 连接失败 | 环境不具备当前容器验收条件；不是已证实 PoC 逻辑失败 |
| 写口审计 | `python -m qswarm.kernel.write_guard --strict` PASS | 未发现该静态审计定义内的写口违规 |
| wheel 检查 | 缺 exams/prompts/supply/sandbox；禁用 editable hook 后导入失败 | 源码目录可导入不等于安装包可用 |
| precision 最小反例 | 1 条错误正向上报 + 9 条正确拒判，输出 0.9 | 当前“上报 precision”口径错误 |
| L1 隔离复现 | 第一仓 4 注册/4 处理；第二仓 3 注册/0 处理 | task 身份跨 run 冲突会吞掉新任务 |
| 索引缓存纯内存检查 | 同 bytes 的 a.py、b.py 返回相同旧 path | cache 混合了共享解析产物与文件实例 |
| route 纯逻辑检查 | 显式 pool=[]，仍可能选 active cell 专员 | 专员通道未受显式候选池约束 |

不要为复现环境失败改动本机其他项目、监听器、Docker daemon、全局挂载或权限。历史 WSL 桥接配方不等于当前获准使用。

## 2. 必读材料与本轮决策

按以下顺序阅读，之后按函数名重新定位；本文行号只作接手锚点。

- [README](<../README.md>)、[打包配置](<../pyproject.toml>)。
- [设计调整方案](<DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md>)：保留“先专长、后路由、再组织”的顺序。
- [P1 交付报告](<../out/_p1_final/P1_DELIVERY_REPORT.md>)、[P1 评估](<p1_assessment_20261024.md>)。
- [SPEC 索引](<spec/README.md>)、[SPEC-1 数据契约](<spec/SPEC-1_data_contracts.md>)、[SPEC-2 内核](<spec/SPEC-2_kernel.md>)。
- [SPEC-7 感知](<spec/SPEC-7_perception.md>)、[SPEC-8 会诊](<spec/SPEC-8_m4_consult.md>)、[SPEC-9 沙箱](<spec/SPEC-9_m5_sandbox.md>)、[SPEC-10 证据与评测](<spec/SPEC-10_evidence_and_evaluation.md>)。
- [决策记录](<spec/DECISIONS.md>)，尤其 D46 与 P1 兼容条款。

### 2.1 规格冲突要显式处理

[SPEC-10:215–224](<spec/SPEC-10_evidence_and_evaluation.md#L215-L224>) 和 [337 行](<spec/SPEC-10_evidence_and_evaluation.md#L337>)明确规定 P1 双层结果只加报告，不改旧 M5 outcome。下一轮让可信判据控制生产结论，属于**明确的行为修订**，不是“原行为不变的小接线”。

先写短的 R1 变更说明：旧语义、新语义、事件/工件版本、兼容策略、受影响测试、迁移边界。负责人已要求按本文实施时，可据此修订对应规格；不能倒填“此前已批准/此前已完成”。需要超出本文的生产策略或 DDL 时，单独列差异并申请。

### 2.2 本轮采用的保守默认

- 新生产路径缺少可核验可信证据时，不自动 real、不自动 fp、不产生 S2 奖励。
- `mechanical_single + supported` 不自动等于成对独立验证，更不自动授予 L3。
- 旧账本、旧工件、旧黄金数据保留原样。旧的宽松逻辑只允许明确隔离的离线历史重放/仿真使用。
- 新运行使用版本化输入身份；同 run 输入发生变化，拒绝混用，要求新 run。
- 第一版采用单协调器恢复语义，不建设分布式调度器或 lease 系统。
- 优先复用现有 metadata 与不可变工件，不为本轮建立一套新生产数据库。
- 不可靠的跨 commit 根因关系记 unknown，不通过新 occurrence 数量膨胀学习样本。

## 3. 任务顺序、优先级与交付批次

| 批次 | 任务 | 必须先完成什么 | 退出条件 |
|---|---|---|---|
| R1-A | T0 基线/红测 + T1 执行边界止血 + T2 可信写入门 | 接手盘点、短变更说明 | 无授权不可晋级/奖励；不可信 process 在执行前拒绝 |
| R1-B | T3 身份/缓存/恢复 + T4 路由准入 | T0；不得依赖真实模型 | 同 run 幂等、跨 run 不丢任务、恢复谱系正确、专员不越池 |
| R1-C | T5 测量纠错 + T6 校准材料新版 | T2 的来源/版本契约确定 | 指标与标签反例通过；校准工具健壮、无泄题 |
| R1-D | T7 打包与干净安装 + T8 总验收 | 前述批次 | 可安装、离线闭环可重放、完整报告 |

执行顺序以此为准。独立的打包或纯指标测试可以并行；涉及提交写口/事件序列化/身份契约的改动由一个主执行者合并，避免两个代理各建一套版本规则。每批先补失败测试，再实现，再运行受影响测试。

## 4. T0：基线与反例测试

### 4.1 先记录，不先修到全绿

创建独立交付目录，例如：

```text
out/r1-hardening/<run-id>/
  baseline.json
  manifest.json
  logs/
  reproductions/
  reports/
  package-check/
```

使用不覆盖历史工件的新路径。记录代码 HEAD、工作区指纹、Python/依赖版本、命令、开始结束时间、退出码、测试数、网络和 Docker 能力。输出脱敏，不保存环境变量值。

先运行能定位问题的小测试，再决定是否重跑完整基线；不要反复支付全量测试的时间成本。此前 805/1 是参考，不必假装本轮又测了一遍。

### 4.2 第一批红测

至少先写出以下失败断言：

- 无可信授权直接 submit verified_real，不能改变 grade、生成 S2 存款。
- 通过 propagate_pending / propagate_verdict 补偿，不能绕过同一证据门。
- 同库第二个 run 的新候选不会因第一轮同名任务跳过。
- 同 bytes 不同 path 的索引返回当前 path。
- 1 错误正向上报 + 9 正确拒判，正向上报 precision 为 0。
- human_verdict 的 direction=0 不成为 refuted 真值。
- 校准集每个 ID 都有匹配映射；零判题/零交集不除零。
- 不可信 process 请求在 pip/Popen 之前被拒；子进程不继承假密钥环境变量。

每个问题提供最小 fixture 和预计断言，不依赖线上输出。Mock 只能证明边界调用和纯逻辑，报告中不能把它写成容器隔离真机证明。

## 5. T1：先收紧执行边界

### 5.1 代码落点

- [ProcessRunner](<../qswarm/sandbox/runner.py#L284-L337>)。
- [环境构建](<../qswarm/sandbox/envcache.py#L110-L139>)。
- [模型预裁决依赖进入执行路径](<../qswarm/kernel/verify.py#L638-L649>)。
- [SandboxConfig](<../qswarm/sandbox/config.py>) 与 [trial CLI](<../qswarm/sandbox/trial.py>)。

### 5.2 必须实现

1. **划分可信开发夹具与不可信执行。** process 仅允许已核验的固定夹具/开发输入；不可信目标仓库同样算不可信代码，即便 PoC 来自冻结模板。不能只检查模型可填的 `poc_source="template"` 或 `trusted=true`。
2. 在执行依赖安装、复制目标并执行、Popen 之前校验信任来源。自动生产路径不允许 process；container 不可用时拒绝执行，不偷偷降档。
3. 默认拒绝 process 运行时安装 deps。本轮允许使用预先准备的可信环境；不要对模型的 requirements 字符串执行宿主 pip。
4. 容器构建与容器运行分开建威胁模型。优先已批准的固定镜像；未获授权不拉镜像、不联网构建、不安装未知包。raw URL、VCS、local path、pip 选项、换行及未审批包拒绝进入自动安装路径。
5. 子进程环境使用最小 allowlist。明确必要的 PATH、locale、REPO、WORK；不复制 API keys、云凭据、代理凭据、PYTHONPATH/PYTHONSTARTUP。测试使用假敏感变量，不读取真实秘密。
6. 严格执行路径所需的禁网、降权、只读和资源限制失败时 fail-closed。可信 fixture 的弱 process 模式如保留，要标成开发专用，不提供生产降级入口。
7. 输出限额必须在读取时生效。stdout、stderr、合并日志、构建日志、工件都要有上界；事后切片不能防宿主内存耗尽。达到限额时终止进程组/容器，保留有界诊断，不能算验证成功。
8. 超时/取消/异常负责回收后代、容器、fd 和临时资源，清理本身要有超时。不因为清理失败继续返回成功。

不要把“Docker 隔离”写成绝对安全保证。使用同内核容器仍有残余风险；本轮要说明支持的威胁模型。

### 5.3 验收

- Mock 安装/Popen：不可信输入拒绝后，两者调用次数为 0。
- 假 secret 不进入子进程 env；必要 fixture 环境由显式配置注入。
- 依赖字符串拒绝矩阵有测试，失败不执行宿主构建代码。
- 非 UTF-8、双流输出、分块输出超限、超时、清理异常都有界且不成功。
- 生产 container 缺失时零降级、零 PoC、零高可信结果。
- 受控容器真机检查只在环境可用且执行范围获批后运行。daemon 不可达记环境阻断，不把 mock 结果代替真机红线。

## 6. T2：可信验证控制提交、晋级和奖励

### 6.1 不要只改 run_verify

必须一起检查：

- [reconcile 与编排](<../qswarm/kernel/verify.py#L395-L439>)、[oracle 接线及提交](<../qswarm/kernel/verify.py#L776-L861>)。
- [verifier 唯一提交写口](<../qswarm/kernel/dispatch.py#L1086-L1209>) 及公开 submit 分支。
- [propagate_verdict](<../qswarm/kernel/cluster.py#L900-L968>)、[propagate_pending](<../qswarm/kernel/cluster.py#L993-L1053>)。
- [契约模型](<../qswarm/contracts/models.py>)、[账本序列化](<../qswarm/kernel/ledger.py>)、[证据投影](<../qswarm/kernel/evidence.py>)。

现在直接 submit 可以绕过编排；旧 verify_result 也可能被补偿函数重新生成 S2 奖励。只修报告、只修 UI 或只修第一次写入都不合格。

### 6.2 最小可信授权契约

由受信内核/观察器生成验证上下文，提交写口复核。至少绑定：

```text
policy_version
run_id / task_id / cluster_id
execution_profile_id
repo_key / snapshot_digest / target_digest
poc_digest
oracle_kind / oracle_version / observer_version
control and health results
immutable evidence references and digests
execution safety policy / observation time
allowed normalized outcome and evidence strength
```

这是进程内和持久化证据的信任契约，不要求建设公钥基础设施。**模型填一段同名 JSON、布尔开关或 artifact:// 字符串，不构成授权。** 工件存在也不代表工件来源可信。

缺失、未知版本、任务不匹配、snapshot/PoC/工件 hash 不匹配、不可核验来源：拒绝高可信写入。可信授权生成函数不能通过公开提交参数让模型自行构造。

先实现保守门禁即可：没有合格的容器观察/对照来源时，仍可输出候选、日志和无结论报告，但不能为了补齐正向链而构造“可信授权”。

### 6.3 决策表

| 情况 | 当前轮默认处理 |
|---|---|
| 模型报 real，脚本 exit 0，没有独立证据 | inconclusive / verify_error；零 L3、零 S2 奖励 |
| 只有模型自报 PASS 或白名单 ASSERT | 同上 |
| mechanical_single 声称 supported | 仅受限单次支持描述；默认不自动授予 paired 级高可信结论 |
| 成对结果 supported，但修复版健康/合法控制/来源独立性缺失 | inconclusive |
| 受信成对判据 + 完整健康控制 + 正确快照/授权 + 安全执行策略成立 | 按新版策略允许 real；测试必须覆盖实际唯一写口 |
| 模型报 fp，只有格式完整的 blocking 字段 | 不足以确认误报；保守无结论 |
| 独立 blocking 证据覆盖具体原主张的输入、环境、权限及可达性范围 | 按新版策略允许 fp |
| PoC 无效、没触发、超时、缺依赖、目标没调用 | 无结论；不得自动 fp |
| 无法解释/无法验证的历史 real/fp 事件 | 历史描述；不得借当前 repair 生成新高可信奖励 |

不得把未知都改成 FP，也不得把“无授权”静默吞掉。保存原始模型主张、规范化结论和具体 reason。现有 error_kind 不足时，可先使用既有保守类别并另记 reason；新增公共枚举必须先修订契约。

### 6.4 修正 paired 对照健康

[当前 paired 判据](<../qswarm/sandbox/oracle.py#L315-L404>)把驱动 exit 0 与目标健康混用。目标因缺依赖退出 1，驱动仍可能 exit 0；不能让它成为“修复版不触发”的有效负对照。

必须分别记录：driver 状态、目标确实执行、初始化/依赖健康、正常输入控制、攻击输入终态、违规观察、观察器完整性。

**不要简单规定 target rc 非 0 都无效。** 合法修复可以对恶意输入以 rc=2 拒绝；验收应证明它能正常服务合法输入，并按冻结契约拒绝攻击输入。目标缺依赖、空程序、未调用、缺少健康证明不能通过。

同一驱动/输入契约、漏洞和修复快照、对应关系必须匹配。交换样本、不同 PoC hash、拿另一个无功能程序当修复版都不能获得授权。

### 6.5 观察器独立性与边界

[现有观察器](<../qswarm/sandbox/oracle.py#L288-L345>)读取驱动给出的 OBS 路径和文件。独立 Python 函数不等于独立证据来源；同 uid 的脚本可以伪造文件，知道 nonce 也不证明它调用过目标。

本轮最低要求：

- 将现有 process 对照明确限定为 trusted-fixture instrumentation。
- 观察路径由受信 launcher 管理，限制在本次运行的私有根，拒绝越界、符号链接和过大文件；不按不可信 stdout 任意读取宿主路径。
- 没有目标/PoC 与判读输入之间的权限或命名空间分离，就不声称具备对抗性生产 oracle。
- 若完整 container 独立采集面超过本轮范围，停在安全无结论，报告未具备的能力；不新增大型执行框架来凑本轮完成。

### 6.6 验收矩阵

至少覆盖：直接 submit 绕过、repair 绕过、伪造授权、失配 hash、未知策略、旧事件缺来源、机械单次弱支持、fp 缺独立证据、wrapper0+目标 import 失败、修复版缺依赖/超时/未调用/合法控制失败、有效拒绝 rc2、双版皆违规、双版皆不违规、真漏洞但无效 PoC、无条件 PASS、伪造白名单 ASSERT、直接造 canary/target_output、OBS 越界或 symlink、旧工件重用。

每例检查：最终规范化 outcome、finding 状态、grade、verify_result、deposit、修复后重入和事务原子性。不能仅断言 violation bool 或报告字段。

## 7. T3：运行身份、候选归属、缓存与恢复

### 7.1 已知代码落点

- [候选定位键](<../qswarm/perception/candidates.py#L43-L82>)。
- [候选注册与 open_run](<../qswarm/kernel/dispatch.py#L243-L353>)、[DDL](<../qswarm/contracts/schema.sql#L8-L52>)。
- [L1 任务](<../qswarm/kernel/loop.py#L251-L258>)、[L2 任务](<../qswarm/kernel/consult.py#L658-L722>)。
- [索引缓存](<../qswarm/perception/indexer.py#L301-L344>)。
- 同时阅读 run_scan、run_consult、_submit_scout、_submit_auditor、dispatch、re_dispatch 和候选到 finding 的谱系装配。

### 7.2 冻结 run 输入

为新运行定义 `identity_version` 和不可变输入 manifest。至少含：stage、repo_key、实际扫描 snapshot_digest、scope/changed_paths、相关参数和规则/解析器/prompt/profile 版本。L2 另存 source_scan_run_id 与源快照。

- snapshot 要反映实际纳入扫描的文件内容，包括会影响结果的 dirty/untracked 文件；只记录 git HEAD 不够。
- 默认可将 resolved checkout path 标为“本地仓库身份”；不猜两个 remote/fork/checkout 语义等价。显式 repo_key 的规则写清楚。
- 同 run 相同 manifest 恢复；同 run 换仓、改源码、改 scope/关键参数时，在调用/新增写入前拒绝，要求新 run。
- consult 读取源 scan 候选前核验目标快照；不能拿 A 的候选读 B 的代码。
- 不顺手更改预算追加政策。建立时预算与运行期间预算变更分开记录；本轮未获准的追加拒绝。

### 7.3 分开三个身份

建议采用以下最小模型，具体编码可以调整，语义不得省略：

```text
local_locator_key = 既有候选定位 hash(path, span, kind, clue)
candidate_occurrence_id = H(version, scan_run_id, local_locator_key)
exact_snapshot_tracking = (repo_key, snapshot_digest, local_locator_key, file_blob_hash)

L1 task_id = H(stage, identity_version, scan_run_id, candidate_occurrence_id)
L2 task_id = H(stage, identity_version, consult_run_id,
               source_scan_run_id, candidate_occurrence_id)
```

保留现有纯感知候选的局部 hash，进入生产注册前做显式 run binding。DB、任务 shard、提示词候选 ID、ScoutReport 校验、绑定工件必须使用同一 occurrence 映射。保留 raw L0 工件和 raw→bound 映射，不只保存本次 inserted 的候选。

不要只给 task 加 run 前缀：当前候选表主键也跨 run 冲突。不要用短截断 ID 或候选排序序号作为唯一语义键。

跨 run 新 occurrence 只代表新观察，不代表新独立根因。跨 commit、rename、语义同源根因合并本轮不做。

### 7.4 写口与所有权守卫

- register_candidates 对同 occurrence 核对 owner run 与不可变输入；冲突报错，不 INSERT OR IGNORE、不移动旧行。
- _submit_scout 核 output candidate 等于 task 预期 candidate，并属于该 scan run；不能只查“候选存在”。
- L2 task 属于 consult run，候选属于 source scan run；不要误要求两者 run_id 相同。
- 显式传给 dispatch/re_dispatch/submit 的 run_id 与 task 实际归属不符时拒绝，不能覆盖归属。
- L2 submit 同事务持久化 candidate→task→finding 或 declined/result_ref；恢复不得按路径、插入顺序或“最新 finding”猜谱系。

优先复用 runs.meta_json、task.shard_json 的版本化 input/result 区和不可变工件，通过既有写口更新。若发现必须新增列/事件，先提交 additive 迁移方案；不能只改 DDL 文件就声称旧库能升级。

### 7.5 恢复语义

| 现有状态 | 本轮要求 |
|---|---|
| L0 已完成且 run 绑定工件完整 | 读取已存候选，不重新调用模型通道 |
| task done | 读取持久化输出/谱系，不再调用、不重复存款 |
| task queued | 复用原任务；有合法池后继续派发 |
| task dispatched | 按既有剩余 attempts 和显式恢复规则处理；不重置次数 |
| task failed | 保持终态；重开另有明确动作/新 run，不静默重跑 |
| 请求发出后、响应持久化前崩溃 | 标记不确定窗口；有日志/缓存才恢复，不能承诺外部调用 exactly-once |

先按单协调器实现。不承诺多进程并发 resume；如检测到并发，明确拒绝或报告不支持。

同 run 幂等要覆盖 L0 模型调用、L1、L2、finding、面板、费用与奖励，而不只是 task 行数。报告恢复所依赖的是 DB 一致性备份+工件；现有事件重放并不重建全部 runs/tasks/candidates，不能夸称仅 ledger 足以恢复整套运行。

### 7.6 缓存修复

cache key 纳入 cache 版本、语言、parser/grammar 指纹与 bytes。缓存路径无关的解析产物，返回时绑定当前 path/lang，不返回另一个文件的 IndexEntry 实例。旧缓存版本视为 miss。

测试包括内存命中、落盘后新进程读取、同 bytes 不同 path、同 bytes 不同语言、grammar 版本变化和损坏缓存。不要靠把 repo 路径塞进 key 来掩盖结构问题。

### 7.7 验收

1. 同 run 同输入两次：第二次已完成部分 Fake 调用为 0；候选/task/finding/费用/存款不增。
2. 同库新 run 扫同仓同快照：候选和任务有各自归属，不窃用首 run triaged；按 exact_snapshot_tracking/可靠映射去重，不以 run ID 当根因 ID，根因独立数不能因此翻倍。
3. 两仓同 path/span/clue/bytes：不串候选、任务、根因或报告。
4. 同 run 换 root、dirty bytes、scope、关键参数：调用与写入前拒绝；新 run 可以处理。
5. scan A→consult B 快照拒绝；合法 source scan 与 consult run 不同可正常处理。
6. 候选重排或新增无关候选：原任务身份不变。
7. queued 空池恢复、done 恢复、dispatched 剩余次数、failed 终态各有用例。
8. ScoutReport 指向另一 run 的既有 candidate、错 run_id 提交：唯一写口拒绝，相关状态与存款不变。
9. L2 finding 落库后故障重入：不重复 finding，谱系可恢复，后续评审归因一致。
10. legacy 数据库只读检查/迁移 dry-run 不改原库；新 run 不回退查旧 unscoped ID 当作已完成。

## 8. T4：专员路由与派单前准入

[route](<../qswarm/kernel/route.py#L202-L277>)当前只在探索通道使用显式 pool，专员通道仍取全量 active cells；[L2 派单](<../qswarm/kernel/consult.py#L682-L715>)因而可能选到已不在合法池中的 agent。

要求：

- 显式 pool 是此次调用的完整可派范围，专员/探索/fallback/重试都不得越界；pool=[] 必须空，pool=None 保留约定的旧推导语义。
- cells 的 active 状态不替代 agents 本体 active、角色资格及当前 profile。
- 对已经启用资格门的角色，在最终派单和改派前 recheck；撤销或 profile 变化后零模型调用。不要扩大默认 enforce_roles，也不要替负责人启用生产资格迁移。
- 查不到合法 model_ref 不能用空串继续调用并记成普通 infra 故障；明确区分 empty_pool / qualification_filtered / revoked_midrun。
- 保留原路由算法及随机数纪律；不借修候选池换 bandit、重调 TILT 或制造新专家。

验收覆盖：空池但有高分 active cell、池外最强专员、inactive 本体、failed/revoked 资格、profile 变化、排队后撤销、重试到池外 agent、正常合格专员。确认调用次数与边界结果，而不只检查路由 reason。

## 9. T5：修正指标、真值与时间视图

### 9.1 代码落点

[证据读取](<../qswarm/kernel/evidence.py#L79-L180>)、[能力汇总](<../qswarm/kernel/evidence.py#L203-L265>)、[人工忽略语义](<../qswarm/kernel/cluster.py#L1056-L1075>)、[E0 runner](<../experiments/design_revision/run_e0_checks.py>)、[投影测试](<../tests/test_evidence_projection.py>)。

### 9.2 先定义 population，再算数

当前输入主要是 verifier 决策观察，不是全仓金标准。必须分别命名并输出：

| 指标 | 统计集合与公式 |
|---|---|
| 正向上报 precision | 预定义正向报告集合中 TP/(TP+FP)；正确拒判 TN 不进分子 |
| 未知标签界限 | 对同一正向集合，U 为未知/争议标签；[TP/(TP+FP+U), (TP+U)/(TP+FP+U)] |
| 正确拒判/负例拒判率 | 已知负例集合中的正确拒判；不能叫 precision |
| decision accuracy（如保留） | 已知标签的正负判断正确率，明确是否排除 abstention，并另报覆盖率 |
| 系统召回 | 从完整金标准根因任务清单取分母；预算未执行、infra 失败、漏斗丢失不消失 |
| 覆盖与可靠性 | 所有尝试的已知/未知真值比例、abstention、verify_error、infra、成本与延迟 |

没有完整金标准清单时，系统召回输出 not_estimable，不从已验证正例反推全仓召回。候选报告 precision 和最终确认 precision 若都输出，分别固定集合，不混用。

precision 的 U 只属于该正向报告集合；不要机械地把全部 verify_error 塞进 precision 的 U，也不能让这些尝试从总体覆盖/召回/成本统计消失。unknown 界限不是统计置信区间。

### 9.3 真值和纠错

- direction=0 是忽略，不是 refuted，也不应与既有正标签制造假 disputed。
- 同级冲突保留 disputed；外部标签与模型主张分开，不能给自己的 verdict 当真值。
- 新观察携带已冻结的 profile/oracle/policy/证据引用；历史字段不可恢复时 unknown，不补猜模型异构或资格。
- 区分 attempt history 与有效事实：一次 infra 失败不等于撤销旧真值，旧未经授权的 real 也不能凭“旧事实”永久有效。更正/撤销要有明确来源与适用版本。
- 历史 as_of 视图不得通过今天的 findings/cluster 富化读取未来信息。使用事件时不可变元数据，或按 as_of 重建关联；相同历史输入不受未来 merge/新增 finding 改变。
- 先按可确认的 profile/role/family 分组；profile unknown 的 legacy 只作描述，不进入确认性专长比较。

### 9.4 验收

- 1 错误正向报告 + 9 正确拒判：正向 precision=0，不是 0.9。
- 只有 TN、没有正向报告：precision=not_estimable。
- 正向 TP/FP/U/disputed 混合：分子分母和界限正确。
- 有 verify_error：尝试/成本/覆盖率保留；金标准正例执行失败仍在系统召回分母。
- direction=0：无标签时保持 unknown；已有 +1 真值时不覆盖、不制造假冲突。
- 对已证明属于同一根因的重复调用、纠错、同快照新 occurrence 不重复计数；真正新增根因可增加。跨 commit/仓库关系无法可靠判定时记 provisional/unknown，不纳入确认性独立样本计数，本轮不建设语义根因合并系统。真实标签冲突保留不确定性。
- 未来真值、未来 finding/merge、相同 as_of、输出排序和哈希均有断言。
- 清理恒真断言，如 `assert condition or True`；不以放宽测试替代修复。

新版指标/投影另加版本；历史报告只补勘误链接，不覆盖原结果。

## 10. T6：校准工具与材料修复，暂不执行裁判

### 10.1 代码和数据落点

[校准手册](<../experiments/design_revision/calibration/RUNBOOK.md>)、[人工题面](<../experiments/design_revision/calibration/judging_sheet.jsonl>)、[裁判材料](<../experiments/design_revision/calibration/judge_materials.jsonl>)、[一致率工具](<../experiments/design_revision/calibration/compute_agreement.py>)、[冻结 manifest](<../experiments/design_revision/manifests/e0_calibration_set.jsonl>)。

### 10.2 要做的事

1. **不要覆盖已冻结的旧 25 题。** 创建版本化新材料、内容哈希、生成规则与旧→新映射，记录泄题和 ID 问题勘误。
2. 去掉题面中的“漏洞版/修复版/安全版/预期判读/答案键”等提示，以及无关的类别号、文件路径标签。更换 blind_id 不能消除源码 docstring 泄漏。
3. 保留完成判断所需的语义和配置前提。原主张真假与 PoC 是否有效分成两个问题，不把“代码有漏洞”当作“该 PoC 已证明漏洞”。
4. root-cause group 与重复样本显式登记。第 1/7/8/9 类重复源码不算四个独立根因；按组统计并报告逐 item 结果。
5. 真值映射单一来源，解决 TRA/PAT 不一致；所有 ID 唯一、映射完整、枚举合法，未知/重复 ID 报错，不能静默覆盖。
6. 零人工判题、空 judge 文件、双方无交集、只判争议题、零分母都输出 not_estimable/未完成，不 KeyError/除零/默认通过。
7. 争议项报告全量与分层结果；不能只删争议项提高一致率。
8. 题面不带答案，隐藏映射不送给裁判；给人工的参考意见必须与正式独立判题隔离。

### 10.3 不要做的事

GLM 本轮是施工者。既有 D46 禁止 GLM 族与施工 AI 本体担任主裁判/盲审；不能以“我自己看过这些夹具”完成独立人工校准，也不能预填正式人工答案。

本轮只交付材料和工具的机械验收。人工判题、跨族裁判选型、调用、阈值和经费另批。正式校准成绩继续标记未完成。

## 11. T7：安装包与资源可用性

### 11.1 代码落点

[打包配置](<../pyproject.toml#L30-L40>)、[默认配置定位](<../qswarm/contracts/config.py#L14-L17>)、[默认规则定位](<../qswarm/perception/channels.py#L320-L341>)、[SQL 资源读取参考](<../qswarm/contracts/__init__.py>)。

### 11.2 修改要求

- 打包包含所需 qswarm 子包，受限发现范围；不能把 tests/scripts/out/缓存/私有数据库顺带发布。
- 明确打包 SQL、params、taxonomy、semgrep rules 等运行必需资源。不要整目录包含部署专用模型配置，更不能包含本地密钥。
- 默认资源使用 importlib.resources 等安装包可用方式读取；给外部 semgrep 的临时资源路径要覆盖整个 subprocess 生命周期。
- 显式配置路径优先，显式不存在时报错，不偷偷回落默认。默认资源只读；DB/cache/report 写用户指定可写位置，不写 site-packages。
- 默认资源如果移动或复制，测试其内容哈希与冻结配置一致，避免维护两套默默漂移的默认值。
- 模型配置与题库原本要求显式传入的，继续要求显式传入；修 wheel 不等于获准发布完整 seeds、响应缓存或实际供给配置。
- 核心安装与 perception extra 两档分别说明；缺 semgrep/grammar 时给真实缺席诊断，不报告已完整扫描。
- 本轮不强行创建完整统一产品 CLI。保证现有入口可安装运行，并交付一个离线 FakeAdapter smoke 即可；真实项目一键入口放下一轮。

### 11.3 干净安装验收

1. 在临时构建目录，从包含本轮必要文件的源码快照构建 sdist，再由 sdist 构建 wheel；不能遗漏尚未提交的新模块。
2. 使用独立新 venv、非 editable 安装；清除 PYTHONPATH 和源码 cwd，防既有 editable .pth 掩盖缺包。安装依赖使用已批准/本地可用资源，缺构建工具先报告，不擅自联网补装。
3. 从无关临时目录 import 各运行子包，加载 SQL、默认 params/taxonomy/rules。
4. 使用 FakeAdapter 和临时 DB 跑离线 scan→consult，以及同 run/跨 run 回归。
5. 检查 wheel 文件清单，不含密钥、DB、响应缓存、out、内部工件和不必要项目数据。
6. 测试安装目录只读时资源可读、运行工件写到显式输出目录。

只跑 `pip install -e .` 或在仓库根目录 import 不算交付验收。

## 12. 历史兼容、迁移和规格纪律

### 12.1 需要保留的内容

旧 append-only 事件、原始模型响应工件、旧 cells、M0 黄金文件与旧实验结果保持原含义。新代码可提供明确隔离的 legacy 离线重放，以检验历史可复现性。

**不要求错误的新报告继续输出旧错误数字。** precision、校准和可信门升级版本，生成新报告；解释旧结果为何不能继续用于结论。

### 12.2 安全兼容与生产恢复

- 新生产默认严格；缺 policy/证据字段不进入宽松 fallback。
- legacy 参数、默认 None、schema 缺列、旧事件缺字段都不能成为新生产的逃生门。
- 历史事件忠实重放与“现在补发新奖励”是两回事；repair 使用与提交相同的 eligibility 策略。
- 新 run 不 UPDATE 旧候选/task 主键；旧归属无法证明时保留 unknown，不把历史吞掉的调用补造出来。
- 若新增序列化字段，检查旧 payload 的字节兼容。给 pydantic 模型加默认 None 可能改变旧写出 JSON；不要全局 exclude_none 来修一个字段。
- 写入仍经过现有唯一写口；报告脚本不得直写生产表。DDL 或白名单改变要同步规格与审计，不扩大白名单绕过审计。

### 12.3 不碰实际生产库

所有迁移在临时库或经批准的一致性备份上 dry-run。若未来申请真实迁移，提供 SQLite backup/WAL 一致性方案、哈希、schema 版本、重复执行幂等性、回退步骤和无法恢复的字段清单。本轮不自动执行 apply。

## 13. T8：命令、环境验收和报告

以下为建议命令框架，先确认实际解释器和依赖。所有失败都要调查，不把退出码藏在管道后面。

```bash
pwd
git status --short
git rev-parse HEAD

# 新增反例优先局部运行，测试名以实际实现为准。
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/test_evidence_projection.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/test_s2_oracle_controls.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/test_kernel_loop_m3.py tests/test_kernel_consult.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest tests/test_kernel_route.py tests/test_role_qualification.py
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m qswarm.kernel.write_guard --strict

# 最后只做一次最终完整回归；若修复后需重跑，记录原因。
PATH="$PWD/.venv/bin:$PATH" .venv/bin/python -m pytest

# Docker 先探针，不要用恶意 PoC 探环境。
docker info --format ok
```

- 全量测试先核查真实执行范围；如果既有套件会运行夹具，确认属于已批准受控输入，且 T1 的入口限制已完成。
- 当前真实 Docker 测试仅按 CLI 是否存在决定 skip，不足以判断 daemon 可用。探针必须检查退出码与 stderr；此次探针 stdout 虽有 ok，退出码仍为 1，不能只匹配输出字符串。可修测试前置诊断，但要区分开发可选测试和 release 必需门。
- 开发环境可以将 unavailable 的真实容器项明确 skip，报告原因、数量与命令；**不能把它记成容器验收 PASS**。
- release gate 要求真实 container 验收时，daemon 不可用就 BLOCKED/NOT VERIFIED，不能删除测试或用 skip 获得生产放行。
- 不修改全局 daemon/WSL 挂载，也不启动替代服务来遮住原环境问题。
- 升级后的 E0 runner 执行前检查输出路径、版本与执行档；旧 runner 的 container 参数并不代表每个 C1 用例都在 container 执行。
- 如果使用后台任务，记录 job id、收集最终退出码，结束前处理仍相关的任务。

### 13.1 每批交付格式

```text
任务 ID:
状态: PASS / FAIL / BLOCKED / NOT VERIFIED / DEFERRED
修改范围:
问题最小反例:
旧行为与新行为:
规格与版本变化:
测试命令、退出码、passed/failed/skipped:
实际调用/执行范围:
工件位置与摘要哈希:
legacy兼容结果:
未解决风险、所需批准、下一步:
```

BLOCKED 要写具体外部条件；NOT VERIFIED 用于尚未运行的真实能力；不要用“待优化”模糊一个安全缺口。主执行者按真实结果报告，不把独立审阅者的推断包装成动态实测。

### 13.2 最终交付物

建议新增，不覆盖历史交付：

```text
docs/R1_HARDENING_DELIVERY_YYYYMMDD.md
out/r1-hardening/<run-id>/manifest.json
out/r1-hardening/<run-id>/reports/acceptance.json
out/r1-hardening/<run-id>/reports/acceptance.md
out/r1-hardening/<run-id>/logs/
out/r1-hardening/<run-id>/reproductions/
out/r1-hardening/<run-id>/package-check/
```

报告必须列出新增/修改测试与代码、每个 T0–T8 的完成情况、真实容器未验收项、禁止上线项、无模型调用声明、安装包检查和工作区变更归属。规格按实际改动同步，不为“规格完整”重写整个设计体系。

## 14. 总验收与停止条件

### 14.1 本轮离线工程完成条件

- [ ] T0 反例先失败、实现后通过，保留可复现证据。
- [ ] 不可信 process/宿主安装路径在副作用前拒绝；env、输出和清理边界有测试。
- [ ] 直接 submit、自动编排、propagate/repair 使用同一可信策略；缺来源不晋级不奖励。
- [ ] paired 健康与合法控制正确；机械单次支持不能冒充独立成对证明。
- [ ] 同 run 幂等、跨 run/仓库归属、manifest 冲突拒绝、缓存和谱系恢复通过。
- [ ] 专员/探索/fallback/重试不越显式合法池；启用角色派单前复验。
- [ ] precision、direction0、unknown、召回分母、纠错与 as_of 测试通过。
- [ ] 新校准材料无标签提示，ID 完整，边界输入健壮；独立人工/裁判仍未执行。
- [ ] 干净非 editable 安装、资源、离线闭环通过；包内无秘密和内部数据。
- [ ] 写口审计通过，legacy 黄金保持，受影响旧测试的语义变化有说明。
- [ ] 全量回归已执行；失败/skip/环境项逐项披露，不把数量当效果证明。

### 14.2 不随离线工程完成而自动获得的结论

- 不代表真实 container 红线全部验收。
- 不代表已有通用、对抗性生产 oracle。
- 不代表正式 verifier 供给和资格已就绪。
- 不代表 E0 独立校准完成。
- 不代表真实仓库召回、误报、经济性或蜂群收益成立。
- 不代表获准迁移生产库或启用新生产策略。

### 14.3 应停止并报告的情况

1. 必须靠放宽证据门、伪造资格/人工标签、降低 QC 或删除负对照才能跑通：停止该路径，保留无结论。
2. 必须改生产库、下载未知依赖、联网构建、运行真实外部/不可信 PoC 或超出已批准受控夹具范围、付费调用才能继续：停止相关任务并申请，不拖住可独立完成的离线修复。
3. 需要大型新调度器/新账本/完整跨 namespace 执行框架：拆出后续工作。本轮先交付 fail-closed 防线和明确未完成项。
4. 发现新增安全缺口：先记录反例与影响；与当前写入/执行边界直接相关的纳入修复，其他问题列后续，不无限扩范围。

## 15. 本轮完成后才申请的下一轮

按以下顺序提交建议，不自动执行：

1. 获批环境中复验受控 container 红线与独立观察能力，明确可生产支持的范围。
2. 人工审核新版校准材料，选择合规跨族裁判并批准少量调用。沿用既有预算上限，但不擅自解释或重置 D48 起算锚。
3. 做窄范围真实使用试点：Python 命令注入/路径穿越；输出候选、证据、无结论与人工确认，不伪称全仓安全。
4. 冻结同预算对照：单强模型可多轮工具调用 vs 廉价筛选+固定深审+验证；统一任务、信息、安全边界与成本口径。
5. 同时或随后检验 2–3 个实质不同 execution profile 的跨批次专长。按仓库/根因隔离；至少每家族约 20 个独立根因组、跨至少 3 个项目只作 pilot 起点，不承诺统计功效。
6. 简单级联有收益才增加固定多蜂；稳定专长和固定多蜂均有价值，才比较历史路由。未知结论不自动放行。

本轮不创建完整 E1/E2 调用脚本、不预购样本、不新增生产自适应路径。交付报告给出申请所需数据和缺口即可。

## 16. 可直接交给 GLM 的启动指令

```text
请阅读 docs/HANDOVER_GLM_R1_HARDENING_20261028.md，按其中 R1 hardening 范围实施。
先核对 /opt/projects/qswarm 的当前工作区，保护全部既有未提交改动。
第一步提交一份简短的 T0–T8 执行清单和 R1 语义变更说明，然后按批次补红测、修复、验收。
本指令批准该文档列明的离线代码、测试与配套规格修复，不批准生产库变更、实际生产启用、
付费模型调用、联网安装/拉镜像/构建、未知依赖执行或真实外部 PoC。
不要扩展蜂群机制；不要让旧兼容路径绕过新安全门。
无法完成独立观察时保持无结论、零高可信奖励，明确报告 NOT VERIFIED，不为跑通造标签。
每批汇报修改、反例、命令/退出码、结果、遗留风险；最后交付 R1 报告及下一阶段申请事项。
```
