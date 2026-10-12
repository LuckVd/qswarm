"""终检插桩：seed=33 下 ③median_time_to_active=None 的成因解剖（复用 RunResult）。"""
from __future__ import annotations

import collections

from qswarm.kernel.ledger import KernelLedger
from qswarm.simulator.engine import run
from qswarm.simulator.exam import ExamWorldParams
from qswarm.simulator.metrics import (
    cell_episodes, median_time_to_active, promotion_recall,
    split_node, _tasks_done_by,
)
from qswarm.simulator.verdict import VerdictParams
from qswarm.simulator.engine import SimClock
from qswarm.simulator.metrics import SIM_T0

DB = "results/_finalcheck_ledger_s33.db"

res = run(
    seed=33, n_days=30,
    overrides={"promote.N": 6, "promote.DELTA": 0.05, "tilt.T_BASE": 0.60},
    verdict=VerdictParams(verify_error=0.2),
    exam=ExamWorldParams(weight_high=0.3),
    replay_every=10, db_path=DB, keep_db=True,
)
truth, timeline = res.truth, res.timeline
clock = SimClock(SIM_T0)
ledger = KernelLedger(DB, clock=clock)

eps = cell_episodes(ledger)
arch = collections.Counter()
expert_act = []
for key, ep in eps.items():
    if not ep.activations:
        continue
    a = truth.archetype.get(key.agent_id)
    arch[a] += 1
    if a == "expert":
        ef = truth.expert_family.get(key.agent_id)
        fam_ok = ef is None or split_node(key.node)[0] == ef
        dur = (_tasks_done_by(timeline, ep.activations[0])
               - _tasks_done_by(timeline, ep.first_deposit_ts))
        expert_act.append((key.agent_id, key.role, key.node, ef, fam_ok, dur))
print("activated cells by archetype:", dict(arch))
print("EXPERT activated cells:", len(expert_act))
for row in expert_act[:12]:
    print("   ", row)
print("recall recompute:", promotion_recall(ledger, truth, timeline, frac=0.2, task_window=500))
print("③ median:", median_time_to_active(ledger, truth, timeline))
print("EXPERT agents:", {ag: truth.expert_family.get(ag)
                         for ag in truth.archetype if truth.archetype[ag] == "expert"})
print("DEGRADED agents:", {ag: truth.expert_family.get(ag)
                           for ag in truth.archetype if truth.archetype[ag] == "degraded"})
ledger.close()
