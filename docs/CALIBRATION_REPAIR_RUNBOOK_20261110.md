# 校准修卷交接：v2 → v2.1 → 当前 v2.2

状态：修卷工件已生成；**不能据此宣布校准通过**。工作区已另行推进到v2.2及负责人盲判准备流程，本交接不覆盖该轮冻结材料、不重写其标签或答案。

**2026-11-11负责人面更新：** 负责人明确改为接受AI辅助复核并视作本轮人工复核完成；已单独登记25项claim/18项PoC，来源为 `human_reviewed_ai_assisted`，不是独立人工盲判。原卷与空表保持冻结，后续评分须选用派生表，详见[本轮登记与评分命令](<CALIBRATION_V2_2_OWNER_ASSISTED_20261111.md>)。下文盲判流程为历史安排；独立裁判和新版校准门槛仍待完成。

## 核验结论

用户的核心判断成立，旧卷不能靠复核少数分歧凑到23/25来证明系统可靠：

- 18道构造题中11道保留解释性注释；另2道题库题有模板提示，合计13/25。这是操作/阻断/分类提示统计，不等于每句都直接给出最终标签。
- 25/25没有具体主张；18/18构造题没有实际payload或驱动，却称“payload原文见下”。
- 去掉blind_id后的完整题面只有18种。三组完全重复为INJ-01/07/08/09、PAT-01/07/08/09、INJ-06/PAT-06，涉及10条、超额7条。
- 两道05只有导入和打印；两道06只有等待和打印。不能把它们说成旧源码已有漏洞实现、只是执行未到达。
- 7道题库题不是全被截断：声明文件9/9齐全，只有ImageShow展示本截断；其余题另有依赖、调用链或构建缺口。

精确证据见[旧卷审计](<../experiments/design_revision/calibration_v2_1/audit_v2.json>)。旧轮结果仅是这些旧材料上的描述性一致率，不作为可靠性资格证据；历史确认与原始工件不倒改。

## 修复及交付边界

v2.1提供25个明确claim、18个完整PoC问题、7个显式claim_only问题：

- 完整驱动源码、精确参数构造、环境、文件布局、平台、时间限制与观察条件；明确区分静态参照配置和本次PoC配置。
- 01/07/08/09保留同目标、不同真实驱动，恢复攻击、自报成功、仅准备环境、良性输入的区分。整题不重复，但不能作为独立漏洞根因重复计数。
- 05在导入后、06在等待后加入真实目标操作。这是有记录的新题语义，不是等价清理旧源码，不能沿用旧标签。
- 从本地原快照恢复完整ImageShow；不默修Java异常符号/作用域、不假定C++未展示来源链。缺执行闭包的7题只问静态主张，PoC为null且不入18题运行分母，**不是25题全可执行的PoC套件**。
- Python/Java/C++展示本清理注释并将身份注释、CVE/commit移到私有溯源；原本地源码和版权/许可头完整保留。另发现冻结清理器的通用边界缺陷：逐行 `rstrip()` 可改变任意多行字符串内部的行尾空白，已加严格预期失败回归；当前5份Python题库输入经AST与字符串token逐一比对均不受影响。为保护已冻结且进入后续盲判的卷子不回改生成器；未来推广该工具必须修复并另起版本，不能据本题集检查声称任意源码语义不变。
- 冻结版登记19组并提供逐题、组等权和全组正确率；**19是设计分组，不是19个已证明独立根因**。全部构造题来自两类模板，修复/包装的相关性仍应披露；更保守谱系汇总可另做附加统计，但不在负责人盲判过程中改写冻结映射。
- 工作区现有v2.2继续中性化4个bank展示本的族别标识符，并换盲ID/顺序；其声明保持构造题字节和设计标签不变，见[v2.2清单](<../experiments/design_revision/calibration_v2_2/materials_v2_2_manifest.json>)。当前使用版本以该清单和既有负责人流程为准。

## 公开与私有材料

只发给新的独立裁判：

1. [裁判提示词](<CALIBRATION_REPAIR_JUDGE_PROMPT_20261110.md>)。
2. **当前**[v2.2盲卷](<../experiments/design_revision/calibration_v2_2/judge_materials_v2_2.jsonl>)。若专门复核冻结v2.1，改用[v2.1盲卷](<../experiments/design_revision/calibration_v2_1/judge_materials_v2_1.jsonl>)，不得混用两轮ID。

不要同时发整个目录、生成器、设计答案、人工表、溯源、测试或旧结果。施工AI及其同会话子代理不冒充独立裁判。

负责人私有审核：

- [v2.1设计答案草案](<../experiments/design_revision/calibration_v2_1/truth_map_v2_1.json>)、[当前v2.2设计答案草案](<../experiments/design_revision/calibration_v2_2/truth_map_v2_2.json>)：AI编制的待确认标签，不是未经确认即可冒充的独立人工真值。
- [v2.1空白人工表](<../experiments/design_revision/calibration_v2_1/judging_sheet_v2_1.jsonl>)、[当前v2.2空白人工表](<../experiments/design_revision/calibration_v2_2/judging_sheet_v2_2.jsonl>)：原始空表不回写，已填表另存并明确来源。
- [逐题改动](<../experiments/design_revision/calibration_v2_1/changes_v2_1.json>)与[来源/许可记录](<../experiments/design_revision/calibration_v2_1/provenance_v2_1.json>)。
- [v2冻结清单](<../experiments/design_revision/calibration_v2_1/v2_frozen_inventory.json>)、[v2.1冻结清单](<../experiments/design_revision/calibration_v2_2/v21_frozen_inventory.json>)；冻结文件不得因注释清理器改进或评分需要回改。
- [v2.1运行记录](<../experiments/design_revision/calibration_v2_1/runtime_checks_v2_1.json>)：可信本地夹具检查，不是任意代码sandbox、对抗性生产oracle或人工确认。

## 验证与重判

从项目根执行，不联网、不安装依赖、不调用付费模型：

```bash
# 仅读取比对发布工件；运行检查仅执行已审查的固定18个临时夹具
python3 -B experiments/design_revision/calibration_v2_1/gen_materials_v2_1.py --check --verify-runtime
python3 -B -m pytest tests/test_calibration_v2_1_materials.py tests/test_calibration_v2_1_scoring.py
```

不要重跑旧v2生成器。以后题面或清理逻辑改变必须另起版本，不能破坏已冻结v2.1/v2.2；`-B`避免写入被冻结的Python缓存。

1. 按当前负责人盲判流程重新审核新版题意和标签；本次修卷请求不是对新设计答案的预先认可。若看过AI草案后认可，披露AI辅助来源，不声称先独立盲判。
2. 另由未参与出题、未见答案的独立裁判收到上述公开材料，输出25行，按所用版本保存新结果；不迁移旧owner/judge答案，也不编造尚未执行的裁判轮。
3. 当前v2.2评分使用对应工具与人工表；仅复核v2.1时才使用v2.1工具。缺判标incomplete，禁止缩小分母后宣布通过。评分工具报告描述性一致率，不自动将23/25升级为校准资格。

```bash
python3 -B experiments/design_revision/calibration_v2_2/compute_agreement_v2_2.py --help
# 若专门复核旧冻结v2.1：
python3 -B experiments/design_revision/calibration_v2_1/compute_agreement_v2_1.py --help
```

本次没有读取或修改进行中的负责人盲判答案，没有提交新裁判结果或放行E0/E1。新版仍属开发校准材料，不得改名当未见保留集。
