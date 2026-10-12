import sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qswarm.simulator.engine import run

OVERRIDES = {
    "promote.N": 10, "promote.DELTA": 0.10, "tilt.T_BASE": 0.7,
    "promote.M_PRORATE": True, "promote.UNIQUE_PER_FAMILY": True,
    "promote.WINDOW_ADVANTAGE": 2,
    "exam.ENTRY_DIRECT": 0.25, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.25, "exam.ENTRY_MARGIN": 1,
    "exam.ENTRY_GRACE_DAYS": 3, "exam.ENTRY_CORROB_S2S3": 2.0,
    "exam.ENTRY_WINDOW_DAYS": 12,
    "exam.TYPE_ENTRY_COMBINE": 0.25,
    "exam.COOLDOWN_DAYS": 1,
    "demote.FC_DAYS": 1, "demote.FC_ITEMS": 60, "demote.FC_MARGIN": 0.15,
    "demote.FC_CONSEC": 2, "demote.FC_BASE_EXAMS": 2,
    "roster.scout": 6, "roster.auditor": 5, "roster.reviewer": 4,
    "roster.verifier": 3,
}
res = run(seed=11, n_days=30, overrides=OVERRIDES, replay_every=0)
per = defaultdict(list)
for e in res.events:
    if str(e.kind) != "promotion":
        continue
    key = (e.payload["agent"], str(e.payload["role"]), e.payload["node"])
    eid = str(e.payload.get("exam_id", ""))
    per[key].append((int(res.timeline.day_frac(e.ts)), e.payload["from_status"], e.payload["to_status"], eid.split(":")[0]))
never = [(k, v) for k, v in per.items() if not any(x[2] == "active" for x in v)]
print("nominated-but-never-active:", len(never))
for k, v in never[:8]:
    print("  ", k, v)
