# 校准 v2：负责人复核确认与人工真值登记

日期：2026-11-09。负责人在本会话明确表示：

> 我审查过了  没问题 可以当作人工真值

据此，接受[前一轮25项 AI 复核](<CALIBRATION_V2_AI_REVIEW_20261109.md>)作为**经负责人复核确认的本轮人工真值**，不再将这批 claim 标记为“等待负责人确认”。来源登记为 `human_reviewed_ai_assisted`：AI 初判 → 人工审查认可，不追写成“先独立盲判、后看 AI”。

## 已登记

- **25项 claim** 已写入[人工判定表](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl>)的 `owner_claim_judgment`：real 10 / refuted 6 / inconclusive 9。
- 每项 `owner_note` 保留负责人确认、具体主张范围、证据与不确定性；9项 inconclusive 作为有效的“无法判定”标签保留，不改为确定结论。
- **PoC**：18项继续为 null，表示人工认可“材料缺失、尚无有效性标签”；7项为 not_applicable，表示原题未设 PoC 问题。确认意见没有创造新的驱动或执行证据，不将缺材料改成 ineffective/not_runnable。
- 所有题面、代码、题号、顺序、盲卷及设计真值保持原样；只有人工表原先为空的三个 `owner_*` 字段发生变化。

## 封存及来源

- [人工确认记录](<../out/design-revision/human-confirmed-20261109/owner_confirmation.json>)：负责人原话、登记时间、来源类型、前后哈希和状态。
- [人工标签摘要](<../out/design-revision/human-confirmed-20261109/owner_judgments.jsonl>)：25条标签、主张范围及 PoC 缺证状态。
- [确认后的完整人工表快照](<../out/design-revision/human-confirmed-20261109/judging_sheet_v2.owner_confirmed.jsonl>)。
- [确认前空白人工表快照](<../out/design-revision/human-confirmed-20261109/judging_sheet_v2.before_owner_confirmation.jsonl>)。
- [原 AI 判定](<../out/design-revision/ai-review-20261109/ai_judgments.jsonl>)及[原 AI 冻结清单](<../out/design-revision/ai-review-20261109/review_manifest.json>)保持不变，记录的是人工确认之前的历史阶段，不用当前人工状态倒改历史。

## 当前完成度

| 事项 | 状态 |
|---|---|
| 负责人认可本轮结论作为人工真值 | 已完成 |
| 25项 claim 人工标签登记 | 已完成，范围和不确定性随标签保留 |
| 18项实际 PoC 有效性标签 | 待补驱动与环境材料，不是待再次确认同一份 AI 意见 |
| 另7项 PoC 不适用标签 | 已登记，原题未设问 |
| 独立跨族裁判与人工真值一致率 | 未执行 |
| 整体双问题校准门 | NOT_VERIFIED，未自动放行 |

后续可将这份已确认的人工表作为裁判比较基准，但需披露其 AI 辅助来源；不得据此声称标签与当前模型完全独立。没有使用同一份 AI 结果与人工认可后的自身副本计算“独立裁判一致率”，也没有启动付费模型、目标执行或生产写入。
