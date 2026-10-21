# 实施规格系统（spec/）

> 定位链条：**设计文档**（`../swarm_design_discussion_20260929.md`，v0.3.4）→ **规格**（本目录，实现级，无歧义）→ **代码**（`qswarm/`，AI 施工）。
> 设计文档回答"建什么、为什么"；规格回答"字段叫什么、类型是什么、公式是什么"；代码不引入规格之外的设计决策。

## 里程碑状态（2026-10-11）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| M0 | 机制仿真器（SPEC-4 §3 全矩阵 + §5 门禁） | ✅ 定稿放行（2026-10-09；M1 接线后 workers=3 复跑批 162/162×9/9 维持，`results/M0_GATE_FINAL.md`） |
| M1 | 编排内核：聚类归并 + 预算熔断 + 委托状态机 | ✅ **交付闭环**（2026-10-11：终检 528 全绿 + 遗留 L1 `submit()` 当日按方案 (a) 补实现关闭 → 535 全绿，门禁 §9.1 全项通过；`out/_m1_final/M1_DELIVERY_REPORT.md` + 附录 E） |
| M2 | 种子题库 v0 + 考试播种（§16.6 依赖序第 4 位：组织出生前置件） | ✅ **放行（2026-10-12 负责人拍板）**：代码全量落位，A/B/C/D 组判据全绿；E 组题库判据经 v1+v2 双轮播种 + S9 位级重放 + 答案键缺陷披露与 29 题复核闭环（18 键正确/7 键坏/4 家族键不可达）+ v2 修正重判四蜂全达 QC——v0.2 题库真值缺陷根因为 Juliet 上游空壳模板（非变异引擎），v0.3 防线已实装（taint 存活校验）；**遗留已闭合：第三次重考完成（2026-10-12 晚）**——v0.3 题库（bank_version=2）在线播种 240/240 行 + 20/20 存款，四蜂全过 QC 0.8 并 active（真题口径 air 1.0000 / 5.2 0.9750 / 4.6 0.9500 / glm-5 0.9250；strong 档登顶确认 v2 修正重判结论），schema_fail=1 / infra_fail=0 / canary_hit=0，S9 位级重放逐位一致（glm-5 后三格 ER 行号序因幂等续跑与规范序不同，内容面逐位相等）；`out/m2-seeding-v03/`、`docs/HANDOVER_20261012.md` §11）（`docs/answers_key_audit_20261012.md`、`docs/m2_gate_brief_20261012.md`、`out/_m2_final/M2_DELIVERY_REPORT.md`） |
| 下一步 | M3：感知层 L0 v2 + L1 侦察路由 v1（§16.6 第 5/6 位）；promote_scan 生产接线（孵化直晋通道）；reviewer/verifier 补播 | ✅ **实现落位（2026-10-12）**：`qswarm/perception/`（三通道 L0：clues/indexer/channels/candidates/scan/prompts + 内容寻址缓存 + 探测式语言分级 D25）+ `kernel/loop.py`（L0→L1 流水线 + temp 池 least-loaded + ×0.2 存款 + 影子 promote_scan）+ `kernel/bootstrap.py`（D28 空账本引导）+ exams 补播面（judge 四角色 + `--roles` + 纸面考卷）+ semgrep 必选件实跑门禁（D30，真 semgrep 子进程判据）；A/B/C/D 组判据全绿、全量 pytest 全绿（611 例零回归 + M3 新增判据），E 组在线补播考按卷面口径待负责人放行（离线判分链路已绿）；实现期决策 M3-1～M3-9 九行记 DECISIONS；`out/_m3_final/M3_DELIVERY_REPORT.md` |
| M4 | L2 会诊 + repo map 切片注入 + route() 生产接线 + 影子期指标（§16.6 第 7 位）| ✅ **实现落位（2026-10-13）**：`qswarm/perception/repomap.py`（D37 名称绑定近似 + indexer all_calls 第四档）+ `kernel/consult.py`（route() 生产接线 F3 + 粗聚类消费 + 面板投票 D31 帽兼任 + S1 收口 D32 因子 + Jaccard 精聚类 + 预裁决单 D29 + 预算熔断 + 影子指标 + 最小报告 D34）+ exams 补播扩展（attach_hat/--hat-agents）；全量 pytest 697 全绿（673 基线零回归 + 24 例新增）、写口审计 PASS(--strict)；实现期决策 M4-1～M4-5 记 DECISIONS；**E 组真蜂联调未执行**（前置件绑定，命令就绪 `out/_m4_final/M4_DELIVERY_REPORT.md` §4）；M4→M5 放行裁决待负责人 |
| M5 | S2 沙箱 + 真验证 + D34 仲裁件 + 暗考接线（§16.6 第 8 位，两大重件之二）| ✅ **实现落位（2026-10-21，D38–D43 拍板单轮闭环）**：qswarm/sandbox/ 双档 runner（container 五项红线真机实测全绿 / process 近似档，D38）+ run_verify 独立入口（D40-b；D43-a process 零账本写 / container 唯一生产口径；R7-① quota_s2 verifier 实编）+ PoC 双源（D39 蜂一体产双围栏 + 家族模板）+ G2 前置离线试跑实跑 match 2/2（E 组 smoke 真素材）+ run_arbitrate 最小仲裁件（D42-a）+ 暗考接线默认关（G5）+ G6 遗留清账（taxonomy types 面/存款版本化刷新/rules_runner 装配/路径归一/pv 三态 + M4 判据缺口四条收口）；SPEC-9 v1.0 + SPEC-5 M5 卷同轮导出；全量 pytest 754 全绿（712 基线零回归 + 42 新增）、写口审计 --strict PASS、M0 黄金零触碰；E 组真蜂联调（D41 级联入编考 + 真沙箱 smoke）留待负责人放行执行  |

## 规格索引

| 编号 | 内容 | 状态 |
|---|---|---|
| SPEC-1 | 数据契约：枚举 / SQLite DDL / pydantic 契约 / 配置文件 schema | ✅ 前置（地基） |
| SPEC-4 | 机制仿真器数学规格 | ✅ 前置（M0 的直接输入） |
| SPEC-2 | 编排内核接口（route/dispatch/cluster/budget/状态机函数签名） | ✅ 已导出（2026-10-10，M1 开工前） |
| SPEC-5 | 验收测试与门禁清单（按里程碑机器可检） | ✅ M1 卷 + M2 卷已导出；**M3 卷已导出（随 SPEC-7 v1.0 定稿）** |
| SPEC-3 | 蜂角色提示词模板 + 输出契约（5 角色，精度关键件） | ✅ 已导出（2026-10-11，M2 开工前；实装随 M2/M3） |
| SPEC-6 | 种子题库 v0 + 考试 runner + 考试播种（含供给适配层） | ✅ 已导出（2026-10-11，M2 唯一真源） |
| SPEC-7 | 感知层 L0 v2 + L1 侦察路由 v1 + promote_scan 接线 + reviewer/verifier 补播（M3 实现级规格） | ✅ **定稿 v1.0（2026-10-12，D25–D30 六项全部负责人拍板；D26/D28/D30 按裁决偏离建议稿，逐条见 §9 与 DECISIONS 同日行）** |
| SPEC-8 | L2 会诊 + repo map 切片注入 + route() 生产接线 + 影子期指标（M4 实现级规格） | ✅ **定稿 v1.0（2026-10-13，D31–D34/D37/Q1 拍板见 DECISIONS 同日行；验收卷 SPEC-5_m4_acceptance 随日导出）** |
| SPEC-9 | S2 沙箱 + 真验证 + D34 仲裁件 + 暗考接线（M5 实现级规格） | ✅ **定稿 v1.0（2026-10-21，D38–D43 拍板见 DECISIONS 同日行；验收卷 SPEC-5_m5_acceptance 同轮导出；E 组真蜂联调轮为后续前置件）** |

## 滚动协议（负责人已拍板：混合模式）

1. 每个模块开工前，先从设计文档导出该模块的 SPEC（沿用编号），未定细节在 SPEC 的"决策记录"节内拍板；
2. 编码 AI **只按 SPEC 实现**——SPEC 没写的按最小惊讶原则实现并记入 `DECISIONS.md`（视同技术债，需复盘）；
3. SPEC 变更必须同步 `DECISIONS.md`（日期/模块/决策/理由/影响五字段），设计文档只在机制级变更时才升版；
4. 人只审里程碑（验收标准见设计文档 §16.6 与各 SPEC 的门禁节）。
