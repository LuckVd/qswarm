# M1 编排内核 终检交付报告

- **任务**：实现 M1 编排内核：聚类归并 + 预算熔断 + 委托状态机（清偿 M0 技术债）
- **子任务**：终检与交付报告（0 skip 全绿、写口审计、SPEC-实现对账、遗留问题）
- **终检执行时点**：2026-10-11（全量 pytest 332.53s + 门禁 grep 复跑 + 证据固化）
- **验收分支**：SPEC-5 §9.3 分支 (ii) 免审直跑（DECISIONS 2026-10-10 D26；见附录 A）——门禁不降档，§9.1 全项照常执行
- **证据目录**：`out/_m1_final/`（本报告 + 5 份证据文件）

---
> **【2026-10-11 更新：L1 已处置关闭（方案 a，负责人明示）】** `submit()` 四分支
> 补实现落位 `qswarm/kernel/dispatch.py`（SchemaRejected 退回 / candidates 三角化
> 回填 / 错配自动 re_route / 票满自动 close_panel / §10.4 改判守卫 + S2 映射与
> propagate_verdict(S2)），SPEC-5 §1.2 七例 `test_submit_*` 按名落位
> `tests/test_kernel_dispatch.py`——门禁 A 组「最低用例名集齐」**40/47 → 47/47**，
> §9.1 全项含 L1 处置全部通过（见文末「附录 E：L1 处置记录」；DECISIONS
> 2026-10-11 两行）。下文 §0/§9 的 40/47 与「待负责人明示」字样保留为终检时点
> 历史快照，不再反映现状。

---

## 0. 终检结论摘要

| # | 终检项 | 结论 |
|---|---|---|
| 1 | 全量 pytest | ✅ **528 passed, 0 failed, 0 skipped**，rc=0（`out/_m1_final/full_pytest.log`） |
| 2 | 写口审计（§2.1 判据 2 + audit_write_ports 双模式） | ✅ 独立 grep 空输出；19 条写口全部落在 §6.1 圣化模块，kernel/ 外违规 0，strict 漂移 0 |
| 3 | 无网络（§7.1） | ✅ 0 命中 |
| 4 | 无新增第三方依赖（§7.2） | ✅ 第三方闭集 = {pydantic, yaml, pytest}；pyproject.toml 依赖零变更；原判据命令白名单漏 3 个标准库根，已按滚动协议修订留痕（DECISIONS 107 行） |
| 5 | 黄金 M0 基线 digest（§4.1 判据 B） | ✅ 66/66 OK（sha256sum -c 退出码 0） |
| 6 | 官方矩阵（§5，如触发） | ✅ `--full --workers 3`：manifest workers=3、n_rows=507、wall 4118s；162/162 组 9/9（`out/group_targets.csv` 终检独立重读复核）；终检另证 M1 窗口内全部 4 批 experiment manifest workers 均 =3 |
| 7 | A 组最低用例名集齐（§1） | ⚠️ **40/47**：7 例 `test_submit_*` 全仓无承载（SPEC-2 §2.5 `submit()` 统一入口未落位）→ 遗留问题 **L1**，按 §9.2 如实记录，处置待负责人明示 |
| 8 | 确定性（§2.3） | ✅ 判据 2（零随机源 grep）0 命中；判据 1 点名文件/用例名未落位但语义等效覆盖（映射留痕，DECISIONS 106 行）→ 遗留问题 **L2**（已按滚动协议修订，无需行动） |
| 9 | 重放等价（§3） | ✅ 单元五用例 + 轨迹级专项均在全量绿内（`test_replay_equivalence_m1.py`、`test_kernel_replay_m1_events.py`）；仿真环内 ⑨=507/507（§R） |
| 10 | M0 四债清偿（§6） | ✅ 债①（view_cells 真只读）/债②（触发器补建）/债③（ENTRY_DIRECT 数学可达，**0 skip**）/债④（params 逐键钉住）判据全绿；`SHA256_MANIFEST_ALL_OK`（66/66 即第 5 项） |
| 11 | DECISIONS 同步（§7.4） | ✅ M1 窗口新增五字段行 **35 行**（2026-10-10×32 + 终检 2026-10-11×3），清单见 §5 |
| 12 | 重负载并发 3 留痕（§5.2/§7.3） | ✅ M1 窗口 experiment 批 4/4 全部 workers=3；pytest 为串行单进程（无 workers 语义，无 xdist 且禁新增依赖）——留痕表见 §7 |

**总判定**：终检机器判据除 **L1**（A 组 7 例 submit 最低用例名无承载，属实现范围切片遗留而非测试失败——文件级 pytest 判据全绿）外全部通过；按 SPEC-5 §9.2「原样记录触发项与现场输出，如实报告」，L1 处置（补实现 submit 四分支 / 修订 §2.5+§1.2 判据）**待负责人明示**，本报告不放行、不掩盖。

---

## 1. 全量 pytest：528 passed / 0 skipped（子任务 ac-1）

命令（仓库根；串行单进程——pytest 无 workers 语义，pytest-xdist 未安装且本任务禁新增依赖，与 M0/M1 既有全量记录同口径）：

```
$ python3 -m pytest -rs --tb=short
........................................................................ [100%]
528 passed in 332.53s (0:05:32)
PYTEST_EXIT=0
```

- 日志全路径：`out/_m1_final/full_pytest.log`（含命令行、时间戳、`DONE_MARKER`）
- **0 skip 证明**：进度行无 `s/F/E/x` 字符；`-rs` 无任何 short summary 输出；汇总行无 `skipped`/`failed`/`error` 字样
- 规模沿革：M0 时点 338 passed + 1 skipped（债③修前唯一 skip）→ 技术债清偿批 394 → M1 收官 **528 全绿 0 skip**（各子任务新增 M1 测试 134 例）
- 恰含 SPEC-5 各组判据文件：A 组五文件、B 组（写口/幂等/确定性）、C 组（重放三投影）、D 组（黄金回归 + 突变门）、F 组（四债）、§1.5 事务横切

## 2. 变更清单（H-1：文件级 + 一句话目的）

### 2.1 M1 新增（qswarm/ 包内）

| 文件 | 目的（SPEC-2 落位） |
|---|---|
| `qswarm/kernel/cluster.py` | **新增**：两级聚类判据、建簇/扩簇/拆分、簇级结论传播、attribute_findings 编排口（§1） |
| `qswarm/kernel/dispatch.py` | **新增**：委托契约四件套装配、派单/Finding 结论双状态机、open_run/open_finding 写口切片（§2.2–§2.4；§2.5 submit 未落位→L1） |
| `qswarm/kernel/reroute.py` | **新增**：re_route 触发、猜测格弱存款、猜测聚合（§3） |
| `qswarm/kernel/budget.py` | **新增**：三档预算、L2/S2 配额、熔断降级、过期收尾（§4） |
| `qswarm/kernel/write_guard.py` | **新增**：§6.1 写口词表扫描实现本体（audit_write_ports.py 与写口纪律测试共用单一实现） |

### 2.2 M1 最小扩展（既有文件）

| 文件 | 目的 |
|---|---|
| `qswarm/kernel/replay.py` | 三投影重建（rebuild_clusters/rebuild_findings）与逐位 diff（§6.3）；M1 事件装载 |
| `qswarm/kernel/ledger.py` | `transaction()` 可重入事务上下文（D24）；`rebuild()` 升级三投影；债②触发器补建（§6.2） |
| `qswarm/kernel/cells.py` | 债①：新增 `view_cells` 只读形态，评估路径不再触发 cells 物理写（2026-10-10 13:35） |
| `qswarm/kernel/promote_scan.py` | 债①配套：评估路径改走 view_cells（同日） |
| `qswarm/kernel/__init__.py` | 导出 M1 新模块符号 |
| `qswarm/contracts/models.py` | `DepositPayload.src_seq` 可选字段（§5.2/D7）；`BudgetParams` 三新键带规格缺省 |
| `qswarm/contracts/config.py` | budget 节 3 新键契约（FAST_CENTS/COST_PER_L2_CENTS/COST_PER_S2_CENTS，§8.1） |
| `configs/params.yaml` | M1 旋钮回填：纯新增 3 键 + 注释（53 既有键值逐字未动；`out/_m1_evidence/params.m1.diff` 留痕） |
| `qswarm/simulator/engine.py` | 仿真接通真实聚类：昼末经 attribute_findings 归属、open_finding 建档（§0.4，sanctioned） |
| `qswarm/simulator/verdict.py` | 世界侧 verdict 流对接内核谱系（VerdictContext 装配） |

### 2.3 M1 新增测试与工具

| 文件 | 目的 |
|---|---|
| `tests/test_kernel_cluster.py`（20 例） | §1.1 最低集 16 例 + CL1–CL5 边界 |
| `tests/test_kernel_dispatch.py`（12 例） | §1.2 文件级判据（12/17 最低集，7 例 submit_* 缺→L1） |
| `tests/test_kernel_reroute.py`（10 例） | §1.3 最低集 4 例 + 幂等/标准形 |
| `tests/test_kernel_budget.py`（14 例） | §1.4 最低集 8 例 + 熔断/配额边界 |
| `tests/test_kernel_transaction.py`（5 例） | §1.5 事务横切最低集 3 例 |
| `tests/test_kernel_replay_m1_events.py`（4 例） | §3.1 M1 事件重放/三投影 |
| `tests/test_replay_equivalence_m1.py`（3 例） | §3.2 轨迹级专项（3 种子 × 检查点 + 收尾全账本 + cells 物理表） |
| `tests/test_sim_cluster_wiring.py`（8 例） | §0.4 接通判据 + 确定性跨 PYTHONHASHSEED |
| `tests/test_verdict_deposits.py`（21 例） | S1/S2/S3 存款矩阵行 + verdict 流位级/跨进程 |
| `tests/test_golden_cluster.py`（6 例） | cluster 黄金类回归 |
| `tests/test_golden_mutation_gate.py` / `test_golden_regression.py` / `golden_loader.py` / `m1_harness.py` | 黄金并轨（dispatch/re_route/budget/cluster 入 M1_KINDS）与突变门扩展 |
| `tests/test_kernel_write_guard.py` / `test_kernel_ledger.py` / `test_kernel_replay.py` / `test_kernel_promote_scan.py` / `test_kernel_cells.py` / `test_config.py` / `test_sim_exam.py` / `test_sim_engine.py` / `test_sim_report.py` / `test_m0_final_knobs.py` | 债①–④判据与既有面扩展（写口纪律 11 表、schema 触发器、12 题口径、逐键钉住） |
| `tests/golden/cluster|budget|dispatch|re_route/`（15 夹具） | M1 四新黄金类（origin=spec_literal，冻结前自检） |
| `scripts/audit_write_ports.py` | 一键写口审计入口（终检复用） |
| `scripts/freeze_golden_m1.py` / `freeze_budget_golden.py` / `freeze_golden.py`（恢复） | M1 黄金冻结入口与 WINDOW_DAYS 解耦（DECISIONS 104 行） |
| `experiments/_rerun_w3_verify.py` / `_rerun_w3_matrix_compare.py` / `_rerun_digest_rootcause.py` | workers=3 复跑批四类证据与 digest 根因定位 |
| `docs/spec/SPEC-2_kernel.md` / `SPEC-5_m1_acceptance.md` | 两 SPEC 定稿导出 + 终检三轮修订留痕（2026-10-11） |
| `docs/spec/DECISIONS.md` | M1 窗口新增 35 行（§5） |
| `docs/spec/README.md` | 规格索引状态更新（SPEC-2/SPEC-5 ✅） |
| **删除**：无 | — |

## 3. SPEC-实现对账表（H-2）

### 3.1 SPEC-2 §0.2 M1 新增模块落位（8 行逐行）

| §0.2 行 | 状态 | 落位文件 | 测试文件（用例数） | 黄金类 | 对账 |
|---|---|---|---|---|---|
| `kernel/cluster.py` | 新增 | ✅ 同名落位 | test_kernel_cluster(20)、test_golden_cluster(6)、test_sim_cluster_wiring(8)、test_kernel_replay_m1_events(4) | `tests/golden/cluster/`（4 案，CL5 独立树） | ✅ |
| `kernel/dispatch.py` | 新增 | ✅（§2.2–§2.4 + open_run/open_finding 切片） | test_kernel_dispatch(12)、test_verdict_deposits(21)、test_sim_cluster_wiring(8) | `tests/golden/dispatch/`（4 案，共享 golden_loader） | ⚠️ §2.5 submit() 统一入口未落位→L1；其余语义面分散落位有测试 |
| `kernel/reroute.py` | 新增 | ✅ | test_kernel_reroute(10) | `tests/golden/re_route/`（4 案） | ✅ |
| `kernel/budget.py` | 新增 | ✅ | test_kernel_budget(14) | `tests/golden/budget/`（3 案） | ✅ |
| `kernel/replay.py` | 扩展 | ✅ rebuild_clusters/rebuild_findings + diff | test_kernel_replay、test_kernel_replay_m1_events(4)、test_replay_equivalence_m1(3) | 复用 slices 全家（回归钉住） | ✅ |
| `kernel/ledger.py` | 最小扩展 | ✅ transaction() + 三投影 rebuild + 债②补建 | test_kernel_ledger、test_kernel_transaction(5) | —（不变量由写口/幂等测试承载） | ✅ |
| `contracts/models.py` | 最小扩展 | ✅ DepositPayload.src_seq | test_kernel_cluster、test_golden_cluster、test_kernel_replay_m1_events（src_seq 21 处断言） | 随 cluster 黄金类 | ✅ |
| `contracts/config.py`+`params.yaml` | 扩展 | ✅ budget 3 新键 | test_config（债④逐键钉住） | manifest_params 形态回归 | ✅ |

### 3.2 SPEC-2 §0.3 M0 复用清单（逐行「复用未改/最小增量」声明）

| §0.3 面 | 声明 | 证据 |
|---|---|---|
| `KernelLedger.append/read/latest_seq` | **复用未改**（append 仅增事务内延迟提交分支，D24；无上下文路径逐位不变，test_kernel_transaction::test_append_without_context_unchanged 钉住） | 全量回归绿 |
| 三时钟 Wall/Fixed/Stepped | **复用未改** | test_kernel_ledger 绿 |
| agg_cells/CellView | **复用未改**；另增只读形态 view_cells（债①，聚合语义逐位一致） | test_kernel_cells + digest 66 OK |
| route/route_thompson/RouteDecision | **复用未改** | test_kernel_route(✓)+test_kernel_route_thompson(✓)+黄金 route×9/ts×9 |
| promote_scan（含 v2 通道） | **复用未改**（债①仅改其评估调用的 cells 入口） | test_kernel_promote_scan + 黄金 promote×8 |
| rebuild_cells/diff_cells | **复用未改** | test_kernel_replay 绿 |
| PAYLOAD_SCHEMA 校验 | **复用未改** | test_schema 绿 |
| schema.sql append-only 触发器 | **复用未改**（债②将「在位判定」升级为表+触发器双查，触发器 DDL 本体未动） | test_kernel_ledger 债②用例 |
| M1 新函数零复制声明 | cluster/dispatch/reroute/budget 只**调用**上述面（import 关系见 §3.1 测试映射；写口审计 19 口全部为各圣化模块自有 SQL，无跨模块复制写逻辑） | 写口审计 strict 0 漂移 |

### 3.3 SPEC-5 判据组 → 证据对账

| SPEC-5 组 | 判据 | 终检证据 | 判定 |
|---|---|---|---|
| §1 A 组 | 文件级 pytest + 最低用例名 | 全量绿；最低集核验 40/47（**7 例 submit_*→L1**） | ⚠️/✅ |
| §2.1 写口 | 纪律测试 + 独立 grep | test_kernel_write_guard 绿 + grep 空输出 + audit 双模式 PASS | ✅ |
| §2.2 幂等 | ≥8 例双界线 | cluster/dispatch/budget/reroute 幂等+guard 用例全绿（20+12+14+10 内） | ✅ |
| §2.3 确定性 | 判据 1（位级+跨哈希序）判据 2（零随机源） | 判据 2：0 命中；判据 1：等效映射 6 用例全绿（DECISIONS 106） | ✅（修订留痕） |
| §2.4 M0 复用面 | 七文件 pytest | 全量绿 | ✅ |
| §3 重放等价 | 单元五例 + 轨迹专项 + 环内校验 | replay_m1_events(4)+replay_equivalence_m1(3)+仿真 ⑨=507/507 | ✅ |
| §4 黄金 | digest 逐位不变 + 突变门 + M1 四新类 | 66/66 OK；突变门绿；cluster4+budget3+dispatch4+re_route4=15 夹具入回归 | ✅ |
| §5 矩阵 | 162/162×9/9、workers=3、位级复跑 | manifest workers=3 n_rows=507；group_targets 162 qualifies 全真（终检重读）；ALL_EQUAL 形态差异已按 §R 如实归档（ sanctioned 接线差异，非跌标） | ✅ |
| §6 四债 | §6.1–§6.5 | 债①–④判据全绿；0 skip；SHA256 全 OK | ✅ |
| §7 约束 | 无网络/依赖/并发 3/DECISIONS | §4/§6/§7/§5 本报告 | ✅ |
| §8 交付对账 | 七项 | 本报告 §2–§7 + 附录 A/B/C | ✅（L1 如实标注） |
| §9.1 门禁 | 全项勾选 | §0 摘要表；**A 组最低用例名一项按 40/47 如实标未满足** | ⚠️ L1 待决 |

## 4. 写口 grep 审计证据（H-3）

**判据 2 独立 grep（SPEC-5 §2.1 逐字命令，空输出即通过）**：

```
$ grep -rnE "\b(INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|UPDATE|DELETE[[:space:]]+FROM)[[:space:]]+(ledger|cells|findings|evidence_nodes|votes|verify_results|clusters|cluster_members|tasks|candidates|runs)\b" \
    qswarm/ --include="*.py" | grep -v "^qswarm/kernel/"
（无输出）
[pipeline exit=1 —— grep 无命中=无越界]
```

**一键审计（与写口纪律测试共用单一实现）**：

```
$ python3 scripts/audit_write_ports.py          → 写口清单 19 条全部 ✓，违规 0，PASS，exit 0
$ python3 scripts/audit_write_ports.py --strict → 清单漂移 0 条，PASS，exit 0
```

19 条写口分布：budget.py×1（UPDATE runs）、cells.py×2、cluster.py×5（clusters/cluster_members）、dispatch.py×11（tasks/findings/runs）、ledger.py×1（INSERT ledger）——与 SPEC-2 §6.1「表 → 唯一写口模块」逐条相符。
完整输出：`out/_m1_final/write_port_audit.log`、`out/_m1_final/gate_grep_evidence.log` §1。
**无新增绕过**：终检未新增任何 SQL 写语句；本轮唯一包内代码变更为零（终检只改 docs/spec 与 out/ 证据）。

## 5. DECISIONS.md 新增行清单（H-4）

M1 窗口（SPEC-2/5 导出 2026-10-10 起）共新增 **35 行**（五字段齐备），行号 73–107：

- **行 73–86（规格导出与定稿拍板，14 行）**：SPEC-2 导出（73）、旋钮缺省纪律（74）、finding 事件承载（75）、src_seq 字段（76）、re_route 触发口径（77）、SCALING 承载降级（78）、D19–D24 六条定稿（79–84）、D25 §5.4 命令修正（85）、**D26 免审直跑**（86）。
- **行 87–104（实现期切片与清偿，18 行）**：债③④清偿（87）、债① 真只读拍板（88）、债② 补建拍板（89）、params 三键回填（90）、dispatch 切片（91）、reroute 切片（92）、hop_budget 口径（93）、M1 黄金并轨（94）、budget 切片（95）、D24 transaction 落地（96）、budget 黄金（97）、CL1–CL5 五条（98–102）、仿真接线 sanctioned（103）、冻结工具恢复与重冻（104）。
- **行 105–107（本轮终检新增，3 行）**：105=§1.2 七例 submit_* 无承载对账（→L1）；106=§2.3 判据 1 修订留痕（等效映射）；107=§7.2 白名单补 3 标准库根。
- 完整单行摘要表（35 行 × 行号/日期/模块/决策首句）见附录 D；全文即 `docs/spec/DECISIONS.md`。

## 6. 无网络与依赖自查（G 组）

**§7.1 无网络（逐字命令）**：

```
$ grep -rnE "\b(socket|ssl|urllib|urllib3|http\.client|httpx|requests|aiohttp|ftplib|smtplib|telnetlib)\b" qswarm/ --include="*.py"
（0 命中，exit=1）
```

**§7.2 依赖**：

- 修订后判据命令（DECISIONS 107 行留痕）：输出为空 ✅
- 原始命令（修订前口径）：输出 `contextlib`/`functools`/`re` —— 三者均为 CPython 标准库，非第三方；白名单漏列系规格笔误，已修订
- AST 全量扫描（81 个 .py：qswarm+tests+scripts+experiments+conftest）：标准库外 import 根闭集 = **{pydantic, yaml, pytest}** + 仓库本地模块（golden_loader/m1_harness，经 tests/ sys.path 装载）；无任何网络库；`subprocess` 仅 qswarm/simulator/experiments.py（M0 DECISIONS 2026-10-08 在案的沙箱降级执行器）
- `pyproject.toml`：`dependencies = [pydantic>=2.7, pyyaml>=6.0]`、`dev = [pytest>=8]` —— **M1 零变更**（本终检亦未动）
- 证据：`out/_m1_final/dep_ast_scan.log`、`gate_grep_evidence.log` §2/§5/§6

## 7. 重负载并发 3 留痕（H-7；子任务 ac-3）

M1 窗口（2026-10-10 11:00 SPEC 定稿后）**全部** experiment 批次 manifest 终检逐个核验：

| 时点 | 批 | workers | n_rows | 命令（argv） |
|---|---|---|---|---|
| 10-10 22:47 | `_rerun_w3_evidence/canary` | **3** | 3 | `--round 52 … --workers 3` |
| 10-11 00:02 | `experiments-full`（官方全矩阵） | **3** | 507 | `--full --workers 3 --out results/experiments-full` |
| 10-11 00:11 | `_rerun_w3_evidence/probeA` | **3** | 3 | `--round 53 … --workers 3` |
| 10-11 00:11 | `_rerun_w3_evidence/probeB` | **3** | 3 | `--round 54 … --workers 3` |

- **workers>3 批次数 = 0**；官方矩阵 wall 4118s ≤ 2h、per-run max 54.4s ≤ 5 min（`results/full-run6-w3.log`）
- M0 期历史 manifest 63 批（workers 分布 {4:19, 3:42, 2:2}）全部早于 M1 窗口，属 §7.3 明文「历史留痕仅作对照」
- `grep workers results/full_run_v2.sh` 唯一命中 `--workers 4`（M0 旧脚本，§7.3 同条明文对照豁免）；`results/full-run-m1.log` 不存在（M1 矩阵日志为 full-run6-w3.log）
- 全量 pytest 为串行单进程：pytest 无 workers 语义；pytest-xdist 未安装，本任务「无新增第三方依赖」约束下不引入——与全仓既有全量记录同口径
- 证据：`out/_m1_final/workers_evidence.log`（含全部 67 批 manifest 的 workers×时刻分类与 M1 窗口判定）

## 8. 遗留问题（H-5）

| # | 级别 | 问题 | 现场证据 | 建议处置 |
|---|---|---|---|---|
| **L1** | 高（门禁字面缺口，非测试失败） | SPEC-2 §2.5 `submit()` 统一代落账入口未落位（四分支：scout 三角化回填/auditor 建档+SchemaRejected 退回/reviewer 自评守卫+票满自动收口/verifier 改判守卫的 submit 内编排）；SPEC-5 §1.2 最低用例集 7 例 `test_submit_*` 全仓无承载 → 门禁 A 组「最低用例名集齐」40/47 | `grep -rn "def submit(" qswarm/` 0 命中；dispatch.py 模块 docstring「范围边界」明文在案；DECISIONS 105 行；语义面分散落位且测试全绿（reroute 10 例、open_finding 入 dispatch 12 例、cluster.close_panel/propagate_verdict + verdict_deposits 21 例） | 二择一待负责人明示：(a) 补实现 submit 四分支 + 7 例用例（契约面收口，推荐）；(b) 修订 §2.5/§1.2 判据为「窄口语义面」口径并同步 DECISIONS。按 §9.2 本终检不放行该项 → **已于 2026-10-11 按方案 (a) 处置关闭**（顶部更新块 + 附录 E；SPEC-5 §1.2 处置留痕；DECISIONS 同日两行） |
| **L2** | 低（已按滚动协议修订闭环，留档备查） | SPEC-5 §2.3 判据 1 点名文件/用例名与实现落位偏差（test_kernel_determinism.py 不存在） | DECISIONS 106 行 + SPEC §2.3 修订留痕注 + 6 用例等效映射全绿 | 无需行动；若后续增设集中确定性文件可按映射归并 |
| **L3** | 信息 | M1 接线后轨迹 digest 对 M0 归档批全量不同（507/507）与 45 runs 度量差异——已由 M0_GATE_FINAL §R 如实归根因（配置指纹 3 新键 + cluster_merge 事件位移 + 簇级传播），非门禁跌破、非调参掩盖 | results/_rerun_w3_evidence/compare_all_groups.{csv,log}；§R-R2 | 无需行动；严格 digest 级 ALL_EQUAL 仅对同代码态复现成立（复跑批自身 probeA/B 逐位一致已证） |
| **L4** | 信息 | dispatch 切片行（91）曾预期 reviewer/verifier submit 分支由接线子任务回补，接线子任务最终按 §0.4 窄口交付——两行决策的衔接缝隙即 L1 的成因 | DECISIONS 91 vs 103 行对读 | 随 L1 一并处置 |

## 9. 门禁节 §9.1 勾选状态（终检视角）

- [x] A 组四块功能全绿（文件级五命令全 passed）——*「最低用例名集齐」40/47，7 例 submit_* 缺 → L1 如实标未满足*
- [x] 不变量：写口全词表绿 + 独立 grep 空；幂等 ≥8 例；确定性两判据（判据 1 按修订映射）绿 + 零随机源 grep 空
- [x] 重放等价：§3.1 五例 + §3.2 专项 + 仿真 ⑨ 507/507
- [x] 黄金：37 例基线 + digest 66/66 OK + 突变门绿 + M1 四新类 15 夹具入回归 + `--check` 退出码 0（DECISIONS 104 行重冻后）
- [x] 矩阵（§5.1 已触发）：162/162×9/9 PASS + 位级复跑批自身 ALL_EQUAL（probeA/B）+ 归档与引用更新（M0_GATE_FINAL §R）
- [x] 技术债四项：§6.1–§6.4 全绿 + 全量 0 skip + SHA256 全 OK
- [x] 约束：无网络 grep 空、依赖白名单闭集（修订留痕）、并发 3 留痕、DECISIONS 同步（35 行）
- [x] 全量 pytest：528 passed / 0 failed / 0 skipped（N ≥ 339+新增 ✅）
- [x] 交付对账：§8 七项齐备（本报告）
- [x] **L1 处置**：~~待负责人明示（补实现 / 修订判据）后勾选~~ 2026-10-11 方案 a 补实现落位、47/47 集齐后勾选（附录 E）

---

## 附录 A：免审直跑记录（H-6，SPEC-5 §9.3 分支 (ii)）

- 判定：**人工介入不可用**（taskflow 自治执行会话，无负责人实时审批回路）→ 记 DECISIONS 2026-10-10 **D26**（行 86；五字段：日期/模块=SPEC-5/决策=免审直跑/理由=环境无人工介入通道/影响=门禁照常全量执行），执行记录见 SPEC-5 §9.3 末条。
- 门禁不因免审降档：§9.1 全项 + §9.2 停止判定 + §5.6 跌标即停照常执行（本报告 §0/§9 即全量执行结果，L1 按 §9.2 如实上报）。
- **两 SPEC 全文随本报告交付**：附录 B = SPEC-2 定稿 v1.0 全文（含终检 0 改动）；附录 C = SPEC-5 全文（含终检 2026-10-11 三处修订留痕注）。负责人批准记录：无（分支 (ii) 无批准环节）。

---

## 附录 B：SPEC-2 编排内核接口（全文）

> 以下为 `docs/spec/SPEC-2_kernel.md` 定稿 v1.0（2026-10-10）全文逐字快照（终检未改动该文件）。

<!-- ↓↓↓ SPEC-2_kernel.md 全文开始 ↓↓↓ -->

# SPEC-2 编排内核接口（M1 实现级规格）

> 来源：设计文档 v0.3.4 §2.1/§2.2/§2.4/§3.2/§3.4/§4.1–4.5/§5.2/§6.1/§6.2/§6.5/§10.2/§10.3/§10.4/§11/§12/§16.6/§16.7；SPEC-1 §2/§4/§5/§6；SPEC-4 §0；DECISIONS.md 全部既有决策（尤其：缺省关闭=规格逐字、写口纪律、promotion 事件承载考试流转）。
> 读者：AI 编码代理。函数签名 / 字段名 / 语义不得偏离本文件；发现矛盾以本文件为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 硬纪律：**只复用不重写**——M0 已落地的 `kernel.ledger` / `kernel.cells` / `kernel.route` / `kernel.route_thompson` / `kernel.promote_scan` / `kernel.replay` 及 `contracts` 一律原样复用（SPEC-4 §0「仿真复用内核实现」的同一纪律向 M1 延伸）；本 SPEC 只定义 M1 新增面与必要的最小增量修订。
> 本文件只写规格；实现落位与 `params.yaml` 回填由 M1 实现子任务按本文件执行。
> 状态：**定稿 v1.0（2026-10-10，规格门歧义拍板）**——本轮新增决策 D19–D24（五字段定稿表见 §10.1，已逐条同步 `docs/spec/DECISIONS.md`），并对 §1.3/§1.4/§1.5/§2.3/§2.5/§7.1 做定稿微修（逐处标注 D 编号）。实现期发现的进一步未定细节按滚动协议记 `DECISIONS.md`，不回改本文件编号语义。门禁分支裁定见 SPEC-5 §9.3 执行记录。

---

## 0. 范围、模块落位与来源映射

### 0.1 五块机制 ↔ 设计章节 ↔ 本文件章节（验收对映表）

| 机制块 | 设计文档出处 | 本文件 |
|---|---|---|
| 聚类归并（粗/精两级、split/merge 事件、簇级结论传播） | §3.4、§2.1、§3.2、§5.3、§16.5 | §1 |
| 委托契约四件套与派单状态机（含 Finding 结论状态机） | §4.2、§4.3、§4.4、§10.2、§10.3、§10.4、§16.7-8 | §2 |
| re_route 与侦察蜂猜测格弱存款 | §6.5 附带、§2.1、§3.2 | §3 |
| 配额与预算熔断（三档） | §2.4、§2.2、§12、§16.7-1 | §4 |
| 并发纪律（单写者/事务/三种读/时间解耦） | §4.5、SPEC-1 §6 | §7 |

其余章：§5 账本事件与 payload 契约总表；§6 四条不变量 + 确定性；§8 新旋钮清单；§9 M1 门禁要点；§10 决策记录索引。

### 0.2 M1 新增模块落位

| 模块 | 状态 | 内容 |
|---|---|---|
| `kernel/cluster.py` | **新增** | 两级聚类判据、建簇/扩簇/拆分、簇级结论传播（§1） |
| `kernel/dispatch.py` | **新增** | 委托契约装配、任务派单状态机、蜂产出代落账、Finding 结论状态机（§2） |
| `kernel/reroute.py` | **新增** | re_route 触发、猜测格弱存款、猜测聚合（§3） |
| `kernel/budget.py` | **新增** | L2/S2 配额、预算熔断与降级、过期收尾（§4） |
| `kernel/replay.py` | **扩展** | 三投影重建 `rebuild_clusters` / `rebuild_findings` 与逐位 diff（§6.3） |
| `kernel/ledger.py` | **最小扩展** | `transaction()` 事务上下文；`rebuild()` 升级为三投影（§7.1/§6.3） |
| `contracts/models.py` | **最小扩展** | `DepositPayload` 增可选字段 `src_seq`（§5.2） |
| `contracts/config.py` + `configs/params.yaml` | **扩展** | `budget` 节 3 个新键（§8；由实现子任务回填） |

### 0.3 M0 复用清单（禁止重写的既有面）

`KernelLedger.append/read/latest_seq`、三时钟（`Wall/Fixed/Stepped`）、`agg_cells`/`CellView`（C*/LOO/地板）、`route`/`route_thompson`/`RouteDecision`、`promote_scan`（含 v2 通道）、`rebuild_cells`/`diff_cells`、`PAYLOAD_SCHEMA` 校验、`schema.sql` append-only 触发器。M1 全部新函数只**调用**这些面，不复制其逻辑。

### 0.4 仿真接通原则（对 M1 总目标「接通仿真真实聚类」）

SPEC-4 §0 的复用纪律延伸到 M1：仿真器接通真实聚类/预算时，直接调用 `kernel.cluster` / `kernel.budget` / `kernel.dispatch`，不写第二份实现；世界侧只负责生成 finding 流、相似度函数（确定性文本相似度注入 `sim_fn`，见 §1.2）与谱系装配（`VerdictContext`，见 §1.4）。M0 九项度量与既有 golden 轨迹**不得回归**（§9）。

---

## 1. 聚类归并（`kernel/cluster.py`；设计 §3.4）

### 1.1 机制语义（逐条可溯源）

- **两级归并**（§3.4）：
  - **粗聚类**（投票前）：仅「同 `path` + 行窗重叠 ≥50% + CWE 同 + `lang` 同」的保守合并，省投票；
  - **精聚类**（投票后）：粗条件**全部成立**之外再加入「证据链向量相似度 ≥ 0.9」，省沙箱；
- **保守优先**（§3.4 逐字）：宁可漏并（多花一次验证）不可错并（丢真 finding）；
- **簇级验证**（§3.4/§5.3）：S2 按簇跑（一簇一沙箱），成员共享结论；
- **结论传播**（§3.4/§6.5）：簇收到 S2/S3 verdict → 按 verdict 方向给每个成员的报告者记存款（§6.5 归属矩阵全行展开）；S3 标记任一成员 → 簇内传播；
- **错并可人工拆分**（§3.4）：`cluster_split` 事件，**拆分本身零存款**；
- **为什么必须有**（§3.4/§2.1）：防重复存款虚增 n_eff、防 S3 标记失效、防 L2/S2 预算翻倍；归并决策由内核执行（确定性规则），事件落账本可审计。

### 1.2 常量、数据结构与函数签名

```python
#: 粗/精两级共用的行窗重叠下限（§3.4 逐字 ">=50%"；模块常量不进 params，
#: extra=forbid 纪律同 route.SPEC_CAP 先例，DECISIONS 2026-10-08）。
CLUSTER_OVERLAP_MIN: float = 0.5

#: 精聚类证据链向量相似度阈值（§3.4 逐字 "相似度 ≥0.9"）。
CLUSTER_EMBED_SIM_MIN: float = 0.9

#: S1 共识存款源权重（§6.1 区间 [0.05, 0.1] 取下沿；世界侧 VerdictParams.s1_weight
#: 是仿真实验臂旋钮不进内核，DECISIONS 2026-10-08 先例）。S2/S3 源权重为规格常量
#: 0.5 / 1.0（§6.1；与 simulator.verdict.S2_SOURCE_WEIGHT/S3_SOURCE_WEIGHT 同源同值）。
S1_SOURCE_W: float = 0.05
S2_SOURCE_W: float = 0.5
S3_SOURCE_W: float = 1.0

#: 相似度函数注入点：接收两个 FindingFacts（含 sim_text），返回 [0,1] 相似度。
#: **缺省 None = 精聚类不产生任何合并**（保守漏并；§3.4 宁漏勿错 + §8 旋钮表）。
SimFn = Callable[["FindingFacts", "FindingFacts"], float]


@dataclass(frozen=True)
class FindingFacts:
    """聚类判据所需的 finding 事实面（调用方从 findings 表行 + evidence_nodes 装配）。"""

    finding_id: str
    path: str
    line_start: int
    line_end: int
    cwe: str | None                # None = 未判 CWE（§3.1 可空）
    lang: str
    family: str
    type: str | None
    sim_text: str = ""             # 标题 + 证据链序列化文本（kind:where:ref 串联），
                                   # 供注入的 embedder 消费；内核不解释其内容


def line_overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """行窗重叠系数：交集行数 / min(|a|, |b|)（DECISIONS 2026-10-10，口径裁定）。

    选交集/min 而非 Jaccard 的理由：子窗完全含于大窗时系数=1.0——同一根因的
    窄证据窗不应因邻居窗更宽而被拒并；空窗或 start>end → 0.0。"""


def coarse_mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
    """粗聚类判据（§3.4 保守合并）：同 path ∧ 同 lang ∧ CWE 相等
    ∧ line_overlap ≥ CLUSTER_OVERLAP_MIN。
    CWE 语义（DECISIONS 2026-10-10）：双方非 None 须相等；双方 None 退化为
    (family, type) 相等（家族级 finding 的保守替身）；恰一侧 None → False（保守）。"""


def fine_mergeable(fa: FindingFacts, fb: FindingFacts, *, sim_fn: SimFn | None) -> bool:
    """精聚类判据 = coarse_mergeable ∧ sim_fn(fa, fb) ≥ CLUSTER_EMBED_SIM_MIN。
    sim_fn 为 None 时恒 False（缺省不并，§1.2 注入点纪律）。"""


@dataclass(frozen=True)
class ClusterProposal:
    """一次归并提案（apply_merges 的输入；纯数据，可审计可重放）。"""

    level: str                      # "coarse" | "fine"
    member_ids: tuple[str, ...]     # 锚点（最小 finding_id）在前，其余字典序
    key_path: str
    key_cwe: str | None
    key_lang: str
    line_window: tuple[int, int]    # 成员行窗的并包络 (min_start, max_end)
    sim_min: float | None           # 精聚类：成员对最小相似度；粗聚类 None


def cluster_findings(
    findings: Sequence[FindingFacts],
    *,
    level: str,                                     # "coarse" | "fine"
    sim_fn: SimFn | None = None,
    assigned: Mapping[str, str] | None = None,      # finding_id → 既有 cluster_id
) -> list[ClusterProposal]:
    """确定性两级归并提案（纯函数，零 IO 零 rng）。

    - 候选序：按 (path, line_start, line_end, finding_id) 字典序扫描（§6.5 确定性纪律）；
    - 合并判据：**all-pairwise**——新成员须与簇内**全部**成员两两可合并（DECISIONS
      2026-10-10：不做单链传递闭包，A~B~C 而 A≁C 时不并 C，防链式错并，§3.4 保守优先）；
    - `assigned` 中已归属的 finding 不参与新簇提案（扩簇场景由调用方把既有簇成员
      放进 assigned）；
    - 同一 finding 至多出现在一个提案中（先到先得，按候选序）。"""
```

### 1.3 建簇 / 扩簇 / 拆分（事件与写口）

```python
def apply_merges(
    ledger: KernelLedger,
    proposals: Sequence[ClusterProposal],
    *,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """建簇/扩簇 + cluster_merge 事件，**同一事务**（SPEC-1 §2 事务边界：
    clusters 合并与 cluster_merge 事件同事务）。

    - clusters / cluster_members 表写口在本模块（§6.1）；
    - id 生成：cluster_id = f"C-{date}-{seq:06d}"，date=注入时钟当日 YYYYMMDD，
      seq = 同日期前缀在账本库内已有最大序号 + 1（D20：自增域=账本库而非 run——
      引擎支持复用 db_path 追加写，多 run 同库同日期时按 run 内计数会撞 clusters.id
      主键；单 run 独库时与「run 内自增」逐位同值，M0 轨迹零影响。内核单写者串行
      保证唯一，SPEC-1 §6-1）；
    - 幂等键 = (level, frozenset(members))：已存在同 level 簇恰好包含全部 members
      → 零新事件直接跳过（§6.4）；
    - 返回按落库序的全部事件；无变化 → []。"""


def split_cluster(
    ledger: KernelLedger,
    cluster_id: str,
    groups: Sequence[Sequence[str]],      # ≥2 组，并集须 = 原成员全集
    *,
    actor: str,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """人工拆分（§3.4「错并可人工拆分」）：cluster_split 事件 + 成员表改写，同事务。

    - **拆分零存款**（§3.4 逐字）：已按簇传播的存款一律不回滚（§6.2 无回滚纪律，
      账本只追加）；拆分后各子簇 verdict 投影清空（verdict_* 置空列更新，
      属 clusters.verdict_* 内核写口）；
    - groups < 2 或并集 ≠ 原成员全集 → ValueError；
    - 幂等：簇不存在或已拆分（成员数 < 拆分前）→ ValueError（拆分是人工动作，
      不做静默跳过）。"""
```

### 1.4 簇级结论传播（§3.4 × §6.5 归属矩阵）

```python
@dataclass(frozen=True)
class MemberFacts:
    """簇成员的报告者事实（§6.5 系数按角色取）。"""

    finding_id: str
    reporter: str
    reporter_role: str             # "auditor"（×1.0）| "scout"（×SCOUT_X，fast 档 §16.7-1）


@dataclass(frozen=True)
class VerdictContext:
    """归属谱系（DECISIONS 2026-10-10：候选→侦察→finding 谱系由调用方显式装配，
    内核不改 SPEC-1 DDL、不自建谱系表）。"""

    members: tuple[MemberFacts, ...]
    scouts: tuple[str, ...] = ()               # 额外侦察行（非 reporter 的簇标记者）
    votes: tuple[tuple[str, int], ...] = ()    # (reviewer, direction) 评审回校（§6.5 评审行）
    verifier_id: str | None = None             # §6.5 验证行归属（S3 到达时需要）


def propagate_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    tier: SignalTier,                              # S1 | S2 | S3
    direction: int,                                # ±1（0 = 无方向，不落任何存款）
    source_seq: int,                               # 源事件 seq（幂等键，见下）
    source_kind: LedgerKind,                       # VERIFY_RESULT | HUMAN_VERDICT | VOTE | FINDING
                                                   #   （D23：词表增 FINDING——close_panel 的 S1 收口
                                                   #   源事件即 panel 收口 finding 事件；实现须校验
                                                   #   ledger[source_seq] 存在且 kind == source_kind）
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """簇 verdict → clusters.verdict_* 投影更新 + §6.5 归属展开 deposit 事件（同事务）。

    - 存款展开序（确定性，DECISIONS 2026-10-10；对齐 M0 simulator.deposits 行序）：
      成员报告者行（按 finding_id 字典序；reporter_role=scout 走 ×SCOUT_X，§16.7-1）
      → 侦察行（scouts 字典序，×SCOUT_X）→ 评审回校行（votes 字典序，与 verdict 同向
      + / 反向 −，×REVIEWER_X）→ 验证行（仅 tier=S3 且簇存在先验 S2 verdict 时：
      S3 同向 +VERIFIER_CONFIRM / 反向 −VERIFIER_CONFLICT；ctx.verifier_id 为 None
      且先验存在 → ValueError）；
    - 每笔派生存款 payload 携带 `src_seq = source_seq`（§5.2 新字段；崩溃修复与
      重放幂等的唯一判据，§6.3）；
    - verify_error / direction == 0 → 零存款零投影更新（§6.2/§6.3：不产生任何进化压力）；
    - **冲突语义**（§6.2 逐字）：S3 与 S2/S1 结论冲突时不回滚旧存款，按 S3 方向补记
      更强反向存款（本函数对同一簇可被 S1/S2/S3 先后调用各一次，净额自然收敛）；
    - 源事件一致性（D23）：ledger[source_seq] 不存在或 kind ≠ source_kind → ValueError
      （审计校验；source_kind 仅作派生审计用途，payload 不承载、不新增 LedgerKind）；
    - 幂等键 = (cluster_id, tier, source_seq)：同键重复调用 → 零新事件（§6.4）。"""


def propagate_pending(
    ledger: KernelLedger,
    *,
    lineage: Callable[[str], VerdictContext | None],   # cluster_id → 谱系（harness 提供）
    from_seq: int = 0,
    upto_seq: int | None = None,
    params: ParamsConfig | None = None,
) -> list[LedgerEvent]:
    """批处理修复口（§6.3 可重放不变量的运行时配套）：扫描窗口内全部
    verify_result / human_verdict / vote（S1 收口）事件，凡「应派生而未派生」的
    存款（按 (cluster, tier, src_seq) 幂等键比对）逐个补齐。崩溃窗口=源事件已落、
    派生存款未落——本函数把窗口收敛到零。lineage 返回 None（谱系缺失，如 M0 世界
    侧未接簇）→ 跳过该簇并保留在返回值的 skipped 列表，不报错。"""


def apply_human_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    direction: int,                    # +1 真报 | -1 误报 | 0 忽略
    actor: str,
    source: str = "panel",
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """§12 S3 入口（dashboard 复审面板 → 内核）：human_verdict 事件 + 簇内全员
    finding 状态流转 + S3 存款按簇传播（±1.0），同事务。

    - direction=+1 → 全员 transition_finding(CONFIRMED)；−1 → REJECTED；
      **0 = 忽略：落事件、零存款、零状态变更**（§12 逐字「忽略 ≠ 误报，零存款，
      歧义信号不入账」——事件落账仅为审计轨迹）；
    - 状态流转复用 §2.3 迁移表（终态 finding 再次收到 verdict → ValueError，
      防重复裁决；D21：终态守卫先于 direction 分支——direction=0 对终态簇同样
      ValueError，单事务回滚零落库，不存在「忽略态半落事件」）；
    - S3 存款经 propagate_verdict(tier=S3)（验证行随之 §6.5）。"""
```

### 1.5 S1 评审聚合（§10.3）

```python
@dataclass(frozen=True)
class VoteFacts:
    """一笔评审票的事实面（votes 表行同构）。"""

    reviewer: str
    direction: int
    dim_completeness: float
    dim_reachability: float
    dim_exploitability: float
    dim_fp_suspicion: float


@dataclass(frozen=True)
class VoteConsensus:
    finding_id: str
    direction: int          # −1|0|+1：票向符号和的符号；0 = 平票
    confidence: float       # 多数方向票的四维均值之平均（区间 [0,1]；无多数票 → 0.5）
    disagreement: bool      # 存在反向票 → True（DECISIONS 2026-10-10-D22，见 §1.6）
    n_votes: int


def aggregate_votes(finding_id: str, votes: Sequence[VoteFacts]) -> VoteConsensus:
    """§10.3 共识规则（纯函数，确定性）：方向 = 多数；平票（符号和为 0）→ direction=0。
    分歧是资产（§10.3）：存在反向票即记 disagreement 并显式上报，不强制仲裁。"""


def close_panel(
    ledger: KernelLedger,
    finding_id: str,
    *,
    ctx: VerdictContext,                 # votes 取自 ctx.votes
    expected_votes: int | None = None,   # 缺省 = params.panel.SIZE
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """panel 收口（投票后、精聚类/S2 前的必经步，§2.1 流水线序）：

    - findings.confidence = consensus.confidence、disagreement_json 写入（内核写口）；
    - finding 事件 open→voted（§2.3 迁移表）；
    - consensus.direction ≠ 0 → propagate_verdict(tier=S1, source_seq=本收口 finding
      事件的 seq)，S1 存款 = 源权重 S1_SOURCE_W × 角色系数（§6.5 全行展开）；
      direction == 0 → 只落状态与分歧，零存款；
    - 幂等：finding 已 voted → 零新事件；票数不足 expected_votes → ValueError。"""
```

### 1.6 本节决策记录（滚动协议 1；已同步 DECISIONS.md，见 §10）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 重叠口径 = 交集行数 / min(两窗行数) | 子窗含于大窗应判可并；Jaccard 会拒并窄证据窗 |
| D2 | all-pairwise 保守合并，不做单链传递闭包 | §3.4「宁可漏并不可错并」对链式错并的直接落地 |
| D3 | CWE 双 None → 退 (family,type) 相等；单 None → 不可并 | 家族级 finding 的保守替身；半边缺失并簇风险不可控 |
| D4 | sim_fn 注入缺省 None = 精聚类不并 | M1 内核零模型依赖；缺省行为=保守漏并（§3.4 优先序） |
| D5 | cluster_merge.members = 本次并入集（建簇=全部初始成员；扩簇=新增成员） | 全量成员以 cluster_members 表为真源，事件记增量可审计 |
| D6 | 派生存款增 src_seq 字段做幂等键 | 可重放不变量要求崩溃窗口可修复（§6.3） |
| D7 | 结论传播展开序固定（成员→侦察→评审→验证，各自字典序） | 确定性纪律 §6.5；跨运行逐位可比 |
| D20 | finding_id/cluster_id 自增域 = 同日期前缀账本库内 max+1（§1.3/§2.5 措辞同步修订） | 引擎支持复用 db_path 追加写（多 run 同库同日期）；id 是 TEXT PRIMARY KEY，run 内计数会撞主键；单 run 独库时逐位同值 |
| D21 | apply_human_verdict 终态守卫先于 direction 分支（0=忽略对终态簇同样 ValueError、事务回滚零落库） | 守卫无条件优先才防重复裁决；否则终态簇可被忽略态反复刷审计事件 |
| D22 | aggregate_votes 分歧语义定稿补记：反向票 → disagreement=True 显式上报不强制仲裁；修正 §1.5 错引「见 §10-8」 | §10.3「分歧是资产」逐字；该决策此前声称已同步 DECISIONS.md 但无对应行，本次补齐 |
| D23 | propagate_verdict source_kind 词表增 FINDING + 源事件 kind 一致性校验（ledger[source_seq].kind ≠ source_kind → ValueError） | close_panel 的 S1 源事件客观上是 finding 事件，原词表与之错位；校验让幂等键的 src_seq 可信 |

---

## 2. 委托契约与派单状态机（`kernel/dispatch.py`；设计 §4.2/§4.3/§10.2）

### 2.1 机制语义

- **蜂后 = 确定性内核 + LLM 顾问**（§4.2）：一切写态动作在内核执行；LLM 产出一律是建议工件，落地必须过内核 schema 校验，**LLM 对全局状态零写权**；
- **委托契约四件套**（§4.3）：每次派单必含 ①目标；②输出格式（schema 校验，不合格直接退回）；③工具与信息源边界；④任务边界。工蜂产出**一律落盘为工件、只回传引用**；
- **三条硬纪律**（§4.4）：写态收归内核串行；评审蜂不得为报告者本人（纪律 2，库级表达 = votes UNIQUE(finding_id, reviewer) + 内核提交守卫）；单蜂不产生确定性结论（L3 必经 S2）。

### 2.2 委托契约装配

```python
#: ② 输出格式：role → SPEC-1 §4.2 输出契约名（queen_advisor 不派产 finding 任务，
#: §16.7-9 只产结构化仲裁摘要建议稿，落账仍走内核）。
ROLE_OUTPUT_SCHEMA: Mapping[str, str] = {
    "scout": "ScoutReport",
    "auditor": "AuditorFinding",
    "reviewer": "ReviewerVote",
    "verifier": "VerifierReport",
}

#: ③ 工具与信息源边界：§4.1 权限边界列的机器可读形态（只读仓 / repo map 只读 /
#: finding 与工件只读 / 沙箱可写+仓只读）。verifier 的 sandbox:rw 仅指隔离沙箱，
#: 永不触生产（§4.1）。
ROLE_TOOL_BOUNDS: Mapping[str, tuple[str, ...]] = {
    "scout": ("repo:ro",),
    "auditor": ("repo:ro", "repomap:ro"),
    "reviewer": ("finding:ro", "artifact:ro"),
    "verifier": ("repo:ro", "sandbox:rw"),
}

#: ④ 任务边界缺省模板（一句话；SPEC-3 提示词模板细化时引用同一文案源）。
ROLE_TASK_BOUNDS: Mapping[str, str] = {
    "scout": "只分诊给定候选点并给出类型猜测；不做深挖、不产 finding。",
    "auditor": "只分析派单节点指名的可疑点×类型并产带证据链 finding；不扩面、不改结论分级。",
    "reviewer": "只按四维独立打票；不复用报告者结论、不产新 finding。",
    "verifier": "只对指名簇做沙箱复现并产三分类结构化结果；不投票、不产 finding。",
}

#: 深挖跳数（§2.2 档位表逐字：fast=0 / pr=1 / full=2；§2.1 repo map 按
#: 「候选点 ± N 跳邻域子图」注入 L2 上下文）。
HOP_BUDGET_BY_MODE: Mapping[str, int] = {"fast": 0, "pr": 1, "full": 2}


def hop_budget_for(mode: RunMode | str) -> int:
    """档位 → 深挖跳数（§2.2 逐字表）；未知档位 → ValueError。"""


def build_contract(
    task,
    decision: RouteDecision,
    *,
    mode: RunMode | str,
    deadline_s: int,
    node: str | None = None,            # 缺省 = decision.node
    context_refs: Sequence[str] = (),   # artifact:// 或 repo 路径+行窗
    goal: str | None = None,            # 缺省 = f"对 {node} 以 {role} 职责完成本次分片"
    task_bounds: str | None = None,     # 缺省 = ROLE_TASK_BOUNDS[role]
) -> DispatchContract:
    """四件套装配（§4.3；SPEC-1 §4.1 DispatchContract 直出）。纯函数零 IO：
    ①goal ②output_schema_ref=ROLE_OUTPUT_SCHEMA[role] ③tool_bounds=ROLE_TOOL_BOUNDS[role]
    ④task_bounds；hop_budget=hop_budget_for(mode)；taxonomy_node=node。"""
```

### 2.3 任务派单状态机（tasks.state：queued → dispatched → done|failed）

```
queued ──dispatch()──→ dispatched ──submit() 全量代落账成功──→ done
                          │           │
                          │           └─超时/schema 不合格─→ re_dispatch(attempts<2) ──→ 重派
                          │                         └─attempts≥2─→ failed
任意非终态 ──expire_run()（预算熔断/超时）──→ failed（任务侧；finding 侧=expired）
```

```python
#: 单任务派单次数上限 = 初派 1 + 重试 1（§16.7-8 逐字「timeout → 重试 1 次 → 改派
#: 他人」；模块常量不进 params，extra=forbid 纪律）。
DISPATCH_MAX_ATTEMPTS: int = 2


def dispatch(
    ledger: KernelLedger,
    task_id: str,
    contract: DispatchContract,
    decision: RouteDecision,
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """queued → dispatched：tasks 行（assignee / state='dispatched' / dispatched_at /
    attempts=1）+ DISPATCH 事件，同事务。

    - tasks.state ≠ 'queued' → ValueError（重复派单被状态守卫拒绝，§6.4 幂等）；
    - decision.assignee is None（empty_pool）→ 仍落 DISPATCH 事件（assignee=null，
      审计痕迹），tasks **留 queued**、attempts 不消耗；改派由调用方重入；"""


def re_dispatch(
    ledger: KernelLedger,
    task_id: str,
    *,
    to_agent: str,
    reason: Literal["timeout", "schema_fail"],     # = ReDispatchPayload.reason 词表
    run_id: str | None = None,
) -> LedgerEvent:
    """重派（§16.7-8）：tasks.attempts < DISPATCH_MAX_ATTEMPTS → tasks 重派
    （assignee=to_agent、attempts+1、state='dispatched'）+ RE_DISPATCH 事件，同事务；
    attempts 超限 → tasks.state='failed' 并抛 ValueError（重试通道关闭，零存款）。"""


def expire_run(
    ledger: KernelLedger,
    run_id: str,
    *,
    reason: str = "budget_breaker",
    params: ParamsConfig | None = None,
) -> list[LedgerEvent]:
    """run 收尾过期（§2.4「未跑项标记 expired（零存款）」× §10.2 任意状态→expired）：

    - 该 run 下全部非终态 finding（state ∈ {open, voted, inconclusive, verified}；
      D19：「非终态」= 迁移表出边非空，与 §2.4 迁移表 VERIFIED→EXPIRED 对齐）逐个
      transition_finding(EXPIRED)——**零存款**（本函数路径不产任何 deposit）；
    - 对应 tasks 行 state ∈ {queued, dispatched} → 'failed'；
    - 幂等：全部已终态 → []。"""
```

### 2.4 Finding 结论状态机（§10.2）

```python
#: 迁移表（§10.2 图逐字 + 一条 S3 直达扩展，DECISIONS 2026-10-10-D8：
#: open→confirmed/rejected 由 S3 直答触发——§12 入口语义「标记一个 finding 真假
#: 10 秒内可完成」允许人对未投票 finding 直答；除该扩展外不新增任何边）。
FINDING_TRANSITIONS: Mapping[FindingState, frozenset[FindingState]] = {
    OPEN:         frozenset({VOTED, CONFIRMED, REJECTED, EXPIRED}),
    VOTED:        frozenset({VERIFIED, INCONCLUSIVE, CONFIRMED, REJECTED, EXPIRED}),
    INCONCLUSIVE: frozenset({CONFIRMED, REJECTED, EXPIRED}),
    VERIFIED:     frozenset({CONFIRMED, REJECTED, EXPIRED}),   # S3 推翻/确认（§6.2）
    CONFIRMED:    frozenset(),                                  # 终态
    REJECTED:    frozenset(),                                  # 终态
    EXPIRED:     frozenset(),                                  # 终态
}


def transition_finding(
    ledger: KernelLedger,
    finding_id: str,
    to_state: FindingState,
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """结论状态机流转：迁移表守卫（非法迁移 / 终态再流转 → ValueError）；
    findings.state 列更新 + kind=finding 事件（payload.state=to_state）同事务。

    - 状态事件载体裁定（DECISIONS 2026-10-10-D3）：流转落 kind=finding 事件，
      payload.state 承载新状态——不新增 LedgerKind（枚举词表被 tests/test_enums.py
      ast 钉死）；重放规则 = 取每 finding 最后一条 finding 事件的 state（§6.3）；
    - **expired 零存款**：本函数从不产 deposit（§10.2 簧语义：不产生「没验证=负面」
      偏置）；零存款由测试断言（§9）。"""
```

**S2 结果 → 状态/结论映射**（submit(verifier) 内部执行，§6.3/§10.4/§10.2）：

| VerifyOutcome | 簇内全员 finding 状态 | clusters.verdict_* 投影 | 存款 |
|---|---|---|---|
| `verified_real` | `verified`（grade→L3，§10.1） | tier=S2, dir=+1 | §6.5 全行展开（S2 源权重 0.5） |
| `verified_fp` | `rejected` | tier=S2, dir=−1 | 同上；**缺 blocking_evidence 先强制改判 verify_error**（§10.4） |
| `verify_error` | `inconclusive`（§6.2「复现失败 ≠ 非漏洞，只降级」） | 无投影更新 | **零存款**；error_kind 必填（§6.3） |

### 2.5 蜂产出代落账 submit（§4.5 纪律 1：内核是唯一写手）

```python
class SchemaRejected(ValueError):
    """② 输出格式不合格（§4.3「schema 校验，不合格直接退回」）：零落库，
    调用方据 reason 走 re_dispatch(schema_fail) 或 failed。"""


def submit(
    ledger: KernelLedger,
    task_id: str,
    output: ScoutReport | AuditorFinding | ReviewerVote | VerifierReport,
    *,
    contract: DispatchContract,
    ctx: VerdictContext | None = None,       # auditor/verifier/reviewer 收口所需谱系
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """代落账：pydantic 校验 → 表行 + 账本事件 + 状态机流转，**单事务**（§7.1）。
    按 output 类型分发：

    - **ScoutReport**：candidates.triaged=1、guess_family/guess_type 回填（candidates
      表写口在本模块）；injection_flag=True 的候选点按 §11.6-3 保持候选态并标高优先
      （不做任何丢弃）；
    - **AuditorFinding**：findings + evidence_nodes 行落库（finding_id = 内核签发
      f"F-{date}-{seq:06d}"，seq = 同前缀库内已有最大序号 + 1，D20 同 §1.3）+
      finding 事件(state=open)；证据链校验
      （≥3 节点且含 source/sink、sanitizer 必出现，SPEC-1 §4.2 注记）不合格 →
      SchemaRejected；产出 (family,type) 与 contract.taxonomy_node 错配 → 自动
      re_route（§3.1，含猜测格弱存款）；
    - **ReviewerVote**：纪律 2 守卫（reviewer == 该 finding.reporter → ValueError）；
      votes 行 + vote 事件；票数达 params.panel.SIZE → 自动 close_panel（§1.5）；
    - **VerifierReport**：§10.4 改判守卫（verified_fp 缺 blocking → 强制改判
      verify_error，error_kind=poc_fail）→ verify_results 行 + verify_result 事件 +
      S2 状态映射（§2.4 表）+ propagate_verdict(S2)（§1.4，同事务）。

    task 非 dispatched 态 → ValueError。返回按落库序的全部事件。"""
```

### 2.6 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D8 | 迁移表增 open→confirmed/rejected（S3 直达）边 | §12 入口语义；除此之外迁移表逐字照 §10.2 图 |
| D9 | 状态流转以 kind=finding 事件承载（不新增 LedgerKind） | §3.2 信封+按 kind 分载荷；枚举词表被测试钉死 |
| D10 | 空池派单落 assignee=null 的 dispatch 事件且 tasks 留 queued | 派单失败需可审计；attempts 只计真派发 |
| D11 | verified_fp → rejected（而非 verified 态变体） | §6.2 verified_fp 是确定性负结论；与 S3 否定同状态出口 |
| D19 | expire_run 非终态覆盖集 = {open, voted, inconclusive, verified}（修正 §2.3 原枚举漏 verified） | 迁移表 VERIFIED→EXPIRED 出边非空；漏收尾会留下永悬 verified 行，破坏 run 收尾无非终态 |

---

## 3. re_route 与侦察蜂猜测格弱存款（`kernel/reroute.py`；设计 §6.5 附带）

### 3.1 机制语义与触发规则

- **侦察蜂的初步类型猜测**作为 L2 路由的 taxonomy 初值（§2.1 L1 初筛）；
- **路由初值随 guesses 口碑加权**（§6.5 附带）：猜测格 = `(scout, role=scout, node=猜测路径)`，其口碑（C*）参与猜测聚合；
- **re_route 事件**（审查蜂声明类型错配）→ 对猜测格记 **−0.05 弱存款**（§6.5 附带逐字）；
- **触发裁定**（DECISIONS 2026-10-10-D12）：内核**确定性比对**——产出 finding 的
  `(family, type)` 与派单契约 `taxonomy_node` 不一致即触发，无需蜂显式声明字段：
  - 错配 = 家族段不同，**或**（家族相同 ∧ 双方都含类型段 ∧ 类型不同）；
  - 家族级契约 + 更细类型产出 = **细化不是错配**，不触发；
  - 每个 finding 至多触发一次（首错配生效；后续产出与已改路由节点一致即不再触发）。

### 3.2 函数签名与弱存款契约

```python
#: 猜测错配弱存款幅值（§6.5 附带逐字 -0.05；模块常量不进 params）。
RE_ROUTE_WEAK_W: float = 0.05


def is_mismatch(from_node: str, to_node: str) -> bool:
    """§3.1 触发裁定的纯函数实现（家族段不同，或同族双类型段且类型不同）。"""


def re_route_finding(
    ledger: KernelLedger,
    finding_id: str,
    *,
    from_node: str,                        # 派单契约 taxonomy_node
    to_node: str,                          # 产出 finding 的 (family[/type])
    scout_ids: Sequence[str] = (),         # 猜测来源侦察蜂（弱存款归属；空 = L0 直出无猜测者）
    reason: str = "type_mismatch",
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """re_route 事件 + 猜测格弱存款，同事务：

    - 事件：kind=re_route，payload {finding_id, from_node, to_node, reason}
      （ReRoutePayload 逐字，SPEC-1 §4.3）；
    - 弱存款：每个 scout_id 一笔 DepositPayload ——
      `agent=scout_id, role=scout, taxonomy_path=from_node 路径段（猜测即被证伪的
      路由初值）, signal_tier=S1, direction=-1, weight=0.05, origin=prod,
      src_seq=<re_route 事件 seq>`；
      记 S1 档的理由（DECISIONS 2026-10-10-D13）：弱存款是生产面最低档信号，且 S1
      不计晋升硬门槛 n_eff_s2s3（§6.1/§7.3）——「弱」字的结构化表达；
    - 幂等：同 finding 已有 re_route 事件 → 零新事件（§6.4）。"""


def guess_taxonomy(
    reports: Sequence[ScoutReport],
    cells: CellView,
) -> tuple[str, ...] | None:
    """L1 猜测聚合 → L2 路由 taxonomy 初值（§2.1/§6.5「路由初值随 guesses 口碑加权」）。

    - 候选 = reports 中 (guess_family, guess_type) 非空的 distinct 猜测路径集合；
    - 得分序（字典序降序取首）：票数 → 猜测格 C* 之和（Σ cells.c_star(scout,
      "scout", 猜测node)，无格子按 ROOT_PRIOR 计）→ 猜测路径字典序升序；
    - 无任何猜测 → None（调用方退家族级路由）。纯函数零 rng。"""
```

### 3.3 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D12 | 错配由内核确定性比对触发；细化（家族级→类型级）不算错配 | §6.5 附带「审查蜂声明类型错配」的机制化；细化是正常流程 |
| D13 | 弱存款记 signal_tier=S1 / weight=0.05 / direction=−1 / 猜测路径 | 「弱」= 低档 + 不进 n_eff_s2s3 硬门槛；幅值逐字 §6.5 |
| D14 | 谱系（candidate→scout→finding）由调用方显式接参（scout_ids / VerdictContext），不改 SPEC-1 DDL | findings/candidates 表无谱系列；M1 不为内核便利扩散 DDL 变更，harness 可从 dispatch 事件流装配 |

---

## 4. 配额与预算熔断（`kernel/budget.py`；设计 §2.4/§2.2）

### 4.1 机制语义（§2.4 逐条）

- **L2 配额（full 档）**：`quota_L2 = min(45, budget_remaining / cost_per_L2)`；
- **S2 配额**：`quota_S2 = quota_L2 存活数 × budget_remaining / cost_per_S2`，按（severity 初判 × 类型稀缺度 × 置信边际）降序取；
- **预算熔断器**：单 run 预算耗尽 → 档位自动降级（full 行为退化为 pr 语义），未跑项标记 `expired`（零存款，不产生「没验证=负面」的偏置）；
- **Batch 开关**：full 档默认走 Batch API（50% 折扣）；fast/pr 延迟敏感禁用——供给层职责（models.yaml `runtime.batch_mode_for_full`，SPEC-1 §5.1），内核只在 `BudgetPolicy.batch_eligible` 暴露档位判定；
- **三档预算**：run 建立时按入口档取 `budget_for_mode`（§2.2 成本锚点 → §8 新键）。

### 4.2 数据结构与函数签名

```python
SEVERITY_RANK: Mapping[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class BudgetPolicy:
    """一次 run 的预算约束载体（harness 装配；注入None = 无预算约束路径，
    M0 仿真轨迹逐字兼容——见 §8 旋钮表「缺省时行为」列）。"""

    mode: str                          # 入口档 "fast"|"pr"|"full"
    budget_cents: int                  # run 总预算（= runs.budget_cents）
    cost_per_l2_cents: int             # L2 单点成本估计（§8 新键）
    cost_per_s2_cents: int             # S2 单次成本估计（§8 新键）
    l2_cap: int                        # = params.budget.L2_QUOTA_CAP（§2.4 的 45）

    @property
    def batch_eligible(self) -> bool:
        """§2.4 Batch 开关的档位判定（full 独占；实际 Batch 调用归供给层）。"""


@dataclass(frozen=True)
class BudgetState:
    mode: str
    spent_cents: int
    remaining_cents: int
    quota_l2: int
    quota_s2: int
    degraded: bool                     # True = 熔断已触发
    tier: str                          # 降级后的生效语义档（"full"→"pr"→"fast"）


def budget_for_mode(mode: RunMode | str, params: ParamsConfig) -> int:
    """入口档 → run 预算（美分）：fast→budget.FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS。"""


def quota_l2(remaining_cents: int, cost_per_l2_cents: int, *, cap: int) -> int:
    """§2.4 逐字：min(cap, ⌊remaining / cost⌋)；cost ≤ 0 或 remaining ≤ 0 → 0
    （未配置成本模型不派单，保守）。"""


def quota_s2(l2_survivors: int, remaining_cents: int, cost_per_s2_cents: int) -> int:
    """§2.4 逐字：⌊存活数 × remaining / cost⌋；cost ≤ 0 → 0。"""


def s2_rank_key(
    severity: str,
    type_node: str | None,
    confidence: float,
    *,
    run_cluster_counts: Mapping[str, int],   # 本 run 已出现的类型节点 → 簇数
) -> tuple[int, float, float]:
    """§2.4 S2 降序取的排序键（DECISIONS 2026-10-10-D15）：
    (SEVERITY_RANK[severity], scarcity, confidence) 三元组字典序降序。
    scarcity = 1 / (1 + run_cluster_counts.get(type_node, 0))——本 run 内越稀缺的
    类型越优先；不取乘积（零置信会把乘积归零吞掉 severity 主序）。
    type_node 为 None（家族级）按空串计（scarcity 最高，与「类型稀缺度」的稀疏优先一致）。"""


def budget_state(spent_cents: int, policy: BudgetPolicy, *, params: ParamsConfig | None = None) -> BudgetState:
    """熔断判定（纯函数）：spent ≥ budget → degraded=True（含等号，保守闭包，
    DECISIONS 2026-10-10-D16），tier 沿 full→pr→fast 阶梯落一档；quota 按剩余预算
    重算。未触发 → tier=policy.mode。"""


def on_cost(
    ledger: KernelLedger,
    run_id: str,
    cents: int,
    *,
    policy: BudgetPolicy,
    note: str = "",
) -> BudgetState:
    """成本入账（runs.spent_cents += cents，runs 表写口在本模块，§6.1）并返回
    trip 后 BudgetState。**首次** trip：SCALING 事件（pool=f"mode:{policy.mode}",
    delta=-1, reason="budget_breaker"）+ runs.status='degraded'，与 spent 更新同事务
    （DECISIONS 2026-10-10-D17：不新增 LedgerKind，降级以既有 scaling 事件承载可审计
    轨迹，§11-3「一切伸缩可追溯」）；重复 trip 零新事件（幂等）。"""
```

### 4.3 熔断触达后的行为契约（降级语义的精确化）

| 侧 | 触发 spent ≥ budget 后的行为 | 出处 |
|---|---|---|
| 新派单 | quota 公式在 remaining ≤ 0 下恒 0 → 不再派任何付费任务（L2/S2） | §2.4 公式直译 |
| 在途任务 | 不撤回，走 §2.3 状态机自然完成或失败 | 内核无取消语义（保守） |
| 未跑项 | `expire_run(run_id)`：未终态 finding → expired（零存款）、未完任务 → failed | §2.4/§10.2 |
| 语义档 | full→pr（pr→fast，fast 到底）：新判定按低档参数（hop_budget 取低档值） | §2.4「档位自动降级」 |
| 可观测 | SCALING 事件 + runs.status='degraded'（RunStatus.DEGRADED） | §11-3 / SPEC-1 §1 |

### 4.4 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D15 | S2 排序键 = (severity, scarcity, confidence) 字典序降序；scarcity = 1/(1+本 run 类型簇数) | §2.4 乘积表述在零值处失真；字典序保留 severity 主序且确定 |
| D16 | 熔断判据 spent ≥ budget（含等号） | 保守闭包；> 会放行恰好压线的第 N+1 次付费调用 |
| D17 | 降级落 SCALING 事件 + runs.status=degraded，不新增 LedgerKind | §11-3 可追溯；枚举词表冻结（test_enums ast 钉 SPEC-1） |
| D18 | BudgetPolicy 注入 None = 无预算约束路径 | M0 仿真轨迹逐字兼容（缺省关闭=规格逐字纪律的预算侧形态；§2.4 熔断本身无开关，生产 harness 必配 policy） |

---

## 5. 账本事件与 payload 契约总表（LedgerKind ×15）

payload 字段与 pydantic 模型以 **SPEC-1 §4.3 为唯一真源**（PAYLOAD_SCHEMA 按 kind 注册，append 前强制校验）；本表补充 M1 视角：产出函数 / 触发时机 / 事务边界 / 幂等键。M0 已有 kind 标注「M0」。

| kind | 产出函数（M1） | 触发时机 | 事务边界 | 幂等键 |
|---|---|---|---|---|
| `dispatch` | dispatch() | queued→dispatched | tasks 行+事件同事务 | tasks.state 守卫（非 queued 拒绝） |
| `finding` | submit(auditor) / transition_finding / close_panel | 建档 / 结论流转 | 表行+事件同事务 | §2.4 迁移表守卫 |
| `vote` | submit(reviewer) | 评审票代落账 | votes 行+事件同事务 | votes UNIQUE(finding_id, reviewer)（库级，SPEC-1 §2） |
| `verify_result` | submit(verifier) | S2 代落账 | verify_results 行+事件+状态映射+S2 存款同事务 | §10.4 改判守卫 + propagate 幂等键 |
| `human_verdict` | apply_human_verdict | S3 面板直答 | 事件+状态流转+S3 存款同事务 | 终态守卫 + propagate 幂等键 |
| `exam_result` | 考试 runner（M0） | 入学/暗考/周期考 | 单事件 | exam_id（M0 现状） |
| `promotion` | promote_scan（M0） | 孵化流转（**承载考试通道全部流转**，to_status 如实记录） | 事件与 cells 重放投影 | 转换前件守卫（M0 幂等性） |
| `demotion` | promote_scan（M0） | 机制性熄灭 decay/dormant/eliminate | 同上 | 同上 |
| `scaling` | budget.on_cost（M1 复用）/ 扩缩容触发器 | 预算降级 / 编制伸缩 | spent/status+事件同事务 | trip 位（首次才落） |
| `rollback` | 快照回滚（M2+，§11 闸2） | 口碑快照指针切换 | 单事件 | snap id |
| `cluster_merge` | apply_merges | 建簇/扩簇 | clusters/cluster_members+事件同事务 | (level, frozenset(members)) |
| `cluster_split` | split_cluster | 人工拆分（零存款） | 成员表+verdict 清空+事件同事务 | 拆分前件守卫 |
| `re_route` | re_route_finding / submit 自动错配 | 类型错配 | 事件+弱存款同事务 | 每 finding 至多一次 |
| `re_dispatch` | re_dispatch | timeout/schema_fail 重派 | tasks 行+事件同事务 | attempts 守卫 |
| `deposit` | propagate_verdict / re_route_finding / M0 世界侧 | 存款（唯一驱动进化的 kind） | 与源状态变更同事务（§4.5 纪律 1） | src_seq 派生键（M1 派生行；M0 世界侧行维持现状） |

### 5.1 payload 字段契约增量

除下述一条外，全部 payload 模型**逐字沿用 SPEC-1 §4.3**，不新增不删改：

```python
class DepositPayload(BaseModel):               # kind=deposit（§3.2/§6.5 归属矩阵）
    finding: str | None
    cluster: str | None
    agent: str
    role: Role
    taxonomy_path: list[str]
    signal_tier: SignalTier
    direction: int = Field(ge=-1, le=1)
    weight: float
    origin: DepositOrigin = DepositOrigin.PROD
    src_seq: int | None = None                 # ← M1 新增可选字段（DECISIONS 2026-10-10-D6）：
                                               #   派生存款的源事件 seq（§5 表幂等键）。
                                               #   M0 历史事件无此字段（=None）；聚合器
                                               #   （kernel.cells._fold）不读该字段，
                                               #   M0 轨迹与重放逐位不受影响。
```

`ExamDepositPayload(DepositPayload)` 继承即自动携带 `src_seq`；`PAYLOAD_SCHEMA` 注册表不变。

### 5.2 派生存款的标准形（§6.5 归属矩阵的内核展开）

| 行 | agent | role | signal_tier | direction | weight |
|---|---|---|---|---|---|
| 报告者（auditor） | member.reporter | auditor | verdict tier | verdict 方向 | 源权重 × 1.0 |
| 报告者（scout，fast 档 §16.7-1） | member.reporter | scout | verdict tier | verdict 方向 | 源权重 × SCOUT_X |
| 侦察行（§6.5） | ctx.scouts | scout | verdict tier | verdict 方向 | 源权重 × SCOUT_X |
| 评审回校行（§6.5） | ctx.votes reviewer | reviewer | verdict tier | 与 verdict 同向 + / 反向 − | 源权重 × REVIEWER_X |
| 验证行（§6.5，仅 S3） | ctx.verifier_id | verifier | S3 | 同向 +1 / 反向 −1 | VERIFIER_CONFIRM(0.25) / VERIFIER_CONFLICT(0.5) |
| 猜测弱存款（§3） | scout_id | scout | S1 | −1 | 0.05（RE_ROUTE_WEAK_W） |

---

## 6. 不变量（编码 AI 必须遵守；SPEC-1 §6 的 M1 扩展）

### 6.1 写口唯一（SPEC-1 §6-2 扩展）

**全局状态只有内核一个写者**（§4.5 纪律 1）。表 → 唯一写口模块：

| 表/对象 | 唯一写口 | 状态 |
|---|---|---|
| `ledger` | `kernel.ledger.KernelLedger.append` | M0 |
| `cells` | `kernel.cells._write_cells`（agg_cells 内部） | M0 |
| `findings` / `evidence_nodes` / `findings.state` | `kernel.dispatch`（submit / transition_finding / close_panel / expire_run） | **M1** |
| `votes` | `kernel.dispatch.submit`（reviewer 分支） | **M1** |
| `verify_results` | `kernel.dispatch.submit`（verifier 分支） | **M1** |
| `clusters` / `cluster_members` / `clusters.verdict_*` | `kernel.cluster`（apply_merges / split_cluster / propagate_verdict） | **M1** |
| `tasks` | `kernel.dispatch`（dispatch / re_dispatch / expire_run） | **M1** |
| `candidates` | `kernel.dispatch.submit`（scout 分支：triaged/guess 回填） | **M1** |
| `runs.spent_cents` / `runs.status` | `kernel.budget.on_cost` / run 生命周期入口 | **M1** |
| `exam_*` / `snapshots` / `artifacts` 索引 | 考试 runner / 快照入口（非本 SPEC 范围） | M0/后置 |

- 蜂与仿真器：只读，或经 `KernelLedger.append` 落事件（蜂**从不**直接写任何表）；
- **静态扫描扩展**：tests/test_kernel_ledger.py 的写语句正则从 `(ledger|cells)` 扩展到上表全部内核写表（exact 词表见 SPEC-5 滚动导出）；越界即红。

### 6.2 append-only

- `ledger` 由 schema.sql 触发器（`ledger_no_update`/`ledger_no_delete`，RAISE(ABORT)）强制 append-only——**内核 bug 也改不动账本**（DECISIONS 2026-09-29）；
- 一切校正都是新事件：S3 与 S2/S1 冲突补记反向存款不回滚（§6.2 设计逐字）；拆分不回滚已传播存款（§1.3）；降级是新增 SCALING 事件而非改写历史；
- `runs.status` / `tasks.state` / `findings.state` / `clusters.verdict_*` 是**当前态投影**（非 append-only 表），但其每次变更必有同事务账本事件（SPEC-1 §2「不存在无账本事件的状态变更」），投影可随时由账本重建（§6.3）。

### 6.3 可重放

- **三投影全量重建**（SPEC-1 §2 重放重建规则 + §6-6 重放等价）：
  - `cells` ← `deposit` + `promotion`/`demotion`（M0 `agg_cells`，不动）；
  - `clusters`/`cluster_members`/`verdict_*` ← `cluster_merge`/`cluster_split` + `verify_result`/`human_verdict`（M1 新增）；
  - `findings.state`/`grade`/`confidence`/`disagreement` ← `finding`/`vote`/`verify_result`/`human_verdict`（M1 新增；state 取每 finding **最后一条** finding 事件的 payload.state）；
- **单一实现纪律**（承 M0 replay「聚合唯一实现」）：重建只重放事件，不重跑合并/传播逻辑——`cluster_merge.members` 即成员真源、deposit 事件即存款真源，投影 = 事件折叠，无第二套业务规则；
- **入口**：`ledger.rebuild()` M1 起重建三投影（返回含三投影摘要的 dict；`rebuild_cells` 保留为 cells 单投影入口）；`kernel.replay` 新增 `rebuild_clusters(ledger, upto_seq=None, *, from_seq=0)` / `rebuild_findings(...)` 与逐位 diff `diff_clusters` / `diff_findings`（字段 `==` 全等比较，无容差——M0 diff_cells 同一纪律）；CLI `python -m qswarm.kernel.replay` 接口不变、摘要增投影列；
- **崩溃修复**：`propagate_pending`（§1.4）以 src_seq 幂等键把「源事件已落、派生存款未落」的窗口收敛到零；重放取事件折叠（无时钟、无副作用、确定性）。

### 6.4 幂等

同一账本状态重复执行任何内核操作 → 零新事件、零表变更：

| 操作 | 幂等来源 |
|---|---|
| apply_merges | (level, frozenset(members)) 已存在即跳过 |
| split_cluster / dispatch / re_dispatch | 状态守卫（前件不满足即 ValueError，非静默） |
| propagate_verdict | (cluster, tier, src_seq) 派生键已存在即跳过 |
| close_panel / transition_finding | 迁移表守卫（已 voted/终态拒绝或零事件） |
| re_route_finding | 每 finding 至多一次 |
| on_cost trip | 首次 trip 才落 SCALING 事件 |
| expire_run | 已终态跳过 |
| promote_scan | M0 转换前件守卫（不动） |

「静默跳过」与「ValueError」的界线：**读侧幂等**（重复触发同一业务事实）→ 跳过返回 []；**写侧非法**（状态机不允许的动作）→ ValueError。测试两侧都要钉（§9）。

### 6.5 确定性（注入 rng + 字典序候选集）

- M1 全部新函数**零随机性**：不接 rng 参数、不消费任何随机源；内核中唯一的随机消费点仍是 `route` / `route_thompson`（M0 既有，注入 `random.Random`）；
- 一切集合展开/排序用**字典序或钉死的规范序**：聚类候选 (path, line_start, line_end, finding_id)、簇成员 finding_id、侦察/评审行 agent 字典序、S2 排序键平手字典序、格子遍历 sorted(view.cells)；
- 时间由注入时钟签发（M0 三时钟不动）；`同 (种子, 输入事件序, params)` ⇒ 内核决策与事件序列**逐位一致**，跨 PYTHONHASHSEED 成立（禁内建 hash，承 simulator.rng 纪律）。

---

## 7. 读写并发纪律（§4.5 落地）

### 7.1 事务边界（单事务原子性）

```python
@contextmanager
def transaction(ledger: KernelLedger):
    """多写原子上下文（kernel.ledger M1 最小扩展）：

    - 以 BEGIN IMMEDIATE 显式开事务，正常退出 COMMIT、异常 ROLLBACK；
    - **append 语义修订**：conn.in_transaction 为真时 append() 不再逐条 commit，
      由本上下文边界统一提交——无上下文时行为与 M0 逐条提交完全一致（M0 零影响）；
    - 全部「表行 + 账本事件」复合操作（§5 表事务边界列）必须在本上下文内执行，
      保证 §4.5 纪律 1「不存在半写态」。
    """
```

§4.5 五种读写对象与存储映射的 M1 归位：任务契约（tasks 表 + 进程内队列，写者=内核 dispatch）；工件（内容寻址库，产出蜂私有区，1→N）；Finding/Cluster/Vote/Verify（SQLite 各表，内核代落账，大字段只存 artifact:// 引用）；账本（append-only，仅内核串行 N→1 fold）；口碑视图（cells 快照，1→∞）。**没有蜂间消息存储**——协同的全部状态就是这两层存储（§4.5 逐字）。

### 7.2 并发三纪律的内核表达

1. **单写者**：§6.1 写口表是穷尽枚举；蜂的「提交」= 写私有工件区 → 回传结构化载荷 → `submit()` 过 schema 校验代为落账；Finding 与其首条 deposit 同事务；
2. **三种读各司其职**：1→1（派单契约、一簇一沙箱）、1→N（一个 finding 喂 params.panel.SIZE 只跨模型族评审蜂，§16.7-6）、N→1（存款→浓度、票→共识、finding→簇）；
3. **时间/身份解耦**：写一次读无数次；蜂熄灭后历史存款仍影响路由（stigmergy 本体）——时钟注入 + 账本重放保证该性质与部署形态无关。

### 7.3 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D24 | transaction() 可重入（计数式嵌套）：已在事务中时内层不再 BEGIN/COMMIT，仅登记上下文，由最外层边界统一 COMMIT/ROLLBACK；内核复合函数（submit/apply_merges/propagate_verdict/on_cost 等）内部自开事务，嵌套调用时沿用外层 | BEGIN IMMEDIATE 事务中重入直接报错；复合函数与 harness 批处理两侧都需要原子性，禁嵌套会迫使内核暴露「裸/带事务」双形态；无上下文时 append 逐条提交与 M0 逐位一致（§7.1 语义不变） |

---

## 8. 新旋钮清单（缺省值 + 规格出处；缺省关闭=规格逐字）

> 纪律（承 M0 审计结论）：**凡新增旋钮，缺省值必须使行为 = 设计文档逐字口径**；扩展机制一律默认关闭。设计文档已定死的数值做成模块常量（不进 params.yaml，extra=forbid 纪律，同 route.SPEC_CAP 先例）；部署相关估计值才进 params.yaml。

### 8.1 params.yaml 新键（budget 节；实现子任务回填，SPEC-1 §5.2 budget 节扩展）

| 键 | 缺省 | 语义 | 出处 / 缺省时行为 |
|---|---|---|---|
| `budget.FAST_CENTS` | `2` | fast 档单 run 预算（美分） | §2.2 fast 成本锚点上界 $0.02；熔断为 §2.4 常设机制、无开关 |
| `budget.COST_PER_L2_CENTS` | `5` | L2 单点成本估计（配额估计用，非记账费率——费率以 on_cost 实测回报为准） | §2.4 公式 cost_per_L2 输入；§2.2 成本假设（L2 单点 12k in/2k out @强档 ≈$0.05）；为 0 → quota 恒 0（保守不派） |
| `budget.COST_PER_S2_CENTS` | `10` | S2 单次验证成本估计（同上） | §2.4 公式 cost_per_S2 输入；工程档调用+沙箱重试余量 ≈$0.10；为 0 → quota 恒 0 |

既有键（`PR_CENTS=100` / `FULL_CENTS=600` / `L2_QUOTA_CAP=45` / `RESCAN_WEEKLY=0.07`）缺省值不动。

### 8.2 模块常量（不进 params.yaml）

| 常量 | 落位 | 值 | 出处 / 缺省行为 |
|---|---|---|---|
| `CLUSTER_OVERLAP_MIN` | kernel.cluster | `0.5` | §3.4 逐字「行窗重叠 ≥50%」 |
| `CLUSTER_EMBED_SIM_MIN` | kernel.cluster | `0.9` | §3.4 逐字「向量相似度 ≥0.9」 |
| `S1_SOURCE_W` | kernel.cluster | `0.05` | §6.1 S1 权重区间 [0.05, 0.1] 下沿（世界侧实验旋钮不进内核，DECISIONS 2026-10-08 先例） |
| `S2_SOURCE_W` / `S3_SOURCE_W` | kernel.cluster | `0.5` / `1.0` | §6.1 规格常量（与 simulator.verdict 同值同源） |
| `RE_ROUTE_WEAK_W` | kernel.reroute | `0.05` | §6.5 附带逐字「−0.05 弱存款」 |
| `DISPATCH_MAX_ATTEMPTS` | kernel.dispatch | `2` | §16.7-8 逐字「重试 1 次」（初派+重试） |
| `HOP_BUDGET_BY_MODE` | kernel.dispatch | `{fast:0, pr:1, full:2}` | §2.2 档位表「深挖跳数」列逐字 |
| `ROLE_OUTPUT_SCHEMA` / `ROLE_TOOL_BOUNDS` / `ROLE_TASK_BOUNDS` | kernel.dispatch | 角色表 | §4.3 四件套 ②③④ 缺省；§4.1 权限边界；文案细化归 SPEC-3 |
| `FINDING_TRANSITIONS` | kernel.dispatch | §2.4 迁移表 | §10.2 状态机图逐字 + D8 一条 S3 直达边 |

### 8.3 注入点（非参数的装配面）

| 注入点 | 形态 | 缺省 | 缺省时行为 / 出处 |
|---|---|---|---|
| `sim_fn` | `SimFn \| None`（cluster_findings/fine_mergeable） | `None` | 精聚类不产生合并（保守漏并；§3.4 宁漏勿错）；仿真接通时注入确定性文本相似度（§0.4） |
| `BudgetPolicy` | budget 各函数可选注入 | `None` | 无预算约束路径 = M0 仿真轨迹逐字兼容；§2.4 熔断本身常设，生产 harness 必配（D18） |
| 谱系 `VerdictContext` / `lineage` / `scout_ids` | propagate/close_panel/submit 显式接参 | 必填 | 谱系解析归 harness（D14）；不改 SPEC-1 DDL |
| `embedder` 调用 | SimFn 内部自由实现 | — | 内核零模型依赖（§4.2 内核=纯代码确定性，LLM 只出建议工件） |

### 8.4 M0 既有旋钮（本 SPEC 全部不动）

`promote.*`（含 M_PRORATE/UNIQUE_PER_FAMILY/WINDOW_ADVANTAGE）、`tilt.*`、`shrink.LAMBDA`、`decay.*`、`demote.*`（含 FC_*）、`exam.*`（含 ENTRY_*/TYPE_ENTRY*）、`explore.UNIFORM_FLOOR`、`deposits.*`、`panel.*`、`roster.*`——缺省值与语义照 DECISIONS.md 既有定稿，M1 不回填不重释。

---

## 9. M1 门禁要点（SPEC-5 滚动导出的输入；每项机器可检）

1. **写口静态扫描扩展**：§6.1 表全词表越界即红；
2. **重放等价三投影**：随机事件前缀 rebuild vs 在线投影，`diff_cells`/`diff_clusters`/`diff_findings` 全空（逐位 ==）；
3. **幂等双跑**：同一账本连续两次 apply_merges / close_panel / propagate_verdict / re_route / on_cost / expire_run → 第二次零新事件；状态守卫类（dispatch 重复、终态流转、attempts 超限）→ ValueError；
4. **确定性**：同种子同输入序列两次运行事件流逐位一致；跨 PYTHONHASHSEED 一致；
5. **熔断状态机表驱动用例**：quota 公式边界（0 成本 / 0 余额 / 恰压线 ≥）、trip 一次性、降级阶梯、expire 零存款断言（expired 路径不产任何 deposit）；
6. **守卫负用例**：§10.4 verified_fp 缺 blocking 强制改判；纪律 2 reviewer==reporter 拒绝；§4.4 L3 必经 S2（单蜂 finding grade 不得为 L3）；
7. **仿真接通回归**：M1 引擎接真实聚类/预算后，M0 九度量与既有 golden 轨迹不回归（SPEC-4 §0 复用纪律的验收面）。

---

## 10. 决策记录索引（本 SPEC 拍板的未定细节；已同步 DECISIONS.md 2026-10-10 行）

| # | 一句话 | 详见 |
|---|---|---|
| D1 | 行窗重叠口径 = 交集/min | §1.2 |
| D2 | 聚类 all-pairwise，不做单链传递闭包 | §1.2 |
| D3 | CWE 双 None 退 (family,type)；单 None 不可并 | §1.2 |
| D4 | sim_fn 缺省 None = 精聚类不并 | §1.2/§8.3 |
| D5 | cluster_merge.members 记增量 | §1.6 |
| D6 | DepositPayload 增可选 src_seq | §5.1 |
| D7 | 结论传播展开序固定 | §1.4 |
| D8 | 迁移表增 S3 直达边（open→confirmed/rejected） | §2.4 |
| D9 | 状态流转以 kind=finding 事件承载 | §2.4 |
| D10 | 空池派单落 null assignee 事件、tasks 留 queued | §2.3 |
| D11 | verified_fp → rejected | §2.4 |
| D12 | re_route 确定性比对触发；细化不算错配 | §3.1 |
| D13 | 弱存款 = S1/−0.05/猜测路径 | §3.2 |
| D14 | 谱系显式接参，不改 DDL | §3.3 |
| D15 | S2 排序键三元组字典序 | §4.2 |
| D16 | 熔断判据 spent ≥ budget | §4.2 |
| D17 | 降级以 SCALING 事件承载 | §4.2 |
| D18 | BudgetPolicy 缺省 None = 无约束路径 | §4.4 |
| D19 | expire_run 非终态集含 verified（定稿修订） | §2.3/§2.6/§10.1 |
| D20 | finding_id/cluster_id 自增域 = 同前缀库内 max+1 | §1.3/§2.5/§10.1 |
| D21 | apply_human_verdict 终态守卫先于 direction 分支 | §1.4/§10.1 |
| D22 | aggregate_votes 分歧语义定稿补记（DECISIONS 缺行补齐） | §1.5/§10.1 |
| D23 | source_kind 词表增 FINDING + 源事件一致性校验 | §1.4/§10.1 |
| D24 | transaction() 可重入（计数式嵌套） | §7.3/§10.1 |

### 10.1 定稿拍板表（2026-10-10，规格门歧义拍板；五字段格式；已逐条同步 `docs/spec/DECISIONS.md`）

| 日期 | 模块 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| 2026-10-10 | SPEC-2/kernel | D19：expire_run 非终态覆盖集 = 迁移表全部非终态 {open, voted, inconclusive, verified}（修正 §2.3 原枚举遗漏 verified） | §2.4 迁移表 VERIFIED→EXPIRED 出边非空，verified 非终态；熔断/超时收尾若漏 verified 会留下永悬行，破坏「run 收尾无非终态」 | kernel/dispatch.expire_run；tests/test_kernel_dispatch.py test_expire_run_zero_deposit_terminal_idempotent |
| 2026-10-10 | SPEC-2/kernel | D20：finding_id/cluster_id 自增域 = 同日期前缀在账本库内已有最大序号 +1（§1.3/§2.5「run 内自增」措辞同步修订） | 引擎支持复用 db_path 追加写（多 run 同库同日期），findings.id/clusters.id 为 TEXT PRIMARY KEY，按 run 内计数第二 run 即撞主键 IntegrityError；单 run 独库时 max+1 与 run 内自增逐位同值，M0 轨迹（世界侧 F-SIM-<t> 自成前缀）零影响 | kernel/cluster.apply_merges；kernel/dispatch.submit；tests/test_kernel_cluster.py 跨 run 同库断言 |
| 2026-10-10 | SPEC-2/kernel | D21：apply_human_verdict 守卫次序 = 终态守卫先于 direction 分支；direction=0 对终态簇同样 ValueError，单事务回滚零落库 | 「终态再裁 ValueError」须无条件优先，否则终态簇可被忽略态反复刷审计事件、守卫形同虚设；事务语义下异常即整体回滚，无「半落事件」 | kernel/cluster.apply_human_verdict；tests/test_kernel_cluster.py test_apply_human_verdict_three_way_and_terminal_guard |
| 2026-10-10 | SPEC-2/kernel | D22：aggregate_votes 分歧语义定稿补记——direction=符号和符号（平票 0）、confidence=多数方向票四维均值之平均（无多数 0.5）、存在反向票 → disagreement=True 显式上报不强制仲裁；同步修正 §1.5 错引「见 §10-8」 | §10.3「分歧是资产」逐字；强制仲裁吞掉评审面信号，disagreement 供 queen_advisor 仲裁建议稿消费；该决策此前声称已同步 DECISIONS.md 但无对应行，本次补齐 | kernel/cluster.aggregate_votes；tests/test_kernel_cluster.py test_aggregate_votes_majority_tie_disagreement |
| 2026-10-10 | SPEC-2/kernel | D23：propagate_verdict source_kind 词表增 FINDING（S1 收口源事件 = panel 收口 finding 事件），实现增加源事件一致性校验（ledger[source_seq] 缺失或 kind 不符 → ValueError）；source_kind 仅审计用，payload 不承载、不新增 LedgerKind | 原词表（VERIFY_RESULT\|HUMAN_VERDICT\|VOTE）与 close_panel「source_seq=收口 finding 事件 seq」客观错位；一致性校验使 (cluster, tier, src_seq) 幂等键的 src_seq 可信 | kernel/cluster.propagate_verdict；kernel/dispatch.close_panel；tests/test_kernel_cluster.py 源事件校验负用例 |
| 2026-10-10 | SPEC-2/ledger | D24：ledger.transaction() 可重入（计数式嵌套，最外层边界统一提交/回滚）；内核复合函数内部自开事务、嵌套时沿用外层；无上下文时 append 逐条提交与 M0 逐位一致 | BEGIN IMMEDIATE 事务中重入直接报错；复合函数与 harness 批处理两侧都需要原子性；接口最小惊讶要求可重入而非双形态 API | kernel/ledger.transaction；tests/test_kernel_transaction.py 嵌套用例 |

> 滚动协议 3：本 SPEC 落地时上述决策已同步 `docs/spec/DECISIONS.md`；实现子任务发现的进一步未定细节按协议记入该文件，不回改本文件编号语义。

<!-- ↑↑↑ SPEC-2_kernel.md 全文结束 ↑↑↑ -->

---

## 附录 C：SPEC-5 M1 验收测试与门禁清单（全文）

> 以下为 `docs/spec/SPEC-5_m1_acceptance.md` 全文逐字快照（含 2026-10-11 终检三处修订留痕注：§1.2/§2.3/§7.2；DECISIONS 105–107 行同源）。

<!-- ↓↓↓ SPEC-5_m1_acceptance.md 全文开始 ↓↓↓ -->

# SPEC-5 M1 验收测试与门禁清单（机器可检）

> 来源：SPEC-2 §0–§9（唯一机制输入）；设计文档 §16.6（每步验收机器可检、人只审里程碑）与 §16.7-8；SPEC-4 §5（门禁四项模式）；SPEC-1 §6（写口/重放/幂等不变量）；父任务合同 ac_mv1s39t26l1to9he…ac_mv1s39t2luneoms3（M0 四项技术债、重放等价 3 种子×≥3 检查点、矩阵复跑并发 3、无网络/无新增依赖约束）。
> 读者：AI 编码代理与验收执行者。每条验收 = **【判据】可执行命令或 pytest 用例名 + 【期望】精确输出**；判据通过 = 命令退出码 0 且输出匹配期望模式。发现本文件与 SPEC-2 矛盾，以 SPEC-2 为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 基线快照（2026-10-10，SPEC-5 导出时点，M0 终态）：全量 `python -m pytest` → **338 passed, 1 skipped**（`--collect-only` 339 项；唯一 skip = 技术债③ tests/test_m0_final_knobs.py 的 ENTRY_DIRECT 边界格）；黄金回归集 34 例（route 9 / route_thompson 9 / promote_scan 8 / agg_cells 8，manifest seed=7）；官方矩阵 `results/experiments-full` n_units=n_rows=507、162 组全达标（out/group_targets.csv qualifies 全真）。已知基线红：黄金溯源再生成 `freeze_golden.py --check` 失败（manifest 钉 v2 前旧参数 vs 当前 params.yaml v2，promote-006 transitions 21≠20）——处置见 §4.1，不属 M1 门禁除非触发 sanctioned 重冻。
> 状态：**定稿 v1.0（2026-10-10，规格门歧义拍板）**——本轮定稿修订：§5.4 命令补 `--workers 3`（D25，tune_round 缺省 workers=4 与 §7.3 冲突）、§9.3 免审直跑执行记录（D26）、新增 §11 决策记录（五字段，已逐条同步 `docs/spec/DECISIONS.md`）；SPEC-2 同日定稿（其 D19–D24 见该文件 §10.1）。实现期发现的进一步未定细节按滚动协议记 `DECISIONS.md`。

---

## 0. 判定总则

- **命令口径**：pytest 统一 `python -m pytest <路径> -q`（pyproject `addopts=-q`，全量直接 `python -m pytest`）；工作目录 = 仓根 `/data/Qswarm`；exit 0 = 通过。
- **期望输出形态**：pytest 期望 `N passed`（不得出现 `failed`/`error`；门禁终态另要求 `0 skipped`）；比对类脚本期望文中所列字面行（如 `ALL_EQUAL=true`）。
- **位级纪律**：凡标注「逐位/逐字段/逐字节」的判据一律 `==` 全等，无容差、无近似（SPEC-4 §6、kernel.replay `diff_cells` 同一纪律）。
- **期望计数**：文中给出的 N（用例数、行数、组数）为导出时点基线；实现新增测试使 N 只增不减，「≥ 基线」即为判据。
- **证据留痕**：所有判据命令的输出摘录进交付报告；矩阵/重放级证据按节落盘 `results/`。
- **两 SPEC 冻结约束**（ac_mv1s39t26l1to9he）：SPEC-2 与本文件定稿落盘之前，`qswarm/` 与 `tests/` 不得出现任何 M1 新增或修改代码（含技术债修复）。机器可检验证：交付报告附两 SPEC 的落盘时刻戳与最早 M1 变更文件的 mtime 对照（`find qswarm tests -name '*.py' -newer <SPEC 路径>` 的反向佐证）；无 git 仓库，以文件时间为准。

---

## 1. A 组：M1 四块功能验收（判据 = 测试文件 + 最低用例名集）

四块机制 ↔ SPEC-2 章节：聚类归并 §1、委托契约与派单状态机 §2、re_route 与猜测格弱存款 §3、配额与预算熔断 §4；并发纪律 §7 为横切（§1.5）。测试文件为 M1 新增；下列用例名为**门禁最低集**（实现可增不可缺）。

### 1.1 聚类归并 `kernel/cluster.py`（SPEC-2 §1）

【判据】`python -m pytest tests/test_kernel_cluster.py -q`
【期望】全 passed；且 `--collect-only` 含以下最低用例名：

| 用例名 | 钉住的判据（SPEC-2 出处） |
|---|---|
| `test_line_overlap_intersection_over_min` | 交集/min；子窗含于大窗 = 1.0；空窗 / start>end = 0.0（§1.2/D1） |
| `test_coarse_mergeable_requires_path_lang_cwe_overlap` | 同 path ∧ 同 lang ∧ CWE 相等 ∧ 重叠 ≥ 0.5；CWE 双 None 退 (family,type)、恰一侧 None → False（§1.2/D3） |
| `test_fine_mergeable_never_merges_without_sim_fn` | sim_fn=None 恒 False；阈值 0.9 含等号边界（§1.2/D4） |
| `test_cluster_findings_all_pairwise_no_transitive_closure` | A~B~C 而 A≁C 不并 C（§1.2/D2） |
| `test_cluster_findings_deterministic_order_first_fit` | 候选序 (path,line_start,line_end,finding_id) 字典序；一 finding 至多一提案；assigned 已归属不参与（§1.2） |
| `test_apply_merges_cluster_id_and_event_same_transaction` | cluster_id=`C-{YYYYMMDD}-{seq:06d}`；clusters/cluster_members 行 + cluster_merge 事件同事务；members=本次并入集（§1.3/D5） |
| `test_apply_merges_idempotent_zero_events` | 幂等键 (level, frozenset(members)) 已存在 → 零新事件返回 []（§1.3/§6.4） |
| `test_split_cluster_zero_deposit_and_verdict_reset` | 拆分零存款、verdict_* 投影清空、groups<2 或并集≠全集 ValueError、簇不存在/已拆分 ValueError（§1.3） |
| `test_propagate_verdict_expansion_order_and_weights` | 展开序=成员(reporter_id 字典序)→侦察→评审→验证；scout×SCOUT_X、评审同向+/反向−×REVIEWER_X、S3 验证行 VERIFIER_CONFIRM/CONFLICT；每笔携带 src_seq（§1.4/§5.2/D7） |
| `test_propagate_verdict_idempotent_by_cluster_tier_src_seq` | 同 (cluster,tier,src_seq) 重复 → 零新事件（§1.4/§6.4） |
| `test_propagate_verdict_conflict_appends_not_rolls_back` | S3 与 S2/S1 冲突补记更强反向存款，不回滚（§1.4/§6.2） |
| `test_propagate_verdict_zero_direction_no_deposit` | direction=0 / verify_error → 零存款零投影更新（§1.4） |
| `test_propagate_pending_repairs_crash_window` | 「源事件已落、派生存款未落」构造场景逐个补齐；lineage→None 的簇入 skipped 不报错（§1.4/§6.3） |
| `test_apply_human_verdict_three_way_and_terminal_guard` | +1 全员 CONFIRMED、−1 REJECTED、0=落事件零存款零状态；终态再裁 ValueError（§1.4/§12） |
| `test_aggregate_votes_majority_tie_disagreement` | 方向=符号和多数、平票 0、confidence=多数票四维均值之平均、反向票 → disagreement=True（§1.5/D22） |
| `test_close_panel_state_s1_propagation_and_guards` | open→voted；confidence/disagreement_json 写入；direction≠0 → S1 存款（S1_SOURCE_W×角色系数）；=0 零存款；票数不足 ValueError；已 voted 零事件（§1.5） |

### 1.2 委托契约与派单状态机 `kernel/dispatch.py`（SPEC-2 §2）

【判据】`python -m pytest tests/test_kernel_dispatch.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_role_literal_tables` | ROLE_OUTPUT_SCHEMA / ROLE_TOOL_BOUNDS / ROLE_TASK_BOUNDS / FINDING_TRANSITIONS 逐字表（§2.2/§2.4） |
| `test_hop_budget_by_mode_and_unknown_raises` | fast=0/pr=1/full=2；未知档 ValueError（§2.2） |
| `test_build_contract_four_pieces_pure` | ①goal ②output_schema_ref ③tool_bounds ④task_bounds + hop_budget + taxonomy_node 直出；纯函数零 IO（§2.2/§4.3） |
| `test_dispatch_guard_rejects_non_queued` | tasks.state≠queued → ValueError（§2.3/§6.4） |
| `test_dispatch_empty_pool_audits_stays_queued` | assignee=null 的 DISPATCH 事件落账、tasks 留 queued、attempts 不消耗（§2.3/D10） |
| `test_re_dispatch_attempts_limit_then_failed` | attempts<2 重派（attempts+1、state=dispatched）+ RE_DISPATCH 事件；≥2 → failed 且 ValueError、零存款（§2.3/§16.7-8） |
| `test_finding_transitions_table_and_terminal_guard` | §10.2 迁移表逐字 + D8 直达边；非法迁移/终态流转 ValueError（§2.4） |
| `test_transition_finding_event_carries_state_zero_deposit` | kind=finding 事件 payload.state=新状态；从不产 deposit（§2.4/D9） |
| `test_submit_scout_triage_and_guess_backfill` | candidates.triaged=1、guess 回填；injection_flag 候选保持候选态标高优先（§2.5/§11.6-3） |
| `test_submit_auditor_issues_id_and_evidence_chain_guard` | finding_id=`F-{date}-{seq:06d}` run 内自增；证据链 <3 节点或缺 source/sink/sanitizer → SchemaRejected 零落库（§2.5/SPEC-1 §4.2） |
| `test_submit_auditor_mismatch_auto_reroute` | 产出 (family,type) 与契约节点错配 → 自动 re_route + 猜测格弱存款（§2.5/§3.1） |
| `test_submit_reviewer_self_vote_rejected_and_auto_close_panel` | reviewer==reporter → ValueError（纪律 2）；票满 params.panel.SIZE 自动 close_panel（§2.5/§4.4） |
| `test_submit_verifier_fp_without_blocking_forced_verify_error` | §10.4 改判守卫：verified_fp 缺 blocking_evidence → 强制 verify_error(error_kind=poc_fail)（§2.4/§2.5） |
| `test_submit_verifier_outcome_mapping_and_s2_deposit` | verified_real→verified+tier=S2 dir=+1、verified_fp→rejected+dir=−1、verify_error→inconclusive 零存款零投影（§2.4 表） |
| `test_submit_rejects_task_not_dispatched` | task 非 dispatched → ValueError（§2.5） |
| `test_expire_run_zero_deposit_terminal_idempotent` | 非终态 finding→expired（零存款）、未完任务→failed；已终态 → []（§2.3） |

【修订留痕 2026-10-11 终检（DECISIONS 同日终检行）】上表 17 例中 7 例 `test_submit_*` 全仓无承载：SPEC-2 §2.5 `submit()` 统一代落账入口未落位（dispatch 切片行预声明归接线子任务，接线按 §0.4 窄口接通后未回补），语义面已分散落位（scout 弱存款→test_kernel_reroute、auditor 建档→open_finding、reviewer/verifier 收口→cluster.close_panel/propagate_verdict + test_verdict_deposits）。门禁 A 组「最低用例名集齐」本文件口径按 40/47 如实记录，处置待负责人明示（补实现或修订判据）；交付报告遗留问题 L1。其余 40 例全部按名落位且全绿。

### 1.3 re_route 与猜测格弱存款 `kernel/reroute.py`（SPEC-2 §3）

【判据】`python -m pytest tests/test_kernel_reroute.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_is_mismatch_rules` | 家族段不同=错配；同族双类型段不同=错配；家族级→类型级细化=不算（§3.1/D12） |
| `test_re_route_finding_weak_deposit_standard_form` | 每 scout 一笔：S1 / −1 / 0.05 / taxonomy_path=from_node 路径段 / origin=prod / src_seq=re_route 事件 seq（§3.2/D13） |
| `test_re_route_finding_at_most_once` | 同 finding 已有 re_route 事件 → 零新事件（§3.2/§6.4） |
| `test_guess_taxonomy_ranking_deterministic` | 票数 → 猜测格 C* 之和（无格 ROOT_PRIOR）→ 字典序；无猜测 None；纯函数零 rng（§3.2） |

### 1.4 配额与预算熔断 `kernel/budget.py`（SPEC-2 §4）

【判据】`python -m pytest tests/test_kernel_budget.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_budget_for_mode_table` | fast→FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS（§4.2） |
| `test_quota_l2_boundaries` | min(cap, ⌊remaining/cost⌋)；cost≤0 或 remaining≤0 → 0（§4.2） |
| `test_quota_s2_formula_and_zero_cost` | ⌊存活数×remaining/cost⌋；cost≤0 → 0（§4.2） |
| `test_s2_rank_key_lexicographic` | (SEVERITY_RANK, scarcity, confidence) 字典序降序；scarcity=1/(1+簇数)；type_node=None 按空串；不取乘积（§4.2/D15） |
| `test_budget_state_trips_at_equal_spent` | spent ≥ budget 含等号 → degraded=True（D16）；tier 沿 full→pr→fast 到底；quota 按剩余重算（§4.2/§4.3） |
| `test_on_cost_first_trip_scaling_event_degraded_status` | 首次 trip：SCALING(pool=f"mode:{mode}", delta=−1, reason="budget_breaker") + runs.status='degraded' 同事务；重复 trip 零新事件（§4.2/D17） |
| `test_batch_eligible_full_only` | batch_eligible 仅 full 为 True（§4.1） |
| `test_expire_run_after_breaker_zero_deposit` | 熔断收尾接 expire_run：expired 路径不产任何 deposit（负断言：全账本 deposit 计数不变）（§4.3/§2.4） |

### 1.5 并发纪律横切 `kernel/ledger.transaction()`（SPEC-2 §7）

【判据】`python -m pytest tests/test_kernel_transaction.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_transaction_commits_or_rolls_back_atomically` | BEGIN IMMEDIATE；正常退出 COMMIT、异常 ROLLBACK；回滚后账本与表零残留（§7.1） |
| `test_append_inside_transaction_defers_commit` | conn.in_transaction 为真时 append 不逐条 commit，由上下文边界统一提交（§7.1） |
| `test_append_without_context_unchanged` | 无上下文行为与 M0 逐条提交逐位一致（§7.1；M0 兼容由全量回归双保险） |

---

## 2. B 组：不变量与确定性验收

### 2.1 写口静态扫描扩展（SPEC-2 §6.1/§9-1）

【判据 1（测试）】`python -m pytest tests/test_kernel_ledger.py -q` 全 passed，其中 `test_write_port_static_scan` 的词表已扩展为 SPEC-2 §6.1 全表：
`ledger, cells, findings, evidence_nodes, votes, verify_results, clusters, cluster_members, tasks, candidates, runs`（写语句正则仍为 `INSERT INTO|REPLACE INTO|UPDATE|DELETE FROM`）。
【判据 2（独立 grep 审计，交付报告粘贴输出）】

```bash
grep -rnE "\b(INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|UPDATE|DELETE[[:space:]]+FROM)[[:space:]]+(ledger|cells|findings|evidence_nodes|votes|verify_results|clusters|cluster_members|tasks|candidates|runs)\b" \
  qswarm/ --include="*.py" | grep -v "^qswarm/kernel/"
```
【期望】输出为空（管道无行），全链路退出码非零（grep 无命中）＝无越界。

### 2.2 幂等双跑（SPEC-2 §6.4/§9-3）

【判据】`python -m pytest tests/test_kernel_cluster.py tests/test_kernel_dispatch.py tests/test_kernel_budget.py tests/test_kernel_reroute.py -k "idempotent or guard or at_most_once or zero_events or zero_deposit" -q`
【期望】全 passed，收集数 ≥ 8（§6.4 表八行逐行有测试承载：apply_merges / split/dispatch/re_dispatch 守卫 / propagate / close_panel / re_route / on_cost trip / expire_run）。
【界线钉死】读侧幂等（重复业务事实）→ 跳过返回 []；写侧非法（状态机不允许）→ ValueError——两侧用例都须存在（`test_*_idempotent_zero_events` 与 `test_*_guard_rejects`/`_then_failed` 命名即区分）。

### 2.3 确定性（SPEC-2 §6.5/§9-4）

【判据 1】`python -m pytest tests/test_kernel_determinism.py -q` 全 passed，最低用例：
- `test_m1_event_stream_bit_identical_same_seed`：同 (种子, 输入事件序, params) 两次运行事件流 sha256 全等；
- `test_event_stream_identical_across_pythonhashseed`：`PYTHONHASHSEED` 取不同值的两子进程事件流逐位一致（对标 tests/test_world_taskgen.py 跨解释器模式）。

【修订留痕 2026-10-11 终检（DECISIONS 同日 §2.3 行）】上列文件与用例名未按名落位，判据语义由既有测试等效承载，按以下映射执行：`test_sim_cluster_wiring.py::test_same_seed_attribution_bit_identical` + `::test_attribution_identical_across_pythonhashseed`（簇归属与轨迹 digest，子进程跨 PYTHONHASHSEED）、`test_verdict_deposits.py::test_verdict_flow_bit_identical_same_seed` + `::test_verdict_flow_cross_process_bit_identical`（M1 事件流同种子位级 + 跨进程）、`test_kernel_cluster.py::test_attribute_findings_deterministic_and_hashseed_free`、`test_kernel_reroute.py::test_re_route_deterministic_event_stream`——全部用例在全量 pytest（528 例 0 skip）内。
【判据 2（M1 新函数零随机源）】

```bash
grep -nE "\brng\b|\brandom\b" qswarm/kernel/cluster.py qswarm/kernel/dispatch.py qswarm/kernel/reroute.py qswarm/kernel/budget.py
```
【期望】0 命中（内核唯一随机消费点仍是 route/route_thompson）。

### 2.4 M0 复用面零重写（SPEC-2 §0.3）

【判据】`python -m pytest tests/test_kernel_route.py tests/test_kernel_route_thompson.py tests/test_kernel_cells.py tests/test_kernel_ledger.py tests/test_kernel_promote_scan.py tests/test_kernel_replay.py tests/test_golden_regression.py -q`
【期望】全 passed——M0 六面的既有行为测试与黄金回归全绿即「原样复用」的可检表达；M1 新模块 diff 不得出现对上述模块逻辑的复制（评审项：交付报告对账表逐行「调用/未复制」声明）。

---

## 3. C 组：重放等价三投影（父任务 ac_mv1s39t231p9yxml 判据化）

### 3.1 单元级：rebuild/diff 扩展（SPEC-2 §6.3/§9-2）

【判据】`python -m pytest tests/test_kernel_replay.py -q` 全 passed，新增最低用例：

| 用例名 | 钉住的判据 |
|---|---|
| `test_rebuild_clusters_from_events` | clusters/cluster_members/verdict_* ← cluster_merge/cluster_split + verify_result/human_verdict 事件折叠；members 真源=事件增量（§6.3） |
| `test_rebuild_findings_last_state_wins` | findings.state 取每 finding 最后一条 finding 事件 payload.state；grade/confidence/disagreement 同法重建（§6.3/D9） |
| `test_diff_clusters_diff_findings_bit_exact` | 字段 `==` 全等无容差；重建 vs 在线投影 diff 为空（§6.3） |
| `test_rebuild_three_projections_arbitrary_prefix` | 多个 upto_seq 前缀（含 0/中途/最新）三投影 diff 全空（「任意时点」判据） |
| `test_ledger_rebuild_returns_three_projection_summary` | `ledger.rebuild()` 三投影摘要 dict；`rebuild_cells` 单投影入口保留（§6.3） |

### 3.2 轨迹级专项：3 种子 × ≥3 检查点 + 收尾全账本 + cells 物理表比对

【判据】新增独立取证脚本 `experiments/verify_ac_replay_m1.py`（对标 scripts/verify_ac_*.py 模式）：

```bash
python experiments/verify_ac_replay_m1.py --seeds 11,22,33 --n-days 30 --replay-every 10 \
  2>&1 | tee results/_replay_m1_ac.log; echo "EXIT=$?"
```
【方案（脚本必须实现并在输出中逐行留痕）】
1. 逐种子跑 engine（seeds 11/22/33，n_days=30，replay_every=10 与官方运行同口径）；
2. **每种子 ≥3 个检查点**（day 10 / day 20 / 收尾 day 30）：在检查点 seq 处 `rebuild_cells` vs 在线 cells → `diff_cells` 空；`rebuild_clusters` vs 在线簇投影 → `diff_clusters` 空；`rebuild_findings` vs 在线 finding 投影 → `diff_findings` 空；
3. **收尾全账本重放**：从空账整段重放至临时库；
4. **cells 物理表逐字段比对**：临时库 vs 原库 `SELECT * FROM cells`（全列，按 (agent,role,node,family,type) 主键序）逐行逐列 `==` 全等；clusters/cluster_members/findings（含 state/grade/confidence/disagreement 列）同法比对；
5. 输出：逐检查点一行 `PASS <seed> <checkpoint> diff_cells=0 diff_clusters=0 diff_findings=0`；收尾一行 `PASS <seed> physical-tables cells/clusters/findings ALL EQUAL`；末行 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY`。
【期望】退出码 0；`results/_replay_m1_ac.log` 含末行 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY` 且无 FAIL。

### 3.3 引擎内环校验不回退（M0 ⑨ 承载）

【判据】`python -m pytest tests/test_sim_engine.py -q` 全 passed（`test_run_produces_all_event_kinds_and_nine_metrics` 断言 replay_report.passed 且 replay_day_reports 全过）；矩阵级 ⑨=100% 由 E 组全量复核。

### 3.4 崩溃窗口修复

【判据】`python -m pytest tests/test_kernel_cluster.py -k "propagate_pending" -q` 全 passed（§1.1 `test_propagate_pending_repairs_crash_window`——构造「源事件已落、派生存款未落」，运行后按 (cluster,tier,src_seq) 比对派生存款集合，窗口收敛为零）。

---

## 4. D 组：黄金回归与突变门（digest 比对方式）

### 4.1 M0 黄金回归全绿（digest 逐位不变）

【判据 A（回归绿，基线判据）】`python -m pytest tests/test_golden_regression.py -q`
【期望】`37 passed`（导出时点基线 = 34 例夹具参数化〔route 9 / route_thompson 9 / promote_scan 8 / agg_cells 8〕+ 3 项元断言〔manifest counts 门 / 溯源 seed+digest / slices 前缀性〕；M1 新类冻结后总数只增不减，M0 四类 34 例不得减少）。夹具参数以 manifest.json 钉死（`manifest_params`），不随 configs/params.yaml 漂移——这是黄金门的有效比对面。

【判据 B（夹具字节不变，基线判据）】导出时点已固化基线清单 `results/_golden_m0_baseline.sha256`（57 文件：34 case + manifest + slices + README）：

```bash
sha256sum -c results/_golden_m0_baseline.sha256 --quiet && echo SHA256_MANIFEST_ALL_OK
```
【期望】`SHA256_MANIFEST_ALL_OK`，退出码 0——「既有 M0 黄金用例 digest 逐位不变」的可检形态。凡 M1 实现需要改任何 tests/golden/ 文件，必须先走 §5.1 sanctioned 判定。

【digest 比对方式与 `--check` 的基线事实】
- manifest.json 的 `trajectory.digest`（seed=7，sha256）+ `regen_command`/`verify_command` 字段为溯源锚点（比对命令：`python3 -c "import json;print(json.load(open('tests/golden/manifest.json'))['trajectory'])"`，输出不得变化）；
- **`python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check` 在 SPEC-5 导出时点为红（基线即红，非 M1 造成）**：夹具冻结早于 params.yaml v2 定稿——manifest 钉旧参数（promote N=10/DELTA=0.1、无 FC_*/ENTRY_* 键），重生成走当前 v2 参数，自检在 promote-006 失败（transitions expected=21 got=20，已实测留痕）。该工具的恢复属 M1「冻结新黄金」子任务：凡发生 §5.1 sanctioned 变更并按 manifest.regen_command 重冻（以**当时** configs/params.yaml 全量重生成，M0 34 例一并重冻、清单更新并记 DECISIONS），重冻后 `--check` **必须恢复退出码 0** 并升格为 D 组终判据；若 M1 未触发 sanctioned 变更，`--check` 修复不列入 M1 门禁、如实记遗留问题（禁止静默当绿）。

【跌标处置】M1 实现致判据 A/B 任一红 = 内核行为变更：非 sanctioned 立即停止并修复回归；属 sanctioned 变更走 §5 矩阵复跑流程并按上述重冻（重冻记入 DECISIONS.md 与交付报告变更清单，禁止静默重冻）。

### 4.2 突变门

【判据】`python -m pytest tests/test_golden_mutation_gate.py -q` → 全 passed（既有公式突变必红自证不回退）。

### 4.3 M1 新增黄金四类并纳入突变门（父任务 ac_mv1s39t2jayfvi09）

【判据】
- `ls tests/golden/` 含 `cluster/ dispatch/ budget/ re_route/` 四个新目录，各 ≥3 个夹具（沿用 M0 命名 `<kind>-NNN.json`、glob `*.json`；`find tests/golden/{cluster,dispatch,budget,re_route} -name '*.json' | wc -l` ≥ 12）；
- manifest.json `counts` 增四类且各 ≥3；每夹具含输入账本切片/生效参数/种子/期望输出与溯源（regen 扩展命令入 manifest）；
- `python -m pytest tests/test_golden_regression.py -q` 参数化覆盖四新类（总数 ≥ 37+12=49 passed；若触发 sanctioned 重冻致 M0 四类计数变化，则以重冻后 manifest `counts` 重新固化 `results/_golden_m0_baseline.sha256` 与总数基线，约束改为「M0 四类各 ≥6、新四类各 ≥3」，并在交付报告声明重冻）；
- `tests/test_golden_mutation_gate.py` 对四新类各 ≥1 代表公式钉「突变必红」：`CLUSTER_OVERLAP_MIN`、quota 公式（quota_l2/quota_s2 任一）、`FINDING_TRANSITIONS` 任一边、`RE_ROUTE_WEAK_W`。

---

## 5. E 组：官方矩阵复跑（父任务 ac_mv1s39t2m47uutjo；并发 3）

### 5.1 触发条件

仿真路径因 M1 接线变更时（verdict 归属接真实聚类、仿真注入 BudgetPolicy、引擎调用 kernel.cluster/dispatch 等 sanctioned 变更）**必跑本组**；判据：交付报告声明接线面 + §4.1 黄金/digest 是否变化。若六件套与基线逐位一致（digest 全等）可免复跑，但 §5.4 位级复核仍必做。

### 5.2 命令（统一并发 3，禁 4 或更高）

```bash
python -m qswarm.simulator.experiments --full --workers 3 --out results/experiments-full-m1 \
  > results/full-run-m1.log 2>&1
python -m qswarm.simulator.report --from results/experiments-full-m1 --out out
```

### 5.3 达标判据（162/162 组 9/9）

```bash
python3 - <<'EOF'
import csv, json
m = json.load(open("results/experiments-full-m1/manifest.json"))
assert m["workers"] == 3 and m["n_units"] == 507 and m["n_rows"] == 507, m
assert m["seeds"] == [11, 22, 33] and m["n_days"] == 30
assert m["wall_s"] <= 7200, m["wall_s"]
assert m["per_run_elapsed_s"]["max"] <= 300, m["per_run_elapsed_s"]
rows = list(csv.DictReader(open("out/group_targets.csv")))
assert len(rows) == 162 and all(r["qualifies"] in ("True", "true") for r in rows)
assert all(int(r["n_unmet"]) == 0 and int(r["n_nodata"]) == 0 for r in rows)
print("MATRIX_M1 162/162 9/9 PASS")
EOF
```
【期望】末行 `MATRIX_M1 162/162 9/9 PASS`，退出码 0。辅助核验：`timing_by_group.csv` 全行 `within_budget_5min=True`；`out/sim_report.md` §1 推荐组 `N06-D0.05-T0.60-E0.30-V0.20` 九行全 ✅；`starvation.csv` ⑧ 判定与 summary 一致。

### 5.4 位级复跑 ALL_EQUAL 证据

```bash
python experiments/tune_round.py --round 52 --workers 3   # 同管道复跑推荐组（种子 11/22/33，--replay-every 10，显式钉全部定稿旋钮；D25：tune_round 缺省 workers=4，必须显式 3，见 §11）
python experiments/compare_rerun.py \
  --base results/experiments-full-m1/results.csv \
  --rerun results/tune-round52/results.csv \
  --group "N06-D0.05-T0.60-E0.30-V0.20" | tee results/_gate_compare_round52.log; echo "EXIT=$?"
```
【期望】`metrics_equal=true digest_equal=true ALL_EQUAL=true`，退出码 0（SPEC-4 §6 位级复现；基线对照模式见 results/_gate_compare_round51.log 先例）。

### 5.5 归档与引用更新

- 【判据】M0 原矩阵目录原样保留：`ls results/experiments-full/manifest.json results/experiments-full/results.csv` 存在且 `python3 -c "import csv;print(sum(1 for _ in csv.DictReader(open('results/experiments-full/results.csv'))))"` 输出 `507`（不删除不覆盖）；
- 【判据】`results/M0_GATE_FINAL.md` 追加「M1 复核」节，引用 `results/experiments-full-m1`、`results/_gate_compare_round52.log` 与新 sim_report 路径；原文四项结论保留可考（`grep -c "M1 复核" results/M0_GATE_FINAL.md` ≥ 1 且原文标题行未改动）；
- （建议，非门）旧 `out/` 三产物快照至 `results/_archive-m1-out/` 后再覆盖。

### 5.6 跌标即停（硬条款）

`group_targets.csv` 出现任一 `qualifies=false`、9 度量任一跌破 §2 表目标、或 `replay_equivalent` < 100% → **立即停止后续步骤**：不再复跑、不调参、不放宽阈值、不重冻 golden 掩盖；原样记录跌破值并如实报告，等待负责人明示口径修订后才可继续。此条款为门禁停止判定，优先级高于本清单其他任何「修复后再验」路径。

---

## 6. F 组：M0 四项技术债清偿（父任务 ac_mv1s39t2j8yssfv6 逐项判据化）

> 每项完成后均须：`python -m pytest`（全量）→ `0 failed`，且 §4.1 判据 A/B 双绿（回归全 passed + `sha256sum -c results/_golden_m0_baseline.sha256` 全 OK，黄金 digest 逐位不变）。技术债修复**不得引入任何行为变更**（黄金不变是硬判据）；实现选择（二择一处）当场记 `DECISIONS.md` 五字段。

### 6.1 债① promote_scan 评估路径的 cells 重写

- **现状位点**：`qswarm/kernel/promote_scan.py::evaluate_candidate_criteria` 经 `agg_cells(...)` 取视图，而 agg_cells 收尾**整表重写 cells**；docstring 声称「只读」与行为不符。
- **清偿口径（二择一，记 DECISIONS）**：
  - (a) **真只读**：评估路径不再触发 cells 写表；或
  - (b) **如实声明**：docstring 明示「会经 agg_cells 重写 cells」并以 grep 限定调用点集合。
- 【判据】
  - 新防回归测试 `test_evaluate_candidate_criteria_read_only_or_scoped`（落 tests/test_kernel_promote_scan.py）：
    - 口径 (a)：断言调用前后 `SELECT * FROM cells` 行集与全列值逐位不变（且 `conn.total_changes` 增量为 0）；
    - 口径 (b)：docstring 含「重写 cells」字样（`grep -n "重写 cells" qswarm/kernel/promote_scan.py` 命中）且 `grep -rn "evaluate_candidate_criteria" qswarm/ --include="*.py"` 的非测试命中 ⊆ {promote_scan.py}；
  - `python -m pytest tests/test_kernel_promote_scan.py tests/test_m0_final_knobs.py -q` 全 passed。

### 6.2 债② ledger._schema_present 触发器校验

- **现状位点**：`qswarm/kernel/ledger.py::_schema_present` 仅查 `ledger` 表存在，不校验 append-only 两触发器（`ledger_no_update`/`ledger_no_delete`）。
- **清偿口径**：校验两触发器存在；缺失即**补建**（executescript 对应 DDL）**或显式报错**（二择一，记 DECISIONS）。
- 【判据】
  - 新测试 `test_reopen_prebuilt_db_missing_triggers`（落 tests/test_kernel_ledger.py）：预建有 ledger 表、无触发器的库文件 → 打开 KernelLedger 后 `sqlite_master` 中两触发器存在（补建口径），或抛出文档明示的异常类型（报错口径）；
  - `python -m pytest tests/test_kernel_ledger.py -q` 全 passed。

### 6.3 债③ ENTRY_DIRECT 边界测试数学可达化（全仓 0 skip）

- **现状位点**：`tests/test_m0_final_knobs.py`（约 ：385）`pytest.skip("种子 42-46 中无 accuracy=0.875 的边界格（世界抽样使然）")`——entry_items=12 时 accuracy 粒度为 k/12，0.875×12=10.5 非整数，精确等值边界**数学不可达**，该分支恒 skip。
- **清偿口径**：按 12 题口径重写为可达断言——阈值上首达格 = ⌈0.875×12⌉/12 = 11/12、末失格 = 10/12 双侧断言（保留 ≥0.875 判据语义），删除 skip 路径。
- 【判据】
  - `python -m pytest tests/test_m0_final_knobs.py -q` → 全 passed、**0 skipped**；
  - `grep -rn "pytest.skip" tests/ --include="*.py"` 输出为空（test_sim_report.py 的 `results/experiments-full 不存在` 条件 skip 分支一并移除或改为显式失败——产物目录在仓内恒存在，该分支属死代码）；
  - 全量 `python -m pytest` 末行无 `skipped` 字样。

### 6.4 债④ test_config 逐键钉住 params.yaml 全部 v2 定稿旋钮

- **现状位点**：`tests/test_config.py` 仅抽查 ~18 键。
- **清偿口径**：对 `configs/params.yaml` **全部叶子键**逐一显式断言期望值（promote 8 / tilt 4 / shrink 1 / decay 3 / demote 8 / exam 14 / explore 1 / budget 4+M1 新增 3 / deposits 4 / panel 2 / roster 4——以实现时点实际叶子集为准；v2 机制旋钮值见 DECISIONS 2026-10-09/10 各定稿行）。
- 【判据】
  - 新测试 `test_config_pins_every_leaf_key`：程序化取 params.yaml 叶子键全集，与测试内显式期望表键集做差集断言 `== ∅`（缺口即红，防未来加键漏钉）；
  - `python -m pytest tests/test_config.py -q` 全 passed。

### 6.5 四债总清偿判据

```bash
python -m pytest 2>&1 | tail -2          # 期望：N passed（无 failed、无 skipped），N ≥ 339 + 新增
sha256sum -c results/_golden_m0_baseline.sha256 --quiet && echo SHA256_MANIFEST_ALL_OK
grep -rn "pytest.skip" tests/ --include="*.py" | wc -l   # 期望 0
```
【期望】全量 pytest 全绿 0 skip；`SHA256_MANIFEST_ALL_OK` 打印；skip 计数 0；四项各留证据行进交付报告。

---

## 7. G 组：约束判据（父任务 ac_mv1s39t2luneoms3）

### 7.1 无网络

```bash
grep -rnE "\b(socket|ssl|urllib|urllib3|http\.client|httpx|requests|aiohttp|ftplib|smtplib|telnetlib)\b" qswarm/ --include="*.py"
```
【期望】0 命中（基线已核验：现仓唯一近似命中为 `subprocess` 本地降级执行器，非网络）。测试进程不访问网络（pytest 全绿本身在离线环境即可复现为旁证）。

### 7.2 无新增第三方依赖

```bash
grep -rhE "^\s*(import|from) " qswarm/ --include="*.py" | grep -vE "^\s*(from|import) \." \
  | sed -E 's/^\s+//; s/^(from|import) ([a-zA-Z_0-9]+).*/\2/' | sort -u \
  | grep -vx -f <(printf '%s\n' __future__ argparse bisect collections concurrent contextlib csv dataclasses datetime enum functools hashlib importlib itertools json math os pathlib pydantic qswarm random re shutil sqlite3 statistics subprocess sys tempfile time types typing yaml)
```
【期望】输出为空（qswarm/ 顶层第三方根 ⊆ {pydantic, yaml}；tests/ 另允许 pytest）。`pyproject.toml` 的 `dependencies`/`optional-dependencies` 不变（交付报告 diff 留痕）。禁网络与依赖白名单同时保证「无 LLM SDK」。
【修订留痕 2026-10-11 终检（DECISIONS 同日 §7.2 行）】白名单补 3 个标准库根 `contextlib`/`functools`/`re`（M1 kernel/replay.py 等新增 import；原清单漏列致字面「输出为空」不可复现，三根均非第三方）；判定结论不变，另以 AST 全量扫描（81 文件，第三方根闭集 {pydantic, yaml, pytest}）作双口径证据。

### 7.3 并发 3

- 【判据】官方矩阵与一切批量验证命令显式 `--workers 3`；§5.3 判据已断言 `manifest.json workers == 3`；（D25：`experiments/tune_round.py` 的 `--workers` **缺省 4**，凡调用必须显式 `--workers 3`，§5.4 命令已按此定稿）；
- 【判据】`grep -rn "workers" results/full_run_v2.sh results/full-run-m1.log 2>/dev/null | head`——新运行记录中不得出现 `--workers 4` 或更高（M0 旧脚本 `full_run_v2.sh` 的 `--workers 4` 属历史留痕仅作对照，M1 起所有新命令为 3）。

### 7.4 规格未覆盖处当场记 DECISIONS

【判据】`docs/spec/DECISIONS.md` 含 M1 实现期新增五字段行（`grep -c "| 2026-10-" docs/spec/DECISIONS.md` ≥ 实现决策数），交付报告附新增行清单；实现与记录一致（评审项）。

---

## 8. H 组：交付对账（终检子任务输入）

交付报告必须含，且每项有证据行：

| # | 项 | 判据/出处 |
|---|---|---|
| 1 | 变更清单 | 文件级（新增/修改/删除）+ 一句话目的 |
| 2 | SPEC-实现对账表 | SPEC-2 §0.2 每行 → 落位文件 + 测试文件 + 黄金类（§0.3 每行标「复用未改」） |
| 3 | 写口 grep 审计证据 | §2.1 判据 2 命令与输出（空输出即证据）粘贴 |
| 4 | DECISIONS 新增行 | §7.4 |
| 5 | 遗留问题 | 逐条（无则明示「无」） |
| 6 | 免审直跑记录 | 见 §9 免审分支；两份 SPEC（SPEC-2、SPEC-5）全文进交付报告 |
| 7 | 重负载并发 3 留痕 | §5.2/§7.3 manifest 与日志 |

---

## 9. 门禁节（M1 → 下一步放行条件；对齐设计 §16.6 模式与 SPEC-4 §5 形态）

### 9.1 通过判定（全部勾选方放行）

- [ ] **A 组四块功能全绿**：§1.1–§1.5 五条 pytest 命令全 passed，最低用例名集齐；
- [ ] **不变量**：写口全词表扫描绿 + §2.1 独立 grep 审计空输出；幂等双跑 ≥8 例；确定性两用例绿 + M1 新函数零随机源 grep 空；
- [ ] **重放等价**：§3.1 单元五用例绿 + §3.2 专项脚本 `REPLAY_M1 PASS seeds=3 checkpoints>=3 ALL_DIFF_EMPTY`（3 种子×≥3 检查点 + 收尾全账本 + cells 物理表逐字段全等）；
- [ ] **黄金**：§4.1 判据 A（`37 passed` 基线）+ 判据 B（`sha256sum -c results/_golden_m0_baseline.sha256` 全 OK，digest 逐位不变）；突变门绿；M1 四新类 ≥12 例纳入回归与突变门；（sanctioned 重冻后）`--check` 恢复退出码 0；
- [ ] **矩阵**（如触发 §5.1）：`MATRIX_M1 162/162 9/9 PASS` + 位级复跑 `ALL_EQUAL=true` + 归档与 M0_GATE_FINAL.md 引用更新；
- [ ] **技术债四项**：§6.1–§6.4 逐项判据绿 + §6.5 全量 0 skip 全绿 + `SHA256_MANIFEST_ALL_OK`；
- [ ] **约束**：无网络 grep 空、依赖白名单闭集、并发 3 留痕、DECISIONS 同步；
- [ ] **全量 pytest**：`python -m pytest` → `N passed`、`0 failed`、`0 skipped`（N ≥ 339+新增）；
- [ ] **交付对账**：§8 七项齐备。

**人审物**（§16.6「人只审里程碑」）：门禁节勾选状态 + 交付报告 + results/ 证据文件；人审不重复执行机器判据，只审证据链完整性与跌标处置是否符合 §5.6。

### 9.2 停止判定（跌标即停，任一触发即停）

1. 任一判据命令非零退出 / 期望字面未出现，且按本清单无既定修复路径；
2. §5.6 触发：矩阵任一度量跌破目标 / `qualifies=false` / ⑨<100%；
3. M0 黄金 digest 变化且不属 §5.1 sanctioned 变更；
4. 技术债修复引入行为变更（§4.1 digest 不变判据红）；
5. 需要「静默改 golden、调参掩盖、放宽阈值、跳过判据」才能继续的任何情形。

停止后动作：原样记录触发项与现场输出，如实报告，**不得放行**；等待负责人明示决策（口径修订 / 返工 / 免审）后才可继续。

### 9.3 人工批准 / 免审直跑分支（父任务 ac_mv1s39t26l1to9he）

- **冻结门槛**：SPEC-2 与本文件定稿落盘前，`qswarm/` 与 `tests/` 不得出现任何 M1 新增或修改代码（含技术债修复）；验证方式见 §0 末条（文件时间对照，无 git 仓库以 mtime 为准）。
- **人工介入可用**（环境提供向负责人请示的通道）：两 SPEC 定稿落盘后**停下**，待负责人批准后放行实现子任务；批准记录（时刻/渠道/结论）进交付报告。
- **人工介入不可用**：执行侧**记录免审直跑决定**（记入 `DECISIONS.md` 五字段：日期/模块=SPEC-5/决策=免审直跑/理由=环境无人工介入通道/影响=门禁照常全量执行），且 **SPEC-2 与 SPEC-5 全文进交付报告**；门禁本身不因免审而降档——§9.1 全项照常执行。
- **分支可检判据**：交付报告出现以下之一——(i) 负责人批准记录，或 (ii) 免审直跑的 DECISIONS 行 + 两 SPEC 全文附件。二者缺一 = 门禁不通过。
- **执行记录（2026-10-10，规格门歧义拍板子任务 ac_mv1s9fkj2973617s 落笔）**：本执行环境判定为**人工介入不可用**——taskflow 自治执行会话（工具提权自动放行、无负责人实时审批回路；停止等待将使流水线停摆且会话被看门狗按静默挂起终止）→ 走分支 (ii) **免审直跑**，已按预设五字段记入 `DECISIONS.md`（模块=SPEC-5 / 决策=免审直跑 / 理由=环境无人工介入通道 / 影响=门禁照常全量执行）。**SPEC-2 与本文件全文须进交付报告**；门禁不因免审降档——§9.1 全项、§9.2 停止判定、§5.6 跌标即停照常生效；后续实现类子任务自本记录落盘起放行。

---

## 10. 判据索引（对照父任务验收标准）

| 父任务 ac | 本文件判据节 |
|---|---|
| ac_mv1s39t26l1to9he（SPEC 落盘/冻结/批准分支） | §0、§9.3 |
| ac_mv1s39t2j8yssfv6（技术债四项） | §6.1–§6.5 |
| ac_mv1s39t2jayfvi09（M1 功能实现纪律） | §1（A 组）、§2（B 组）、§4.3（黄金突变门）、§9.1 |
| ac_mv1s39t231p9yxml（digest 不变 + 重放等价 3×≥3） | §4.1、§3（C 组） |
| ac_mv1s39t2m47uutjo（矩阵复跑并发 3 / 162×9/9 / 跌标即停） | §5（E 组）、§9.2 |
| ac_mv1s39t2luneoms3（无网络/无新增依赖/并发 3/DECISIONS/交付） | §7（G 组）、§8（H 组） |

---

## 11. 决策记录（定稿拍板；五字段；已逐条同步 `docs/spec/DECISIONS.md`）

本文件定稿（2026-10-10）时拍板的歧义与最小惊讶项；SPEC-2 侧同轮拍板的 D19–D24 见该文件 §10.1（同样已同步 DECISIONS.md）。实现期新歧义按滚动协议记 DECISIONS.md，不回改本文件编号语义。

| 日期 | 模块 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| 2026-10-10 | SPEC-5 | D25：§5.4 定稿修正——`tune_round.py --round 52` 补显式 `--workers 3`（导出时点遗漏该旗标） | 该工具 `--workers` 缺省 4，照抄原命令即违反 §7.3「一切批量验证命令显式 --workers 3、禁 4 或更高」；并发 3 是父任务合同硬约束，定稿阶段修命令行优于实现期返工 | docs/spec/SPEC-5_m1_acceptance.md §5.4/§7.3；experiments/tune_round.py 代码不改（缺省值留作历史对照） |
| 2026-10-10 | SPEC-5 | D26：免审直跑——环境无人工介入通道（taskflow 自治执行会话，工具提权自动放行、无负责人实时审批回路），两 SPEC 定稿落盘后不停待批准，放行后续实现类子任务 | SPEC-5 §9.3 预设分支 (ii)；自治环境下等待人工批准将使流水线停摆且触发看门狗静默终止 | 门禁不降档：§9.1 全项 + §9.2 停止判定 + §5.6 跌标即停照常执行；**SPEC-2 与 SPEC-5 全文进交付报告**（§9.3 分支 (ii) 可检判据）；执行记录见 §9.3 末条 |

> 滚动协议：本文件为 M1 里程碑的验收快照；实现期发现判据不可执行（命令不存在、期望不可复现）时，按滚动协议记 `DECISIONS.md` 并在本文件对应节修订（修订留痕，不回改编号语义）；SPEC-4 §5 仍是 M0 门禁的原始记录，不被本文件取代。

<!-- ↑↑↑ SPEC-5_m1_acceptance.md 全文结束 ↑↑↑ -->

---

## 附录 D：DECISIONS.md M1 窗口新增行单行摘要（35 行）

> 自动提取自 `docs/spec/DECISIONS.md` 行 73–107（列：行号｜日期｜模块｜决策首句）；五字段全文见原文件。

| 行 | 日期 | 模块 | 决策（首句） |
|---|---|---|---|
| 73 | 2026-10-10 | SPEC-2 | SPEC-2 导出（docs/spec/SPEC-2_kernel.md，五块机制 |
| 74 | 2026-10-10 | SPEC-2 | 旋钮缺省纪律落地 |
| 75 | 2026-10-10 | SPEC-2 | findings.state 流转以 kind=finding 事件承载（payload.state=新状态；重放取每  |
| 76 | 2026-10-10 | SPEC-2 | DepositPayload 增可选字段 src_seq |
| 77 | 2026-10-10 | SPEC-2 | re_route 触发=内核确定性比对（产出 (family,type) 与契约 taxonomy_node 家族段不同 |
| 78 | 2026-10-10 | SPEC-2 | 预算降级以既有 SCALING 事件承载（pool="mode |
| 79 | 2026-10-10 | SPEC-2/kernel | D19 定稿 |
| 80 | 2026-10-10 | SPEC-2/kernel | D20 定稿 |
| 81 | 2026-10-10 | SPEC-2/kernel | D21 定稿 |
| 82 | 2026-10-10 | SPEC-2/kernel | D22 定稿补记 |
| 83 | 2026-10-10 | SPEC-2/kernel | D23 定稿 |
| 84 | 2026-10-10 | SPEC-2/ledger | D24 定稿 |
| 85 | 2026-10-10 | SPEC-5 | D25 定稿 |
| 86 | 2026-10-10 | SPEC-5 | D26 免审直跑 |
| 87 | 2026-10-10 | SPEC-4/tests | M0 审计技术债③④清偿 |
| 88 | 2026-10-10 | SPEC-5/kernel | M0 技术债①清偿口径拍板 |
| 89 | 2026-10-10 | SPEC-1/kernel | M0 审计技术债②清偿（SPEC-5 §6.2 二择一拍板取**补建**） |
| 90 | 2026-10-10 | SPEC-2/configs | params.yaml M1 旋钮回填（SPEC-2 §8.1 落地） |
| 91 | 2026-10-10 | SPEC-2/kernel | dispatch 实现切片定稿（委托契约与派单状态机子任务） |
| 92 | 2026-10-10 | SPEC-2/kernel | reroute 实现切片定稿 |
| 93 | 2026-10-10 | SPEC-2/kernel | hop_budget「计数与耗尽」口径拍板 |
| 94 | 2026-10-10 | SPEC-5/golden | M1 黄金并轨拍板 |
| 95 | 2026-10-10 | SPEC-2/budget | 预算实现切片定稿（kernel/budget.py） |
| 96 | 2026-10-10 | SPEC-2/ledger | D24 transaction() 由预算子任务落地 |
| 97 | 2026-10-10 | SPEC-5/golden | budget 黄金并轨 |
| 98 | 2026-10-10 | SPEC-2/kernel | CL1 定稿 |
| 99 | 2026-10-10 | SPEC-2/kernel | CL2 定稿 |
| 100 | 2026-10-10 | SPEC-2/kernel | CL3 定稿 |
| 101 | 2026-10-10 | SPEC-2/kernel | CL4 定稿 |
| 102 | 2026-10-10 | SPEC-2/kernel | CL5 定稿 |
| 103 | 2026-10-10 | SPEC-2/simulator | 仿真 verdict 归属接通真实聚类归并（§0.4/§2.1 落地，sanctioned 变更） |
| 104 | 2026-10-10 | SPEC-5/golden | 冻结工具恢复与重冻执行（承 §4.1 sanctioned 重冻条款） |
| 105 | 2026-10-11 | SPEC-5/终检 | 终检对账 |
| 106 | 2026-10-11 | SPEC-5/终检 | §2.3 判据 1 修订留痕 |
| 107 | 2026-10-11 | SPEC-5/终检 | §7.2 判据命令白名单补 3 个标准库根 `contextlib`/`functools`/`re`（M1 kerne |

---

## 附录 E：L1 处置记录（2026-10-11，方案 a 补实现）

- **裁定**：负责人明示按交付报告 §8-L1 建议项 (a) 处置——补实现 `submit()` 四分支
  + 7 例用例（契约面收口），不走判据修订路线。
- **实现落位**：`qswarm/kernel/dispatch.py` 新增 `SchemaRejected` 与 `submit()`
  统一代落账入口，四分支编排（全部复用既有圣化写口与实现，零第二套业务规则）：
  - **scout**：`candidates.triaged=1` + `guess_family/guess_type` 回填（candidates
    写口在本模块，§6.1）；`injection_flag=True` 按 §11.6-3 保持候选态并标高优先
    （机制形态 = `channel_score` 置 1.0，DECISIONS 2026-10-11）；投影回填无对应
    账本 kind（词表冻结）→ 零事件，同 DECISIONS 拍板行；
  - **auditor**：证据链内核侧校验（≥3 节点、含 source/sink、sanitizer 必出现、
    kind 词表，SPEC-1 §4.2 注记）不合格 → `SchemaRejected` 零落库；`finding_id`
    内核签发 `F-{date}-{seq:06d}`（D20 库内同前缀 max+1）；建档复用
    `open_finding` + `evidence_nodes` 落库；产出 (family[/type]) 与
    `contract.taxonomy_node` 错配 → 自动 `re_route_finding`（D12 细化不触发；
    scout_ids 取 `ctx.scouts`，D14）；
  - **reviewer**：纪律 2 守卫（reviewer=task.assignee == findings.reporter →
    ValueError）先于一切写；`votes` 行 + vote 事件（vote_id = `V-{finding}-{seq:03d}`）；
    票数达 `params.panel.SIZE` → 自动 `close_panel`（§1.5，同事务）；
  - **verifier**：§10.4 改判守卫（verified_fp 缺 blocking → 强制改判
    verify_error(error_kind=poc_fail)）；`verify_results` 行 + verify_result 事件 +
    §2.4 S2 状态映射（复用 `transition_finding`；verified_real 另 grade→L3）+
    `propagate_verdict(S2)`（source_seq=verify_result 事件 seq，D23 同源校验）；
    verify_error 零存款零投影（§6.2/§6.3）。
  - 前件守卫：任务不存在 / state≠'dispatched' → ValueError；全量代落账成功 →
    `tasks.state='done'`（§2.3，同事务投影更新）。装配面三处规格未定细节
    （谱系身份=task.assignee、finding_id/建档字段=shard_json 载体、scout 零事件
    与标高优先形态）记 DECISIONS 2026-10-11 行。
- **测试落位**：`tests/test_kernel_dispatch.py` 新增 7 例（用例名逐字对照 SPEC-5
  §1.2）：`test_submit_scout_triage_and_guess_backfill` /
  `test_submit_auditor_issues_id_and_evidence_chain_guard` /
  `test_submit_auditor_mismatch_auto_reroute` /
  `test_submit_reviewer_self_vote_rejected_and_auto_close_panel` /
  `test_submit_verifier_fp_without_blocking_forced_verify_error` /
  `test_submit_verifier_outcome_mapping_and_s2_deposit` /
  `test_submit_rejects_task_not_dispatched`；`tests/m1_harness.py` 补
  `seed_candidate` / `seed_cluster` / `candidate_row` 装配面。
- **门禁复核**：SPEC-5 §1.2 A 组最低用例名 **47/47** 集齐；写口审计
  （`scripts/audit_write_ports.py --strict`）candidates 写口落位圣化模块
  `kernel/dispatch.py`、零漂移 PASS；仿真窄口（§0.4）未动、M0 轨迹与黄金基线
  零影响；全量 pytest 复跑 **535 collected 全绿 rc=0**（`out/_m1_final/
  full_pytest_l1.log`，较终检 528 新增本处置 7 例）；零随机源/无网络 grep 零命中
  （新增代码仅 stdlib `json`）。
