"""M0 定稿 v2 机制验收测试：孵化直晋席位通道 + 裁量复核熄灭通道。

被测对象（全部默认关闭、由 configs/params.yaml 定稿开启的旋钮，DECISIONS
2026-10-09 v2 条）：
  - simulator.exam.seat_selection       席位化提名的纯函数核心（k/门槛/边际/平手）
  - simulator.exam.seed_entry_exams     类型级 quiz 与 (role, node) 席位提名
  - simulator.exam.ForCauseExamCenter   裁量复核考（§8.1 淘汰通道的考试本体）
  - kernel.promote_scan(for_cause=…)    转换⑥：FC_NET 触发 → 复核考不过 → 熄灭
  - kernel.promote_scan 转换② v2        直晋候选人的宽限/生产佐证/暗考兜底收官

默认关闭（旋钮全 0）的逐字回归：v1 直晋（K=1 ace）路径事件序列不变、promote_scan
不触发复核/佐证分支——由本文件与既有套件共同钉住。
"""

from datetime import datetime, timedelta, timezone
import math

from qswarm.contracts import LedgerKind, ParamsConfig, load_params, load_taxonomy
from qswarm.kernel import FixedClock, KernelLedger, promote_scan
from qswarm.simulator.exam import (
    ExamWorldParams,
    ForCauseExamCenter,
    seed_entry_exams,
    seat_selection,
)
from qswarm.simulator.world import AgentPool

AS_OF_DT = datetime(2030, 6, 30, tzinfo=timezone.utc)
AS_OF = "2030-06-30T00:00:00Z"
TAX = load_taxonomy()
PARAMS = load_params()


def params_with(**kv) -> ParamsConfig:
    data = PARAMS.model_dump()
    for key, value in kv.items():
        section, name = key.split(".")
        data[section][name] = value
    return ParamsConfig.model_validate(data)


# ══ seat_selection：席位化提名纯函数 ═════════════════════════════


def test_seat_selection_k_bar_margin_and_ties():
    scored = [(7, "a03"), (6, "a01"), (6, "a02"), (5, "a04"), (4, "a05")]
    # k = ⌈0.2·5⌉ = 1；平手按 id；门槛与边际满足
    assert seat_selection(scored, top_frac=0.2, bar=0.5, margin=0, items=8) == ["a03"]
    # 边际 2：入选最低 7 − 未入选最高 6 = 1 < 2 → 整席空（宁缺毋滥）
    assert seat_selection(scored, top_frac=0.2, bar=0.5, margin=2, items=8) == []
    # 边际 1 通过
    assert seat_selection(scored, top_frac=0.2, bar=0.5, margin=1, items=8) == ["a03"]
    # 门槛不满足：最高 7/8 < 0.9·8=7.2 → 空
    assert seat_selection(scored, top_frac=0.2, bar=0.9, margin=0, items=8) == []
    # k=2（top_frac 0.4）：入选 {a03, a01}（6 分平手取 id 小者）
    assert seat_selection(scored, top_frac=0.4, bar=0.5, margin=0, items=8) == [
        "a03", "a01"
    ]


def test_seat_selection_top_frac_zero_or_empty():
    assert seat_selection([], top_frac=0.2, bar=0.0, margin=0, items=8) == []
    assert seat_selection([(8, "a")], top_frac=0.0, bar=0.0, margin=0, items=8) == []


# ══ seed_entry_exams：v2 类型 quiz 与席位提名 ════════════════════


def _seed(tmp_path, params, *, quiz_items=12):
    pool = AgentPool.generate(params.roster.model_dump(), TAX, seed=11)
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "l.db"
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        seed_entry_exams(
            pool, led, seed=11, params=params,
            config=ExamWorldParams(type_quiz_items=quiz_items),
        )
        events = led.read()
    return pool, events


def test_v2_off_reproduces_v1_k1_lane(tmp_path):
    """旋钮全关（TOP_FRAC=0/TYPE_ENTRY=0）：提名恰为 K=1 ace，无类型 quiz 存款。"""
    p = params_with(**{
        "exam.ENTRY_DIRECT": 0.875, "exam.ENTRY_TOP_FRAC": 0.0,
        "exam.TYPE_ENTRY": 0.0, "exam.ENTRY_MARGIN": 0,
        "exam.ENTRY_GRACE_DAYS": 0, "exam.ENTRY_CORROB_S2S3": 0.0,
        "exam.ENTRY_WINDOW_DAYS": 0, "exam.TYPE_ENTRY_COMBINE": 0.0,
        "demote.FC_DAYS": 0,
    })
    pool, events = _seed(tmp_path, p)
    promos = [e for e in events if e.kind is LedgerKind.PROMOTION]
    quiz_dep = [
        e for e in events
        if e.kind is LedgerKind.DEPOSIT and len(e.payload["taxonomy_path"]) == 2
    ]
    assert quiz_dep == []                       # 关闭态零类型存款、零新随机消耗
    # K=1：同 (role, family) 至多一条直晋
    seen = set()
    for e in promos:
        key = (str(e.payload["role"]), str(e.payload["node"]))
        assert key not in seen
        seen.add(key)
        assert str(e.payload["exam_id"]).startswith("entry-fast:")
        assert str(e.payload["to_status"]) == "candidate"


def test_v2_seats_and_type_quiz_nominations(tmp_path):
    """v2 开启：席位=⌈0.2n⌉、类型节点获提名、quiz 为提名考试不落存款。"""
    p = params_with(**{
        "exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
        "exam.TYPE_ENTRY": 0.5, "exam.ENTRY_MARGIN": 0,
    })
    pool, events = _seed(tmp_path, p)
    promos = [e for e in events if e.kind is LedgerKind.PROMOTION]
    # 存款形态：§8.4 家族级入学存款（一级路径，每 agent × family 恰一笔）+
    # 类型席位的入格推动存款（两级路径，恰为每个类型提名一笔；§7.1 惰性建格
    # 要求提名格有存款键才能被夜批/度量看见）。
    deposits = [e for e in events if e.kind is LedgerKind.DEPOSIT]
    fam_dep = [e for e in deposits if len(e.payload["taxonomy_path"]) == 1]
    type_dep = [e for e in deposits if len(e.payload["taxonomy_path"]) == 2]
    assert len(fam_dep) == len(pool.agents) * 6
    type_promos = [e for e in promos if "/" in str(e.payload["node"])]
    assert len(type_dep) == len(type_promos)     # 每个类型提名恰一笔入格存款
    assert all(len(e.payload["taxonomy_path"]) == 2 for e in type_dep)
    by_role_node = {}
    for e in promos:
        by_role_node.setdefault(
            (str(e.payload["role"]), str(e.payload["node"])), []
        ).append(str(e.payload["agent"]))
        assert str(e.payload["exam_id"]).startswith("entry-fast:")
    type_noms = [k for k in by_role_node if "/" in k[1]]
    assert type_noms, "类型节点应有席位提名"
    nom_pairs = {(str(e.payload["agent"]), str(e.payload["role"]),
                  str(e.payload["node"])) for e in promos if "/" in str(e.payload["node"])}
    dep_pairs = {(str(e.payload["agent"]), str(e.payload["role"]),
                  "/".join(e.payload["taxonomy_path"])) for e in type_dep}
    assert nom_pairs == dep_pairs               # 类型存款与提名一一对应
    for (role, node), agents in by_role_node.items():
        n_role = sum(1 for a in pool.agents if a.role.value == role)
        assert len(agents) <= max(1, -(-n_role * 2 // 10))   # ⌈0.2·n⌉ 上界


def test_v2_new_substream_does_not_move_entry_exams(tmp_path):
    """'entry-type' 子流与 'entry' 正交：开/关类型 quiz，入学考存款逐位不变。"""
    p_off = params_with(**{"exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.0,
                           "exam.TYPE_ENTRY": 0.0})
    p_on = params_with(**{"exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
                          "exam.TYPE_ENTRY": 0.9})
    pool_off, ev_off = _seed(tmp_path / "off", p_off)
    pool_on, ev_on = _seed(tmp_path / "on", p_on)
    dep = lambda evs: [
        (str(e.payload["agent"]), str(e.payload["role"]),
         tuple(e.payload["taxonomy_path"]), e.payload["direction"],
         e.payload["weight"])
        for e in evs if e.kind is LedgerKind.DEPOSIT
        and len(e.payload["taxonomy_path"]) == 1
    ]
    # §8.4 家族级入学存款逐位一致（类型提名入格存款是 v2-on 的追加，不回改家族流）
    assert dep(ev_off) == dep(ev_on)


# ══ 转换② v2：宽限 / 生产佐证 / 暗考兜底 ═════════════════════════

def _nominate(db, agent: str, role: str, node: str, ts: str) -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "promotion",
            {
                "agent": agent, "role": role, "node": node,
                "exam_id": f"entry-fast:{agent}:{node}",
                "from_status": "shadow", "to_status": "candidate",
            },
        )


def _prod(db, ts: str, agent: str, node: str, *, d: int = 1, tier: str = "S3",
          n: int = 1, role: str = "auditor") -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        for i in range(n):
            led.append(
                "deposit",
                {
                    "finding": None, "cluster": f"c{i:02d}", "agent": agent,
                    "role": role, "taxonomy_path": [node], "signal_tier": tier,
                    "direction": d, "weight": 1.0, "origin": "prod",
                },
            )


class ScriptedExam:
    """暗考脚本化回调：按调用序返回结果，记录调用。"""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, *, agent_id, role, node, from_status):
        self.calls.append((agent_id, role, node))
        if not self.results:
            return None
        r = self.results.pop(0)
        return r if r is None else type("O", (), {"passed": r, "exam_id": "x:1"})


def test_corroboration_fast_track_activates_without_dark_exam(tmp_path):
    """佐证达标：免暗考直转 active（exam_id=entry-corroborated:*），回调零调用。"""
    db = tmp_path / "l.db"
    _nominate(db, "a1", "auditor", "injection", days_ago(10))
    _prod(db, days_ago(8), "a1", "injection", d=1, n=3)   # n_eff_s2s3=6
    p = params_with(**{
        "exam.ENTRY_GRACE_DAYS": 5, "exam.ENTRY_CORROB_S2S3": 3.0,
        "exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
        "exam.TYPE_ENTRY": 0.5,
    })
    exam = ScriptedExam([True])
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        trans = promote_scan(led, AS_OF, params=p, exam=exam)
    acts = [t for t in trans if t.to_status == "active"]
    assert len(acts) == 1 and acts[0].exam_id.startswith("entry-corroborated:")
    assert exam.calls == []                       # 佐证通道不经暗考


def test_grace_defers_dark_exam_then_fallback(tmp_path):
    """宽限期内不落暗考；期满未佐证 → §8.2 暗考兜底（回调恰一次）。"""
    db = tmp_path / "l.db"
    _nominate(db, "a1", "auditor", "injection", days_ago(4))
    _prod(db, days_ago(4), "a1", "injection", d=-1, n=4)  # 负证据，无佐证
    p = params_with(**{
        "exam.ENTRY_GRACE_DAYS": 3, "exam.ENTRY_CORROB_S2S3": 3.0,
        "exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
        "exam.TYPE_ENTRY": 0.5,
    })
    exam = ScriptedExam([False])
    # 提名后第 1 夜（宽限 3 天内）：不考、零流转
    mid = (AS_OF_DT - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with KernelLedger(db, clock=FixedClock(mid)) as led:
        trans = promote_scan(led, mid, params=p, exam=exam)
    assert trans == [] and exam.calls == []
    # 第 5 天（提名 +4 天 > 宽限 3）：暗考兜底
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        trans = promote_scan(led, AS_OF, params=p, exam=exam)
    assert exam.calls == [("a1", "auditor", "injection")]
    fails = [t for t in trans if t.reason == "exam_fail"]
    assert len(fails) == 1


def test_corroboration_disabled_matches_literal(tmp_path):
    """GRACE=0 且 CORROB=0：直晋候选人当夜即暗考（§8.2 逐字，无宽限分支）。"""
    db = tmp_path / "l.db"
    _nominate(db, "a1", "auditor", "injection", AS_OF)
    _prod(db, AS_OF, "a1", "injection", d=1, n=3)
    p = params_with(**{
        "exam.ENTRY_GRACE_DAYS": 0, "exam.ENTRY_CORROB_S2S3": 0.0,
        "exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
        "exam.TYPE_ENTRY": 0.5,
    })
    exam = ScriptedExam([True])
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        promote_scan(led, AS_OF, params=p, exam=exam)
    assert exam.calls == [("a1", "auditor", "injection")]


def days_ago(n: int) -> str:
    return (AS_OF_DT - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ══ 转换⑥：裁量复核熄灭通道（FC_NET）═════════════════════════════


def _active_cell(db, agent: str, node: str, *, activate_days_ago: int = 20) -> None:
    """铺一格 active：晋升事件（20 天前激活）+ 流量簇 + 正向质量。"""
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        led.append(
            "deposit",
            {"finding": None, "cluster": None, "agent": agent, "role": "auditor",
             "taxonomy_path": [node], "signal_tier": "S1", "direction": 1,
             "weight": 0.2, "origin": "exam", "exam_id": "x"},
        )
    with KernelLedger(db, clock=FixedClock(days_ago(activate_days_ago))) as led:
        led.append(
            "promotion",
            {"agent": agent, "role": "auditor", "node": node, "exam_id": "e:1",
             "from_status": "candidate", "to_status": "active"},
        )
    with KernelLedger(db, clock=FixedClock(days_ago(activate_days_ago))) as led:
        for i in range(12):
            led.append(
                "deposit",
                {"finding": None, "cluster": f"c{i:02d}", "agent": agent,
                 "role": "auditor", "taxonomy_path": [node], "signal_tier": "S3",
                 "direction": 1, "weight": 1.0, "origin": "prod"},
            )


class ScriptedForCause:
    """周期复核脚本化回调：以 (baseline, below, decayed) 三元组脚本应答。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self.script:
            return None
        baseline, below, decayed = self.script.pop(0)
        return type("O", (), {"baseline": baseline, "below": below,
                              "decayed": decayed, "passed": not below,
                              "exam_id": f"fc:{len(self.calls)}"})


FC_PARAMS = {
    "demote.FC_DAYS": 2, "demote.FC_ITEMS": 60, "demote.FC_MARGIN": 0.18,
    "demote.FC_CONSEC": 2, "demote.FC_BASE_EXAMS": 2,
    "exam.ENTRY_DIRECT": 0.5, "exam.ENTRY_TOP_FRAC": 0.2,
    "exam.TYPE_ENTRY": 0.5,
}


def test_for_cause_consecutive_fails_extinguish_with_decay_reason(tmp_path):
    """周期复核连续 2 场不达标 → demotion(reason=decay) 熄灭。"""
    db = tmp_path / "l.db"
    _active_cell(db, "a1", "injection")
    p = params_with(**FC_PARAMS)
    fc = ScriptedForCause([
        (True, False, False),   # 立基线场：不判
        (False, False, False),  # 达标：连续清零
        (False, True, False),   # 不达标 #1
        (False, True, True),    # 不达标 #2 → 熄灭
    ])
    days = ["2030-06-28T00:00:00Z", "2030-06-28T12:00:00Z",
            "2030-06-29T00:00:00Z", "2030-06-29T12:00:00Z"]
    for d in days:
        with KernelLedger(db, clock=FixedClock(d)) as led:
            trans = promote_scan(led, d, params=p, for_cause=fc)
    dems = [t for t in trans if t.kind == "demotion"]
    assert len(dems) == 1 and dems[0].reason == "decay"
    assert dems[0].from_status == "active" and dems[0].to_status == "shadow"
    assert dems[0].exam_id.startswith("fc:")


def test_for_cause_pass_resets_and_never_fires_without_history(tmp_path):
    """达标清零连续计数；无复核夜（day % FC_DAYS ≠ 0）回调不受传唤路径影响。"""
    db = tmp_path / "l.db"
    _active_cell(db, "a1", "injection")
    p = params_with(**FC_PARAMS)
    fc = ScriptedForCause([
        (True, False, False),
        (False, True, False),   # 不达标 #1
        (False, False, False),  # 达标 → 清零
        (False, True, False),   # 重新计数 #1（不熄灭）
    ])
    for d in ("2030-06-28T00:00:00Z", "2030-06-28T12:00:00Z",
              "2030-06-29T00:00:00Z", "2030-06-29T12:00:00Z"):
        with KernelLedger(db, clock=FixedClock(d)) as led:
            trans = promote_scan(led, d, params=p, for_cause=fc)
    assert [t for t in trans if t.kind == "demotion"] == []


def test_for_cause_disabled_when_fc_days_zero(tmp_path):
    """FC_DAYS=0（默认）：周期复核通道关闭（§7.3 逐字）。"""
    db = tmp_path / "l.db"
    _active_cell(db, "a1", "injection")
    p = params_with(**{**FC_PARAMS, "demote.FC_DAYS": 0})
    fc = ScriptedForCause([(False, True, True)])
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        trans = promote_scan(led, AS_OF, params=p, for_cause=fc)
    assert [t for t in trans if t.kind == "demotion"] == []
    assert fc.calls == []


# ══ ForCauseExamCenter：周期复核考本体 ═══════════════════════════


def _fc_center(tmp_path, params):
    pool = AgentPool.generate(params.roster.model_dump(), TAX, seed=11)
    db = tmp_path / "l.db"
    led = KernelLedger(db, clock=FixedClock(AS_OF))
    center = ForCauseExamCenter(pool, led, seed=7, params=params)
    return center, led


def test_for_cause_exam_baseline_then_trend_and_determinism(tmp_path):
    """首场立基线不判；趋势判定 = score < 基线均值 − MARGIN；同种子逐位可复现。"""
    p = params_with(**FC_PARAMS)
    pool = AgentPool.generate(p.roster.model_dump(), TAX, seed=11)
    led_a = KernelLedger(tmp_path / "a.db", clock=FixedClock(AS_OF))
    led_b = KernelLedger(tmp_path / "b.db", clock=FixedClock(AS_OF))
    center = ForCauseExamCenter(pool, led_a, seed=7, params=p)
    twin = ForCauseExamCenter(
        AgentPool.generate(p.roster.model_dump(), TAX, seed=11),
        led_b, seed=7, params=p,
    )
    o1 = center.run_for_cause_exam("scout-01", "scout", "injection", day=2, t=0)
    assert o1.baseline and not o1.below and o1.consecutive == 0
    o1b = twin.run_for_cause_exam("scout-01", "scout", "injection", day=2, t=0)
    assert o1.score == o1b.score                 # 同种子逐位可复现
    o2 = center.run_for_cause_exam("scout-01", "scout", "injection", day=4, t=0)
    ref = o1.score
    assert o2.below == (o2.score < ref - 0.18)
    assert o2.consecutive == (1 if o2.below else 0)
    assert o2.passed == (not o2.below)
    led_a.close()
    led_b.close()


def test_for_cause_exam_phase_gated_by_period(tmp_path):
    """非复核夜（day % FC_DAYS ≠ 0）返回 None，不消耗随机数（attempts 不变）。"""
    p = params_with(**FC_PARAMS)
    center, led = _fc_center(tmp_path, p)
    assert center.run_for_cause_exam("scout-01", "scout", "injection",
                                     day=1, t=0) is None
    assert center.attempts("scout-01", "scout", "injection") == 0
    assert center.run_for_cause_exam("scout-01", "scout", "injection",
                                     day=2, t=0) is not None
    assert center.attempts("scout-01", "scout", "injection") == 1
    led.close()
