"""kernel.route_thompson 测试：TS 对照路由（SPEC-4 §3 附加实验臂）。

对应验收：
- ac_muz07ulj0r1nfq6g  TS 采样经注入 RNG 可复现：同种子两次调用决策序列逐位一致、
                       异种子不同、空池不消耗随机数；引擎级 TS 臂同种子重跑全轨迹
                       （事件+路由+度量）digest 逐位一致
- ac_muz07uljujh70qbj  同世界同种子闭环：TS 路由与默认路由均可完整跑通并产出可比
                       regret 曲线——闭环直接复用 simulator.engine 主循环本体
                       （§0「仿真复用内核实现」的同一架构位，不另写第二份 harness），
                       regret 用官方 ``simulator.regret_curve`` 口径（同一条任务流、
                       同长度、逐点 gap≥0 且有限、累计单调不减、终点=metrics 互证）；
                       **默认臂双通道必须齐触发**（specialist 通道有规模地出现，
                       防「对照退化为纯探索 vs TS」的假对照——无加速孵化的世界里
                       无人转正时 TILT 机制全程不参与，对照即失真）
- ac_muz07uljcftzax0l  默认 route() 行为不受影响——既有 tests/test_kernel_route.py
                       黄金用例全绿背书；route.py 零改动（TS 臂经 engine.router
                       注入点换臂，默认臂代码路径不变）

后验构造的黄金口径：α = pos_w + λ/4、β = neg_w + λ/4 ⇒ 后验均值 ≡ §5.1 单层收缩
``(n_eff·C_raw + λ·0.5)/(n_eff + λ)``（父先验 ROOT_PRIOR=0.5），分数精确对照：

    mean = (pos + λ/4) / (pos + neg + λ/2)
         = (2·pos + λ/2) / (2·(pos+neg) + λ)           # 分子分母同乘 2
         = (n_eff·C_raw + λ·ROOT_PRIOR) / (n_eff + λ)  # n_eff = 2(pos+neg)

闭环场景标定（与 tests/test_sim_engine.py 同款口径，机制动力学的真实时标）：
孵化门（Wilson 优势 + 暗考）决定 active 专员约在第 10+ 日出现，tilt 专员通道必须
有 active 格才会触发；故闭环取 18 日 × λ=40 + 加速孵化覆盖（promote.N/N_S2S3/
DELTA/M 收紧）保证默认臂 specialist 通道在闭环内有规模地真实触发。两臂同 seed
同 stream 同 overrides，唯一变量 = ``router``（§3 附加单变量实验「同参数，路由换
TS，比 regret」；回归 regret 全程走官方 simulator.regret_curve，两臂同一口径）。
"""

import math
import random
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction

import pytest

from qswarm.contracts import DispatchPayload, load_params
from qswarm.kernel import (
    FixedClock,
    KernelLedger,
    agg_cells,
    route_thompson,
)
from qswarm.kernel.route_thompson import TS_CHANNEL, beta_posterior, posterior_mean
from qswarm.simulator import TaskStreamParams, regret_curve, run

T0 = "2030-01-01T00:00:00Z"
PARAMS = load_params()                    # λ=5 ⇒ 先验伪计数 α0=β0=1.25


# ── 工具：落账 / 激活 / 视图 / 任务桩（与 test_kernel_route.py 同款）──

def _dep(
    db,
    agent: str,
    *,
    role: str = "auditor",
    path: tuple[str, ...] = ("injection", "sqli"),
    direction: int = 1,
    weight: float = 1.0,
    tier: str = "S3",
) -> None:
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        led.append(
            "deposit",
            {
                "finding": None, "cluster": None, "agent": agent, "role": role,
                "taxonomy_path": list(path), "signal_tier": tier,
                "direction": direction, "weight": weight, "origin": "prod",
            },
        )


def _activate(db, agent: str, node: str = "injection/sqli", role: str = "auditor") -> None:
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        led.append(
            "promotion",
            {
                "agent": agent, "role": role, "node": node, "exam_id": "ER-TS",
                "from_status": "shadow", "to_status": "active",
            },
        )


def _view(db):
    with KernelLedger(db) as led:
        return agg_cells(led, T0)


@dataclass(frozen=True)
class TsTask:
    """§5.2 伪码的 task：已带 taxonomy（family[/type]）与 role。"""

    task_id: str
    role: str
    family: str
    type: str | None = None


# ── Beta 后验黄金口径：后验均值 ≡ §5.1 单层收缩 ─────────────────

def test_beta_posterior_mean_matches_single_level_shrink():
    """分数精确对照：mean(α,β) = (n_eff·C_raw + λ·0.5)/(n_eff+λ)，λ=5；纯先验=0.5。"""
    lam = 5
    cases = [
        (Fraction(3), Fraction(1)),        # 3 正 1 负
        (Fraction(6), Fraction(0)),        # 6 正
        (Fraction(0), Fraction(4)),        # 4 负
        (Fraction(1, 2), Fraction(1, 4)),  # 挥发后小数权重
    ]
    for pos_f, neg_f in cases:
        pos, neg = float(pos_f), float(neg_f)
        alpha, beta = beta_posterior(pos, neg, lam=lam)
        assert alpha == pytest.approx(pos + lam / 4)
        assert beta == pytest.approx(neg + lam / 4)
        n_eff = 2 * (pos_f + neg_f)
        c_raw = pos_f / (pos_f + neg_f)
        shrink = (n_eff * c_raw + lam * Fraction(1, 2)) / (n_eff + lam)
        assert posterior_mean(alpha, beta) == pytest.approx(float(shrink), rel=1e-12)

    # 无存款（冷启动）：纯先验 Beta(λ/4, λ/4)，均值 = ROOT_PRIOR = 0.5
    alpha, beta = beta_posterior(0.0, 0.0, lam=lam)
    assert posterior_mean(alpha, beta) == pytest.approx(0.5)


# ── 冷启动对称探索（TS 的探索本性）─────────────────────────────

def test_cold_start_explores_symmetrically(tmp_path):
    """空账本 + 显式池：两候选均无格子（纯先验同分布）⇒ 中签率 ≈ 50/50、字段为 TS 词表。"""
    v = _view(tmp_path / "led.db")

    n_trials = 4_000
    n_a = 0
    rng = random.Random(20260929)
    for i in range(n_trials):
        dec = route_thompson(TsTask(f"T-{i}", "auditor", "injection", "sqli"), v, rng,
                             PARAMS, pool=["b", "a"])   # 乱序传入：内部排序后抽样
        assert dec.channel == TS_CHANNEL and dec.reason == "ts_sample"
        assert dec.specs == ("a", "b")                       # 候选字典序
        assert dec.tilt is None and dec.raw_tilt is None and dec.uniform_pick is None
        assert dec.node == "injection/sqli" and dec.family == "injection"
        assert dec.spec_c_stars == pytest.approx((0.5, 0.5))  # 纯先验均值
        n_a += dec.assignee == "a"
    assert abs(n_a / n_trials - 0.5) <= 0.03                  # 对称探索（≈3.8σ 内）


# ── 证据主导采样 + 显式池 / 过载剔除 / 载荷形状 ─────────────────

def test_evidence_dominates_and_pool_semantics(tmp_path):
    """hot(6正) vs cold(6负) vs newbie(无格子)：hot 中签率 ≥0.9、newbie 仍偶被探索。"""
    db = tmp_path / "led.db"
    for _ in range(8):
        _dep(db, "hot", direction=1)
        _dep(db, "cold", direction=-1)
    v = _view(db)
    pool = ["newbie", "cold", "hot", "hot"]                   # 重复项去重、字典序

    n_trials = 4_000
    picks = {"hot": 0, "cold": 0, "newbie": 0}
    rng = random.Random(20260929)
    for i in range(n_trials):
        dec = route_thompson(TsTask(f"T-{i}", "auditor", "injection", "sqli"), v, rng,
                             PARAMS, pool=pool)
        assert dec.specs == ("cold", "hot", "newbie")         # 去重 + 排序
        assert dec.spec_c_stars == pytest.approx(
            (1.25 / 10.5, 9.25 / 10.5, 0.5)                   # 后验均值点估计位
        )
        picks[dec.assignee] += 1
    # hot 后验 Beta(9.25, 1.25) 均值 0.881；newbie 宽先验 Beta(1.25,1.25) 天然分走
    # ~一成探索份量（TS 本性）——阈值取 0.85，语义由排序断言承载
    assert picks["hot"] / n_trials >= 0.85                    # 证据主导
    assert picks["hot"] > picks["newbie"] > 0                 # 无证据者仍被 TS 探索
    assert picks["hot"] > picks["cold"]

    # 过载剔除：max_load=2 剔 hot(load=5)；newbie 零载在途（缺省 0）保留
    task = TsTask("T-D", "auditor", "injection", "sqli")
    dec = route_thompson(task, v, random.Random(0), PARAMS, pool=pool,
                         load={"hot": 5, "cold": 0}, max_load=2)
    assert dec.specs == ("cold", "newbie") and "hot" not in dec.specs
    assert dec.assignee in ("cold", "newbie")
    payload = dec.to_dispatch_payload(hop_budget=1)
    assert payload["assignee"] == dec.assignee and payload["node"] == "injection/sqli"
    DispatchPayload.model_validate(payload)

    # 全员过载：空池决策、不消耗随机数（rng 状态逐位未动）
    rng = random.Random(1)
    state = rng.getstate()
    dec = route_thompson(task, v, rng, PARAMS, pool=pool,
                         load={"hot": 9, "cold": 9, "newbie": 9}, max_load=9)
    assert dec.assignee is None and dec.reason == "empty_pool" and dec.channel == TS_CHANNEL
    assert dec.specs == () and dec.spec_c_stars == () and dec.tilt is None
    assert rng.getstate() == state

    # 缺省池派生（无 pool 参数）：同 role 且格子落在该家族的 agent = {cold, hot}
    dec = route_thompson(task, v, random.Random(0), PARAMS)
    assert dec.specs == ("cold", "hot")
    # 家族级任务（无类型）：node = family
    dec = route_thompson(TsTask("T-F", "auditor", "injection"), v, random.Random(0), PARAMS)
    assert dec.node == "injection" and dec.family == "injection"


# ── 同种子逐位可复现（验收 ac_muz07ulj0r1nfq6g）────────────────

def _ts_fixture(tmp_path):
    """3 agent × 2 家族的混合证据 + 激活：专员/探索样本空间齐全。"""
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "a1", direction=1)
    for _ in range(2):
        _dep(db, "a2", direction=1)
    _dep(db, "a2", direction=-1)
    for _ in range(3):
        _dep(db, "b1", path=("crypto", "tls"), direction=1)
    _dep(db, "b1", path=("crypto", "tls"), direction=-1, weight=0.5)
    _activate(db, "a1")
    _activate(db, "a2")
    return _view(db)


def test_same_seed_route_thompson_bitwise_reproducible(tmp_path):
    """同种子两次整段路由逐位一致；异种子不同；单次调用亦逐位一致。"""
    v = _ts_fixture(tmp_path)

    def run_seq(seed: int, n: int = 150) -> list[tuple]:
        rng = random.Random(seed)
        out = []
        for i in range(n):
            # 交替类型级/家族级任务：跨 (role, node) 后验、样本空间都覆盖
            t = (TsTask(f"T-{i:04d}", "auditor", "injection", "sqli")
                 if i % 3 else TsTask(f"T-{i:04d}", "auditor", "crypto", "tls"))
            d = route_thompson(t, v, rng, PARAMS)
            out.append(
                (d.task_id, d.node, d.assignee, d.channel, d.reason,
                 d.tilt, d.raw_tilt, d.specs, d.spec_c_stars, d.uniform_pick)
            )
        return out

    seq1 = run_seq(20260929)
    seq2 = run_seq(20260929)
    assert seq1 == seq2                                       # 逐位一致（含 float）
    assert seq1 != run_seq(77)                                # 异种子必不同
    assert all(row[3] == TS_CHANNEL and row[4] == "ts_sample" for row in seq1)
    assert len({row[2] for row in seq1}) >= 3                 # 全体候选都被探索过

    # 单次调用可复现：两个独立同种子 rng 各调一次，决策逐位一致
    d1 = route_thompson(TsTask("T-X", "auditor", "injection", "sqli"),
                        v, random.Random(42), PARAMS)
    d2 = route_thompson(TsTask("T-X", "auditor", "injection", "sqli"),
                        v, random.Random(42), PARAMS)
    assert d1 == d2


# ── 同世界同种子闭环：两臂完整跑通 + 可比 regret 曲线 ───────────
# （验收 ac_muz07uljujh70qbj / ac_muz07ulj0r1nfq6g 引擎级；复用 simulator.engine
#   主循环本体——§0「仿真复用内核实现」，不另写第二份 harness）

SEED = 11
STREAM = TaskStreamParams(lam=40.0)     # §3 主矩阵量级（λ=50/日）的校准近似
OVERRIDES = {
    # 加速孵化（test_sim_engine.OVERRIDES_MAIN 同款标定）：active 专员早于第 10 日
    # 出现 ⇒ 默认臂 specialist 通道在闭环内真实成规模触发（TILT 机制参与对照）
    "tilt.GAMMA": 0,
    "promote.N": 3,
    "promote.N_S2S3": 0,
    "promote.DELTA": 0.01,
    "promote.M": 1,
    "demote.DORMANT_DAYS": 1,
    # §7.3/§8.2 字面机制标定：M0 定稿旋钮在此关闭（同 test_sim_engine.OVERRIDES_MAIN
    # 注；定稿行为由 tests/test_m0_final_knobs.py 与官方全矩阵覆盖）
    "promote.M_PRORATE": False,
    "promote.UNIQUE_PER_FAMILY": False,
    "promote.WINDOW_ADVANTAGE": 0,
    "exam.ENTRY_DIRECT": 0.0,
}
KW = dict(seed=SEED, n_days=18, stream=STREAM, overrides=OVERRIDES, replay_every=6)


@pytest.fixture(scope="module")
def arms():
    """三个引擎级臂：default / thompson / thompson 同种子重跑（模块级只跑一次）。

    两臂同 (seed, n_days, stream, overrides)，唯一变量 = ``router``（§3 单变量
    实验）；缺省 router=None 即 kernel.route（默认臂代码路径原样）。
    """
    return {
        "default": run(**KW),
        "thompson": run(router=route_thompson, **KW),
        "thompson-rerun": run(router=route_thompson, **KW),
    }


def test_regret_curves_comparable_same_world_same_seed(arms):
    """同世界同种子：两臂完整跑通、官方口径 regret 曲线可比、默认臂双通道齐触发。"""
    d, t = arms["default"], arms["thompson"]

    # 完整跑通：同一条任务流各派单恰一次；dispatch 事件齐备；环内+收尾重放等价
    assert d.router_name == "kernel.route" and t.router_name == "route_thompson"
    assert [x.task_id for x in d.tasks] == [x.task_id for x in t.tasks]
    assert len(d.routing) == len(t.routing) == len(d.tasks) > 0
    assert d.event_kinds()["dispatch"] == len(d.tasks)
    assert t.event_kinds()["dispatch"] == len(t.tasks)
    assert d.replay_report.passed and t.replay_report.passed
    assert d.metrics.replay_equivalent is True and t.metrics.replay_equivalent is True
    assert all(rep.passed for rep in d.replay_day_reports + t.replay_day_reports)

    # 默认臂双通道齐触发（本闭环的关键标定）：specialist 有规模（非孤例）、
    # explore 在场；TS 臂全量走 thompson 通道且派单非空、探索覆盖 ≥2 agent
    ch_d = Counter(r.channel for r in d.routing)
    assert set(ch_d) == {"specialist", "explore"}
    assert ch_d["specialist"] >= 10, ch_d
    assert {r.channel for r in t.routing} == {TS_CHANNEL}
    assert all(r.assignee is not None for r in t.routing)
    assert len({r.assignee for r in t.routing}) >= 2
    assert d.status_counts.get("active", 0) > 0     # active 专员确实长出来了

    # 可比 regret 曲线：官方 simulator.regret_curve 口径（两臂同一任务流/真值/定义）
    curve_d = regret_curve(d.tasks, d.truth, d.routing)
    curve_t = regret_curve(t.tasks, t.truth, t.routing)
    assert len(curve_d) == len(curve_t) == len(d.tasks)
    for name, curve in (("default", curve_d), ("thompson", curve_t)):
        assert all(math.isfinite(p.gap) and p.gap >= 0.0 for p in curve), name
        cums = [p.cum_regret for p in curve]
        assert all(b >= a - 1e-12 for a, b in zip(cums, cums[1:])), name
        assert cums[-1] > 0.0, (name, cums[-1])       # 冷启动必有遗憾

    # 引擎度量与曲线终点互证（两臂通用口径的一致性）
    assert d.metrics.regret_total == pytest.approx(curve_d[-1].cum_regret)
    assert t.metrics.regret_total == pytest.approx(curve_t[-1].cum_regret)
    assert d.metrics.regret_per_task == pytest.approx(curve_d[-1].mean_regret)
    assert t.metrics.regret_per_task == pytest.approx(curve_t[-1].mean_regret)

    # 真对照：同世界同种子、仅换路由器 ⇒ 决策流与 regret 曲线都确实不同
    assert [r.assignee for r in d.routing] != [r.assignee for r in t.routing]
    assert [p.gap for p in curve_d] != [p.gap for p in curve_t]


def test_engine_same_seed_ts_rerun_bitwise(arms):
    """TS 臂同种子重跑：全轨迹（事件+路由+度量）digest 逐位一致；异臂轨迹必不同。"""
    t1, t2 = arms["thompson"], arms["thompson-rerun"]
    assert t1.digest == t2.digest
    assert arms["default"].digest != t1.digest
