# v2.2 裁判轮预登记：门槛冻结 + 裁判身份

日期：2026-11-11（Asia/Shanghai）。状态：**预登记于裁判轮执行之前**——本文件落档时未看到、未生成任何 v2.2 裁判答案。负责人同日拍板：裁判复用 GPT 6.1sol、门槛维持 claim 一致率 ≥23/25（DECISIONS 2026-11-11 行）。

## 裁判身份（冻结）

- 裁判：**GPT 6.1sol（OpenAI 族）**。D46 跨族合规：禁同族仅排除 GLM 族与施工 AI 本体；E1 若设 GPT 族实验臂，与裁判同族须披露偏倚。
- 执行方式：**全新会话**。不得延续 v2 轮裁判会话上下文，不得向裁判提及 v1/v2 旧轮结果、分歧清单或修订历史。
- 裁判只收两样材料：
  1. [公开裁判提示词](<CALIBRATION_REPAIR_JUDGE_PROMPT_20261110.md>) 的 text 代码块；
  2. [v2.2 盲卷](<../experiments/design_revision/calibration_v2_2/judge_materials_v2_2.jsonl>)（25 题、J22-* 盲 ID、族别标识符已中性化）。
- **不得同时提供**：设计答案（truth_map）、owner 辅助复核答案及派生评分表、生成器、溯源、变更记录、冻结清单、任何旧轮（v1/v2/v2.1）裁判或 owner 结果、本项目仓库访问。

## 对比表（冻结引用）

owner 面使用 [owner-assisted 派生评分表](<../out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl>)。其来源为 `human_reviewed_ai_assisted`（2026-11-11 负责人授权接受），**不是独立人工盲判**；该局限随本门结果如实披露，不因裁判轮执行而消除。不使用原空白盲判表（保持空置受保护）。

## 门槛（看到裁判答案前冻结）

1. 裁判须 **25/25 完整作答**：claim 三值枚举零越界；PoC 题（18 题）按四值枚举或如实 null；blind_id 与盲卷一致、输出顺序与输入相同。
2. **主指标：judge × owner claim 一致率 ≥ 23/25**。
3. 有争议的不可达/环境失败项单列，**不从分母删除**；缺项或无交集 = 不可估，不默认通过。
4. PoC 一致率（judge × owner，18 题）为**描述性报告**：逐项分歧照登，不设门、不阻断、不事后补门。
5. 未达标处置（冻结协议）：换裁判重判，或负责人人工复核分歧项；**不重抽题、不改标签凑门槛、不降门**。
6. 这是小样本仪器门：通过 ≠ 泛化精度置信保证，**不自动放行 E1**——E1 供给批准与 manifest 冻结另行拍板。

## 计分（冻结命令）

评分器只计算不裁决；门的判定以本预登记为准，结果人工登记回 DECISIONS：

```bash
python3 -B experiments/design_revision/calibration_v2_2/compute_agreement_v2_2.py \
  --sheet out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl \
  --judge out/design-revision/judge-v22-20261111/judge_results_v2_2.jsonl
```

裁判答案文件原样保存、不改写、不修格式；机械校验（blind_id 合法性/枚举边界/行数）由评分器 fail-closed 完成，不合格即 `invalid_inputs`，不人工放行。

## 其他

- 预算硬门禁已移除（DECISIONS 2026-11-10）；裁判轮费用尽力登记 `experiments/design_revision/budget/budget_ledger.jsonl`。
- 裁判轮执行说明：[交接目录](<../out/design-revision/judge-v22-20261111/README.md>)。
- 本预登记不修改任何冻结材料；v2.2 材料目录与 owner 盲判目录哈希不变。
