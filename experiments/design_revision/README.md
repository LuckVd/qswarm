# experiments/design_revision —— 设计调整实验体系（方案 §7；P1 工作包 E 登记）

本目录是方案 `docs/DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md` §7.1 的
登记面：获批且冻结的实验定义（manifests/）、预算消耗登记（budget/）、
确定性判据 runner（run_e0_checks.py）。**本目录零账本写、零生产表**——
实验工件只落 `out/design-revision/<experiment_id>/`。

## 执行顺序（协议）

1. **E0 仪器与标签可靠性（前置，方案 §7.2）**——`run_e0_checks.py` 全过是
   P1 退出条件之一；人工校准面（20–30 例负责人判 + 跨族裁判一致率）题目已
   随 `manifests/e0_calibration_set.jsonl` 冻结，**供给与调用待批（D47-A：
   E1 开工前另行批）**；
   - **校准卷修订链（2026-11-10）**：v2（已判旧卷，带提示/重复/缺驱动，成绩仅描述性）
     → v2.1（显式主张+完整驱动+注释剥离，冻结未判）→ **v2.2（当前卷）**：4 个 bank
     展示本族别标识符中性化（cmdi/CWE23/OMITBAD→中性 token），fixture 与 v2.1 逐字节
     恒等，真值逐字不变；生成器 fail-closed + 负向门禁 + runtime 18/18；
     `calibration_v2_2/materials_v2_2_manifest.json` 为卷级真源。原负责人独立盲判
     方案保留于 `out/design-revision/owner-blind-v22-20261110/`，不改原卷与空表。
     **2026-11-11负责人改为接受AI辅助复核**：负责人面已按明确授权登记为
     `human_reviewed_ai_assisted`，不是独立人工盲判；25项claim与18项PoC已填写。
     派生表为 `out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl`，
     评分须显式传 `--sheet`。详情见[本轮记录](<../../docs/CALIBRATION_V2_2_OWNER_ASSISTED_20261111.md>)。
     **2026-11-11 裁判轮已执行，预登记门通过**：门槛（judge×owner claim ≥23/25）
     与裁判身份（GPT 6.1sol，全新无工具会话）先于执行冻结
     （[预登记](<../../docs/CALIBRATION_V2_2_JUDGE_PREREG_20261111.md>)）；
     实测 **judge×owner claim = 25/25，分歧 0**（PoC 18/18 描述性；judge×truth 25/25）。
     门判定按预登记人工登记（DECISIONS 2026-11-11「v2.2 裁判轮执行与门判定」行）；
     工件 `out/design-revision/judge-v22-20261111/`。**E0 校准面收口；通过≠E1 放行**——
     E1 供给批准（D47-A）与 manifest 冻结为下一拍板点。
2. E1 专长稳定性 → E2 同预算架构消融 → E3 时序学习（各随 manifest 冻结后开，
   逐门批准，未批不调用）。

## manifest 冻结面（方案 §7.1 逐字）

每个 manifest 锁定：数据哈希、root-cause 分组、开发/验证/保留集、profile 与
prompt 哈希、paper manifest、预算、采样参数、重试规则、停止规则、主要指标、
最小有价值效应、统计分析、随机种子、软件版本、允许的联网范围与审批记录。

纪律（§7.1）：相同源码变异/同一 CVE 多快照/漏洞-修复对同组；用过的保留集不能
改名当未见集；看过后继续调试的保留集升级为开发数据；统一评测显式传 paper
manifest；诊断实验与正式资格考试分离（实验新卷 ≠ 获准 re-roll 入编）。

## 预算登记（D46 冻结值；**2026-11-10 负责人拍板移除硬上限**）

- ~~硬上限 ¥500 / 墙钟 4 周~~ **已移除**（DECISIONS 2026-11-10「预算门禁」行）；
  消耗转为不设门的尽力登记 `budget/budget_ledger.jsonl`；
- 首笔线上调用：2026-11-10 E0 跨族裁判盲判（GPT 6.1sol，25 题，v2 旧卷），金额未登记；
- 第二笔：2026-11-11 v2.2 裁判盲判（GPT 6.1sol，25 题，门通过 25/25），金额未申报；
- D46 其余红线（跨族裁判/禁同族、未达标不重抽题）不受影响维持；
- 历史：截至 P1 离线批零线上调用零消耗（ledger 首行为原 meta 记录）。

## 红线（D46 + 方案 §13）

跨族强 LLM 兜底禁同族：glm-5.3 与施工 AI 本体均不得任主裁判/盲审；任一实验
臂含与裁判同族模型 → 该对照作废或如实披露偏倚。未批准不调用。
