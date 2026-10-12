"""verify_error 旋钮扫描——SPEC-4 §3 实验矩阵的单变量预演（S2 饥饿传导信号侧）。

对 §3 的扫描点 verify_error ∈ {0.2, 0.35, 0.5} × 3 种子跑 verdict→deposit 流，
输出：S2 触达/结论/出错率、S2 存款供给（每千 finding 的 S2 行数）、三层存款
构成。验证口径：verify_error 只压低判对分支确认率（1−ve），判错分支结构不变
（0.1 错误确认 + 0.5 阻断证据）；S1/S3 供血不受影响（S2 饥饿不中断闭环，§6.3）。

用法：python3 experiments/sweep_verify_error.py   （仓库根目录；纯 stdlib，秒级）
输出：stdout 表格 + experiments/sweep_verify_error.csv
"""

from __future__ import annotations

import csv
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import RunMode, SignalTier, VerifyOutcome, load_params, load_taxonomy
from qswarm.simulator import (
    AgentPool,
    TaskGenerator,
    VerdictParams,
    attribute_deposits,
    run_findings_flow,
)

VE_POINTS = (0.20, 0.35, 0.50)
SEEDS = (11, 22, 33)
N_DAYS = 120

PARAMS = load_params()
TAX = load_taxonomy()


def run_one(ve: float, seed: int) -> dict:
    pool = AgentPool.from_params(PARAMS, TAX, seed=seed)
    tasks = TaskGenerator(PARAMS, TAX, seed=seed).generate(N_DAYS)
    flow = run_findings_flow(tasks, pool, seed=seed, params=PARAMS,
                             vparams=VerdictParams(verify_error=ve))
    attempts = [r for _, r in flow if r.s2_triggered]
    real = [r for r in attempts if r.truth_real]
    tiers = Counter()
    n_deps = 0
    for ctx, res in flow:
        rows = attribute_deposits(ctx, res, PARAMS)
        n_deps += len(rows)
        for p in rows:
            tiers[p.signal_tier] += 1
    n = len(flow)
    return {
        "verify_error": ve,
        "seed": seed,
        "findings": n,
        "s2_attempts": len(attempts),
        "s2_reach": len(attempts) / n,
        "verify_error_rate": (
            sum(1 for r in attempts if r.s2.outcome is VerifyOutcome.VERIFY_ERROR) / len(attempts)
            if attempts else float("nan")
        ),
        "p_real_confirmed": (
            sum(1 for r in real if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(real)
            if real else float("nan")
        ),
        "s2_rows_per_1k": tiers[SignalTier.S2] / n * 1000.0,
        "s1_rows_per_1k": tiers[SignalTier.S1] / n * 1000.0,
        "s3_rows_per_1k": tiers[SignalTier.S3] / n * 1000.0,
        "full_share": sum(1 for c, _ in flow if c.task.mode is RunMode.FULL) / n,
    }


def main() -> None:
    rows = [run_one(ve, seed) for ve in VE_POINTS for seed in SEEDS]
    fields = list(rows[0])
    out_csv = Path(__file__).parent / "sweep_verify_error.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"verify_error 旋钮扫描（{N_DAYS} 日 × {len(SEEDS)} 种子，均值±std）")
    print(f"{'ve':>5} {'S2触达':>9} {'ver_err率':>10} {'判对确认':>9} "
          f"{'S2行/1k':>9} {'S1行/1k':>9} {'S3行/1k':>9}")
    for ve in VE_POINTS:
        grp = [r for r in rows if r["verify_error"] == ve]

        def m(key: str) -> str:
            vals = [r[key] for r in grp]
            return f"{statistics.fmean(vals):.3f}±{statistics.pstdev(vals):.3f}"

        print(f"{ve:>5.2f} {m('s2_reach'):>9} {m('verify_error_rate'):>10} "
              f"{m('p_real_confirmed'):>9} {m('s2_rows_per_1k'):>9} "
              f"{m('s1_rows_per_1k'):>9} {m('s3_rows_per_1k'):>9}")
    print(f"\nCSV 已写出：{out_csv}")


if __name__ == "__main__":
    main()
