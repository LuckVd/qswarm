"""kernel.promote_scan 验收测试（设计 §7.3 专长孵化状态机）。

对照子任务验收标准：
  - ac_muz07uljdan2ltwy  账本片段对四判据逐条独立证伪：任一不满足不晋升，
    全满足才转 candidate 并落 promotion 事件；
  - ac_muz07uljm4xn1d6a  Wilson LB/UB 与标准公式在 ≥3 组 (n, p̂) 手算对照一致；
  - ac_muz07uljb4f70nxa  连续 2 窗衰退触发熄灭、单窗不触发；60 天无任务转
    dormant；dormant 格 n_eff≥5 才重激活（各有测试断言）；
  - ac_muz07uljm7q2n3s2  promote_scan 幂等：同一账本重复执行两次，cells 状态
    与账本事件不变。

构造约定：AS_OF=2030-06-30（UTC 零点）为扫描时点；存款/事件时间戳全部用
FixedClock 精确落到 days_ago(n)，整日龄期使 2^(−n/30) 与窗口边界可手算；
扫描一律用 _open（时钟钉在 AS_OF，扫描新落事件的 ts == as_of，可复现）。
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from qswarm.contracts import (
    CellStatus,
    ExamPurpose,
    LedgerKind,
    ParamsConfig,
    Role,
    load_params,
)
from qswarm.kernel import (
    CellKey,
    FixedClock,
    KernelLedger,
    agg_cells,
    promote_scan,
    wilson_bounds,
    wilson_lb,
    wilson_ub,
    WILSON_Z,
)
from qswarm.kernel.promote_scan import evaluate_candidate_criteria
from qswarm.simulator import ExamCenter, ExamWorldParams, WorldHiddenParams

AS_OF_DT = datetime(2030, 6, 30, tzinfo=timezone.utc)
AS_OF = "2030-06-30T00:00:00Z"
PARAMS = load_params()


def spec_literal_params():
    """§7.3 字面口径参数：M0 定稿旋钮（M_PRORATE/UNIQUE_PER_FAMILY/
    WINDOW_ADVANTAGE/ENTRY_* 直晋通道/FC 复核通道）全部关闭，§7.3 门槛字面值
    钉死（N=10/DELTA=0.10/M=30/WINDOWS=2/DORMANT=60/REACTIVATE=5/COOLDOWN=14）
    ——本文件的判据语义与手算锚不随 params.yaml 定稿漂移。"""
    data = PARAMS.model_dump()
    data["promote"].update({
        "M_PRORATE": False, "UNIQUE_PER_FAMILY": False, "WINDOW_ADVANTAGE": 0,
        "N": 10, "N_S2S3": 3, "DELTA": 0.10, "M": 30, "M_window_days": 90,
    })
    data["exam"].update({
        "ENTRY_DIRECT": 0.0, "ENTRY_TOP_FRAC": 0.0, "ENTRY_MARGIN": 0,
        "TYPE_ENTRY": 0.0, "ENTRY_GRACE_DAYS": 0, "ENTRY_CORROB_S2S3": 0.0,
        "ENTRY_WINDOW_DAYS": 0, "TYPE_ENTRY_COMBINE": 0.0, "COOLDOWN_DAYS": 14,
    })
    data["demote"].update({"WINDOWS": 2, "DORMANT_DAYS": 60, "REACTIVATE_N": 5,
                           "FC_DAYS": 0})
    return ParamsConfig.model_validate(data)


def days_ago(n: int) -> str:
    """AS_OF 前 n 整天的 UTC ISO 时间戳（整日龄期 → 挥发因子恰为 2^(−n/30)）。"""
    return (AS_OF_DT - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _open(db):
    """扫描用句柄：时钟钉在 AS_OF——扫描新落事件的 ts == as_of（可复现）。"""
    return KernelLedger(db, clock=FixedClock(AS_OF))


def dep(
    db,
    ts: str,
    *,
    agent: str,
    role: str = "auditor",
    path: tuple[str, ...] = ("injection",),
    tier: str = "S3",
    d: int = 1,
    w: float = 1.0,
    cluster: str | None = None,
    origin: str = "prod",
) -> None:
    """落一笔存款（每次重开库注入 FixedClock，与既有 kernel 测试同款）。"""
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "deposit",
            {
                "finding": None,
                "cluster": cluster,
                "agent": agent,
                "role": role,
                "taxonomy_path": list(path),
                "signal_tier": tier,
                "direction": d,
                "weight": w,
                "origin": origin,
            },
        )


def fill_traffic(db, ts: str, n: int = 30, agent: str = "z", node: str = "injection") -> None:
    """n 个不同簇的微小生产存款：只喂判据③的类型流量计数（权重 0.01 极小）。"""
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        for i in range(n):
            led.append(
                "deposit",
                {
                    "finding": None,
                    "cluster": f"c{i:02d}",
                    "agent": agent,
                    "role": "auditor",
                    "taxonomy_path": [node],
                    "signal_tier": "S1",
                    "direction": 1,
                    "weight": 0.01,
                    "origin": "prod",
                },
            )


def promo_event(db, ts, agent, node="injection", *, exam_id, frm, to, role="auditor"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "promotion",
            {"agent": agent, "role": role, "node": node, "exam_id": exam_id,
             "from_status": frm, "to_status": to},
        )


def demote_event(db, ts, agent, node="injection", *, reason, role="auditor"):
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("demotion", {"agent": agent, "role": role, "node": node, "reason": reason})


def scan(db, **kw):
    """§7.3 字面语义扫描：显式钉 M0 定稿旋钮全关（M_PRORATE/UNIQUE_PER_FAMILY=
    False、WINDOW_ADVANTAGE=0、ENTRY_DIRECT=0），不随 params.yaml 定稿漂移——
    本文件验证的是 §7.3 状态机本体；定稿旋钮的行为见 tests/test_m0_final_knobs.py。"""
    if "params" not in kw:
        kw["params"] = spec_literal_params()
    with _open(db) as led:
        return promote_scan(led, AS_OF, **kw)


def read_events(db, kinds=None):
    """扫描后统一重开只读句柄取事件（scan 返回前句柄已关闭）。"""
    with KernelLedger(db) as led:
        return led.read() if kinds is None else led.read(kinds=kinds)


def cells_rows(db):
    import sqlite3

    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells"
            " ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


def ledger_rows(db):
    with KernelLedger(db) as led:
        return [
            (e.seq, e.ts, e.kind.value, sorted(e.payload.items(), key=lambda kv: kv[0]))
            for e in read_events(db)
        ]


def view_of(db):
    with KernelLedger(db) as led:
        return agg_cells(led, AS_OF)


# ══ 公共账本片段：正例（四判据全满足）与其逐条证伪变体 ════════════
#
# 格 a/auditor/injection：5×S3(+1,w=1.0)@as_of → total=5.0、n_eff=10.0（恰达
# 门槛，验证 ≥ 语义）、n_eff_s2s3=10.0、C_raw=1.0、LB=1/(1+z²/10)=0.788047。
# 基线（LOO 剔 a）：b 贡献 pos=1.0/neg=1.0；z 的流量填充贡献 pos≈0.30
# → 均值 ≈ 1.3/2.3 ≈ 0.5652；LB > 0.5652+0.10 ✓。
# z 的 30 个不同簇（90 天窗内）恰好满足判据③ M=30（恰达门槛）。

def _positive_ledger(db, *, a_weight: float = 1.0, a_count: int = 5,
                     a_tier: str = "S3", traffic: int = 30,
                     baseline_agent_b: bool = True) -> None:
    for _ in range(a_count):
        dep(db, AS_OF, agent="a", tier=a_tier, w=a_weight)
    if baseline_agent_b:
        dep(db, AS_OF, agent="b", d=1, w=1.0)
        dep(db, AS_OF, agent="b", d=-1, w=1.0)
    fill_traffic(db, AS_OF, n=traffic)


# ══ ac_muz07uljm4xn1d6a：Wilson LB/UB 手算对照 ═════════════════

def test_wilson_matches_hand_computed_values():
    """z=1.64 原式逐对手算（Decimal 40 位精度独立计算后取 10 位小数）：

    n=2(pos+neg)、p̂=pos/(pos+neg)；LB/UB = (p̂ ± z·√(p̂(1−p̂)/n + z²/4n²) + z²/2n)/(1+z²/n)
      (n=10, p̂=0.80): denom=1.26896 center=0.93448 √(0.016+0.006724)=√0.022724
                      → LB=0.5415919330 UB=0.9312362097
      (n=28, p̂=0.50): denom=1.096057… center=0.548057… → LB=0.3519807367 UB=0.6480192633
      (n=4,  p̂=0.25): → LB=0.0581295804 UB=0.6428988817
      (n=50, p̂=0.92): → LB=0.8336249536 UB=0.9634963000
      (n=10, p̂=1.00): LB=1/(1+z²/n)=0.7880469046，UB≡1
    """
    assert WILSON_Z == 1.64
    cases = [
        # (pos, neg)            n   p̂      LB            UB
        (4.0, 1.0),   # n=10, p̂=0.80
        (7.0, 7.0),   # n=28, p̂=0.50
        (0.5, 1.5),   # n=4,  p̂=0.25
        (23.0, 2.0),  # n=50, p̂=0.92
        (5.0, 0.0),   # n=10, p̂=1.00
    ]
    expected = [
        (0.5415919330, 0.9312362097),
        (0.3519807367, 0.6480192633),
        (0.0581295804, 0.6428988817),
        (0.8336249536, 0.9634963000),
        (0.7880469046, 1.0000000000),
    ]
    for (pos, neg), (lb, ub) in zip(cases, expected):
        got = wilson_bounds(pos, neg)
        assert got[0] == pytest.approx(lb, abs=1e-9)
        assert got[1] == pytest.approx(ub, abs=1e-9)
        assert got[0] == wilson_lb(pos, neg)
        assert got[1] == wilson_ub(pos, neg)
        # 区间有效性：LB ≤ p̂ ≤ UB
        p_hat = pos / (pos + neg)
        assert got[0] <= p_hat <= got[1]


def test_wilson_boundary_and_monotonicity():
    """端点与单调性质（同公式在不同 (n, p̂, z) 下的方向性手算核对）。"""
    assert wilson_bounds(0.0, 0.0) is None            # 无质量：区间无定义
    zero = wilson_bounds(0.0, 3.0)                    # p̂=0：LB 恰为 0
    assert zero[0] == 0.0 and 0.0 < zero[1] < 1.0
    one = wilson_bounds(2.0, 0.0)                     # p̂=1：UB = (1+z²/n)/(1+z²/n) = 1
    assert one[1] == pytest.approx(1.0) and 0.0 < one[0] < 1.0
    # 同 p̂=0.8，n 从 10 → 32：样本越大 LB 越高（不确定性收窄）
    assert wilson_lb(4.0, 1.0) < wilson_lb(12.8, 3.2)
    # z 越大区间越宽：LB 更低、UB 更高
    assert wilson_lb(4.0, 1.0, z=1.28) > wilson_lb(4.0, 1.0) > wilson_lb(4.0, 1.0, z=1.96)
    assert wilson_ub(4.0, 1.0, z=1.28) < wilson_ub(4.0, 1.0) < wilson_ub(4.0, 1.0, z=1.96)


# ══ ac_muz07uljdan2ltwy：四判据逐条独立证伪 ═════════════════════

def _snapshot(db, agent="a", node="injection"):
    with KernelLedger(db) as led:
        return evaluate_candidate_criteria(
            led, AS_OF, CellKey(agent, "auditor", node),
            params=spec_literal_params(),
        )


def test_all_four_criteria_met_promotes_to_candidate_with_event(tmp_path):
    """正例：四判据全满足 → 转 candidate 并落 promotion 事件（ac 前半）。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    transitions = scan(db)

    assert len(transitions) == 1
    t = transitions[0]
    assert (t.agent_id, t.role, t.node) == ("a", "auditor", "injection")
    assert (t.from_status, t.to_status, t.reason, t.kind) == (
        "shadow", "candidate", "candidate", "promotion")
    assert t.criteria.all_ok and t.criteria.failed() == ()

    events = read_events(db, (LedgerKind.PROMOTION,))
    assert len(events) == 1                     # 恰一条 promotion 事件
    payload = events[0].payload
    assert payload["agent"] == "a" and payload["node"] == "injection"
    assert payload["from_status"] == "shadow" and payload["to_status"] == "candidate"
    assert read_events(db, (LedgerKind.DEMOTION, LedgerKind.EXAM_RESULT)) == []

    v = view_of(db)
    cell = v.get("a", "auditor", "injection")
    assert cell.status == "candidate"           # cells 状态与事件同扫落定
    assert cell.n_eff == pytest.approx(10.0)    # 恰达 N=10 门槛（≥ 语义）
    assert cell.n_eff_s2s3 == pytest.approx(10.0)


def test_criterion1_n_eff_below_N_blocks(tmp_path):
    """证伪①：n_eff=8 < 10（其余三条全满足）→ 不晋升。"""
    db = tmp_path / "l.db"
    _positive_ledger(db, a_count=4)             # 4×S3 → n_eff=8.0、s2s3=8.0≥3
    transitions = scan(db)
    assert transitions == []
    snap = _snapshot(db)
    assert snap.failed() == ("hard",)
    assert snap.n_eff == pytest.approx(8.0) and snap.n_eff_s2s3 == pytest.approx(8.0)
    assert snap.advantage_ok and snap.traffic_ok and snap.gate_ok
    assert view_of(db).get("a", "auditor", "injection").status == "shadow"
    assert read_events(db, (LedgerKind.PROMOTION,)) == []


def test_criterion1_s2s3_below_three_blocks(tmp_path):
    """证伪①（硬门槛后半）：n_eff=10 达标但 n_eff_s2s3=0 < 3（纯 S1 堆量）→ 不晋升。"""
    db = tmp_path / "l.db"
    _positive_ledger(db, a_count=20, a_tier="S1", a_weight=0.25)  # 20×0.25=5.0（精确）
    transitions = scan(db)
    assert transitions == []
    snap = _snapshot(db)
    assert snap.failed() == ("hard",)
    assert snap.n_eff == pytest.approx(10.0)    # 总量恰好达标
    assert snap.n_eff_s2s3 == pytest.approx(0.0)
    assert snap.advantage_ok and snap.traffic_ok and snap.gate_ok
    assert read_events(db, (LedgerKind.PROMOTION,)) == []


def test_criterion2_wilson_advantage_blocks(tmp_path):
    """证伪②：LOO 基线被强者抬高（均值≈0.903），LB 0.788 < 0.903+0.10 → 不晋升。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    for _ in range(9):                          # b：合计 10 正 2 负（含正例的 ±1）
        dep(db, AS_OF, agent="b", d=1, w=1.0)
    dep(db, AS_OF, agent="b", d=-1, w=1.0)
    transitions = scan(db)
    assert transitions == []                    # a、b 都不该晋升（b 自身 LB<基线+δ）
    snap = _snapshot(db)
    assert snap.failed() == ("advantage",)
    assert snap.wilson_lb == pytest.approx(0.7880469046, abs=1e-9)
    assert snap.baseline == pytest.approx(10.3 / 12.3, abs=1e-9)
    assert snap.hard_ok and snap.traffic_ok and snap.gate_ok
    assert read_events(db, (LedgerKind.PROMOTION,)) == []


def test_criterion3_type_traffic_below_M_blocks(tmp_path):
    """证伪③：90 天窗簇数 29 < M=30（其余三条全满足）→ 不晋升。"""
    db = tmp_path / "l.db"
    _positive_ledger(db, traffic=29)
    transitions = scan(db)
    assert transitions == []
    snap = _snapshot(db)
    assert snap.failed() == ("traffic",)
    assert snap.traffic == 29
    assert snap.hard_ok and snap.advantage_ok and snap.gate_ok
    assert read_events(db, (LedgerKind.PROMOTION,)) == []


def test_criterion3_traffic_window_is_90d_and_counts_distinct_clusters(tmp_path):
    """判据③口径：只数 90 天窗内的不同簇（prod），窗外的簇与 exam 存款簇都不算。"""
    db = tmp_path / "l.db"
    _positive_ledger(db, traffic=0)             # as_of 的 0 簇
    fill_traffic(db, days_ago(91), n=10)        # 窗外（91 天前）→ 不计
    with KernelLedger(db, clock=FixedClock(AS_OF)) as led:
        for i in range(5):                      # exam origin 的簇 → 不计（§8.4）
            led.append(
                "deposit",
                {"finding": None, "cluster": f"exam{i}", "agent": "e", "role": "auditor",
                 "taxonomy_path": ["injection"], "signal_tier": "S1", "direction": 1,
                 "weight": 0.01, "origin": "exam"},
            )
    fill_traffic(db, days_ago(2), n=30, agent="w")   # 窗内 30 个不同簇（含 c00..c29）
    snap = _snapshot(db)
    assert snap.traffic == 30                   # 去重后恰 30（z@as_of 无簇；w 的 30 簇）
    assert snap.traffic_ok


def test_criterion4_exam_cooldown_blocks_retrigger(tmp_path):
    """证伪④：暗考失败退回 shadow 后 1 天（< COOLDOWN_DAYS）→ 统计再达标也不重触发。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    promo_event(db, days_ago(PARAMS.exam.COOLDOWN_DAYS - 1), "a",
                exam_id="EX-old", frm="candidate", to="shadow")
    transitions = scan(db)
    assert transitions == []
    snap = _snapshot(db)
    assert snap.failed() == ("gate",)
    assert snap.hard_ok and snap.advantage_ok and snap.traffic_ok
    assert len(read_events(db, (LedgerKind.PROMOTION,))) == 1   # 只有既有的失败事件


def test_criterion4_cooldown_expires_after_14d(tmp_path):
    """判据④边界：失败事件恰满 COOLDOWN_DAYS 天 → 冷却结束，可再次触发晋升。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    promo_event(db, days_ago(14), "a",     # §8.2 字面冷却 14 天（spec_literal 钉住）
                exam_id="EX-old", frm="candidate", to="shadow")
    transitions = scan(db)
    assert [t.reason for t in transitions] == ["candidate"]
    assert len(read_events(db, (LedgerKind.PROMOTION,))) == 2


def test_s2s3_exactly_three_passes_hard_gate(tmp_path):
    """硬门槛边界：n_eff=10 且 n_eff_s2s3=3.0 恰达 → 满足（≥ 语义）。"""
    db = tmp_path / "l.db"
    dep(db, AS_OF, agent="a", tier="S3", w=0.75)     # s2s3 各 1.5，共 3.0（精确）
    dep(db, AS_OF, agent="a", tier="S3", w=0.75)
    for _ in range(14):                              # 14×0.25=3.5 → 总量 5.0、n_eff=10.0
        dep(db, AS_OF, agent="a", tier="S1", w=0.25)
    dep(db, AS_OF, agent="b", d=1, w=1.0)
    dep(db, AS_OF, agent="b", d=-1, w=1.0)
    fill_traffic(db, AS_OF)
    transitions = scan(db)
    assert len(transitions) == 1
    assert transitions[0].criteria.n_eff_s2s3 == pytest.approx(3.0)
    assert transitions[0].criteria.hard_ok


# ══ SPEC-5 §6.1 债①：evaluate_candidate_criteria 真只读（口径 (a)）══

def test_evaluate_candidate_criteria_read_only_or_scoped(tmp_path):
    """诊断入口真只读（SPEC-5 §6.1 债①口径 (a)，DECISIONS 2026-10-10）：

    - 已有物理 cells 表：调用前后 ``SELECT * FROM cells`` 行集与全列值逐位
      不变，且 ``conn.total_changes`` 增量为 0（不触发整表重写）；
    - 空 cells 表（账本有事件、从未重写过）：视图走只读重放路径照常评出与
      写路径一致的快照，且不把视图落表（无隐式写副作用）。
    """
    db = tmp_path / "l.db"
    _positive_ledger(db)                    # a 格四判据全满足的正例账本
    params = spec_literal_params()

    with KernelLedger(db) as led:
        agg_cells(led, AS_OF, params=params)          # 正常写路径先落一次表
        cols = ("SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3,"
                " status, activated_at, last_event_seq, updated_at FROM cells"
                " ORDER BY agent_id, role, node")
        before = led.conn.execute(cols).fetchall()
        assert before                                 # 表非空，防空表上空转
        changes0 = led.conn.total_changes
        snap = evaluate_candidate_criteria(
            led, AS_OF, CellKey("a", "auditor", "injection"), params=params)
        changes1 = led.conn.total_changes
        after = led.conn.execute(cols).fetchall()
    assert after == before                            # 行集与全列值逐位不变
    assert changes1 == changes0                       # conn.total_changes 增量为 0
    assert snap.all_ok and snap.failed() == ()        # 评估语义不受只读化影响

    db2 = tmp_path / "fresh.db"
    _positive_ledger(db2)                             # 账本有事件，cells 从未落表
    with KernelLedger(db2) as led:
        changes0 = led.conn.total_changes
        snap2 = evaluate_candidate_criteria(
            led, AS_OF, CellKey("a", "auditor", "injection"), params=params)
        changes1 = led.conn.total_changes
        rows = led.conn.execute("SELECT * FROM cells").fetchall()
    assert snap2 == snap                              # 只读重放 == 写路径视图快照
    assert rows == [] and changes1 == changes0        # 空 cells 原样、零写副作用


# ══ 转换②：candidate → active / 退回 shadow（考试回调驱动）══════

def _stub_exam(passed: bool, exam_id: str = "EX-1", seq_promotion: int | None = None):
    def _run(*, agent_id, role, node, from_status):
        return SimpleNamespace(
            passed=passed, exam_id=exam_id, seq_promotion=seq_promotion, score=1.0
        )

    return _run


def test_exam_pass_activates_cell(tmp_path):
    db = tmp_path / "l.db"
    _positive_ledger(db)
    transitions = scan(db, exam=_stub_exam(True, "EX-P"))
    assert [(t.reason, t.to_status) for t in transitions] == [
        ("candidate", "candidate"), ("exam_pass", "active")]
    promos = read_events(db, (LedgerKind.PROMOTION,))
    assert [(p.payload["from_status"], p.payload["to_status"]) for p in promos] == [
        ("shadow", "candidate"), ("candidate", "active")]
    assert promos[1].payload["exam_id"] == "EX-P"
    v = view_of(db)
    cell = v.get("a", "auditor", "injection")
    assert cell.status == "active" and cell.activated_at == AS_OF


def test_exam_fail_returns_to_shadow_and_cooldowns(tmp_path):
    db = tmp_path / "l.db"
    _positive_ledger(db)
    transitions = scan(db, exam=_stub_exam(False, "EX-F"))
    assert [(t.reason, t.to_status) for t in transitions] == [
        ("candidate", "candidate"), ("exam_fail", "shadow")]
    fail_events = [
        e for e in read_events(db, (LedgerKind.PROMOTION,))
        if e.payload["to_status"] == "shadow"
    ]
    assert len(fail_events) == 1 and fail_events[0].payload["exam_id"] == "EX-F"
    assert view_of(db).get("a", "auditor", "injection").status == "shadow"

    # 冷却期内（同一时点）重扫：统计仍达标也被判据④拦住 → 零新事件（幂等）
    transitions2 = scan(db, exam=_stub_exam(True, "EX-AGAIN"))
    assert transitions2 == []
    assert len(read_events(db, (LedgerKind.PROMOTION,))) == 2


def test_exam_callback_own_promotion_seq_not_duplicated(tmp_path):
    """回调自带 seq_promotion（如 simulator.ExamCenter 已落事件）→ 内核不重复落。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    transitions = scan(db, exam=_stub_exam(True, "EX-C", seq_promotion=12345))
    assert transitions[1].seq == 12345
    assert len(read_events(db, (LedgerKind.PROMOTION,))) == 1   # 仅 shadow→candidate 一条


def test_exam_callback_none_keeps_candidate(tmp_path):
    """回调返回 None（无卷/回调内冷却拒绝）→ 保持 candidate，无多余事件。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    transitions = scan(db, exam=lambda **kw: None)
    assert [(t.reason, t.to_status) for t in transitions] == [("candidate", "candidate")]
    assert view_of(db).get("a", "auditor", "injection").status == "candidate"


class StubAgent:
    """最小 agent 桩：capability 查表（与 tests/test_sim_exam.py 同款）。"""

    def __init__(self, agent_id, q_by_node):
        self.agent_id = agent_id
        self.role = Role.AUDITOR
        self.q_by_node = q_by_node

    def capability(self, family, type_=None, *, t=0, hidden=None):
        return self.q_by_node[family if type_ is None else f"{family}/{type_}"]


class StubPool:
    hidden_params = WorldHiddenParams()

    def __init__(self, *agents):
        self._by_id = {a.agent_id: a for a in agents}

    def get(self, agent_id):
        return self._by_id[agent_id]


@pytest.mark.parametrize("q,should_pass", [(1.0, True), (0.0, False)])
def test_real_exam_center_integration(tmp_path, q, should_pass):
    """SPEC-4 §0 集成：真实 simulator.ExamCenter 作回调，shadow→candidate→暗考收官。"""
    db = tmp_path / "l.db"
    _positive_ledger(db)
    with _open(db) as led:
        center = ExamCenter(
            StubPool(StubAgent("a", {"injection": q})),
            led,
            seed=7,
            params=PARAMS,
            config=ExamWorldParams(p_clip=(0.0, 1.0)),
        )

        def adapter(**kw):
            return center.run_promotion_exam(
                kw["agent_id"], kw["role"], kw["node"], day=0,
                from_status=CellStatus(kw["from_status"]),
            )

        transitions = promote_scan(led, AS_OF, exam=adapter,
                                   params=spec_literal_params())
        reasons = [(t.reason, t.to_status) for t in transitions]
        events = read_events(db)

    exam_results = [e for e in events if e.kind is LedgerKind.EXAM_RESULT]
    assert len(exam_results) == 1
    assert exam_results[0].payload["correct"] is should_pass
    assert exam_results[0].payload["purpose"] == ExamPurpose.PROMOTION.value
    promos = [e for e in events if e.kind is LedgerKind.PROMOTION]
    assert len(promos) == 2                              # candidate + 暗考收官，无重复
    if should_pass:
        assert reasons == [("candidate", "candidate"), ("exam_pass", "active")]
        assert promos[1].payload["to_status"] == "active"
    else:
        assert reasons == [("candidate", "candidate"), ("exam_fail", "shadow")]
        assert promos[1].payload["to_status"] == "shadow"


# ══ ac_muz07uljb4f70nxa：衰退 / 休眠 / 重激活 ═══════════════════

def _active_specialist(db, agent, node, *, promo_days=100):
    """活跃专员格：activation 事件 + 近窗正存款由各用例自铺。"""
    promo_event(db, days_ago(promo_days), agent, node, exam_id="E-seed",
                frm="candidate", to="active")


def test_decay_two_consecutive_windows_extinguishes(tmp_path):
    """连续 2 窗 UB < LOO 基线 → 落 demotion(decay)，回 shadow（激活史保留）。"""
    db = tmp_path / "l.db"
    _active_specialist(db, "sp", "crypto")
    dep(db, days_ago(15), agent="sp", path=("crypto",), d=-1)   # 窗0（31~0 天前）
    dep(db, days_ago(16), agent="sp", path=("crypto",), d=-1)
    dep(db, days_ago(45), agent="sp", path=("crypto",), d=-1)   # 窗1（60~31 天前）
    dep(db, days_ago(46), agent="sp", path=("crypto",), d=-1)
    dep(db, days_ago(20), agent="bp", path=("crypto",), d=1)    # 基线：同窗全正
    dep(db, days_ago(22), agent="bp", path=("crypto",), d=1)
    dep(db, days_ago(50), agent="bp", path=("crypto",), d=1)
    dep(db, days_ago(52), agent="bp", path=("crypto",), d=1)

    # 窗口力学用手算日龄（窗0=31~0 天前 / 窗1=60~31 天前）显式钉 W=30 天：
    # 定稿缺省（7 天）下的在野行为由仿真器实验矩阵与调参轮覆盖。
    transitions = scan(db, window_days=30.0)
    assert [(t.from_status, t.to_status, t.reason, t.kind) for t in transitions] == [
        ("active", "shadow", "decay", "demotion")]
    demotes = read_events(db, (LedgerKind.DEMOTION,))
    assert len(demotes) == 1 and demotes[0].payload["reason"] == "decay"
    cell = view_of(db).get("sp", "auditor", "crypto")
    assert cell.status == "shadow"
    assert cell.activated_at == days_ago(100)    # 账本重放：激活史保留


def test_decay_single_bad_window_does_not_extinguish(tmp_path):
    """单窗跌破、另一窗健康 → 不连续，不熄灭（两个方向各验一次）。"""
    for bad_first in (True, False):
        db = tmp_path / f"l{bad_first}.db"
        _active_specialist(db, "sp", "crypto")
        if bad_first:                            # 窗0 跌、窗1 好
            dep(db, days_ago(15), agent="sp", path=("crypto",), d=-1)
            dep(db, days_ago(16), agent="sp", path=("crypto",), d=-1)
            dep(db, days_ago(45), agent="sp", path=("crypto",), d=1)
            dep(db, days_ago(46), agent="sp", path=("crypto",), d=1)
        else:                                    # 窗1 跌、窗0 好
            dep(db, days_ago(15), agent="sp", path=("crypto",), d=1)
            dep(db, days_ago(16), agent="sp", path=("crypto",), d=1)
            dep(db, days_ago(45), agent="sp", path=("crypto",), d=-1)
            dep(db, days_ago(46), agent="sp", path=("crypto",), d=-1)
        dep(db, days_ago(20), agent="bp", path=("crypto",), d=1)
        dep(db, days_ago(50), agent="bp", path=("crypto",), d=1)

        transitions = scan(db, window_days=30.0)   # 窗0/窗1 手算口径，见上测注释
        assert transitions == []
        assert view_of(db).get("sp", "auditor", "crypto").status == "active"
        assert read_events(db, (LedgerKind.DEMOTION,)) == []


def test_decay_window_must_lie_within_activation_tenure(tmp_path):
    """刚激活（< 2 窗任期）的格子没有"在任衰退"证据 → 不熄灭（压误熄灭率）。"""
    db = tmp_path / "l.db"
    _active_specialist(db, "sp", "crypto", promo_days=10)    # 10 天前才激活
    dep(db, days_ago(5), agent="sp", path=("crypto",), d=-1)
    dep(db, days_ago(6), agent="sp", path=("crypto",), d=-1)
    dep(db, days_ago(20), agent="bp", path=("crypto",), d=1)
    transitions = scan(db, window_days=30.0)    # 窗0 整段晚于 activation 的口径
    assert transitions == []                    # 窗1 整段早于 activation → 不算


def test_dormant_after_60d_without_node_tasks(tmp_path):
    """60 天无该类型任务 → dormant（历史账保留）；59/60 天边界不休眠。"""
    for idle_days, expect_dormant in [(90, True), (60, False), (59, False)]:
        db = tmp_path / f"l{idle_days}.db"
        _active_specialist(db, "dz", "authz")
        dep(db, days_ago(idle_days), agent="dz", path=("authz",), d=1)

        transitions = scan(db)
        demotes = read_events(db, (LedgerKind.DEMOTION,))
        if expect_dormant:
            assert [(t.to_status, t.reason) for t in transitions] == [("dormant", "dormant")]
            assert len(demotes) == 1 and demotes[0].payload["reason"] == "dormant"
            cell = view_of(db).get("dz", "auditor", "authz")
            assert cell.status == "dormant"
            assert cell.pos_w > 0               # 历史账保留（格仍在，只换状态）
        else:
            assert transitions == [] and demotes == []
            assert view_of(db).get("dz", "auditor", "authz").status == "active"


def test_dormancy_traffic_is_node_wide_across_agents(tmp_path):
    """活动钟是节点级（全组织）：他人窗内有生产存款 → 该格不因自身清闲而休眠。"""
    db = tmp_path / "l.db"
    _active_specialist(db, "dz", "authz")
    dep(db, days_ago(90), agent="dz", path=("authz",), d=1)      # 自己很闲
    dep(db, days_ago(5), agent="busy", path=("authz",), d=1)     # 但类型仍活跃
    transitions = scan(db)
    assert transitions == []


def _dormant_cell(db, agent, node, deposits):
    """休眠格：activation → 生产 → 60 天无任务休眠。"""
    dep(db, days_ago(95), agent=agent, path=(node,), d=1)
    promo_event(db, days_ago(94), agent, node, exam_id="E1", frm="candidate", to="active")
    demote_event(db, days_ago(80), agent, node, reason="dormant")
    for ts, w in deposits:
        dep(db, ts, agent=agent, path=(node,), w=w)


def test_dormant_reactivates_only_when_n_eff_ge_5_and_traffic_alive(tmp_path):
    """重激活三象限：n_eff≥5 + 流量回潮 → active；n_eff<5 → 不动；节点仍死 → 不动。"""
    # ① 4×S3@10d → n_eff=8·2^(−1/3)≈6.35 ≥ 5，节点 10 天前有生产存款 → 重激活
    db1 = tmp_path / "a.db"
    _dormant_cell(db1, "rz", "config_dep", [(days_ago(10), 1.0)] * 4)
    transitions1 = scan(db1)
    assert [(t.from_status, t.to_status, t.reason, t.kind) for t in transitions1] == [
        ("dormant", "active", "reactivate", "promotion")]
    promo = read_events(db1, (LedgerKind.PROMOTION,))[-1]
    assert promo.payload["from_status"] == "dormant"
    assert promo.payload["to_status"] == "active"
    assert promo.payload["exam_id"].startswith("reactivate:")
    assert view_of(db1).get("rz", "auditor", "config_dep").status == "active"

    # ② 2×S3@10d → n_eff≈3.17 < 5：历史再厚也不走快速通道
    db2 = tmp_path / "b.db"
    _dormant_cell(db2, "rz", "config_dep", [(days_ago(10), 1.0)] * 2)
    transitions2 = scan(db2)
    assert transitions2 == []

    # ③ 16.0 单笔@70d → n_eff≈6.35 ≥ 5 但节点 70 天无生产任务（未回潮）→ 不动
    db3 = tmp_path / "c.db"
    _dormant_cell(db3, "rz", "config_dep", [(days_ago(70), 16.0)])
    transitions3 = scan(db3)
    assert transitions3 == []
    assert view_of(db3).get("rz", "auditor", "config_dep").status == "dormant"


# ══ ac_muz07uljm7q2n3s2：幂等 ═══════════════════════════════════

def _rich_ledger(db):
    """五个节点五种命运：晋升 / 保持 / 衰退 / 休眠 / 重激活，一扫俱全。"""
    _positive_ledger(db)                                              # P: shadow→candidate
    _active_specialist(db, "h", "deser")                              # H: 健康 active
    dep(db, days_ago(10), agent="h", path=("deser",), d=1)
    dep(db, days_ago(12), agent="h2", path=("deser",), d=1)           # 基线同窗正存款
    _active_specialist(db, "sp", "crypto")                            # D: 连续 2 窗衰退
    for ts in (days_ago(15), days_ago(16), days_ago(45), days_ago(46)):
        dep(db, ts, agent="sp", path=("crypto",), d=-1)
    # 基线代理每窗两笔（单窗总质量须 ≥ ε=0.5，否则地板击穿、基线无定义）
    dep(db, days_ago(20), agent="bp", path=("crypto",), d=1)
    dep(db, days_ago(21), agent="bp", path=("crypto",), d=1)
    dep(db, days_ago(50), agent="bp", path=("crypto",), d=1)
    dep(db, days_ago(51), agent="bp", path=("crypto",), d=1)
    _active_specialist(db, "dz", "authz")                             # O: 90 天无任务
    dep(db, days_ago(90), agent="dz", path=("authz",), d=1)
    _dormant_cell(db, "rz", "config_dep", [(days_ago(10), 1.0)] * 4)  # R: 可重激活


def test_promote_scan_idempotent_same_ledger_same_result(tmp_path):
    db = tmp_path / "l.db"
    _rich_ledger(db)

    # sp 的两窗衰退日龄按 W=30 手算（见 test_decay_two_consecutive_windows 注释）
    t1 = scan(db, window_days=30.0)
    reasons1 = [((t.agent_id, t.role, t.node), t.from_status, t.to_status, t.reason) for t in t1]
    # 按格子字典序推进：a→candidate、dz→dormant、rz→reactivate、sp→decay
    assert reasons1 == [
        (("a", "auditor", "injection"), "shadow", "candidate", "candidate"),
        (("dz", "auditor", "authz"), "active", "dormant", "dormant"),
        (("rz", "auditor", "config_dep"), "dormant", "active", "reactivate"),
        (("sp", "auditor", "crypto"), "active", "shadow", "decay"),
    ]
    snap1_ledger, snap1_cells = ledger_rows(db), cells_rows(db)
    assert len(snap1_ledger) == 62     # 种子 58（37+3+9+2+7）+ 本扫 4 条流转事件
    statuses = {r[7] for r in snap1_cells}
    assert {"candidate", "active", "shadow", "dormant"} <= statuses

    # 同一账本第二次扫描：零新事件、cells 逐字节不变
    t2 = scan(db, window_days=30.0)
    assert t2 == []
    assert ledger_rows(db) == snap1_ledger
    assert cells_rows(db) == snap1_cells

    # 第三次仍不变（防"隔次振荡"）
    t3 = scan(db, window_days=30.0)
    assert t3 == []
    assert ledger_rows(db) == snap1_ledger
    assert cells_rows(db) == snap1_cells


def test_scan_with_no_eligible_cells_writes_nothing(tmp_path):
    """空账本 / 无资格账本：扫描是纯读，零事件、cells 为空。"""
    db = tmp_path / "empty.db"
    transitions = scan(db)
    assert transitions == [] and read_events(db) == [] and cells_rows(db) == []

    db2 = tmp_path / "quiet.db"
    dep(db2, days_ago(3), agent="solo", w=0.1)   # 一格影子账，远不够门槛
    transitions2 = scan(db2)
    assert transitions2 == []
    assert len(read_events(db2)) == 1            # 只有原存款，无新事件
    assert view_of(db2).get("solo", "auditor", "injection").status == "shadow"


def test_exam_deposit_feeds_cells_but_not_traffic_or_dormancy_clock(tmp_path):
    """§8.4：考试存款入格（n_eff）但不喂判据③流量与休眠活动钟。"""
    db = tmp_path / "l.db"
    _active_specialist(db, "dz", "authz")
    with KernelLedger(db, clock=FixedClock(days_ago(1))) as led:
        led.append(
            "deposit",
            {"finding": None, "cluster": "exam-only", "agent": "dz", "role": "auditor",
             "taxonomy_path": ["authz"], "signal_tier": "S1", "direction": 1,
             "weight": 5.0, "origin": "exam"},
        )
    dep(db, days_ago(90), agent="dz", path=("authz",), d=1)
    transitions = scan(db)
    # exam 存款确实入格（n_eff 涨破 5），但活动钟只认 prod（90 天前）→ 仍休眠
    assert view_of(db).get("dz", "auditor", "authz").n_eff > 5
    assert [(t.to_status, t.reason) for t in transitions] == [("dormant", "dormant")]
