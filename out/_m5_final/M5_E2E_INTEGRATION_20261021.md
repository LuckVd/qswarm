# M5 E 组真蜂联调报告（2026-10-21 启动，2026-10-22 收口）

> 执行依据：`out/_m5_final/M5_DELIVERY_REPORT.md` §4（负责人指令「接下来该做什么
> 你直接做」放行，2026-10-21）。两件事：① D41 verifier 入编考（级联序列 a→b）；
> ② 真沙箱 smoke（container 档生产全链）。**结果：① 双腿如实执行、双双未过线；
> ② 结构性阻塞（无合格 verifier 可派，联调纪律下不可硬凑）**。事实面逐条如下，
> 决策点见 §6 待负责人拍板。

## §0 结果摘要

| 面 | 判定 | 一句话 |
|---|---|---|
| D41-(a) engineering:flash 重考 | **0.775 < 0.8 未过** | 旧口径 0.675 → 新口径 0.775（仪器修复提分 +0.10 属实），仍差 0.025 |
| D41-(b) strong:flash 帽入职考 | **0.725 < 0.8 未过** | 级联自动落 (b)；帽 QC 只告警不降编（strong 蜂本体仍 active，纪律面正确） |
| ② 真沙箱 smoke（container 生产链） | **受阻未执行** | 唯一可派 verifier（strong 帽 ∧ active）帽考未过线——派单面无 QC 门槛，跑了就是 D41 明文禁止的硬凑 |

R3（供血风险 / 全 flash 判码能力未实证）从评估文的风险判断转为**实测结论**：
两蜂同卷失分面高度一致（injection 0.625 / crypto 0.625 弱，mem_safety 1.0/0.94
强），显示的是能力边界而非仪器残差。

## §1 D41-(a)：engineering:flash 新口径重考

- 命令：
  `QSWARM_KEY_ZHIPU=... python -m qswarm.exams.seeding --bank seeds/bank.yaml
  --models configs/models.zhipu-flash.yaml --db out/m4-seeding-v3/prod.db
  --out out/m5-e2e/exam-engineering --roles verifier`
- 口径：bank v3 + S4 中性化 + FAMILY_GLOSS 单一源（新口径）；`--seed 0`（与 v3
  原考同卷，仪器修复重考先例）；全新缓存目录（真蜂调用零重放）。
- 实测：**61 model_calls / 60 rows / 5 格全有效**，schema_fail=0 infra_fail=0
  canary_hit=0；**deposits=0（exam_id 幂等，不重复供血）**；状态流转：留
  provisioning（apply_qc_decision 既有语义）。
- 逐格：injection 0.625 · crypto 0.625 · authz 0.875 · config_dep 0.75 ·
  mem_safety 1.00。
- 执行备注：首轮后台进程在 ~50/60 题处被会话工具取消连坐终止（无 QC 判定）；
  以 setsid 脱进程组原地重启，响应缓存命面前 50 题（同卷同答案），仅补 ~10 题
  真调用后正常收口——缓存即断点续考面，结果与一次全程无异（60 行如实重写）。

## §2 D41-(b)：strong:flash 帽兼任 verifier 帽入职考（级联）

- 不走 CLI 的原因：`--roles verifier` 会把 engineering 蜂陪绑重考（同卷同缓存
  同答案 0.775 已如实记档，60 次调用无新信息）；按 M4 smoke 先例写
  `out/m5-e2e/exam_strong_hat.py` 直调 `seed_entry(roles=(), extra_hats=...)`
  （与 CLI main 逐件同构：bank 装载/SnapshotStore/UrllibAdapter/报告落盘）。
- 实测：**70 model_calls / 59 rows / 5 格全有效**（SEED-0044 infra 无效 1 题
  如实剔除）；schema_fail=3（S7 契约失能判错）canary_hit=0；**deposits=5**
  （strong 蜂 verifier 角色格首考，新 exam_id 合法供血，S1 档不进晋升硬门槛）；
  **qc_alerts=[strong:zhipu/glm-5.3-flash@cv1]**（帽 QC 失败只告警不降编，
  seed_entry 对 extra_hats 跳过 apply_qc_decision——既定语义如实兑现）。
- 逐格：injection 0.625 · crypto 0.625 · authz 0.75 · config_dep 0.625 ·
  mem_safety 1.00。
- 状态面：strong 蜂本体 active（auditor 正编不受影响）；verifier formal 帽
  已挂（attach_hat 先注册后考试的唯一写口）；**帽摘除随 M5 编制管理面**
  （M5-3 既有 caveat，现为现实问题，见 §5-②）。

## §3 ② 真沙箱 smoke：受阻判据（如实，不执行）

- 硬前置 = 有过线 verifier 可派。现状：`_active_by_role`（consult.py）派单门
  = **帽 ∧ active 双条件，无 QC 门槛**——strong 蜂（帽考 0.725 未过）恰在此
  门内。此刻 `run_verify(tier="container")` 会正常派单并产生产 ×0.5 存款：
  未过线蜂入生产账本 = D41 拍板原文「不硬凑纪律不变」所禁止面。
- 因此 ② 不执行。「无 verifier fail-closed」判据（C 组）pytest 已证
  （test_m5_verify.py），生产账本上再证一次只会多写一行零 spent 的 run 行，
  无增量信息。
- 联调素材与脚本就绪待命：`out/m5-e2e/smoke_verify.py`（三面判据打印 +
  R7-② 披露面）；docker 禁网容器实跑、python:3.11 基底镜像、G2 试跑基线
  （match 2/2）均已在位——**合格 verifier 一到编即可全链**。

## §4 成本披露（D33 双轨口径；考试面按调用计数）

- 轮次 ×1：D41-(a) 61 calls（含断点重启，其中 ~50 缓存命中零网络）+
  D41-(b) 70 calls（3 题 attempt-2 重试计入）= **131 calls 全 flash**。
- 沙箱面：本轮 0 次（② 未执行）；基线面 G2 试跑 match 2/2 时墙钟 0.1s（非
  LLM，M5 交付报告 §3 已披露）。
- smoke 待命成本预估：2 簇 × verifier 一体产 ≈ 2–4 calls + 沙箱墙钟。

## §5 结构性发现（联调实测，记档供拍板）

- **① R2 实锤升级**：编制无合格 verifier 非「工程蜂旧口径分数未过」个案，
  而是全 flash 供给面在 verifier 帽考上的能力边界（两蜂同卷 0.775 / 0.725，
  失分同构）。进化闭环 S2 供血为零的现状（n_eff(S2+S2) 缺口）在供给不变前提
  下不随重考自愈。
- **② 派单门 QC 门槛缺失**（新发现）：`_active_by_role` = 帽 ∧ active，无
  帽考过线约束。帽 QC 失败只告警不降编（D31 语义，属蜂本体）+ 帽摘除随编制
  管理面（无摘除写口）→ **未过线帽蜂在制度上仍处派单池**。M4 期未暴露因帽考
  全过线；本轮首次触发。候选处置（归负责人）：派单门加格权/过线约束，或
  attach_hat 补 QC 摘除写口，或维持现状以编制管理面人工时序约束。
- **③ 失分面诊断**（重考面证据）：injection/crypto 双格 0.625 为共性弱项
  （fp 侧拒判尚可、real 侧漏判为主——schema_fail 3 题全落 real 件）；mem_safety
  满分/近满分。若走定向补强路线（题面/提示词/供给），弱项格是第一杠杆。

## §6 待负责人拍板（D41 序列耗尽后的下一步）

- **D44 候选 · verifier 入编破局路径**（D41 级联 (a)→(b) 双未过线）：
  - (a) **供给面换档**：verifier 帽考挂 glm-5.3 旗舰请求名（configs/models.zhipu.yaml
    strong 档；M4 探针实测旗舰服务）重考 strong 蜂——能力档实质不同，过线预期
    最高；代价 = 供给配置变更（编制管理面）+ 调用成本上浮。
  - (b) **re-roll 重考**：同蜂新卷（seed≠0）。考试纪律上 re-roll 须负责人明示
    批准（M2 先例重考均为仪器修复非 re-roll）；flash 两蜂同卷波动 ±0.05 量级，
    (a) 0.775 过线概率存在但不高。
  - (c) **接受推迟**：S2 生产链联调挂起至合格 verifier 入编；§5-② 派单门缺口
    同步记工程欠账；影子期票流与 L2 面不受影响（verifier 缺位只封 S2）。
- **D45 候选 · 派单门 QC 门槛**（§5-②）：同上三选一或不动作，随 D44 一并拍。
- 本报告不作任何上述选择；② smoke 脚本与素材保持待命态。

## §7 证据清单

| 文件 | 内容 |
|---|---|
| `out/m5-e2e/exam-engineering/seeding_report.{json,md}` | D41-(a) 组织面报告（QC 0.775 / deposits=0 / 60 rows） |
| `out/m5-e2e/exam-engineering/cache/`（本地面，不入库）+ `store/` | 响应缓存（断点续考实证）+ 考试工件（exam_log 61 件） |
| `out/m5-e2e/exam-strong-hat/seeding_report.{json,md}` | D41-(b) 帽考报告（QC 0.725 / deposits=5 / 59 rows / qc_alerts） |
| `out/m5-e2e/exam_strong_hat.py` | 帽考脚本（seed_entry roles=() 直调，§2 理由） |
| `out/m5-e2e/smoke_verify.py` | smoke 待命脚本（三面判据 + R7-② 披露） |
| 账本 `out/m4-seeding-v3/prod.db` | exam_results +120 行 / cells verifier 5 格 / hats verifier ×2 / agents 状态两处如实 |
