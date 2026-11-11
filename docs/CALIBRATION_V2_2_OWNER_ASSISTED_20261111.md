# v2.2 负责人接受的 AI 辅助复核

日期：2026-11-11（Asia/Shanghai）。本轮完成的是负责人面登记，不是独立人工盲判，也不是独立裁判校准通过。

## 授权与来源

负责人先获知“AI 初判＋负责人复核确认不能登记为负责人独立盲判”，随后明确回复：

> 可以 按照AI辅助复核 并且可以认为是人复核完成的

因此按负责人明确授权，将本轮负责人面记为已接受的 `human_reviewed_ai_assisted`。接受发生于本轮逐题结果生成之前，不能推定负责人已在生成后逐项阅读这些答案；记录明确保留 `post_result_item_by_item_human_inspection_observed=false`。这是对 AI 辅助结果的接受授权，不是现场观察到一次独立人类判卷。

当前施工 AI 完成25题静态复核，同流程辅助代理复核七道静态题。两者均不作为独立裁判；未执行目标或PoC、未联网、未发付费请求。逐题判定先写入，再与设计标签比对，没有按设计标签回调答案。

## 结果

| 维度 | 结果 |
|---|---|
| claim 覆盖 | 25/25 |
| claim 分布 | real 15；refuted 5；inconclusive 5 |
| PoC 覆盖 | 18/18；另7题未设问，保持null |
| PoC 分布 | effective 2；ineffective 12；not_applicable 2；not_runnable 2 |
| 与设计草案描述性一致 | claim 25/25；PoC 18/18；分歧0 |
| 独立裁判 | 本轮没有执行，v2.2仍缺 |
| 校准放行 | **未通过／未放行** |

与设计草案一致不是独立可靠性证据：当前复核属于施工 AI 谱系，且此前已接触项目设计背景；19个分组也不是已证实的19个独立漏洞根因。5项 inconclusive 均保留具体缺失上下文，未为追求覆盖率猜测事实。

- [逐题判定与证据](<../out/design-revision/owner-assisted-v22-20261111/owner_answers_v2_2.ai_assisted.jsonl>)
- [负责人授权与来源登记](<../out/design-revision/owner-assisted-v22-20261111/owner_confirmation.json>)
- [独立保存的负责人评分表](<../out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl>)
- [原评分器输出](<../out/design-revision/owner-assisted-v22-20261111/agreement_raw.json>)
- [设计草案分歧清单](<../out/design-revision/owner-assisted-v22-20261111/design_disagreements.json>)
- [原始工件保护清单](<../out/design-revision/owner-assisted-v22-20261111/protected_input_inventory.json>)
- [登记脚本](<../out/design-revision/owner-assisted-v22-20261111/register_assisted_review.py>)

## 冻结与评分边界

原v2.2材料目录及负责人原盲判目录的文件哈希在登记前后保持一致。没有填写、覆盖原空白盲判表，没有调用会写入“independent blind judgment”来源的旧登记脚本，没有修改冻结设计标签。新标签单独保存在上述派生评分表。

原评分器硬编码 `pending_owner_confirmation`，因此保留的原始评分输出仍会显示该状态。负责人本轮明确接受的管理事实另记在授权记录，二者不混淆；没有为更新状态去改冻结评分器，更没有将其输出改成“校准通过”。

以后评分必须显式选择本轮派生表，避免默认读到原空表：

```bash
python3 -B experiments/design_revision/calibration_v2_2/compute_agreement_v2_2.py \
  --sheet out/design-revision/owner-assisted-v22-20261111/judging_sheet_v2_2.owner_assisted.jsonl
```

## 验证

本轮运行三组校准材料／评分回归：**152 passed，1 xfailed，退出码0**。预期失败仍为冻结清理器对任意多行字符串行尾空白的已知缺陷，不在本轮改写冻结生成器。本轮只跑校准相关回归，未重跑全项目测试，也未重新执行18题目标夹具。

## 下一步

负责人面已按上述授权接受。剩余是：预登记新版校准门槛、确定符合隔离与跨族要求的新独立裁判并另行批准裁判轮；仅向裁判提供公开提示词与盲卷，不发本轮答案或设计标签。不得把施工 AI 本轮结果写入独立裁判结果文件。E1/E2/E3与生产效果结论仍未放行。

预算硬门禁已按2026-11-10最新决策移除，费用尽力登记；独立性、未批准不调用和未达标不重抽题等要求仍维持。
