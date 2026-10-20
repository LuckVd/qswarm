# 任务：审计 qswarm 种子题库 authz 家族的答案键完整性

你是一名 C/C++ 漏洞分析专家。仓库在 `/opt/projects/qswarm`（只读排查，**不要修改任何文件**）。
我们怀疑题库 authz 家族的部分"planted 漏洞题"答案键有问题。请逐题人工排查并给出裁决。

## 背景

- 本项目（qswarm）用种子题库给 LLM 审计蜂做入职考试。题库 v0.3（bank_version=2，`seeds/bank.yaml`）
  素材主要来自 Juliet 测试集的**变异件**（`scripts/build_seeds/build.py` 对 Juliet 模板做
  rename / reorder / dead_code / restructure 四类变异后截断为单文件快照）。
- **历史缺陷（M2，2026-10-12）**：v0.2 题库曾实锤「变异杀死漏洞但标签不变」——快照里漏洞已不存在，
  item 标签仍是 `planted: true / expected: real`。v0.3 加了防线 `taint_param_live()`
  （`scripts/build_seeds/build.py:348-404`）：要求签名中的指针/对象参数在函数体内被引用 ≥1 次。
- **本次疑点**：三只同底层模型（glm-5.3-flash）的评审蜂在 authz 家族全部考砸（正确率
  0.125–0.375），其余四个家族全部 0.875–1.0。三蜂在共享题面上对同样一批 real 题
  **高置信拒报**（fp_suspicion 0.75–0.9），这是答案键缺陷的典型签名，但也可能是模型真实短板。

## 判分语义（为什么"拒报"会判错）

reviewer 角色考卷：给蜂送审一个**预置 finding**（声称某快照存在某家族漏洞），蜂输出
`direction: +1（背书，finding 立得住）/ -1（否决，finding 不成立）/ 0（证据不足）`。
判分规则：`expected=real` 的题，判对 = direction=+1；`expected=fp` 判对 = direction=-1；
0 双侧都判错。所以：**如果某题漏洞实际已被变异杀死、标签仍是 real，那么三蜂的拒报其实是
正确行为，是答案键错了**。

## 考试证据（三蜂在共享题面上的原始判定）

嫌疑题（多蜂一致拒报、被判错）：

| 题号 | expected | 难度 | mid 蜂 | air 蜂 | 4.6 蜂 | 被判 |
|---|---|---|---|---|---|---|
| SEED-0050 | real | hard | -1（fp_susp 0.9） | +1 | -1（0.85） | 错 |
| SEED-0051 | real | easy | -1（0.8） | （未抽中） | -1（0.8） | 错 |
| SEED-0057 | real | easy | -1（0.85） | +1 | -1（0.75） | 错 |
| SEED-0058 | real | medium | -1（0.75） | -1（0.8） | （未抽中） | 错 |
| SEED-0059 | real | hard | -1（0.9） | +1 | -1（0.85） | 错 |
| SEED-0060 | real | easy | -1（0.85） | （未抽中） | -1（0.9） | 错 |

对照题（多数背书、被判对）：

| 题号 | expected | 难度 | mid 蜂 | air 蜂 | 4.6 蜂 | 被判 |
|---|---|---|---|---|---|---|
| SEED-0053 | real | hard | +1（fp_susp 0.25） | -1（0.85） | （未抽中） | 对 |
| SEED-0055 | real | medium | +1（0.3） | -1（0.85） | +1（0.35） | 对 |

## authz 家族题单（12 题，全部 expected=real，本家族无 fp 题）

SEED-0049(medium) / 0050(hard) / 0051(easy) / 0052(medium) / 0053(hard) / 0054(easy) /
0055(medium) / 0056(hard) / 0057(easy) / 0058(medium) / 0059(hard) / 0060(easy)。
每题文件：`seeds/items/SEED-00XX/item.json`（元数据：taxonomy_path/planted/expected/
canary/mutation.kinds）+ `seeds/items/SEED-00XX/snapshot/source.cpp`（被试快照本体）。

## 排查方法（对每题执行）

1. **通读快照** `snapshot/source.cpp`，从模板注释识别 CWE 类型、BadSource/GoodSource、sink。
2. **污点链活性**（核心）：攻击者可控数据（source）是否**在本文件内**实际流向危险 sink？
   注意 Juliet 的 flow variant（如 81 = 数据经参数/虚方法传递）可能把 source 留在另一个
   编译单元——此时判断标准是：sink 侧接收的参数是否仍无验证地进入危险函数。若快照内
   污点已被切断（如 source 被替换成固定值、参数未使用、sink 只吃常量），即为 v0.2 型
   「漏洞已死标签仍在」。
3. **变异损伤评估**：找变异痕迹——canary 注释（`qsw-canary-SEED-00XX-xxxx`）附近、
   `if (0) { /* dead */ }` 死代码、被改名的符号（如 `va1590e`、`v90a26b` 这类哈希样
   标识符，可能未定义）、语句重排。判断：变异是否 (a) 杀死了漏洞本体；(b) 产生会让
   谨慎评审者合理怀疑整个快照真实性的噪声（未定义符号 = 无法编译）。
4. **防线漏检分析**：对照 `scripts/build_seeds/build.py:348-404` 的 `taint_param_live()`——
   该防线只检查「签名参数在函数体内被引用 ≥1 次」，**引用≠污点仍然流向 sink**（比如参数
   被引用只是赋值给无关变量、或引用发生在不可达分支）。逐题说明该防线为何放行本题。
5. **必要时对照 Juliet 上游语义**：Juliet 模板文件名含 CWE 编号与 flow variant（如
   `CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad.cpp`），文件名本身
   声称的是"bad 流（有漏洞）"。判断「模板声称有洞」与「快照实际有洞」是否一致。

## 输出要求

对每题给一行裁决 + 证据（引用快照行号）：

```
SEED-0050: KEY-OK / KEY-BROKEN / NOISY —— 证据：…（污点链 source→…→sink 是否存活；
变异做了什么；防线为何放行；三蜂拒报是否合理）
```

- **KEY-OK**：漏洞链完整存活，答案键正确（蜂拒报是模型能力问题）；
- **KEY-BROKEN**：漏洞已死/不存在，答案键错误（蜂拒报正确，需修库重考）；
- **NOISY**：漏洞链存活但变异噪声（未定义符号/死代码）足以让谨慎评审者合理拒报——
  灰色地带，注明你倾向哪边。

最后给总结论：
1. 嫌疑 6 题的裁决分布；2. v0.3 防线的具体漏洞（为什么放行了这些题）；3. 若 KEY-BROKEN
或 NOISY 占多数，给出题库修复建议（重生成/换源/人工修键）；4. 对照题 0053/0055 的裁决
（检验你的标准与考试结果的自洽性）。
