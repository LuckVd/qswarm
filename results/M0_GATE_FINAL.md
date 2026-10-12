# M0 门禁终检（SPEC-4 §5 · v2 机制定稿版，2026-10-09 · 2026-10-10 并发 3 复跑更新）

> **【2026-10-10 更新】** M1 编排内核（聚类归并）接线后，按滚动协议以
> `--workers 3` 复跑官方 `--full` 全矩阵并全量重出报告。旧产物**改名归档保留、
> 未删除**，新旧证据链并存：
>
> | 产物 | M0 定稿批（已归档） | M1 接线后复跑批（现行） |
> | --- | --- | --- |
> | 官方全矩阵（507 runs） | `results/experiments-full-m0gate-20261009/`（原名 `results/experiments-full/`，2026-10-09） | `results/experiments-full/`（2026-10-10） |
> | 报告三件（sim_report / sensitivity / group_targets） | `out/m0gate-arch-20261009/` | `out/`（重生成） |
>
> **结论提前**：复跑批 **162/162 参数组 9/9 全达标**（`out/group_targets.csv`
> qualifies 全真）、推荐组不变（`N06-D0.05-T0.60-E0.30-V0.20`，且与 M0 批同组
> 逐种子逐位一致）、⑨ 重放等价 507/507；§5 门禁四项维持全部通过。与 M0 批的
> 位级差异如实清单（含 digest 全量差异与根因）见 **§R**——严格 digest 级
> ALL_EQUAL 相对 M0 批不成立，属 M1 接线的既定形态差异，非门禁度量跌破，
> 未做任何调参掩盖。

## §R M1 接线后复跑记录（2026-10-10，workers=3）

命令（`results/full_run_w3.sh`，形态同 M0 `results/full_run_v2.sh`，仅
workers 4→3；日志 `results/full-run6-w3.log`）：

    python -m qswarm.simulator.experiments --full --workers 3 --out results/experiments-full
    python -m qswarm.simulator.report --from results/experiments-full --out out

`manifest.json`：argv=#[--full, --workers, 3, --out, results/experiments-full]，
workers=3，n_rows=507；**wall 4118s ≤ 2h，per-run max 54.4s ≤ 5 min**。

### R1 复跑批达标表（162/162 × 9/9）

- `out/group_targets.csv`：matrix 组 162 个，全部 `n_pass=9、n_unmet=0、
  n_nodata=0、qualifies=True`；九项目标逐项通过组数均为 162/162
  （`out/sim_report.md` §1 汇总表）。
- 推荐组 `N06-D0.05-T0.60-E0.30-V0.20`（3 种子，选择规则不变、选中不变），
  且该组与 M0 归档批**逐种子 9 度量 + 轨迹 digest 全部逐位一致**
  （`results/_rerun_w3_evidence/probeA/` 独立重跑复核）：

  | # | 度量 | 目标 | 实测 | 判定 |
  |---|---|---|---|---|
  | ① | promotion_precision | ≥ 0.90 | 0.9114 ± 0.0255 | ✅ |
  | ② | promotion_recall | ≥ 0.70 | 0.7000 ± 0.0433 | ✅（含等号） |
  | ③ | median_time_to_active | ≤ 200 | 150.0 | ✅ |
  | ④ | wrong_extinguish_rate | ≤ 0.05 | 0.0389 ± 0.0245 | ✅ |
  | ⑤ | degradation_response | ≤ 150 | 71.00 ± 12.73 | ✅ |
  | ⑥ | regret_narrowing | 收窄 | 0.5352 < 1 | ✅ |
  | ⑦ | cold_start_length | < 50 | 1.67 ± 0.58 | ✅ |
  | ⑧ | s2_starvation | ≤ 2× | 1.0000 | ✅ |
  | ⑨ | replay_equivalent | 100% | 1.0000 | ✅ |

- ⑨ 重放等价：复跑批 507/507 runs 全真（results.csv `replay_equivalent` 列）。

### R2 与 M0 归档批的位级对照（诚实清单，无调参掩盖）

比对工具：`experiments/_rerun_w3_matrix_compare.py`（compare_rerun 同语义的
全矩阵版：float 精确相等 / digest 字面全等 / 空值两侧同空，按 (group_id, seed)
对齐 486 主矩阵 runs）+ 官方 `experiments/compare_rerun.py`（逐组单组切批喂入）。

1. **轨迹 digest：507/507 不同。** 根因三条（逐条留痕）：
   - **配置指纹**：`canonical_trajectory()` 含 `params.model_dump()`，
     params.yaml 新增 3 个 M1 预算键（budget.FAST_CENTS / COST_PER_L2_CENTS /
     COST_PER_S2_CENTS，SPEC-2 §8.1）必然改变摘要；剔除 3 键的变体哈希仍不等于
     旧值（`experiments/_rerun_digest_rootcause.py`）→ 存在机制侧差异，见下条。
   - **M1 真实聚类接线**：昼末内核签发 `cluster_merge` 账本事件（金丝雀组实测
     30 日 1297 条）并位移全事件流 seq，`deposit/finding` 载荷携带簇归属——
     事件流字节变更，digest 随之全量变更。
   - **簇级结论传播**：传播展开的派生存款在 45/162 组的边界种子 run 上移动了
     存款流 → 度量读数变化（见第 2 条）。
2. **主矩阵度量：486 runs 中 441 逐位一致，45 runs 存在度量列差异**
   （差异列分布：`wrong_extinguish_rate`×36、`regret_total`×36、
   `regret_per_task`×36、`regret_late_over_early`×27；其余 5 项度量全矩阵
   逐位一致）。全部差异仍在门禁口径（组级跨种子均值判定）之内：
   **162/162 组 9/9 通过**，无任何目标跌破——负向分支未触发。
   （附注：per-run ④>0.05 的种子 run 在 M0 批同样存在——如推荐组 seed 11
   ④=0.0562 两批一致；门禁按组级均值判定，此非复跑引入。）
3. **附加实验 21 runs：基线切换**。附加臂（tilt/rx/ts，基线=params.yaml 缺省）
   的参数列由 M0 批的 N10/δ0.10/T0.70 变为 N6/δ0.05/T0.60——M0 定稿流程在
   官方矩阵**之后**把 params.yaml 基线定稿为推荐组坐标（归档快照
   `out/_m1_evidence/params.pre.yaml` 已含新基线），属 M0 定稿既定后果，
   非 M1 行为；该 21 runs 为论证回填/对照臂，不承载 §2 门禁判定。
4. **官方工具逐组结论**：`compare_rerun.py` × 162 组
   （`results/_rerun_w3_evidence/compare_all_groups.{csv,log}`）：
   **metrics_equal=117/162、digest_equal=0/162、ALL_EQUAL=0/162**。
   即对 M0 批的严格位级 ALL_EQUAL **不成立**，根因即上文 1-3 条；度量与
   门禁目标不受影响，如实落盘。
5. **复跑批自身确定性**：双探针经 `experiments/tune_round.py`（同一单元执行
   管道）独立重跑——推荐组（无度量差异组）与 N06-D0.05-T0.60-E0.50-V0.20
   （受传播影响组）各 3 种子，对复跑批同组行**逐位一致（含 digest）**
   （`results/_rerun_w3_evidence/probe{A,B}/`）。
6. **代码态钉定**：执行前后全树哈希 `results/_rerun_w3_evidence/
   code_state_{pre,post}.sha256`（30 文件，含 configs/params.yaml）逐字节一致；
   执行期间并行任务曾短暂触碰 `qswarm/kernel/replay.py`（mtime 变更），
   内容与快照终值一致（哈希复核通过）。

### R3 全量 pytest（本树新证）

`python -m pytest tests -q` → **528 collected，全部通过（rc=0，无 F/E/s）**
（`results/_rerun_w3_evidence/pytest_full.log`；M0 时点为 338 passed +
1 skipped，M1 各子任务新增测试后规模 528）。

## §5 门禁四项（逐项留痕）

1. **全部 9 项度量达目标（§2 表）——✅ 通过（M0 批与复跑批双证）**。
   M0 官方矩阵 **162/162 参数组全达标**（归档批 out 产物：
   `out/m0gate-arch-20261009/group_targets.csv` qualifies 全真）；M1 接线后
   workers=3 复跑批同样 **162/162 全达标**（`out/group_targets.csv`，§R-R1）。
   选择器推荐组（= params.yaml 坐标）`N06-D0.05-T0.60-E0.30-V0.20`（3 种子
   均值，两批 sim_report §1 数值一致，逐位复核见 §R-R1）：

   | # | 度量 | 目标 | 实测 | 判定 |
   |---|---|---|---|---|
   | ① | promotion_precision | ≥ 0.90 | 0.9114 ± 0.0255 | ✅ |
   | ② | promotion_recall | ≥ 0.70 | 0.7000 ± 0.0433 | ✅（含等号） |
   | ③ | median_time_to_active | ≤ 200 | 150.0 | ✅ |
   | ④ | wrong_extinguish_rate | ≤ 0.05 | 0.0389 ± 0.0245 | ✅ |
   | ⑤ | degradation_response | ≤ 150 | 71.00 ± 12.73 | ✅ |
   | ⑥ | regret_narrowing | 收窄 | 0.5352 < 1 | ✅ |
   | ⑦ | cold_start_length | < 50 | 1.67 ± 0.58 | ✅ |
   | ⑧ | s2_starvation | ≤ 2× | 1.0000 | ✅ |
   | ⑨ | replay_equivalent | 100% | 1.0000 | ✅ |

2. **重放等价 100%——✅ 通过**：M0 批 507 runs ⑨ 全真（归档
   `results/experiments-full-m0gate-20261009/results.csv`）；复跑批 507/507
   全真（`results/experiments-full/results.csv`）。环内逐 10 日校验 + 收尾全
   账本校验全过（test_sim_engine 回归钉住，§R-R3 全量 pytest rc=0）。

3. **params.yaml 定稿并提交（含定稿依据引用 sim_report.md）——✅ 通过**：
   configs/params.yaml 头注逐条引用 sim_report §1/§2/§4 与 group_targets.csv；
   坐标 = 选择器推荐组（§4-1：162 个全达标组中中位晋升时长最短者；通道主导下
   坐标不敏感，敏感性表 §2）。机制/世界旋钮定稿依据见 DECISIONS 2026-10-09 v2
   各条与 results/TUNING_LOG.md Round 9-51。M1 阶段新增 3 个预算键
   （SPEC-2 §8.1，缺省=规格逐字、仿真缺省不注入 BudgetPolicy）不触碰任何
   M0 仿真旋钮（§R-R2-3 基线证据、§R-R1 推荐组逐位一致）。

4. **内核黄金用例测试绿——✅ 通过**：M0 时点全量 pytest **338 passed,
   1 skipped**（含黄金回归、突变门、内核重放、§7.3 字面回归 spec_literal
   钉住、v2 通道测试 tests/test_m0_lane_fc.py 14 例）；M1 接线后本树复跑
   **528 collected 全绿 rc=0**（§R-R3，含 M1 新增 tests/test_kernel_cluster.py、
   test_sim_cluster_wiring.py、test_golden_cluster.py 等）。

## 推荐组复跑位级复核（历史：M0 定稿时点，2026-10-09）

`experiments/tune_round.py --round 51`（同管道同种子纪律，显式钉全部定稿旋钮）
→ `results/tune-round51/` → `experiments/compare_rerun.py` 对照官方矩阵行：
metrics_equal=true digest_equal=true ALL_EQUAL=true（逐种子 9 度量 + 轨迹
sha256 全部逐位一致，`results/_gate_compare_round51.log`）。该 digest 级等价
属 M0 同代码态复现；M1 接线后的 digest 级形态差异与根因见 §R-R2。

## 结论

**§5 门禁四项在 M0 定稿批与 M1 接线后 workers=3 复跑批上均全部通过，
M0 → M1 放行维持有效。** 复跑批相对 M0 批的 digest 级 ALL_EQUAL 不成立，
根因为 M1 真实聚类接线（cluster_merge 事件/簇级传播）、M1 预算键配置指纹与
M0 定稿基线切换（附加臂）三项既定形态差异（§R-R2），162/162 × 9/9 门禁度量
与重放等价 507/507 不受影响，无调参掩盖。
