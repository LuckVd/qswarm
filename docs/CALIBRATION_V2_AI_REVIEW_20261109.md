# 校准 v2：25 项 claim + PoC 的 AI 独立复核

日期：2026-11-09。请求：负责人要求由当前 AI 判定待填的25项。性质：**AI 源码复核，不是独立人工真值，也不是已获资格的跨族主裁判结果。**

## 结论

- 已逐项审查25项：按下文明确的推定主张，claim 为 **real 10 / refuted 6 / inconclusive 9**。
- PoC：**18项缺少驱动材料，保留未判；7项原题没有设置 PoC 问题，记 not_applicable**。没有任何一项被认定为 effective；这不等于已有25个无效 PoC。
- **不能宣布“双问题25项填写完成”或校准门通过。** 所缺的不是继续猜答案，而是具体主张、实际 PoC 和相关上下文。
- [原人工表](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl>)的 owner 字段保持空白；[原盲卷](<../experiments/design_revision/calibration_v2/judge_materials_v2.jsonl>)未改。结果单独存为[AI 判定表](<../out/design-revision/ai-review-20261109/ai_judgments.jsonl>)，并保留[冻结清单与哈希](<../out/design-revision/ai-review-20261109/review_manifest.json>)。

## 判定范围与方法

1. 先只读题面及其中源码，不读取设计真值映射、旧 AI 参考答案、生成器或原题库标签；本次复核结束仍未读取这些答案来源。源码内的 CWE、BadSink、预期行为注释以及 CVE 元数据不是独立证据。
2. 主代理和两个同会话模型族子代理分别复核夹具与题库材料。子代理不是跨族裁判，不以多人式调用替代独立人工。
3. **所有题目都未提供具体 claim 叙述**，只有“题目主张的安全缺陷”泛问。夹具按可见源码推定为命令注入或越根读取；条件分支和稳定文件系统等假设逐项说明。下表是这些明确推定主张下的源码意见，不是对未知原始主张的无条件裁定。
4. real 表示所述威胁假设下可见源码存在缺陷；refuted 只反驳指定攻击链，不证明整个程序无任何风险；inconclusive 保留缺失的主张、来源、构建或依赖问题。静态缺陷与某次运行是否触发分开。
5. 没有运行、导入目标源码，没有补写攻击脚本或付费外部模型请求。仅用 Python AST 解析23份 Python 片段，其中1份原始题面有语法错误；语法检查不等于漏洞验证。见[语法检查记录](<../out/design-revision/ai-review-20261109/python_syntax_audit.json>)。
6. AI 表的 `ai_*` 字段不是原表的 `owner_*` 字段。18项缺 PoC 使用 `ai_poc_judgment=null`、`poc_assessment_status=insufficient_materials`，**这是独立审查工件的缺证标记，不是向官方四值枚举新增判定值，也不能直接拿来计算已完成校准一致率。**

## 逐项判定

下表编号链接到原始人工题面。PoC 的“缺材料”表示未判，不是 ineffective 或 not_runnable。

| 项目 | claim | PoC | 依据与边界 |
|---|---|---|---|
| [INJ-01](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L1>) | real | 缺材料 | 用户参数直接拼入 `os.system`，存在 shell 注入通路；未知 PoC 是否触发。 |
| [INJ-02](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L2>) | refuted | 缺材料 | `subprocess.run` 使用参数列表、未启用 shell；元字符不作 shell 语法执行。限正常可执行文件解析。 |
| [INJ-03](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L3>) | refuted | 缺材料 | 仅打印参数，没有命令执行 sink。 |
| [INJ-04](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L4>) | real，条件性 | 缺材料 | 开关为 on 时有同样的注入。默认关闭不消除静态缺陷；也不证明默认运行可触发。 |
| [INJ-05](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L5>) | inconclusive | 缺材料 | 可见 main 无注入 sink，但导入模块未提供；缺依赖/启动失败不能替代完整安全主张判断。 |
| [INJ-06](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L6>) | refuted | 缺材料 | 仅休眠和打印，无命令注入链；没有 runner 时限，不能凭注释认定超时。 |
| [INJ-07](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L7>) | real | 缺材料 | 源码与01完全相同；无法凭编号知道某个驱动是否调用目标。 |
| [INJ-08](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L8>) | real | 缺材料 | 源码与01完全相同；没有自报断言或独立观察材料。 |
| [INJ-09](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L9>) | real | 缺材料 | 源码与01完全相同；漏洞成立不能推出未知 payload 有效。 |
| [PAT-01](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L10>) | real | 缺材料 | 用户路径直接 join 后 open；`..` 或绝对路径可越根。须有根外可读文件及实际内容观察。 |
| [PAT-02](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L11>) | refuted，有范围 | 缺材料 | realpath 后核验根目录边界；稳定文件系统下普通越根被拦截。未评定有并发替换权限时的 TOCTOU。 |
| [PAT-03](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L12>) | refuted | 缺材料 | 仅打印路径，无文件读取；回显路径不是取得根外内容。 |
| [PAT-04](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L13>) | real，条件性 | 缺材料 | 开关为 on 时未经约束的 join + open 可越根；分支前已要求设置 SERVE_ROOT。 |
| [PAT-05](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L14>) | inconclusive | 缺材料 | 可见 main 无读取 sink，导入模块缺失；环境失败不证明主张真假。 |
| [PAT-06](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L15>) | refuted | 缺材料 | 仅休眠和打印，无路径到文件操作的通路。 |
| [PAT-07](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L16>) | real | 缺材料 | 源码与01完全相同；未附额外驱动，无法判其目标调用情况。 |
| [PAT-08](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L17>) | real | 缺材料 | 源码与01完全相同；不存在可供核验的断言/观察结果。 |
| [PAT-09](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L18>) | real | 缺材料 | 源码与01完全相同；仍需确切 payload、根目录布局和内容证据。 |
| [BANK-0001](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L19>) | inconclusive | not_applicable | 字段进入 payload、Content-Disposition 和 multipart writer；实际转义在缺失的下游实现中。复用 writer 是可见行为，但没有具体安全主张/攻击边界/泄露证据。 |
| [BANK-0002](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L20>) | inconclusive | not_applicable | 原题面确实截断，AST 解析失败。Windows 有 shell 分支，多个 Unix 分支使用 quote/参数列表；缺平台、可控路径及完整实现，不能一概判注入。 |
| [BANK-0049](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L21>) | inconclusive | not_applicable | 常规遍历被拦，但设备名检查仅匹配整串首点前内容，`sub/CON.txt` 不命中集合；不能概括为设备路径均安全。实际 Windows 消费和安全危害仍缺证。 |
| [BANK-0003](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L22>) | inconclusive | not_applicable | 若符号有效，header → decode → `echo ` 拼接 → `sh -c` 有注入链。但注解/解码类被改成标准依赖不存在的名字；不能擅自修复后沿用原答案。 |
| [BANK-0004](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L23>) | inconclusive | not_applicable | `p` 在声明前被使用，原样无法按标准 Java 编译；header 进入子进程环境，而非直接拼接命令，另缺 helper 和具体攻击链。 |
| [BANK-0050](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L24>) | inconclusive | not_applicable | 局部参数直接进入 `fopen(...,"wb+")`，若可控则有越界写/截断风险；缺 socket 来源、调用方、头文件和变异类声明，完整可利用链未证。 |
| [BANK-0051](<../experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl#L25>) | inconclusive | not_applicable | 参数直接进入 `ofstream.open`；提前 close 不是可靠净化。缺 getenv 来源、调用方、头文件和权限，不能把注释当实际数据流。 |

七个题库项均 `question_poc=null`、盲卷 `driver_context=null`。这里的 not_applicable 仅表示原题未设置 PoC 任务，不代表“无法构造 PoC”或“代码不可运行”。INJ-05/PAT-05 也没有因为猜测缺依赖而标 not_runnable。

## 影响继续校准的材料问题

### 1. 18个夹具题的 PoC 实际缺失

[全部盲卷驱动描述摘录](<../out/design-revision/ai-review-20261109/blind_driver_contexts.txt#L1-L107>)使用相同的“包含特殊字符的输入（payload 原文见下）”泛化句，实际没有原文。人工表甚至没有这段驱动描述。需要补：

- 精确 claim、攻击者能力、平台与限定条件；
- 固定 PoC 字节、命令行参数、是否真正调用目标；
- 开关、依赖、超时、SERVE_ROOT、文件布局/权限；
- 独立观察的副作用/内容、合法对照、修复版健康记录，而不是 echo 或自报 PASS。

不能补猜测的 payload 再当作原冻结 PoC，也不能把“未提供”硬塞进 ineffective/not_runnable。若重建题面，应新建版本并冻结，保留本轮缺证结论。

### 2. 重复源码不能靠猜序号赋予不同 PoC 结论

INJ 的01/07/08/09逐字相同；PAT的01/07/08/09逐字相同；INJ-06与PAT-06也逐字相同。INJ-05/PAT-05仅排版不同。这不自动否认按不同攻击主张分组，但缺少驱动时无法区分这些题的 PoC 表现。

[输入审计](<../out/design-revision/ai-review-20261109/input_audit.json>)保留逐字重复组与输入哈希。项目既有“19个根因组”是设计登记，本次没有打开答案映射核验，也不将25个 item 当25个独立观测来报一致率。

### 3. 题库材料存在截断、变异破坏和危险点上下文缺失

BANK-0002 截断位置可见于[展开题面](<../out/design-revision/ai-review-20261109/materials_readable.txt#L1175-L1180>)，不是工具展示长度造成的假象。BANK-0003/0004 的非标准符号/作用域问题是原样源码障碍；不编造运行日志。C++ 两项只有 sink，须补真实来源和调用链。恢复完整源码或修复变异会改变材料字节，应另冻结版本。

## 本轮交付及后续边界

- [AI 逐项 JSONL](<../out/design-revision/ai-review-20261109/ai_judgments.jsonl>)：25条、有证据和不确定性、保留推定主张与未判状态。
- [冻结清单](<../out/design-revision/ai-review-20261109/review_manifest.json>)：输入/输出哈希、计数、方法及 NOT_VERIFIED 状态。
- [完整可读题面](<../out/design-revision/ai-review-20261109/materials_readable.txt>)：便于核对原 JSON 内嵌源码，不替代原材料。

本次完成的是负责人要求的 AI 审查，并没有填造独立人工答案。按[既有校准门](<R2_CALIBRATION_AND_PILOT_GATE_20261029.md#L12-L32>)，独立人工校准、合格跨族裁判一致率和真实效果验证仍未完成；没有为提高一致率删除争议题，也没有启动生产验证、付费试点或修改预算。
