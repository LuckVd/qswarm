"""simulator.experiments：SPEC-4 §3 实验矩阵 runner（一条命令 / 并行 / 可复现）。

一条命令跑完 §3 全矩阵并落盘（§6 实现约束：单实验 ≤ 5 分钟、总矩阵 ≤ 2 小时、
可并行；种子显式注入、结果可复现）::

    python -m qswarm.simulator.experiments --smoke   # 子集模式，分钟级验管道
    python -m qswarm.simulator.experiments --full    # §3 全矩阵 + 三项附加实验
    python -m qswarm.simulator.experiments --full --plan   # 只打印计划不执行

实验矩阵（SPEC-4 §3 逐字对应）：

- 主矩阵 ``matrix``：N ∈ {6,10,14}（promote.N）× δ ∈ {0.05,0.10,0.15}
  （promote.DELTA）× T_BASE ∈ {0.6,0.7,0.8}（tilt.T_BASE）× exam 权重上限 ∈
  {0.3,0.5}（ExamWorldParams.weight_high，§3 世界侧旋钮）× verify_error ∈
  {0.2,0.35,0.5}（VerdictParams.verify_error，§1.3 饥饿旋钮）——3³×2×3 = 162 组
  × 每组 3 个固定种子 = 486 次 run；指标取均值±std（``summary.csv``）；
- 附加单变量实验（同基线参数、同种子表、各自单变量）：
  - ``tilt_fixed_vs_adaptive``：TILT 固定（tilt.GAMMA=0）vs 自适应（缺省 GAMMA），
    §5.4 论证回填；
  - ``reviewer_x``：评审存款系数 REVIEWER_X ∈ {0.25, 0.5, 1.0}（待决#12 定标依据）；
  - ``thompson_control``：同参数路由换 ``kernel.route_thompson``，比 regret
    （§5.4 的实验承诺，DECISIONS「TS 作对照组而非替代」）。

机制参数扫描全部经 :func:`qswarm.simulator.engine.apply_overrides`（点路径覆盖 +
pydantic 全量重校验），世界侧旋钮走 engine.run 的显式注入——本模块不绕过任何
校验，也不重写任何机制（§0 复用纪律）。

输出物（一个输出目录，七件套 = 确定性载荷四件 + 遥测三件；
「确定性载荷 / 遥测」两分离是可复现验收的关键）：

======================  =====================================================
文件                    内容与确定性
======================  =====================================================
``results.csv``         逐 run 一行：参数维度列 + 种子 + 9 项度量 + 轨迹 digest。
                        **确定性载荷**（位级复现断言对象）。
``summary.csv``         逐参数组一行：各度量 3 种子 mean±std。确定性载荷。
``starvation.csv``      ⑧ S2 饥饿传导：主矩阵每个 (N,δ,T_BASE,exam) 元组在
                        verify_error 各档的中位晋升时长与增幅倍数。确定性载荷。
``results.json``        results.csv 的同构 JSON + 计划元数据（网格/种子表）。
                        确定性载荷。
``timing.csv``          逐 run 耗时（elapsed_s，参数维度列 + 种子全量随行）。
                        **遥测**——耗时天然随时钟波动，不参与位级复现断言
                        （但受 <5 min/实验预算断言）。
``timing_by_group.csv`` 逐参数组耗时聚合（mean/max/sum + 5 min 预算内标记）。
                        **遥测**——同上，不参与位级复现断言。
``manifest.json``       命令行/时间戳/wall/进程数等运行环境遥测。非确定性。
======================  =====================================================

并行与可复现（§6）：``--workers`` 限定进程数（缺省 min(cpu, 8)，1 = 串行）；
run 单元是纯函数（一次 run = 一个独立种子化闭环 + 独立临时账本），行内容与
进程数/完成顺序无关——输出前按计划序（unit_id）排序、组内按种子排序后再聚合，
同命令同种子两次运行（任意进程数）确定性载荷逐位一致。

种子表显式固定（§3「3 个随机种子重复」）：full = (11, 22, 33)、smoke = (11, 22)，
写入 results.json 元数据与每行 ``seed`` 列；``--seeds`` 可显式覆盖（非缺省口径）。
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Any, Mapping, Sequence

from qswarm.kernel import SLIDING_WINDOW_DAYS, route_thompson

from .engine import run
from .exam import ExamWorldParams
from .metrics import aggregate_seeds, s2_starvation_propagation
from .report import REGRET_RATIO_COL          # ⑥ 组级可选列名（单一事实源在 report）
from .verdict import VerdictParams
from .world import WorldHiddenParams

__all__ = [
    "FULL_GRID",
    "FULL_N_DAYS",
    "FULL_SEEDS",
    "GROUP_TIMING_COLS",
    "METRIC_COLS",
    "PARAM_COLS",
    "RESULT_COLS",
    "RUN_BUDGET_S",
    "SMOKE_GRID",
    "SMOKE_N_DAYS",
    "SMOKE_SEEDS",
    "ExperimentUnit",
    "additional_units",
    "build_plan",
    "execute_plan",
    "group_summaries",
    "group_timing_rows",
    "main",
    "matrix_units",
    "run_experiments",
    "s2_starvation_rows",
    "write_group_timing",
    "write_outputs",
]


# ── §3 网格与种子表（显式固定，勿改；改即换实验口径）────────────

#: 主矩阵网格（维度 → 取值；键序即 group_id 与遍历的规范序）。
FULL_GRID: dict[str, tuple[float, ...]] = {
    "N": (6, 10, 14),                    # promote.N（§3）
    "DELTA": (0.05, 0.10, 0.15),         # promote.DELTA（§3）
    "T_BASE": (0.6, 0.7, 0.8),           # tilt.T_BASE（§3）
    "exam_weight_high": (0.3, 0.5),      # ExamWorldParams.weight_high（§3 exam 权重上限）
    "verify_error": (0.2, 0.35, 0.5),    # VerdictParams.verify_error（§1.3/§3）
}

#: smoke 子集网格：每个维度取两档端点（5 维全保留，验证管道而非产结论）。
SMOKE_GRID: dict[str, tuple[float, ...]] = {
    "N": (6, 14),
    "DELTA": (0.05, 0.15),
    "T_BASE": (0.6, 0.8),
    "exam_weight_high": (0.3, 0.5),
    "verify_error": (0.2, 0.5),
}

#: 网格维度 → 机制参数点路径（世界侧两维不经 overrides，走 engine 显式注入）。
_GRID_OVERRIDES = {
    "N": "promote.N",
    "DELTA": "promote.DELTA",
    "T_BASE": "tilt.T_BASE",
}

#: 种子表（§3「3 个随机种子重复」；smoke 减半验管道）。
FULL_SEEDS: tuple[int, ...] = (11, 22, 33)
SMOKE_SEEDS: tuple[int, ...] = (11, 22)

#: 仿真日数：full 取 SPEC-4 预算口径（30 日 ≈ 1500 任务，§6 单实验 ≤5 min），
#: smoke 减半（分钟级跑完全管道）。
FULL_N_DAYS = 30
SMOKE_N_DAYS = 8

#: ⑧ S2 饥饿传导判定口径（§2：中位晋升时长增幅 ≤ 2×，基准档 verify_error=0.2）。
S2_REFERENCE_VE = 0.20
S2_LIMIT = 2.0

#: 附加单变量实验的臂定义（§3）。
REVIEWER_X_POINTS: tuple[float, ...] = (0.25, 0.5, 1.0)   # 待决#12 定标扫描
TILT_GAMMA_FIXED = 0.0                                    # TILT 固定臂：GAMMA=0
ROUTER_ARMS: tuple[str, ...] = ("kernel.route", "route_thompson")

#: 路由名 → 路由器（None = kernel.route 缺省臂；TS 对照臂注入 kernel.route_thompson）。
_ROUTERS: dict[str, Any] = {"kernel.route": None, "route_thompson": route_thompson}

#: 实验名（CSV ``experiment`` 列的规范取值，顺序 = 计划顺序）。
EXP_MATRIX = "matrix"
EXP_TILT = "tilt_fixed_vs_adaptive"
EXP_REVIEWER = "reviewer_x"
EXP_THOMPSON = "thompson_control"

# ── 输出 schema（列序固定 = 位级复现的一部分）────────────────────

#: 9 项度量列（= RunMetrics 字段序；⑧ 为跨运行派生，见 starvation.csv）。
#: regret_late_over_early 是 ⑥ 的曲线读数（后段/前段平均差距之比）——sim_report
#: 的 ⑥ 判定仅认组级可选列（DECISIONS 2026-10-08），六件套自本版起逐 run 落此列。
METRIC_COLS: tuple[str, ...] = (
    "promotion_precision",        # ① 晋升查准
    "promotion_recall",           # ② 晋升查全
    "median_time_to_active",      # ③ 中位晋升时长
    "wrong_extinguish_rate",      # ④ 误熄灭率
    "degradation_response",       # ⑤ 退化响应
    "regret_total",               # ⑥ 探索 regret（终点累计）
    "regret_per_task",            # ⑥' 终点平均（单调收窄读数）
    "regret_late_over_early",     # ⑥'' 曲线后段/前段平均差距之比（严格 < 1 收窄）
    "cold_start_length",          # ⑦ 冷启动长度
    "replay_equivalent",          # ⑨ 重放等价（bool）
)

#: 参数维度列（全矩阵扫描维度 + 附加实验变量 + 运行标识）。
PARAM_COLS: tuple[str, ...] = (
    "experiment", "arm", "group_id", "seed", "n_days",
    "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error",
    "reviewer_x", "tilt_gamma", "router", "window_days",
)

_DIGEST_COL = "digest"
_UNIT_COL = "unit_id"
_ELAPSED_COL = "elapsed_s"

#: results.csv / results.json 的列序（确定性载荷）。
RESULT_COLS: tuple[str, ...] = PARAM_COLS + METRIC_COLS + (_DIGEST_COL,)


# ── 实验计划 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExperimentUnit:
    """最小可执行实验单元：一次 ``(参数组, 种子)`` 的 engine.run。

    只携带可 pickle 的原语（并行 worker 重建 run 输入）；机制参数覆盖走点路径
    ``overrides``，世界侧两维（exam 权重上限 / verify_error）走 engine 显式注入。
    """

    unit_id: int                  # 计划内规范序（输出排序键，与进程数无关）
    experiment: str               # matrix / tilt_fixed_vs_adaptive / reviewer_x / thompson_control
    arm: str                      # 臂标签（matrix 恒 'base'）
    group_id: str                 # 参数组标识（experiment 内唯一、规范可读）
    seed: int                     # 主种子（引擎内派生全部子流）
    n_days: int
    overrides: tuple[tuple[str, Any], ...]   # 机制参数点路径覆盖（apply_overrides 口径）
    exam_weight_high: float       # 世界侧旋钮：exam 权重上限
    verify_error: float           # 世界侧旋钮：S2 出错率
    router: str                   # 'kernel.route' | 'route_thompson'
    replay_every: int = 10        # 环内重放校验间隔（0 关闭；透传 engine.run）
    promote_window_days: float = SLIDING_WINDOW_DAYS   # 衰退滑动窗窗长（M0 定稿候选参数）
    t_drift: int | None = None    # 世界旋钮：退化漂移时刻（None = WorldHiddenParams 缺省）
    type_quiz_items: int | None = None   # 世界旋钮：类型级孵化 quiz 题数（None = 缺省）
    entry_items: int | None = None       # 世界旋钮：入学考题数（None = 缺省 8；§8.4 域 8–12）


def _gid_for(N: float, DELTA: float, T_BASE: float, exam: float, ve: float) -> str:
    """主矩阵参数组标识（定宽规范格式，字典序 = 计划序）。"""
    return f"N{N:02d}-D{DELTA:.2f}-T{T_BASE:.2f}-E{exam:.2f}-V{ve:.2f}"


def matrix_units(
    grid: Mapping[str, Sequence[float]],
    seeds: Sequence[int],
    n_days: int,
    *,
    first_unit_id: int = 0,
    replay_every: int = 10,
    window_days: float = SLIDING_WINDOW_DAYS,
) -> list[ExperimentUnit]:
    """主矩阵单元：网格全积 × 种子表（组间参差、组内仅种子不同）。"""
    combos = list(product(
        grid["N"], grid["DELTA"], grid["T_BASE"],
        grid["exam_weight_high"], grid["verify_error"],
    ))
    units: list[ExperimentUnit] = []
    uid = first_unit_id
    for n, delta, t_base, exam_wh, ve in combos:
        gid = _gid_for(n, delta, t_base, exam_wh, ve)
        overrides = (
            ("promote.N", int(n)),
            ("promote.DELTA", float(delta)),
            ("tilt.T_BASE", float(t_base)),
        )
        for seed in seeds:
            units.append(ExperimentUnit(
                unit_id=uid, experiment=EXP_MATRIX, arm="base", group_id=gid,
                seed=int(seed), n_days=n_days, overrides=overrides,
                exam_weight_high=float(exam_wh), verify_error=float(ve),
                router="kernel.route", replay_every=replay_every,
                promote_window_days=float(window_days),
            ))
            uid += 1
    return units


def additional_units(
    seeds: Sequence[int],
    n_days: int,
    *,
    first_unit_id: int = 0,
    replay_every: int = 10,
    window_days: float = SLIDING_WINDOW_DAYS,
) -> list[ExperimentUnit]:
    """三项附加单变量实验（§3；基线参数 + 单变量，种子表与主矩阵一致）。"""
    units: list[ExperimentUnit] = []
    uid = first_unit_id

    # ① TILT 固定 vs 自适应（§5.4 论证回填）：唯一变量 = tilt.GAMMA
    tilt_arms = (
        ("adaptive", ()),  # GAMMA 取 params.yaml 缺省（自适应）
        ("fixed", (("tilt.GAMMA", TILT_GAMMA_FIXED),)),  # 固定 = 温度恒 T_BASE
    )
    for arm, ov in tilt_arms:
        for seed in seeds:
            units.append(ExperimentUnit(
                unit_id=uid, experiment=EXP_TILT, arm=arm,
                group_id=f"tilt-{arm}", seed=int(seed), n_days=n_days,
                overrides=ov, exam_weight_high=0.5, verify_error=0.20,
                router="kernel.route", replay_every=replay_every,
                promote_window_days=float(window_days),
            ))
            uid += 1

    # ② 评审存款系数 REVIEWER_X ∈ {0.25, 0.5, 1.0}（待决#12）
    for x in REVIEWER_X_POINTS:
        arm = f"rx{x:.2f}"
        for seed in seeds:
            units.append(ExperimentUnit(
                unit_id=uid, experiment=EXP_REVIEWER, arm=arm,
                group_id=f"rx-{x:.2f}", seed=int(seed), n_days=n_days,
                overrides=(("deposits.REVIEWER_X", float(x)),),
                exam_weight_high=0.5, verify_error=0.20,
                router="kernel.route", replay_every=replay_every,
                promote_window_days=float(window_days),
            ))
            uid += 1

    # ③ Thompson sampling 对照（同参数换路由比 regret，§5.4 实验承诺）
    for name in ROUTER_ARMS:
        for seed in seeds:
            units.append(ExperimentUnit(
                unit_id=uid, experiment=EXP_THOMPSON, arm=name,
                group_id=f"ts-{name}", seed=int(seed), n_days=n_days,
                overrides=(), exam_weight_high=0.5, verify_error=0.20,
                router=name, replay_every=replay_every,
                promote_window_days=float(window_days),
            ))
            uid += 1

    return units


def build_plan(
    mode: str,
    *,
    seeds: Sequence[int] | None = None,
    n_days: int | None = None,
    replay_every: int = 10,
    window_days: float = SLIDING_WINDOW_DAYS,
) -> tuple[list[ExperimentUnit], dict[str, Any]]:
    """按模式构建实验计划；返回 (units, 元数据)。

    ``mode`` ∈ {"smoke", "full"}；``seeds``/``n_days`` 缺省取该模式的显式常量
    （种子表显式固定，§3/§6）。``window_days`` 为衰退滑动窗窗长（M0 定稿候选
    参数，kernel 缺省常量的显式覆盖口）。元数据含网格、种子表、组数——进 results.json。
    """
    if mode == "full":
        grid, default_seeds, default_days = FULL_GRID, FULL_SEEDS, FULL_N_DAYS
    elif mode == "smoke":
        grid, default_seeds, default_days = SMOKE_GRID, SMOKE_SEEDS, SMOKE_N_DAYS
    else:
        raise ValueError(f"mode 必须是 'smoke' 或 'full'：{mode!r}")
    if seeds is None:
        seeds = default_seeds
    seeds = tuple(int(s) for s in seeds)
    n_days = int(default_days if n_days is None else n_days)

    n_groups = (
        len(grid["N"]) * len(grid["DELTA"]) * len(grid["T_BASE"])
        * len(grid["exam_weight_high"]) * len(grid["verify_error"])
    )
    matrix = matrix_units(grid, seeds, n_days, replay_every=replay_every,
                          window_days=window_days)
    extra = additional_units(seeds, n_days, first_unit_id=len(matrix),
                             replay_every=replay_every, window_days=window_days)
    meta = {
        "mode": mode,
        "grid": {k: [float(v) for v in vals] for k, vals in grid.items()},
        "seeds": [int(s) for s in seeds],
        "n_days": n_days,
        "n_matrix_groups": n_groups,
        "n_additional_runs": len(extra),
        "n_units": len(matrix) + len(extra),
        "s2_reference_ve": S2_REFERENCE_VE,
        "s2_limit": S2_LIMIT,
        "replay_every": int(replay_every),
        "window_days": float(window_days),
    }
    return matrix + extra, meta


# ── 执行（并行且与进程数无关）────────────────────────────────────


def _execute_unit(unit: ExperimentUnit) -> dict[str, Any]:
    """worker 侧纯函数：一次 run → 一行记录（参数 + 9 度量 + digest + 耗时）。"""
    t0 = time.perf_counter()
    res = run(
        seed=unit.seed,
        n_days=unit.n_days,
        overrides=dict(unit.overrides),
        verdict=VerdictParams(verify_error=unit.verify_error),
        exam=ExamWorldParams(
            weight_high=unit.exam_weight_high,
            **({"type_quiz_items": int(unit.type_quiz_items)}
               if unit.type_quiz_items is not None else {}),
            **({"entry_items": int(unit.entry_items)}
               if unit.entry_items is not None else {}),
        ),
        router=_ROUTERS[unit.router],
        replay_every=unit.replay_every,
        promote_window_days=unit.promote_window_days,
        hidden=(WorldHiddenParams(t_drift=unit.t_drift)
                if unit.t_drift is not None else None),
    )
    elapsed = time.perf_counter() - t0
    row: dict[str, Any] = {
        "experiment": unit.experiment,
        "arm": unit.arm,
        "group_id": unit.group_id,
        "seed": unit.seed,
        "n_days": res.n_days,
        # 参数维度列一律读「生效后」值（覆盖经 pydantic 重校验，单一事实源）
        "N": res.params.promote.N,
        "DELTA": res.params.promote.DELTA,
        "T_BASE": res.params.tilt.T_BASE,
        "exam_weight_high": res.exam.weight_high,
        "verify_error": res.verdict.verify_error,
        "reviewer_x": res.params.deposits.REVIEWER_X,
        "tilt_gamma": res.params.tilt.GAMMA,
        "router": unit.router,
        "window_days": float(unit.promote_window_days),
    }
    row.update(res.metrics_record())          # 9 项度量（⑨ = replay_equivalent）
    row[_DIGEST_COL] = res.digest             # 轨迹 sha256（复现性指纹）
    row[_UNIT_COL] = unit.unit_id
    row[_ELAPSED_COL] = elapsed
    return row


#: 子进程降级执行器的 worker 引导代码：sys.path 修复 → 逐单元执行 → JSONL 落盘。
#: 协调只走普通文件（不走 POSIX 信号量/共享内存），在禁用 /dev/shm 的受限环境
#: （seccomp/landlock 沙箱）也可并行；语义与进程池路径完全一致。
_CHILD_BOOTSTRAP = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from qswarm.simulator.experiments import _run_units_to_jsonl
_run_units_to_jsonl(sys.argv[2], sys.argv[3])
"""


def _run_units_to_jsonl(units_json: str, out_path: str) -> None:
    """子进程侧：执行一批单元并把行记录写 JSONL（父进程侧 ：func:`_units_payload`）。"""
    with open(out_path, "w", encoding="utf-8") as f:
        for unit in _units_from_json(units_json):
            f.write(json.dumps(_execute_unit(unit)) + "\n")


def _units_from_json(units_json: str) -> list[ExperimentUnit]:
    """JSON → 单元列表（overrides 的 tuple 在 JSON 往返后是 list，_execute_unit 兼容）。"""
    return [
        ExperimentUnit(
            **{**u, "overrides": [tuple(pair) for pair in u["overrides"]]}
        )
        for u in json.loads(units_json)
    ]


def _units_payload(units: Sequence[ExperimentUnit]) -> str:
    """单元列表 → JSON 载荷（子进程命令行传参；dataclass 字段全为 JSON 原语）。"""
    return json.dumps([dataclasses.asdict(u) for u in units])


def _execute_subprocess(
    units: Sequence[ExperimentUnit],
    workers: int,
    t0: float,
) -> list[dict[str, Any]]:
    """subprocess 分块并行（无 POSIX 信号量依赖的降级执行器）。

    单元静态均分成 ``workers`` 个连续块，每块一个真 OS 子进程顺序执行，行记录
    经各自 JSONL 文件回传父进程合并。单元同质（同 n_days 量级）、块间无依赖，
    静态分块的负载均衡与确定性（行按 unit_id 重排）都不受影响。
    """
    import qswarm
    import shutil
    import subprocess

    pkg_root = str(Path(qswarm.__file__).resolve().parents[1])
    n_chunks = min(max(1, workers), len(units))
    bounds = [len(units) * i // n_chunks for i in range(n_chunks + 1)]
    workdir = Path(tempfile.mkdtemp(prefix="qswarm-exp-"))
    try:
        procs: list[subprocess.Popen] = []
        outs: list[Path] = []
        for c in range(n_chunks):
            chunk = units[bounds[c]: bounds[c + 1]]
            if not chunk:
                continue
            out_path = workdir / f"rows-{c:02d}.jsonl"
            outs.append(out_path)
            procs.append(subprocess.Popen(
                [sys.executable, "-c", _CHILD_BOOTSTRAP,
                 pkg_root, _units_payload(chunk), str(out_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            ))
        for i, proc in enumerate(procs):
            _, err = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"子进程 worker 失败（exit={proc.returncode}）："
                    f"{(err or b'').decode('utf-8', 'replace')[-2000:]}"
                )
            print(f"[experiments] chunk {i + 1}/{len(procs)} done "
                  f"({time.perf_counter() - t0:.0f}s)", flush=True)
        rows: list[dict[str, Any]] = []
        for out_path in outs:
            for line in out_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def execute_plan(
    units: Sequence[ExperimentUnit],
    *,
    workers: int = 1,
    log_every: int | None = None,
) -> list[dict[str, Any]]:
    """执行计划并返回按 unit_id 排序的行（内容与进程数/完成顺序/执行器无关）。

    ``workers`` ≤ 1 串行；>1 并行（每 run 独立种子化闭环 + 独立临时账本，无共享
    状态）：优先 :class:`ProcessPoolExecutor`；构造期 PermissionError（受限沙箱
    禁用 POSIX 信号量，如无 /dev/shm 写权）时自动降级 subprocess 分块执行器，
    两路径产出逐位一致。``log_every`` 缺省每 ~10% 进度打一行（长矩阵的心跳）。
    """
    if not units:
        return []
    units = list(units)
    total = len(units)
    if log_every is None:
        log_every = max(1, total // 10)
    t0 = time.perf_counter()

    if workers <= 1:
        rows = []
        for i, unit in enumerate(units, 1):
            rows.append(_execute_unit(unit))
            if i % log_every == 0 or i == total:
                print(f"[experiments] {i}/{total} runs done "
                      f"({time.perf_counter() - t0:.0f}s)", flush=True)
    else:
        try:
            pool = ProcessPoolExecutor(max_workers=workers)
        except PermissionError:
            pool = None   # 受限沙箱（/dev/shm 不可写）→ subprocess 降级
        if pool is not None:
            with pool:
                rows = []
                done = 0
                futures = {pool.submit(_execute_unit, u): u for u in units}
                for fut in as_completed(futures):
                    rows.append(fut.result())   # 单元失败 → 立即抛出（失败快停）
                    done += 1
                    if done % log_every == 0 or done == total:
                        print(f"[experiments] {done}/{total} runs done "
                              f"({time.perf_counter() - t0:.0f}s)", flush=True)
        else:
            print(f"[experiments] ProcessPool 不可用（受限沙箱），"
                  f"降级 subprocess 分块执行 workers={workers}", flush=True)
            rows = _execute_subprocess(units, workers, t0)

    rows.sort(key=lambda r: r[_UNIT_COL])   # 规范序：输出与完成顺序解耦
    return rows


# ── 聚合（§3「3 种子取均值±std」+ ⑧ 派生）────────────────────────


def group_summaries(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """逐参数组聚合：各度量跨种子 mean±std（组内按种子序，浮点加法序固定）。"""
    order: list[tuple[Any, ...]] = []
    groups: dict[tuple, list[Mapping[str, Any]]] = {}
    for row in rows:                                    # rows 已按 unit_id 排序
        key = (row["experiment"], row["arm"], row["group_id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    summaries: list[dict[str, Any]] = []
    for key in order:
        members = sorted(groups[key], key=lambda r: r[_UNIT_COL])
        agg = aggregate_seeds([{m: r[m] for m in METRIC_COLS} for r in members])
        head = members[0]
        out: dict[str, Any] = {
            "experiment": head["experiment"],
            "arm": head["arm"],
            "group_id": head["group_id"],
            "n_days": head["n_days"],
            "n_seeds": len(members),
            "N": head["N"], "DELTA": head["DELTA"], "T_BASE": head["T_BASE"],
            "exam_weight_high": head["exam_weight_high"],
            "verify_error": head["verify_error"],
            "reviewer_x": head["reviewer_x"], "tilt_gamma": head["tilt_gamma"],
            "router": head["router"], "window_days": head.get("window_days"),
        }
        for m in METRIC_COLS:
            out[f"{m}_mean"] = agg[m].mean
            out[f"{m}_std"] = agg[m].std
        # ⑥ 组级可选列（sim_report 判定的唯一入口）：跨种子均值（None 跳过，
        # 全 None → 组级 None = 数据不足）。
        out[REGRET_RATIO_COL] = agg[REGRET_RATIO_COL].mean
        summaries.append(out)
    return summaries


def s2_starvation_rows(
    rows: Sequence[Mapping[str, Any]],
    ve_points: Sequence[float],
) -> list[dict[str, Any]]:
    """⑧ S2 饥饿传导派生表：主矩阵每个 (N,δ,T_BASE,exam) 在 verify_error 各档下
    的中位晋升时长（跨种子中位）与相对基准档的增幅倍数（s2_starvation_propagation）。"""
    grid_meta: list[tuple[float, float, float, float]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for r in rows:
        if r["experiment"] != EXP_MATRIX:
            continue
        key = (r["N"], r["DELTA"], r["T_BASE"], r["exam_weight_high"])
        if key not in seen:
            seen.add(key)
            grid_meta.append(key)

    out: list[dict[str, Any]] = []
    for n, delta, t_base, exam_wh in sorted(grid_meta):
        by_ve: dict[float, list[float | None]] = {}
        head_vals: dict[str, Any] = {}
        for ve in ve_points:
            cell = sorted(
                (r for r in rows
                 if r["experiment"] == EXP_MATRIX
                 and (r["N"], r["DELTA"], r["T_BASE"], r["exam_weight_high"]) == (n, delta, t_base, exam_wh)
                 and r["verify_error"] == float(ve)),
                key=lambda r: r[_UNIT_COL],
            )
            by_ve[float(ve)] = [r["median_time_to_active"] for r in cell]
            if cell:
                head_vals = {"N": n, "DELTA": delta, "T_BASE": t_base,
                             "exam_weight_high": exam_wh}
        report = s2_starvation_propagation(
            by_ve, reference=S2_REFERENCE_VE, limit=S2_LIMIT
        )
        row: dict[str, Any] = dict(head_vals)
        for ve in ve_points:
            v = float(ve)
            row[f"median_tta_ve{v:.2f}"] = report.medians.get(v)
            row[f"ratio_ve{v:.2f}"] = report.ratios.get(v)
        row["reference"] = report.reference
        row["limit"] = report.limit
        row["passed"] = report.passed
        out.append(row)
    return out


# ── 落盘（确定性载荷 / 遥测分离）─────────────────────────────────


def _csv_cell(v: Any) -> str:
    """CSV 单元格编码：None→空串；其余 str()（float 走 repr，逐位保真）。"""
    return "" if v is None else str(v)


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")   # 显式 \n：跨平台字节稳定
        w.writerow(header)
        for row in rows:
            w.writerow([_csv_cell(row.get(col)) for col in header])


def write_outputs(
    out_dir: str | Path,
    meta: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Path]:
    """写七件套中的确定性载荷四件；返回文件路径表。

    确定性载荷（results/summary/starvation.csv + results.json）只含计划与结果——
    同命令同种子两次运行逐位一致；耗时遥测（timing.csv 逐 run / timing_by_group.csv
    逐组，:func:`write_telemetry` 落盘）与 manifest.json（时间戳/进程数/wall）天然
    非确定，不参与复现断言。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}
    paths["results_csv"] = out / "results.csv"
    _write_csv(paths["results_csv"], RESULT_COLS, rows)

    summaries = group_summaries(rows)
    summary_header = (
        "experiment", "arm", "group_id", "n_days", "n_seeds",
        "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error",
        "reviewer_x", "tilt_gamma", "router", "window_days",
    ) + tuple(c for m in METRIC_COLS for c in (f"{m}_mean", f"{m}_std")) + (
        REGRET_RATIO_COL,
    )
    paths["summary_csv"] = out / "summary.csv"
    _write_csv(paths["summary_csv"], summary_header, summaries)

    ves = [float(v) for v in meta["grid"]["verify_error"]]
    paths["starvation_csv"] = out / "starvation.csv"
    star_rows = s2_starvation_rows(rows, ves)
    star_header = (
        "N", "DELTA", "T_BASE", "exam_weight_high",
    ) + tuple(c for ve in ves for c in (f"median_tta_ve{ve:.2f}", f"ratio_ve{ve:.2f}")) + (
        "reference", "limit", "passed",
    )
    _write_csv(paths["starvation_csv"], star_header, star_rows)

    paths["results_json"] = out / "results.json"
    doc = {
        "schema_version": 1,
        **meta,
        "rows": [{col: row.get(col) for col in RESULT_COLS} for row in rows],
    }
    paths["results_json"].write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return paths


#: 逐参数组耗时聚合表（timing_by_group.csv）的列序：组维度 + 耗时统计 + 预算标记。
GROUP_TIMING_COLS: tuple[str, ...] = (
    "experiment", "arm", "group_id", "n_days", "n_runs",
    "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error",
    "elapsed_s_mean", "elapsed_s_max", "elapsed_s_sum", "within_budget_5min",
)

#: §6 单实验耗时预算（秒）：smoke/full 通用断言口径。
RUN_BUDGET_S = 300.0


def group_timing_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """逐参数组聚合耗时（AC3「记录每组耗时」）：组维度 + n_runs +
    elapsed mean/max/sum + 是否在 §6 单实验 5 min 预算内。遥测数据，
    只从行记录的 elapsed 列派生，可在任意时点对已落盘结果重算。"""
    order: list[tuple[str, str, str]] = []
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:                                    # rows 已按 unit_id 排序
        key = (row["experiment"], row["arm"], row["group_id"])
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    out: list[dict[str, Any]] = []
    for key in order:
        members = groups[key]
        head = members[0]
        elapsed = sorted(float(r[_ELAPSED_COL]) for r in members)
        out.append({
            "experiment": key[0], "arm": key[1], "group_id": key[2],
            "n_days": head["n_days"], "n_runs": len(members),
            "N": head["N"], "DELTA": head["DELTA"], "T_BASE": head["T_BASE"],
            "exam_weight_high": head["exam_weight_high"],
            "verify_error": head["verify_error"],
            "elapsed_s_mean": sum(elapsed) / len(elapsed),
            "elapsed_s_max": elapsed[-1],
            "elapsed_s_sum": sum(elapsed),
            "within_budget_5min": elapsed[-1] < RUN_BUDGET_S,
        })
    return out


def write_group_timing(out_dir: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """写逐参数组耗时聚合表（遥测）；独立入口，便于对既有结果目录补算。"""
    path = Path(out_dir) / "timing_by_group.csv"
    _write_csv(path, GROUP_TIMING_COLS, group_timing_rows(rows))
    return path


def write_telemetry(
    out_dir: str | Path,
    meta: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    argv: Sequence[str],
    workers: int,
    started_at: str,
    wall_s: float,
) -> dict[str, Path]:
    """遥测三件套：timing.csv（逐 run 耗时）+ timing_by_group.csv（逐组耗时）
    + manifest.json（运行环境快照）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    paths["timing_csv"] = out / "timing.csv"
    _write_csv(paths["timing_csv"], PARAM_COLS + (_ELAPSED_COL,), rows)

    paths["timing_by_group_csv"] = write_group_timing(out, rows)

    paths["manifest_json"] = out / "manifest.json"
    manifest = {
        "argv": list(argv),
        "mode": meta["mode"],
        "n_units": meta["n_units"],
        "n_rows": len(rows),
        "workers": int(workers),
        "seeds": meta["seeds"],
        "n_days": meta["n_days"],
        "started_at_utc": started_at,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "wall_s": wall_s,
        "per_run_elapsed_s": {
            "min": min((r[_ELAPSED_COL] for r in rows), default=None),
            "median": sorted(r[_ELAPSED_COL] for r in rows)[len(rows) // 2] if rows else None,
            "max": max((r[_ELAPSED_COL] for r in rows), default=None),
        },
        "python": sys.version.split()[0],
    }
    paths["manifest_json"].write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    return paths


# ── 编排入口与 CLI ───────────────────────────────────────────────


def run_experiments(
    mode: str,
    *,
    out_dir: str | Path | None = None,
    workers: int | None = None,
    seeds: Sequence[int] | None = None,
    n_days: int | None = None,
    replay_every: int = 10,
    window_days: float = SLIDING_WINDOW_DAYS,
    plan_only: bool = False,
    argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    """构建计划 → （并行）执行 → 落盘七件套；返回 {paths, meta, rows}。

    ``plan_only=True`` 只打印计划（组数/run 数/种子表）不执行、不写盘——
    覆盖性核查用（§3 全矩阵 = 162 组 × 3 种子 + 附加 3×3 种子臂）。
    ``window_days``：衰退滑动窗窗长（M0 定稿候选参数，透传全部单元）。
    """
    units, meta = build_plan(mode, seeds=seeds, n_days=n_days,
                             replay_every=replay_every, window_days=window_days)
    if plan_only:
        _print_plan(mode, meta)
        return {"meta": meta, "rows": [], "paths": {}}

    if out_dir is None:
        out_dir = Path("results") / f"experiments-{mode}"
    if workers is None:
        workers = max(1, min(os.cpu_count() or 1, 8))
    workers = max(1, int(workers))

    print(f"[experiments] mode={mode} units={meta['n_units']} "
          f"(matrix groups={meta['n_matrix_groups']} × seeds={meta['seeds']} "
          f"+ additional {meta['n_additional_runs']} runs) "
          f"n_days={meta['n_days']} workers={workers}", flush=True)
    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    rows = execute_plan(units, workers=workers)
    wall = time.perf_counter() - t0
    over_budget = [r for r in rows if r[_ELAPSED_COL] >= RUN_BUDGET_S]
    if over_budget:
        raise RuntimeError(
            f"{len(over_budget)} 个实验超出 §6 单实验 {RUN_BUDGET_S:.0f}s 预算"
            f"（最慢 {max(r[_ELAPSED_COL] for r in over_budget):.1f}s，"
            f"如 unit {over_budget[0][_UNIT_COL]}）——先降 n_days/并行度再重跑"
        )

    paths = write_outputs(out_dir, meta, rows)
    paths.update(write_telemetry(out_dir, meta, rows, argv=argv or [],
                                 workers=workers, started_at=started_at, wall_s=wall))
    print(f"[experiments] done in {wall:.0f}s → {Path(out_dir)} "
          f"(per-run max {max(r[_ELAPSED_COL] for r in rows):.1f}s)", flush=True)
    return {"meta": meta, "rows": rows, "paths": paths}


def _print_plan(mode: str, meta: Mapping[str, Any]) -> None:
    g = meta["grid"]
    print(f"plan mode={mode}")
    print(f"  matrix grid: N{tuple(g['N'])} × DELTA{tuple(g['DELTA'])} "
          f"× T_BASE{tuple(g['T_BASE'])} × exam_weight_high{tuple(g['exam_weight_high'])} "
          f"× verify_error{tuple(g['verify_error'])}")
    print(f"  matrix groups: {meta['n_matrix_groups']}  "
          f"runs: {meta['n_matrix_groups'] * len(meta['seeds'])} (seeds={meta['seeds']})")
    print(f"  additional experiments: {EXP_TILT}(2 arms) {EXP_REVIEWER}"
          f"({len(REVIEWER_X_POINTS)} arms) {EXP_THOMPSON}(2 arms) — "
          f"{meta['n_additional_runs']} runs")
    print(f"  total runs: {meta['n_units']}  n_days={meta['n_days']}")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：``python -m qswarm.simulator.experiments --smoke|--full [选项]``。"""
    ap = argparse.ArgumentParser(
        prog="python -m qswarm.simulator.experiments",
        description="SPEC-4 §3 实验矩阵 runner（一条命令/并行/可复现）",
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true",
                      help="子集模式：5 维各取两档端点，分钟级验证管道")
    mode.add_argument("--full", action="store_true",
                      help="§3 全矩阵 162 组 + 三项附加单变量实验")
    ap.add_argument("--out", default=None,
                    help="输出目录（缺省 results/experiments-<mode>）")
    ap.add_argument("--workers", type=int, default=None,
                    help="并行进程数（缺省 min(CPU,8)；1=串行）")
    ap.add_argument("--seeds", default=None,
                    help=f"逗号分隔种子表（缺省 full={list(FULL_SEEDS)}, "
                         f"smoke={list(SMOKE_SEEDS)}）")
    ap.add_argument("--n-days", type=int, default=None,
                    help=f"仿真日数（缺省 full={FULL_N_DAYS}, smoke={SMOKE_N_DAYS}）")
    ap.add_argument("--replay-every", type=int, default=10,
                    help="环内重放等价校验间隔（日；0=只做收尾全账本检查）")
    ap.add_argument("--window-days", type=float, default=SLIDING_WINDOW_DAYS,
                    help=f"衰退滑动窗窗长/天（M0 定稿候选参数，缺省 "
                         f"{SLIDING_WINDOW_DAYS}）")
    ap.add_argument("--plan", action="store_true",
                    help="只打印实验计划不执行（覆盖性核查）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    mode = "full" if args.full else "smoke"
    seeds = (
        None if args.seeds is None
        else tuple(int(s) for s in args.seeds.split(",") if s.strip())
    )
    result = run_experiments(
        mode,
        out_dir=args.out,
        workers=args.workers,
        seeds=seeds,
        n_days=args.n_days,
        replay_every=args.replay_every,
        window_days=args.window_days,
        plan_only=args.plan,
        argv=list(argv) if argv is not None else sys.argv[1:],
    )
    if not args.plan:
        for name, path in result["paths"].items():
            print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
