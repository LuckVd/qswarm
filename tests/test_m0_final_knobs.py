"""M0 定稿旋钮验收测试（DECISIONS 2026-10-09 机制层三改动 + ⑧ 零基准边界）。

被测对象（全部默认关闭、由 configs/params.yaml 定稿开启的旋钮）：
  - promote.M_PRORATE        判据③ M 按节点期望簇份额等比缩放（kernel.promote_scan.effective_M）
  - promote.UNIQUE_PER_FAMILY 同族唯一专员：同 (role, family) 至多一个在途/active 格
  - promote.WINDOW_ADVANTAGE 多窗优势稳定性：最近 K 窗逐窗 LB > 同窗基线 + δ
  - exam.ENTRY_DIRECT        入学播种直晋 candidate（§8.4 孵化侧延伸；暗考闸门不动）
  - ⑧ s2_starvation_propagation 零基准边界（各档中位同为 0 → 增幅 1.0）

§7.3 字面语义（旋钮全关）回归仍由 tests/test_kernel_promote_scan.py 钉住。
"""

from datetime import datetime, timedelta, timezone

import pytest

from qswarm.contracts import (
    LedgerKind,
    ParamsConfig,
    load_params,
)
from qswarm.kernel import (
    CellKey,
    FixedClock,
    KernelLedger,
    agg_cells,
    promote_scan,
)
from qswarm.kernel.promote_scan import effective_M, evaluate_candidate_criteria
from qswarm.simulator.exam import seed_entry_exams
from qswarm.simulator.metrics import s2_starvation_propagation
from qswarm.simulator.world import AgentPool

AS_OF_DT = datetime(2030, 6, 30, tzinfo=timezone.utc)
AS_OF = "2030-06-30T00:00:00Z"
PARAMS = load_params()


def params_with(**promote_exam) -> ParamsConfig:
    """在 yaml 基础上覆盖定稿旋钮（其余字段不动，过全量校验）。"""
    data = PARAMS.model_dump()
    for key, value in promote_exam.items():
        section, name = key.split(".")
        data[section][name] = value
    return ParamsConfig.model_validate(data)


def days_ago(n: int) -> str:
    return (AS_OF_DT - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def dep(db, ts: str, *, agent: str, role: str = "auditor",
        path: tuple[str, ...] = ("injection",), tier: str = "S3",
        d: int = 1, w: float = 1.0, cluster: str | None = None) -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "deposit",
            {
                "finding": None, "cluster": cluster, "agent": agent, "role": role,
                "taxonomy_path": list(path), "signal_tier": tier,
                "direction": d, "weight": w, "origin": "prod",
            },
        )


def fill_traffic(db, ts: str, n: int = 30, agent: str = "z",
                 node: str = "injection") -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        for i in range(n):
            led.append(
                "deposit",
                {
                    "finding": None, "cluster": f"c{i:02d}", "agent": agent,
                    "role": "auditor", "taxonomy_path": [node],
                    "signal_tier": "S1", "direction": 1, "weight": 0.01,
                    "origin": "prod",
                },
            )


def scan(db, **kw):
    if "params" not in kw:
        kw["params"] = params_with()
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        return promote_scan(led, AS_OF, **kw)


def promo_events(db):
    with KernelLedger(db) as led:
        return led.read(kinds=(LedgerKind.PROMOTION,))


# ══ promote.M_PRORATE：判据③有效 M ═══════════════════════════════

def test_effective_m_literal_when_off():
    """M_PRORATE=False：§7.3 逐字，全节点同一 M。"""
    p = params_with(**{"promote.M_PRORATE": False})
    for node in ("injection", "injection/sqli", "mem_safety", "mem_safety/overflow"):
        assert effective_M(node, params=p) == p.promote.M


def test_effective_m_prorate_by_expected_cluster_share():
    """M_PRORATE=True：最高份额节点保持 M，其余按 (v1_weight/扇出)/max 等比缩放。

    taxonomy（M5 types 面补齐后，SPEC-9 §8）：injection(1.0, 6 类型)
    / deser(1.0, 2) / crypto·config_dep(1.0, 3) / authz(1.0, 4) / mem_safety(0.3, 2)。
    per-type 份额：deser 0.5（max）、crypto·config_dep 1/3、authz 1/4、injection 1/6、
    mem_safety 0.15 → M=30 时：deser type 30、crypto/config_dep type 20、authz
    type 15、injection type 10、mem_safety type 9；家族级（无扇出）：weight-1.0 → 30、
    mem_safety → 9；未知家族容错回 M。
    """
    p = params_with(**{"promote.M_PRORATE": True})
    assert p.promote.M == 30
    assert effective_M("deser", params=p) == 30
    assert effective_M("deser/java_gadget", params=p) == 30
    assert effective_M("crypto", params=p) == 30
    assert effective_M("crypto/weak_algo", params=p) == 20
    assert effective_M("authz/idor", params=p) == 15
    assert effective_M("config_dep/secret_leak", params=p) == 20
    assert effective_M("injection", params=p) == 30
    assert effective_M("injection/sqli", params=p) == 10
    assert effective_M("mem_safety", params=p) == 9
    assert effective_M("mem_safety/overflow", params=p) == 9
    assert effective_M("no_such_family", params=p) == 30      # 未知家族容错
    assert effective_M("mem_safety/overflow", params=p) >= 1  # 下限 1


def test_traffic_criterion_uses_effective_m(tmp_path):
    """判据③端到端：prorate 下 10 簇恰达 injection type 门槛（10），9 簇被卡。"""
    for clusters, ok in ((10, True), (9, False)):
        db = tmp_path / f"l{clusters}.db"
        for _ in range(5):
            dep(db, days_ago(3), agent="a", path=("injection", "sqli"))
        dep(db, days_ago(3), agent="b", d=1, w=0.1)
        dep(db, days_ago(3), agent="b", d=-1, w=0.1)
        fill_traffic(db, days_ago(3), n=clusters, node="injection/sqli",
                     agent="z2")
        p = params_with(**{"promote.M_PRORATE": True,
                           "promote.UNIQUE_PER_FAMILY": False,
                           "promote.WINDOW_ADVANTAGE": 0,
                           "exam.ENTRY_DIRECT": 0.0})
        with KernelLedger(db) as led:
            snap = evaluate_candidate_criteria(
                led, AS_OF, CellKey("a", "auditor", "injection/sqli"), params=p,
            )
        assert snap.traffic == clusters
        assert snap.traffic_ok is ok
        assert snap.m_eff == 10


# ══ promote.UNIQUE_PER_FAMILY：同族唯一专员 ═══════════════════════

def _two_claimants_ledger(db):
    """格 a@injection 先晋（scan1 → candidate），随后 b@injection 亦达四判据。

    a：5×S3(+1)@AS_OF → n_eff=10.0（z 的 30 簇全负向微存款 → LOO 基线 0.0，
    LB=0.788 > 0+0.1 ✓）；scan1（WADV=0/UNIQUE=False）后 a 转负 5×S3(−1) 拉低
    LOO 基线（baseline(b) ≈ 0.485 < LB(b)=0.788−0.1）；b：5×S3(+1)@AS_OF 恰达
    判据①。全部存款 @AS_OF（零龄期、无挥发），数值可手算。
    """
    for _ in range(5):
        dep(db, AS_OF, agent="a")
    fill_traffic(db, AS_OF, n=30, agent="z2")            # z2：全负向微流量填充
    # fill_traffic 只写正向；这里直接落 30 簇负向（方向不影响 ③ 的簇计数，
    # 但把基线拉到 0，使 a 的 LB 端 0.788 > 0+0.10 成立）
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        for i in range(30):
            led.append(
                "deposit",
                {
                    "finding": None, "cluster": f"n{i:02d}", "agent": "z3",
                    "role": "auditor", "taxonomy_path": ["injection"],
                    "signal_tier": "S3", "direction": -1, "weight": 0.01,
                    "origin": "prod",
                },
            )
    scan(db, params=params_with(**{"promote.UNIQUE_PER_FAMILY": False,
                                   "promote.WINDOW_ADVANTAGE": 0,
                                   "promote.M_PRORATE": False,
                                   "exam.ENTRY_DIRECT": 0.0}))
    for _ in range(5):
        dep(db, AS_OF, agent="a", d=-1)
    for _ in range(5):
        dep(db, AS_OF, agent="b")


@pytest.mark.parametrize("unique", [False, True])
def test_unique_per_family_gates_second_promotion(tmp_path, unique):
    db = tmp_path / "l.db"
    _two_claimants_ledger(db)
    transitions = scan(db, params=params_with(**{"promote.UNIQUE_PER_FAMILY": unique,
                                                 "promote.WINDOW_ADVANTAGE": 0,
                                                 "promote.M_PRORATE": False,
                                                 "exam.ENTRY_DIRECT": 0.0}))
    b_promotions = [t for t in transitions if t.agent_id == "b"]
    if unique:
        assert b_promotions == []            # 家族被 a（candidate）占用 → 不晋升
        view = None
        with KernelLedger(db) as led:
            view = agg_cells(led, AS_OF,
                             params=params_with(**{"promote.UNIQUE_PER_FAMILY": True}))
        assert view.get("b", "auditor", "injection").status == "shadow"
    else:
        assert len(b_promotions) == 1        # §7.3 字面：无同族约束
        assert b_promotions[0].to_status == "candidate"


def test_unique_guard_dormant_reactivation_takes_seat_before_b(tmp_path):
    """dormant 不静态占位，但重激活（转换⑤）在守卫下先取回席位：a 复活、b 被挡。"""
    db = tmp_path / "l.db"
    _two_claimants_ledger(db)
    with KernelLedger(db, clock=FixedClock(days_ago(1))) as led:
        led.append(
            "promotion",
            {"agent": "a", "role": "auditor", "node": "injection",
             "exam_id": "x1", "from_status": "candidate", "to_status": "active"},
        )
        led.append(
            "demotion",
            {"agent": "a", "role": "auditor", "node": "injection",
             "reason": "dormant"},
        )
    p = params_with(**{"promote.UNIQUE_PER_FAMILY": True,
                       "promote.WINDOW_ADVANTAGE": 0,
                       "promote.M_PRORATE": False,
                       "exam.ENTRY_DIRECT": 0.0})
    transitions = scan(db, params=p)
    a_transitions = [t for t in transitions if t.agent_id == "a"]
    b_transitions = [t for t in transitions if t.agent_id == "b"]
    # a：dormant → n_eff 20 ≥ REACTIVATE_N → 转换⑤重激活取回家族席位；
    # b：四判据全满足但同族席位被本轮复活的 a 占用 → 守卫挡下。
    assert [(t.reason, t.to_status) for t in a_transitions] == [("reactivate", "active")]
    assert b_transitions == []


# ══ promote.WINDOW_ADVANTAGE：多窗优势稳定性 ═════════════════════

def _stale_advantage_ledger(db):
    """累计优势成立但最近 2 窗（14 天）无任何窗内数据：只允许判据②①③④全过。"""
    for _ in range(10):
        dep(db, days_ago(20), agent="a")                 # 窗外（>14d）的累计优势
    dep(db, days_ago(20), agent="b", d=1, w=1.0)
    dep(db, days_ago(20), agent="b", d=-1, w=1.0)        # 基线 0.5（同在窗外）
    fill_traffic(db, days_ago(20), n=30)                 # 流量在窗内与否不影响 ③（90d 窗）


def test_window_advantage_blocks_stale_cumulative_edge(tmp_path):
    db = tmp_path / "l.db"
    _stale_advantage_ledger(db)
    p_off = params_with(**{"promote.WINDOW_ADVANTAGE": 0,
                           "promote.UNIQUE_PER_FAMILY": False,
                           "promote.M_PRORATE": False,
                           "exam.ENTRY_DIRECT": 0.0})
    with KernelLedger(db) as led:
        snap = evaluate_candidate_criteria(
            led, AS_OF, CellKey("a", "auditor", "injection"), params=p_off,
        )
    assert snap.advantage_ok, "前提：累计优势成立（LB=0.899 > 0.5+0.1）"
    p_on = params_with(**{"promote.WINDOW_ADVANTAGE": 1,
                          "promote.UNIQUE_PER_FAMILY": False,
                          "promote.M_PRORATE": False,
                          "exam.ENTRY_DIRECT": 0.0})
    with KernelLedger(db) as led:
        snap1 = evaluate_candidate_criteria(
            led, AS_OF, CellKey("a", "auditor", "injection"), params=p_on,
        )
    assert snap1.window_advantage_ok is False
    assert snap1.advantage_ok is False
    assert snap1.failed() == ("advantage",)


def test_window_advantage_passes_with_recent_evidence(tmp_path):
    """最近窗数据充分且逐窗占优 → WADV 不阻碍晋升。"""
    db = tmp_path / "l.db"
    for _ in range(10):
        dep(db, days_ago(3), agent="a")
    dep(db, days_ago(3), agent="b", d=1, w=1.0)
    dep(db, days_ago(3), agent="b", d=-1, w=1.0)
    for _ in range(10):
        dep(db, days_ago(10), agent="a")
    dep(db, days_ago(10), agent="b", d=1, w=1.0)
    dep(db, days_ago(10), agent="b", d=-1, w=1.0)
    fill_traffic(db, days_ago(3), n=30)
    p = params_with(**{"promote.WINDOW_ADVANTAGE": 2,
                       "promote.UNIQUE_PER_FAMILY": False,
                       "promote.M_PRORATE": False,
                       "exam.ENTRY_DIRECT": 0.0})
    with KernelLedger(db) as led:
        snap = evaluate_candidate_criteria(
            led, AS_OF, CellKey("a", "auditor", "injection"), params=p,
        )
    assert snap.window_advantage_ok is True
    assert snap.advantage_ok and snap.all_ok


# ══ exam.ENTRY_DIRECT：入学播种直晋 candidate ═════════════════════

def _seed_with(entry_direct: float, seed: int = 42):
    import tempfile
    from pathlib import Path
    pool = AgentPool.from_params(PARAMS, seed=seed)
    p = params_with(**{
        "exam.ENTRY_DIRECT": entry_direct,
        "exam.ENTRY_TOP_FRAC": 0.0, "exam.TYPE_ENTRY": 0.0,
        "exam.ENTRY_MARGIN": 0, "exam.ENTRY_GRACE_DAYS": 0,
        "exam.ENTRY_CORROB_S2S3": 0.0, "exam.ENTRY_WINDOW_DAYS": 0,
        "exam.TYPE_ENTRY_COMBINE": 0.0, "demote.FC_DAYS": 0,
    })
    with tempfile.TemporaryDirectory(prefix="qswarm-entryknob-") as td:
        with KernelLedger(Path(td) / "l.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
            outcomes = seed_entry_exams(pool, led, seed=seed, params=p)
            events = led.read(kinds=(LedgerKind.PROMOTION,))
    return pool, outcomes, events, p


def test_entry_direct_zero_writes_no_promotions():
    """ENTRY_DIRECT=0（关闭）：播种只写存款，零 promotion 事件（§8.4 字面）。"""
    _pool, _outcomes, events, _p = _seed_with(0.0)
    assert events == []


def test_entry_direct_promotes_best_ace_per_role_family():
    """ENTRY_DIRECT=0.875：正确率 ≥ 7/8 且方向为正的格直晋 candidate，
    同 (role, family) 恰一人（最高正确率，平手取 agent_id 字典序）。"""
    pool, outcomes, events, p = _seed_with(0.875)
    threshold = p.exam.ENTRY_DIRECT
    expected = {}
    for o in outcomes:
        if o.direction == 1 and o.accuracy >= threshold:
            key = (o.role.value, o.family)
            cur = expected.get(key)
            if cur is None or (-o.accuracy, o.agent_id) < (-cur[0], cur[1]):
                expected[key] = (o.accuracy, o.agent_id)
    got = {
        (str(e.payload["role"]), e.payload["node"]): e.payload["agent"]
        for e in events
    }
    assert {k: v for k, v in expected.items()} and len(events) == len(expected)
    assert got == {k: v for k, (_, v) in expected.items()}
    for e in events:
        assert e.payload["from_status"] == "shadow"
        assert e.payload["to_status"] == "candidate"
        assert e.payload["exam_id"].startswith("entry-fast:")


def test_entry_direct_candidates_visible_in_cells():
    """直晋事件经 cells 重放投影：对应格 status=candidate（暗考由夜批收官）。"""
    pool, outcomes, events, p = _seed_with(0.875)
    assert events, "seed=42 池中应存在 7/8 及以上正确率的格"
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(prefix="qswarm-entryknob2-") as td:
        with KernelLedger(Path(td) / "l.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
            seed_entry_exams(pool, led, seed=42, params=p)
            view = agg_cells(led, "2030-01-01T00:00:00Z", params=p)
    for e in events:
        cell = view.get(str(e.payload["agent"]), str(e.payload["role"]), str(e.payload["node"]))
        assert cell is not None and cell.status == "candidate"


def test_entry_direct_threshold_boundary_is_inclusive():
    """正确率恰等于门槛（含等号语义）→ 直晋；低于 → 不直晋。

    12 题口径构造等号可达场景（DECISIONS 2026-10-10）：entry_items=12 下正确率
    只能落在 k/12 网格上，旧 8 题口径的 0.875=10.5/12 数学不可达（曾致本用例
    永久 skip）。改为从种子表**导出**等号门槛：取某 (role, family) 的最高正向
    正确率 a*=k/12（6≤k<12）作 ENTRY_DIRECT，则该族最高分格恰在等号上——必
    直晋；事件集与按同规则重构的期望集逐格相等，严格低于门槛的正向格绝不出现。
    找不到可达族顶按 fail 处理（12 题口径下不应发生），不设 skip 路径。
    """
    for seed in (42, 43, 44, 45, 46):
        _pool, outcomes, _ev, _p0 = _seed_with(0.0, seed=seed)
        # 各 (role, family) 最高正向正确率（best-ace 同规则：分高优先、平手取小 id）
        family_top: dict[tuple[str, str], float] = {}
        for o in outcomes:
            if o.direction == 1:
                key = (o.role.value, o.family)
                cur = family_top.get(key)
                if cur is None or o.accuracy > cur:
                    family_top[key] = o.accuracy
        reachable = sorted(a for a in family_top.values() if 0.5 <= a < 1.0)
        if not reachable:
            continue
        k = round(reachable[0] * 12)                   # 门槛落到 k/12 网格（逐位精确）
        assert 6 <= k < 12
        threshold = k / 12
        _pool, outcomes, events, _p = _seed_with(threshold, seed=seed)
        expected = {}
        for o in outcomes:
            if o.direction == 1 and o.accuracy >= threshold:
                key = (o.role.value, o.family)
                cur = expected.get(key)
                if cur is None or (-o.accuracy, o.agent_id) < (-cur[0], cur[1]):
                    expected[key] = (o.accuracy, o.agent_id)
        got = {
            (str(e.payload["role"]), e.payload["node"]): e.payload["agent"]
            for e in events
        }
        # 等号见证：门槛恰取自某族最高正向正确率 → 该族席位格 accuracy==门槛且在事件中
        at_boundary = [key for key, (a, _aid) in expected.items() if a == threshold]
        assert at_boundary, "等号门槛应至少命中一个族顶格"
        assert got == {key: agent for key, (_a, agent) in expected.items()}
        below = {(o.role.value, o.family, o.agent_id) for o in outcomes
                 if o.direction == 1 and o.accuracy < threshold}
        assert not below & {(r, n, a) for (r, n), a in got.items()}
        for e in events:
            assert e.payload["from_status"] == "shadow"
            assert e.payload["to_status"] == "candidate"
            assert e.payload["exam_id"].startswith("entry-fast:")
        return
    pytest.fail("种子 42-46 中无可达等号族顶（6/12≤top<12/12）；12 题口径下不应发生")


# ══ ⑧ s2_starvation_propagation：零基准边界 ══════════════════════

def test_s2_starvation_all_zero_medians_are_no_propagation():
    """各档中位晋升时长同为 0（直晋通道对 verify_error 不敏感）→ 增幅 1.0、达标。"""
    rep = s2_starvation_propagation({0.2: [0.0, None], 0.35: [0.0], 0.5: [0.0, 0.0]})
    assert rep.medians == {0.2: 0.0, 0.35: 0.0, 0.5: 0.0}
    assert rep.ratios == {0.2: 1.0, 0.35: 1.0, 0.5: 1.0}
    assert rep.passed


def test_s2_starvation_zero_reference_with_positive_other_is_nodata():
    """基准档为 0 而他档 >0：增幅不可判（0→x 无界）→ ratio=None、不判达标。"""
    rep = s2_starvation_propagation({0.2: [0.0], 0.35: [120.0], 0.5: [300.0]})
    assert rep.ratios[0.2] == 1.0
    assert rep.ratios[0.35] is None and rep.ratios[0.5] is None
    assert not rep.passed
