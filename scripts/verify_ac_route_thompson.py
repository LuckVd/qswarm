#!/usr/bin/env python3
"""kernel.route_thompson 验收标准逐条数值演示（taskflow 验收台举证用）。

直跑内核实现 qswarm/kernel/route_thompson.py + simulator.engine 主循环本体，
对三条验收标准各给出可核对的具体数字（不经 pytest，证据可独立复跑）：
  ac_muz07uljujh70qbj  同世界同种子闭环：TS 路由与默认路由均可完整跑通并产出可比
                       regret 曲线（官方 simulator.regret_curve 口径；默认臂
                       specialist/explore 双通道齐触发，防对照退化）
  ac_muz07uljcftzax0l  默认 route() 行为不受影响：route.py 无 thompson 痕迹 +
                       黄金 tilt 手算用例复算一致（全量黄金由
                       tests/test_kernel_route.py 在门禁里背书）
  ac_muz07ulj0r1nfq6g  TS 采样经注入 RNG 可复现：同种子两次调用决策序列逐位一致、
                       异种子不同、空池不消耗随机数；引擎级 TS 臂同种子重跑
                       全轨迹 digest 逐位一致

产物：results/route_thompson/regret_curves.csv（两臂逐任务 regret 曲线）
用法：python3 scripts/verify_ac_route_thompson.py   （退出码 0 = 全部断言通过）
"""

from __future__ import annotations

import csv
import random
import sys
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import load_params
from qswarm.kernel import (
    FixedClock,
    KernelLedger,
    agg_cells,
    route,
    route_thompson,
)
from qswarm.kernel.route_thompson import TS_CHANNEL
from qswarm.simulator import TaskStreamParams, regret_curve, run

P = load_params()
OUT_DIR = Path(__file__).resolve().parents[1] / "results" / "route_thompson"

# 闭环场景：与 tests/test_sim_engine.py / test_kernel_route_thompson.py 同款标定
SEED = 11
STREAM = TaskStreamParams(lam=40.0)
OVERRIDES = {
    "tilt.GAMMA": 0,
    "promote.N": 3,
    "promote.N_S2S3": 0,
    "promote.DELTA": 0.01,
    "promote.M": 1,
    "demote.DORMANT_DAYS": 1,
}
KW = dict(seed=SEED, n_days=18, stream=STREAM, overrides=OVERRIDES, replay_every=6)


@dataclass(frozen=True)
class Task:
    """§5.2 伪码的 task：已带 taxonomy（family[/type]) 与 role。"""

    task_id: str
    role: str
    family: str
    type: str | None = None


def head(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def ac1_two_arm_closed_loop() -> None:
    head("ac_muz07uljujh70qbj  同世界同种子闭环：两臂完整跑通 + 可比 regret 曲线")
    d = run(**KW)                              # 默认臂：kernel.route（None 注入）
    t = run(router=route_thompson, **KW)       # TS 对照臂：仅 router 换臂
    print(f"router 回显：default={d.router_name}  thompson={t.router_name}")
    print(f"任务流：n={len(d.tasks)}（两臂同一条："
          f"{[x.task_id for x in d.tasks] == [x.task_id for x in t.tasks]}）")
    assert [x.task_id for x in d.tasks] == [x.task_id for x in t.tasks]

    from collections import Counter
    ch_d = Counter(r.channel for r in d.routing)
    ch_t = Counter(r.channel for r in t.routing)
    print(f"default 臂通道分布：{dict(ch_d)}  active 格收尾={d.status_counts.get('active', 0)}")
    print(f"thompson 臂通道分布：{dict(ch_t)}  active 格收尾={t.status_counts.get('active', 0)}")
    assert set(ch_d) == {"specialist", "explore"} and ch_d["specialist"] >= 10
    assert set(ch_t) == {TS_CHANNEL}
    assert all(r.assignee is not None for r in t.routing)
    assert len({r.assignee for r in t.routing}) >= 2
    assert d.replay_report.passed and t.replay_report.passed

    curve_d = regret_curve(d.tasks, d.truth, d.routing)
    curve_t = regret_curve(t.tasks, t.truth, t.routing)
    assert len(curve_d) == len(curve_t) == len(d.tasks)
    for name, curve in (("default", curve_d), ("thompson", curve_t)):
        gaps = [p.gap for p in curve]
        cums = [p.cum_regret for p in curve]
        assert all(g >= 0.0 for g in gaps) and cums[-1] > 0.0
        assert all(b >= a - 1e-12 for a, b in zip(cums, cums[1:]))
        print(f"{name:>9} 臂 regret：终点累计={cums[-1]:.4f}  终点均值="
              f"{curve[-1].mean_regret:.6f}  逐点 gap∈[{gaps[0]:.4f}, {max(gaps):.4f}]"
              f"  累计单调不减=True")
    assert d.metrics.regret_total == curve_d[-1].cum_regret
    assert t.metrics.regret_total == curve_t[-1].cum_regret

    seq_d = [r.assignee for r in d.routing]
    seq_t = [r.assignee for r in t.routing]
    diff = sum(a != b for a, b in zip(seq_d, seq_t))
    print(f"真对照：逐任务派单不同 {diff}/{len(seq_d)}；曲线逐点不同="
          f"{[p.gap for p in curve_d] != [p.gap for p in curve_t]}")
    assert seq_d != seq_t

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "regret_curves.csv"
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["arm", "i", "task_id", "t", "gap", "cum_regret", "mean_regret"])
        for arm, curve in (("default", curve_d), ("thompson", curve_t)):
            for i, p in enumerate(curve):
                w.writerow([arm, i, p.task_id, p.t,
                            repr(p.gap), repr(p.cum_regret), repr(p.mean_regret)])
    print(f"regret 曲线落盘：{out}（{2 * len(curve_d)} 行 = 2 臂 × {len(curve_d)} 任务）")


def ac2_default_route_untouched() -> None:
    head("ac_muz07uljcftzax0l  默认 route() 行为不受影响（黄金手算复算一致）")
    import inspect
    import qswarm.kernel.route as route_mod
    src = inspect.getsource(route_mod)
    assert "thompson" not in src.lower()
    print("静态：qswarm/kernel/route.py 全文无 thompson 痕迹（TS 臂只经 engine.router "
          "注入点换臂，默认臂代码路径不变）")
    assert route.__module__ == "qswarm.kernel.route"

    # 黄金 tilt 用例复算（verify_ac_route.py ac1 的「带内」场景同款）：3 正 1 负 S3
    # + 1 负 S2(w=0.5)，对手 b 1 正。C*(a) 四层收缩链手算 = 35101/41472。
    import tempfile
    T0 = "2030-01-01T00:00:00Z"

    def deposit(db, agent, *, direction, tier="S3", weight=1.0):
        with KernelLedger(db, clock=FixedClock(T0)) as led:
            led.append("deposit", {
                "finding": None, "cluster": None, "agent": agent, "role": "auditor",
                "taxonomy_path": ["injection", "sqli"], "signal_tier": tier,
                "direction": direction, "weight": weight, "origin": "prod",
            })

    def activate(db, agent):
        with KernelLedger(db, clock=FixedClock(T0)) as led:
            led.append("promotion", {
                "agent": agent, "role": "auditor", "node": "injection/sqli",
                "exam_id": "ER-X", "from_status": "shadow", "to_status": "active",
            })

    def shrink(n: int, c_raw: Fraction, parent: Fraction) -> Fraction:
        return (Fraction(n) * c_raw + Fraction(5) * parent) / (Fraction(n) + 5)

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(3):
            deposit(db, "a", direction=1)
        deposit(db, "a", tier="S2", direction=-1, weight=0.5)
        deposit(db, "b", direction=1)
        activate(db, "a")
        with KernelLedger(db) as led:
            v = agg_cells(led, T0)
        c_type = Fraction(35101, 41472)                   # 四层收缩链手算（n=7, 6/7）
        assert abs(v.c_star("a", "auditor", "injection/sqli") - float(c_type)) < 1e-12
        raw = 0.70 + float(c_type) - 1.0                  # LOO 基线：b 1 正 ⇒ 1.0

        class ScriptedRng:                                # 首抽 0.0 < tilt ⇒ tilt_hit
            def random(self) -> float:
                return 0.0

        got = route(Task("T1", "auditor", "injection", "sqli"), v, ScriptedRng(), P)
        print(f"手算 C*(a)=35101/41472={float(c_type):.9f}  raw=0.70+C*−1.0={raw:.12f}")
        print(f"实现  tilt={got.tilt!r} raw_tilt={got.raw_tilt!r} assignee={got.assignee!r}"
              f" channel={got.channel}")
        assert got.tilt == got.raw_tilt and abs(got.tilt - raw) < 1e-12
        assert got.specs == ("a",) and got.assignee == "a" and got.channel == "specialist"
    print("行为：黄金 tilt 数值/专员集合/派单结果与 Fraction 手算逐位一致"
          "（全量黄金用例由 tests/test_kernel_route.py 在门禁背书）")


def ac3_rng_reproducibility() -> None:
    head("ac_muz07ulj0r1nfq6g  TS 采样经注入 RNG 可复现（同种子逐位一致）")
    # ① 单元级：混合任务序列 300 次，同种子两次全字段逐位一致；异种子必不同
    import tempfile
    T0 = "2030-01-01T00:00:00Z"

    def deposit(db, agent, *, direction, path=("injection", "sqli")):
        with KernelLedger(db, clock=FixedClock(T0)) as led:
            led.append("deposit", {
                "finding": None, "cluster": None, "agent": agent, "role": "auditor",
                "taxonomy_path": list(path), "signal_tier": "S3",
                "direction": direction, "weight": 1.0, "origin": "prod",
            })

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(4):
            deposit(db, "a1", direction=1)
        for _ in range(2):
            deposit(db, "a2", direction=1)
        deposit(db, "a2", direction=-1)
        for _ in range(3):
            deposit(db, "b1", direction=1, path=("crypto", "tls"))
        with KernelLedger(db) as led:
            v = agg_cells(led, T0)

        def run_seq(seed: int, n: int = 300) -> list[tuple]:
            r = random.Random(seed)
            out = []
            for i in range(n):
                t = (Task(f"T-{i:04d}", "auditor", "injection", "sqli")
                     if i % 3 else Task(f"T-{i:04d}", "auditor", "crypto", "tls"))
                d = route_thompson(t, v, r, P)
                out.append((d.task_id, d.node, d.assignee, d.channel, d.reason,
                            d.specs, d.spec_c_stars))
            return out

        s1, s2 = run_seq(20260929), run_seq(20260929)
        assert s1 == s2 and s1 != run_seq(77)
        assert {row[3] for row in s1} == {TS_CHANNEL}
        assert len({row[2] for row in s1}) >= 3
        print(f"单元级：同种子(20260929) 300 任务两遍决策序列逐位一致=True；"
              f"异种子(77)不同=True；覆盖 agent 数={len({row[2] for row in s1})}")

        # ② 空池不消耗随机数（rng 状态逐位未动）
        r = random.Random(1)
        state = r.getstate()
        d = route_thompson(Task("T-E", "auditor", "injection", "sqli"), v, r, P,
                           pool=[], load={"x": 1}, max_load=0)
        assert d.assignee is None and d.reason == "empty_pool"
        assert r.getstate() == state
        print("空池：决策 assignee=None reason=empty_pool，rng.getstate() 逐位未动=True")

    # ③ 引擎级：TS 臂同种子重跑，全轨迹（事件+路由+度量）digest 逐位一致
    t1 = run(router=route_thompson, **KW)
    t2 = run(router=route_thompson, **KW)
    d1 = run(**KW)
    assert t1.digest == t2.digest and t1.digest != d1.digest
    print(f"引擎级：TS 臂同种子重跑 digest 一致=True（sha256={t1.digest[:16]}…）；"
          f"默认臂 digest 不同=True")


if __name__ == "__main__":
    ac1_two_arm_closed_loop()
    ac2_default_route_untouched()
    ac3_rng_reproducibility()
    print("\nALL ROUTE-THOMPSON ACCEPTANCE DEMOS PASSED")
