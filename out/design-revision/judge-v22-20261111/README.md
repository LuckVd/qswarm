# v2.2 独立裁判轮交接（GPT 6.1sol）

日期：2026-11-11。预登记：[CALIBRATION_V2_2_JUDGE_PREREG_20261111.md](../../../docs/CALIBRATION_V2_2_JUDGE_PREREG_20261111.md)。本目录只放裁判轮产物；冻结材料不复制、不改动。

## 结果（2026-11-11 收口）

裁判轮已由负责人执行（全新无工具会话），25 行答案原样落盘并核验逐字节一致。
机械校验全过；计分（预登记冻结命令）：**judge×owner claim = 25/25 = 1.0 ≥ 23/25 门 → 通过**，
分歧 0、19 组全对；PoC judge×owner = 18/18（描述性）；judge×truth = 25/25（描述性）。
门判定登记：DECISIONS 2026-11-11「v2.2 裁判轮执行与门判定」行。

- 答案：`judge_results_v2_2.jsonl`（原样，未改写）
- 计分全量输出：`agreement_full_20261111.json`

## 执行步骤（负责人执行）

1. **新开**一个 GPT 6.1sol 会话（不复用 v2 轮裁判会话，不提旧轮任何结果）。
2. 发送 `docs/CALIBRATION_REPAIR_JUDGE_PROMPT_20261110.md` 中的 **text 代码块**（仅该代码块）。
3. 发送 `experiments/design_revision/calibration_v2_2/judge_materials_v2_2.jsonl` 的文件内容（或文件本体）。
4. 收到裁判输出的 25 行 JSONL 代码块后，**原样**保存为本目录下 `judge_results_v2_2.jsonl`——不改 ID、不改枚举值、不修格式、不删裁判的 uncertainty 字段。
5. 计分（fail-closed；机械校验不合格即拒）：

   ```bash
   python3 -B experiments/design_revision/calibration_v2_2/compute_agreement_v2_2.py \
     --sheet out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl \
     --judge out/design-revision/judge-v22-20261111/judge_results_v2_2.jsonl
   ```

6. 费用尽力登记：向 `experiments/design_revision/budget/budget_ledger.jsonl` 追加一行 `record_type=call`。
7. 结果（一致率、门判定、分歧清单）登记回 `docs/spec/DECISIONS.md` 新行。

## 红线（违反即该轮作废）

- 裁判会话**不得**收到：设计答案（truth_map）、owner 辅助复核答案、派生评分表、生成器、溯源、变更记录、冻结清单、v1/v2/v2.1 旧轮结果、本仓库访问。
- 施工 AI（GLM 族）不任裁判、不代收代判。
- 未达标不重抽题；处置按预登记冻结协议（换裁判或负责人复核分歧项）。

## 判定口径提示（常见分歧来源，供事后复核对照，不发给裁判）

- 缺依赖变体（代码有 sink 但环境缺依赖）：claim 按题面明确主张与范围判，不因环境失败默改 refuted/real。
- PoC 与 claim 互不为前提：静态主张与驱动判定按各自配置分别比。
