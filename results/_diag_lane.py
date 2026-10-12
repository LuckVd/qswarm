"""SCRATCH: lane decomposition diagnostics for one run."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.simulator.engine import run
from qswarm.simulator.metrics import (
    TaskTimeline, cell_episodes, promotion_recall, snapshot_world_truth, SIM_T0,
)

OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.55, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.55, "exam.ENTRY_MARGIN": 0,
    "exam.ENTRY_GRACE_DAYS": 3, "exam.ENTRY_CORROB_S2S3": 3.0,
    "demote.FC_NET": 1, "demote.FC_DAYS": 2.0,
    "demote.FC_ITEMS": 20, "demote.FC_PASS": 0.55,
    "roster.scout": 12, "roster.auditor": 10, "roster.reviewer": 8,
    "roster.verifier": 6,
}

seed = int(sys.argv[1]) if len(sys.argv) > 1 else 11
res = run(seed=seed, n_days=30, overrides=OVERRIDES, replay_every=10, db_path="results/_diag_led.db", keep_db=True)
print("status:", res.status_counts, "cells:", res.cells_count)

evs = res.events
nom = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-fast:")]
print("nominations:", len(nom), "by role:", Counter(str(e.payload['role']) for e in nom),
      "family-level:", sum(1 for e in nom if "/" not in str(e.payload["node"])))
corrob = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-corroborated:")]
print("corroborated activations:", len(corrob))
acts = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "active"]
print("total activations:", len(acts), "by day:", dict(sorted(Counter(res.timeline.day_frac(e.ts) // 1 for e in acts).items())))
fails = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "shadow" and str(e.payload.get("from_status")) == "candidate"]
print("exam fails (candidate->shadow):", len(fails))
print("stuck candidates (last status candidate):", res.status_counts.get("candidate", 0))

# recall decomposition
from qswarm.kernel import KernelLedger
led = KernelLedger("results/_diag_led.db")
truth = res.truth
tl = res.timeline
r = promotion_recall(led, truth, tl)
print("recall:", r)
# denominator by role
traffic = set()
for e in led.read(kinds=("deposit",)):
    if tl.cum_tasks(int(res.timeline.day_frac(e.ts))) > 500:
        continue
    role = str(e.payload["role"])
    path = e.payload["taxonomy_path"]
    for i in range(len(path)):
        traffic.add((role, "/".join(path[: i + 1])))
den = Counter()
for role, node in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        den[role] += 1
num = Counter()
eps = cell_episodes(led)
for (role, node) in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        ep = eps.get((a, role, node))
        if ep is not None and ep.first_active_ts is not None and \
                res.timeline.cum_tasks(int(res.timeline.day_frac(ep.first_active_ts))) <= 500:
            num[role] += 1
print("recall num/den by role:", dict(num), "/", dict(den))
# TTA for experts
from qswarm.simulator.metrics import median_time_to_active
print("TTA:", median_time_to_active(led, truth, tl))
experts = [a for a in truth.agents if truth.archetype[a] == "expert"]
print("experts:", experts, "expert family:", {a: truth.expert_family[a] for a in experts})
for a in experts:
    ef = truth.expert_family[a]
    for (key, ep) in eps.items():
        if key.agent_id == a and ep.first_deposit_ts and key.node.split("/")[0] == ef:
            fa = ep.first_active_ts
            print(f"   expert cell {key}: first_active={'Y' if fa else 'N'}",
                  "" if not fa else f"t={res.timeline.cum_tasks(int(res.timeline.day_frac(fa)))}")
led.close()
Path("results/_diag_led.db").unlink(missing_ok=True)
Path("results/_diag_led.db-wal").unlink(missing_ok=True)
Path("results/_diag_led.db-shm").unlink(missing_ok=True)
