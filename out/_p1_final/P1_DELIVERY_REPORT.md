# P1 交付报告：可信测量最小闭环（SPEC-10 v0.2 §6–§9）

日期：2026-10-24 · 依据：DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022（方案 §4–§7
工作包 B/C/D/E0 + §10-P1 阶段门）· 放行：负责人会话指令「开工」（P1 四门整体）
· 决策点 D47/D48 未获答复、按评估 §6 建议值 A/A 保守执行（零调用路径，正式
拍板点保留至 E1 供给批准时追认，budget_ledger 如实标注 provisional）
· 评估：docs/p1_assessment_20261024.md · 规格：SPEC-10 v0.2 + SPEC-9 v1.1 §10

## §0 门禁自检（方案 P1 退出条件）

| 退出条件 | 结果 |
|---|---|
| E0 通过 | ✅ 确定性判据面：run_e0_checks.py 五组全过（C1 九类负对照真机 20 项 / C2 模板回归 6 项 / C3 指标分母 / C4 投影确定性与纠错 / C5 漏斗装配），报告归档 out/design-revision/e0-20261023T190618Z/；人工校准面（25 例题目已冻结）与裁判选型待批（D47-A），不属本轮 |
| 能区分脚本失败 / 主张不成立 / 无结论 | ✅ claim_validation + reason 判层面：execution_failed/timeout（脚本失败）、refuted（仅 §10.4 blocking 路径，不出自执行判据）、inconclusive（含 ⑨「真实漏洞但 PoC 无效」硬类——绝不 verified_fp） |
| 重复和纠错不增加根因数 | ✅ C4 + test_evidence_projection 钉：同簇二次判定 supersedes、独立根因数不变、有效视图单条；重复投票/finding 同 |
| 红线 | ✅ 零线上调用、零生产写、零 DDL、零新 LedgerKind、不扩公共枚举、M0 黄金零触碰、reconcile 三分类 outcome 行为零改 |

## §1 交付面

**新文件**
- `qswarm/sandbox/oracle.py` — 判据模块 oc-v1：OracleKind 五值（D46 四层级 +
  mechanical_single 降格形态）、ClaimValidation、validate_pair 成对判据（修复版
  环境失败≠负对照通过；refuted 不出自执行判据）、观察器分离（冻结驱动只驱动只
  记录；宿主侧 observe_injection/observe_traversal 机械判读；ASSERT 自报永不
  参与判定）、assess_single 内核单次执行面（冻结断言白名单 + 对照门 + 蜂产
  自报非独立如实 inconclusive）
- `qswarm/sandbox/fixtures/` — 九类验收集两族×六目标变体（vuln/fix/echo_safe/
  unreachable/missing_dep/timeout）+ 四驱动变体（含 unconditional_pass/
  no_target_call/ineffective）+ NINE_CLASSES 期望表
- `qswarm/kernel/evidence.py` — evidence_v2 只读投影（§4.2 字段逐字；存量缺
  记录如实 unknown；supersedes/disputed；同 as_of 同投影哈希；capability_v2
  TP/FP/U + precision 界公式；零 SQL 写）
- `qswarm/kernel/funnel.py` — 七层漏斗 trace 装配 + 金标准回查 first_loss_layer
  （L0/L1 逐条不可得 → not_assessable，不伪造定位）
- `experiments/design_revision/` — README（协议/预算/红线）、manifests/
  e0_manifest_template.json（§7.1 冻结面）、manifests/e0_calibration_set.jsonl
  （**25 项冻结** = 夹具 18 + 题库分层 7）、budget/budget_ledger.jsonl（¥500/4
  周、起算锚 provisional）、run_e0_checks.py（E0 确定性判据 runner）
- `tests/{test_s2_oracle_controls.py, test_evidence_projection.py, test_funnel_trace.py}` — 37 例新增

**修改**
- `qswarm/sandbox/pocgen.py` — 模板 v2：echo-marker → 副作用金丝雀（注入 =
  目标进程内创建含 marker 文丝雀文件；穿越 = nonce 只在允许根外文件内容里 +
  legal_control 对照门）；冻结断言名白名单 VIOLATION/CONTROL_ASSERTIONS
- `qswarm/sandbox/runner.py` — container 档 tmpfs `mode=1777` 钉死（环境漂移
  修复：docker 29.2.1 缺省 0755 → nobody EACCES，「可写点」红线名存实亡）
- `qswarm/kernel/verify.py` — 双层结果接线（assess_single → result.json
  oracle 节 + report claim_validation 计数与 detail 行；三分类 outcome 零改）
- `tests/test_m5_sandbox.py` — 模板钉值升 v2（旧 echo-marker 词为已修仪器缺陷）
- `docs/spec/{SPEC-10 v0.2（§6–§9 骨架升精度面 + §11 增 7 行决策）、
  SPEC-9 v1.1（§10 增补）、README 索引、DECISIONS 2026-10-24 P1 行}`

## §2 判据结果

| 判据 | 结果 |
|---|---|
| 全量 pytest | **806 收集全绿**（769 基线零回归 + 37 新增；含 container 档真机判据，docker 桥接环境 bind mount 当日重挂后复验） |
| E0 runner 真机 | C1–C5 全 PASS（out/design-revision/e0-20261023T190618Z/report.json） |
| 写口审计 | `python -m qswarm.kernel.write_guard --strict` → **PASS**（违规 0 / 漂移 0；零新表零 DDL，词表 16 表不变） |
| M5 兼容 | reconcile 三分类 outcome / verify_result 载荷 / 黄金文件零触碰；claim_validation 为纯报告面增量 |
| 预算 | 零线上调用、零消耗（budget_ledger 仅 meta 行） |

## §3 开工前实证发现（评估 §5 兑现情况）

- **R1（已修复）**：旧 pocgen echo-marker 断言可被「仅回显安全程序」满足——
  模板 v2 + 九类第 ③ 类钉死（C2 回归判据）。M5 试跑 match 2/2 的口径据此重述
  （历史工件不改，SPEC-9 §10 记档）。
- **R2（如实）**：题库 100 题单快照无修复版配对——成对对照全部自建受控夹具
  （§6.3 许可）；上游真值层在 Python 两族面覆盖极薄已入 E0 校准集分层。
- **R3/R4（如实）**：生产账本近乎空投影（无生产 run）——B 投影判据建立在
  合成/试跑账本；存量 selection_probability 全 unknown，加权估计不可做。
- **环境漂移（连带修复）**：docker server 29.2.1 tmpfs 缺省 mode 0755（10-23
  通过属版本缺省差异）——mode=1777 钉死后 container 真机判据复验通过；
  bind mount 不跨 WSL 重启已重挂（DECISIONS 2026-10-23 配方）。

## §4 下一步（P2 前置，逐门待批）

1. **D44 正式了断**（建议口径不变：合格 verifier 经新资格体系考取，或引入
   跨族供给——供给变更需另行批准）；真沙箱 smoke 等 D44 不等 P2。
2. **E0 人工校准面执行**：**校准配套已全部就位**（2026-10-24 追记：
   experiments/design_revision/calibration/——25 例盲卷 judging_sheet +
   裁判盲卷 judge_materials + 裁判提示词模板 + 一致率计算器 + RUNBOOK）；
   待负责人两件事：①判 25 例（约 30–40 分钟）②跨族裁判供给拍板（GLM 族
   禁用红线；施工 AI 任判定 = D46 红线所禁且对自建夹具结构性自证）；
   **首次线上调用即触发预算起算锚（D48 建议值待追认）**。
3. P2 放行评估（E1 专长稳定性 + E2 A/B/C 消融；数据分组切分/paper manifest/
   停止规则随 manifest 冻结后逐门批准）。
