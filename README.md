# qswarm

蜂群代码安全审查系统（设计文档 v0.3.4）。规格见 `docs/spec/`（SPEC-1 数据契约
为唯一真源；机制层仿真按 SPEC-4）。

## 布局

```
configs/            params.yaml（机制参数，M0 定稿 2026-10-09）/ taxonomy.yaml（家族树 §5.3）/ models.yaml（供给面参考形态，M2）
qswarm/contracts/   枚举 enums.py · DDL schema.sql · pydantic 契约 models.py · 配置加载 config.py
qswarm/kernel/      编排内核（SPEC-2 滚动导出后实现；账本唯一写口）
qswarm/prompts/     蜂角色提示词模板与输出解析（SPEC-3，M2；纯函数零 IO）
qswarm/exams/       种子题库装载/出卷/判分/考试播种（SPEC-6，M2；agents/hats/exam_* 四表唯一写口）
qswarm/supply/      供给适配层 models.yaml + UrllibAdapter（SPEC-6 §5，M2；全仓唯一网络落点）
qswarm/simulator/   M0 机制仿真器（SPEC-4：直接复用 kernel 实现）
scripts/build_seeds/ 题库三源构建流水线（M2，外网侧；缓存优先可离线重放）
tests/              pytest 测试（枚举逐字对照 / DDL 触发器 / 配置校验 / 契约模型 / 内核 / 考试播种）
```

## 命令

```bash
pip install -e '.[dev]'   # 或依赖已在环境中时跳过
pytest                    # 全部测试
python -c "import qswarm.contracts, qswarm.kernel, qswarm.simulator"
```

## 实验矩阵（SPEC-4 §3）

```bash
python -m qswarm.simulator.experiments --smoke    # 子集模式，分钟级验证管道
python -m qswarm.simulator.experiments --full     # §3 全矩阵 162 组 + 三项附加实验（≈1 小时，4 进程）
python -m qswarm.simulator.experiments --full --plan   # 只打印计划（507 runs）不执行
python -m qswarm.simulator.experiments --full --window-days 7  # 覆盖衰退滑动窗（M0 定稿参数，缺省=kernel 常量）
```

输出落 `results/experiments-<mode>/`：确定性载荷 `results.csv`（逐 run：参数维度 +
种子 + 9 项度量（⑥ 另含曲线读数 `regret_late_over_early`）+ 轨迹 digest）、
`summary.csv`（逐组 mean±std + ⑥ 组级列）、`starvation.csv`
（⑧ S2 饥饿传导）与 `results.json` 位级可复现（同命令同种子两次运行逐字节一致）；
`timing.csv` / `manifest.json` 为耗时与环境遥测。并行用 `--workers`（缺省
min(CPU,8)；受限沙箱自动降级 subprocess 分块执行，产出不变）。

调参与诊断工具（M0 定稿迭代用，正式结论以 `--full` + report 为准）：

```bash
python experiments/tune_round.py --round 1 --dims "cooldown=0,2,7 window=1,3,7"  # 自定义网格调参轮 → results/tune-round<R>/
python experiments/diagnose_gaps.py --seed 22 --N 6    # 单 run 晋升瓶颈解剖（四判据逐日首达/暗考/⑤窗数学）
```

考试播种（M2，SPEC-6；离线判据经 FakeAdapter，在线调用需 models.yaml `api_key_env` 就绪）：

```bash
python -m qswarm.exams.seeding --bank seeds/bank.yaml --models configs/models.yaml \
  --db out/m2-seeding/seeds.db --out out/m2-seeding             # 在线播种 → 存款 + 播种报告
python -m qswarm.exams.seeding --replay --cache out/m2-seeding/cache/ \
  --db out/m2-seeding/replay.db --out out/m2-seeding-replay     # 缓存重放（不经网络，S9 位级复现）
python3 scripts/build_seeds/build.py --out seeds --cache .cache-seeds   # 题库三源构建（外网侧，缓存优先）
```

每轮参数与指标记录见 `results/TUNING_LOG.md`（迭代证据链与缺口结论）。

## 仿真报告（SPEC-4 §4 sim_report）

```bash
python -m qswarm.simulator.report --from results/experiments-full --out out
```

生成 `out/sim_report.md`：§1 九项度量达成表（推荐参数组实测 / §2 目标 / 判定 /
差距；无全达标组时输出最接近组并逐项列差距，不静默选组）+ §2 参数敏感性
（`out/sensitivity.csv`：N/δ/T_BASE/exam 上限/verify_error/REVIEWER_X/TILT 臂/
路由臂各维度对关键指标的边缘均值±std）+ §3 Thompson 对照结论（同参数 TS vs
默认路由 regret）+ §4 params.yaml 定稿建议；`out/group_targets.csv` 为逐组
9 目标判定明细。

约定：SPEC 未覆盖处按最小惊讶原则实现并记 `docs/spec/DECISIONS.md`（五字段）。
