# qswarm

蜂群代码安全审查系统（设计文档 v0.3.4）。规格见 `docs/spec/`（SPEC-1 数据契约
为唯一真源；机制层仿真按 SPEC-4）。

## 设计调整提案

[设计调整与验证方案（2026-10-22）](<docs/DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md>)：先证明专长稳定，再验证同预算路由收益。P0/P1 与 R1 的工程实现已落位，不代表蜂群效果或生产可信验证已获证实。当前按负责人授权推进 [R2 收口计划](<docs/R2_IMPLEMENTATION_PLAN_20261029.md>)：恢复、历史口径、执行/工件安全、可信授权与运行入口；P2–P4 的实际效果仍待校准与对照实验，不提前宣称通过。

## 布局

```
configs/            params.yaml（机制参数，M0 定稿 2026-10-09）/ taxonomy.yaml（家族树 §5.3）/ models.yaml（供给面参考形态，M2）
qswarm/contracts/   枚举 enums.py · DDL schema.sql · pydantic 契约 models.py · 配置加载 config.py
qswarm/kernel/      编排内核（SPEC-2 滚动导出后实现；账本唯一写口）
qswarm/prompts/     蜂角色提示词模板与输出解析（SPEC-3，M2；纯函数零 IO）
qswarm/exams/       种子题库装载/出卷/判分/考试播种（SPEC-6，M2；agents/hats/exam_* 四表唯一写口）
qswarm/supply/      供给适配层 models.yaml + UrllibAdapter（SPEC-6 §5，M2；全仓唯一网络落点）
qswarm/sandbox/     S2 沙箱执行面（SPEC-9，M5；双档 runner/env 缓存/PoC 生成 + trial 试跑 CLI）
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

## 统一入口与运行边界（R2）

源码环境可用 `python -m qswarm`，安装 wheel 后可用 `qswarm`：

```bash
python -m qswarm doctor                    # 不读密钥、不建账本、不调用模型
python -m qswarm doctor --check-tools      # Semgrep/Docker 启动探针
python -m qswarm trial --help              # process/container 离线试跑；零模型、零业务写入
python -m qswarm scan --help
python -m qswarm consult --help
python -m qswarm verify --help
```

生产三个入口均需既有账本、供给配置、显式 `--allow-model-calls --max-calls N`，不会自动创建假蜂或放宽 readiness。CLI 对四个模型角色强制检查资格；库级历史兼容默认仍保持不变。请求上限包含失败/重试，但**不是实际货币账单上限**；付费对照实验仍必须冻结协议并逐笔登记预算。`verify` 只走容器生产档；缺可信成对授权仍保守无结论。`trial --tier container` 只是容器离线执行，不写生产结果或奖励；`--trusted-repo` 仅用于明确审核的 process 开发输入，不是任意仓库的安全降级入口。

干净安装验收使用隔离构建/运行环境，既有项目虚拟环境不安装、不卸载：

```bash
.venv/bin/python scripts/verify_install.py
```

本地已有的第三方运行依赖离线复制到新的运行环境；sdist → wheel → 非 editable 安装 → 无关 cwd / 清 PYTHONPATH 导入与命令入口均实际执行。构建工件和指纹保留在 `out/r2-hardening/`，不声称执行了联网依赖解析或真实模型效果实验。

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
