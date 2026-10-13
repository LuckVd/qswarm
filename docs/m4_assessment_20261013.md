# M4 开工前评估（2026-10-13）

> 评估依据：设计文档 v0.3.4（§2.1/§2.4/§4/§5/§10/§11.6/§16.6）、SPEC-7 v1.0 交接面、
> `out/_m3_final/M3_DELIVERY_REPORT.md` §5/§6、`docs/HANDOVER_20261012.md` §12、
> `docs/spec/DECISIONS.md` 至 bfd5201、代码基座实测（全量 pytest 673 passed，
> 本日复跑留痕）。本文只评估不拍板；决策点清单见 §6，供负责人裁决与 SPEC-8 导出用。
> **修订（2026-10-13 自审轮）**：撤销 G4（事实错误，§6.5 归属矩阵 M1 已全量实装）；
> R1 扩写为供给天花板结构性约束；新增前置件 M3→M4 放行记录、决策点 D37 与
> 疑问清单（§6 末）；R3 补时延量化。

---

## 0. 一句话结论

M4 的确定性机制地基比典型里程碑厚得多——L2 会诊所需的全部内核写口与纯函数面
（findings/evidence_nodes/votes/verify_results 四写口、两级聚类、聚票与面板共识、
L2/S2 配额与熔断、route 纯函数、五角色提示词含注入防线）在 M0–M3 已全部落地且有
测试钉住；M4 的真实工作量集中在**生产编排层**（L1 存活点 → auditor 派单 → 聚类 →
评审投票 → 预裁决单的循环驱动 + repo map 切片供给）与**五个需负责人拍板的决策点**。
最大的非代码前置件是 E 组在线补播考（已裁决为 M4 生产联调前置件，命令就绪）；
最大的结构性风险是**评审团编制数学**：现编制与名册展开下面板 SIZE=3 无法成军（§5-R1）。

## 1. 交付面定义（口径对齐）

按 spec/README 里程碑表与 HANDOVER §12 的 M4 口径（依赖序第 7 位 + v0.3.4 增补）：

| # | 交付面 | 设计依据 | 内容 |
|---|---|---|---|
| F1 | L2 会诊生产编排 | §16.6-7「L2 审查 + 评审：证据链 schema + 跨模型族投票」 | L1 存活点 → auditor 深诊（产 finding+证据链）→ 粗聚类 → 评审 S1 投票 → 精聚类 → verifier 预裁决单（D29）→ finding 状态机流转至 voted |
| F2 | repo map 切片注入 | §2.1 v0.3.4 补 | 文件树 + import/依赖图 + 调用点索引（M3 地基）+ 历史扫描摘要；派单按「候选点 ± N 跳邻域子图」切片注入 L2 上下文（N=hop_budget_for(mode)） |
| F3 | route() 生产接线 | §5.2 + SPEC-7 §2 留口 | auditor 派单从 temp 池 least-loaded 切换为 route() 口碑路由（active 格 C* 降序 K=3 + 自适应 TILT + 探索通道）；route_thompson 保持对照 |
| F4 | 影子期结束裁决接线 | §16.6-9 影子运行（安全阀1） | M3-7 的直晋三通道零注入换为「负责人裁决后按 M0 定稿参数开闸 + 暗考回调 exam 接线」；M4 交付观察指标面，裁决本身归人 |

范围边界（建议 SPEC-8 明示）：S2 真沙箱是依赖序第 8 位（M5）——M4 的 verifier 只到
D29 预裁决单，finding 终态到 voted/预裁决，verified(L3) 不在本里程碑；仲裁/报告件
（§4.2 蜂后顾问摘要、分级结论输出）未在依赖序单列，是否入 M4 见 §6-D34。

## 2. 基座盘点（已有落点，全部可复用）

| M4 需求 | 已有落点 | 状态 |
|---|---|---|
| finding + 证据链写口 | `kernel/dispatch.py` `_submit_auditor`：findings + evidence_nodes 行 + finding 事件同事务，F-{date}-{seq} 签发（D20） | ✅ M1，测试钉 |
| 粗聚类/精聚类/归属 | `kernel/cluster.py`：coarse_mergeable/cluster_findings/apply_merges/split_cluster/attribute_findings/fine_mergeable(sim_fn) | ✅ M1 + M0 仿真接通真实聚类（sanctioned 重冻） |
| 聚票与面板共识 | `cluster.aggregate_votes/close_panel` + `dispatch._submit_reviewer`（votes 行 + vote 事件 + UNIQUE(finding_id,reviewer) 守卫）+ `record_panel_consensus`（open→voted 迁移） | ✅ M1 |
| §6.5 归属矩阵全量实装 | `cluster.close_panel → propagate_verdict → _expansion_rows`：成员报告者行（auditor ×1.0 / scout ×SCOUT_X）+ 侦察行 + 评审回校行（与 verdict 同向 +/反向 −，×REVIEWER_X，**S1 即触发**——§6.5 表「verdict 到达时回看投票方向」逐字）+ S3 验证行（±VERIFIER_CONFIRM/CONFLICT）；S1_SOURCE_W=0.05 = §10.3「0.05–0.1」区间下沿 | ✅ M1（自审轮核实，原 G4 撤销） |
| verifier 写口 | `dispatch._submit_verifier`（verify_results）；D29 预裁决三字段的消费形态需 SPEC 核对 | ✅ 写口在，形态待核 |
| L2/S2 配额与熔断 | `kernel/budget.py`：budget_for_mode/quota_l2(cap 45)/quota_s2/s2_rank_key/budget_state/on_cost；`dispatch.expire_run`（零存款 expired） | ✅ M1 |
| 路由纯函数 | `kernel/route.py`（active K=3、TILT、80/20 探索、least_loaded、load/max_load 参数齐备）+ `route_thompson.py`；`reroute.guess_taxonomy`（L1 猜测→路由初值） | ✅ M0/M1，M3 C 组池非空断言 |
| 契约注入点 | `dispatch.build_contract(context_refs=…)`——repo map 切片以 artifact:// 引用注入，参数已留 | ✅ 留口即用 |
| L2 输入 | L1 存活点 = candidates 表 triaged 行 + guess 列（M3-4 口径）；candidates 写口 `dispatch.register_candidates` | ✅ M3 |
| repo map 地基 | `perception/indexer.py`：tree-sitter 调用点/标识符/函数定义三档索引 + blake2b 内容寻址缓存（§2.1「未变不重析」逐字） | ✅ M3 本体；邻域子图切片/ import 图为增量 |
| 五角色提示词 | `prompts/templates.py`：scout/auditor/reviewer/verifier/queen_advisor 五 SYSTEM+SCHEMA_FIELDS；INJECTION_GUARD 防线段五模板共享尾部（§11.6-1 逐字） | ✅ M2（SPEC-3） |
| 评审/验证存款旋钮 | params：panel.SIZE=3/MIN_FAMILIES=2；deposits.SCOUT_X=0.2/REVIEWER_X=0.5/VERIFIER_CONFLICT=0.5/VERIFIER_CONFIRM=0.25 | ✅ M0 定稿 |
| 考试补播面 | exams：judge 四角色、`--roles`、纸面考卷（reviewer 送审 finding 块/verifier 预置簇块）；E 组命令就绪 | ✅ M3 |
| 开工门禁 | `bootstrap.verify_ready`（4 active + ≥20 考试存款 fail-closed）；现 prod 编制 4 蜂全 active（v0.3 播种库实证：2 scout + 2 auditor） | ✅ M3 |
| 注入防线 | 五模板共享 INJECTION_GUARD + 伪造分隔行通告 + 蜂零写权（§4.5 单写者）+ 工件不可执行 | ✅ 1/2/4 道防线在；第 3 道（注入痕迹→候选）L1 有 injection_flag 字段，生产消费面需测试钉 |

**结论**：M4 没有任何「机制重写」风险点；全部缺口是编排与供给层的新建面。

## 3. 缺口清单（M4 新建面，G1–G7）

- **G1 L2 会诊编排**：loop.py 扩展或新编排模块——candidates triaged 行 → auditor 任务化
  （route() 选蜂，F3 本体）→ FakeAdapter/真蜂执行 → `_submit_auditor` 建档 → 簇提案
  （attribute_findings）→ 面板派票 → aggregate_votes/close_panel → 精聚类 → 预裁决单；
  与 budget 的 quota_l2/on_cost 熔断接线（full 档降级语义 §2.4）。模块归属（loop 内 vs
  新模块）由 SPEC-8 定，硬纪律仍是「只复用不重写」。
- **G2 repo map 切片器**：indexer 三档索引之上加 import/依赖图（无 grammar 语言 import
  正则兜底，§2.1 逐字）+ 调用点邻域 N 跳展开 + 切片渲染为 context_refs 工件；缓存复用
  内容寻址。pr=1 跳 / full=2 跳 / fast=0 跳（hop_budget_for 已定）。
- **G3 面板构造器**（§16.7-6）：3 人、排除报告者、qualified 池 least-loaded、家族/档位
  多样性、池不足降 S1 权重——**qualified 池定义与编制数学是决策点**（§5-R1/§6-D31）。
- **G4（自审轮撤销，非缺口）**：原文「S1 存款双口径分立需实装」是**事实错误**——
  §10.3 的对报告者低权重 S1 存款与 §6.5 的评审回校行均已随 M1 的
  close_panel → propagate_verdict → _expansion_rows 全量实装（见 §2 表新行；评审
  回校行 S1 即触发，与 §6.5 表「verdict 到达时回看投票方向」逐字一致）。M4 在此面
  零新建，剩余工作只是 G1 编排把 VerdictContext（members/votes/scouts）装配起来。
- **G5 verifier 预裁决单生产消费**：精聚类簇 → 每簇一任务（§5.3 一簇一沙箱的前身）→
  D29 三字段 `{expected_verdict, poc_shape, env_requirements[]}` → 落工件供仲裁/人工与
  M5 沙箱排程。`_submit_verifier` 分支形态需与 D29 词表核对（不改词表）。
- **G6 影子期观察指标面**：影子账规模（格数/n_eff 分布/覆盖家族）、直晋候选格存量、
  暗考回调就绪自检——作为 F4 的交付物；开闸动作 = params 注入覆写撤除 + exam 回调
  实装（考试执行器已有）。
- **G7 测试与门禁面**：SPEC-5 M4 卷导出（A–E 组机器可检判据）；写口审计词表不动
  （votes/verify_results 写口已在 dispatch）；基线 673 零回归；e2e 离线全链
  （FakeAdapter）+ route 黄金回归扩展。

## 4. 前置件

0. **M3→M4 放行裁决的记录缺口（自审轮新增）**：HANDOVER §12.5-③ 要求「M3→M4
   放行裁决由负责人按 M3 交付报告门禁结论明示」；最新提交 bfd5201 只含 E 组补播
   考推迟/名册裁决（隐含 M4 为既成事项），DECISIONS 与 spec/README 里程碑表**未见
   显式的 M3→M4 放行行**。按项目纪律（人只审里程碑），开工前应补记——本轮评估
   本身可视作负责人启动该流程的信号，裁决落 DECISIONS 即闭合。
1. **E 组在线补播考**（负责人已裁决绑定 M4 生产联调前执行）：命令就绪（交付报告 §2.E），
   纪律同 M2——先探针（4–8 次调用验证请求名与实际服务模型）→ 新 DB + 全新缓存 →
   S9 位级重放对照。离线判分链路已绿；门禁判据=链路正确非必须及格。**但见 R1：
   名册展开 reviewer/verifier 各只产 1 蜂，补播前应先拍板编制口径**。
2. **生产账本尚无任何生产 run**：bootstrap/loop 未经真实仓库全链跑过（M3 全离线）。
   M4 联调首扫本身是新的风险面（时长/配额/错误恢复），建议 SPEC-8 含 smoke 档
   联调判据（单族小仓库 fast 档全链）。
3. **成本锚点校准**：budget.COST_PER_L2_CENTS=5 是调研期 per-token 美分锚点；智谱
   Coding Plan 是订阅制（响应 model 字段实测与请求名脱钩），成本记账口径（按订阅
   摊销 or 名义价）需拍板，否则 quota_l2 的「预算换配额」语义在生产面失真（§6-D33）。

## 5. 风险与结构性发现

- **R1 供给天花板（结构性，最高优先；自审轮扩写）**：`expand_candidates` 按档位
  primary+fallbacks 去重展开，agent_id ≡ (tier:provider/model@cv) 不可复制（§16.7-2）。
  现名册 mid/engineering 档均无 fallback 且同为 glm-5.3 → E 组补播后 **reviewer 池=1、
  verifier 池=1**，panel.SIZE=3 且排除报告者 → **面板永远无法成军**。**扩写：面板只是
  症状，根因是组织规模被供给面封顶**——agent 数上限 = Σ 各档 distinct (tier, model)
  条目，真实服务面仅 5.3/5.3-flash 两模型，即使 D31(a) 落地全组织也只有 ~7 蜂，远低于
  设计编制 §16.7-7 的 18 顶帽子（侦察6/审查5/评审4/验证3）。连带退化：route() 探索通道
  与 TILT 在 2 只 auditor 上动力学平凡；least-loaded 无梯度；UNIQUE_PER_FAMILY 下 2 只
  auditor 至多占 2 个家族专员席；跨模型族结构性不可能 → S1 统计价值封顶，**S2（M5）
  比设计假设更早成为承重件**。可选解：(a) mid 档加 glm-5.3-flash fallback →
  reviewer=2（把「跨族退化」变成真实的 5.3/5.3-flash 两档异构）；(b) 面板降规格
  （SIZE=2 + S1 降权，类推 §16.7-6 条款——注意实现会触 params.yaml 定稿面与钉值/
  黄金测试，需 DECISIONS 记录）；(c) 帽兼任——**这是设计原生答案而非变通**（§4.1
  「角色是帽子不是身份」逐字），但 reviewer 帽仍需过入职考（补播面已有）且利益隔离
  排除报告者后 6 蜂池可凑面板；根治 = 引第二供给商（见疑问 Q1，与 DECISIONS 既有
  「接受降级」裁决冲突，须负责人翻案）。**须 D31 拍板，补播考名册随之定**。
- **R2 跨模型族约束**：GLM 单族（真实服务面 5.3/5.3-flash 两档，DECISIONS 2026-10-12），
  §16.7-6「优先跨模型族」退化为跨档位；S1 权重降档公式需 SPEC 落地（§6-D32）。
  同底模错误相关性由 M0 仿真的 z_panel 共模项（ρ=0.4）定量覆盖——降权幅度有仿真依据可循。
- **R3 思考型模型运行点 vs 档位时延/成本目标**：GLM 5.x 思考通道实测单题 30s–3min、
  思考 ~10.3K tok（32K 输出预算）。**量化（自审轮补）**：pr 档设 10 个 L1 存活点，
  全链 ≈ 10×(auditor 深诊 1–3min + 面板 3 票并行 0.5–3min + 精聚类/预裁决 1 票) 串行
  ≈ **20–60+ min**，突破 pr「分钟级（CI 可用）」目标一个量级；SPEC-8 需拍板逐 run
  墙钟预算旋钮与并行派单口径（面板 1→N 天然可并行，loop 编排要真并发才吃到）。
  COST_PER_L2_CENTS=5 锚点与订阅制不符（见 §4-3）。M4 需拍板生产 L2 是否
  沿用 thinking enabled（考试/生产同点位可复现——M2 决策的延伸）或生产降档（§6-D33）。
- **R4 L2 路由冷启动**：auditor 两蜂的 cells 只有考试存款（entry origin，M2 播种），
  生产存款为零。route() 按 C* 路由在 M4 前期等于按考试口碑路由——这是 §8.4 双轨制
  设计内行为，但 M4 解读路由决策与 TILT 起浮时须区分 origin（cells 折叠含 exam 存款、
  权重上限 0.5 已定稿）；否则会误读「审计蜂分化」为生产能力信号。
- **R5 影子期结束裁决依赖生产数据**：F4 的观察指标要有意义的影子账，需 M4 联调真实
  扫描累积 L1/L2 生产存款之后；裁决大概率落在 M4 末期或 M5 前，不阻塞 M4 开发
  （FakeAdapter 全离线可测）。暗考回调接口已在 M3-7 留好（exam=None 占位）。
- **R6 注入防线覆盖**：防线段五模板共享已立；L2 深挖面把 repo map 切片（更多仓库内容）
  注入上下文，防线 1 的呈现纪律需在切片渲染器上同样生效并有测试钉；防线 3 的
  injection_flag 生产消费（痕迹→候选）目前只有字段没有消费断言，M4 判据应补。

## 6. 待负责人拍板的决策点（SPEC-8 开工前，D31–D37 候选）

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| D31 | 评审团/预裁决编制口径 | (a) mid 档加 glm-5.3-flash fallback（reviewer=2）/ (b) 面板降规格 SIZE=2+降权 / (c) 帽兼任扩池（设计原生，§4.1「帽子不是身份」） | (a)+(b) 组合：编制到 2 + 面板最小 2 人并按 §16.7-6 降 S1 权重；(c) 可作候补扩池手段（reviewer 帽需补考，面已有）；(b) 实现触 params 定稿面与钉值/黄金测试，须 DECISIONS 记录 |
| D32 | 跨档位异构的 S1 降权公式 | 固定系数 / 按 M0 z_panel 共模参数推导 | 按 M0 仿真参数推导，可追溯；**caveat（自审轮）**：ρ=0.4 是同族标定值，5.3 vs 5.3-flash 跨档对的相关性未实证——记为校准债，真票积累后回测 |
| D33 | 生产 L2 运行点与成本记账 | thinking 全开 / 生产关思考；订阅摊销 vs 名义价入 quota | 沿 M2「考试=生产同点位」纪律：thinking 全开；成本按订阅摊销记遥测、quota 用名义价并披露；**pr 档墙钟预算随本项一并定（R3 量化）** |
| D34 | 仲裁/报告件是否入 M4 | 含（蜂后顾问摘要+分级报告）/ 推 M5 与 S2 同交付 / 最小折中：仅落 voted+预裁决单的人类可读报告（md/json，无仲裁） | 推迟全量仲裁；**建议含最小报告**——否则 M4 交付无人类可读输出、演示价值薄弱，而最小报告成本远低于仲裁件 |
| D35 | 影子期结束判据 | 观察指标阈值（影子账格数/n_eff 分布）交负责人裁决 | M4 交付指标面，判据阈值 SPEC 给建议值、裁决留人 |
| D36 | fast 档 L2 缺席的 S1 面 | fast 档无 finding 无面板（§16.7-1 已答）按此执行 | 无需新决策，确认即可 |
| D37 | repo map v1 跨文件解析近似级（自审轮新增） | 精确解析（stack-graphs 级）/ 名称绑定近似（调用点↔定义名匹配 + import 边）/ 仅同文件邻域 | 名称绑定近似：现有三档索引可直接支撑、误差由 L2 深诊吸收（跨函数推理本就归 L2 agent）；精确解析成本不成比例 |

**疑问清单（非决策，澄清即闭）**：

- **Q1**：第二供给商（Anthropic/OpenAI/DeepSeek key）在 M4/M5 是否可能上桌？这是 R1
  供给天花板与 R2 跨族约束的唯一根治解；与 DECISIONS 2026-10-12「接受降级为跨档位」
  的既有裁决冲突，翻案须负责人明示。若不上桌，建议把「S1 降级 + S2 前置承重」
  记为长期立场而非临时妥协。
- **Q2**：pr 档「分钟级 CI 可用」目标在思考模式下（R3 量化 20–60+ min）是否放宽、
  或 pr 档允许 thinking 降档？（与 D33 同拍）
- **Q3**：D31 若走 (c) 帽兼任，兼任蜂的 reviewer 帽考试是否复用 E 组补播同一轮执行
  （一次网络成本覆盖两名册）？

## 7. 建议开工序与量级

```
1. §6 决策拍板（D31–D33/D37 为硬前置；D34/D35 可随 SPEC 期裁）
2. SPEC-8（M4 实现级规格）+ SPEC-5 M4 卷导出（滚动协议 1）
3. E 组补播考执行（探针先行；名册按 D31 定案后）
4. 实现（依赖序）：G2 repo map 切片（按 D37 近似级）→ G1 L2 编排（含 F3 route
   接线）→ G3 面板构造 → G5 预裁决单 → G6 影子指标
5. 门禁：A–E 组判据 + 写口审计 + 673 零回归 + e2e 离线全链 + smoke 档真仓联调
```

量级参照：M3 一天交付（61 新测试）；M4 新建面为编排/供给层且多一次真蜂联调与
一轮补播考，估计 **1.5–2× M3**，其中 D31 未拍板则 R1 直接阻塞 G3/E 组两条线。
