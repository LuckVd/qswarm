import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qswarm.kernel import KernelLedger, CellKey
from qswarm.simulator.engine import run
from qswarm.simulator.metrics import cell_episodes, _tasks_done_by

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 11
OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.25, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.25, "exam.ENTRY_MARGIN": 1,
    "exam.ENTRY_GRACE_DAYS": 3, "exam.ENTRY_CORROB_S2S3": 1.0,
    "exam.COOLDOWN_DAYS": 1,
    "demote.FC_DAYS": 2, "demote.FC_ITEMS": 60, "demote.FC_MARGIN": 0.18,
    "demote.FC_CONSEC": 2, "demote.FC_BASE_EXAMS": 2,
    "roster.scout": 6, "roster.auditor": 5, "roster.reviewer": 4,
    "roster.verifier": 3,
}
res = run(seed=SEED, n_days=30, overrides=OVERRIDES, replay_every=0,
          db_path="results/_diag4.db", keep_db=True)
truth, tl = res.truth, res.timeline
evs = res.events
nom = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("exam_id", "")).startswith("entry-fast:")]
acts = [e for e in evs if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "active"]
print(f"nom={len(nom)} acts={len(acts)}")
print("acts by day:", dict(sorted(Counter(int(tl.day_frac(e.ts)) for e in acts).items())))
led = KernelLedger("results/_diag4.db")
eps = cell_episodes(led)
traffic = set()
for e in led.read(kinds=("deposit",)):
    if _tasks_done_by(tl, e.ts) > 500:
        continue
    role = str(e.payload["role"])
    path = e.payload["taxonomy_path"]
    for i in range(len(path)):
        traffic.add((role, "/".join(path[: i + 1])))
num = Counter(); den = Counter(); miss = []
for role, node in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        den[role] += 1
        ep = eps.get(CellKey(a, role, node))
        ok = ep is not None and ep.first_active_ts is not None and \
            _tasks_done_by(tl, ep.first_active_ts) <= 500
        if ok: num[role] += 1
        else:
            miss.append((role, node, a,
                "no_cell" if ep is None else ("never" if ep.first_active_ts is None else f"late{int(_tasks_done_by(tl, ep.first_active_ts))}")))
print("recall:", sum(num.values()), "/", sum(den.values()), "=", round(sum(num.values())/max(1,sum(den.values())),3))
print("num:", dict(num), "den:", dict(den))
print("miss reasons:", Counter(m[3].split('(')[0] for m in miss))
nomset = {(str(e.payload["agent"]), str(e.payload["role"]), str(e.payload["node"])) for e in nom}
print("misses nominated:", sum(1 for m in miss if (m[2],m[0],m[1]) in nomset), "of", len(miss))
# precision decomposition by activation day
from qswarm.simulator.metrics import promotion_precision
print("precision:", promotion_precision(led, truth, tl))
late_acts = []
for e in acts:
    key = CellKey(str(e.payload["agent"]), str(e.payload["role"]), str(e.payload["node"]))
    t_act = int(_tasks_done_by(tl, e.ts))
    hit = str(e.payload["agent"]) in truth.top_fraction(str(e.payload["role"]), str(e.payload["node"]), t=t_act)
    if t_act > 500:
        late_acts.append(hit)
print("post-500 activations:", len(late_acts), "hits:", sum(late_acts))
led.close()
for suf in ("", "-wal", "-shm"):
    Path(f"results/_diag4.db{suf}").unlink(missing_ok=True)
