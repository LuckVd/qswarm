"""SCRATCH: activation funnel for the x4 config, seed 22."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import KernelLedger
from qswarm.simulator.engine import run
from qswarm.simulator.metrics import cell_episodes
from qswarm.simulator.metrics import _tasks_done_by

OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.3, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.3, "exam.ENTRY_MARGIN": 1,
    "exam.ENTRY_GRACE_DAYS": 5, "exam.ENTRY_CORROB_S2S3": 1.0,
    "exam.COOLDOWN_DAYS": 2,
    "demote.FC_NET": 1, "demote.FC_DAYS": 2.0,
    "demote.FC_ITEMS": 20, "demote.FC_PASS": 0.55,
    "roster.scout": 6, "roster.auditor": 5, "roster.reviewer": 4,
    "roster.verifier": 3,
}
SEED = 22
res = run(seed=SEED, n_days=30, overrides=OVERRIDES, replay_every=0,
          db_path="results/_diag3.db", keep_db=True)
truth, tl = res.truth, res.timeline
evs = res.events
nom = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-fast:")]
retry = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-retry:")]
acts = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "active"]
corrob = [e for e in acts if str(e.payload.get("exam_id", "")).startswith("entry-corroborated:")]
dark = [e for e in acts if str(e.payload.get("exam_id", "")).startswith("promo:")]
print(f"nom={len(nom)} retry={len(retry)} acts={len(acts)} (corrob={len(corrob)}, dark={len(dark)})")
print("acts by day:", dict(sorted(Counter(int(tl.day_frac(e.ts)) for e in acts).items())))
print("status:", res.status_counts)

led = KernelLedger("results/_diag3.db")
eps = cell_episodes(led)
traffic = set()
for e in led.read(kinds=("deposit",)):
    if _tasks_done_by(tl, e.ts) > 500:
        continue
    role = str(e.payload["role"])
    path = e.payload["taxonomy_path"]
    for i in range(len(path)):
        traffic.add((role, "/".join(path[: i + 1])))
from qswarm.kernel import CellKey
num = Counter(); den = Counter()
miss = []
for role, node in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        den[role] += 1
        ep = eps.get(CellKey(a, role, node))
        ok = ep is not None and ep.first_active_ts is not None and \
            _tasks_done_by(tl, ep.first_active_ts) <= 500
        if ok:
            num[role] += 1
        else:
            miss.append((role, node, a,
                         "no_cell" if ep is None else
                         ("never_active" if ep.first_active_ts is None else
                          f"late({int(_tasks_done_by(tl, ep.first_active_ts))})")))
print("recall:", sum(num.values()), "/", sum(den.values()), "=", round(sum(num.values())/max(1,sum(den.values())), 3))
print("num by role:", dict(num), " den by role:", dict(den))
print("miss sample:", miss[:12])
print("miss counts by reason:", Counter(m[3].split("(")[0] for m in miss))
# nominations among denominator cells
nomcells = {(str(e.payload["agent"]), str(e.payload["role"]), str(e.payload["node"])) for e in nom}
ind = sum(1 for m in miss if (m[2], m[0], m[1]) in nomcells)
print("misses that were nominated:", ind, "of", len(miss))
led.close()
for suf in ("", "-wal", "-shm"):
    Path(f"results/_diag3.db{suf}").unlink(missing_ok=True)
