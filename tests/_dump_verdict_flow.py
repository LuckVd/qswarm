"""跨进程可复现性取证脚本（tests/test_verdict_deposits.py 的子进程夹具）。

以固定种子生成任务流 → verdict 流 → §6.5 存款流，全部字段以 canonical JSON
（sort_keys + repr 浮点往返）逐行写出。供父测试在**不同 PYTHONHASHSEED** 的
两个子进程里各跑一次后逐字节 diff：一致 ⇔ 流跨进程逐位可复现（不依赖进程内
哈希随机化、解释器实例或调用次序）。

用法：python _dump_verdict_flow.py --days N --seed S --out PATH
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from qswarm.contracts import load_params, load_taxonomy
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.simulator import (
    AgentPool,
    TaskGenerator,
    append_deposits,
    attribute_deposits,
    run_findings_flow,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--seed", type=int, default=2024)
    ap.add_argument("--out", type=str, required=True)
    args = ap.parse_args()

    params = load_params()
    tax = load_taxonomy()
    pool = AgentPool.from_params(params, tax, seed=2024)
    tasks = TaskGenerator(params, tax, seed=7).generate(args.days)
    flow = run_findings_flow(tasks, pool, seed=args.seed, params=params)

    lines: list[str] = []
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        for ctx, res in flow:
            votes = [
                {
                    "reviewer_id": v.reviewer_id,
                    "direction": v.direction,
                    "p_agree": repr(v.p_agree),
                    "z_own": repr(v.z_own),
                    "z_panel": repr(v.z_panel),
                    "z_agent": repr(v.z_agent),
                }
                for v in res.votes
            ]
            rec = {
                "finding_id": ctx.finding_id,
                "mode": ctx.task.mode.value,
                "truth_real": res.truth_real,
                "p_correct": repr(res.p_correct),
                "votes": votes,
                "s1_direction": res.s1_direction,
                "s2_triggered": res.s2_triggered,
                "s2_outcome": res.s2.outcome.value if res.s2 else None,
                "s2_blocking": res.s2.blocking if res.s2 else None,
                "s3_direction": res.s3.direction if res.s3 else None,
                "s3_lag": repr(res.s3.lag_days) if res.s3 else None,
                "s3_arrival": repr(res.s3.arrival_day) if res.s3 else None,
                "deposits": [
                    p.model_dump(mode="json")
                    for p in attribute_deposits(ctx, res, params)
                ],
            }
            lines.append(json.dumps(rec, sort_keys=True, ensure_ascii=False))
            append_deposits(led, attribute_deposits(ctx, res, params), run_id="R-XPROC")
        ledger_lines = [
            json.dumps(e.payload, sort_keys=True, ensure_ascii=False)
            for e in led.read(kinds=["deposit"])
        ]

    out = Path(args.out)
    out.write_text(
        "\n".join(lines) + "\n--- ledger ---\n" + "\n".join(ledger_lines) + "\n",
        encoding="utf-8",
    )
    print(f"findings={len(lines)} deposits={len(ledger_lines)}")


if __name__ == "__main__":
    main()
