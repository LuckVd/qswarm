# -*- coding: utf-8 -*-
"""验收证据采集：kernel.promote_scan（设计 §7.3 专长孵化状态机）。

对照子任务四条验收标准逐项输出实测证据：
  ac_muz07uljdan2ltwy  账本片段对四判据逐条独立证伪（任一不满足不晋升，
                       全满足才转 candidate 并落 promotion 事件）；
  ac_muz07uljm4xn1d6a  Wilson LB/UB 与标准公式在 ≥3 组 (n, p̂) 手算对照一致
                       （本脚本用 Decimal 50 位精度独立重算，非复用实现代码）；
  ac_muz07uljb4f70nxa  连续 2 窗衰退触发熄灭、单窗不触发；60 天无任务转
                       dormant；dormant 格 n_eff≥5 才重激活；
  ac_muz07uljm7q2n3s2  promote_scan 幂等：同一账本重复执行两次，cells 状态
                       与账本事件不变（sha256 指纹对照）。

运行：python experiments/verify_ac_promote_scan.py   （秒级，全部内存临时库）
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import LedgerKind, load_params
from qswarm.kernel import (
    CellKey,
    FixedClock,
    KernelLedger,
    agg_cells,
    promote_scan,
    wilson_bounds,
    WILSON_Z,
)
from qswarm.kernel.promote_scan import evaluate_candidate_criteria

AS_OF_DT = datetime(2030, 6, 30, tzinfo=timezone.utc)
AS_OF = "2030-06-30T00:00:00Z"
PARAMS = load_params()

PASS_COUNT = 0
FAIL_COUNT = 0


def check(ac: str, label: str, ok: bool, detail: str = "") -> None:
    global PASS_COUNT, FAIL_COUNT
    mark = "PASS" if ok else "FAIL"
    if ok:
        PASS_COUNT += 1
    else:
        FAIL_COUNT += 1
    print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        print(f"      !! 验收项受影响: {ac}")


def days_ago(n: int) -> str:
    return (AS_OF_DT - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(db):
    return KernelLedger(db, clock=FixedClock(AS_OF))


def dep(db, ts, *, agent, role="auditor", path=("injection",), tier="S3", d=1,
        w=1.0, cluster=None, origin="prod"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("deposit", {
            "finding": None, "cluster": cluster, "agent": agent, "role": role,
            "taxonomy_path": list(path), "signal_tier": tier,
            "direction": d, "weight": w, "origin": origin,
        })


def fill_traffic(db, ts, n=30, agent="z", node="injection"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        for i in range(n):
            led.append("deposit", {
                "finding": None, "cluster": f"c{i:02d}", "agent": agent,
                "role": "auditor", "taxonomy_path": [node], "signal_tier": "S1",
                "direction": 1, "weight": 0.01, "origin": "prod",
            })


def promo_event(db, ts, agent, node="injection", *, exam_id, frm, to, role="auditor"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("promotion", {"agent": agent, "role": role, "node": node,
                                 "exam_id": exam_id, "from_status": frm,
                                 "to_status": to})


def demote_event(db, ts, agent, node="injection", *, reason, role="auditor"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("demotion", {"agent": agent, "role": role, "node": node,
                                "reason": reason})


def scan(db, **kw):
    with _open(db) as led:
        return promote_scan(led, AS_OF, **kw)


def promo_rows(db):
    with KernelLedger(db) as led:
        return led.read(kinds=(LedgerKind.PROMOTION,))


def demote_rows(db):
    with KernelLedger(db) as led:
        return led.read(kinds=(LedgerKind.DEMOTION,))


def cells_rows(db):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells"
            " ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


def ledger_fingerprint(db):
    """账本全事件指纹（seq/ts/kind/payload 排序后 sha256）。"""
    with KernelLedger(db) as led:
        blobs = [
            f"{e.seq}|{e.ts}|{e.kind.value}|" + json_dumps(e.payload)
            for e in led.read()
        ]
    return hashlib.sha256("\n".join(blobs).encode()).hexdigest()


def json_dumps(payload) -> str:
    import json
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def view_of(db):
    with KernelLedger(db) as led:
        return agg_cells(led, AS_OF)


def snapshot(db, agent="a", node="injection"):
    with KernelLedger(db) as led:
        return evaluate_candidate_criteria(
            led, AS_OF, CellKey(agent, "auditor", node))


def positive_ledger(db, *, a_count=5, a_tier="S3", a_weight=1.0, traffic=30):
    for _ in range(a_count):
        dep(db, AS_OF, agent="a", tier=a_tier, w=a_weight)
    dep(db, AS_OF, agent="b", d=1, w=1.0)
    dep(db, AS_OF, agent="b", d=-1, w=1.0)
    fill_traffic(db, AS_OF, n=traffic)


# ════════════════════════════════════════════════════════════════════

def ac_wilson() -> None:
    """ac_muz07uljm4xn1d6a：Wilson LB/UB 手算对照（Decimal 50 位独立重算）。"""
    print("\n── ac_muz07uljm4xn1d6a：Wilson LB/UB 与标准公式手算对照 ──")
    getcontext().prec = 50
    z = Decimal("1.64")
    check("ac_wilson", "WILSON_Z == 1.64（§7.3 原式分位）", WILSON_Z == 1.64)
    cases = [
        # (pos, neg)          n      p̂      期望 LB          期望 UB
        (Decimal("4.0"),  Decimal("1.0")),   # n=10, p̂=0.80
        (Decimal("7.0"),  Decimal("7.0")),   # n=28, p̂=0.50
        (Decimal("0.5"),  Decimal("1.5")),   # n=4,  p̂=0.25
        (Decimal("23.0"), Decimal("2.0")),   # n=50, p̂=0.92
        (Decimal("5.0"),  Decimal("0.0")),   # n=10, p̂=1.00
    ]
    n_groups = 0
    for pos, neg in cases:
        # 独立按 §7.3 原式重算（Decimal，50 位）：n = 2(pos+neg)，p̂ = pos/(pos+neg)
        n = 2 * (pos + neg)
        ph = pos / (pos + neg)
        z2 = z * z
        denom = 1 + z2 / n
        center = ph + z2 / (2 * n)
        spread = z * ((ph * (1 - ph) / n + z2 / (4 * n * n)).sqrt())
        exp_lb, exp_ub = (center - spread) / denom, (center + spread) / denom
        got_lb, got_ub = wilson_bounds(float(pos), float(neg))
        tol = 1e-9
        ok = (
            abs(got_lb - float(exp_lb)) < tol and abs(got_ub - float(exp_ub)) < tol
            and float(exp_lb) <= float(ph) <= float(exp_ub)
        )
        n_groups += 1
        check(
            "ac_wilson",
            f"(n={n}, p̂={ph}): LB {got_lb:.10f} = 手算 {exp_lb:.10f},"
            f" UB {got_ub:.10f} = 手算 {exp_ub:.10f}",
            ok,
        )
    check("ac_wilson", f"手算对照组数 {n_groups} ≥ 3", n_groups >= 3)


def ac_four_criteria() -> None:
    """ac_muz07uljdan2ltwy：四判据逐条独立证伪 + 全满足转 candidate。"""
    print("\n── ac_muz07uljdan2ltwy：四判据逐条独立证伪 ──")
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)

    # 正例：四判据全满足 → 转 candidate + 恰一条 promotion 事件
    db = root / "all_ok.db"
    positive_ledger(db)
    ts = scan(db)
    ok_all = (
        len(ts) == 1
        and (ts[0].from_status, ts[0].to_status) == ("shadow", "candidate")
        and ts[0].criteria.all_ok and ts[0].criteria.failed() == ()
    )
    promos = promo_rows(db)
    check("ac_4crit", "全满足 → shadow→candidate，criteria.all_ok",
          ok_all, f"transitions={[(t.from_status, t.to_status) for t in ts]}")
    check("ac_4crit", "落恰一条 promotion 事件且 to_status=candidate",
          len(promos) == 1 and promos[0].payload["to_status"] == "candidate")
    check("ac_4crit", "无 demotion / exam_result 事件",
          demote_rows(db) == [])
    cell = view_of(db).get("a", "auditor", "injection")
    check("ac_4crit", "cells.status 同扫落定为 candidate",
          cell.status == "candidate")
    check("ac_4crit", "n_eff 恰达 N=10（≥ 语义边界）",
          abs(cell.n_eff - 10.0) < 1e-9)

    # 证伪①a：n_eff=8 < 10，其余三条全满足
    db = root / "c1a.db"
    positive_ledger(db, a_count=4)
    ts = scan(db)
    snap = snapshot(db)
    check("ac_4crit", "证伪①a n_eff=8<10：不晋升且恰 hard 被证伪",
          ts == [] and snap.failed() == ("hard",)
          and snap.advantage_ok and snap.traffic_ok and snap.gate_ok)
    check("ac_4crit", "证伪①a：零 promotion 事件、格仍 shadow",
          len(promo_rows(db)) == 0
          and view_of(db).get("a", "auditor", "injection").status == "shadow")

    # 证伪①b：n_eff=10 达标但 n_eff_s2s3=0 < 3（纯 S1 堆量）
    db = root / "c1b.db"
    positive_ledger(db, a_count=20, a_tier="S1", a_weight=0.25)
    ts = scan(db)
    snap = snapshot(db)
    check("ac_4crit", "证伪①b n_eff=10 但 s2s3=0<3：不晋升且恰 hard 被证伪",
          ts == [] and snap.failed() == ("hard",)
          and abs(snap.n_eff - 10.0) < 1e-9 and abs(snap.n_eff_s2s3) < 1e-9)

    # 证伪②：LOO 基线被强者抬高 → LB < 基线+δ
    db = root / "c2.db"
    positive_ledger(db)
    for _ in range(9):
        dep(db, AS_OF, agent="b", d=1, w=1.0)
    dep(db, AS_OF, agent="b", d=-1, w=1.0)
    ts = scan(db)
    snap = snapshot(db)
    expected_base = 10.3 / 12.3
    check("ac_4crit", "证伪② LB 0.7880 < LOO 基线+δ：不晋升且恰 advantage 被证伪",
          ts == [] and snap.failed() == ("advantage",)
          and abs(snap.wilson_lb - 0.7880469046) < 1e-9
          and abs(snap.baseline - expected_base) < 1e-9
          and snap.hard_ok and snap.traffic_ok and snap.gate_ok)

    # 证伪③：90 天窗簇数 29 < M=30
    db = root / "c3.db"
    positive_ledger(db, traffic=29)
    ts = scan(db)
    snap = snapshot(db)
    check("ac_4crit", "证伪③ 簇数 29<30：不晋升且恰 traffic 被证伪",
          ts == [] and snap.failed() == ("traffic",) and snap.traffic == 29
          and snap.hard_ok and snap.advantage_ok and snap.gate_ok)

    # 证伪④：暗考失败后冷却期内
    db = root / "c4.db"
    positive_ledger(db)
    promo_event(db, days_ago(PARAMS.exam.COOLDOWN_DAYS - 1), "a",
                exam_id="EX-old", frm="candidate", to="shadow")
    ts = scan(db)
    snap = snapshot(db)
    check("ac_4crit", "证伪④ 冷却期内：不晋升且恰 gate 被证伪",
          ts == [] and snap.failed() == ("gate",)
          and snap.hard_ok and snap.advantage_ok and snap.traffic_ok)
    check("ac_4crit", "证伪④：账本只有既有 1 条失败事件，无新事件",
          len(promo_rows(db)) == 1)

    # 冷却期满 → 可再触发（判据④边界）
    db = root / "c4b.db"
    positive_ledger(db)
    promo_event(db, days_ago(PARAMS.exam.COOLDOWN_DAYS), "a",
                exam_id="EX-old", frm="candidate", to="shadow")
    ts = scan(db)
    check("ac_4crit", "冷却恰满期后可再晋升（事件数 1→2）",
          [t.reason for t in ts] == ["candidate"] and len(promo_rows(db)) == 2)

    tmp.cleanup()


def stub_exam(passed: bool):
    def _run(**kw):
        return SimpleNamespace(passed=passed, exam_id="EX-1", score=1.0)
    return _run


def ac_lifecycle() -> None:
    """ac_muz07uljb4f70nxa：衰退 2 窗 / 单窗；60 天休眠；n_eff≥5 重激活。"""
    print("\n── ac_muz07uljb4f70nxa：衰退 / 休眠 / 重激活 ──")
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)

    def active_specialist(db, agent, node, promo_days=100):
        promo_event(db, days_ago(promo_days), agent, node,
                    exam_id="E-seed", frm="candidate", to="active")

    # ① 连续 2 窗 UB < LOO 基线 → decay 熄灭（W=30 手算窗：窗0=31~0 天前、窗1=60~31）
    db = root / "decay2.db"
    active_specialist(db, "sp", "crypto")
    for d in (15, 16, 45, 46):
        dep(db, days_ago(d), agent="sp", path=("crypto",), d=-1)
    for d in (20, 22, 50, 52):
        dep(db, days_ago(d), agent="bp", path=("crypto",), d=1)
    ts = scan(db, window_days=30.0)
    demotes = demote_rows(db)
    cell = view_of(db).get("sp", "auditor", "crypto")
    check("ac_life", "连续 2 窗跌破 → demotion(decay)、active→shadow",
          [(t.from_status, t.to_status, t.reason, t.kind) for t in ts]
          == [("active", "shadow", "decay", "demotion")]
          and len(demotes) == 1 and demotes[0].payload["reason"] == "decay")
    check("ac_life", "熄灭后激活史保留（activated_at 不变）",
          cell.status == "shadow" and cell.activated_at == days_ago(100))

    # ② 单窗跌破（两个方向）→ 不熄灭
    ok_both = True
    for bad_first in (True, False):
        db = root / f"decay1_{bad_first}.db"
        active_specialist(db, "sp", "crypto")
        good, bad = (1, -1) if bad_first else (-1, 1)
        dep(db, days_ago(15), agent="sp", path=("crypto",), d=bad)
        dep(db, days_ago(16), agent="sp", path=("crypto",), d=bad)
        dep(db, days_ago(45), agent="sp", path=("crypto",), d=good)
        dep(db, days_ago(46), agent="sp", path=("crypto",), d=good)
        dep(db, days_ago(20), agent="bp", path=("crypto",), d=1)
        dep(db, days_ago(50), agent="bp", path=("crypto",), d=1)
        ts = scan(db, window_days=30.0)
        ok_both = ok_both and ts == [] and demote_rows(db) == [] and \
            view_of(db).get("sp", "auditor", "crypto").status == "active"
    check("ac_life", "单窗跌破（窗0/窗1 两方向）→ 不熄灭、保持 active", ok_both)

    # ③ 60 天无该类型任务 → dormant；59/60 边界不休眠
    ok_bounds = True
    for idle, expect in ((90, True), (60, False), (59, False)):
        db = root / f"dorm{idle}.db"
        active_specialist(db, "dz", "authz")
        dep(db, days_ago(idle), agent="dz", path=("authz",), d=1)
        ts = scan(db)
        if expect:
            ok_bounds = ok_bounds and \
                [(t.to_status, t.reason) for t in ts] == [("dormant", "dormant")] \
                and view_of(db).get("dz", "auditor", "authz").status == "dormant"
        else:
            ok_bounds = ok_bounds and ts == [] and \
                view_of(db).get("dz", "auditor", "authz").status == "active"
    check("ac_life", "60 天无任务 → dormant；59/60 天边界不休眠", ok_bounds)

    # ④ dormant 格 n_eff≥5 且流量回潮 → 重激活；n_eff<5 不动
    def dormant_cell(db, deposits):
        dep(db, days_ago(95), agent="rz", path=("config_dep",), d=1)
        promo_event(db, days_ago(94), "rz", "config_dep",
                    exam_id="E1", frm="candidate", to="active")
        demote_event(db, days_ago(80), "rz", "config_dep", reason="dormant")
        for tstamp, w in deposits:
            dep(db, tstamp, agent="rz", path=("config_dep",), w=w)

    db1 = root / "re1.db"
    dormant_cell(db1, [(days_ago(10), 1.0)] * 4)     # n_eff≈6.35 ≥ 5
    ts1 = scan(db1)
    check("ac_life", "dormant 格 n_eff≥5 + 流量回潮 → 快速重激活 promotion",
          [(t.from_status, t.to_status, t.reason, t.kind) for t in ts1]
          == [("dormant", "active", "reactivate", "promotion")]
          and view_of(db1).get("rz", "auditor", "config_dep").status == "active")

    db2 = root / "re2.db"
    dormant_cell(db2, [(days_ago(10), 1.0)] * 2)     # n_eff≈3.17 < 5
    ts2 = scan(db2)
    check("ac_life", "dormant 格 n_eff<5：历史再厚也不重激活",
          ts2 == [] and view_of(db2).get("rz", "auditor", "config_dep").status
          == "dormant")

    db3 = root / "re3.db"
    dormant_cell(db3, [(days_ago(70), 16.0)])        # n_eff≥5 但节点 70 天无任务
    ts3 = scan(db3)
    check("ac_life", "dormant 格 n_eff≥5 但节点未回潮：不重激活",
          ts3 == [] and view_of(db3).get("rz", "auditor", "config_dep").status
          == "dormant")

    tmp.cleanup()


def ac_idempotent() -> None:
    """ac_muz07uljm7q2n3s2：同一账本重复扫描，cells 与账本事件不变。"""
    print("\n── ac_muz07uljm7q2n3s2：幂等性 ──")
    tmp = tempfile.TemporaryDirectory()
    root = Path(tmp.name)
    db = root / "rich.db"

    # 五节点五种命运的一轮扫描
    positive_ledger(db)                                    # P: shadow→candidate
    promo_event(db, days_ago(100), "h", "deser", exam_id="E-seed",
                frm="candidate", to="active")
    dep(db, days_ago(10), agent="h", path=("deser",), d=1)
    dep(db, days_ago(12), agent="h2", path=("deser",), d=1)
    promo_event(db, days_ago(100), "sp", "crypto", exam_id="E-seed",
                frm="candidate", to="active")
    for d in (15, 16, 45, 46):
        dep(db, days_ago(d), agent="sp", path=("crypto",), d=-1)
    for d in (20, 21, 50, 51):
        dep(db, days_ago(d), agent="bp", path=("crypto",), d=1)
    promo_event(db, days_ago(100), "dz", "authz", exam_id="E-seed",
                frm="candidate", to="active")
    dep(db, days_ago(90), agent="dz", path=("authz",), d=1)
    dep(db, days_ago(95), agent="rz", path=("config_dep",), d=1)
    promo_event(db, days_ago(94), "rz", "config_dep",
                exam_id="E1", frm="candidate", to="active")
    demote_event(db, days_ago(80), "rz", "config_dep", reason="dormant")
    for _ in range(4):
        dep(db, days_ago(10), agent="rz", path=("config_dep",), w=1.0)

    t1 = scan(db, window_days=30.0)
    reasons = sorted(
        (t.agent_id, t.node, t.from_status, t.to_status, t.reason) for t in t1)
    expected = sorted([
        ("a", "injection", "shadow", "candidate", "candidate"),
        ("dz", "authz", "active", "dormant", "dormant"),
        ("rz", "config_dep", "dormant", "active", "reactivate"),
        ("sp", "crypto", "active", "shadow", "decay"),
    ])
    check("ac_idem", "首轮扫描产生 4 条流转（晋升/休眠/重激活/衰退各一）",
          reasons == expected, f"got {reasons}")

    fp1, cells1 = ledger_fingerprint(db), cells_rows(db)
    t2 = scan(db, window_days=30.0)
    fp2, cells2 = ledger_fingerprint(db), cells_rows(db)
    t3 = scan(db, window_days=30.0)
    fp3, cells3 = ledger_fingerprint(db), cells_rows(db)
    check("ac_idem", "第 2 次扫描零流转、账本指纹与 cells 逐字节不变",
          t2 == [] and fp2 == fp1 and cells2 == cells1)
    check("ac_idem", "第 3 次扫描仍不变（防隔次振荡）",
          t3 == [] and fp3 == fp1 and cells3 == cells1)
    print(f"      ledger sha256 = {fp1[:16]}…，cells 行数 = {len(cells1)}")

    tmp.cleanup()


def main() -> int:
    print(f"kernel.promote_scan 验收证据采集  AS_OF={AS_OF}")
    ac_wilson()
    ac_four_criteria()
    ac_lifecycle()
    ac_idempotent()
    print(f"\n总判定：{PASS_COUNT} PASS / {FAIL_COUNT} FAIL"
          + ("  → 全部通过" if FAIL_COUNT == 0 else "  → 存在失败！"))
    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
