# E0 校准执行手册（RUNBOOK）

> 状态：材料与工具已就位（2026-10-24）；**待负责人两件事**——① 25 例判定、
> ② 跨族裁判供给拍板。执行序如下，全程零改动冻结工件。

## 步骤 1｜负责人判 25 例（约 30–40 分钟，唯一人工步骤）

1. 打开 `judging_sheet.jsonl`，逐项读 `materials`（目标代码 / 快照），按
   `question` 在 `owner_judgment` 填 `real` / `refuted` / `inconclusive`
   （可加 `owner_note`）；
2. **判完再**看 `ai_reference.jsonl`（施工 AI 建议，D46 红线下仅供参考且
   故意不与题面同屏——避免锚定）；
3. 跑 `python3 compute_agreement.py` 看「负责人 × 设计真值」一致率——
   这一步校的是**尺子本身**（夹具/答案键按人读是否成立）；类别 4（代码含
   sink 但特性缺省关）是预期分歧项，单列不计失败；
4. 发现错题（人判与设计真值不合且有据）：**隔离整个关联组**、修订后重跑
   受影响对照（方案 §7.2 门禁），不静默改判。

## 步骤 2｜跨族裁判供给拍板（D47；首次线上调用触发预算起算 D48）

1. 负责人指定跨族旗舰（GPT 族等；**GLM 族与施工 AI 本体禁任**）并提供
   API 通道；预算消耗逐笔登记 `budget/budget_ledger.jsonl`；
2. 按 `judge_prompt_template.md` 盲卷喂 `judge_materials.jsonl`（每批 ≤10
   项、确定性档、served_model 留档）→ 产 `judge_results.jsonl`；
3. `compute_agreement.py --threshold <T>` 出「裁判 × 负责人」一致率——
   阈值 T 由负责人在本轮 pilot 数据上冻结（D46：未过换裁判或加人工）。

## 步骤 3｜收口

- 一致率达标 → E0 校准门关闭，E1 可申请开工（P2 逐门批）；
- 全程留档：judging_sheet（含判定）/ judge_results / agreement 输出
  随交付报告归档 `out/_p1_final/` 增补或 `out/design-revision/`。

## 纪律备忘

- 施工 AI 可做：材料装配、调用编排、计算、留档——**不做判定**（D46 红线；
  对自建夹具判定 = 结构性自证，一致率恒 100% 无信息量）；
- 裁判输入不含类别号/期望表/设计真值/施工 AI 建议（盲卷）；
- 题库项判定同时是答案键抽检（E0「各家族题目抽样人工复核」承载面）。
