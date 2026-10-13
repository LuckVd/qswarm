# SPEC-8：L2 会诊 + repo map 切片注入 + route() 生产接线 + 影子期指标（M4 实现级规格）

> 版本：v1.0（2026-10-13 导出；D31–D34/D37/Q1 全部拍板见 DECISIONS 同日行）
> 来源：设计文档 v0.3.4 §2.1（repo map/漏斗）、§2.4（配额熔断）、§4.3（委托四件套）、
> §5.2（TILT 路由）、§6.5（归属矩阵）、§10.1–10.4（结论分级/评审聚合）、§11.6（注入防线）、
> §16.6（依赖序第 7 位）、§16.7-6（评审团构造）；SPEC-2（内核复用面）、SPEC-7（L0/L1 交接面、
> M3-1～M3-9 实现期决策）；评估报告 `docs/m4_assessment_20261013.md`。
> 硬纪律（承 SPEC-7 逐字）：**只复用不重写**——M0–M3 已落地的 kernel.*（ledger/cells/route/
> route_thompson/promote_scan/dispatch/budget/cluster/replay/bootstrap）、contracts、prompts、
> supply、exams、perception（indexer/channels/candidates/scan）一律原样复用；本 SPEC 只定义
> M4 新增面与最小增量修订。写口词表（15 表）零扩；账本唯一写口纪律不变。

---

## 0. 范围与模块落位

交付四件（m4_assessment §1 口径）：F1 L2 会诊生产编排、F2 repo map 切片注入、
F3 route() 生产接线、F4 影子期观察指标。边界：finding 终态到 **voted**；verifier 只到
D29 预裁决单（S2 真沙箱 = M5）；仲裁件 = M5（D34）。

| 模块 | 动作 | 内容 |
|---|---|---|
| `qswarm/perception/repomap.py` | **新增** | 名称绑定图（D37）+ 候选点 ±N 跳邻域切片 + 切片工件（§1） |
| `qswarm/kernel/consult.py` | **新增** | L2 会诊编排：L1 存活点 → route() 派 auditor → 粗聚类 → 面板投票 → S1 收口存款 → 精聚类 → 预裁决单 → 预算熔断 → 最小报告（§2） |
| `qswarm/kernel/cluster.py` | **微扩** | `VerdictContext` 增 `s1_factor: float = 1.0`；`propagate_verdict`/`_expansion_rows` 对 tier=S1 行源权重乘该因子（D32；缺省 1.0 = 既有行为逐位不变）（§2.4） |
| `qswarm/exams/seeding.py` | **微扩** | CLI `--hat-agents <agent_id,...>`：对既有 active 蜂注册指定角色帽并纳入该轮补播考（D31 帽兼任的考试面；§7） |
| `qswarm/exams/store.py` | **微扩** | `ARTIFACT_KINDS` 词表扩 `{repo_map, pre_verdict}`（M3-9 同款纪律；写口审计词表不动）（§1.3/§2.6） |
| `configs/params.yaml` + `contracts/config.py` | **回填** | `perception.repomap` 节 2 旋钮 + `consult` 节 4 旋钮（extra=forbid；缺节回落规格缺省保黄金兼容）（§6） |
| `configs/models.zhipu.yaml` | **微改** | mid 档 fallbacks 加 glm-5.3-flash（D31；expand_candidates 去重展开 → reviewer 候选 = 2 蜂） |
| `qswarm/kernel/loop.py` | **零改动** | L0→L1 面原样；consult 独立入口消费其产物（candidates triaged 行），e2e 组合调用 |

不做的（防蔓延）：沙箱执行（M5）、蜂后顾问接线（D34）、route_thompson 生产切换
（保持对照臂）、扩缩容、dashboard、full 档 Batch 化（依赖序第 10 位）。

## 1. repo map 切片（`perception/repomap.py`）

### 1.1 名称绑定图（D37 近似级）

输入 = `indexer.index_repo` 产物（`dict[path, IndexEntry]`；内容寻址缓存复用，不重析）。
纯函数构建两张图：

- **调用图（函数级）**：边 `(caller_path, caller_name) → (callee_path, callee_name)`。
  对每个 `IndexEntry.calls` 的被调名 n：① 同文件 `defs` 含 n → 仅同文件边（最强绑定）；
  ② 否则全仓 `defs` 中名为 n 的定义**恰一个** → 跨文件边；③ 零个或 ≥2 个 → 不成边
  （歧义保守丢弃，宁缺勿错）。caller 归属 = 调用行所在 def（defs 起止行包含调用行；
  文件顶层调用归 `"<module>"` 虚节点）。
- **import 图（文件级）**：正则逐行抽取（全语言统一，语言无关纪律）——
  `^\s*(?:import|from)\s+(\w+)`（py）/ `^\s*import\s+([\w.]+)`（js/ts/go/java）、
  `^\s*#\s*include\s*[<"]([\w./]+)[>"]`（c/cpp）、`^\s*import\s+([\w.]+)`（其余）。
  边 = 当前文件 → 同仓内路径前缀/模块名匹配的目标文件（`a/b.py` 匹配模块 `a.b` 与
  尾段 `b`；C 的 `#include "x.h"` 直接相对路径匹配）。匹配不到同仓文件的边丢弃。

无 grammar 语言（parse_ok=False）：调用图退化为空（调用点缺席），import 图照常——
切片仍可由 import 邻域供给（§2.1 退化语义逐字）。

### 1.2 邻域切片算法

`slice_for(anchors, index, hop, *, max_files, max_lines) -> RepoSlice`，纯函数确定性：

1. 种子 = anchor 集（候选点 `(path, line)`；所在 def 不存在则种子为文件级）；
2. BFS 扩展：第 k 跳沿调用图双向（caller+callee）与 import 图双向（importer+importee）
   扩展；hop = `dispatch.hop_budget_for(mode)`（fast 0 → 仅种子文件；pr 1；full 2）；
3. 文件收集序 = BFS 层序 + 层内字典序，达 `max_files` 截断（种子文件恒在）；
4. 每文件载荷 = 种子/路径经过的 def 全文（defs 起止行切行）+ 文件头 import 段 +
   无 def 命中时整文件截 `max_lines`；总行数达 `max_lines` 截断（截断计数入报告）。

`RepoSlice`（frozen dataclass）：`files: tuple[SliceFile,...]`（path/lang/spans/lines/
truncated）、`digest`（blake2b 内容寻址）、`hop`、`truncated_files/n_lines` 计数。

### 1.3 工件与注入

- 切片落工件库：`kind="repo_map"`（store.artifact_put 形态，词表扩入 §0 表）；
  `context_refs` 装配 = `artifact://<repo_map digest>` 1 条 + 逐文件 `"path:start-end"`
  行窗引用（build_contract 既有形态，§4.3「输入只给引用」逐字）。
- 切片渲染进 prompt 走 ContextBlock 数据块通道（M3 通道 B 同款），INJECTION_GUARD
  防线段自动共享（templates 单一源，§11.6-1 对切片同样生效——M4 卷 B 组判据钉）。

## 2. L2 会诊编排（`kernel/consult.py`）

### 2.1 入口与运行生命周期

```
run_consult(ledger, *, repo, mode, seed, adapter, out_dir, params=None, clock=None) -> ConsultReport
```

- 会话：`bootstrap.verify_ready(ledger)` fail-closed（复用，不重写）→
  `dispatch.open_run(mode, repo_url=repo)`。独立于 run_scan 的 run（L1 扫描 run 与
  会诊 run 分离，靠 candidates 表行衔接）；同参数重跑幂等（§2.8）。
- 输入 = candidates 表 `status='triaged'` 行（M3 L1 存活点；按 candidate_id 字典序）。
  无存活点 → 空 report 直接收口（合法，零事件）。
- 每步写态经 `create_task/dispatch/submit/transition/...` 既有写口；本模块零直写 SQL。

### 2.2 L2 派单（F3 route() 生产接线）

每候选一任务（M3-3 同款粒度）：`task_id = "l2:<candidate_id[:12]>:<seq:03d>"`、
role=auditor、taxonomy_node = 候选 guess 家族（L1 猜测 = 路由 taxonomy 初值，§2.1 逐字；
无猜测 → 候选 taxonomy_hint ∨ 分片多数族，同 M3-4 归属序）。

选蜂 = **`route()` 本体**（F3 逐字）：
`route(task, view_cells(ledger, now), rng, params, load=在途计数, max_load=params.route.MAX_LOAD?, pool=active auditor 蜂)`；
rng = `random.Random(derive_seed(seed, "route", run_id, candidate_id))`（随机性纪律同
SPEC-4 §6：显式注入、单原语）。RouteDecision.assignee → `dispatch()`；decision 字段
经 `to_dispatch_payload()` 落 re_route/dispatch 事件面（既有函数，零新形状）。纯探索池
（无 active 格）时 route 返回 empty_pool → fallback **least-loaded**（M3 temp 池同式，
行为降级不阻塞漏斗，报告计数 `l2_route_empty_pool`）。

任务头渲染：AUDITOR_SYSTEM + ContextBlock（候选块：文件片 ± L2_CONTEXT_LINES(40)
+ repo map 切片数据块 §1.3）+ AUDITOR_SCHEMA_FIELDS；产出 `parse_output("auditor")`
→ `submit()` auditor 分支（findings + evidence_nodes + finding 事件 + 错配 re_route
全部既有）。候选判 null（无漏洞）→ ScoutReport 式 null 面不适用——AuditorFinding
契约无 null 面，候选无 finding 产出 = 任务 done + 计数 `l2_clean`（零落库，防凑数
finding；与「L1 拦截不落存款」对称）。

### 2.3 粗聚类与面板

- 批内 finding → `FindingFacts` 装配 → `cluster_findings`（粗聚类提案）→
  `attribute_findings`（既有编排口，簇号 C-{date}-{seq} 内核签发）。
- 每簇开面板（§3 构造器）：3 席（D31）；对每席派投票任务
  `task_id = "vote:<finding_id>:<seq:02d>"`、role=reviewer、分片携带 finding_id +
  finding 行 + evidence_nodes + 候选代码行窗 + 簇成员摘要（ContextBlock）；
  `submit()` reviewer 分支（votes 行 + vote 事件 + 纪律 2 守卫既有）。
- 票齐（= 面板人数）→ `close_panel(expected_votes=len(panel), ctx=...)` 显式收口
  （不用 params.panel.SIZE 自动闭，D31 人数可变语义）→ open→voted 迁移 +
  `propagate_verdict(tier=S1)`（§6.5 全行展开 + D32 因子，§2.4）。

### 2.4 S1 收口存款（D32 因子）

- `VerdictContext` 增字段 `s1_factor: float = 1.0`；`close_panel` 装配 ctx 时按面板
  构成计算：`diversity = (distinct_real_models − 1)/(len(panel) − 1)`（len=1 时
  diversity=1），`s1_factor = 1 − 0.4·(1 − diversity)`（ρ=Z_PANEL_W，M0 常量对齐；
  数值钉：3 人同模型 0.6 / 3 人两模型 0.8 / 全异 1.0 / 2 人同模型 0.6）。
- `_expansion_rows`：tier=S1 时成员报告者行/侦察行/评审回校行的 source_w 乘
  `ctx.s1_factor`；S2/S3 行不乘。缺省 1.0 → 既有黄金/测试逐位不变（增量修订的
  兼容性硬判据）。
- 真实服务模型映射：agent_id → tier 前缀（cheap/strong→请求名≠服务模型，实际由
  models.yaml runtime 实测口径）——v1 以 **model_ref 的服务模型解析表**（configs
  models.yaml 的 tier→primary/fallback 名单 ∪ Coding Plan 映射注记）查得；解析不到
  → 按 distinct 计 1（保守降 diversity，多降权不少降）。

### 2.5 精聚类（sim_fn v1）

`sim_fn` 首个生产实现 = 确定性词面 Jaccard：
`J(A,B) = |A∩B|/|A∪B|`，A/B = 成员 finding 的 `{path, type∨family} ∪ {evidence
where_path 全集}`（词面 casefold）。`fine_mergeable` 判据与 CLUSTER_EMBED_SIM_MIN=0.9
常量不动。精聚类合并经 `apply_merges(level="fine")` 既有口；合并后簇成员变更是
verifier 预裁决的输入（§2.6）。无嵌入模型依赖（v1 不引供给面新依赖）。

### 2.6 预裁决单（D29 生产消费）

每精聚类簇一张：`task_id = "pv:<cluster_id>"`、role=verifier、**不走 submit 状态机**
（M3-1 先例：预裁决无状态机语义、无 verify_results 行、零存款——verify_results 是
S2 真验证专用面，词表不改）。装配 = DispatchContract 直装（output_schema_ref=
`pre_verdict` 自描述）+ 簇证据 ContextBlock；产出解析为新契约
`PreVerdictSlip(BaseModel)`：`expected_verdict: Literal["confirmed","refuted",
"inconclusive"]`、`poc_shape: str`、`env_requirements: list[str]`（contracts/models.py
新增，词表零改）。落工件 `kind="pre_verdict"`（§0 词表扩）；失败/schema 畸形 →
计数 `pv_schema_fail`（漏斗不停摆语义，D30 同款），不重试（预裁决单是尽力而为面）。

### 2.7 预算与熔断（§2.4 逐字）

- `budget_state(mode, params)` 开工取预算；每次 adapter.complete 后 `on_cost` 记名义
  费（D33：名义价驱动配额，订阅摊销只进报告披露）。
- L2 深诊派单前查 `quota_l2`（cap=budget.L2_QUOTA_CAP）；配额/预算尽 → 停止新 L2
  派单，未深诊候选计数 `l2_quota_skipped`；面板/预裁决任务不再新开（在途者跑完收口）。
- run 收口选择性过期：仍 `open`（未开面板）的 finding → `transition_finding`→expired
  （零存款，§10.2/§2.4）；**已 voted 不动**（`expire_run` 的全量语义在此不适用，
  逐 finding 过渡，报告计数 `expired_open`）。

### 2.8 幂等与失败语义

- 任务 id 全部确定性派生（candidate_id/finding_id/cluster_id 输入）→ 同 run 重跑按
  「已 done 跳过、在途 queued/dispatched 重派」收敛（M3 §5.6 同口径；精细断点恢复
  仍不做，v1 无此场景）。
- schema 畸形 → `re_dispatch(schema_fail)` 改派重试 1 次（§16.7-8 既有）；二次仍败 →
  任务 failed、计数、零存款（auditor 面不产凑数 finding）。供给失败 →
  re_dispatch(timeout) 同 M3。
- 墙钟：`consult.wall_clock_budget_s`（D33）超时 → 停新派单，走 §2.7 收口路径，
  报告 `wall_clock_exceeded=true`。

## 3. 面板构造器（D31；`kernel/consult.py` 内纯函数 `build_panel`）

- qualified 池 = `hats.role='reviewer'` ∧ `agents.status='active'`（帽与活性双条件；
  帽注册唯一写口 = exams/roster，§7）。
- 构造：剔除 finding.reporter → 排序键 `(在途 load 升序, agent_id)`，贪心选取时
  优先互补真实服务模型（已选模型数最少者优先，平手按排序键）→ 目标 3 席；
  池不足 3 → 全取（≥2 收口 + §2.4 因子）；**<2 → 不开面板**（finding 停 open，
  计数 `panel_starved`，fail-closed 不硬凑）。
- 在途 load = 本 run 内派给该 agent 的未收口任务数（进程内计数，M3 least-loaded 同式）。

## 4. 影子期观察指标（F4；`kernel/consult.py::shadow_report`）

纯读函数（`view_cells` 只读形态，不触 cells 写）：
`{cells_by_status: {shadow,candidate,active,dormant}, n_eff 分位数(p50/p90/max),
per_family: {family: {active_n, candidate_n, shadow_n}}, entry_knobs_zeroed: bool
（M3-7 注入覆写仍生效否）, exam_callback_ready: bool（常量 True，执行器在）}`。
进 consult_report 顶层；独立 CLI 可打印（`python -m qswarm.kernel.consult --shadow-only`）。
**裁决不在本里程碑**：指标面供负责人判影子期结束（D35 阈值建议随 M4 交付报告给出）。

## 5. 最小报告（D34）

`out/<run>/consult_report.{json,md}`（同内容两形态）：run 元（mode/repo/seed/墙钟/
预算名义费+摊销披露）；漏斗计数（candidates → audited → findings → clusters(coarse→
fine) → panels → voted → pre_verdicts → expired_open/skipped/starved）；逐 finding 表
（F-id、簇、family/type、severity、confidence、panel 人数、方向、disagreement 有无）；
预裁决单列表；存款计数（按 tier/role 分列）；route 诊断（specialist/explore 占比、
empty_pool 次数、tilt 均值）；shadow_report 摘要（§4）。md 面向人、json 面向机器，
字段一一对应。

## 6. params 旋钮（extra=forbid；缺节回落保黄金兼容）

```yaml
perception:
  repomap: {MAX_FILES: 12, MAX_LINES: 1500}     # §1.2 切片预算
consult:
  WALL_CLOCK_BUDGET_S: 3600                     # D33
  MIN_PANEL: 2                                  # §3 fail-closed 下限
  S1_RHO: 0.4                                   # D32（=M0 Z_PANEL_W 对齐钉值）
  L2_CONTEXT_LINES: 40                          # §2.2 候选块窗（L1 的 20 的深诊档）
```

逐键钉值测试 + 缺节回落测试（M3 perception 节同款两只）。

## 7. exams 补播面扩展（D31 帽兼任）

`seeding.py` CLI 增 `--hat-agents "<agent_id=role[,agent_id=role,...]>"`：
对**已存在**的 active agent 注册指定角色帽（`exams/roster` 唯一写口新增
`attach_hat(agent_id, role)`——hats INSERT 口既有实现内函数化，零新写面），并把
这些 (agent, role) 纳入本轮考试与 QC/存款/active 化流程（与 mid/engineering 新蜂
同判）。E 组补播执行命令随名册定案更新为：mid fallback 生效后的
`--roles reviewer,verifier --hat-agents "cheap:zhipu/glm-4.5-air@cv1=reviewer,..."`。
帽兼任蜂的 reviewer 格独立记账（agent×role 维，§4.1 逐字）。

## 8. 决策记录（本 SPEC 导出即拍板面）

D31/D32/D33/D34/D37/Q1 六行见 DECISIONS 2026-10-13（拍板人与理由在案）。
实现期微决策沿 M3-1～M3-9 先例以 M4-x 编号续记本文件不设——直接记 DECISIONS
（滚动协议 2）。预留实现期风险点：route() 对 auditor 的探索池语义（pool=active
auditor 全集 vs 家族过滤格——route 内部已按家族过滤，pool 只需全量 auditor）；
PreVerdictSlip 的 inconclusive 与 VerifyOutcome.verify_error 的语义边界（前者=
无法预判，后者=执行失败，互斥不混）。
