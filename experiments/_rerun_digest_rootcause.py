"""experiments/_rerun_digest_rootcause.py —— 复跑 digest 差异根因定位（一次性证据工具）。

背景：M1 预算旋钮落地后 configs/params.yaml 新增 3 个键（budget.FAST_CENTS /
COST_PER_L2_CENTS / COST_PER_S2_CENTS），canonical_trajectory() 含
params.model_dump()，因此新增键必然改变轨迹摘要——即使机制行为逐位不变。

本脚本对推荐组 seed=11 复跑一次，计算：
  d_full        —— 现状完整 canonical 轨迹摘要（应等于复跑 results.csv 中的 digest）；
  d_minus_m1    —— 同一轨迹、仅把 params.budget 中 3 个 M1 新键剔除后的摘要；
并对照 M0 官方矩阵同组同种子行的 digest（ca703220…）判定：
  d_minus_m1 == 旧 digest  ⇒  差异唯一来源 = 3 个新增配置键（机制轨迹逐位不变）；
  否则                     ⇒  存在机制性偏差（事件/路由/任务流有实质差异）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import SLIDING_WINDOW_DAYS
from qswarm.simulator.engine import run
from qswarm.simulator.exam import ExamWorldParams
from qswarm.simulator.verdict import VerdictParams

M1_BUDGET_KEYS = ("FAST_CENTS", "COST_PER_L2_CENTS", "COST_PER_S2_CENTS")
GROUP = "N06-D0.05-T0.60-E0.30-V0.20"
SEED = 11


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def main() -> int:
    res = run(
        seed=SEED, n_days=30,
        overrides={"promote.N": 6, "promote.DELTA": 0.05, "tilt.T_BASE": 0.6},
        verdict=VerdictParams(verify_error=0.2),
        exam=ExamWorldParams(weight_high=0.3),
        router=None, replay_every=10,
        promote_window_days=SLIDING_WINDOW_DAYS,
    )
    d_full = res.digest
    doc = json.loads(res.canonical_trajectory())
    budget = doc["params"]["budget"]
    removed = {k: budget.pop(k) for k in M1_BUDGET_KEYS if k in budget}
    d_minus_m1 = sha(json.dumps(doc, sort_keys=True, ensure_ascii=False))

    with open("results/experiments-full/results.csv", newline="", encoding="utf-8") as f:
        old = next(r for r in csv.DictReader(f)
                   if r["group_id"] == GROUP and r["seed"] == str(SEED))
    d_old = old["digest"]

    print(f"M1 新增 budget 键（已从变体剔除）: {removed}")
    print(f"d_full(现状完整)          = {d_full}")
    print(f"d_minus_m1(剔除3新键变体) = {d_minus_m1}")
    print(f"d_old(M0 官方矩阵同组)    = {d_old}")
    print(f"现状 digest 复现复跑批    = {d_full == '282e9f884ebb91d948fe5c81d09db942fdba78c7eda82bb165df21b484cbe7e3'}")
    verdict = (d_minus_m1 == d_old)
    print(f"ROOT_CAUSE_ADDITIVE_KEYS_ONLY = {str(verdict).lower()}")
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
