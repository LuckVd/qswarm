"""experiments/diagnose_recall.py —— ②① 度量解剖：分母构成与逐格卡点。

对 (seed, overrides, world knobs) 单次 run：
  1. ② recall 分母按 role × node 层级（family/type）分解：top 格数、被晋升数、
     未晋升格的期末 n_eff / n_eff_s2s3 / 节点窗内流量 / 期末状态；
  2. ① precision：被晋升格逐格 hit/miss（true top-⌈0.2n⌉ 与否）按 role 分解；
  3. ③ 每个 EXPERT 家族格的 (首dep, 首active) 任务坐标与判定瓶颈。

用法：
  python experiments/diagnose_recall.py --seed 11 [--N 6] [--delta 0.05] \
      [--cooldown 7] [--window-days 7] [--n-days 30]
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import KernelLedger, agg_cells
from qswarm.kernel.promote_scan import evaluate_candidate_criteria
from qswarm.simulator.engine import run
from qswarm.simulator.metrics import (
    SIM_T0,
    _tasks_done_by,
    cell_episodes,
    deposit_nodes,
)
from qswarm.simulator.verdict import VerdictParams


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--N", type=int, default=6)
    ap.add_argument("--delta", type=float, default=0.05)
    ap.add_argument("--t-base", type=float, default=0.6)
    ap.add_argument("--cooldown", type=int, default=None)
    ap.add_argument("--window-days", type=float, default=7.0)
    ap.add_argument("--n-days", type=int, default=30)
    ap.add_argument("--task-window", type=int, default=500)
    args = ap.parse_args()

    overrides = {"promote.N": args.N, "promote.DELTA": args.delta,
                 "tilt.T_BASE": args.t_base}
    if args.cooldown is not None:
        overrides["exam.COOLDOWN_DAYS"] = args.cooldown

    with tempfile.TemporaryDirectory(prefix="qswarm-recalldiag-") as td:
        db = Path(td) / "ledger.db"
        res = run(seed=args.seed, n_days=args.n_days, overrides=overrides,
                  verdict=VerdictParams(verify_error=0.2),
                  promote_window_days=args.window_days, replay_every=0,
                  db_path=db, keep_db=True)
        ledger = KernelLedger(db)
        try:
            diagnose(res, ledger, args)
        finally:
            ledger.close()
    return 0


def diagnose(res, ledger: KernelLedger, args) -> None:
    truth, timeline = res.truth, res.timeline
    tw = args.task_window
    m = res.metrics
    print(f"== run seed={args.seed} N={args.N} δ={args.delta} T={args.t_base} "
          f"cd={args.cooldown} win={args.window_days} elapsed={res.elapsed_s:.1f}s")
    print(f"metrics: prec={m.promotion_precision} recall={m.promotion_recall} "
          f"tta={m.median_time_to_active} wrong_ext={m.wrong_extinguish_rate} "
          f"degr={m.degradation_response}")

    eps = cell_episodes(ledger)
    final_ts = res.events[-1].ts
    view = agg_cells(ledger, final_ts, params=res.params)

    # ── ② 分母分解 ──
    traffic_pairs = set()
    for ev in ledger.read():
        from qswarm.contracts import LedgerKind
        if ev.kind is not LedgerKind.DEPOSIT:
            continue
        from qswarm.kernel import node_prefixes
        if _tasks_done_by(timeline, ev.ts) > tw:
            continue
        for nd in node_prefixes(ev.payload["taxonomy_path"]):
            traffic_pairs.add((str(ev.payload["role"]), nd))

    top_cells = set()
    for role, node in traffic_pairs:
        for agent in truth.top_fraction(role, node, frac=0.2, t=tw):
            top_cells.add((agent, role, node))

    by_bucket: dict[tuple, dict] = {}
    for agent, role, node in sorted(top_cells):
        level = "family" if "/" not in node else "type"
        b = by_bucket.setdefault((role, level), {"n": 0, "promoted": 0,
                                                 "unpromoted": []})
        b["n"] += 1
        ep = eps.get((agent, role, node) and __import__("qswarm.kernel", fromlist=["CellKey"]).CellKey(agent, role, node))
        promoted = ep is not None and ep.first_active_ts is not None and \
            _tasks_done_by(timeline, ep.first_active_ts) <= tw
        if promoted:
            b["promoted"] += 1
        else:
            cell = view.get(agent, role, node)
            b["unpromoted"].append((agent, node, cell))

    print(f"\n[2] recall 分母 top 格总数={len(top_cells)}（task_window={tw}）")
    print(f"    {'role':9s} {'level':6s} {'top':>4s} {'promo':>5s}  "
          f"unpromoted: n_eff med / s2s3 med / status")
    for (role, level), b in sorted(by_bucket.items()):
        effs = [c.n_eff for _, _, c in b["unpromoted"] if c is not None]
        s2s3 = [c.n_eff_s2s3 for _, _, c in b["unpromoted"] if c is not None]
        stats = [c.status for _, _, c in b["unpromoted"] if c is not None]
        print(f"    {role:9s} {level:6s} {b['n']:4d} {b['promoted']:5d}  "
              f"n={len(stats)} "
              f"n_eff_med={statistics.median(effs) if effs else None} "
              f"s2s3_med={statistics.median(s2s3) if s2s3 else None} "
              f"statuses={dict(Counter(stats))}")

    # ── ① precision 分解 ──
    print("\n[1] promoted cells hit/miss（true top 与否）:")
    hits = misses = 0
    by_role = {}
    for key, ep in sorted(eps.items()):
        if not ep.activations:
            continue
        t_act = int(_tasks_done_by(timeline, ep.activations[0]))
        hit = key.agent_id in truth.top_fraction(key.role, key.node, frac=0.2, t=t_act)
        by_role.setdefault(key.role, [0, 0])
        by_role[key.role][0 if hit else 1] += 1
        if hit:
            hits += 1
        else:
            misses += 1
            print(f"    MISS {key.agent_id:12s} {key.role:9s} {key.node:26s} "
                  f"t_act={t_act}")
    for role, (h, mm) in sorted(by_role.items()):
        print(f"    {role:9s} hit={h} miss={mm} prec={h / (h + mm) if h + mm else None}")

    # ── ③ expert 格时间线 ──
    print("\n[3] EXPERT 家族格（family_scoped）:")
    for key, ep in sorted(eps.items()):
        if truth.archetype.get(key.agent_id) != "expert":
            continue
        ef = truth.expert_family.get(key.agent_id)
        if ef is None or key.node.split("/")[0] != ef:
            continue
        dep = _tasks_done_by(timeline, ep.first_deposit_ts) if ep.first_deposit_ts else None
        act = _tasks_done_by(timeline, ep.activations[0]) if ep.activations else None
        cell = view.get(key.agent_id, key.role, key.node)
        print(f"    {key.agent_id:12s} {key.role:9s} {key.node:22s} dep={dep and round(dep)} "
              f"act={act and round(act)} n_eff={cell and round(cell.n_eff, 1)} "
              f"s2s3={cell and round(cell.n_eff_s2s3, 1)}")

    # ── 暗考统计 ──
    promo = [e for e in res.events if getattr(e.kind, "value", "") == "exam_result"
             and e.payload.get("purpose") == "promotion"]
    passed = sum(1 for e in promo if e.payload["correct"])
    print(f"\n[4] 暗考={len(promo)} 及格={passed}")


if __name__ == "__main__":
    raise SystemExit(main())
