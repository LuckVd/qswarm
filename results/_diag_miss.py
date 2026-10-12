import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qswarm.kernel import KernelLedger, CellKey
from qswarm.simulator.engine import run
from qswarm.simulator.exam import ExamWorldParams
from qswarm.simulator.metrics import cell_episodes, _tasks_done_by

SEED = int(sys.argv[1]) if len(sys.argv) > 1 else 11
OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.3, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.3, "exam.ENTRY_MARGIN": 1,
    "exam.TYPE_ENTRY_COMBINE": 0.25,
    "exam.ENTRY_GRACE_DAYS": 3, "exam.ENTRY_CORROB_S2S3": 2.0,
    "exam.ENTRY_WINDOW_DAYS": 12,
    "exam.COOLDOWN_DAYS": 1,
    "demote.FC_DAYS": 1, "demote.FC_ITEMS": 60, "demote.FC_MARGIN": 0.15,
    "demote.FC_CONSEC": 2, "demote.FC_BASE_EXAMS": 2,
    "roster.scout": 6, "roster.auditor": 5, "roster.reviewer": 4,
    "roster.verifier": 3,
}
res = run(seed=SEED, n_days=30, overrides=OVERRIDES, replay_every=0,
          db_path="results/_diag6.db", keep_db=True,
          exam=ExamWorldParams(weight_high=0.5, type_quiz_items=56, entry_items=12))
truth, tl = res.truth, res.timeline
led = KernelLedger("results/_diag6.db")
eps = cell_episodes(led)
evs = res.events
traffic = set()
for e in led.read(kinds=("deposit",)):
    if _tasks_done_by(tl, e.ts) > 500:
        continue
    role = str(e.payload["role"])
    path = e.payload["taxonomy_path"]
    for i in range(len(path)):
        traffic.add((role, "/".join(path[: i + 1])))
miss = []
tot = hits = 0
for role, node in traffic:
    for a in truth.top_fraction(role, node, frac=0.2, t=500):
        tot += 1
        ep = eps.get(CellKey(a, role, node))
        ok = ep is not None and ep.first_active_ts is not None and \
            _tasks_done_by(tl, ep.first_active_ts) <= 500
        if ok:
            hits += 1
        else:
            miss.append((role, node, a))
print(f"recall {hits}/{tot}")
# activation-level misses (precision side)
misses = []
total_acts = 0
for e in evs:
    if str(e.kind) == "promotion" and str(e.payload.get("to_status")) == "active":
        key = CellKey(str(e.payload["agent"]), str(e.payload["role"]), str(e.payload["node"]))
        if key in misses or any(m[0]==key.agent_id and False for m in []):
            pass
        t_act = int(_tasks_done_by(tl, e.ts))
        if any(k == key for k in total_acts*[1]) :
            continue
        total_acts += 1
        if str(e.payload["agent"]) not in truth.top_fraction(str(e.payload["role"]), str(e.payload["node"]), t=t_act):
            misses.append((key, t_act))
print(f"precision misses: {len(misses)} of ~{total_acts}")
for (k, t) in misses:
    print("   miss:", k.agent_id, k.role, k.node, "t_act", t, "arch", truth.archetype[k.agent_id])
print("recall-miss cells:", len(miss))
for m in miss[:12]:
    print("   ", m)
led.close()
for suf in ("", "-wal", "-shm"):
    Path(f"results/_diag6.db{suf}").unlink(missing_ok=True)
