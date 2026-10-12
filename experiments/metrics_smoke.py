"""metrics 端到端自证——SPEC-4 §2 全部 9 项度量在真实内核路径上的可采集性。

最小闭环（每次运行一条完整轨迹）：
  TaskGenerator 任务流 → kernel.route 派单（cells 视图逐日刷新）→
  simulator.verdict/deposit_schedule 生成三层存款（S3 按 arrival_day 延迟入账）→
  kernel.promote_scan 夜间批（ExamCenter 暗考回调）→ metrics 采集。

采集口径：
  ①–⑦ + ⑨ 经 collect_run_metrics / replay_equivalence（内核输出侧 + WorldTruth）；
  ⑧ S2 饥饿传导：同一世界在 verify_error ∈ {0.2, 0.35, 0.5} 下重跑，
    以 median_time_to_active（真专家口径）的中位增幅判定
    （s2_starvation_propagation，§2 目标 ≤ 2×）。

注意：本脚本只做**度量自证**（纯 stdlib、分钟级），不是引擎本体——引擎 harness
的逐任务 cells 刷新/预算调度属后续子任务；此处逐日刷新视图属演示级简化。
随机性全部走 derive_seed 派生（不用内建 hash，跨进程可复现）。

用法：python3 experiments/metrics_smoke.py
输出：stdout 表格 + experiments/metrics_smoke.csv（中间账本 db 用后即删）
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import DispatchPayload, LedgerKind, RunMode, load_params
from qswarm.kernel import KernelLedger, agg_cells, promote_scan, route
from qswarm.simulator import (
    AgentPool,
    ExamCenter,
    RoutingRecord,
    TaskGenerator,
    TaskStreamParams,
    aggregate_run_metrics,
    assign_finding_context,
    collect_run_metrics,
    deposit_schedule,
    derive_seed,
    generate_finding_verdict,
    regret_curve,
    replay_equivalence,
    seed_entry_exams,
    snapshot_world_truth,
    s2_starvation_propagation,
)
from qswarm.simulator.metrics import (
    SIM_T0,
    RunMetrics,
    TaskTimeline,
    oracle_route_stream,
)
from qswarm.simulator.verdict import VerdictParams

T0 = SIM_T0
SEEDS = (11, 22)
N_DAYS = 30
LAM = 20.0
VE_POINTS = (0.20, 0.35, 0.50)
RUN_KEYS = (
    "promotion_precision", "promotion_recall", "median_time_to_active",
    "wrong_extinguish_rate", "degradation_response", "regret_total",
    "regret_per_task", "cold_start_length", "replay_equivalent",
)


class _RoutedTask:
    """WorldTask 的路由适配：补 route() 所需的 role 属性（fast→scout，其余→auditor）。"""

    def __init__(self, task) -> None:
        self.task_id = task.task_id
        self.family = task.family
        self.type = task.type
        self.role = "scout" if task.mode is RunMode.FAST else "auditor"


class _Clock:
    """逐条前移的测试时钟：ts = T0 + day 天 + 落账序号 × 1 秒（沿 seq 严格递增）。"""

    def __init__(self) -> None:
        self.dt = T0
        self._i = 0

    def advance_to(self, day: float) -> None:
        self._i += 1
        self.dt = T0 + timedelta(days=day, seconds=self._i)

    def now(self):
        return self.dt


def run_one(seed: int, *, verify_error: float, n_days: int = N_DAYS) -> dict:
    params = load_params()
    clock = _Clock()
    db_path = Path(__file__).parent / f"_metrics_smoke_s{seed}_ve{verify_error}.db"
    ledger = KernelLedger(db_path, clock=clock)

    pool = AgentPool.from_params(params, seed=seed)
    truth = snapshot_world_truth(pool)
    gen = TaskGenerator(params, seed=seed, stream=TaskStreamParams(lam=LAM))
    days = list(gen.iter_days(n_days))                     # 确定性任务流，一次物化
    tasks_all = [t for batch in days for t in batch]
    center = ExamCenter(pool, ledger, seed=seed, params=params)
    rng_route = random.Random(derive_seed(seed, "route"))
    vp = VerdictParams(verify_error=verify_error)

    seed_entry_exams(pool, ledger, seed=seed, params=params)      # §8.4 播种（全开）

    routing: list[RoutingRecord] = []
    pending: list[tuple[float, int, object]] = []                 # (arrival_day, order, payload)
    order = 0
    view = agg_cells(ledger, clock.dt, params=params)
    for day, batch in enumerate(days):
        clock.advance_to(float(day))
        view = agg_cells(ledger, clock.dt, params=params)         # 逐日刷新（演示级简化）
        for task in batch:
            decision = route(_RoutedTask(task), view, rng_route, params)
            clock.advance_to(float(day))
            ledger.append(
                LedgerKind.DISPATCH,
                DispatchPayload(**decision.to_dispatch_payload()),
            )
            routing.append(RoutingRecord(
                task_id=task.task_id, role=decision.role, node=decision.node,
                assignee=decision.assignee, channel=decision.channel,
            ))
            if task.produces_finding and decision.assignee:
                ctx = assign_finding_context(task, pool, params)
                res = generate_finding_verdict(ctx, pool, seed, vp)
                for rec in deposit_schedule(ctx, res, params):
                    pending.append((rec.day, order, rec.payload))
                    order += 1
        # 当日到期存款入账（S3 按 arrival_day 延迟）
        due = sorted((p for p in pending if p[0] <= day), key=lambda p: (p[0], p[1]))
        pending = [p for p in pending if p[0] > day]
        for _, _, payload in due:
            clock.advance_to(float(day))
            ledger.append(LedgerKind.DEPOSIT, payload)
        # 夜间批：promote_scan + 晋升暗考（as_of 落在当日末，日内秒级偏移不越日）
        clock.advance_to(float(day) + 0.999)
        promote_scan(
            ledger,
            clock.dt,
            params=params,
            exam=lambda *, agent_id, role, node, from_status: center.run_promotion_exam(
                agent_id, role, node, day=day, t=0, from_status=from_status
            ),
        )

    timeline = TaskTimeline.from_tasks(tasks_all)
    metrics = collect_run_metrics(ledger, truth, timeline, tasks_all, routing)
    rep = replay_equivalence(ledger, sample=8)
    steps = oracle_route_stream(tasks_all, truth)
    curve = regret_curve(tasks_all, truth, routing)
    out = {
        "seed": seed,
        "verify_error": verify_error,
        "tasks": len(tasks_all),
        "oracle_steps": len(steps),
        **metrics.to_dict(),
        "replay_equivalent": rep.passed,
        "replay_rate": rep.equivalence_rate,
        "regret_curve_points": len(curve),
    }
    ledger.close()
    db_path.unlink()                                       # 中间账本用后即删
    return out


def main() -> None:
    rows = [run_one(seed, verify_error=0.20) for seed in SEEDS]
    ve_rows = [run_one(seed=11, verify_error=ve) for ve in VE_POINTS]

    agg = aggregate_run_metrics([RunMetrics(**{k: r[k] for k in RUN_KEYS}) for r in rows])
    targets = {
        "promotion_precision": "≥ 0.90",
        "promotion_recall": "≥ 0.70（500 任务窗）",
        "median_time_to_active": "≤ 200（真专家）",
        "wrong_extinguish_rate": "≤ 0.05",
        "degradation_response": "≤ 150",
        "regret_total": "随任务数收窄",
        "regret_per_task": "随任务数收窄",
        "cold_start_length": "播种 < 50",
    }
    print(f"metrics 端到端自证（{N_DAYS} 日 × λ={LAM} × {len(SEEDS)} 种子，均值±std）")
    print(f"{'度量':<26}{'mean':>12}{'std':>10}{'n':>4}   目标")
    for key, target in targets.items():
        m = agg[key]
        mean = "-" if m.mean is None else f"{m.mean:.4f}"
        std = "-" if m.std is None else f"{m.std:.4f}"
        print(f"{key:<26}{mean:>12}{std:>10}{m.n:>4}   {target}")
    print(f"{'replay_equivalent(率)':<26}{rows[0]['replay_rate']:>12.3f}{'-':>10}{len(rows):>4}   100%（回归项）")

    rep8 = s2_starvation_propagation(
        {ve: [r["median_time_to_active"] for r in ve_rows if r["verify_error"] == ve]
         for ve in VE_POINTS}
    )
    print("\n⑧ S2 饥饿传导（seed=11，真专家中位晋升时长）")
    for ve in VE_POINTS:
        print(f"  verify_error={ve:.2f}: median={rep8.medians[ve]} ratio={rep8.ratios[ve]}")
    print(f"  passed (≤ 2×): {rep8.passed}")

    out_csv = Path(__file__).parent / "metrics_smoke.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        writer.writerows(ve_rows)
    print(f"\nCSV 已写出：{out_csv}")

    assert all(r["replay_rate"] == 1.0 for r in rows + ve_rows), "重放等价必须 100%"
    print("断言通过：全部运行 replay_rate == 1.0")


if __name__ == "__main__":
    main()
