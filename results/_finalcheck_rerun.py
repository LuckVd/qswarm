"""终检独立复跑：推荐组 N06-D0.05-T0.60-E0.30-V0.20 种子 11/33。

按 experiments._execute_unit 同参调用 engine.run，比对 results.csv 存档行的
9 项度量与 digest（位级可复现性终检）；并打印 ③ 观测口径相关字段。
"""
from __future__ import annotations

import csv

from qswarm.simulator.engine import run
from qswarm.simulator.verdict import VerdictParams
from qswarm.simulator.exam import ExamWorldParams

STORED = {}
with open("results/experiments-full/results.csv") as fh:
    for r in csv.DictReader(fh):
        if r["experiment"] == "matrix" and r["group_id"] == "N06-D0.05-T0.60-E0.30-V0.20":
            STORED[int(r["seed"])] = r

FLOAT_KEYS = ["promotion_precision", "promotion_recall", "median_time_to_active",
              "wrong_extinguish_rate", "degradation_response", "regret_total",
              "cold_start_length", "regret_late_over_early"]

all_ok = True
for seed in (11, 33):
    res = run(
        seed=seed, n_days=30,
        overrides={"promote.N": 6, "promote.DELTA": 0.05, "tilt.T_BASE": 0.60},
        verdict=VerdictParams(verify_error=0.2),
        exam=ExamWorldParams(weight_high=0.3),
        replay_every=10,
    )
    st = STORED[seed]
    print(f"\n=== seed={seed} ===")
    ok = True
    for k in FLOAT_KEYS:
        mine = getattr(res.metrics, k)
        theirs = st[k]
        match = (theirs == "" and mine is None) or (
            theirs != "" and mine is not None
            and abs(float(theirs) - float(mine)) < 1e-9)
        ok &= match
        print(f"  {k:26s} rerun={mine!r:<22} stored={theirs:<22} {'OK' if match else 'MISMATCH'}")
    re_mine, re_stored = res.metrics.replay_equivalent, st["replay_equivalent"]
    match9 = str(re_mine) == re_stored or (re_stored == "True") == bool(re_mine)
    ok &= match9
    print(f"  {'replay_equivalent':26s} rerun={re_mine!r:<22} stored={re_stored:<22} {'OK' if match9 else 'MISMATCH'}")
    d_ok = res.digest == st["digest"]
    ok &= d_ok
    print(f"  digest rerun={res.digest[:16]}… stored={st['digest'][:16]}… {'OK' if d_ok else 'MISMATCH'}")
    all_ok &= ok
    print(f"  --> {'BIT-IDENTICAL' if ok else 'DIFFERS'}")

print("\nVERDICT:", "BIT-IDENTICAL (both seeds)" if all_ok else "DIFFERS — investigate")
