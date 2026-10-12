"""world+taskgen 验收测试（SPEC-4 §1.1/§1.2）。

对照子任务验收标准：
  - ac_muz07uljid44v2xx：同种子两次生成 agent 池与任务流逐位一致；不同种子不同；
    并以两个独立解释器（不同 PYTHONHASHSEED）跨进程逐位比对字节级输出加固；
  - ac_muz07uljnna36xqq：隐藏设定 10%/10%/5% 统计分布（单种子定值 + 5 种子聚合
    收敛双断言）；退化型 T_drift 前后 q 差 0.25；
  - ac_muz07ulj33wgm80z：家族混合按 v1_weight 归一化（mem_safety 0.3）；模式 50/35/15。

统计断言容差均取 ≥2.8σ（固定种子下数值确定，无随机抖动风险）。
"""

import math
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from qswarm.contracts import Role, RunMode, load_params, load_taxonomy
from qswarm.simulator import (
    AgentPool,
    Archetype,
    TaskGenerator,
    TaskMix,
    TaskStreamParams,
    WorldHiddenParams,
    WorldTask,
    derive_seed,
)

TAX = load_taxonomy()
PARAMS = load_params()
FAMILIES = list(TAX.families)
TYPE_PATHS = [f"{f}/{ty}" for f in FAMILIES for ty in TAX.families[f].types]

# 放大编制做统计断言（1800 只；比例断言的样本量载体）
ROSTER_BIG = {"scout": 600, "auditor": 500, "reviewer": 400, "verifier": 300}


# ── ac_muz07uljid44v2xx：确定性 ─────────────────────────────────

def test_same_seed_pool_bit_identical():
    p1 = AgentPool.from_params(PARAMS, TAX, seed=42)
    p2 = AgentPool.from_params(PARAMS, TAX, seed=42)
    assert p1.agents == p2.agents
    # repr 往返对 float 精确保真：逐位一致的最强字符串证据
    assert [repr(a) for a in p1.agents] == [repr(a) for a in p2.agents]
    # 能力视图（含退化时钟前后）也逐位一致
    for a in p1.agents:
        for f in FAMILIES:
            for t in (0, 499, 500):
                assert p1.capability(a.agent_id, f, t=t) == p2.capability(a.agent_id, f, t=t)


def test_same_seed_tasks_bit_identical():
    t1 = TaskGenerator(PARAMS, TAX, seed=7).generate(30)
    t2 = TaskGenerator(PARAMS, TAX, seed=7).generate(30)
    assert t1 == t2
    assert [x.to_record() for x in t1] == [x.to_record() for x in t2]


def test_different_seed_pool_differs():
    p1 = AgentPool.from_params(PARAMS, TAX, seed=42)
    p2 = AgentPool.from_params(PARAMS, TAX, seed=43)
    assert [repr(a) for a in p1.agents] != [repr(a) for a in p2.agents]
    q1 = {a.agent_id: dict(a.family_q) for a in p1.agents}
    q2 = {a.agent_id: dict(a.family_q) for a in p2.agents}
    assert q1 != q2


def test_different_seed_tasks_differs():
    t1 = TaskGenerator(PARAMS, TAX, seed=7).generate(30)
    t2 = TaskGenerator(PARAMS, TAX, seed=8).generate(30)
    assert [x.to_record() for x in t1] != [x.to_record() for x in t2]


def test_substreams_independent_and_named():
    """子流独立：生成 agent 池不消耗任务子流；同名子流跨生成器同值；异名不同种子。"""
    seed = 123
    alone = TaskGenerator(PARAMS, TAX, seed=seed).generate(20)
    _ = AgentPool.from_params(PARAMS, TAX, seed=seed)          # 先滚一遍池
    after_pool = TaskGenerator(PARAMS, TAX, seed=seed).generate(20)
    assert [x.to_record() for x in alone] == [x.to_record() for x in after_pool]
    assert derive_seed(seed, "task", "family") != derive_seed(seed, "task", "mode")
    assert derive_seed(seed, "task") != derive_seed(seed + 1, "task")


# ── ac_muz07uljid44v2xx（加固）：跨解释器逐位一致 ───────────────

# 子进程载荷：seed 由 argv 注入；输出 = agent 池全量 repr（含全部 float 位型）
# + 能力视图（t=0/499/500 横跨 T_drift=500 前后）+ 60 日任务流平铺记录。
# 两次运行的字节流逐位相等 ⇔ 池与任务流跨进程逐位一致。
_CROSS_PROC_SCRIPT = """\
import io, sys
from qswarm.contracts import load_params, load_taxonomy
from qswarm.simulator import AgentPool, TaskGenerator

seed = int(sys.argv[1])
params, tax = load_params(), load_taxonomy()
pool = AgentPool.from_params(params, tax, seed=seed)
out = io.StringIO()
for a in pool.agents:
    out.write(repr(a) + "\\n")
for a in pool.agents:
    for f in tax.families:
        for t in (0, 499, 500):
            out.write(f"{a.agent_id} {f} {t} {pool.capability(a.agent_id, f, t=t)!r}\\n")
for x in TaskGenerator(params, tax, seed=seed).generate(60):
    out.write(repr(x.to_record()) + "\\n")
sys.stdout.write(out.getvalue())
"""


def _run_cross_proc(seed: int, pythonhashseed: str) -> str:
    repo_root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONHASHSEED": pythonhashseed, "PYTHONPATH": str(repo_root)}
    r = subprocess.run(
        [sys.executable, "-c", _CROSS_PROC_SCRIPT, str(seed)],
        cwd=repo_root, env=env, capture_output=True, text=True, check=True,
    )
    assert r.stderr == "", r.stderr
    return r.stdout


def test_cross_process_bit_identical_and_seed_sensitive():
    """两个独立解释器（PYTHONHASHSEED=0 vs random）同种子：输出字节流逐位相等。"""
    a = _run_cross_proc(42, "0")
    b = _run_cross_proc(42, "random")
    assert len(a) > 20_000                       # 18 池 + 3024 能力行 + ~3000 任务行
    assert a == b                                # 跨进程逐位一致
    c = _run_cross_proc(43, "0")
    assert a != c                                # 不同种子结果不同


# ── 编制与隐藏设定的机制不可见性 ────────────────────────────────

def test_roster_and_ids():
    pool = AgentPool.from_params(PARAMS, TAX, seed=42)
    assert len(pool) == 6 + 5 + 4 + 3 == 18
    assert {r: len(pool.by_role(r)) for r in Role} == {
        Role.SCOUT: 6, Role.AUDITOR: 5, Role.REVIEWER: 4,
        Role.VERIFIER: 3, Role.QUEEN_ADVISOR: 0,
    }
    ids = [a.agent_id for a in pool]
    assert len(set(ids)) == len(ids)
    assert ids[0] == "scout-01" and ids[6] == "auditor-01"


def test_mechanism_cannot_see_ground_truth():
    """机制面只有 (id, role)；任务对象不携带 q / archetype（§1.1 纪律）。"""
    pool = AgentPool.from_params(PARAMS, TAX, seed=42)
    roster = pool.mechanism_roster()
    assert roster == [(a.agent_id, a.role) for a in pool.agents]
    assert set(WorldTask.__dataclass_fields__) == {
        "task_id", "t", "day", "family", "type", "difficulty", "mode", "cache_hit",
    }


# ── ac_muz07uljnna36xqq：隐藏设定分布 + 退化漂移 ───────────────

def test_hidden_setting_proportions():
    pool = AgentPool.generate(ROSTER_BIG, TAX, seed=1)
    n = len(pool)
    counts = Counter(a.ground.archetype for a in pool.agents)
    p = {k: counts[k] / n for k in counts}
    assert set(counts) == set(Archetype)                      # 四类互斥完备
    assert math.isclose(sum(p.values()), 1.0)
    assert abs(p[Archetype.EXPERT] - 0.10) <= 0.02            # σ=0.0071，2.8σ
    assert abs(p[Archetype.LUCKY] - 0.10) <= 0.02
    assert abs(p[Archetype.DEGRADED] - 0.05) <= 0.015         # σ=0.0051，2.9σ
    assert abs(p[Archetype.NORMAL] - 0.75) <= 0.025
    # 真专家家族能力确实高（Beta(8,2) 均值 0.8），运气型能力分布与常人无异
    exp_q = [a.family_q[a.ground.expert_family] for a in pool.agents
             if a.ground.archetype is Archetype.EXPERT]
    assert len(exp_q) == counts[Archetype.EXPERT]
    assert sum(exp_q) / len(exp_q) > 0.72
    lucky_q = [q for a in pool.agents if a.ground.archetype is Archetype.LUCKY
               for q in a.family_q.values()]
    assert 0.40 < sum(lucky_q) / len(lucky_q) < 0.60          # Beta(2,2) 均值 0.5
    # 运气型带早期正向偏差标记，其余为零（§1.3 verdict 生成器消费）
    for a in pool.agents:
        bias_expected = pool.hidden_params.luck_bias if a.ground.archetype is Archetype.LUCKY else 0.0
        assert a.ground.early_positive_bias == bias_expected


def test_hidden_setting_proportions_multiseed():
    """5 种子 × 1800 只聚合 9000 样本：10/10/5 比例收敛到设定值（非单次抽样侥幸）。

    n=9000 时 σ(10%)≈0.0032、σ(5%)≈0.0023、σ(75%)≈0.0046，容差 ≈3.3–3.8σ。
    """
    counts: Counter = Counter()
    n = 0
    for seed in range(1, 6):
        pool = AgentPool.generate(ROSTER_BIG, TAX, seed=seed)
        counts.update(a.ground.archetype for a in pool.agents)
        n += len(pool)
    assert n == 5 * sum(ROSTER_BIG.values()) == 9000
    p = {k: counts[k] / n for k in Archetype}
    assert abs(p[Archetype.EXPERT] - 0.10) <= 0.012
    assert abs(p[Archetype.LUCKY] - 0.10) <= 0.012
    assert abs(p[Archetype.DEGRADED] - 0.05) <= 0.008
    assert abs(p[Archetype.NORMAL] - 0.75) <= 0.015


def test_family_q_beta22_moments_on_normal():
    """NORMAL 型家族能力 q_f ~ Beta(2,2)：均值 0.5、方差 0.05（矩断言）。

    NORMAL ≈ 1350 只 × 6 家族 ≈ 8100 样本，σ_mean≈0.0025（容差 4σ）。
    """
    pool = AgentPool.generate(ROSTER_BIG, TAX, seed=3)
    qs = [q for a in pool.agents if a.ground.archetype is Archetype.NORMAL
          for q in a.family_q.values()]
    assert len(qs) > 7000
    assert all(0.0 < q < 1.0 for q in qs)
    mean = sum(qs) / len(qs)
    var = sum((q - mean) ** 2 for q in qs) / len(qs)
    assert abs(mean - 0.5) <= 0.01                            # Beta(2,2) 均值
    assert abs(var - 0.05) <= 0.005                           # Beta(2,2) 方差


def test_degraded_drift_exact_quarter():
    """退化型：T_drift 前后家族级 q 差恰为 0.25（全部家族、全部退化 agent）。"""
    pool = AgentPool.generate(ROSTER_BIG, TAX, seed=1)
    td = pool.hidden_params.t_drift
    degraded = [a for a in pool.agents if a.ground.archetype is Archetype.DEGRADED]
    assert degraded, "1800 只 ×5% 应有 ~90 只退化型"
    for a in degraded:
        assert a.ground.t_drift == td
        for f in FAMILIES:
            before = pool.capability(a.agent_id, f, t=td - 1)
            after = pool.capability(a.agent_id, f, t=td)
            assert before - after == pytest.approx(0.25, abs=1e-12)
    # 非退化型能力与 t 无关
    for a in pool.agents[:50]:
        if a.ground.archetype is Archetype.DEGRADED:
            continue
        for f in FAMILIES:
            assert pool.capability(a.agent_id, f, t=0) == pool.capability(a.agent_id, f, t=10**6)


def test_degraded_t_drift_configurable():
    pool = AgentPool.generate(ROSTER_BIG, TAX, seed=1,
                              hidden=WorldHiddenParams(t_drift=100))
    for a in pool.agents:
        if a.ground.archetype is Archetype.DEGRADED:
            assert a.ground.t_drift == 100
            for f in FAMILIES:
                assert pool.capability(a.agent_id, f, t=99) != pool.capability(a.agent_id, f, t=100)
                diff = pool.capability(a.agent_id, f, t=99) - pool.capability(a.agent_id, f, t=100)
                assert diff == pytest.approx(0.25, abs=1e-12)
        else:
            for f in FAMILIES:
                assert pool.capability(a.agent_id, f, t=99) == pool.capability(a.agent_id, f, t=100)


def test_type_capability_clip_formula():
    """q_t = clip(q_f + ε_t, 0.05, 0.95)：逐 agent 逐类型按存档 ε 复算核验。"""
    pool = AgentPool.generate(ROSTER_BIG, TAX, seed=2)
    for a in pool.agents:
        for tp in TYPE_PATHS:
            f, ty = tp.split("/")
            q_f, eps = a.family_q[f], a.ground.type_eps[tp]
            expected = min(0.95, max(0.05, q_f + eps))
            assert a.capability(f, ty, t=0, hidden=pool.hidden_params) == expected
            assert 0.05 <= expected <= 0.95


# ── ac_muz07ulj33wgm80z：家族混合 + 模式比例 ───────────────────

def test_family_weights_normalized_from_v1_weight():
    gen = TaskGenerator(PARAMS, TAX, seed=0)
    w = gen.family_weights
    assert math.isclose(sum(w.values()), 1.0, rel_tol=0, abs_tol=1e-12)
    total = 5 * 1.0 + 0.3                                     # 5 家族 ×1.0 + mem_safety 0.3
    for f in FAMILIES:
        expected = TAX.families[f].v1_weight / total
        assert w[f] == pytest.approx(expected, rel=1e-12)
    assert w["mem_safety"] == pytest.approx(0.3 / 5.3, rel=1e-12)
    assert w["injection"] == pytest.approx(1.0 / 5.3, rel=1e-12)


def test_family_mix_empirical():
    n_days = 1000                                              # ~5 万任务，σ_family≈0.0018
    tasks = TaskGenerator(PARAMS, TAX, seed=11).generate(n_days)
    gen = TaskGenerator(PARAMS, TAX, seed=11)
    total = 5.3
    counts = Counter(t.family for t in tasks)
    for f in FAMILIES:
        obs = counts[f] / len(tasks)
        exp = TAX.families[f].v1_weight / total
        assert abs(obs - exp) <= 0.008, f"{f}: obs={obs:.4f} exp={exp:.4f}"
    # 每个任务的类型确属该家族（家族混合未串线）
    for t in tasks:
        assert t.type in TAX.families[t.family].types


def test_mode_ratio_50_35_15():
    tasks = TaskGenerator(PARAMS, TAX, seed=12).generate(1000)
    counts = Counter(str(t.mode) for t in tasks)
    n = len(tasks)
    assert abs(counts["fast"] / n - 0.50) <= 0.006            # σ≈0.0016，≥3.7σ
    assert abs(counts["pr"] / n - 0.35) <= 0.006
    assert abs(counts["full"] / n - 0.15) <= 0.006
    # 混合可配：全 fast 合法
    tasks2 = TaskGenerator(PARAMS, TAX, seed=12,
                           stream=TaskStreamParams(lam=50.0, mix=TaskMix(fast=1.0, pr=0.0, full=0.0))).generate(5)
    assert all(t.mode is RunMode.FAST for t in tasks2)


def test_taskmix_validation():
    with pytest.raises(ValueError):
        TaskMix(fast=0.5, pr=0.35, full=0.20)                  # 和 ≠ 1
    with pytest.raises(ValueError):
        TaskMix(fast=-0.1, pr=0.6, full=0.5)                   # 负权重


# ── §1.2 其余机制：到达 / 难度 / 重扫 ──────────────────────────

def test_arrival_poisson_rate():
    n_days = 1000
    tasks = TaskGenerator(PARAMS, TAX, seed=13).generate(n_days)
    per_day = Counter(t.day for t in tasks)
    assert len(per_day) <= n_days
    mean = sum(per_day.values()) / n_days
    assert abs(mean - 50.0) <= 1.0                            # σ_mean≈0.22，4.5σ
    var = sum((per_day[d] - mean) ** 2 for d in per_day) / n_days
    assert abs(var - 50.0) <= 10.0                            # 泊松方差=λ（卡方 ~4%截尾无碍）
    lam0 = TaskGenerator(PARAMS, TAX, seed=13, stream=TaskStreamParams(lam=0.0)).generate(50)
    assert lam0 == []


def test_difficulty_beta_23():
    tasks = TaskGenerator(PARAMS, TAX, seed=14).generate(1000)
    ds = [t.difficulty for t in tasks]
    assert all(0.0 < d < 1.0 for d in ds)
    mean = sum(ds) / len(ds)                                  # Beta(2,3) 均值 0.4，σ_mean≈0.0006
    var = sum((d - mean) ** 2 for d in ds) / len(ds)
    assert abs(mean - 0.4) <= 0.003
    assert abs(var - 0.04) <= 0.002                           # Beta(2,3) 方差 0.04


def test_rescan_cache_hit_semantics():
    tasks = TaskGenerator(PARAMS, TAX, seed=15).generate(1000)
    n = len(tasks)
    hits = [t for t in tasks if t.cache_hit]
    assert abs(len(hits) / n - PARAMS.budget.RESCAN_WEEKLY) <= 0.004   # σ≈0.0008，5σ
    for t in hits:
        assert t.produces_finding is False                    # 无新 finding，只测路由
    for t in tasks:
        if not t.cache_hit:
            assert t.produces_finding is True
    # 任务序严格递增、id 与 t 对齐、day 单调不降
    assert [t.t for t in tasks] == list(range(len(tasks)))
    assert all(t.task_id == f"T-{t.t:06d}" for t in tasks)
    days = [t.day for t in tasks]
    assert days == sorted(days)
