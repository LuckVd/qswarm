# M5 开工前评估（2026-10-21）

> **2026-10-21 收口追记**：负责人拍板「开始做 M5，直接做完」（= D38–D43 全部按
> 本文推荐项批准，逐条落 `docs/spec/DECISIONS.md` 同日行）；**D38 前提变化**——
> Docker Desktop WSL 集成本日开通，container 档五项红线实测全绿（本文 §4-2/§5-R1
> 的「docker 不可用」表述就此作废，D38-a 即刻可落）；G2 前置离线试跑已实跑
> （E 组 smoke 2 voted 簇 → 模板 PoC → process 档真执行 → verified_real ×2，
> match 2/2，`out/m5-trial/`）。实现与验收见 `docs/spec/SPEC-9_m5_sandbox.md`
> + `docs/spec/SPEC-5_m5_acceptance.md`（全量 pytest 754 passed）与
> `out/_m5_final/M5_DELIVERY_REPORT.md`。以下为开工前评估原文（事实面以追记为准）。
>
> 评估依据：设计文档 v0.3.4（§3.4/§6.3/§6.5/§10.4/§16.4/§16.6/§16.7）、SPEC-2（§2.4
> S2 映射/写口表）、SPEC-8 v1.0（§2.6 预裁决单/§4 影子指标）、
> `out/_m4_final/M4_DELIVERY_REPORT.md` §5、`docs/HANDOVER_20261020.md`、
> `docs/spec/DECISIONS.md` 至 62b9ed3、代码基座实测（全量 pytest 712 passed，
> 2026-10-20 复跑留痕）与本机环境实测（docker 可用性/内存/CPU）。本文只评估不拍板；
> 决策点清单见 §6（D38–D43 候选），供负责人裁决与 SPEC-9 导出用。
> 2026-10-21 补强：外部复核四点并入——D43（执行档落账口径）新增、D41 改级联
> 预授权、G2 增前置离线试跑、预算口径两点（quota_s2 代理/沙箱资源成本）随
> SPEC-9 明确；标注见 §0 补强行 / §3 G1–G3 / §5 R6–R7 / §6 / §7。

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

**补强（2026-10-21 复核并入，四点）**：① **D43 新增**——`verify_results`/
`VerifierReport` 均无执行档（tier）字段，D38 双档并存后 process 档结果与
container 档同表同形、不可审计来源档位，是账本污染级缺口（非「证据力打折」）；
② **D41 改级联预授权**——重考不过线自动落帽兼任 strong:flash，联调窗口不赌
一次考试；③ **G2 前置离线试跑**——E 组 smoke 2 voted 簇已知答案试跑，提前暴露
PoC 生成面质量风险；④ **预算口径两点**——quota_s2 存活数代理改 verifier 实编、
沙箱资源成本入 D33 双轨披露面。

## 1. 交付面定义（口径对齐）

按 §16.6 依赖序第 8 位「S2 沙箱（§6.3/待决#9）── docker 分级隔离 + env 缓存；
两大重件之一」+ M4 交付报告 §5 的 M5 输入面：

| # | 交付面 | 设计依据 | 内容 |
|---|---|---|---|
| F1 | S2 沙箱执行面 | §6.3/待决#9/§16.7-5 | 沙箱 runner：分级隔离 tier（container / 进程级）、env 缓存、红线配置（非特权、无 socket、只读根、默认禁网+逐例白名单、带宽/时长限额、超时）、verify_error 四分类（env_build_fail/poc_fail/timeout/flaky）落点、执行档 provenance 落账口径（D43） |
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
  artifact 库）。**补强**：tier 抽象连同落账口径一并定（D43）——执行档
  provenance 不入 verify_results 则生产存款不可审计来源档位。
- **G2 PoC 生成与 harness**：从簇证据链（evidence_chain/poc_shape/env_requirements）
  产 PoC 脚本 + 可执行 harness（PatchEval Docker 验证管线参照）；attempts 重试语义
  （VerifierReport.attempts 已有库内派生）；PoC 失败分类 poc_fail vs flaky 判据。
  **补强（前置离线试跑）**：正式联调前用 E 组 smoke 产出 2 voted 簇 +
  PreVerdictSlip（poc_shape/env_requirements 现成）做已知答案试跑——PoC 生成 →
  最低可用档真执行（process 档即可）→ 三分类比对 expected_verdict；不需 verifier
  入编与真蜂调用，R3 生成面风险从联调窗口拆出提前一阶段暴露（2 簇样本只够判
  「生成面完全不可用」量级，结论如实记档不外推）。
- **G3 verifier 生产派单接线**：独立排程入口（见 D40）→ task 头渲染（verifier 模板
  已在）→ 真蜂调用 → VerifierReport 解析 → submit() 复用；S2 存款落账与状态机
  verified/rejected/inconclusive 流转联调。**补强**：独立入口下 quota_s2 的存活数
  代理（kernel/budget.py 以 quota_l2 为代理）不再成立——SPEC-9 改以 verifier 实编
  为准（现编制大概率 1 只），防容量高估。
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
3. verifier 入编（D41 级联拍板 → 补播考/帽考真蜂调用，key/链路就绪；(a) 不过线
   自动落 (b)，联调不因单次考试失败阻塞）。
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
- **R6 S2 执行档落账不可区分（补强新增；DDL 实证）**：`verify_results`
  （contracts/schema.sql）与 `VerifierReport` 均无 tier/运行时字段——D38 双档并存后
  process 档（隔离近似、证据力弱）结果与 container 档同表同形，其 verified_real
  真实产 ×0.5 生产存款且事后不可审计来源档位：账本污染，非「证据力打折」。缓解
  = D43。
- **R7 预算口径两点（补强）**：① quota_s2 以 quota_l2 为存活数代理（kernel/budget.py
  注释钉死），D40 独立入口下应改 verifier 实编（代理高估容量）；② 沙箱轮首引非
  LLM 成本（env 构建 CPU/墙钟 ×簇数，340s 量级/簇），D33 双轨只覆盖 LLM 调用——
  披露面不扩则 S2 成本系统性低估。两项均随 SPEC-9 导出明确。

## 6. 待负责人拍板的决策点（SPEC-9 开工前，D38–D42 候选）

- **D38 沙箱基建分级与运行时落地**（待决#9 本机化）：
  (a) **启用 Docker Desktop 的 WSL 集成**（Windows 侧一次勾选；container 档即刻
  可用，红线原生支持，PatchEval harness 直接参照）——**推荐**；
  (b) 本发行版内直装 docker-ce（root 可行，WSL2 内核/systemd 叠加需验证，运维自担）；
  (c) v1 先落 process 档（subprocess+独立工作树+rlimits+禁网），container 档推迟
  （「分级隔离起步」字面合规，但逃逸红线只能近似，verify 证据力打折）。
  建议统一写法：SandboxRunner tier 抽象双档并存，(a) 就绪前用 (c) 作开发/CI 档。
  两档的落账口径见 D43（process 档若入生产面必须可审计）。
- **D39 PoC 生成归属**：(a) **verifier 蜂一体产**（VerifierReport+PoC 同次生成，
  attempts=2 重试语义既有；与 §6.3 trigger_path/assertions 由验证蜂产出同构）——
  **推荐**；(b) 工程蜂专职产 PoC 再交 verifier 执行（多一跳派单+成本，角色表无此岗）。
- **D40 S2 排程触发面**：(a) run_consult 内联（精聚类后自动排程，一站到底）；(b)
  **独立 run_verify 入口**（消费精聚类簇/预裁决单，quota_s2 独立档，分批补验）——
  **推荐**（沙箱时长不可控，与 L2 墙钟耦合拖垮交互性；M4-4 原文「预裁决单消费方
  = 人与 M5 沙箱排程」与独立入口天然对齐）。
- **D41 verifier 入编路径（补强：级联预授权）**：(a) **新口径重考 engineering:flash**
  （S4 中性化+gloss 修复后分数预期变化，仪器修复重考 M2 先例）；**(a) 不过线自动落
  (b) verifier 帽兼任挂 strong:flash**（D31 帽兼任先例，strong 0.90 蜂现成，非变通；
  仍须过 verifier 帽入职考，不硬凑纪律不变）——两步作为**同一序列拍板**推荐：
  S2 派单面无人可派是联调硬前置，联调窗口不赌一次考试失败（engineering:flash 旧
  口径 0.675、全 flash 写码能力未实证即 R3）。
- **D42 D34 仲裁件接线形态**：(a) **最小仲裁件**（人工触发 run_arbitrate → 工件
  落盘+落账，仅建议权；M4-4 原文「随 S2 同交付」，全自动依赖 S2 后信息早做必返工）——
  **推荐**；(b) 接入 consult 流水线自动触发（推迟到 S2 数据积累后另拍）。
- **D43 S2 执行档落账口径（补强新增；DDL 实证缺口）**：`verify_results`
  （contracts/schema.sql）与 `VerifierReport` 均无 tier/运行时字段——D38 双档并存后，
  process 档（隔离近似、证据力弱）结果与 container 档同表同形，其 verified_real 会
  真实产 ×0.5 生产存款且事后不可审计来源档位：账本污染，非「证据力打折」。候选：
  (a) **process 档不写生产账本**（开发/CI 档只产工件与报告，S2 生产存款唯一口径 =
  container 档；M3-7 影子期零注入同款思路，schema 零改动）——**推荐**；
  (b) verify_results 加 tier 列 + VerifierReport 扩字段（schema 变更随 SPEC-9 一并
  导出，双档均可落账、按 tier 审计）；(c) 维持现状不加区分——不建议（R6）。
  机器可检（入 SPEC-5_m5）：(a)=写口拒绝/生产库零 VR 行断言；(b)=tier 非空约束。
- 影子期暗考开关不另设决策点：放行行已定「影子期先开跑积累」，M5 只接线、
  默认关，开启归 D35 批准面（负责人）。

## 7. 建议开工序与量级

D38–D43 拍板 → SPEC-9 导出（§16.6-8 实现级，含验收卷 SPEC-5_m5 同步；导出注意
三项：tier 落账口径按 D43 落、quota_s2 存活数改 verifier 实编、沙箱资源成本
〔env 构建 CPU/墙钟 ×簇数〕入 D33 双轨披露面）→ G1（含红线配置与 env 缓存，
量级最大）→ **G2 前置离线试跑（补强：E 组 smoke 2 voted 簇已知答案——PoC 生成 →
process 档真执行 → 比对预裁决单 expected_verdict；G1 最低可用档即开跑，不需
verifier 入编）** → G2/G3（PoC+接线，与 G1 可并行开发 FakeRunner）→ G4 仲裁件
（小）→ G5 暗考接线（小）→ G6 遗留清账 → E 组真蜂联调（verifier 入编按 D41
级联——联调窗口不赌一次考试；真沙箱 smoke：真簇 → PoC → 沙箱 → 三分类 →
S2 存款全链）。
量级对标：设计账 S2 占工程量 ~25%（两大重件之一），实现会话量预计 ≥ M4；
真蜂联调成本新增 verifier 考试 + smoke 沙箱轮（调用面 + 沙箱墙钟），预算口径
沿用 D33 双轨 + 沙箱资源成本披露扩展（R7）。
