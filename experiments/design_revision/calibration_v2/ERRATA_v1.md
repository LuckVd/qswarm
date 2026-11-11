# E0 校准材料 v1 勘误与 v2 重建说明（R1-T6）

日期：2026-10-28 · 授权：HANDOVER_GLM_R1_HARDENING_20261028 §10
· v1 材料冻结不动（`../calibration/`），本目录为**版本化新材料**（旧→新映射
内嵌 `truth_map_v2.json.items[].old_item_id`）。

## v1 勘误（生成器自检实证，非推断）

1. **题面泄题（结构性）**：18/18 夹具项的 `target_source` 模块 docstring 直接
   标注答案（「夹具目标①：命令注入**漏洞版**——shell 拼接 sink」等；词频：
   「夹具目标」×18、「漏洞版」×8、「修复版」×2、「安全版」×2、「预期判读」×2）。
   更换 blind_id 不能消除源码泄漏（HANDOVER §10.2-2 所指）。
2. **类别号/路径标签外泄**：v1 sheet 携带 `family` 与夹具路径标签（
   `injection/vuln/target.py` 形态直通九类期望表）。
3. **双问题混一**：claim 真假与 PoC 有效性未分立——「代码有漏洞」被当作
   「该 PoC 已证明漏洞」的隐含前提（§10.2-3）。
4. **根因重复计数**：第 1/7/8/9 类共用同一 vuln 源码，v1 一致率按 4 个独立
   item 计（§10.2-4）。
5. **真值映射多源**：设计真值内嵌于 `compute_agreement.py`（FIXTURE_TRUTH
   常量）与 manifest 两处，TRA/PAT 家族标签不一致无单一裁决（§10.2-5）。
6. **边界输入脆弱**：零判题/空 judge 文件/零交集场景的行为未定义
   （`compute_agreement.py` 直接对空 sheet 迭代，一致率分母语义不显式）。

## v2 修复面（本目录）

| 勘误 | v2 落点 |
|---|---|
| 泄题 | `gen_materials_v2.py` 剥 docstring + 泄题注释；内嵌 `_assert_no_leak` 硬门（题面含泄题词即拒绝出厂）；family/路径标签不下发盲卷 |
| 双问题 | `judging_sheet_v2.jsonl` 拆 `question_claim`/`question_poc` + 双判定字段 |
| 根因分组 | `truth_map_v2.json.root_cause_groups`（1/7/8/9 → `rcg-fixture-vuln-<族>` 同组；组内真值一致性生成期断言）；`compute_agreement_v2.py` 按组聚合 + 逐 item 双面 |
| 单一来源 | `truth_map_v2.json`（item ↔ blind ↔ 真值 ↔ 组 ↔ old_id ↔ item_ref）；`validate_mapping` 三方一致性硬门 |
| 边界健壮 | `compute_agreement([], {})` → `status=not_estimable/zero_owner_judgments`；空 judge/零交集/零分母全部分支显式 |
| 争议项 | `CONTENTIOUS` 全量单列（分层 + 逐 item），不剔除不静默 |

## 不变面

- 25 项构成（18 夹具 + 7 题库分层）与 v1 冻结集一致（材料内容级去泄题，
  覆盖面不变）；
- v1 工件零触碰；人工判题/跨族裁判选型/调用/阈值/经费仍待负责人另批
  （D46 红线维持：GLM 族与施工 AI 本体不任裁判；本目录只交付机械验收）。
