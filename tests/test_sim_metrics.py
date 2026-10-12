"""simulator.metrics 单测：SPEC-4 §2 九项度量的构造小场景断言。

对应验收（taskflow 子任务「实现 simulator.metrics」）：
- ac_muz07uljxkv110uu  每项度量有构造小场景单测（已知晋升时刻/已知熄灭情形，
                       断言中位数与比例正确）：①②③④⑤⑧ 各一节；
- ac_muz07uljcn4w28le  oracle 路由按隐藏 q 对同一任务流输出对错基线，regret
                       曲线可计算（测试断言逐点 ≥ 0、累计单调、oracle 零差距）；
- ac_muz07uljo0eb4y4g 聚合接口对多种子输入产出均值±std；隐藏真值只读访问
                       （WorldTruth 快照 frozen + MappingProxyType，世界对象不可变）。

场景构造：直接用 KernelLedger + 真实 payload schema 落账本事件（时钟注入，
ts 严格随 seq 递增），WorldTruth 用手工设定的小池（q 已知，期望值可手算）。
"""

from __future__ import annotations

import statistics
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import MappingProxyType

import pytest

from qswarm.contracts import (
    DepositPayload,
    DispatchPayload,
    LedgerKind,
    Role,
    RunMode,
    SignalTier,
    load_taxonomy,
)
from qswarm.kernel import CellKey, KernelLedger, agg_cells
from qswarm.simulator import AgentPool, TaskGenerator, run
from qswarm.simulator.metrics import (
    SIM_T0,
    RoutingRecord,
    RunMetrics,
    TaskTimeline,
    WorldHiddenParams,
    WorldTruth,
    aggregate_run_metrics,
    aggregate_seeds,
    cell_episodes,
    cold_start_length,
    cold_start_seeded_vs_unseeded,
    collect_run_metrics,
    degradation_response,
    expected_accuracy_series,
    expected_correctness,
    mean_std,
    median_time_to_active,
    oracle_route_stream,
    promotion_precision,
    promotion_recall,
    regret_curve,
    replay_equivalence,
    routing_records_from_ledger,
    snapshot_world_truth,
    s2_starvation_propagation,
    uniform_accuracy_series,
    wrong_extinguish_rate,
)
from qswarm.simulator.taskgen import WorldTask
from qswarm.simulator.world import Archetype

T0 = SIM_T0

# ── 构造基建：时钟注入的账本 + 手工真值池 ────────────────────────


class _Clock:
    """测试注入时钟：逐条改写当前时刻（内核无时间源，ts 全由时钟签发）。"""

    def __init__(self) -> None:
        self.dt = T0

    def now(self) -> datetime:
        return self.dt


class _EventLog:
    """按「仿真日」落事件的账本句柄：ts = T0 + day 天 + 落账序号秒（严格递增）。"""

    def __init__(self, tmp_path: Path, name: str = "metrics.db") -> None:
        self.clock = _Clock()
        self.ledger = KernelLedger(tmp_path / name, clock=self.clock)
        self._i = 0

    def add(self, day: float, kind: LedgerKind | str, payload: dict):
        self._i += 1
        self.clock.dt = T0 + timedelta(days=day, seconds=self._i)
        return self.ledger.append(kind, payload)


def _dep(agent: str, role: str, node: str, *, weight: float = 1.0, direction: int = 1):
    family, _, typ = node.partition("/")
    path = [family] + ([typ] if typ else [])
    return DepositPayload(
        finding=f"F-{agent}-{node}",
        cluster=None,
        agent=agent,
        role=Role(role),
        taxonomy_path=path,
        signal_tier=SignalTier.S3,
        direction=direction,
        weight=weight,
    )


def _promo(agent: str, role: str, node: str, frm: str, to: str, exam_id: str) -> dict:
    return {
        "agent": agent, "role": role, "node": node, "exam_id": exam_id,
        "from_status": frm, "to_status": to,
    }


def _truth(spec: dict[str, dict], *, hidden: WorldHiddenParams | None = None) -> WorldTruth:
    """手工真值池：id → {role, archetype, family_q, type_eps, expert_family, t_drift}。"""
    role, arch, ef, td, fq, te = {}, {}, {}, {}, {}, {}
    for aid, s in spec.items():
        role[aid] = s.get("role", "auditor")
        arch[aid] = s.get("archetype", "normal")
        ef[aid] = s.get("expert_family")
        td[aid] = s.get("t_drift")
        fq[aid] = MappingProxyType(dict(s.get("family_q", {})))
        te[aid] = MappingProxyType(dict(s.get("type_eps", {})))
    return WorldTruth(
        agents=tuple(spec),
        role=MappingProxyType(role),
        archetype=MappingProxyType(arch),
        family_q=MappingProxyType(fq),
        type_eps=MappingProxyType(te),
        expert_family=MappingProxyType(ef),
        t_drift=MappingProxyType(td),
        hidden=hidden or WorldHiddenParams(),
    )


def _auditor_five() -> WorldTruth:
    """5 只 auditor，authz 家族真值 q 严格分层：a1 > a2 > a3 > a4 > a5。"""
    qs = (0.9, 0.75, 0.5, 0.3, 0.1)
    return _truth(
        {f"a{i}": {"family_q": {"authz": q}} for i, q in enumerate(qs, start=1)}
    )


def _timeline_1task_per_day(days: int = 400) -> TaskTimeline:
    return TaskTimeline([1] * days)


def _synthetic_ledger(tmp_path: Path, name: str = "synth.db") -> _EventLog:
    """16 条事件的迷你轨迹：3 格建格 + 追加存款 + 2 dispatch + 晋升/熄灭。"""
    log = _EventLog(tmp_path, name)
    cells = [
        ("s-1", "scout", "injection"),
        ("a-1", "auditor", "crypto"),
        ("r-1", "reviewer", "authz"),
    ]
    day = 1.0
    for a, r, n in cells:
        log.add(day, LedgerKind.DEPOSIT, _dep(a, r, n))
        day += 1
    for i in range(6):                                     # 交替方向追加存款
        a, r, n = cells[i % 3]
        log.add(day, LedgerKind.DEPOSIT, _dep(a, r, n, direction=1 if i % 2 == 0 else -1))
        day += 1
    log.add(day, LedgerKind.DISPATCH, DispatchPayload(
        task_id="T-000000", role=Role.SCOUT, node="injection", assignee="s-1"))
    day += 1
    log.add(day, LedgerKind.DISPATCH, DispatchPayload(
        task_id="T-000001", role=Role.AUDITOR, node="crypto", assignee="a-1"))
    day += 1
    log.add(day, LedgerKind.PROMOTION, _promo("s-1", "scout", "injection", "shadow", "candidate", "e1"))
    day += 1
    log.add(day, LedgerKind.PROMOTION, _promo("s-1", "scout", "injection", "candidate", "active", "e1"))
    day += 1
    log.add(day, LedgerKind.PROMOTION, _promo("a-1", "auditor", "crypto", "shadow", "candidate", "e2"))
    day += 1
    log.add(day, LedgerKind.PROMOTION, _promo("a-1", "auditor", "crypto", "candidate", "active", "e2"))
    day += 1
    log.add(day, LedgerKind.DEMOTION, {"agent": "s-1", "role": "scout", "node": "injection", "reason": "decay"})
    return log


def _synthetic_truth() -> WorldTruth:
    """与 _synthetic_ledger 配套的真值池（家族级格子节点；scout 池两只供冷启动对照）。"""
    return _truth({
        "s-1": {"role": "scout", "family_q": {"injection": 0.9, "crypto": 0.2, "authz": 0.1}},
        "s-2": {"role": "scout", "family_q": {"injection": 0.1, "crypto": 0.3, "authz": 0.2}},
        "a-1": {"role": "auditor", "family_q": {"injection": 0.4, "crypto": 0.8, "authz": 0.2}},
        "r-1": {"role": "reviewer", "family_q": {"injection": 0.1, "crypto": 0.3, "authz": 0.6}},
    })


def _tasks_5(n_days: int = 5) -> list[WorldTask]:
    return [
        WorldTask(
            task_id=f"T-{i:06d}", t=i, day=i, family="injection", type="",
            difficulty=0.5, mode=RunMode.FULL, cache_hit=False,
        )
        for i in range(n_days)
    ]


# ── WorldTruth：只读快照（世界对象不可变）── ac_muz07uljo0eb4y4g ──


def test_world_truth_snapshot_is_immutable():
    pool = AgentPool.from_params(seed=7)
    snap = snapshot_world_truth(pool)

    with pytest.raises(AttributeError):          # frozen dataclass：字段赋值被拒
        snap.role = {}                            # type: ignore[misc]
    with pytest.raises(TypeError):               # 内层映射只读
        snap.family_q["scout-01"]["injection"] = 0.99
    with pytest.raises(TypeError):
        snap.type_eps["scout-01"]["injection/sqli"] = 0.5
    with pytest.raises(TypeError):
        snap.archetype["scout-01"] = "expert"


def test_world_truth_capability_matches_pool_pre_and_post_drift():
    pool = AgentPool.from_params(seed=7)
    snap = snapshot_world_truth(pool)
    tax = load_taxonomy()
    fam, typ = "injection", tax.families["injection"].types[0]
    t_drift = pool.hidden_params.t_drift
    for agent in list(pool)[:4]:
        for t in (0, t_drift, t_drift + 1):      # 漂移前后逐位一致
            assert snap.capability(agent.agent_id, fam, t=t) == pytest.approx(
                pool.capability(agent.agent_id, fam, t=t)
            )
            assert snap.capability(agent.agent_id, fam, typ, t=t) == pytest.approx(
                pool.capability(agent.agent_id, fam, typ, t=t)
            )


def test_world_truth_snapshot_decoupled_from_pool_mutation():
    pool = AgentPool.from_params(seed=7)
    snap = snapshot_world_truth(pool)
    agent = pool.agents[0]
    fam = next(iter(agent.family_q))
    before = snap.capability(agent.agent_id, fam, t=0)
    agent.family_q[fam] = 0.0                    # 快照之后改池——快照不受影响
    assert snap.capability(agent.agent_id, fam, t=0) == before


def test_top_fraction_ranking_and_k():
    truth = _auditor_five()
    assert truth.top_fraction("auditor", "authz", frac=0.2) == ("a1",)      # ⌈0.2·5⌉ = 1
    assert truth.top_fraction("auditor", "authz", frac=0.4) == ("a1", "a2")
    assert truth.top_fraction("auditor", "authz", frac=1.0) == tuple(f"a{i}" for i in range(1, 6))
    tie = _truth({"x": {"family_q": {"f": 0.5}}, "y": {"family_q": {"f": 0.5}}})
    assert tie.top_fraction("auditor", "f", frac=0.5) == ("x",)             # 平手按 id
    assert truth.top_fraction("scout", "authz", frac=0.2) == ()             # 无该角色 → 空


# ── TaskTimeline：ts → 任务坐标 ─────────────────────────────────


def test_task_timeline_index_and_cum():
    tl = TaskTimeline([2, 3])
    assert tl.cum_tasks(-1) == 0
    assert tl.cum_tasks(0) == 2
    assert tl.cum_tasks(1) == 5
    assert tl.cum_tasks(9) == 5                                  # 越界夹取总数
    assert tl.task_index(T0) == 0.0
    assert tl.task_index(T0 - timedelta(days=1)) == 0.0          # 早于 t0 夹取 0
    assert tl.task_index(T0 + timedelta(hours=12)) == 1.0        # 0.5 日 × 2 任务
    assert tl.task_index(T0 + timedelta(days=1, hours=12)) == pytest.approx(2 + 1.5)
    assert tl.task_index(T0 + timedelta(days=9)) == 5.0          # 末日之后夹取总数


def test_task_timeline_from_tasks_and_monotone():
    tasks = _tasks_5()
    tl = TaskTimeline.from_tasks(tasks)
    assert tl.day_counts == (1, 1, 1, 1, 1) and tl.total_tasks == 5
    seq = [T0 + timedelta(days=d, hours=h) for d in range(4) for h in (1, 12)]
    idx = [tl.task_index(ts) for ts in seq]
    assert idx == sorted(idx)                                    # 单调不减


def test_expected_correctness_formula_and_clamp():
    assert expected_correctness(0.9, 0.0) == pytest.approx(0.9)
    assert expected_correctness(0.9, 1.0) == pytest.approx(0.9 * 0.7)
    assert expected_correctness(0.9, 1.0) < expected_correctness(0.9, 0.0)
    assert expected_correctness(-0.2, 0.0) == 0.01               # 下夹取
    assert expected_correctness(2.0, 0.0) == 0.99                # 上夹取


# ── cell_episodes：账本事件重放成逐格轨迹 ────────────────────────


def test_cell_episodes_folding(tmp_path: Path):
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("s1", "scout", "injection"))
    log.add(2.0, LedgerKind.DEPOSIT, _dep("a1", "auditor", "crypto/reveal"))
    log.add(3.0, LedgerKind.PROMOTION, _promo("a1", "auditor", "crypto", "shadow", "candidate", "e1"))
    log.add(4.0, LedgerKind.PROMOTION, _promo("s1", "scout", "injection", "shadow", "candidate", "e2"))
    log.add(5.0, LedgerKind.PROMOTION, _promo("a1", "auditor", "crypto", "candidate", "active", "e1"))
    log.add(6.0, LedgerKind.PROMOTION, _promo("s1", "scout", "injection", "candidate", "shadow", "e2"))
    log.add(7.0, LedgerKind.DEMOTION, {"agent": "a1", "role": "auditor", "node": "crypto", "reason": "decay"})

    eps = cell_episodes(log.ledger)
    s1 = eps[CellKey("s1", "scout", "injection")]
    assert s1.first_deposit_ts is not None and s1.activations == ()
    assert len(s1.exam_fails) == 1                               # 暗考不过
    fam = eps[CellKey("a1", "auditor", "crypto")]
    assert fam.first_deposit_ts is not None
    assert len(fam.activations) == 1 and len(fam.decay_demotions) == 1
    typ = eps[CellKey("a1", "auditor", "crypto/reveal")]
    assert typ.first_deposit_ts is not None                      # 前缀格同记账
    assert typ.activations == ()                                 # 晋升只落在显式 node


# ── ① 晋升查准：已知前 20% 集合的比例 ── ac_muz07uljxkv110uu ────


def test_promotion_precision_known_scenario(tmp_path: Path):
    truth = _auditor_five()
    tl = _timeline_1task_per_day()
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("a1", "auditor", "authz"))
    log.add(2.0, LedgerKind.DEPOSIT, _dep("a5", "auditor", "authz"))
    log.add(10.0, LedgerKind.PROMOTION, _promo("a1", "auditor", "authz", "candidate", "active", "e1"))
    log.add(11.0, LedgerKind.PROMOTION, _promo("a5", "auditor", "authz", "candidate", "active", "e2"))

    # top-20%（5 只取 1）= {a1}：a1 命中、a5 落空 → 查准 1/2
    assert promotion_precision(log.ledger, truth, tl) == pytest.approx(0.5)

    # 再晋 a2（仍非 top-1）→ 1/3
    log.add(12.0, LedgerKind.PROMOTION, _promo("a2", "auditor", "authz", "candidate", "active", "e3"))
    assert promotion_precision(log.ledger, truth, tl) == pytest.approx(1.0 / 3.0)

    # 口径放宽到 top-40%（取 2 = {a1, a2}）：a1、a2 命中，a5 落空 → 2/3
    assert promotion_precision(log.ledger, truth, tl, frac=0.4) == pytest.approx(2.0 / 3.0)


def test_promotion_precision_empty_returns_none(tmp_path: Path):
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("a1", "auditor", "authz"))
    assert promotion_precision(log.ledger, _auditor_five(), _timeline_1task_per_day()) is None


# ── ② 晋升查全：500 任务窗内的比例 ── ac_muz07uljxkv110uu ───────


def test_promotion_recall_known_scenario_and_window(tmp_path: Path):
    truth = _auditor_five()
    tl = _timeline_1task_per_day()
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("a1", "auditor", "authz"))     # 窗内流量
    log.add(2.0, LedgerKind.DEPOSIT, _dep("a2", "auditor", "authz"))
    log.add(10.0, LedgerKind.PROMOTION, _promo("a1", "auditor", "authz", "candidate", "active", "e1"))
    log.add(50.0, LedgerKind.PROMOTION, _promo("a2", "auditor", "authz", "candidate", "active", "e2"))

    # top-40% = {a1, a2}；窗 40 任务：a1(第10日)在内、a2(第50日)在外 → 0.5
    assert promotion_recall(log.ledger, truth, tl, frac=0.4, task_window=40) == pytest.approx(0.5)
    # 窗放宽到 60：全命中 → 1.0
    assert promotion_recall(log.ledger, truth, tl, frac=0.4, task_window=60) == pytest.approx(1.0)
    # 窗边界（夜间批口径：第 d 日末累计 = d+1 个任务）：
    # top-100% = 5 格，第 40 日末 = 41 任务：a1(第10日→11) 在内、a5(第40日→41) 在内、a2(第50日→51) 在外
    log.add(40.0, LedgerKind.PROMOTION, _promo("a5", "auditor", "authz", "candidate", "active", "e3"))
    assert promotion_recall(log.ledger, truth, tl, frac=1.0, task_window=40) == pytest.approx(1.0 / 5.0)
    assert promotion_recall(log.ledger, truth, tl, frac=1.0, task_window=41) == pytest.approx(2.0 / 5.0)


def test_promotion_recall_excludes_zero_traffic_pairs(tmp_path: Path):
    truth = _auditor_five()
    tl = _timeline_1task_per_day()
    log = _EventLog(tmp_path)
    # 只有 a3 的格子有流量：top-20% = {a1}，(auditor, authz) 有流量但 a1 无格 → 0/1
    log.add(1.0, LedgerKind.DEPOSIT, _dep("a3", "auditor", "authz"))
    log.add(10.0, LedgerKind.PROMOTION, _promo("a3", "auditor", "authz", "candidate", "active", "e1"))
    assert promotion_recall(log.ledger, truth, tl) == pytest.approx(0.0)
    # 完全无流量 → 分母为空 → None
    log2 = _EventLog(tmp_path, "empty.db")
    assert promotion_recall(log2.ledger, truth, tl) is None


# ── ③ 中位晋升时长：已知晋升时刻 ── ac_muz07uljxkv110uu ─────────


def test_median_time_to_active_known(tmp_path: Path):
    truth = _truth({
        "e1": {"role": "auditor", "archetype": "expert", "expert_family": "authz",
               "family_q": {"authz": 0.9, "crypto": 0.2}},
        "n1": {"role": "auditor", "family_q": {"authz": 0.5}},
    })
    tl = _timeline_1task_per_day()
    log = _EventLog(tmp_path)
    # e1 专家格 1：第 10 日建格、第 40 日晋升 → 30 任务
    log.add(10.0, LedgerKind.DEPOSIT, _dep("e1", "auditor", "authz"))
    log.add(40.0, LedgerKind.PROMOTION, _promo("e1", "auditor", "authz", "candidate", "active", "e1"))
    # e1 专家格 2（类型格）：第 20 日建格、第 90 日晋升 → 70 任务
    log.add(20.0, LedgerKind.DEPOSIT, _dep("e1", "auditor", "authz/sqli"))
    log.add(90.0, LedgerKind.PROMOTION, _promo("e1", "auditor", "authz/sqli", "candidate", "active", "e2"))
    # 非专家格：第 5 日建格、第 300 日晋升 → 295 任务
    log.add(5.0, LedgerKind.DEPOSIT, _dep("n1", "auditor", "authz"))
    log.add(300.0, LedgerKind.PROMOTION, _promo("n1", "auditor", "authz", "candidate", "active", "e3"))

    assert median_time_to_active(log.ledger, truth, tl) == pytest.approx(50.0)          # median(30, 70)
    assert median_time_to_active(log.ledger, truth, tl, only_experts=False) == pytest.approx(70.0)
    # family_scoped：e1 的 crypto 格（专家家族之外）不参与
    log.add(21.0, LedgerKind.DEPOSIT, _dep("e1", "auditor", "crypto"))
    log.add(90.0, LedgerKind.PROMOTION, _promo("e1", "auditor", "crypto", "candidate", "active", "e4"))
    assert median_time_to_active(log.ledger, truth, tl) == pytest.approx(50.0)
    assert median_time_to_active(
        log.ledger, truth, tl, only_experts=False, family_scoped=False
    ) == pytest.approx(69.5)                                    # median(30, 70, 295, 69) = (69+70)/2


def test_median_time_to_active_no_expert_observations_returns_none(tmp_path: Path):
    truth = _truth({"n1": {"family_q": {"authz": 0.5}}})
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("n1", "auditor", "authz"))
    log.add(9.0, LedgerKind.PROMOTION, _promo("n1", "auditor", "authz", "candidate", "active", "e1"))
    assert median_time_to_active(log.ledger, truth, _timeline_1task_per_day()) is None
    assert median_time_to_active(log.ledger, truth, _timeline_1task_per_day(), only_experts=False) == 8.0


# ── ④ 误熄灭率：已知熄灭情形 ── ac_muz07uljxkv110uu ─────────────


def test_wrong_extinguish_rate_known(tmp_path: Path):
    truth = _truth({
        "n1": {"family_q": {"authz": 0.5}},
        "n2": {"family_q": {"authz": 0.5}},
        "n3": {"family_q": {"authz": 0.5}},
        "g1": {"archetype": "degraded", "t_drift": 100, "family_q": {"authz": 0.5}},
    })
    log = _EventLog(tmp_path)
    for who, day in (("n1", 1.0), ("n2", 1.5), ("n3", 1.25), ("g1", 2.0)):
        log.add(day, LedgerKind.DEPOSIT, _dep(who, "auditor", "authz"))
    for who, day in (("n1", 5.0), ("n2", 5.5), ("n3", 10.0), ("g1", 6.0)):
        log.add(day, LedgerKind.PROMOTION, _promo(who, "auditor", "authz", "candidate", "active", f"e-{who}"))
    log.add(30.0, LedgerKind.DEMOTION, {"agent": "n1", "role": "auditor", "node": "authz", "reason": "decay"})
    log.add(31.0, LedgerKind.DEMOTION, {"agent": "n2", "role": "auditor", "node": "authz", "reason": "dormant"})
    # n3 的 decay 发生在首次激活之前 → 不算在任衰退
    log.add(2.5, LedgerKind.DEMOTION, {"agent": "n3", "role": "auditor", "node": "authz", "reason": "decay"})
    # 退化格的熄灭不进分母也不进分子
    log.add(32.0, LedgerKind.DEMOTION, {"agent": "g1", "role": "auditor", "node": "authz", "reason": "decay"})

    assert wrong_extinguish_rate(log.ledger, truth) == pytest.approx(1.0 / 3.0)


def test_wrong_extinguish_rate_no_active_cells_returns_none(tmp_path: Path):
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("n1", "auditor", "authz"))
    assert wrong_extinguish_rate(log.ledger, _auditor_five()) is None


# ── ⑤ 退化响应：漂移→熄灭任务数中位 ── ac_muz07uljxkv110uu ──────


def test_degradation_response_known(tmp_path: Path):
    truth = _truth({
        "g1": {"archetype": "degraded", "t_drift": 100, "family_q": {"authz": 0.5}},
        "g2": {"archetype": "degraded", "t_drift": 200, "family_q": {"authz": 0.5}},
        "g3": {"archetype": "degraded", "t_drift": 100, "family_q": {"authz": 0.5}},
        "n1": {"family_q": {"authz": 0.5}},
    })
    tl = _timeline_1task_per_day()
    log = _EventLog(tmp_path)
    # g1：漂移@100，第 250 日熄灭 → 响应 251−100 = 151（夜间批坐标：第 d 日末累计 d+1 任务）
    log.add(50.0, LedgerKind.DEPOSIT, _dep("g1", "auditor", "authz"))
    log.add(60.0, LedgerKind.PROMOTION, _promo("g1", "auditor", "authz", "candidate", "active", "e1"))
    log.add(250.0, LedgerKind.DEMOTION, {"agent": "g1", "role": "auditor", "node": "authz", "reason": "decay"})
    # g2：漂移@200，第 320 日熄灭 → 响应 321−200 = 121
    log.add(100.0, LedgerKind.DEPOSIT, _dep("g2", "auditor", "authz"))
    log.add(110.0, LedgerKind.PROMOTION, _promo("g2", "auditor", "authz", "candidate", "active", "e2"))
    log.add(320.0, LedgerKind.DEMOTION, {"agent": "g2", "role": "auditor", "node": "authz", "reason": "decay"})
    # g3：仅漂移前的熄灭 + 漂移后的 dormant → 都不算响应
    log.add(10.0, LedgerKind.DEPOSIT, _dep("g3", "auditor", "authz"))
    log.add(20.0, LedgerKind.PROMOTION, _promo("g3", "auditor", "authz", "candidate", "active", "e3"))
    log.add(50.0, LedgerKind.DEMOTION, {"agent": "g3", "role": "auditor", "node": "authz", "reason": "decay"})
    log.add(300.0, LedgerKind.DEMOTION, {"agent": "g3", "role": "auditor", "node": "authz", "reason": "dormant"})
    # 非退化格熄灭与退化响应无关
    log.add(1.0, LedgerKind.DEPOSIT, _dep("n1", "auditor", "authz"))
    log.add(400.0, LedgerKind.DEMOTION, {"agent": "n1", "role": "auditor", "node": "authz", "reason": "decay"})

    assert degradation_response(log.ledger, truth, tl) == pytest.approx(136.0)  # median(151, 121)


def test_degradation_response_no_post_drift_extinguish_returns_none(tmp_path: Path):
    truth = _truth({"g1": {"archetype": "degraded", "t_drift": 100, "family_q": {"authz": 0.5}}})
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DEPOSIT, _dep("g1", "auditor", "authz"))
    log.add(2.0, LedgerKind.DEMOTION, {"agent": "g1", "role": "auditor", "node": "authz", "reason": "decay"})
    assert degradation_response(log.ledger, truth, _timeline_1task_per_day()) is None


# ── ⑥ oracle 基线与 regret 曲线 ── ac_muz07uljcn4w28le ─────────


def _crypto_tasks() -> list[WorldTask]:
    difficulties = (0.0, 0.5, 1.0, 0.25)
    return [
        WorldTask(
            task_id=f"T-{i:03d}", t=i, day=i, family="crypto", type="reveal",
            difficulty=d, mode=RunMode.FULL, cache_hit=False,
        )
        for i, d in enumerate(difficulties)
    ]


def _crypto_truth() -> WorldTruth:
    return _truth({
        f"b{i}": {"family_q": {"crypto": q}, "type_eps": {"crypto/reveal": 0.0}}
        for i, q in zip((1, 2, 3), (0.9, 0.5, 0.1))
    })


def test_oracle_stream_outputs_per_task_baseline():
    tasks = _crypto_tasks()
    truth = _crypto_truth()
    steps = oracle_route_stream(tasks, truth)
    assert [s.task_id for s in steps] == [t.task_id for t in tasks]
    assert all(s.agent == "b1" for s in steps)                  # oracle 恒取隐藏 q argmax
    assert all(s.p_correct >= s.uniform_p_correct for s in steps)   # argmax ≥ 池均值
    for task, s in zip(tasks, steps):
        assert s.p_correct == pytest.approx(expected_correctness(0.9, task.difficulty))
        assert s.uniform_p_correct == pytest.approx(
            expected_correctness(statistics.fmean([0.9, 0.5, 0.1]), task.difficulty)
        )


def test_regret_curve_zero_against_oracle_and_positive_against_worst():
    tasks = _crypto_tasks()
    truth = _crypto_truth()

    # 实际路由 == oracle → 逐点差距 0，累计恒 0（基线侧可达）
    perfect = regret_curve(tasks, truth, {t.task_id: "b1" for t in tasks})
    assert all(p.gap == 0.0 and p.cum_regret == 0.0 for p in perfect)
    assert len(perfect) == len(tasks)

    # 实际路由恒取最差 → 逐点 > 0、累计单调不减、均值 > 0
    worst = regret_curve(tasks, truth, {t.task_id: "b3" for t in tasks})
    assert all(p.actual_p < p.oracle_p and p.gap > 0.0 for p in worst)
    cums = [p.cum_regret for p in worst]
    assert cums == sorted(cums)
    assert worst[-1].mean_regret > 0.0

    # RoutingRecord 序列输入与 Mapping 等价；未派单任务按全额 oracle 差距计
    # （未派单 → q=0 → 期望判对率取概率域下夹取 0.01）
    records = [RoutingRecord(task_id="T-000", role="auditor", node="crypto/reveal", assignee="b1")]
    mixed = regret_curve(tasks, truth, records)
    assert mixed[0].gap == 0.0
    assert mixed[1].actual_p == pytest.approx(expected_correctness(0.0, tasks[1].difficulty))
    assert mixed[1].gap == pytest.approx(mixed[1].oracle_p - mixed[1].actual_p)


def test_expected_and_uniform_series_align_with_tasks():
    tasks = _crypto_tasks()
    truth = _crypto_truth()
    routing = {t.task_id: "b1" for t in tasks}
    actual = expected_accuracy_series(tasks, truth, routing)
    baseline = uniform_accuracy_series(tasks, truth)
    steps = oracle_route_stream(tasks, truth)
    assert actual == [s.p_correct for s in steps]
    assert baseline == [s.uniform_p_correct for s in steps]


def test_routing_records_from_ledger_and_regret_roundtrip(tmp_path: Path):
    """dispatch 账本事件 → RoutingRecord 重建；⑥⑦ 仅凭账本可复算（内核输出侧）。"""
    log = _EventLog(tmp_path)
    recs = [
        RoutingRecord(task_id="T-000", role="auditor", node="crypto/reveal",
                      assignee="b1", channel="specialist"),
        RoutingRecord(task_id="T-001", role="auditor", node="crypto/reveal",
                      assignee="b2", channel="explore"),
        RoutingRecord(task_id="T-002", role="auditor", node="crypto/reveal",
                      assignee=None),
    ]
    for i, r in enumerate(recs):
        log.add(1.0 + i, LedgerKind.DISPATCH, DispatchPayload(
            task_id=r.task_id, role=Role.AUDITOR, node=r.node, assignee=r.assignee))
    out = routing_records_from_ledger(log.ledger)
    assert out == [replace(r, channel=None) for r in recs]   # channel 不落账 → None

    # regret 曲线：账本重建输入 == 内存记录输入（逐点相等）
    tasks = _crypto_tasks()[:3]
    truth = _crypto_truth()
    from_memory = regret_curve(tasks, truth, recs)
    from_ledger = regret_curve(tasks, truth, out)
    assert from_ledger == from_memory
    assert all(p.gap == 0.0 for p in from_ledger[:1])        # T-000 == oracle b1
    assert from_ledger[1].gap > 0.0                          # T-001 = b2（非 argmax）
    assert from_ledger[2].gap > 0.0                          # T-002 未派单 = 全额差距

    # 冷启动长度同判（⑦ 仅凭账本可复算）
    base = uniform_accuracy_series(tasks, truth)
    assert cold_start_length(expected_accuracy_series(tasks, truth, out), base) == \
        cold_start_length(expected_accuracy_series(tasks, truth, recs), base)


def test_routing_records_first_dispatch_wins_and_re_dispatch(tmp_path: Path):
    log = _EventLog(tmp_path)
    log.add(1.0, LedgerKind.DISPATCH, DispatchPayload(
        task_id="T-000", role=Role.AUDITOR, node="crypto", assignee="b2"))
    log.add(2.0, LedgerKind.DISPATCH, DispatchPayload(
        task_id="T-000", role=Role.AUDITOR, node="crypto", assignee="b3"))
    out = routing_records_from_ledger(log.ledger)
    assert len(out) == 1 and out[0].assignee == "b2"          # 同任务重复派单：首条为准

    log.add(3.0, LedgerKind.RE_DISPATCH, {
        "task_id": "T-000", "from_agent": "b2", "to_agent": "b1", "reason": "timeout"})
    assert routing_records_from_ledger(log.ledger)[0].assignee == "b2"       # 缺省不应用改派
    patched = routing_records_from_ledger(log.ledger, apply_re_dispatch=True)
    assert patched[0].assignee == "b1"                        # 应用改派 → to_agent

    log.add(4.0, LedgerKind.RE_DISPATCH, {
        "task_id": "T-999", "from_agent": "b1", "to_agent": "b3", "reason": "timeout"})
    final = routing_records_from_ledger(log.ledger, apply_re_dispatch=True)
    assert len(final) == 1 and final[0].assignee == "b1"      # 未匹配任务的改派忽略


def test_regret_late_over_early_narrowing_and_insufficient_evidence():
    """⑥ 曲线读数：收窄 < 1；前段零差距 / 空曲线 / 过短 → None（数据不足）。"""
    from qswarm.simulator.metrics import RegretPoint, regret_late_over_early

    def _pt(gap: float, i: int) -> RegretPoint:
        return RegretPoint(task_id=f"T-{i}", t=i, oracle_p=0.9, actual_p=0.9 - gap,
                           gap=gap, cum_regret=gap, mean_regret=gap)

    narrowing = [_pt(0.5 - 0.04 * i, i) for i in range(9)]      # 0.50 → 0.18
    ratio = regret_late_over_early(narrowing)
    assert ratio == pytest.approx((0.18 + 0.22 + 0.26) / (0.50 + 0.46 + 0.42))
    assert ratio < 1.0

    widening = [_pt(0.1 + 0.04 * i, i) for i in range(9)]
    assert regret_late_over_early(widening) > 1.0

    assert regret_late_over_early([]) is None                   # 空曲线
    assert regret_late_over_early([_pt(0.3, 0)]) is None        # 不足一个完整段
    flat_zero = [_pt(0.0, i) for i in range(9)]                 # 前段零差距：无收窄空间
    assert regret_late_over_early(flat_zero) is None


# ── ⑦ 冷启动长度：已知跨越点 ── ac_muz07uljxkv110uu ─────────────


def test_cold_start_length_known_crossing():
    actual = [0.5] * 5 + [0.6] * 10
    baseline = [0.5] * 15
    assert cold_start_length(actual, baseline, margin=0.05) == 6     # 0.6 > 0.55（严格超越）
    assert cold_start_length(actual, baseline, margin=0.1) is None   # 恰好 0.6 = 0.6 不算
    assert cold_start_length([0.5] * 15, baseline, margin=0.05) is None


def test_cold_start_length_window_smoothing_and_validation():
    a, b = [0.0, 1.0], [0.5, 0.5]
    assert cold_start_length(a, b, margin=0.05, window=1) == 2
    assert cold_start_length(a, b, margin=0.05, window=2) is None    # 均值 0.5 ≤ 0.55
    with pytest.raises(ValueError):
        cold_start_length([0.1], [0.1, 0.2])
    assert cold_start_length([], []) is None                         # 空序列无跨越 → None
    with pytest.raises(ValueError):
        cold_start_length([0.1, 0.2], [0.1, 0.2], window=0)
    with pytest.raises(ValueError):
        cold_start_length([0.1, 0.2], [0.1, 0.2], margin=-0.01)


def test_cold_start_seeded_vs_unseeded_known_comparison():
    """⑦ 播种 vs 无播种对照：已知跨越点、差值与 §2 目标判定。"""
    baseline = [0.5] * 20
    seeded = [0.5] * 5 + [0.7] * 15          # 第 6 个任务跨越（0.7 > 0.55）
    unseeded = [0.5] * 10 + [0.7] * 10       # 第 11 个任务跨越
    cmp_ = cold_start_seeded_vs_unseeded(seeded, unseeded, baseline)
    assert cmp_.seeded == 6 and cmp_.unseeded == 11
    assert cmp_.delta == 5 and cmp_.seeding_helps is True
    assert cmp_.meets_target(50) is True                      # 播种 6 < 50
    assert cmp_.meets_target(5) is False                      # 6 ≥ 5 不达标
    assert cmp_.margin == 0.05 and cmp_.window == 1


def test_cold_start_seeded_vs_unseeded_never_crosses_and_validation():
    baseline = [0.5] * 10
    cmp_ = cold_start_seeded_vs_unseeded([0.5] * 10, [0.6, 0.5, 0.5, 0.5, 0.5,
                                            0.5, 0.5, 0.5, 0.5, 0.5], baseline)
    assert cmp_.seeded is None                                # 播种侧从未超越 → None
    assert cmp_.unseeded == 1
    assert cmp_.delta is None and cmp_.seeding_helps is None  # 证据不足不判
    assert cmp_.meets_target(50) is False                     # 未跨越 = 未达标
    with pytest.raises(ValueError):                           # 与基线不等长拒绝
        cold_start_seeded_vs_unseeded([0.6, 0.7], [0.6], baseline)
    # window 平滑与 cold_start_length 一致（同参透传）
    a_s, a_u = [0.0, 1.0], [0.5, 0.5]
    cmp_w = cold_start_seeded_vs_unseeded(a_s, a_u, [0.5, 0.5], margin=0.05, window=2)
    assert cmp_w.seeded is None and cmp_w.unseeded is None    # 均值 0.5 ≤ 0.55


# ── ⑧ S2 饥饿传导：中位与增幅倍数 ── ac_muz07uljxkv110uu ────────


def test_s2_starvation_propagation_medians_and_ratios():
    rep = s2_starvation_propagation({
        0.2: [100.0, 200.0, 300.0],          # 中位 200
        0.35: [300.0, 500.0],                # 中位 400 → 2.0×
        0.5: [200.0],                        # 中位 200 → 1.0×
    })
    assert rep.medians[0.2] == pytest.approx(200.0)
    assert rep.medians[0.35] == pytest.approx(400.0)
    assert rep.medians[0.5] == pytest.approx(200.0)
    assert rep.ratios[0.2] == 1.0
    assert rep.ratios[0.35] == pytest.approx(2.0)
    assert rep.ratios[0.5] == pytest.approx(1.0)
    assert rep.passed                                        # 全部 ≤ 2×


def test_s2_starvation_propagation_detects_breach_and_missing():
    rep = s2_starvation_propagation(
        {0.2: [100.0], 0.35: [500.0], 0.5: [800.0]}, limit=2.0
    )
    assert rep.ratios[0.5] == pytest.approx(8.0)
    assert not rep.passed

    missing = s2_starvation_propagation({0.2: [100.0], 0.5: [None, None]})
    assert missing.medians[0.5] is None and missing.ratios[0.5] is None
    assert not missing.passed                                # 观测缺失 → 不判达标


# ── ⑨ 重放等价：多时点 rebuild vs 在线一致 ──────────────────────


def test_replay_equivalence_green_on_synthetic_ledger(tmp_path: Path):
    log = _synthetic_ledger(tmp_path)
    rep = replay_equivalence(log.ledger)
    assert rep.checkpoints, "应至少检查一个时点"
    assert rep.checkpoints[-1].seq == log.ledger.latest_seq()
    assert all(c.diffs == 0 for c in rep.checkpoints)
    assert rep.field_diffs == 0 and rep.table_consistent
    assert rep.passed and rep.equivalence_rate == 1.0


def test_replay_equivalence_detects_tampered_online_view(tmp_path: Path):
    log = _synthetic_ledger(tmp_path, "tamper.db")
    k = log.ledger.latest_seq()
    snap = agg_cells(log.ledger, log.ledger.read()[-1].ts)
    assert snap.cells
    key = next(iter(snap.cells))
    snap.cells[key] = replace(snap.cells[key], pos_w=snap.cells[key].pos_w + 1.0)
    rep = replay_equivalence(
        log.ledger, checkpoints=[k], online={k: snap}, check_table=False
    )
    assert not rep.passed
    assert rep.checkpoints[0].source == "captured"
    assert rep.checkpoints[0].diffs > 0
    assert rep.checkpoints[0].first_diffs                    # 取证信息非空
    with pytest.raises(ValueError):
        replay_equivalence(log.ledger, checkpoints=[0])


# ── RunMetrics 汇集与多种子聚合 ── ac_muz07uljo0eb4y4g ─────────


def test_collect_run_metrics_smoke(tmp_path: Path):
    truth = _synthetic_truth()
    log = _synthetic_ledger(tmp_path, "collect.db")
    tasks = _tasks_5()
    routing = {t.task_id: "s-1" for t in tasks}
    rm = collect_run_metrics(
        log.ledger, truth, TaskTimeline([1] * 30), tasks, routing, replay_equivalent=True
    )
    assert rm.promotion_precision == pytest.approx(1.0)      # 两个 active 格都是本角色 top-1
    assert rm.wrong_extinguish_rate == pytest.approx(0.5)    # s-1 被熄灭、a-1 没有
    assert rm.median_time_to_active is None                  # 池内无真专家
    assert rm.regret_total == pytest.approx(0.0)             # 实际路由 == oracle
    assert rm.cold_start_length == 1.0                       # 第 1 个任务即超均匀基线 +5pp
    assert rm.replay_equivalent is True
    curve = regret_curve(tasks, truth, routing)
    assert rm.regret_per_task == pytest.approx(curve[-1].mean_regret)
    assert rm.regret_late_over_early is None                 # 前段零差距 → 无收窄证据
    assert set(rm.to_dict()) == {
        "promotion_precision", "promotion_recall", "median_time_to_active",
        "wrong_extinguish_rate", "degradation_response", "regret_total",
        "regret_per_task", "regret_late_over_early",
        "cold_start_length", "replay_equivalent",
    }


def test_mean_std_and_aggregate_seeds():
    agg = aggregate_seeds([
        {"precision": 0.9, "recall": 0.7, "empty": None},
        {"precision": 0.8, "recall": None, "empty": None},
        {"precision": 1.0, "recall": 0.9, "empty": None},
    ])
    assert agg["precision"].n == 3
    assert agg["precision"].mean == pytest.approx(0.9)
    assert agg["precision"].std == pytest.approx(statistics.stdev([0.9, 0.8, 1.0]))
    assert agg["recall"].n == 2 and agg["recall"].mean == pytest.approx(0.8)
    assert agg["recall"].std == pytest.approx(statistics.stdev([0.7, 0.9]))
    assert agg["empty"].n == 0 and agg["empty"].mean is None and agg["empty"].std is None

    single = aggregate_seeds([{"x": 1.0}])
    assert single["x"].mean == 1.0 and single["x"].std is None and single["x"].n == 1

    assert mean_std([None, None]).n == 0
    assert mean_std([1.0, None, 3.0]).mean == pytest.approx(2.0)


def test_aggregate_run_metrics_over_seeds():
    runs = [
        RunMetrics(
            promotion_precision=0.9, promotion_recall=0.7,
            median_time_to_active=100.0, wrong_extinguish_rate=0.02,
            degradation_response=120.0, regret_total=1.0, regret_per_task=0.01,
            cold_start_length=30.0, replay_equivalent=True,
        ),
        RunMetrics(
            promotion_precision=0.8, promotion_recall=0.9,
            median_time_to_active=200.0, wrong_extinguish_rate=0.04,
            degradation_response=140.0, regret_total=3.0, regret_per_task=0.03,
            cold_start_length=50.0, replay_equivalent=False,
        ),
    ]
    agg = aggregate_run_metrics(runs)
    assert len(agg) == 10
    assert agg["median_time_to_active"].mean == pytest.approx(150.0)
    assert agg["median_time_to_active"].std == pytest.approx(statistics.stdev([100.0, 200.0]))
    assert agg["replay_equivalent"].mean == pytest.approx(0.5)   # bool 按 0/1 参与聚合
    assert agg["regret_late_over_early"].mean is None            # 未观测 = None 跳过


def test_aggregate_run_metrics_over_real_engine_seeds():
    """多种子聚合走真实引擎 run（非合成数）：mean±std 逐位可核。"""
    runs = [run(seed=s, n_days=4, replay_every=0) for s in (11, 22)]
    assert runs[0].digest != runs[1].digest                      # 不同种子 → 不同轨迹
    agg = aggregate_run_metrics([r.metrics for r in runs])
    regret_vals = [r.metrics.regret_total for r in runs]
    assert agg["regret_total"].n == 2
    assert agg["regret_total"].mean == pytest.approx(statistics.fmean(regret_vals))
    assert agg["regret_total"].std == pytest.approx(statistics.stdev(regret_vals))
    cold_vals = [r.metrics.cold_start_length for r in runs]
    assert all(v is not None for v in cold_vals)
    assert agg["cold_start_length"].mean == pytest.approx(statistics.fmean(cold_vals))
    assert agg["cold_start_length"].std == pytest.approx(statistics.stdev(cold_vals))
    assert agg["replay_equivalent"].mean == pytest.approx(1.0)   # 两次 run 重放全等价


# ── 与世界组件的端到端冒烟（真实池 + 真实任务流的可计算性）────────


def test_metrics_computable_on_real_pool_and_task_stream(tmp_path: Path):
    params_seed, n_days = 11, 6
    pool = AgentPool.from_params(seed=params_seed)
    truth = snapshot_world_truth(pool)
    tasks = TaskGenerator(seed=params_seed).generate(n_days)
    routing = [RoutingRecord(task_id=t.task_id, role="scout", node=t.family, assignee=None)
               for t in tasks]                                # 未派单流（全额 oracle 差距）
    steps = oracle_route_stream(tasks, truth)
    assert len(steps) == len(tasks)
    curve = regret_curve(tasks, truth, routing)
    assert curve[-1].cum_regret > 0.0
    cold_actual = expected_accuracy_series(tasks, truth, routing)
    cold_base = uniform_accuracy_series(tasks, truth)
    assert len(cold_actual) == len(cold_base) == len(tasks)
    tl = TaskTimeline.from_tasks(tasks)
    log = _EventLog(tmp_path, "realpool.db")
    log.add(0.5, LedgerKind.DEPOSIT, _dep("scout-01", "scout", "injection"))
    eps = cell_episodes(log.ledger)
    assert CellKey("scout-01", "scout", "injection") in eps
    assert tl.cum_tasks(n_days - 1) == len(tasks)
    assert {a for a in truth.agents_with_role("scout")} >= {"scout-01"}
    assert Archetype(truth.archetype["scout-01"]) in Archetype
