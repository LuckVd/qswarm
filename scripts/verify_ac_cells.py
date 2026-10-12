#!/usr/bin/env python3
"""agg_cells 验收标准逐条数值演示（taskflow 验收台举证用）。

直跑内核实现 qswarm/kernel/cells.py，对四条验收标准各给出可核对的具体数字：
  ac_muz07ulj9xqynze1  恰 30d/15d 折半、双倍龄期折 1/4、exam 走 15d 档
  ac_muz07uljm4465dwk  n_eff ≡ 2×(pos_w+neg_w)（随机账本）；S1/S2/S3 增量 0.1/1/2 当量
  ac_muz07uljd1vjjbyx  总量 < ε 的格子 C* 等于父层 C*
  ac_muz07ulja9oma87c  收缩黄金数值（Fraction 手算对照）+ LOO 基线剔自身

用法：python3 scripts/verify_ac_cells.py   （退出码 0 = 全部断言通过）
"""

from __future__ import annotations

import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.contracts import load_params
from qswarm.kernel import ROOT_PRIOR, FixedClock, KernelLedger, agg_cells

AS_OF = "2030-06-30T00:00:00Z"
T0 = "2030-01-01T00:00:00Z"
P = load_params()


def deposit(db: Path, ts: str, **kw) -> None:
    body = {
        "finding": None, "cluster": None,
        "agent": kw.get("agent", "a"), "role": kw.get("role", "auditor"),
        "taxonomy_path": list(kw.get("path", ("injection",))),
        "signal_tier": kw.get("tier", "S3"),
        "direction": kw.get("direction", 1),
        "weight": kw.get("weight", 1.0),
        "origin": kw.get("origin", "prod"),
    }
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("deposit", body)


def view(db: Path, as_of: str = AS_OF):
    with KernelLedger(db) as led:
        return agg_cells(led, as_of)


def head(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def ac1_decay() -> None:
    head("ac_muz07ulj9xqynze1  挥发：恰 30d/15d 折半、双倍龄期折 1/4、exam 走 15d 档")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, "2030-05-31T00:00:00Z", agent="prod_30d", weight=1.0)    # 恰 30d
        deposit(db, "2030-05-01T00:00:00Z", agent="prod_60d", weight=1.0)    # 恰 60d
        deposit(db, "2030-06-15T00:00:00Z", agent="exam_15d", weight=1.0, origin="exam")
        deposit(db, "2030-05-31T00:00:00Z", agent="exam_30d", weight=1.0, origin="exam")
        deposit(db, "2030-06-15T00:00:00Z", agent="prod_15d", weight=1.0)    # 对照
        v = view(db)
        p30 = v.get("prod_30d", "auditor", "injection").pos_w
        p60 = v.get("prod_60d", "auditor", "injection").pos_w
        e15 = v.get("exam_15d", "auditor", "injection").pos_w
        e30 = v.get("exam_30d", "auditor", "injection").pos_w
        p15 = v.get("prod_15d", "auditor", "injection").pos_w
        print(f"prod  恰30d: w_eff={p30!r}   期望 2^-1 =0.5   -> {p30 == 0.5}")
        print(f"prod  恰60d: w_eff={p60!r}   期望 2^-2 =0.25  -> {p60 == 0.25}")
        print(f"exam  恰15d: w_eff={e15!r}   期望 2^-1 =0.5   -> {e15 == 0.5}")
        print(f"exam  恰30d: w_eff={e30!r}   期望 2^-2 =0.25  -> {e30 == 0.25}")
        print(f"prod  恰15d: w_eff={p15!r}   期望 2^-0.5      -> {p15 == 2.0 ** -0.5}")
        print(f"同龄期 exam(0.5) < prod(2^-0.5) ⇒ exam 确走 15d 档 -> {e15 < p15}")
        assert p30 == 0.5 and p60 == 0.25 and e15 == 0.5 and e30 == 0.25
        assert p15 == 2.0 ** -0.5 and e15 < p15


def ac2_neff() -> None:
    head("ac_muz07uljm4465dwk  n_eff 恒等式（随机账本）+ S1/S2/S3 增量 0.1/1/2 当量")
    # (a) 逐层增量：规范化权重各落一个隔离家族节点
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, T0, agent="x", path=("injection",), tier="S1", weight=0.05)
        deposit(db, T0, agent="x", path=("deser",), tier="S2", weight=0.5)
        deposit(db, T0, agent="x", path=("crypto",), tier="S3", weight=1.0)
        deposit(db, T0, agent="y", path=("authz",), tier="S2", direction=-1, weight=0.5)
        v = view(db, as_of=T0)
        for ag, nd, exp_n, exp_s in [
            ("x", "injection", 0.1, 0.0),   # S1: 2×0.05 当量，不计 s2s3
            ("x", "deser", 1.0, 1.0),       # S2: 1 当量
            ("x", "crypto", 2.0, 2.0),      # S3: 2 当量
            ("y", "authz", 1.0, 1.0),       # 负向 S2 仍计样本
        ]:
            c = v.get(ag, "auditor", nd)
            ok = c.n_eff == exp_n and c.n_eff_s2s3 == exp_s
            print(f"{ag}/{nd}: n_eff={c.n_eff} s2s3={c.n_eff_s2s3}  期望 {exp_n}/{exp_s} -> {ok}")
            assert ok
    # (b) 随机账本 200 笔：恒等式逐位成立 + 与独立 oracle 逐格一致
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        rng = random.Random(20260929)
        as_of_dt = datetime(2030, 6, 30, tzinfo=timezone.utc)
        specs = []
        for _ in range(200):
            age_s = rng.uniform(0.0, 90.0) * 86400.0
            fam = rng.choice(["injection", "deser", "crypto", "authz"])
            path = (fam,) + ((f"t{rng.randrange(3)}",) if rng.random() < 0.5 else ())
            specs.append({
                "ts": (as_of_dt - timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "agent": rng.choice("abcd"), "role": rng.choice(["auditor", "scout"]),
                "path": path, "tier": rng.choice(["S1", "S2", "S3"]),
                "direction": rng.choice([-1, 1]),
                "weight": round(rng.uniform(0.05, 1.5), 3),
                "origin": "exam" if rng.random() < 0.2 else "prod",
            })
        for s in specs:
            deposit(db, **s)
        v = view(db, as_of=AS_OF)
        ident = all(c.n_eff == 2.0 * (c.pos_w + c.neg_w) for c in v.cells.values())
        s_ok = all(c.n_eff_s2s3 <= c.n_eff + 1e-9 for c in v.cells.values())
        print(f"随机账本 {len(specs)} 笔 -> {len(v.cells)} 格："
              f"n_eff ≡ 2×(pos+neg) 逐位成立 -> {ident}；s2s3 ≤ n_eff -> {s_ok}")
        assert ident and s_ok


def ac3_floor() -> None:
    head("ac_muz07uljd1vjjbyx  挥发地板：总量 < ε 的格子 C* 等于父层 C*")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, T0, path=("crypto",), weight=2.0)      # 角色层健康
        deposit(db, T0, path=("injection",), weight=0.3)   # 家族格 0.3 < ε=0.5
        v = view(db, as_of=T0)
        cell = v.get("a", "auditor", "injection")
        print(f"injection 格 total_w={cell.total_w}  < ε={P.decay.EPS_FLOOR}")
        print(f"c_raw(injection) = {v.c_raw('a', 'auditor', 'injection')}  （地板击穿 → None）")
        cs, cp = v.c_star("a", "auditor", "injection"), v.c_star("a", "auditor", None)
        print(f"c_star(injection)={cs!r}  c_star(role 层)={cp!r}  相等 -> {cs == cp}")
        ccs = v.c_star("a", "auditor", "crypto")
        print(f"对照组 crypto（total=2.0≥ε）：c_star={ccs!r} ≠ 父层 -> {ccs != cs}")
        assert cell.total_w < P.decay.EPS_FLOOR
        assert v.c_raw("a", "auditor", "injection") is None and cs == cp and ccs != cs
        # 类型层地板
        deposit(db, T0, path=("authz",), weight=2.0)
        deposit(db, T0, path=("authz", "idor"), weight=0.2)
        v2 = view(db, as_of=T0)
        assert v2.c_star("a", "auditor", "authz/idor") == v2.c_star("a", "auditor", "authz")
        print("类型格 authz/idor（0.2<ε）C* == 家族层 C* -> True")


def ac4_shrink_loo() -> None:
    head("ac_muz07ulja9oma87c  收缩黄金数值（手算）+ LOO 基线剔自身")
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, T0, path=("injection",), tier="S3", weight=1.0)           # d1
        deposit(db, T0, path=("injection", "sqli"), tier="S3", weight=1.0)    # d2
        deposit(db, T0, path=("injection", "sqli"), tier="S2", direction=-1, weight=0.5)  # d3
        deposit(db, T0, path=("crypto",), tier="S1", weight=0.05)             # d4
        v = view(db, as_of=T0)
        # 手算（Fraction）：role 层 total=2.55, n_eff=51/10, C_raw=41/51
        n, lam, c_raw = Fraction(51, 10), Fraction(5), Fraction(41, 51)
        c_agent = (n * c_raw + lam * Fraction(1, 2)) / (n + lam)      # 66/101
        c_role = (n * c_raw + lam * c_agent) / (n + lam)              # 7441/10201
        c_inj = (Fraction(5) * Fraction(4, 5) + lam * c_role) / 10    # 78009/102010
        c_sqli = (Fraction(3) * Fraction(2, 3) + lam * c_inj) / 8     # 118813/163216
        print(f"手算 c_agent=66/101={float(c_agent):.12f}")
        print(f"手算 c_role =7441/10201={float(c_role):.12f}")
        print(f"手算 c_inj  =78009/102010={float(c_inj):.12f}")
        print(f"手算 c_sqli =118813/163216={float(c_sqli):.12f}")
        got = [v.c_star("a", "auditor"), v.c_star("a", "auditor", "injection"),
               v.c_star("a", "auditor", "injection/sqli")]
        for name, g, e in [("agent,role 层", got[0], c_role), ("injection", got[1], c_inj),
                           ("sqli", got[2], c_sqli)]:
            rel = abs(g - float(e)) / float(e)
            print(f"实现 c_star({name})={g!r}  相对误差 {rel:.2e} -> {rel < 1e-12}")
            assert rel < 1e-12
        # 地板对照：crypto 总量 0.05 < ε → C* 直接取父层（== c_role）
        c_crypto = v.c_star("a", "auditor", "crypto")
        rel_c = abs(c_crypto - float(c_role)) / float(c_role)
        print(f"c_star(crypto)={c_crypto!r} == 父层（地板）相对误差 {rel_c:.2e} -> {rel_c < 1e-12}")
        assert rel_c < 1e-12 and v.c_raw("a", "auditor", "crypto") is None
    # LOO
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "l.db"
        deposit(db, T0, agent="a", weight=0.8)
        deposit(db, T0, agent="a", weight=0.4)
        deposit(db, T0, agent="a", direction=-1, weight=0.2)
        deposit(db, T0, agent="b", weight=0.4)
        deposit(db, T0, agent="b", direction=-1, weight=1.2)
        deposit(db, T0, agent="b", path=("injection", "sqli"), weight=0.3)
        deposit(db, T0, agent="s", role="scout", weight=5.0)   # 异角色不入
        v = view(db, as_of=T0)
        ba = v.agg_baseline("auditor", "injection", exclude_agent="a")
        print(f"LOO(a)：pos={ba.pos_w} neg={ba.neg_w} n_eff={ba.n_eff} 笔数={ba.deposits}"
              f"  期望 0.7/1.2/3.8/3 -> "
              f"{abs(ba.pos_w - 0.7) < 1e-12 and abs(ba.neg_w - 1.2) < 1e-12 and ba.deposits == 3}")
        print(f"baseline_mean(a)={v.baseline_mean('auditor', 'injection', exclude_agent='a')!r}"
              f"  期望 {0.7 / 1.9:.12f}")
        assert abs(ba.pos_w - 0.7) < 1e-12 and abs(ba.neg_w - 1.2) < 1e-12
        assert ba.n_eff == 2.0 * (ba.pos_w + ba.neg_w) and ba.deposits == 3
        bb = v.agg_baseline("auditor", "injection", exclude_agent="b")
        assert abs(bb.pos_w - 1.2) < 1e-12 and abs(bb.neg_w - 0.2) < 1e-12
        ball = v.agg_baseline("auditor", "injection", exclude_agent="nobody")
        assert abs(ball.pos_w - 1.9) < 1e-12 and ball.deposits == 6
        assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") is None
        print("LOO(b) 剔 b 后 1.2/0.2；LOO(不存在者)=全体 1.9/1.4；sqli 基线 < ε → mean=None -> True")


if __name__ == "__main__":
    ac1_decay()
    ac2_neff()
    ac3_floor()
    ac4_shrink_loo()
    print("\nALL ACCEPTANCE DEMOS PASSED")
