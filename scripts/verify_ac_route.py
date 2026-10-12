#!/usr/bin/env python3
"""kernel.route 验收标准逐条数值演示（taskflow 验收台举证用）。

直跑内核实现 qswarm/kernel/route.py + cells.py，不经 pytest，对三条验收标准
各给出可核对的具体数字：
  ac_muz07uljpsgzq9yf  黄金用例：固定 cells 下 tilt 数值 / 专员集合 / 选择结果
                       与 Fraction 手算一致（T_MIN/T_MAX 夹取两端 + T_BASE 中点
                       + 带内无夹取）
  ac_muz07ulj6ap4p4h5  K=3 上限：4 个 active 格子只取 C* 降序前 3
  ac_muz07uljvsu4qhfe  探索通道 20% 均匀保底大样本频率（容差内，含 floor=0 对照）；
                       同种子两次调用路由序列逐位一致

用法：python3 scripts/verify_ac_route.py   （退出码 0 = 全部断言通过）
"""

from __future__ import annotations

import random
import sys
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import load_params
from qswarm.kernel import (
    FixedClock,
    KernelLedger,
    active_specialists,
    adaptive_tilt,
    agg_cells,
    route,
)

T0 = "2030-01-01T00:00:00Z"
P = load_params()                    # T_BASE=0.70 GAMMA=1.0 [0.50,0.90] floor=0.20


@dataclass(frozen=True)
class Task:
    task_id: str
    role: str
    family: str
    type: str | None = None


def deposit(db: Path, agent: str, *, direction: int = 1, tier: str = "S3",
            weight: float = 1.0, role: str = "auditor",
            path: tuple[str, ...] = ("injection", "sqli")) -> None:
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        led.append("deposit", {
            "finding": None, "cluster": None, "agent": agent, "role": role,
            "taxonomy_path": list(path), "signal_tier": tier,
            "direction": direction, "weight": weight, "origin": "prod",
        })


def activate(db: Path, agent: str, node: str = "injection/sqli",
             role: str = "auditor") -> None:
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        led.append("promotion", {
            "agent": agent, "role": role, "node": node, "exam_id": "ER-X",
            "from_status": "shadow", "to_status": "active",
        })


def view(db: Path):
    with KernelLedger(db) as led:
        return agg_cells(led, T0)


def head(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def shrink(n: int, c_raw: Fraction, parent: Fraction) -> Fraction:
    """§5.1 收缩一步：(n_eff·C_raw + λ·C*_parent)/(n_eff+λ)，λ=5。"""
    return (Fraction(n) * c_raw + Fraction(5) * parent) / (Fraction(n) + 5)


def chain4(n: int, c_raw: Fraction) -> Fraction:
    """存款只带 (injection, sqli) ⇒ agent/role/family/type 四层同质量连收 4 次。"""
    root = Fraction(1, 2)
    c = shrink(n, c_raw, root)
    c = shrink(n, c_raw, c)
    c = shrink(n, c_raw, c)
    return shrink(n, c_raw, c)       # type 层 = 路由读数


def chain3(n: int, c_raw: Fraction) -> Fraction:
    """家族层 C*（探索通道权重读数）：agent→role→family 三级收缩。"""
    root = Fraction(1, 2)
    c = shrink(n, c_raw, root)
    c = shrink(n, c_raw, c)
    return shrink(n, c_raw, c)       # family 层 = 探索权重


class ScriptedRng:
    """random() 按脚本弹值（耗尽补 0.0），记录调用轨迹。"""

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self.calls: list[str] = []

    def random(self) -> float:
        self.calls.append("random")
        return self._values.pop(0) if self._values else 0.0


def ac1_golden() -> None:
    head("ac_muz07uljpsgzq9yf  黄金用例：tilt 手算一致（T_MIN/T_MAX 两端 + T_BASE 中点 + 带内）")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(3):
            deposit(db, "a", direction=1)
        deposit(db, "a", tier="S2", direction=-1, weight=0.5)
        deposit(db, "b", direction=1)
        activate(db, "a")
        v = view(db)
        c_type = chain4(7, Fraction(6, 7))                # n=7, C_raw=6/7
        assert c_type == Fraction(35101, 41472)
        base = 1.0                                        # b 只 1 正 ⇒ LOO 基线 1.0
        raw = 0.70 + float(c_type) - base                 # 0.546432… 带内
        got = route(Task("T1", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), P)
        print(f"[带内]  手算 C*(a)=35101/41472={float(c_type):.9f}  base={base}")
        print(f"[带内]  手算 raw={raw:.12f}  实现 tilt={got.tilt!r} raw_tilt={got.raw_tilt!r}")
        print(f"[带内]  specs={got.specs} assignee={got.assignee!r} channel={got.channel}")
        assert got.tilt == got.raw_tilt and abs(got.tilt - raw) < 1e-12
        assert P.tilt.T_MIN < raw < P.tilt.T_MAX          # 两端都不 binding
        assert got.specs == ("a",) and got.assignee == "a"

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(4):
            deposit(db, "a", direction=1)
        deposit(db, "b", direction=1)
        deposit(db, "b", direction=-1)
        activate(db, "a")
        v = view(db)
        c_type = chain4(8, Fraction(1))                   # n=8, C_raw=1
        assert c_type == Fraction(56497, 57122)
        base = 0.5                                        # b 1正1负 ⇒ 基线 0.5
        raw = 0.70 + float(c_type) - base                 # 1.189… > T_MAX
        got = route(Task("T2", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), P)
        print(f"[T_MAX] 手算 C*(a)=56497/57122={float(c_type):.9f}  base={base}")
        print(f"[T_MAX] 手算 raw={raw:.12f}  实现 tilt={got.tilt!r} raw_tilt={got.raw_tilt!r}")
        print(f"[T_MAX] tilt==T_MAX={P.tilt.T_MAX} -> {got.tilt == P.tilt.T_MAX}，"
              f"assignee={got.assignee!r}")
        assert got.tilt == P.tilt.T_MAX and abs(got.raw_tilt - raw) < 1e-12
        assert raw > P.tilt.T_MAX and got.specs == ("a",) and got.assignee == "a"

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, "a", direction=-1)
        for _ in range(3):
            deposit(db, "b", direction=1)
        activate(db, "a")
        v = view(db)
        c_type = chain4(2, Fraction(0))                   # n=2, C_raw=0
        assert c_type == Fraction(625, 4802)
        base = 1.0                                        # b 3 正 ⇒ 基线 1.0
        raw = 0.70 + float(c_type) - base                 # −0.1698… < T_MIN
        got = route(Task("T3", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), P)
        print(f"[T_MIN] 手算 C*(a)=625/4802={float(c_type):.9f}  base={base}")
        print(f"[T_MIN] 手算 raw={raw:.12f}  实现 tilt={got.tilt!r} raw_tilt={got.raw_tilt!r}")
        print(f"[T_MIN] tilt==T_MIN={P.tilt.T_MIN} -> {got.tilt == P.tilt.T_MIN}，"
              f"assignee={got.assignee!r}")
        assert got.tilt == P.tilt.T_MIN and abs(got.raw_tilt - raw) < 1e-12
        assert raw < P.tilt.T_MIN and got.specs == ("a",) and got.assignee == "a"

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, "a", direction=1)
        deposit(db, "a", direction=-1)
        deposit(db, "b", direction=1)
        deposit(db, "b", direction=-1)
        activate(db, "a")
        v = view(db)
        # C*(a)：0.5 是收缩不动点 ⇒ 全链精确 1/2；LOO 基线 = 1/2
        c = chain4(4, Fraction(1, 2))
        assert c == Fraction(1, 2)
        assert v.c_star("a", "auditor", "injection/sqli") == 0.5
        assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") == 0.5
        raw = 0.70 + 0.5 - 0.5                            # = T_BASE 精确
        got = route(Task("T4", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), P)
        print(f"[中点]  手算 C*(a)=1/2（收缩不动点）  base=1/2")
        print(f"[中点]  手算 raw=T_BASE={raw}  实现 tilt={got.tilt!r} raw_tilt={got.raw_tilt!r}")
        print(f"[中点]  T_BASE==频带中点(0.5+0.9)/2={P.tilt.T_BASE}，"
              f"严格带内 {P.tilt.T_MIN} < {raw} < {P.tilt.T_MAX}")
        assert got.tilt == P.tilt.T_BASE == got.raw_tilt == 0.70
        assert P.tilt.T_BASE == (P.tilt.T_MIN + P.tilt.T_MAX) / 2
        assert P.tilt.T_MIN < raw < P.tilt.T_MAX
        assert adaptive_tilt(0.5, 0.5, tilt=P.tilt) == (0.70, 0.70)
        assert got.specs == ("a",) and got.assignee == "a"


def ac2_spec_cap() -> None:
    head("ac_muz07ulj6ap4p4h5  K=3 上限：4 个 active 格子只取 C* 降序前 3")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(4):
            deposit(db, "s1", direction=1)
        for _ in range(2):
            deposit(db, "s2", direction=1)
        deposit(db, "s3", direction=1)
        deposit(db, "s4", direction=1)
        deposit(db, "s4", direction=-1)
        for s in ("s1", "s2", "s3", "s4"):
            activate(db, s)
        v = view(db)
        exp = {
            "s1": chain4(8, Fraction(1)),       # 56497/57122
            "s2": chain4(4, Fraction(1)),       # 12497/13122
            "s3": chain4(2, Fraction(1)),       # 4177/4802
            "s4": chain4(4, Fraction(1, 2)),    # 1/2 不动点
        }
        assert exp["s1"] == Fraction(56497, 57122)
        assert exp["s2"] == Fraction(12497, 13122)
        assert exp["s3"] == Fraction(4177, 4802)
        assert exp["s4"] == Fraction(1, 2)
        all_specs = active_specialists(v, "auditor", "injection/sqli")
        print(f"active 专员全量（不截断）：{[(a, f'{float(c):.6f}') for a, c in all_specs]}")
        print(f"手算 C*：s1=56497/57122 s2=12497/13122 s3=4177/4802 s4=1/2")
        assert [a for a, _ in all_specs] == ["s1", "s2", "s3", "s4"]
        assert len(all_specs) == 4
        for a, c in all_specs:
            assert abs(c - float(exp[a])) < 1e-12
        got = route(Task("T5", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), P)
        loo_pos, loo_total = 4, 5                     # 基线剔 s1：s2 2正 + s3 1正 + s4 1正1负
        raw = 0.70 + float(exp["s1"]) - loo_pos / loo_total
        print(f"route().specs = {got.specs}  （len={len(got.specs)} == K=3，s4 被截）")
        print(f"tilt 手算：0.70 + {float(exp['s1']):.9f} − {loo_pos}/{loo_total} = {raw:.12f}"
              f"  实现 raw_tilt={got.raw_tilt!r}")
        print(f"assignee={got.assignee!r}（零载 least_loaded ⇒ specs[0]=s1）")
        assert got.specs == ("s1", "s2", "s3") and "s4" not in got.specs
        assert len(got.specs) == 3
        assert abs(got.raw_tilt - raw) < 1e-12
        assert got.assignee == "s1"


def ac3_explore_and_repro() -> None:
    head("ac_muz07uljvsu4qhfe  探索通道 20% 均匀保底（大样本频率）+ 同种子逐位可复现")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        for _ in range(4):
            deposit(db, "hot", direction=1)
        for _ in range(8):
            deposit(db, "cold", direction=-1)
        v = view(db)
        task = Task("TS", "auditor", "injection", "sqli")
        pool = ["cold", "hot"]
        w_hot = float(chain3(8, Fraction(1)))          # 家族层链值 ≈0.97155
        w_cold = float(chain3(16, Fraction(0)))        # ≈0.00675
        p_weighted = w_cold / (w_hot + w_cold)
        p_cold = 0.8 * p_weighted + 0.2 * 0.5
        print(f"家族层权重手算：w_hot≈{w_hot:.6f}  w_cold≈{w_cold:.6f}")
        print(f"P(cold|加权)={p_weighted:.6f}  ⇒  P(cold)=0.8·{p_weighted:.6f}+0.2·0.5"
              f"={p_cold:.6f}")

        n = 100_000
        n_uniform = n_cold = 0
        rng = random.Random(20260929)
        for _ in range(n):
            d = route(task, v, rng, P, pool=pool)
            assert d.channel == "explore" and d.tilt is None and d.specs == ()
            n_uniform += bool(d.uniform_pick)
            n_cold += d.assignee == "cold"
        f_uniform, f_cold = n_uniform / n, n_cold / n
        print(f"floor=0.20，{n} 次：均匀分支频率={f_uniform:.5f}（期望 0.20，容差 0.01）"
              f"  cold 频率={f_cold:.5f}（期望 {p_cold:.5f}，容差 0.01）")
        assert abs(f_uniform - 0.20) <= 0.01
        assert abs(f_cold - p_cold) <= 0.01
        assert f_cold > 0.05

        p0 = load_params().model_copy(deep=True)
        p0.explore.UNIFORM_FLOOR = 0.0
        n_cold0 = 0
        rng0 = random.Random(20260929)
        for _ in range(n):
            d = route(task, v, rng0, p0, pool=pool)
            assert d.uniform_pick is False
            n_cold0 += d.assignee == "cold"
        f_cold0 = n_cold0 / n
        print(f"floor=0 对照：cold 频率={f_cold0:.5f}（期望 {p_weighted:.5f}）"
              f"  保底抬升 {f_cold / f_cold0:.1f} 倍")
        assert abs(f_cold0 - p_weighted) <= 0.003 and f_cold0 < 0.02

        # 同种子逐位可复现：混合任务序列（类型级/家族级交替），记录全部决策字段
        db2 = Path(td) / "l2.db"
        for _ in range(4):
            deposit(db2, "a1", direction=1)
        for _ in range(2):
            deposit(db2, "a2", direction=1)
        deposit(db2, "b", direction=1)
        deposit(db2, "b", direction=-1)
        activate(db2, "a1")
        activate(db2, "a2")
        v2 = view(db2)

        def run_seq(seed: int, n_tasks: int = 300) -> list[tuple]:
            r = random.Random(seed)
            out = []
            for i in range(n_tasks):
                t = Task(f"T-{i:04d}", "auditor", "injection",
                         "sqli" if i % 2 == 0 else None)
                d = route(t, v2, r, P)
                out.append((d.assignee, d.channel, d.reason, d.tilt,
                            d.specs, d.uniform_pick))
            return out

        s1, s2 = run_seq(20260929), run_seq(20260929)
        print(f"同种子(20260929) 300 任务路由序列：逐位一致 -> {s1 == s2}")
        print(f"  序列通道分布：specialist={sum(d[1] == 'specialist' for d in s1)} "
              f"explore={sum(d[1] == 'explore' for d in s1)}")
        print(f"异种子(77) 序列不同 -> {s1 != run_seq(77)}")
        assert s1 == s2 and s1 != run_seq(77)
        assert {d[1] for d in s1} == {"specialist", "explore"}


if __name__ == "__main__":
    ac1_golden()
    ac2_spec_cap()
    ac3_explore_and_repro()
    print("\nALL ROUTE ACCEPTANCE DEMOS PASSED")
