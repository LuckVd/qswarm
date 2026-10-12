"""SCRATCH: inspect v2-mechanics run — why is recall 0 / TTA nodata?"""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.simulator.engine import run

res = run(
    seed=11, n_days=30,
    overrides={
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
    },
    replay_every=10,
)
print("status_counts:", res.status_counts)
print("cells:", res.cells_count, "elapsed:", round(res.elapsed_s, 1))
kinds = res.event_kinds()
print("event kinds:", dict(kinds))
tr = Counter((t.kind, t.reason, t.from_status, t.to_status) for t in res.transitions)
print("transitions:")
for k, v in sorted(tr.items()):
    print("   ", k, v)
m = res.metrics.to_dict()
for k, v in m.items():
    print(f"  {k}: {v}")
# activation timing histogram
acts = [ep for ep in __import__("qswarm.simulator.metrics", fromlist=["cell_episodes"]).cell_episodes_db(res.db_path)] if False else None
from qswarm.simulator.metrics import cell_episodes, TaskTimeline
# ledger closed... use transitions instead
by_day = Counter()
for t in res.transitions:
    if t.to_status == "active":
        by_day[t.seq] += 0  # placeholder
import qswarm.simulator.metrics as M
evs = res.events
tl = res.timeline
first_active = {}
for e in evs:
    if str(e.kind).endswith("PROMOTION") and str(e.payload.get("to_status")) == "active":
        key = (e.payload["agent"], str(e.payload["role"]), e.payload["node"])
        first_active.setdefault(key, e.ts)
days = Counter(int(tl.day_frac(ts)) for ts in first_active.values())
print("first-activation by day:", dict(sorted(days.items())))
print("total first activations:", len(first_active))
# candidate stuck?
cand = Counter()
for e in evs:
    if str(e.kind).endswith("PROMOTION"):
        key = (e.payload["agent"], str(e.payload["role"]), e.payload["node"])
        cand[key] = str(e.payload.get("to_status"))
print("cells whose last promotion event = candidate:", sum(1 for v in cand.values() if v == "candidate"))
nomi = sum(1 for e in evs if str(e.kind).endswith("PROMOTION") and str(e.payload.get("exam_id", "")).startswith("entry-fast:"))
print("entry-fast nomination events:", nomi)
corrob = sum(1 for e in evs if str(e.kind).endswith("PROMOTION") and str(e.payload.get("exam_id", "")).startswith("entry-corroborated:"))
print("corroborated activations:", corrob)
exam_events = [e for e in evs if str(e.kind).endswith("EXAM_RESULT")]
print("exam_result events:", len(exam_events))
fc = [e for e in exam_events if "forcause" in str(e.payload.get("exam_id", ""))]
print("forcause exams:", len(fc), "passed:", sum(1 for e in fc if e.payload.get("correct")))
decay = [t for t in res.transitions if t.reason == "decay"]
print("decay demotions:", len(decay))
