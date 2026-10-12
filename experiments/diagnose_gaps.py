"""experiments/diagnose_gaps.py —— M0 调参诊断：一次 run 的晋升瓶颈解剖。

对 (seed, overrides, world knobs) 单次 run（keep_db 落盘后只读重开账本）：
  1. 逐 (role, node) 格的期末状态分布（按角色汇总）；
  2. 全部 activation/decay 时间线（任务坐标）；
  3. EXPERT 家族格的转换①四判据逐日首达（定位卡点判据）；
  4. 暗考逐次结果与及格率；
  5. ⑥ regret 前/后段平均差距之比（regret_late_over_early 组内读数）；
  6. ⑦ 退化型衰退窗可行性核算（窗数学）+ 逐角色期末 n_eff 分位数。

用法：
  python experiments/diagnose_gaps.py --seed 22 --N 6 --delta 0.05 \
      [--cooldown 3] [--window-days 7]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import KernelLedger, agg_cells
from qswarm.kernel.promote_scan import evaluate_candidate_criteria
from qswarm.simulator.engine import run
from qswarm.simulator.metrics import (
    SIM_T0,
    _tasks_done_by,
    cell_episodes,
    regret_curve,
)
from qswarm.simulator.verdict import VerdictParams


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=22)
    ap.add_argument("--N", type=int, default=6)
    ap.add_argument("--delta", type=float, default=0.05)
    ap.add_argument("--t-base", type=float, default=0.7)
    ap.add_argument("--cooldown", type=int, default=None, help="exam.COOLDOWN_DAYS 覆盖")
    ap.add_argument("--window-days", type=float, default=None, help="promote 衰退滑动窗覆盖")
    ap.add_argument("--n-days", type=int, default=30)
    args = ap.parse_args()

    overrides = {"promote.N": args.N, "promote.DELTA": args.delta,
                 "tilt.T_BASE": args.t_base}
    if args.cooldown is not None:
        overrides["exam.COOLDOWN_DAYS"] = args.cooldown
    window_days = args.window_days if args.window_days is not None else 30.0

    with tempfile.TemporaryDirectory(prefix="qswarm-diag-") as td:
        db = Path(td) / "ledger.db"
        res = run(seed=args.seed, n_days=args.n_days, overrides=overrides,
                  verdict=VerdictParams(verify_error=0.2),
                  promote_window_days=window_days, replay_every=0,
                  db_path=db, keep_db=True)
        ledger = KernelLedger(db)
        try:
            report = diagnose(res, ledger, args, window_days)
        finally:
            ledger.close()
    return report


def diagnose(res, ledger: KernelLedger, args, window_days: float) -> int:
    truth, timeline, events = res.truth, res.timeline, res.events
    print(f"== run seed={args.seed} N={args.N} δ={args.delta} T={args.t_base} "
          f"cooldown={args.cooldown} win={window_days} elapsed={res.elapsed_s:.1f}s")
    m = res.metrics
    print(f"metrics: prec={m.promotion_precision} recall={m.promotion_recall} "
          f"tta={m.median_time_to_active} wrong_ext={m.wrong_extinguish_rate} "
          f"degr={m.degradation_response} regret/task={m.regret_per_task} "
          f"cold={m.cold_start_length}")

    final_ts = events[-1].ts
    view = agg_cells(ledger, final_ts, params=res.params)
    by_role: dict[str, dict[str, int]] = {}
    for c in view.cells.values():
        r = by_role.setdefault(c.role, {"cells": 0, "active": 0, "shadow": 0,
                                        "candidate": 0, "dormant": 0})
        r["cells"] += 1
        r[c.status] = r.get(c.status, 0) + 1
    print("\n[1] 期末格子状态（按角色）:")
    for role, st in sorted(by_role.items()):
        print(f"  {role:9s} {st}")

    eps = cell_episodes(ledger)
    print("\n[2] activation/decay 时间线（任务坐标）:")
    for key, ep in sorted(eps.items(),
                          key=lambda kv: kv[1].activations[:1]):
        if not ep.activations and not ep.decay_demotions:
            continue
        act = [_tasks_done_by(timeline, t) for t in ep.activations]
        dec = [_tasks_done_by(timeline, t) for t in ep.decay_demotions]
        dep = (_tasks_done_by(timeline, ep.first_deposit_ts)
               if ep.first_deposit_ts else None)
        print(f"  {key.agent_id:12s} {key.role:9s} {key.node:26s} "
              f"first_dep={dep and round(dep)} act={[round(a) for a in act]} decay={dec}")

    experts = [a for a in truth.agents if truth.archetype[a] == "expert"]
    print(f"\n[3] EXPERT agents={experts} "
          f"expert_family={[truth.expert_family[a] for a in experts]}")
    for a in experts:
        fam = truth.expert_family[a]
        role = truth.role[a]
        ep = next((e for k, e in eps.items()
                   if e.agent_id == a and e.role == role and e.node == fam), None)
        if ep is None:
            print(f"  -- {a}|{role}|{fam}: 无存款格（零流量）")
            continue
        snap = None
        marks = {n: None for n in ("hard", "advantage", "traffic", "gate")}
        for d in range(1, args.n_days):
            as_of = SIM_T0 + timedelta(days=d, hours=12)
            snap = evaluate_candidate_criteria(ledger, as_of, ep.key,
                                               params=res.params)
            for n in ("hard", "advantage", "traffic", "gate"):
                if marks[n] is None and getattr(snap, f"{n}_ok"):
                    marks[n] = d
            if all(v is not None for v in marks.values()):
                break
        print(f"  -- {a}|{role}|{fam} 首dep={ep.first_deposit_ts and round(_tasks_done_by(timeline, ep.first_deposit_ts))}")
        print(f"     判据首达日: {marks}  期末(n_eff={snap.n_eff:.1f}/{res.params.promote.N}"
              f" s2s3={snap.n_eff_s2s3:.1f}/3 traffic={snap.traffic}/{res.params.promote.M}"
              f" LB={snap.wilson_lb and round(snap.wilson_lb, 3)}"
              f" base={snap.baseline and round(snap.baseline, 3)})")

    promo = [e for e in events if getattr(e.kind, "value", "") == "exam_result"
             and e.payload.get("purpose") == "promotion"]
    passed = sum(1 for e in promo if e.payload["correct"])
    print(f"\n[4] 暗考={len(promo)} 场 及格={passed}")
    for e in promo:
        print(f"  t={round(_tasks_done_by(timeline, e.ts)):5d} {e.payload['exam_id']} "
              f"pass={e.payload['correct']} score={e.payload['score']:.2f}")

    curve = regret_curve(res.tasks, truth, res.routing)
    n = len(curve)
    k = max(1, n // 3)
    early = statistics.fmean(p.gap for p in curve[:k])
    late = statistics.fmean(p.gap for p in curve[-k:])
    ratio = late / early if early else float("nan")
    print(f"\n[5] regret early={early:.4f} late={late:.4f} late/early={ratio:.4f} "
          f"total={curve[-1].cum_regret:.1f}")

    print("\n[6] 逐角色期末 n_eff 分位数（family 级格）:")
    for role in sorted({c.role for c in view.cells.values()}):
        fam_cells = [c for c in view.cells.values()
                     if c.role == role and "/" not in c.node]
        vals = sorted(c.n_eff for c in fam_cells)
        if vals:
            med = statistics.median(vals)
            print(f"  {role:9s} n={len(vals)} n_eff min={vals[0]:.1f} med={med:.1f} "
                  f"max={vals[-1]:.1f}  ≥6:{sum(1 for v in vals if v >= 6)} "
                  f"≥10:{sum(1 for v in vals if v >= 10)}")

    degraded = [a for a in truth.agents if truth.archetype[a] == "degraded"]
    print(f"\n[7] 退化型={degraded} t_drift={[truth.t_drift[a] for a in degraded]} "
          f"(2×{window_days:.0f}d 双整窗需 {2*window_days:.0f}d，"
          f"期末距漂移 {args.n_days - max((truth.t_drift[a] or 0) for a in degraded)/50 if degraded else 0:.1f}d)")
    print(f"status_counts={res.status_counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
