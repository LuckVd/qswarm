"""SCRATCH probe (not part of the package): offline feasibility of the v2 exam lane.

Questions:
  A. With per-(role,node) seats = top-ceil(0.2n) selected by exam accuracy
     (entry exam for family nodes, type quiz for type nodes), what (precision,
     recall) can be reached across (items, bar, margin)?
  B. How does roster scaling (x1 vs x3) change it?
Precision proxy ~ ① (lane-dominated activations), recall proxy ~ ② (lane
coverage of the denominator top cells within 500 tasks; the lane fires day 0).
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import load_taxonomy
from qswarm.simulator.exam import exam_item_pass_p
from qswarm.simulator.metrics import snapshot_world_truth
from qswarm.simulator.rng import derive_seed
from qswarm.simulator.world import AgentPool, WorldHiddenParams

TAX = load_taxonomy()
FAMS = list(TAX.families)
TYPES = [(f, t) for f in FAMS for t in TAX.families[f].types]
ROLES = ("auditor", "reviewer", "scout", "verifier")

ROSTER_X1 = {"scout": 6, "auditor": 5, "reviewer": 4, "verifier": 3}
ROSTER_X3 = {"scout": 18, "auditor": 15, "reviewer": 12, "verifier": 9}


def seats_k(n: int, frac: float) -> int:
    return max(1, math.ceil(frac * n))


def probe(seed: int, roster: dict, *, quiz_items: int, bar: float, margin: int,
          frac: float = 0.2, combine: float = 0.0) -> tuple[float, float, int, int]:
    pool = AgentPool.generate(roster, TAX, seed=seed, hidden=WorldHiddenParams())
    truth = snapshot_world_truth(pool)
    # -- exam outcomes (engine-identical substreams) --
    entry_acc: dict[tuple[str, str], float] = {}      # (agent, family) -> acc
    for a in pool.agents:
        for f in FAMS:
            q = a.capability(f, t=0, hidden=pool.hidden_params)
            p = exam_item_pass_p(q)
            rng = random.Random(derive_seed(seed, "exam", "entry", a.agent_id, f))
            acc = sum(rng.random() < p for _ in range(8)) / 8
            entry_acc[(a.agent_id, f)] = acc
    quiz_acc: dict[tuple[str, str, str], float] = {}  # (agent, family, type) -> acc
    for a in pool.agents:
        for f, t in TYPES:
            q = a.capability(f, t, t=0, hidden=pool.hidden_params)
            p = exam_item_pass_p(q)
            rng = random.Random(derive_seed(seed, "exam", "entry-type", a.agent_id, f, t))
            acc = sum(rng.random() < p for _ in range(quiz_items)) / quiz_items
            quiz_acc[(a.agent_id, f, t)] = acc

    hits = total_act = denom = denom_hits = 0
    for role in ROLES:
        agents = [a.agent_id for a in pool.by_role(__import__("qswarm.contracts", fromlist=["Role"]).Role(role))]
        if not agents:
            continue
        k = seats_k(len(agents), frac)
        for f in FAMS:
            nodes = [(f, None)] + [(f, t) for t in TAX.families[f].types]
            for (fam, typ) in nodes:
                node = fam if typ is None else f"{fam}/{typ}"
                true_top = set(truth.top_fraction(role, node, frac=frac, t=0))
                denom += len(true_top)
                if typ is None:
                    scored = sorted(
                        agents,
                        key=lambda a: (-entry_acc[(a, fam)], a),
                    )
                    corr = {a: entry_acc[(a, fam)] * 8 for a in agents}
                    items = 8
                else:
                    def score(a: str) -> float:
                        base = entry_acc[(a, fam)]
                        qa = quiz_acc[(a, fam, typ)]
                        return (1 - combine) * qa + combine * base
                    scored = sorted(agents, key=lambda a: (-score(a), a))
                    corr = {a: quiz_acc[(a, fam, typ)] * quiz_items for a in agents}
                    items = quiz_items
                sel = scored[:k]
                runner = corr[scored[k]] if k < len(scored) else -1.0
                ok_bar = min(corr[a] for a in sel) / items >= bar
                ok_margin = (min(corr[a] for a in sel) - runner) >= margin
                if not (ok_bar and ok_margin):
                    continue
                sel_set = set(sel)
                h = len(sel_set & true_top)
                hits += h
                total_act += len(sel_set)
                denom_hits += h
    prec = hits / total_act if total_act else float("nan")
    rec = denom_hits / denom if denom else float("nan")
    return prec, rec, total_act, denom


def main() -> None:
    print(f"{'roster':6s} {'items':>5s} {'bar':>5s} {'mrg':>3s} {'comb':>4s} "
          f"| {'precision(1)':>12s} {'recall(2)':>9s} {'acts':>5s} {'denom':>5s}  per-seed")
    for tag, roster in (("x1", ROSTER_X1), ("x3", ROSTER_X3)):
        for items in (8, 12, 16):
            for bar in (0.0, 0.5, 0.6):
                for margin in (0, 1, 2, 3):
                    per = []
                    for seed in (11, 22, 33):
                        p, r, na, nd = probe(seed, roster, quiz_items=items,
                                             bar=bar, margin=margin)
                        per.append((p, r))
                    pm = sum(x[0] for x in per) / 3
                    rm = sum(x[1] for x in per) / 3
                    _, _, na, nd = probe(11, roster, quiz_items=items, bar=bar, margin=margin)
                    flag = " <<<" if pm >= 0.90 and rm >= 0.70 else ""
                    print(f"{tag:6s} {items:5d} {bar:5.2f} {margin:3d} {0.0:4.1f} "
                          f"| {pm:12.4f} {rm:9.4f} {na:5d} {nd:5d}  "
                          + " ".join(f"{p:.3f}/{r:.3f}" for p, r in per) + flag)
    # combined-score variant at the most promising geometry
    print("--- combined score (type nodes: w*quiz + (1-w)*entry) ---")
    for tag, roster in (("x1", ROSTER_X1), ("x3", ROSTER_X3)):
        for items in (12, 16):
            for comb in (0.3, 0.5):
                for margin in (1, 2, 3):
                    per = []
                    for seed in (11, 22, 33):
                        p, r, *_ = probe(seed, roster, quiz_items=items, bar=0.0,
                                         margin=margin, combine=comb)
                        per.append((p, r))
                    pm = sum(x[0] for x in per) / 3
                    rm = sum(x[1] for x in per) / 3
                    flag = " <<<" if pm >= 0.90 and rm >= 0.70 else ""
                    print(f"{tag:6s} {items:5d} comb={comb:3.1f} mrg={margin} "
                          f"| prec {pm:.4f} recall {rm:.4f}  "
                          + " ".join(f"{p:.3f}/{r:.3f}" for p, r in per) + flag)


if __name__ == "__main__":
    main()
