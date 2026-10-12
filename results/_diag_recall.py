"""SCRATCH: deep recall decomposition for w1-style config."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import KernelLedger
from qswarm.simulator.engine import run
from qswarm.simulator.metrics import cell_episodes, snapshot_world_truth

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 11
OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.3, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.3, "exam.ENTRY_MARGIN": 0,
    "exam.ENTRY_GRACE_DAYS": 5, "exam.ENTRY_CORROB_S2S3": 1.0,
    "exam.COOLDOWN_DAYS": 2,
    "demote.FC_NET": 1, "demote.FC_DAYS": 2.0,
    "demote.FC_ITEMS": 20, "demote.FC_PASS": 0.55,
    "roster.scout": 12, "roster.auditor": 10, "roster.reviewer": 8,
    "roster.verifier": 6,
}
res = run(seed=SEED, n_days=30, overrides=OVERRIDES, replay_every=10)
truth, tl = res.truth, res.timeline
evs = res.events
nom = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-fast:")]
acts = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "active"]
corrob = [e for e in acts if str(e.payload.get("exam_id", "")).startswith("entry-corroborated:")]
fails = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "shadow"]
print(f"seed={SEED} nom={len(nom)} acts={len(acts)} (corrob={len(corrob)}) exam_fails={len(fails)}")
print("nom by role:", Counter(str(e.payload['role']) for e in nom))
print("nom family-level:", sum(1 for e in nom if '/' not in str(e.payload['node'])),
      " type-level:", sum(1 for e in nom if '/' in str(e.payload['node'])))
print("acts by day:", dict(sorted(Counter(int(tl.day_frac(e.ts)) for e in acts).items())))
print("corrob by day:", dict(sorted(Counter(int(tl.day_frac(e.ts)) for e in corrob).items())))

# recall decomposition (fix CellKey matching)
led = KernelLedger(":memory:")
led.close()
import tempfile
# rebuild from events not possible; use engine's in-memory — rerun with keep_db
res2 = run(seed=SEED, n_days=30, overrides=OVERRIDES, replay_every=0,
           db_path="results/_diag2.db", keep_db=True)
led = KernelLedger("results/_diag2.db")
eps = cell_episodes(led)
from qswarm.simulator.metrics import _tasks_done_by
traffic = set()
for e in led.read(kinds=("deposit",)):
    if _tasks_done_by(tl, e.ts) > 500:
        continue
    role = str(e.payload["role"])
    path = e.payload["taxonomy_path"]
    for i in range(len(path)):
        traffic.add((role, "/".join(path[: i + 1])))
num = Counter(); den = Counter()
miss_role_level = Counter()
for role, node in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        den[role] += 1
        ep = eps.get((a, role, node))
        ok = ep is not None and ep.first_active_ts is not None and \
            _tasks_done_by(tl, ep.first_active_ts) <= 500
        if ok:
            num[role] += 1
        else:
            level = "fam" if "/" not in node else "type"
            miss_role_level[(role, level)] += 1
print("recall:", sum(num.values()), "/", sum(den.values()), "=", round(sum(num.values())/max(1,sum(den.values())), 4))
print("num by role:", dict(num)); print("den by role:", dict(den))
print("misses by (role,level):", dict(miss_role_level))
# TTA
from qswarm.simulator.metrics import median_time_to_active
print("TTA:", median_time_to_active(led, truth, tl))
from qswarm.simulator.metrics import degradation_response, wrong_extinguish_rate
print("deg response:", degradation_response(led, truth, tl))
print("wrong extinguish:", wrong_extinguish_rate(led, truth))
led.close()
for suf in ("", "-wal", "-shm"):
    Path(f"results/_diag2.db{suf}").unlink(missing_ok=True)
