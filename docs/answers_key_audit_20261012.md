# 答案键人工复核报告（answers-key-audit，2026-10-12 下午）

> 交接文档 §6.2 的执行产物。范围 = 29 道存疑题（22 可疑 + 7 cve_rollback）逐题判定
> 「快照中漏洞是否存在 / 家族键是否可由代码真值推出」，外加全库对称风险抽查。
> 机器可读判定：`results/_answers_key_audit.json`（重判脚本以此为准源）。

## 0. 结论一句话

29 题中 **18 题键正确、7 题键错误（漏洞不存在）、4 题家族键不可达**（漏洞真但六家族树
无其归宿）。**交接文档的根因结论被部分改写**：7 道 injection「硬破」不是变异杀死漏洞，
而是 **Juliet 官方 `sources-sink-81` exec\* sink 模板本身就是空壳**（与官方镜像逐字对照
实证）；变异引擎真正的破坏只有「文件/类作用域非法插入」这类语法损伤，不影响答案键。

## 1. 判定口径

| 判定 | 含义 | 处置 |
|---|---|---|
| KEY_OK | 漏洞真实存在且家族标注可由代码真值推出 | 保留 |
| BROKEN_KEY | 快照中漏洞不存在，`expected=real` 不成立（decline 才是真） | v0.3 弃题回补；重判口径：decline/suspicious=false 记对 |
| BROKEN_FAMILY | 漏洞真实存在，但其形态（解压炸弹/DoS）在六家族树无归宿，标注家族（authz）无法由代码推出 | v0.3 retire；重判口径：从真题分母剔除（taxonomy 扩容后可再入池） |

证据方法：Juliet 题对照官方 1.3 材料镜像（Hufffman/Adjusted-Juliet-Test-Suite，仅改
构建系统与 `bind()`，用例源码原样）逐字 diff；CVE 题取 OSV fix 引用 → GitHub fix
commit patch → 与 parent_sha 对账（7/7 对上）→ 判 parent 快照是否含漏洞。

## 2. 七道 injection「硬破」（SEED-0003~0009）→ BROKEN_KEY，根因改写

快照 sink 全部形如 `EXECL(COMMAND_INT_PATH, COMMAND_INT_PATH, ARG1, ARG3, NULL)` /
`EXECV(COMMAND_INT_PATH, args)` / `_spawnv(_P_WAIT, COMMAND_INT_PATH, args)`——污点参数
`data` 只出现在签名，从未进入 sink。**与官方镜像原件对照**（0003 s01 execl、0004 s03
execv、0006 s05 spawnv 全文取回）确认：原件就长这样，且 **bad 与 goodG2B 的 sink 逐字
相同**——这是 Juliet 该模板族的已知形态缺陷（sink 模板作者把 `data` 写丢成了
`COMMAND_INT_PATH`），不是本项目变异引擎所为。本次快照与原件的差异仅：注释内标识符
改名（如 `TEST`→`v647a6a`、`label`→`vae4c64`）+ 非法 `if (0)` 插入 + 横幅/金丝雀。

推论：v1/v2 中 cheap 蜂在此 7 题靠 `/* POTENTIAL FLAW */` 注释模式匹配得的分**全部是
假阳性**；strong 蜂拒报**全部是正确行为**——与交接文档 §3.3 的分数污染方向一致，
但「变异把漏洞变没」的机制归因不成立。

## 3. 十五道 authz / mem_safety（SEED-0054~0060, 0089~0096）→ 全部 KEY_OK

逐题读 `action()` 函数体，污点链全部存活：

- authz 7 题：`FOPEN(data,"wb+")` / `outputFile.open((char*)data)` / `OPEN(data,...)`
  / `CreateFileA(vcaca70,...)`（SEED-0056 为参数**一致改名**，签名与 sink 同步，链路完整）；
- mem_safety 8 题：`strcpy(dest[50],data)`、`wcscpy`/`memcpy`/`wcsncpy`/`memmove`/
  `SNPRINTF` 的大小失配全部成立（目标无尺寸保证、源侧 100 元素/strlen+1）。

损伤仅为外观/语法层（文件作用域 `if (0)`、注释改名），答案键无需改动。

## 4. 七道 cve_rollback（SEED-0001/0002/0049/0050/0051/0052/0053）→ 3 OK + 4 家族键坏

| 题号 | 仓库@fix | 漏洞（parent 快照实证） | 家族键 | 判定 |
|---|---|---|---|---|
| 0001 | aiohttp@9a6ada9 | formdata.add_field 对 content_type 无 CRLF 校验 → 头注入 | injection ✓ | KEY_OK（蜂全漏=题难） |
| 0002 | Pillow@8404ea5 | ImageShow WindowsViewer f-string 插值 + `shell=True` → 命令注入 | injection ✓ | KEY_OK |
| 0049 | werkzeug@f407712 | safe_join 多段路径不查逐段 Windows 设备名 → 穿越绕过 | authz ✓ | KEY_OK |
| 0050 | Pillow@3cb854e | FITS `gzip.decompress(fd.read())` 无界解压 → 解压炸弹 | authz ✗ | BROKEN_FAMILY |
| 0051 | Pillow@0a263e6 | BDF/PCF/FontFile 无 `_decompression_bomb_check` → 解压炸弹 | authz ✗ | BROKEN_FAMILY |
| 0052 | Pillow@f39b0ae | GdImageFile._open 无 bomb check → 解压炸弹 | authz ✗ | BROKEN_FAMILY |
| 0053 | Pillow@0399261 | EPS BeginBinary 负 bytecount 未校验 → 负偏移回读 DoS | authz ✗ | BROKEN_FAMILY（置信略低） |

四道 BROKEN_FAMILY 的共性：**解压炸弹/资源耗尽类漏洞在 taxonomy 六家族
（injection/deser/crypto/authz/config_dep/mem_safety{overflow,uaf}）里没有归宿**；
构建器按 FAMILY_SIGNATURES token 计数强制归入 authz（Pillow 插件文件名遍地 `File`/
`Path`/`new ` 命中），蜂从代码真值出发永远推不出 authz。v2 中 SEED-0050 有蜂报
`suspicious=true, guess_family=mem_safety` 被家族失配判错——**这是家族键坏而非蜂错**
的直接证据。规格级缺口（taxonomy 缺 resource_exhaustion 家族）记入 DECISIONS。

## 5. 全库对称风险抽查（超出 29 题范围的加查）

1. **juliet injection fp 面（SEED-0019~0025）与 real 面同属空壳模板**：goodG2B 与 bad
   的 sink 逐字相同（数据同 §2）。fp 键语义碰巧成立（无漏洞 → decline 对），v2 判分
   **不受影响**；但同一空壳代码在题库里顶着两种相反标签不可接受，v0.3 两面同弃——
   **taint 存活校验必须对 bad/good 两面都跑**。
2. **语法损伤普查**：39/39 道 juliet C(.cpp) 变异题的 `if (0) { /* dead */ }` 落在文件
   作用域（非法 C++，不可编译）；OWASP Java 题落在类作用域（SEED-0010 实证切开了
   `@Override` 与方法声明）；python 模块级合法。**不伤答案键**，v0.3 修插入点。
3. **rename 作用域**：按行正则替换命中注释与字符串字面量（`Filename: vac27e2.cpp`、
   `Buffer v2abae5 Overflow`）——外观损伤，v0.3 限制到代码行。
4. **OWASP real 抽查**（0029 crypto 弱哈希 / 0069 config_dep `setSecure(false)` / 0010
   injection ProcessBuilder cmdi）：sink 与标签一致，未见键坏。
5. 其余 fp 面风险低：四类变异均不引入新漏洞（rename 全局一致 / reorder 仅交换同缩进
   独立声明语句 / dead_code 惰性 / restructure 仅注释）。

## 6. 对 v0.3 重建的直接输入（替代交接文档 §6.1 原建议）

交接文档建议的「变异后漏洞存活」校验方向正确但**检查点放错了位置**——空壳材料在
变异之前就坏，事后校验等于用 bug 验 bug。v0.3 应为：

1. **取材面 taint 存活校验**（bad/good 两面）：juliet 用例的污点参数（action 签名中的
   指针参数）必须在函数体内被引用 ≥1 次——CWE78 exec\* 空壳族（7 real + 7 fp）会在此
   被拒；CWE23/121/122 族存活。
2. **dead_code 插入点修正**：c/java 必须落在函数体内（花括号深度 ≥ 方法体），无合法
   落点则跳过该变异（python 模块级合法，维持现状）。
3. **rename 替换范围收缩**：跳过注释行与 `#include`/annotation 行的替换。
4. （规格流程）taxonomy 是否增设 resource_exhaustion 家族 → 负责人拍板，决定
   0050~0053 是 retire 还是改族重入。

## 7. 对分数的影响（重判详见 `results/_v2_corrected_scores.md`）

- 0003~0009：decline 记对、模式匹配记错 → strong 档上调、cheap 档下调（与交接文档
  §3.4 方向一致）；
- 0050~0053：从分母剔除 → 各蜂真题数 −0~2 不等（按卷面分布）；
- 0054~0096 与 0001/0002/0049：维持原判。
