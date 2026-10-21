# M5 开工前评估（2026-10-21）

> 评估依据：设计文档 v0.3.4（§3.4/§6.3/§6.5/§10.4/§16.4/§16.6/§16.7）、SPEC-2（§2.4
> S2 映射/写口表）、SPEC-8 v1.0（§2.6 预裁决单/§4 影子指标）、
> `out/_m4_final/M4_DELIVERY_REPORT.md` §5、`docs/HANDOVER_20261020.md`、
> `docs/spec/DECISIONS.md` 至 62b9ed3、代码基座实测（全量 pytest 712 passed，
> 2026-10-20 复跑留痕）与本机环境实测（docker 可用性/内存/CPU）。本文只评估不拍板；
> 决策点清单见 §6（D38–D42 候选），供负责人裁决与 SPEC-9 导出用。

---

## 0. 一句话结论

与 M4 同构——S2 的**语义面**（三分类枚举、verify_results 写口、§2.4 状态映射、
§10.4 verified_fp 证据标准、PreVerdictSlip 排程输入、S2_SOURCE_W=0.5 存款归属、
quota_s2 预算档）在契约/内核/规格层全部已备且有测试钉住；M5 的真实工作量在
**执行面**：沙箱运行时基建（分级隔离 + env 缓存 + 红线配置）、PoC 生成与 harness、
verifier 生产派单接线、D34 仲裁件编排。最大的开工阻塞是**本机 docker 不可用**
（Docker Desktop WSL 集成未开，待决#9 基建选型必须拍板）；最大的结构性缺口是
**生产编制无 active verifier**（engineering 蜂旧口径考 0.675 留 provisioning）——
S2 派单面现在无人可派。

## 1. 交付面定义（口径对齐）

按 §16.6 依赖序第 8 位「S2 沙箱（§6.3/待决#9）── docker 分级隔离 + env 缓存；
两大重件之一」+ M4 交付报告 §5 的 M5 输入面：

| # | 交付面 | 设计依据 | 内容 |
|---|---|---|---|
| F1 | S2 沙箱执行面 | §6.3/待决#9/§16.7-5 | 沙箱 runner：分级隔离 tier（container / 进程级）、env 缓存、红线配置（非特权、无 socket、只读根、默认禁网+逐例白名单、带宽/时长限额、超时）、verify_error 四分类（env_build_fail/poc_fail/timeout/flaky）落点 |
| F2 | verifier 真验证接线 | §10.2/§6.3/SPEC-2 §2.4 | 精聚类簇/预裁决单 → PoC 生成（harness 参照 PatchEval Docker 验证管线）→ 沙箱执行 → VerifierReport → submit() 既有写口（VR 行 + verify_result 事件 + §2.4 映射 + propagate_verdict 同事务，M4 批修已加固）→ §6.5 归属矩阵 S2 行（×0.5）存款 |
| F3 | D34 蜂后顾问仲裁件 | §4.2/§16.7-9/M4-4 | 人工触发 run_arbitrate(cluster)：簇全量证据工件引用 → queen_advisor → ArbitrationDraft 工件（仅建议权、落账走内核、不入生产口碑）；随 S2 同交付 |
| F4 | 影子期开跑 + 暗考接线 | §7.3/M3-7 预留/放行行 62b9ed3 | 影子期生产票流开跑（D35 阈值首月数据窗后反推）；暗考回调在 loop.run_scan 尾部预留点接线、开关默认关（开启归 D35 批准面） |
| F5 | 联调遗留小件清账 | E 组补录 §9.5/批修行 | rules_runner 生产显式装配、rule 候选路径归一、pv_skipped 判据按 SPEC-8 §2.4 复核、taxonomy authz types 面补齐、存款值版本化刷新、注入防线显式断言、交付报告 §2 四条判据缺口 |

## 2. 基座盘点（已有落点，全部可复用）

| 面 | 落点 | 状态 |
|---|---|---|
| 契约 | `VerifyOutcome/VerifyErrorKind/BlockingKind` 枚举；`VerifierReport/Assertion/BlockingEvidence/PreVerdictSlip` 模型；`verify_results` DDL（verified_fp 缺 blocking_* 内核强制改判 verify_error，schema 注释钉死） | ✅ M1/M4 备 |
| 写口 | `dispatch._submit_verifier`：VR 行 + verify_result 事件 + §2.4 S2 映射（verified_real→verified / verified_fp→rejected / verify_error→inconclusive 零存款）+ propagate_verdict 同事务；VR 行号库内派生 MAX(attempts)+1、终态成员守卫（2026-10-20 批修） | ✅ 有测试钉 |
| 存款 | §6.5 归属矩阵全行实装（M1）；S2_SOURCE_W=0.5 与仿真器同源同值 | ✅ |
| 预算 | quota_s2 档、预算熔断（consult 侧已扩至面板/pv 全付费调用面） | ✅ |
| 排程输入 | 精聚类簇 + PreVerdictSlip（cluster_id/expected_verdict/poc_shape/env_requirements）；E 组 smoke 产出 2 簇 voted（pv 分歧视跳过面正是 M5 输入） | ✅ 生产面已产 |
| 仲裁件静态面 | QUEEN_ADVISOR_SYSTEM 模板 + ArbitrationDraft 解析 + dispatch 输出契约登记（"queen_advisor 不派产 finding 任务"守卫） | ✅ M2 备，零编排 |
| verifier 提示词 | VERIFIER_SYSTEM/SCHEMA_FIELDS（三分类+blocking 证据标准渲染）+ parse 注册 | ✅ M2 备 |
| 影子指标 | shadow_report（cells_by_status/n_eff 分位数）D35 指标面 | ✅ M4 备 |
| 环境 | venv(dev,perception)+semgrep、.zhipu_key、全 flash 供给配置、E 组 smoke fixture 真仓 | ✅ |

## 3. 缺口清单（M5 新建面，G1–G6）

- **G1 沙箱 runner 模块**（`qswarm/sandbox/` 新包）：SandboxRunner 接口 + tier 抽象
  （container 档 / process 档）、env 缓存（base 镜像 + deps lockfile 复用，§16.4 缓解
  项）、红线配置逐条落档（默认禁网、非特权用户、只读根、无 docker socket、资源
  rlimits、墙钟超时→error_kind=timeout）、工阶段 artifacts（logs/断言 log 落
  artifact 库）。
- **G2 PoC 生成与 harness**：从簇证据链（evidence_chain/poc_shape/env_requirements）
  产 PoC 脚本 + 可执行 harness（PatchEval Docker 验证管线参照）；attempts 重试语义
  （VerifierReport.attempts 已有库内派生）；PoC 失败分类 poc_fail vs flaky 判据。
- **G3 verifier 生产派单接线**：独立排程入口（见 D40）→ task 头渲染（verifier 模板
  已在）→ 真蜂调用 → VerifierReport 解析 → submit() 复用；S2 存款落账与状态机
  verified/rejected/inconclusive 流转联调。
- **G4 D34 仲裁编排**：run_arbitrate(cluster_id) → 簇全量证据工件引用装配 →
  queen_advisor 调用 → ArbitrationDraft 工件落盘（新 ARTIFACT_KIND）+ 落账事件
  （建议权，非 verdict 非存款）。
- **G5 暗考接线**：loop.run_scan 尾部 M3-7 预留点 → 触发条件判据（影子票流 n_eff
  达标后由负责人开启）→ exams 补播面复用；params 旋钮默认关。
- **G6 遗留小件**：F5 清单六项（均为小改，随里程碑顺路清账）。

## 4. 前置件

1. M4→M5 放行 ✅（62b9ed3，2026-10-21）。
2. **沙箱运行时就绪**（D38 拍板后行动）：本机实测 **docker 不可用**——Docker Desktop
   装于 Windows 侧但本 WSL2 发行版未开集成（`docker` 命令不可用）；机量 23GB RAM /
   16 核，container 档资源无忧。负责人 Windows 侧一次勾选（D38-a）或拍板进程档先行。
3. verifier 入编（D41 拍板 → 补播考真蜂调用，key/链路就绪）。
4. 联调素材：PoC 可执行素材仓（E 组 smoke fixture 可起步；/tmp 重生成素材已丢，
   需要时按补录 §8 重克隆）。
5. 全量 pytest 712 全绿（2026-10-20 复跑留痕）。

## 5. 风险与结构性发现

- **R1 基建选型未决 + 本机 docker 缺位**（开工阻塞）：待决#9（docker/gVisor/
  Firecracker 分级）原样留给工程期，本机现状迫使其提前拍板（D38）。建议实现按
  SandboxRunner tier 抽象，双档并存即"分级隔离"本意，选型不再是一锤子买卖。
- **R2 编制无 active verifier**（结构性缺口）：hat verifier(formal) 挂在
  engineering:flash 上，但该蜂 provisioning（R1 旧口径 0.675 未过）。S2 派单面空转；
  且孵化硬门槛 n_eff(S2+S3)≥3 + S2_SOURCE_W=0.5 → verifier 缺位 = 进化闭环 S2
  供血为零。S2 真验证联调前必须入编（D41）。
- **R3 供血风险**（设计文档自列第一风险，§16.4）：PoC 生成质量决定 verify_error 率
  （警戒线初期 <40%）；全 flash 写码能力未实证（engineering 档 0.675 的另一面）。
  联调时 verify_error 率如实测如实报，不硬凑结论；超线按 §11.5 记工程欠账。
- **R4 沙箱安全红线**：AI 产 PoC 在本机执行 = 逃逸面（SandboxEscapeBench 实证前沿
  模型能逃常规容器配置）。红线清单（非特权/无 socket/只读根/禁网/限额）逐条落
  配置并在验收卷机器可检；联调素材只给仓库副本，不指生产路径。
- **R5 时延结构性**：一簇一沙箱 duration_s 数百秒量级（设计示例 340s）×预算封顶
  → 排程独立入口（D40-b）防拖垮 consult 交互性；env 缓存从第一天起做（§16.4）。

## 6. 待负责人拍板的决策点（SPEC-9 开工前，D38–D42 候选）

- **D38 沙箱基建分级与运行时落地**（待决#9 本机化）：
  (a) **启用 Docker Desktop 的 WSL 集成**（Windows 侧一次勾选；container 档即刻
  可用，红线原生支持，PatchEval harness 直接参照）——**推荐**；
  (b) 本发行版内直装 docker-ce（root 可行，WSL2 内核/systemd 叠加需验证，运维自担）；
  (c) v1 先落 process 档（subprocess+独立工作树+rlimits+禁网），container 档推迟
  （「分级隔离起步」字面合规，但逃逸红线只能近似，verify 证据力打折）。
  建议统一写法：SandboxRunner tier 抽象双档并存，(a) 就绪前用 (c) 作开发/CI 档。
- **D39 PoC 生成归属**：(a) **verifier 蜂一体产**（VerifierReport+PoC 同次生成，
  attempts=2 重试语义既有；与 §6.3 trigger_path/assertions 由验证蜂产出同构）——
  **推荐**；(b) 工程蜂专职产 PoC 再交 verifier 执行（多一跳派单+成本，角色表无此岗）。
- **D40 S2 排程触发面**：(a) run_consult 内联（精聚类后自动排程，一站到底）；(b)
  **独立 run_verify 入口**（消费精聚类簇/预裁决单，quota_s2 独立档，分批补验）——
  **推荐**（沙箱时长不可控，与 L2 墙钟耦合拖垮交互性；M4-4 原文「预裁决单消费方
  = 人与 M5 沙箱排程」与独立入口天然对齐）。
- **D41 verifier 入编路径**：(a) **新口径重考 engineering:flash**（S4 中性化+gloss
  修复后分数预期变化，仪器修复重考 M2 先例；不过线再升档不硬凑）——**推荐**；(b)
  verifier 帽兼任改挂 strong:flash（D31 帽兼任先例，strong 0.90 蜂现成）。
- **D42 D34 仲裁件接线形态**：(a) **最小仲裁件**（人工触发 run_arbitrate → 工件
  落盘+落账，仅建议权；M4-4 原文「随 S2 同交付」，全自动依赖 S2 后信息早做必返工）——
  **推荐**；(b) 接入 consult 流水线自动触发（推迟到 S2 数据积累后另拍）。
- 影子期暗考开关不另设决策点：放行行已定「影子期先开跑积累」，M5 只接线、
  默认关，开启归 D35 批准面（负责人）。

## 7. 建议开工序与量级

D38–D42 拍板 → SPEC-9 导出（§16.6-8 实现级，含验收卷 SPEC-5_m5 同步）→ G1（含
红线配置与 env 缓存，量级最大）→ G2/G3（PoC+接线，与 G1 可并行开发 FakeRunner）
→ G4 仲裁件（小）→ G5 暗考接线（小）→ G6 遗留清账 → E 组真蜂联调（verifier 入编 +
真沙箱 smoke：真簇 → PoC → 沙箱 → 三分类 → S2 存款全链）。
量级对标：设计账 S2 占工程量 ~25%（两大重件之一），实现会话量预计 ≥ M4；
真蜂联调成本新增 verifier 考试 + smoke 沙箱轮（调用面 + 沙箱墙钟），预算口径
沿用 D33 双轨。
