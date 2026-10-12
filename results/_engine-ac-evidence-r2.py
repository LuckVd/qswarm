"""子任务 s12（simulator.engine）验收证据演示：三条验收标准逐条端到端自证。

场景与 tests/test_sim_engine.py 完全同参（KW_MAIN=18 日×λ=40 加速孵化；
KW_SHORT=8 日×λ=20 对照臂）。运行：python3 results/_engine-ac-evidence-r2.py
"""

from __future__ import annotations

import json
import sys

from qswarm.contracts import LedgerKind
from qswarm.kernel.route_thompson import TS_CHANNEL, route_thompson
from qswarm.simulator import ExamWorldParams, TaskStreamParams, run, run_s2_starvation

STREAM_MAIN = TaskStreamParams(lam=40.0)
OVERRIDES_MAIN = {
    "tilt.GAMMA": 0,
    "promote.N": 3,
    "promote.N_S2S3": 0,
    "promote.DELTA": 0.01,
    "promote.M": 1,
    "demote.DORMANT_DAYS": 1,
}
KW_MAIN = dict(seed=11, n_days=18, stream=STREAM_MAIN, overrides=OVERRIDES_MAIN, replay_every=6)
KW_SHORT = dict(seed=5, n_days=8, stream=TaskStreamParams(lam=20.0), replay_every=4)

NINE = {
    "promotion_precision", "promotion_recall", "median_time_to_active",
    "wrong_extinguish_rate", "degradation_response", "regret_total",
    "regret_per_task", "regret_late_over_early", "cold_start_length",
    "replay_equivalent",
}


def sig(r):
    return [
        (e.seq, e.ts, str(e.kind), json.dumps(e.payload, sort_keys=True))
        for e in r.events
    ]


def exam_deposits(r):
    return sum(
        1 for e in r.events
        if e.kind is LedgerKind.DEPOSIT and e.payload.get("origin") == "exam"
    )


def main() -> int:
    ok = True

    # ── ac_muz07uljrplfu9pv：固定种子两次 run，轨迹逐位一致 ──
    a = run(**KW_MAIN)
    b = run(**KW_MAIN)
    check1 = (
        sig(a) == sig(b)
        and a.canonical_trajectory() == b.canonical_trajectory()
        and a.digest == b.digest
        and a.metrics == b.metrics
        and a.day_log == b.day_log
        and len(a.transitions) == len(b.transitions) > 0
    )
    ok &= check1
    print(f"[AC1] 固定种子两次 run：digest={a.digest[:16]}…  一致={check1}  "
          f"(events={len(a.events)}, transitions={len(a.transitions)})")

    # ── ac_muz07ulj5fsczmjq：四类事件齐备 + 9 项度量 + T_BASE 覆盖生效 ──
    kinds = a.event_kinds()
    four = {k: kinds[k] for k in ("dispatch", "deposit", "promotion", "demotion")}
    record = a.metrics_record()
    four_ok = all(v > 0 for v in four.values()) and set(record) == NINE and len(record) == 10
    tbase = run(**{**KW_MAIN, "overrides": {**OVERRIDES_MAIN, "tilt.T_BASE": 0.9}})
    diff = sum(
        1 for x, y in zip(a.routing, tbase.routing) if x.assignee != y.assignee
    )
    spec_a = sum(1 for r in a.routing if r.channel == "specialist")
    spec_b = sum(1 for r in tbase.routing if r.channel == "specialist")
    over_ok = (
        a.params.tilt.T_BASE == 0.7 and tbase.params.tilt.T_BASE == 0.9
        and diff > 0 and spec_b > spec_a
        and a.digest != tbase.digest and a.metrics != tbase.metrics
    )
    ok &= four_ok and over_ok
    print(f"[AC2] 事件计数={four}  9项度量键齐备={set(record) == NINE}")
    print(f"[AC2] 度量记录={json.dumps(record, ensure_ascii=False)}")
    print(f"[AC2] T_BASE 0.7→0.9：分歧派单={diff}  specialist 通道 {spec_a}→{spec_b}  "
          f"轨迹不同={a.digest != tbase.digest}  生效={over_ok}")

    # ── ac_muz07ulj3tfuw7pu：exam_seeding 开关 + TS 路由替换（引擎层）──
    on = run(**KW_SHORT)
    off = run(**KW_SHORT, exam=ExamWorldParams(seeding=False))
    seed_ok = (
        on.exam.seeding is True and off.exam.seeding is False
        and exam_deposits(on) == 18 * 6 and exam_deposits(off) == 0
        and on.digest != off.digest
        and len(off.routing) == len(off.tasks) > 0
        and all(r.assignee is not None for r in off.routing)
    )
    ts = run(**KW_SHORT, router=route_thompson)
    ts_ok = (
        ts.router_name == "route_thompson" and on.router_name == "kernel.route"
        and len(ts.routing) > 0 and {r.channel for r in ts.routing} == {TS_CHANNEL}
        and {r.channel for r in on.routing} <= {"specialist", "explore"}
        and on.digest != ts.digest
        and on.replay_report.passed and ts.replay_report.passed
    )
    ok &= seed_ok and ts_ok
    print(f"[AC3] exam_seeding：on 存款={exam_deposits(on)}  off 存款={exam_deposits(off)}  "
          f"轨迹不同={on.digest != off.digest}  无播种不冷死={len(off.routing)} 单派单  生效={seed_ok}")
    print(f"[AC3] TS 替换：router_name={ts.router_name!r}  通道集="
          f"{sorted({r.channel for r in ts.routing})}  轨迹不同={on.digest != ts.digest}  生效={ts_ok}")

    # ── 附加：⑧ S2 饥饿传导驱动（跨运行度量走引擎）──
    s2 = run_s2_starvation(seed=3, n_days=3, stream=TaskStreamParams(lam=12.0), replay_every=0)
    s2_ok = set(s2.metrics_by_point) == {0.2, 0.35, 0.5} and isinstance(s2.report.passed, bool)
    ok &= s2_ok
    print(f"[S2⑧] 三档 verify_error 驱动 OK={s2_ok}  报告判定={s2.report.passed}")

    print(f"\nALL ACCEPTANCE CHECKS: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
