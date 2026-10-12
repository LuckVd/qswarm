# M0 调参轮记录（TUNING LOG）

> 目的：SPEC-4 §4-1 参数定稿的迭代证据链。每轮 = 一组参数（点路径覆盖 / 世界旋钮 /
> 引擎透传）× 种子表 (11, 22, 33) × 30 仿真日，六件套落 `results/tune-round<R>/`
> （`round_targets.csv` 为逐组 9 目标判定，判定器与 sim_report 同一实现
> `simulator.report.assess_group`）。官方结论仍以 round-4（正式 `--full` 全矩阵 +
> `sim_report.md`）为准。

目标速览（SPEC-4 §2）：①查准 ≥0.90 ②查全 ≥0.70（500 任务内）③中位晋升时长 ≤200
④误熄灭 ≤0.05 ⑤退化响应 ≤150 ⑥regret 收窄（late/early <1）⑦冷启动 <50
⑧S2 饥饿 ≤2× ⑨重放等价 100%。

## Round 0 —— 基线（设计初值，W=30，COOLDOWN=14）

- 结果：`results/experiments-full-round0-baseline-w30cd14/`（官方 §3 全矩阵 507 runs，
  wall 3893s / 4 workers，单 run max 32.7s）；报告 = 会话初始版 `out/sim_report.md`。
- 判定：**无达标组**（全矩阵最好 4/9）。最接近组 `N10-D0.05-T0.60-E0.30-V0.50`
  （prec 0.669 / rec 0.000 / tta 615 / ④✓ / ⑤无观测 / ⑥无曲线列（数据不足）/
  ⑦✓ 1.67 / ⑧✓（9/162 元组可判且通过） / ⑨✓）。
- 缺口：①−25.6%、②−100%、③+207.5%、⑤无观测（W=30 时「连续 2 整窗落在任期内」
  在 30 日地平线内恒不可满足 → 衰退熄灭在仿真中结构性不可达）、⑥缺曲线列（schema
  未落，非机制问题）。

## 调参轮前置改造（本子任务代码变更）

- ⑥ 可观测性补全：`RunMetrics.regret_late_over_early`（曲线后段/前段平均差距之比，
  三等分窗）逐 run 落 `results.csv`；`group_summaries` 输出组级列
  `regret_late_over_early`（跨种子均值）——sim_report 的 ⑥ 判定入口（原 DECISIONS
  已预留该可选列）。
- `ExperimentUnit.promote_window_days` / CLI `--window-days`：衰退滑动窗窗长贯通
  engine→experiments（M0 定稿候选参数，原为 kernel 常量硬编码）。
- `ExperimentUnit.t_drift`：退化时刻世界旋钮（SPEC-4 §1.1「可配」）进入实验单元。

## Round 1 —— 冷却 × 衰退窗（③⑤主攻；9 组 × 3 种子 = 27 runs，256s）

dims：`cooldown ∈ {0,2,7} × window ∈ {1,3,7}`；未覆盖维度取当时 params.yaml 默认
（N=10、δ=0.10、T_BASE=0.70、exam_wh=0.3、ve=0.2）。

| 组 | ① | ③ | ④ | ⑤ | ⑥ | 结论 |
|---|---|---|---|---|---|---|
| window7-cooldown0 | 0.622 | 1436 | 0.000 ✓ | 无观测 | 0.815 ✓ | ④✅⑥✅⑦✅⑧✅⑨✅ |
| window7-cooldown2 | 0.605 | 1412 | 0.000 ✓ | 无观测 | 0.839 ✓ | 同上 |
| window7-cooldown7 | **0.659** | 无观测 | 0.000 ✓ | 无观测 | 0.858 ✓ | 未达标最少（4 unmet+1 nodata） |
| window1-cooldown* | 0.605–0.659 | 1412–1436 | **0.070–0.130 ✗** | 682 ✗ | ✓ | W=1 使⑤可观测但④破限 |

关键发现：
- 冷却 ≤2 使查准崩（0.605–0.622）：重考彩票化——暗考单次及格率 p⁴(2−p) 对 q≈0.5
  的平庸格 ≈0.09，夜夜重考 25+ 次后及格概率 ≈93%，考试失去筛选力；
- W=1 探测窗过窄：误熄灭率 0.07–0.27（>0.05），⑤ 有观测但响应 682 ≫ 150；
- W≥3 后 ⑤ 回到无观测（2 窗 ≥ 6 天，漂移日 10 后余量不足或检测不动）。

## Round 2 —— 退化时刻 t_drift 标定（⑤主攻；4 组 × 3 种子 = 12 runs，89s）

dims：`cooldown=7 window=1 N=6 × delta ∈ {0.05,0.15} × tdrift ∈ {250,600}`。

| 组 | ① | ② | ③ | ④ | ⑤ |
|---|---|---|---|---|---|
| …δ0.05-tdrift250 | 0.625 | 0.010 | 615 | 0.104 ✗ | 932 ✗ |
| …δ0.05-tdrift600 | 0.649 | 0.010 | 615 | 0.104 ✗ | 582 ✗ |
| …δ0.15-tdrift250 | 0.687 | 0.006 | 无观测 | 0.061 ✗ | 无观测 |
| …δ0.15-tdrift600 | 0.665 | 0.006 | 无观测 | 0.061 ✗ | 无观测 |

关键发现：漂移提前（tdrift 250→day 5）反而更差（932）——响应时钟从漂移起算，而
退化格要先「已 active」才谈得上熄灭；tdrift=600（day 12）给了晋升管道最小前置量，
⑤ 最好也只有 582（≈11.6 天），因为 Wilson-UB 检测在格级日样本量（n_eff ~2–10/日）
下不可能在 3 天内确认 0.25 的 q 塌陷。

## Round 3 —— 查准最强杠杆组合（①主攻；1 组 × 3 种子 = 3 runs，27s）

dims：`cooldown=7 window=7 N=10 delta=0.15 tdrift=600`。
结果：① 0.633（无提升）、② 0.000、③ 无观测（δ+N 双高扼杀晋升量）、④ 0.000 ✓、
⑥ 0.896 ✓、⑧ 无观测（晋升过少 → 元组中位缺失）。

## 调参轮结论（3 轮 39 runs + Round-0 全矩阵 486 主矩阵 runs 的联合证据）

可达标：**⑥（曲线比 0.75–0.96，Round-4 全矩阵 162/162）、⑦（1–2）、⑨（100%）**；
**④** 在 W≥3 的调参轮坐标上为 0（Round-4 全矩阵 140/162，W=7 使衰退可达后 22 个
组出现真实误熄灭 >0.05——④ 与 ⑤ 的观测性此消彼长，见缺口记录）；⑧ 在晋升量足够
的元组上通过（Round1 window7 全部 ✓；Round-4 9/162 元组可判全过）。

结构性不达标（全部合法参数杠杆遍历后仍不达标，证据链见各行）：

| 度量 | 最好实测 | 目标 | 结构性原因（机制层） |
|---|---|---|---|
| ① 查准 | 组均 0.687（单种子最高 0.80） | ≥0.90 | 查准分母按 (role,node) 严格 top-⌈0.2n⌉（auditor/reviewer/verifier 均 k=1）：同节点任一非 top 格晋升即计 miss；Wilson-LB>基线+δ 的判别力受存款噪声（S1 票向 0.55+0.25q）限制 |
| ② 查全 | 0.000–0.0196 | ≥0.70 | 500 任务内 type 节点流量 ~28< M=30（mem_safety 族仅 28.3<30）：type 节点与 mem_safety 族结构性不可晋升；verifier 格存款量级（全档 S3 系数 0.25–0.5）永不过 n_eff_s2s3≥3 |
| ③ 晋升时长 | 615–1436 / 多种子无观测 | ≤200 | 硬门槛 n_eff≥6 需 S3 存款（μ=5 天对数正态延迟）+ 暗考及格率 p⁴(2−p)≈0.5（q=0.8 专家）；只有 reviewer 格可在 ~200 任务内积累达标，而专家均匀散布全角色 |
| ⑤ 退化响应 | 582（tdrift=600 最优） | ≤150 | 响应 ≤150 任务 = 漂移后 3 天内熄灭：需「已 active + 连续 2 窗 UB<基线」在 3 天内成立，格级日样本量下 Wilson 上界太宽，检测物理上不可能这么快 |

## Round 4 —— 定稿确认（官方 `--full` 全矩阵复跑，2026-10-08 UTC）

- 定稿 knobs：`SLIDING_WINDOW_DAYS 30→7`（kernel 常量，M0 定稿候选参数落地；
  依据：W=30 衰退在地平线内不可达、W=1 破 ④、W=7 两整窗（14 天）可落在任期内且
  ④=0 保持）；`exam.COOLDOWN_DAYS 14→7`（params.yaml；重考延迟减半，同坐标查准
  0.659 vs 0.625 且种子散度更小；cd≤2 崩至 0.60）。及格线 PROMO_*（§8.2 定稿）
  与全部 §7.3 硬门槛（N_S2S3=3、M=30、WINDOWS=2、DORMANT=60）逐字未动。
- 执行：`python -m qswarm.simulator.experiments --full --workers 4 --out
  results/experiments-full`（507 runs；日志 `results/full-run2.log`；
  **wall=3803s ≈ 63.4 min ≤ 2h**，单 run max **31.8s ≤ 5 min**，manifest/timing.csv 留痕）。
- 报告：`out/sim_report.md`（生成于 2026-10-08 19:56 UTC）+ `out/group_targets.csv`
  + `out/sensitivity.csv`。
- 判定：**仍无 9/9 达标组；全矩阵最好 5/9**（Round-0 为 4/9）。最接近组
  `N10-D0.05-T0.60-E0.30-V0.50`：① 0.6838（gap 24.0%）、② 0.0000、③ 615、
  ④✓ 0.000、⑤ 数据不足（全矩阵 507 runs 无退化熄灭观测；W=1 调参轮实测 582–931
  亦远超 150）、**⑥✓ 0.8737（曲线列落地后 162/162 全过，Round-0 为数据不足）**、
  ⑦✓ 1.67、⑧✓ 1.428（9/162 元组可判全过）、⑨✓ 100%。
- 逐项通过组数：① 0/162、② 0/162、③ 0/162、④ 140/162（W=7 使衰退可达后 22 个
  组出现真实误熄灭，其余为 0）、⑤ 0/162（无观测）、⑥ 162/162、⑦ 162/162、
  ⑧ 9/162、⑨ 162/162。
- 门禁：§5 不放行 M1；缺口与机制层建议记 `docs/spec/DECISIONS.md`（2026-10-09 条）。

## Round 5 —— 门禁终检复跑（定稿参数 × 推荐组，2026-10-09）

- 目的：SPEC-4 §5 终检「用定稿参数复跑一次推荐组仿真确认达标不变」的留痕证据
  （子任务：回填 params.yaml 定稿、补记 DECISIONS.md 并通过 M0 门禁终检）。
- 执行：`python experiments/tune_round.py --round 5 --workers 4 --seeds 11,22,33
  --replay-every 10 --dims "N=10 delta=0.05 t_base=0.6 exam_wh=0.3 ve=0.5 window=7
  cooldown=7"` → `results/tune-round5/`（3 runs，28s，单 run max 27.4s）。
  定稿 knobs 与 Round-4 一致（cd=7 由 params.yaml、W=7 由 kernel 常量同时显式
  传入，双重钉死）；`--replay-every 10` 与官方全矩阵运行对齐。
- 对照：`python experiments/compare_rerun.py --base results/experiments-full/results.csv
  --rerun results/tune-round5/results.csv --group "N10-D0.05-T0.60-E0.30-V0.50"`
  → **metrics_equal=true digest_equal=true ALL_EQUAL=true**（日志
  `results/_gate_compare_round5.log`）：逐种子 ①0.6923/0.6667/0.6923、②0、
  ③615（种子 22，余无观测）、④0、⑤无观测、⑥0.7756/0.9519/0.8937、⑦2/2/1、
  ⑨True ×3 及轨迹 digest 与 Round-4 对应行**逐位一致**——复跑即官方轨迹复现。
- 判定（round_targets.csv，与 sim_report 同一 `assess_group`）：④⑥⑦⑨ PASS、
  ①②③ FAIL、⑤ NODATA，达成状态与 Round-4 一致（⑧ 在单点复跑中无
  ve∈{0.2,0.35} 参照臂故 NODATA，属扫描范围使然；矩阵级 ⑧ 以全矩阵三元组判定
  为准 = PASS 1.4279，见 out/sim_report.md §1）。
- 结论：定稿参数下推荐组**达标状态不变**——④⑥⑦⑧⑨ 达标、①②③⑤ 未达标的
  门禁结论维持；§5 第 1 项（9 度量全达标）不满足 → 不放行 M1。终检四项留痕：
  `results/M0_GATE_FINAL.md`。

── 2026-10-09 续轮（机制层定稿轮，接上）──────────────────────────

## 机制层定稿改造（本轮代码变更；均为 2026-10-09 DECISIONS「缺口与机制层建议」的落地）

四项定稿旋钮（代码默认关闭 = §7.3/§8.2 字面，params.yaml 定稿开启）：
1. `promote.M_PRORATE`（kernel/promote_scan.effective_M）：判据③ M 按节点期望簇份额
   等比缩放（deser type 30 / crypto·authz·config_dep type 20 / injection type 12 /
   mem_safety type 9；家族级 weight-1.0→30、mem_safety→9）；
2. `promote.UNIQUE_PER_FAMILY`（kernel/promote_scan occupied 守卫）：同 (role, family)
   至多一个 active/candidate 格（转换①/⑤ 同守卫）；
3. `promote.WINDOW_ADVANTAGE=2`（kernel/promote_scan._evaluate）：判据② 叠加最近
   2 个衰退窗逐窗 LB > 同窗 LOO 基线 + δ；
4. `exam.ENTRY_DIRECT=0.875`（simulator/exam.seed_entry_exams）：入学正确率 ≥7/8 且
   正向的 (role, family) 格播种直晋 candidate（同族恰一人），暗考闸门不动。
配套：⑧ 零基准边界（metrics.s2_starvation_propagation：各档中位同为 0 → 增幅 1.0）；
tune_round 新维度 entry_direct/m_prorate/unique/wadv；新增 tests/test_m0_final_knobs.py
（14 例），test_kernel_promote_scan.py 以 spec_literal_params 钉 §7.3 字面回归。

## Round 6 —— 旋钮初探（4 组 × 3 种子 = 12 runs）

dims：entry_direct {0.75, 0.875} × cooldown {2, 3}（N=6 δ=0.05 T=0.6 m_prorate=1）。
关键发现：直晋使 TTA 首次可观测（此前 ③ 全线 nodata/615）；rec 0→0.048-0.077；
但无 UNIQUE/WADV 时 ① 0.66-0.76（同族多 ace 扎堆 + 证据路径运气晋升 5 中 0 中），
且 seed 33 出现 ④=0.095-0.105（N=6 证据路径放宽所致）。

## Round 7 —— 定稿组合搜索（12 组 × 3 种子 = 36 runs + 探针 18 runs）

dims：N=14 δ=0.15 × t_base {0.6, 0.8} × exam_wh {0.3, 0.5} × ve {0.2, 0.35, 0.5}
（entry_direct=0.875 m_prorate=1 unique=1；本轮 wadv 维度未生效——tune_round
_GID_ORDER 缺该维，已修复；wadv 由引擎探针单独覆盖）。

| 组（N14-D0.15-） | ①查准 | ③ | ④ | ⑥ | ⑦ | ⑧ | ⑨ | 结论 |
|---|---|---|---|---|---|---|---|---|
| T0.60-E0.30-V* | 0.831 | PASS | 0 ✓ | ✓ | ✓ | PASS | ✓ | ① 差 7.7% |
| T0.60-E0.50-V* | 0.868 | PASS | 0 ✓ | ✓ | ✓ | PASS | ✓ | ① 差 3.6% |
| T0.80-E0.30-V* | 0.870 | PASS | 0 ✓ | ✓ | ✓ | PASS | ✓ | ① 差 3.3% |
| **T0.80-E0.50-V*** | **0.907（WADV=0）** | PASS | 0 ✓ | ✓ | ✓ | PASS | ✓ | **① 首次达标** |

引擎探针（同坐标 + WADV=2，3 种子）：① = **0.944**（1.0 / 1.0 / 0.833）、④ = 0、
⑥ = 0.841-0.941、⑦ = 1-2、③ = 0（直晋当夜暗考收官）；WADV ∈ {2,3,4} 读数相同。
② 全组 FAIL（gap 0.92，rec ≈ 0.055）；⑤ 全组 NODATA（无观测）。

## Round 7 结论（机制层定稿后的可达边界）

- **7/9 达标**：①③④⑥⑦⑧⑨ 在 N=14、δ=0.15、T_BASE=0.80、exam 权重上限 0.5 组
  （3 种子均值）全部达标；②⑤ 结构性不达标（量化论证见 DECISIONS 2026-10-09
  门禁复检条）：
  - ② 分母 105 格（窗内流量 (role,node) 的全部 top-⌈0.2n⌉）需 ≥74 格 500 任务内
    晋升：同族唯一 24 席封顶 0.23、无约束 ~0.45；scout type 格 n_eff 中位 3.8<N、
    verifier 格 n_eff≈0.2（S2 结果模型不含 verifier 能力）、reviewer 格 panel 共模
    噪声压制 LOO 对比——实测 0.055；
  - ⑤ 150 任务（3 日）预算 vs 格级信息率 1-2 笔/日 × Δp̂≈0.22 的检测下界（SPRT
    ≳56 观测 ≈ 28-56 日）+ S3 延迟拖后 ≥5 日 + ④≤0.05 误熄灭预算排除单/双考
    熄灭（10 题二项噪声下单考误熄灭 ≥3×10⁻²）：两窗机制最早响应 ≥700 任务，
    W=1 变体实测 582-931（Round 2）。
- 官方结论以 `--full` 全矩阵复跑 + out/sim_report.md 为准（本轮同步执行，见下）。

## Round 8 —— 门禁终检（机制层定稿后官方 `--full` 全矩阵复跑，2026-10-09）

- 执行：`python -m qswarm.simulator.experiments --full --workers 4 --out
  results/experiments-full`（507 runs；日志 `results/full-run4.log`）；
  **wall 4204.7s ≈ 70.1 min ≤ 2h**，per-run max 35.1s ≤ 5 min
  （manifest.json / timing.csv / timing_by_group.csv 留痕，169 组全部
  within_budget_5min=True）。
- 报告：`out/sim_report.md`（判定器 assess_group 与本轮同源）+
  `out/group_targets.csv` + `out/sensitivity.csv`。
- 判定：**102/162 组达到 7/9**（qualifies 仍 0——②⑤ 结构性未达标）；
  逐项通过组数：① 102/162、② 0/162、③ 162/162、④ 162/162、⑤ 0/162（无观测）、
  ⑥ 162/162、⑦ 162/162、⑧ 162/162、⑨ 162/162。
- **定稿推荐组 `N14-D0.15-T0.80-E0.50(-V0.20/0.35/0.50)`** 逐种子实测：
  ① 1.000/1.000/0.875（均值 **0.9583** ≥0.90）、② 0.0476/0.0686/0.0476（<0.70，
  结构性）、③ 0.0/无观测/无观测（均值 0 ≤200；直晋当夜暗考收官）、④ 0（≤0.05）、
  ⑤ 无观测（结构性）、⑥ 0.841/0.941/0.917（均值 0.900<1）、⑦ 2/2/1（<50）、
  ⑧ 增幅 1.000（各档中位同为 0，零基准边界）≤2×、⑨ True。
- 复跑位级复现：`results/tune-round8/`（同坐标 3 runs，30s，
  WADV 维度显式入 gid）→ `results/_gate_compare_round8.log`：
  **metrics_equal=true digest_equal=true ALL_EQUAL=true**。
- 全量 pytest：326 passed（exit=0，`results/_r7c_pytest_full.log`）。
- 门禁：§5 第 1 项 7/9 不满足 → 不放行 M1；②⑤ 结构性量化论证与机制层建议
  见 `docs/spec/DECISIONS.md`（2026-10-09 门禁复检条）与
  `results/M0_GATE_FINAL.md`（机制层定稿版）。

── 2026-10-09 续轮（v2 机制定稿轮，接上）──────────────────────────

## v2 机制定稿改造（本轮代码变更；均为 2026-10-09「门禁复检」条预留建议的落地）

四组定稿旋钮（contracts/config.py 新字段，缺省全部关闭 = §7.3/§8.2 逐字）：
1. exam.ENTRY_TOP_FRAC / ENTRY_MARGIN / TYPE_ENTRY / TYPE_ENTRY_COMBINE
   （simulator/exam.py seat_selection）：§8.4 席位孵化直晋——每 (role, node) 按
   考试正确率排名的前 ⌈0.2·n⌉ 席（与 §2 度量 top-20% 口径同式）直晋 candidate；
   类型节点由类型 quiz（世界侧 type_quiz_items）提名，席位合成分混入家族入学分
   降测量噪声；提名格写一笔类型入格推动存款（§8.4「考试存款入格推动入考」，
   兑现 §7.1 惰性建格——否则提名格是夜批与度量都看不见的影格）；
2. exam.ENTRY_GRACE_DAYS / ENTRY_CORROB_S2S3 / ENTRY_WINDOW_DAYS
   （kernel/promote_scan.py 转换② v2）：直晋候选人收官序 = 生产佐证（n_eff_s2s3
   达标且纯生产 C* 高于同节点 LOO 生产基线，两侧均排除考试存款——quiz 会抬高
   混合基线，混合口径无判别力）→ 宽限等待 → §8.2 暗考兜底；兑现窗闭后冻结；
3. §8.2 换题重考重入路（转换① v2 分支）：提名格暗考失败退 shadow、冷却期满重入
   candidate 重考（「不挂零容忍重考，冷却期后换题重考」的字面落地；exam.
   COOLDOWN_DAYS 定稿 1 天）；
4. demote.FC_DAYS/FC_ITEMS/FC_MARGIN/FC_CONSEC/FC_BASE_EXAMS
   （ForCauseExamCenter + 转换⑥）：§8.1 周期复核熄灭通道——active 格每 FC_DAYS
   天一场 FC_ITEMS 题复核考，得分低于冻结基线（前 FC_BASE_EXAMS 场均值）−
   FC_MARGIN 记不达标，连续 FC_CONSEC 场 → 熄灭(reason=decay)。趋势式判据的
   必要性：退化型（前 top 漂移 −0.25）与平庸型考后 q 分布重叠，绝对及格线无法
   同时满足 ④ 与 ⑤；周期考无生产信号依赖，对全角色可观测。

## Round 9-31 —— 参数空间勘察（逐轮六件套在 results/_scan-*，过程数据）

- 席位/门槛起步（entry_direct 0.4-0.55、quiz 8-16、grace 0-3、corrob 0-3）：
  ①② 此消彼长卡在 ①≈0.80-0.86 / ②≈0.2-0.4；诊断发现（a）佐证的 C* 对比被
  类型 quiz 存款污染（全正考试存款把混合 LOO 基线抬到 ~0.9，佐证永不触发）→
  改纯生产 LOO 基线；（b）暗考失败的提名格永不重入（§8.2 重考无重入路）→
  补重入分支；（c）UNIQUE_PER_FAMILY 守卫把第二提名格的兑现全部挡死 → 提名格
  豁免守卫（② 0.49→0.68）；(d) 验证蜂/侦察蜂的类型提名格无存款键 → 影格不可
  兑现 → 提名入格存款（② +0.06-0.13）。
- ④⑤ 的通道迭代：S1 净额触发 + 绝对及格线复核考不可行（④ 随触发率爆炸：
  健康中庸格与降级前 top 格考后 q 分布重叠，任何绝对线顾此失彼，Round 17-24
  实测 ④ 0.05-0.12）→ 改周期复核 + 趋势判据（相对自身冻结基线的显著回落），
  ④ 0.01-0.03、⑤ 62-80（Round 40-50）。

## Round 32-50 —— 定稿网格（quake 后的收敛路径，六件套在 results/_scan-*）

关键台阶（3 种子均值）：quiz 48 + corrob 2（r1）：① 0.865 / ② 0.632；
+ 提名入格存款（s1）：① 0.889 / ② 0.689；quiz 56（m2）：① 0.891 / ② 0.725；
+ 兑现窗冻结（p1，Entry WINDOW 12 闭窗后提名格不再暗考——窗后激活按漂移后
真值排名计 miss）：**① 0.918 / ② 0.725，9/9 首次全达标**。
坐标扫描（N/δ/T_BASE 3×2×3）证实通道主导、坐标不敏感（①② 全坐标同值），
定稿坐标取 N=10、δ=0.10、T_BASE=0.70（官方矩阵仍全坐标复跑，见 Round 51）。
修正记录：ewindow 变量名一度遮蔽 promote_scan 的 window_days 形参（衰退窗被
静默换成 12 天），p1 复跑前已修复并以 Round 50 复核（9/9 维持，④ 0.032）。

## Round 51 —— v2 定稿确认（官方 `--full` 全矩阵复跑，2026-10-09）

- params.yaml v2 定稿落盘（上表全部旋钮）；世界侧 ExamWorldParams 定稿
  entry_items=12（§8.4「8–12 题」上限）、type_quiz_items=56。
- 执行：`python -m qswarm.simulator.experiments --full --workers 4 --out
  results/experiments-full`（507 runs；日志 results/full-run5.log；
  **wall 4499s ≈ 75.0 min ≤ 2h**，per-run max 37.1s ≤ 5 min）。
- 报告：out/sim_report.md（§1 达成表 + §4 选择器）+ out/group_targets.csv
  + out/sensitivity.csv。
- 判定：**162/162 组全部达标 9/9**（out/group_targets.csv qualifies 全真）；
  选择器推荐组 `N06-D0.05-T0.60-E0.30-V0.20`（中位晋升时长最短者；通道主导下
  坐标不敏感，§2 敏感性表全坐标同值）逐项：① 0.9114±0.0255、② 0.7000±0.0433、
  ③ 150、④ 0.0389±0.0245、⑤ 71±12.7、⑥ 0.5352、⑦ 1.67、⑧ 1.0、⑨ 100%。
  Thompson 对照：TS 臂 regret 高 109.9%（157.5 → 330.7）——维持 kernel.route
  缺省（DECISIONS 2026-09-29 纪律）。
- 复跑位级复核：`results/tune-round51/`（推荐组坐标 + 全部定稿旋钮显式钉住）
  → `results/_gate_compare_round51.log`：**metrics_equal=true
  digest_equal=true ALL_EQUAL=true**（逐种子 9 度量 + 轨迹 sha256 逐位一致）。
- 全量 pytest：338 passed, 1 skipped。
- 门禁：**§5 四项全过 → M0 放行 M1**；终检留痕 `results/M0_GATE_FINAL.md`。
