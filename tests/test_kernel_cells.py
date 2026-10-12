"""kernel.cells 测试：挥发（30d/15d 折半）、n_eff 恒等式与当量增量、挥发地板、
层级收缩黄金数值、LOO 基线剔自身、status 重放与 cells 表整表重建。

对应验收：
- ac_muz07ulj9xqynze1  恰 30d/15d 折半、双倍龄期折 1/4、exam 走 15d 档
- ac_muz07uljm4465dwk  n_eff ≡ 2×(pos_w+neg_w)（随机账本）；S1/S2/S3 增量 0.1/1/2 当量
- ac_muz07uljd1vjjbyx  总量 < ε 的格子 C* 等于父层 C*
- ac_muz07ulja9oma87c  收缩公式黄金数值（手算对照）+ LOO 基线剔自身
"""

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import pytest

from qswarm.contracts import load_params
from qswarm.kernel import (
    ROOT_PRIOR,
    CellKey,
    FixedClock,
    KernelLedger,
    agg_cells,
    decay_factor,
    node_prefixes,
)

AS_OF = "2030-06-30T00:00:00Z"           # 老化用例的观测时点
T0 = "2030-01-01T00:00:00Z"              # "新存款"用例的事件时点（age=0）


# ── 工具：按指定时点落事件（每次重开库注入 FixedClock，seq 跨会话延续）──

def _deposit(
    db: Path,
    ts: str,
    *,
    agent: str = "a",
    role: str = "auditor",
    path: tuple[str, ...] = ("injection",),
    tier: str = "S3",
    direction: int = 1,
    weight: float = 1.0,
    origin: str = "prod",
) -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "deposit",
            {
                "finding": None,
                "cluster": None,
                "agent": agent,
                "role": role,
                "taxonomy_path": list(path),
                "signal_tier": tier,
                "direction": direction,
                "weight": weight,
                "origin": origin,
            },
        )


def _promote(db: Path, ts: str, agent: str, role: str, node: str, to: str) -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append(
            "promotion",
            {
                "agent": agent, "role": role, "node": node,
                "exam_id": "ER-1", "from_status": "shadow", "to_status": to,
            },
        )


def _demote(db: Path, ts: str, agent: str, role: str, node: str, reason: str) -> None:
    with KernelLedger(db, clock=FixedClock(ts)) as led:
        led.append("demotion", {"agent": agent, "role": role, "node": node, "reason": reason})


def _view(db: Path, as_of: str = AS_OF, **kw):
    with KernelLedger(db) as led:
        return agg_cells(led, as_of, **kw)


def _rows(db: Path) -> list[tuple]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


# ── 挥发（验收 ac_muz07ulj9xqynze1）────────────────────────────

def test_decay_prod_exactly_30d_halves_and_60d_quarters(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-05-31T00:00:00Z", agent="p30", weight=1.0)   # 恰 30d 前
    _deposit(db, "2030-05-01T00:00:00Z", agent="p60", weight=1.0)   # 恰 60d 前
    v = _view(db)
    c30 = v.get("p30", "auditor", "injection")
    c60 = v.get("p60", "auditor", "injection")
    assert c30.pos_w == 0.5                  # 2^(−30d/30d)，IEEE 双精度逐位精确
    assert c60.pos_w == 0.25                 # 双倍龄期 → 2^(−2)
    assert c30.n_eff == 1.0                  # ≡ 2×(0.5+0)
    assert c60.n_eff == 0.5


def test_decay_fresh_deposit_untouched(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, AS_OF, weight=1.0)          # age = 0 → 因子恰为 1
    v = _view(db)
    cell = v.get("a", "auditor", "injection")
    assert cell.pos_w == 1.0
    assert cell.n_eff == 2.0
    assert cell.n_eff_s2s3 == 2.0            # S3 当量


def test_decay_exam_walks_15d_tier(tmp_path: Path):
    db = tmp_path / "ledger.db"
    # exam：15d 折半（第一半衰期）、30d 折 1/4（双倍龄期）
    _deposit(db, "2030-06-15T00:00:00Z", agent="e15", weight=1.0, origin="exam")
    _deposit(db, "2030-05-31T00:00:00Z", agent="e30", weight=1.0, origin="exam")
    # 对照：同龄期 prod 存款（15d 只折 2^(−0.5)）
    _deposit(db, "2030-06-15T00:00:00Z", agent="p15", weight=1.0, origin="prod")
    v = _view(db)
    assert v.get("e15", "auditor", "injection").pos_w == 0.5
    assert v.get("e30", "auditor", "injection").pos_w == 0.25
    assert v.get("p15", "auditor", "injection").pos_w == pytest.approx(2.0**-0.5)
    # 同龄期 exam < prod ⇒ exam 确走 15d 档而非 30d 档
    assert v.get("e15", "auditor", "injection").pos_w < v.get("p15", "auditor", "injection").pos_w


def test_decay_applies_to_negative_and_s2s3_weights(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-05-31T00:00:00Z", direction=-1, weight=1.0)          # 负存款折半
    _deposit(db, "2030-05-31T00:00:00Z", agent="b", tier="S2", weight=0.5)  # S2 s2s3 同步挥发
    v = _view(db)
    a = v.get("a", "auditor", "injection")
    b = v.get("b", "auditor", "injection")
    assert (a.pos_w, a.neg_w, a.n_eff) == (0.0, 0.5, 1.0)
    assert b.n_eff_s2s3 == pytest.approx(2 * 0.25)   # 2×(0.5×0.5)


def test_decay_sums_over_deposits_and_excludes_future(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-05-31T00:00:00Z", weight=1.0)   # as_of 06-30：恰 30d → 0.5
    _deposit(db, "2030-05-01T00:00:00Z", weight=1.0)   # 60d → 0.25
    _deposit(db, "2030-07-01T00:00:00Z", weight=9.0)   # 晚于 as_of：整笔剔除
    v = _view(db)
    cell = v.get("a", "auditor", "injection")
    assert cell.pos_w == pytest.approx(0.75)
    assert 9.0 not in (cell.pos_w, cell.neg_w)
    with KernelLedger(db) as led:
        late = agg_cells(led, "2030-07-02T00:00:00Z")  # as_of 后移：事件按龄期重折
    expected = 2.0 ** (-32 / 30) + 2.0 ** (-62 / 30) + 9.0 * 2.0 ** (-1 / 30)
    assert late.get("a", "auditor", "injection").pos_w == pytest.approx(expected)
    assert late.get("a", "auditor", "injection").pos_w > 9.0  # 此前的"未来"存款已计入


def test_decay_factor_unit_and_node_prefixes():
    p = load_params().decay
    assert decay_factor("prod", 0.0, decay=p) == 1.0
    assert decay_factor("prod", 30 * 86400.0, decay=p) == 0.5
    assert decay_factor("prod", 60 * 86400.0, decay=p) == 0.25
    assert decay_factor("exam", 15 * 86400.0, decay=p) == 0.5
    assert decay_factor("exam", 30 * 86400.0, decay=p) == 0.25
    assert node_prefixes(["injection", "sqli"]) == ["injection", "injection/sqli"]
    assert node_prefixes(["injection"]) == ["injection"]
    assert node_prefixes([]) == []


# ── n_eff 恒等式与当量增量（验收 ac_muz07uljm4465dwk）──────────

def test_tier_equivalent_increments_s1_s2_s3(tmp_path: Path):
    db = tmp_path / "ledger.db"
    # 规范权重（§6.1：S1=0.05/S2=0.5/S3=1.0），各落一个家族节点隔离增量
    _deposit(db, T0, agent="x", path=("injection",), tier="S1", weight=0.05)
    _deposit(db, T0, agent="x", path=("deser",), tier="S2", weight=0.5)
    _deposit(db, T0, agent="x", path=("crypto",), tier="S3", weight=1.0)
    _deposit(db, T0, agent="y", path=("authz",), tier="S2", direction=-1, weight=0.5)
    v = _view(db, as_of=T0)
    s1 = v.get("x", "auditor", "injection")
    s2 = v.get("x", "auditor", "deser")
    s3 = v.get("x", "auditor", "crypto")
    neg = v.get("y", "auditor", "authz")
    # S1/S2/S3 存款增量 = 0.1/1/2 当量（n_eff），S2/S3 同步计入 s2s3，S1 记 0
    assert s1.n_eff == pytest.approx(0.1)
    assert s1.n_eff_s2s3 == 0.0
    assert s2.n_eff == pytest.approx(1.0)
    assert s2.n_eff_s2s3 == pytest.approx(1.0)
    assert s3.n_eff == pytest.approx(2.0)
    assert s3.n_eff_s2s3 == pytest.approx(2.0)
    # 负向存款仍是有效样本：s2s3 照计，方向只进 neg_w
    assert (neg.pos_w, neg.neg_w, neg.n_eff, neg.n_eff_s2s3) == (0.0, 0.5, 1.0, 1.0)


def test_n_eff_identity_on_random_ledger(tmp_path: Path):
    """随机账本：全部格子满足 n_eff ≡ 2×(pos+neg)、s2s3 ≡ 2×(S2/S3 质量和)，且与独立 oracle 一致。"""
    db = tmp_path / "ledger.db"
    rng = random.Random(20260929)
    agents = ["a@v1", "b@v1", "c@v1", "d@v1"]
    roles = ["auditor", "scout", "reviewer"]
    fams = ["injection", "deser", "crypto", "authz", "config_dep", "mem_safety"]
    as_of_dt = datetime(2030, 6, 30, tzinfo=timezone.utc)

    specs = []
    for _ in range(120):
        age_s = rng.uniform(0.0, 90.0) * 86400.0
        ts_dt = as_of_dt - timedelta(seconds=age_s)
        specs.append(
            {
                "ts": ts_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
                "agent": rng.choice(agents),
                "role": rng.choice(roles),
                "path": (fam := rng.choice(fams),)
                + ((f"t{rng.randrange(3)}",) if rng.random() < 0.5 else ()),
                "tier": rng.choice(["S1", "S2", "S3"]),
                "direction": rng.choice([-1, 1]),
                "weight": round(rng.uniform(0.05, 1.5), 3),
                "origin": "exam" if rng.random() < 0.2 else "prod",
            }
        )
    for s in specs:
        _deposit(db, **s)

    # 独立 oracle：按同一定义重算每格期望值
    params = load_params()
    expected: dict[tuple[str, str, str], list[float]] = {}
    for s in specs:
        age = (as_of_dt - datetime.fromisoformat(s["ts"])).total_seconds()
        half = params.decay.T_HALF_DAYS_EXAM if s["origin"] == "exam" else params.decay.T_HALF_DAYS
        w_eff = s["weight"] * 2.0 ** (-age / (half * 86400.0))
        s2s3 = 2.0 * w_eff if (s["tier"] in ("S2", "S3") and s["direction"] != 0) else 0.0
        nodes = ["/".join(s["path"][: i + 1]) for i in range(len(s["path"]))]
        for nd in nodes:
            key = (s["agent"], s["role"], nd)
            acc = expected.setdefault(key, [0.0, 0.0, 0.0])
            signed = w_eff * s["direction"]
            if signed > 0:
                acc[0] += signed
            elif signed < 0:
                acc[1] -= signed
            acc[2] += s2s3

    v = _view(db, as_of=AS_OF)
    assert set(v.cells) == {CellKey(*k) for k in expected}
    for key, cell in v.cells.items():
        e_pos, e_neg, e_s2s3 = expected[(cell.agent_id, cell.role, cell.node)]
        assert cell.pos_w == pytest.approx(e_pos, rel=1e-12, abs=1e-15)
        assert cell.neg_w == pytest.approx(e_neg, rel=1e-12, abs=1e-15)
        assert cell.n_eff_s2s3 == pytest.approx(e_s2s3, rel=1e-12, abs=1e-15)
        assert cell.n_eff == pytest.approx(2.0 * (cell.pos_w + cell.neg_w))   # 恒等式
        assert cell.n_eff_s2s3 <= cell.n_eff + 1e-9
    # cells 表与视图一致（写表语义）
    rows = _rows(db)
    assert len(rows) == len(v.cells)
    by_key = {(r[0], r[1], r[2]): r for r in rows}
    for key, cell in v.cells.items():
        r = by_key[(cell.agent_id, cell.role, cell.node)]
        assert (r[3], r[4], r[5], r[6]) == (cell.pos_w, cell.neg_w, cell.n_eff, cell.n_eff_s2s3)


# ── 挥发地板（验收 ac_muz07uljd1vjjbyx）───────────────────────

def test_floor_family_cell_takes_parent_c_star(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, T0, path=("crypto",), weight=2.0)        # 角色层健康历史（同批存款）
    _deposit(db, T0, path=("injection",), weight=0.3)     # 家族格总量 0.3 < ε=0.5
    v = _view(db, as_of=T0)
    assert v.get("a", "auditor", "injection").total_w == pytest.approx(0.3)
    assert v.c_raw("a", "auditor", "injection") is None   # 地板：C_raw 无定义
    assert v.c_star("a", "auditor", "injection") == v.c_star("a", "auditor", None)
    # 对照组：健康格子 C* 严格高于收缩后的父层值 ⇒ 上述相等确由地板造成
    assert v.c_raw("a", "auditor", "crypto") == pytest.approx(1.0)
    assert v.c_star("a", "auditor", "crypto") != pytest.approx(v.c_star("a", "auditor", None))


def test_floor_type_cell_takes_family_c_star(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, T0, path=("injection",), weight=2.0)          # 家族格健康
    _deposit(db, T0, path=("injection", "xss"), weight=0.2)    # 类型格 0.2 < ε
    v = _view(db, as_of=T0)
    assert v.c_raw("a", "auditor", "injection/xss") is None
    assert v.c_raw("a", "auditor", "injection") == pytest.approx(1.0)
    assert v.c_star("a", "auditor", "injection/xss") == v.c_star("a", "auditor", "injection")


def test_floor_cold_start_never_deadlocks(tmp_path: Path):
    db = tmp_path / "ledger.db"
    v = _view(db)  # 空账本
    assert v.cells == {}
    assert v.c_raw("nobody", "scout", "unknown/node") is None
    assert v.c_star("nobody", "scout", "unknown/node") == ROOT_PRIOR   # 无存款 → 中性先验
    _deposit(db, T0, agent="only", role="auditor", path=("crypto",), weight=3.0)
    v2 = _view(db, as_of=T0)
    # 新 agent/新类型：链上层层地板击穿，退到有存款的父层（§5.1 冷启动解）
    assert v2.c_star("stranger", "scout", "injection/sqli") == ROOT_PRIOR
    assert v2.c_star("only", "auditor", "injection") == v2.c_star("only", "auditor", None)


# ── 层级收缩黄金数值（验收 ac_muz07ulja9oma87c）───────────────

def test_shrinkage_golden_values_hand_computed(tmp_path: Path):
    """λ=5、根先验 0.5；四笔存款全部手算对照（Fraction 精确有理数）。

    d1 a/injection        S3 +1 w=1.0
    d2 a/injection/sqli   S3 +1 w=1.0
    d3 a/injection/sqli   S2 −1 w=0.5
    d4 a/crypto           S1 +1 w=0.05
    """
    db = tmp_path / "ledger.db"
    _deposit(db, T0, path=("injection",), tier="S3", direction=1, weight=1.0)
    _deposit(db, T0, path=("injection", "sqli"), tier="S3", direction=1, weight=1.0)
    _deposit(db, T0, path=("injection", "sqli"), tier="S2", direction=-1, weight=0.5)
    _deposit(db, T0, path=("crypto",), tier="S1", direction=1, weight=0.05)
    v = _view(db, as_of=T0)

    # 格子存储值（手算）：pos/neg/n_eff/n_eff_s2s3
    inj = v.get("a", "auditor", "injection")
    sqli = v.get("a", "auditor", "injection/sqli")
    crypto = v.get("a", "auditor", "crypto")
    assert (inj.pos_w, inj.neg_w, inj.n_eff, inj.n_eff_s2s3) == (2.0, 0.5, 5.0, 5.0)
    assert (sqli.pos_w, sqli.neg_w, sqli.n_eff, sqli.n_eff_s2s3) == (1.0, 0.5, 3.0, 3.0)
    assert (crypto.pos_w, crypto.neg_w, crypto.n_eff, crypto.n_eff_s2s3) == (0.05, 0.0, 0.1, 0.0)

    # (agent,role) 层与 (agent) 层（本例只有 auditor，两层同质量）：
    #   total = 2.55, n_eff = 5.1 = 51/10, C_raw = 2.05/2.55 = 41/51
    c_raw = Fraction(41, 51)
    n = Fraction(51, 10)
    lam = Fraction(5)
    c_agent = (n * c_raw + lam * Fraction(1, 2)) / (n + lam)     # = 66/101
    c_role = (n * c_raw + lam * c_agent) / (n + lam)             # = 7441/10201
    c_inj = (5 * Fraction(4, 5) + lam * c_role) / 10             # = 78009/102010
    c_sqli = (3 * Fraction(2, 3) + lam * c_inj) / 8              # = 118813/163216
    assert c_agent == Fraction(66, 101)
    assert c_role == Fraction(7441, 10201)
    assert c_inj == Fraction(78009, 102010)
    assert c_sqli == Fraction(118813, 163216)

    assert v.c_star("a", "auditor") == pytest.approx(float(c_role), rel=1e-12)
    assert v.c_star("a", "auditor", "injection") == pytest.approx(float(c_inj), rel=1e-12)
    assert v.c_star("a", "auditor", "injection/sqli") == pytest.approx(float(c_sqli), rel=1e-12)
    # 地板对照：crypto 总量 0.05 < ε → C* 直接取父层；收缩方向：type < family（负存款拉低）
    assert v.c_star("a", "auditor", "crypto") == pytest.approx(float(c_role), rel=1e-12)
    assert v.c_raw("a", "auditor", "crypto") is None
    assert v.c_star("a", "auditor", "injection/sqli") < v.c_star("a", "auditor", "injection")


# ── LOO 基线剔自身（验收 ac_muz07ulja9oma87c）─────────────────

def test_loo_baseline_excludes_self(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, T0, agent="a", path=("injection",), weight=0.8)
    _deposit(db, T0, agent="a", path=("injection",), weight=0.4)
    _deposit(db, T0, agent="a", path=("injection",), direction=-1, weight=0.2)
    _deposit(db, T0, agent="b", path=("injection",), weight=0.4)
    _deposit(db, T0, agent="b", path=("injection",), direction=-1, weight=1.2)
    _deposit(db, T0, agent="b", path=("injection", "sqli"), weight=0.3)  # 类型前缀也入家族格
    _deposit(db, T0, agent="s", role="scout", path=("injection",), weight=5.0)  # 异角色
    v = _view(db, as_of=T0)

    base_a = v.agg_baseline("auditor", "injection", exclude_agent="a")
    assert base_a.exclude_agent == "a"
    assert base_a.pos_w == pytest.approx(0.7)            # b 的 0.4 + sqli 前缀 0.3
    assert base_a.neg_w == pytest.approx(1.2)
    assert base_a.n_eff == pytest.approx(2 * 1.9)
    assert base_a.n_eff_s2s3 == pytest.approx(2 * 1.9)   # 全 S3
    assert base_a.deposits == 3
    assert v.baseline_mean("auditor", "injection", exclude_agent="a") == pytest.approx(0.7 / 1.9)

    base_b = v.agg_baseline("auditor", "injection", exclude_agent="b")
    assert (base_b.pos_w, base_b.neg_w) == (pytest.approx(1.2), pytest.approx(0.2))
    assert v.baseline_mean("auditor", "injection", exclude_agent="b") == pytest.approx(6.0 / 7.0)

    # 剔除不存在者 = 全体（a+b，仍不含 scout；sqli 存款按前缀也入家族索引）
    base_all = v.agg_baseline("auditor", "injection", exclude_agent="nobody@v1")
    assert (base_all.pos_w, base_all.neg_w) == (pytest.approx(1.9), pytest.approx(1.4))
    assert base_all.deposits == 6

    # 地板：基线总量 < ε → mean 无定义
    assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") is None  # 仅 b 的 0.3
    assert v.baseline_mean("auditor", "crypto", exclude_agent="a") is None          # 空


# ── status 重放与 cells 表整表重建 ─────────────────────────────

def test_status_replayed_from_promotion_and_demotion(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-01-01T00:00:00Z", agent="a")                       # a/auditor/injection
    _deposit(db, "2030-01-01T00:00:00Z", agent="b", path=("deser",))
    _promote(db, "2030-02-01T00:00:00Z", "a", "auditor", "injection", "candidate")
    _promote(db, "2030-03-01T00:00:00Z", "a", "auditor", "injection", "active")
    _promote(db, "2030-03-01T00:00:00Z", "b", "auditor", "deser", "active")
    _demote(db, "2030-04-01T00:00:00Z", "b", "auditor", "deser", "dormant")
    _promote(db, "2030-03-01T00:00:00Z", "ghost", "auditor", "crypto", "active")  # 无存款：不建格

    early = _view(db, as_of="2030-01-15T00:00:00Z")   # 时点早于晋升事件
    assert early.get("a", "auditor", "injection").status == "shadow"

    v = _view(db)
    a = v.get("a", "auditor", "injection")
    b = v.get("b", "auditor", "deser")
    assert (a.status, a.activated_at) == ("active", "2030-03-01T00:00:00Z")
    assert (b.status, b.activated_at) == ("dormant", "2030-03-01T00:00:00Z")  # 休眠保留激活史
    assert v.get("ghost", "auditor", "crypto") is None                   # lazy allocation
    rows = {(r[0], r[1], r[2]): (r[7], r[8]) for r in _rows(db)}
    assert rows["a", "auditor", "injection"] == ("active", "2030-03-01T00:00:00Z")


def test_demotion_decay_maps_to_shadow(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-01-01T00:00:00Z", agent="a")
    _promote(db, "2030-02-01T00:00:00Z", "a", "auditor", "injection", "active")
    _demote(db, "2030-03-01T00:00:00Z", "a", "auditor", "injection", "decay")
    v = _view(db)
    cell = v.get("a", "auditor", "injection")
    assert cell.status == "shadow"
    assert cell.activated_at == "2030-02-01T00:00:00Z"   # 最近一次升 active 的历史保留


def test_rebuild_idempotent_and_incremental(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, "2030-01-01T00:00:00Z", weight=1.0)
    v1 = _view(db, as_of="2030-01-01T00:00:00Z")
    rows1 = _rows(db)
    v2 = _view(db, as_of="2030-01-01T00:00:00Z")          # 幂等：重放两次逐位一致
    rows2 = _rows(db)
    assert rows1 == rows2
    assert v2.cells == v1.cells

    _deposit(db, "2030-01-31T00:00:00Z", path=("crypto",), weight=2.0)  # 增量事件
    v3 = _view(db, as_of="2030-01-31T00:00:00Z")
    rows3 = _rows(db)
    assert len(rows3) == len(rows1) + 1
    assert v3.get("a", "auditor", "injection").pos_w == 0.5          # 恰一个半衰期
    assert v3.get("a", "auditor", "crypto").pos_w == 2.0             # 新事件新鲜
    assert v3.get("a", "auditor", "injection").last_event_seq == 1
    assert v3.get("a", "auditor", "crypto").last_event_seq == 2


def test_empty_ledger_writes_no_rows(tmp_path: Path):
    db = tmp_path / "ledger.db"
    v = _view(db)
    assert v.cells == {} and _rows(db) == []
    assert v.params.decay.T_HALF_DAYS == 30 and v.params.decay.T_HALF_DAYS_EXAM == 15
    assert v.params.decay.EPS_FLOOR == 0.5 and v.params.shrink.LAMBDA == 5


def test_params_override_changes_lambda_and_floor(tmp_path: Path):
    db = tmp_path / "ledger.db"
    _deposit(db, T0, path=("crypto",), weight=0.55)       # 默认 ε=0.5：恰好健康；0.6 击穿
    _deposit(db, T0, path=("injection", "sqli"), tier="S2", direction=-1, weight=0.5)
    _deposit(db, T0, path=("injection", "sqli"), tier="S3", direction=1, weight=2.0)
    p = load_params().model_copy(deep=True)
    p.shrink.LAMBDA = 1.0
    p.decay.EPS_FLOOR = 0.6
    v_default = _view(db, as_of=T0)
    v_custom = _view(db, as_of=T0, params=p)
    # 地板 ε 上调 → crypto 总量 0.55 < 0.6 击穿，C_raw 无定义、C* 退父层
    assert v_default.c_raw("a", "auditor", "crypto") == pytest.approx(1.0)
    assert v_custom.c_raw("a", "auditor", "crypto") is None
    assert v_custom.c_star("a", "auditor", "crypto") == v_custom.c_star("a", "auditor", None)
    # λ 收紧 → 格子更信自身 C_raw=0.8（远离被负存款拉低的父链，C* 上移）
    assert v_custom.c_star("a", "auditor", "injection/sqli") > v_default.c_star(
        "a", "auditor", "injection/sqli"
    )
