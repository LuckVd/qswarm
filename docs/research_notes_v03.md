# v0.3 升版调研笔记（工作文件，非正式文档）

> 用途：汇总四路调研 + 自行找茬结果，作为 v0.3 升版的素材库。
> 状态：随子代理返回持续追加。

---

## A. 竞品核查（调研1，已返回）

### A.1 原表逐项判定
| 项目 | 判定 | 修正点 |
|---|---|---|
| MAVUL (arXiv 2510.00317) | 真实 | 与文档描述一致，TPS-ISA '25 属实 |
| VulAgent (arXiv 2509.11523) | 真实 | ACL 2026 Findings 属实（2026.findings-acl.928） |
| VulnAgent-X (arXiv 2603.13384) | 真实但有出入 | **后续版本已改名 VulnAgent-R2**（"Evidence-Calibrated Multi-Agent Auditing"），引用须注明 |
| AutoReview (FSE 2025) | 真实 | DOI 10.1145/3696630.3728618 |
| Co-RedTeam (ICML 2026) | 真实 | Google Research 官方页 + ICML slides 可取得（文档原注"未取得"已可解除） |
| spotlight | 真实但描述偏差 | 官方自述即 "multi-agent swarm"（扫→利用→修→验→**attest** 五段），非"固定 pipeline" |

### A.2 遗漏竞品（2025–2026）
- **OpenAI Aardvark**（2025.10）：最大牌同类，agentic 安全研究员（发现+验证+修复建议）
- **MulVul**（ACL 2026 Long）：检索增强多智能体 + 跨模型 prompt 进化
- **VulTrial**（arXiv 2505.10961）：法庭对抗式（prosecutor/defense/judge）
- **MultiVer**（arXiv 2602.17875）：Zero-Shot 多智能体，SecurityEval 91.7%
- **Strategic Heterogeneous Multi-Agent**（arXiv 2604.21282）：**明确以成本效益为目标**——"会算账"子空位被占
- **IRIS**（ICLR 2025）：LLM+CodeQL 神经符号混合代表作
- **XBOW**（商业，B 轮）：自主渗透 agent，HackerOne 登顶
- **Google Big Sleep / Project Naptime**：已发现 SQLite 0day（CVE-2025-6965）
- VulnBot（渗透多智能体）、Agent4Vul（智能合约）、GPTLens（早期 auditor/critic）、Microsoft Project Ire（恶意软件，相邻领域）
- "MultiAgent4Vul" 不存在（疑为 Agent4Vul 误记）；"AuditLLM" 是审计小模型家族非多智能体

### A.3 空位判定：部分成立 → 表述须窄化
- 垂直领域内"跨任务组织记忆 + 口碑路由 + 专长孵化"三件套确实无直接对应物 → 空位成立
- 但：① 通用 agent 社区已有萌芽（codomyrmex 按后果历史挣角色、agentex 涌现式专长、共识验证记忆等）→ 定位应写"首个迁移到漏洞检测领域"而非"全新概念"；② "会算账"已被 2604.21282 占据；③ spotlight 已自称 swarm → "组织形态可变"需精确区分
- **建议改写**："在漏洞检测垂直领域内，首个将跨任务组织记忆、信誉路由与专长孵化三者系统集成的检测蜂群"

---

## B. 评测基准（调研2，同日网关恢复后已核验）

### B.1 核验结果（原"待复核"项全部落定）
- **PrimeVul**（arXiv 2403.18624，"How Far Are We?"）：✅ 证实。**C/C++**，~7k 漏洞 + ~229k 良性函数，140+ CWE；人工级标注（号称比自动标注准 3 倍）+ 严格去重 + **时间分割** + **VD-S 指标** + **pairwise 评估**。**核弹级发现：SOTA 7B 模型 F1 从 BigVul 68.26% → PrimeVul 3.09%；GPT-4 级严口径近随机**。来源：https://www.emergentmind.com/papers/2403.18624 ｜ https://github.com/DLVulDet/PrimeVul ｜ https://hf.co/datasets/starsofchance/PrimeVul
- **PatchEval**（ByteDance，arXiv 2511.11019，Apache 2.0）：✅ **证实存在（修正此前"移除候选"结论）**。1000 真实 CVE（2015–2025），**Go/JS/Python 三语均分**，65 CWE；其中 **230 个 CVE 带 Docker 运行时验证**（镜像 + PoC 校验入口 + 单元测试）。定位是"补丁评估"基准，但其 Docker 验证 CVE = 种子题库（真漏洞 + PoC 可运行）与 S2 沙箱 harness 的现成素材。来源：https://huggingface.co/datasets/ByteDance/PatchEval ｜ https://deepwiki.com/bytedance/PatchEval/2-dataset
- **SEC-bench / SEC-bench Pro**（sec-bench.github.io）：✅ 证实。**agentic 安全工程基准**：PoC 生成任务（poc-repo/poc-desc/poc-san 三模式）；Pro = V8/Firefox/Linux 344 实例、自进化题库、Docker harness；榜首 GPT-5.5(xhigh) **58.4%**、Opus 4.6 30.8%、GLM-5 3.8%——agentic 漏洞猎杀现实天花板。来源：http://sec-bench.github.io/
- **VulEval**（TSE 2026）：✅ 证实。4196 CVE / 232k 函数 / 4699 C/C++ 仓；三任务（函数级/依赖预测/仓库级）；**仓库级上下文 +7.43% precision / +4.91% F1；ChatGPT 供上下文 +26%**；prompt 类方法时间分割下稳定。来源：https://lacuna.tiptreesystems.com/work/vuleval-towards-repository-level-evaluation-of-software-vulnerability-detection/wrk_c2573152306a64b3901020d759c86586
- **SeCodePLT**（arXiv 2410.11096）：✅ 证实。5.9k 样本、44 CWE、Python/C/C++/Java；**种子验证 + 定向变异扩展**构建法（与题库思路同构）；C/C++ 检测普遍难（Claude-3.7 0.21 F1 vs Python 0.50）。来源：https://www.alphaxiv.org/abs/2410.11096
- **SVEN / Big-Vul / DiverseVul / Devign / ReVeal / Juliet / OWASP Benchmark / CVEfixes / CrossVul**：维持原判（Big-Vul/DiverseVul 标签噪声是 PrimeVul 批判对象；Juliet/OWASP 作埋雷原料；CVEfixes 作回滚构造原料）
- **SandboxEscapeBench**（Oxford + AISI，2026-03）：✅ 证实。容器逃逸基准（18 场景 CTF、嵌套沙箱）；前沿模型可靠利用配置缺陷（特权/可写挂载/docker socket），内核级与多步链难；**随 token 预算增加成功率上升** → S2 沙箱红线来源。来源：https://cybernoz.com/breaking-out-can-ai-agents-escape-their-sandboxes
- "SecBench" 同名消歧：至少 3 个（agentic SEC-bench / LLM 红队 SecBench / SE 基础模型 SecBench）——引用须写全称

### B.2 选型结论（已写入 v0.3 §8/§14）
- 主尺子 = PrimeVul pairwise + VD-S 协议母本（C/C++）+ 自建多语言 pairwise（PatchEval 1000 CVE 语料 + CVEfixes 回滚）
- agentic 参照 = SEC-bench Pro（现实锚点：最好模型 58.4%）
- 种子题库三源 = post-cutoff CVE 回滚 + **PatchEval 230 Docker 验证 CVE** + Juliet/OWASP 变异
- 校准指标 = Brier / ECE / AUC
- 战略注脚：PrimeVul 3.09% 与 VulEval +26% 共同支撑"仓库上下文 agentic + 证据链 + S2 复现"的架构赌注

## C. 感知层（调研3，同日网关恢复后已核验）

### C.1 核验结果
- **semgrep OSS/CE taint 边界**：✅ 证实且**比初稿更窄**——官方文档：CE 引擎"limited to single-function analysis"（**单函数内**，intraprocedural），跨函数/跨文件为 Semgrep Code/Pro 付费专属。来源：https://semgrep.dev/docs/supported-languages/ ｜ https://semgrep.dev/docs/semgrep-ce-languages
- **语言覆盖**：✅ GA ~17 种（C#/Go/Java/JS/Kotlin/Python/TS/C-C++/JSX/Ruby/Scala/Swift/Rust/PHP/Terraform/Generic/JSON），Beta（Elixir/APEX/Dart），实验级 ~19，官方总口径"35+"——v0.2"30+ 语言"可接受但 GA 子集要有数
- **Pro 规则生态**：GA 语言各有 40–710+ 条 Pro 规则（Python 710+、JS 250+、TS 230+）
- **diff 扫描**：`--baseline-commit` 成熟（CI 样例：merge-base + SARIF + --error 门禁）
- **CodeQL 许可**：✅ 证实——仓库 MIT，CLI/引擎单独许可，**分析闭源代码需商业许可**（官方 README）。来源：https://github.com/microsoft/codeql
- **stack-graphs**（GitHub 开源，Rust）：✅ 证实。tree-sitter 之上的名字解析，file-incremental、SQLite 存储、LSP/CLI；语言实现 JS/TS/Java（+Python）。来源：https://github.com/github/stack-graphs（EVCS 2023，Creager & van Antwerpen）
- **LLM+SAST 先例**：IRIS（ICLR 2025）路线成立（二手锚点）；Big Sleep 假设驱动变体分析非通用 SAST

### C.2 可行性判定：可行有坑（边界已核验，结论强化）
坑清单（更新）：① **OSS taint 单函数边界（比预估更窄）——"L0 只做调用点收集"决策被强化**；② tree-sitter 语义层成本在名字解析（stack-graphs 可复用）而非解析；③ registry 规则质量参差（L1 吸收）；④ CodeQL 闭源代码需商业许可（可选升级定位不变）；⑤ 每语言 adapter 是长期人力投入

## D. LLM 成本（调研4，已返回）

### D.1 档位价格锚点（2025–2026，$/M token in/out）
- 便宜档：GPT-4.1-nano $0.10/0.40；GPT-5-nano $0.05/0.40；Gemini Flash-Lite $0.10/0.40；DeepSeek V3.2 缓存 $0.028/未命中 $0.28，out $0.42（不限流，适合高扇出 L1）
- 强档：Claude Sonnet 4.5 $3/15；Opus 4.5 $5/25；GPT-5.2 ~$1.75/14；Gemini 2.5 Pro $1.25/10
- 能力分层：SecLens(2026) 确认漏洞检测是分层最明显任务之一；PrimeVul 上闭源强模型 F1 明显优于开源 → **L1 可便宜档，L2 不能降档**

### D.2 级联证据
- FrugalGPT 降本最高 98%；RouteLLM 保 95% 质量降本 >85%
- GitHub HydraFusion 警示：最难任务只有顶档追平前沿 → 对应 L2 强档不能省
- 域内实证：Revelio（2026）分层 agentic 单仓 $29.85→$11.70 且检出更好；典型级联降本 60–90%

### D.3 成本实测锚点
- 仓库级：RepoAudit(ICML 2025)/Revelio 单仓 5–9.4M in / 0.1–1.3M out，直跑强模型 $11.7–29.85
- PR 级：轻量 $0.03–0.05/PR；Anthropic 官方 Code Review（无级联直跑 Opus 级）$16–25/PR（引发成本反弹）→ 反面标杆
- Batch API（OpenAI/Anthropic/Gemini 均 50% off）与 full 档天然契合

### D.4 三档成本估算（中型仓 ~30k 行 ≈ 400k token）
- **fast**：20 候选 × 便宜模型 ≈ **$0.004–0.02/次**
- **pr**：L1 + 8 点 L2(Sonnet) + 投票(Haiku) ≈ **$0.3–1.0/PR**（L2 上 Opus 则 $1–2）→ 比 Anthropic 官方方案便宜 1–2 个数量级
- **full**：同步 $5–12/仓；**Batch 后 $2.5–6/仓**

### D.5 供给结论
- 多模型混用**明确可行**（理论+生产+域内三重证据）；几百并发 agent 在中高 tier 现实；DeepSeek 不限流适合 L1 扇出
- 自托管纯成本打不过 DeepSeek 级 API，意义仅在数据不出域
- 建议配置：L1 = DeepSeek V3.2 / GPT-4.1-nano / Flash-Lite（多供应商冗余）；L2 = Sonnet 4.5 / GPT-5.2；投票 = Haiku 4.5 / GPT-5-mini
- ⚠️ 数字经多源交叉确认但 web_fetch 受限，正式报价以官方定价页复核

---

## E. 自行找茬清单（机制层，已完成）

1. **评审/侦察/验证蜂口碑闭环缺失**：文档仅定义对报告者的存款；评审投票未被 S2/S3 回校，侦察标记质量无下游反馈 → 三角色路由无法进化。修法：S2/S3 结论到达时，按投票方向对评审蜂补记 ± 存款；侦察蜂按"标记点下游存活率"记弱存款；S3 推翻 S2 时对验证蜂记负存款（非回滚，是新增存款）。
2. **verified_fp 证据标准未定义**：须要求 blocking_evidence（消毒剂实证/不可达证明/类型安全）必填，否则验证蜂会批量橡皮图章 fp（反向误杀机器）。
3. **Finding 去重/聚类缺失**：多审查蜂报同根因 → 重复存款、n_eff 虚增、S3 标记不传播。修法：cluster_id（path+lines±+CWE+证据重叠/embedding 相似），簇级结论传播，存款按簇记方向、按个体记报告信用。
4. **n_eff 与存款权重两套计分**：实际恰为 2 倍关系（S3: 2/1.0, S2: 1/0.5, S1: 0.1/0.05），文档未点明 → λ=5 语义须显式锚定（= 2.5 个 S3 当量）。
5. **多重比较**：promote_scan 反复扫 ~80 格子，假阳性触发难免；暗考闸门是吸收层（应显式声明为设计优势）；加候选门槛 "S2+S3 有效样本 ≥ 3" 防纯 S1 堆量。
6. **S2/S3 冲突语义**：显式写"冲突不回滚，按更强信号补记反向存款，净值收敛"。
7. **蜂后 = 代码内核 + LLM 顾问**：写态动作全部确定性代码执行，LLM 仅建议权 → 强化 §4.3 纪律1。
8. **TILT 自适应**：TILT_t = clamp(0.5 + k·(C*_spec − baseline), 0.5, 0.9)，回答待决#3。
9. **路由 = contextual bandit**：C*+TILT 是启发式，与 Thompson sampling 对比后保留简单方案（可解释/可审计），写入设计决策记录。
10. **机制仿真器**：冻结参数前用合成 agent（已知真实能力）Monte Carlo 验证晋升/衰退判据灵敏度 → 列为 v1 前置工程。

### E.1 次级找茬
- 衰退判据对称性：晋升 LB>基线+δ（难）、衰退 UB<基线（难）→ 组织惯性偏大，参数须仿真调
- C_raw 随挥发趋 0/0 → 需 ε 地板，跌破即退父层
- 基线计算须 leave-one-out（剔除被评者自身）
- 探索抽样"保底均匀"须量化：如 80% 加权 + 20% 均匀
- 评审团底模多样性：同底模错误相关 → 投票 panel 优先跨模型族（关联待决#6）
- 校准度：Brier 分按 agent 追踪，v1.5 进口碑
- M=30 口径须定义：原始任务数（达 S1+ 的 finding 数）而非加权
- 可观测性指标集：verify_error 率、S3 推翻率、考试通过率、组织级 FP 率（暗考口径）、队列深度 → 关联待决#8
