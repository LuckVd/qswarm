"""kernel.route 测试：自适应 TILT 黄金数值（T_MIN/T_MAX 夹取两端与带内中点）、
K=3 专员上限、探索通道 20% 均匀保底的大样本统计、同种子逐位可复现。

对应验收：
- ac_muz07uljpsgzq9yf  固定 cells 下 tilt 数值 / 专员集合 / 选择结果与手算一致
                       （T_MIN 夹取、T_MAX 夹取两端 + T_BASE=0.70 频带中点精确用例
                       + 带内无夹取用例）
- ac_muz07ulj6ap4p4h5  K=3 上限生效：4 个 active 格子只取 C* 降序前 3
- ac_muz07uljvsu4qhfe  探索通道 20% 均匀保底大样本频率达标（含 floor=0 对照）；
                       同种子两次调用路由序列逐位一致

手算口径：deposit 全部落在 T0（age=0，无挥发）；C* 链 = type→family→(agent,role)
→(agent) 逐级 ``C* = (n_eff·C_raw + λ·C*_parent)/(n_eff+λ)``（λ=5，根先验 0.5，
§5.1）；专员存款只带 (injection, sqli) 路径 ⇒ 四层质量相同，链可手写；
LOO 基线 = 剔 specs[0] 后同 (role, node) 存款池的 C_raw（DECISIONS 2026-10-08）。
"""

import random
from dataclasses import dataclass
from fractions import Fraction

import pytest

from qswarm.contracts import ParamsConfig, DispatchPayload, load_params
from qswarm.kernel import (
    KernelLedger,
    FixedClock,
    RouteDecision,
    active_specialists,
    adaptive_tilt,
    agg_cells,
    route,
)

T0 = "2030-01-01T00:00:00Z"              # 全部事件时点（age=0，无挥发）
_yaml = load_params().model_dump()
_yaml["tilt"]["T_BASE"] = 0.70           # 本文件手算锚定 §5.2 缺省 T_BASE=0.70
PARAMS = ParamsConfig.model_validate(_yaml)   # GAMMA=1.0 [0.50,0.90] floor=0.20


# ── 工具：落账 / 激活 / 任务桩 / rng 桩 ─────────────────────────

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
                "agent": agent, "role": role, "node": node, "exam_id": "ER-ROUTE",
                "from_status": "shadow", "to_status": "active",
            },
        )


def _view(db):
    with KernelLedger(db) as led:
        return agg_cells(led, T0)


@dataclass(frozen=True)
class RouteTask:
    """§5.2 伪码的 task：已带 taxonomy（family[/type]）与 role。"""

    task_id: str
    role: str
    family: str
    type: str | None = None


class ScriptedRng:
    """random.Random 桩：random() 按脚本弹值（耗尽补 0.0），记录调用轨迹。

    route 的 rng 契约：只调 random()、各分支消耗次数固定——用调用轨迹断言。
    """

    def __init__(self, *values: float) -> None:
        self._values = list(values)
        self.calls: list[str] = []

    def random(self) -> float:
        self.calls.append("random")
        return self._values.pop(0) if self._values else 0.0


def _shrink(n: int, c_raw: Fraction, parent: Fraction) -> Fraction:
    """§5.1 收缩一步：(n_eff·C_raw + λ·C*_parent)/(n_eff+λ)，λ=5。"""
    N, lam = Fraction(n), Fraction(5)
    return (N * c_raw + lam * parent) / (N + lam)


def _chain4(n: int, c_raw: Fraction) -> Fraction:
    """专员存款只带 (injection, sqli) ⇒ agent/role/family/type 四层同质量，连收 4 次。"""
    root = Fraction(1, 2)                                   # ROOT_PRIOR
    c_agent = _shrink(n, c_raw, root)
    c_role = _shrink(n, c_raw, c_agent)
    c_family = _shrink(n, c_raw, c_role)
    return _shrink(n, c_raw, c_family)                      # type 层 = 路由读数


# ── 黄金用例：带内中点（无夹取，验收 ac_muz07uljpsgzq9yf）───────

def test_golden_tilt_inside_band_no_clamp(tmp_path):
    """a：3 正(S3) + 1 负(S2,w=.5) ⇒ n=7, C_raw=6/7；b：1 正 ⇒ LOO 基线 1.0。

    手算 C*(a)=35101/41472≈0.846432 ⇒ raw=0.7+(0.846432−1.0)=0.546432 ∈ [0.5,0.9]
    ⇒ tilt=raw（夹取两端都不 binding，T_BASE 起浮带内中点用例）。
    """
    db = tmp_path / "led.db"
    for _ in range(3):
        _dep(db, "a", direction=1)
    _dep(db, "a", tier="S2", direction=-1, weight=0.5)
    _dep(db, "b", direction=1)
    _activate(db, "a")
    v = _view(db)

    c_raw, n = Fraction(6, 7), Fraction(7)
    c_agent = (n * c_raw + 5 * Fraction(1, 2)) / (n + 5)          # = 17/24
    c_role = (n * c_raw + 5 * c_agent) / (n + 5)                  # = 229/288
    c_family = (n * c_raw + 5 * c_role) / (n + 5)                 # = 2873/3456
    c_type = (n * c_raw + 5 * c_family) / (n + 5)                 # = 35101/41472
    assert c_type == Fraction(35101, 41472)
    assert v.c_star("a", "auditor", "injection/sqli") == pytest.approx(float(c_type))
    assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") == pytest.approx(1.0)

    expected_raw = 0.70 + float(c_type) - 1.0                     # GAMMA=1, base=1.0
    assert PARAMS.tilt.T_MIN < expected_raw < PARAMS.tilt.T_MAX   # 带内：无夹取

    dec = route(RouteTask("T-1", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), PARAMS)
    assert dec.raw_tilt == pytest.approx(expected_raw)
    assert dec.tilt == pytest.approx(expected_raw)                # 未被夹取
    assert dec.specs == ("a",) and dec.spec_c_stars[0] == pytest.approx(float(c_type))
    assert dec.channel == "specialist" and dec.reason == "tilt_hit"
    assert dec.assignee == "a"                                    # 无 load ⇒ specs[0]
    # 纯函数对照：adaptive_tilt 同输入同输出
    assert adaptive_tilt(float(c_type), 1.0, tilt=PARAMS.tilt) == (
        pytest.approx(expected_raw), pytest.approx(expected_raw),
    )


# ── 黄金用例：T_MAX 夹取（验收 ac_muz07uljpsgzq9yf）────────────

def test_golden_tilt_clamped_at_t_max(tmp_path):
    """a：4 正 ⇒ C*=56497/57122≈0.989058；b：1正1负 ⇒ 基线 0.5。

    raw = 0.7 + (0.989058 − 0.5) = 1.189058 > T_MAX ⇒ tilt 夹取为 0.90。
    """
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "a", direction=1)
    _dep(db, "b", direction=1)
    _dep(db, "b", direction=-1)
    _activate(db, "a")
    v = _view(db)

    c_type = Fraction(56497, 57122)
    assert v.c_star("a", "auditor", "injection/sqli") == pytest.approx(float(c_type))
    assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") == pytest.approx(0.5)

    expected_raw = 0.70 + float(c_type) - 0.5
    assert expected_raw > PARAMS.tilt.T_MAX

    dec = route(RouteTask("T-2", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), PARAMS)
    assert dec.raw_tilt == pytest.approx(expected_raw)
    assert dec.tilt == pytest.approx(PARAMS.tilt.T_MAX)           # = 0.90，收割上限
    assert dec.tilt != pytest.approx(dec.raw_tilt)
    assert dec.specs == ("a",) and dec.assignee == "a"


# ── 黄金用例：T_MIN 夹取（验收 ac_muz07uljpsgzq9yf）────────────

def test_golden_tilt_clamped_at_t_min(tmp_path):
    """a：1 负 ⇒ C*=625/4802≈0.130156；b：3 正 ⇒ 基线 1.0。

    raw = 0.7 + (0.130156 − 1.0) = −0.169844 < T_MIN ⇒ tilt 夹取为 0.50
    （专员优势崩塌 → 探索最大化，§5.2 自适应语义）。
    """
    db = tmp_path / "led.db"
    _dep(db, "a", direction=-1)
    for _ in range(3):
        _dep(db, "b", direction=1)
    _activate(db, "a")
    v = _view(db)

    c_type = Fraction(625, 4802)
    assert v.c_star("a", "auditor", "injection/sqli") == pytest.approx(float(c_type))
    assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") == pytest.approx(1.0)

    expected_raw = 0.70 + float(c_type) - 1.0
    assert expected_raw < PARAMS.tilt.T_MIN

    dec = route(RouteTask("T-3", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), PARAMS)
    assert dec.raw_tilt == pytest.approx(expected_raw)
    assert dec.tilt == pytest.approx(PARAMS.tilt.T_MIN)           # = 0.50，探索下限
    assert dec.specs == ("a",) and dec.assignee == "a"


# ── 黄金用例：T_BASE 精确中点（验收 ac_muz07uljpsgzq9yf）────────

def test_golden_tilt_at_t_base_midpoint(tmp_path):
    """专员优势边际恰为零 ⇒ tilt 精确等于 T_BASE（带 [T_MIN,T_MAX] 中点，无夹取）。

    a：1正+1负（S3, w=1）⇒ 每层 C_raw=1/2，0.5 是收缩不动点 ⇒ C*(a)=1/2 精确；
    b：1正+1负 ⇒ LOO 基线 = 1/(1+1) = 1/2。于是
    raw = 0.70 + 1.0·(1/2 − 1/2) = 0.70 = T_BASE = (0.50+0.90)/2（频带中点），
    且 T_MIN < raw < T_MAX 严格带内，夹取两端都不 binding。
    """
    db = tmp_path / "led.db"
    _dep(db, "a", direction=1)
    _dep(db, "a", direction=-1)
    _dep(db, "b", direction=1)
    _dep(db, "b", direction=-1)
    _activate(db, "a")
    v = _view(db)

    # C*(a)：1/2 在收缩链上不动点——Fraction 手算逐步对照
    n, c_raw, lam = Fraction(4), Fraction(1, 2), Fraction(5)
    c_agent = (n * c_raw + lam * Fraction(1, 2)) / (n + lam)
    c_role = (n * c_raw + lam * c_agent) / (n + lam)
    c_family = (n * c_raw + lam * c_role) / (n + lam)
    c_type = (n * c_raw + lam * c_family) / (n + lam)
    assert c_agent == c_role == c_family == c_type == Fraction(1, 2)
    assert v.c_star("a", "auditor", "injection/sqli") == pytest.approx(0.5)
    assert v.baseline_mean("auditor", "injection/sqli", exclude_agent="a") == pytest.approx(0.5)

    # 频带中点恒等式：T_BASE 恰为 [T_MIN, T_MAX] 中点（0.70 = (0.50+0.90)/2）
    assert PARAMS.tilt.T_BASE == pytest.approx(
        (PARAMS.tilt.T_MIN + PARAMS.tilt.T_MAX) / 2
    )
    expected_raw = 0.70 + 1.0 * (0.5 - 0.5)                       # = T_BASE 精确
    assert PARAMS.tilt.T_MIN < expected_raw < PARAMS.tilt.T_MAX   # 严格带内，无夹取

    dec = route(RouteTask("T-M", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), PARAMS)
    assert dec.raw_tilt == pytest.approx(float(PARAMS.tilt.T_BASE))
    assert dec.tilt == pytest.approx(float(PARAMS.tilt.T_BASE))   # 精确 T_BASE，无漂移
    assert dec.tilt == pytest.approx((PARAMS.tilt.T_MIN + PARAMS.tilt.T_MAX) / 2)
    assert dec.specs == ("a",) and dec.spec_c_stars == (pytest.approx(0.5),)
    assert dec.channel == "specialist" and dec.reason == "tilt_hit"
    assert dec.assignee == "a"
    # 纯函数对照：adaptive_tilt(0.5, 0.5) ⇒ (T_BASE, T_BASE)
    assert adaptive_tilt(0.5, 0.5, tilt=PARAMS.tilt) == (
        pytest.approx(float(PARAMS.tilt.T_BASE)), pytest.approx(float(PARAMS.tilt.T_BASE)),
    )


# ── K=3 上限（验收 ac_muz07ulj6ap4p4h5）───────────────────────

def test_spec_cap_k3_takes_top3_of_four_active(tmp_path):
    """4 个 active 专员：C* 手算 s1(4正)>s2(2正)>s3(1正)>s4(1正1负=0.5) ⇒ 只取前 3。"""
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "s1", direction=1)
    for _ in range(2):
        _dep(db, "s2", direction=1)
    _dep(db, "s3", direction=1)
    _dep(db, "s4", direction=1)
    _dep(db, "s4", direction=-1)
    for s in ("s1", "s2", "s3", "s4"):
        _activate(db, s)
    v = _view(db)

    # 专员集合本体（不截断）：C* 手算对照 + 严格降序
    all_specs = active_specialists(v, "auditor", "injection/sqli")
    expected_scores = {
        "s1": Fraction(56497, 57122),       # n=8,  C_raw=1
        "s2": Fraction(12497, 13122),       # n=4,  C_raw=1
        "s3": Fraction(4177, 4802),         # n=2,  C_raw=1
        "s4": Fraction(1, 2),               # n=4,  C_raw=1/2：恰为收缩不动点
    }
    assert [a for a, _ in all_specs] == ["s1", "s2", "s3", "s4"]
    for agent, score in all_specs:
        assert score == pytest.approx(float(expected_scores[agent]))

    dec = route(RouteTask("T-4", "auditor", "injection", "sqli"), v, ScriptedRng(0.0), PARAMS)
    assert len(all_specs) == 4                     # 上限截断发生在 route，不在读取
    assert dec.specs == ("s1", "s2", "s3")         # 只取 C* 降序前 3
    assert "s4" not in dec.specs
    assert dec.spec_c_stars == tuple(
        pytest.approx(float(expected_scores[a])) for a in dec.specs
    )
    # tilt 手算：LOO 基线剔 s1 = (s2:2正 + s3:1正 + s4:1正1负) ⇒ 4/5
    expected_raw = 0.70 + float(expected_scores["s1"]) - 0.8
    assert dec.raw_tilt == pytest.approx(expected_raw)
    assert dec.assignee == "s1"


# ── least_loaded 与过载剔除（§5.2 伪码配套语义）────────────────

def test_least_loaded_and_overload_filter(tmp_path):
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "s1", direction=1)
    for _ in range(2):
        _dep(db, "s2", direction=1)
    _dep(db, "s3", direction=1)
    _dep(db, "s4", direction=1)
    _dep(db, "s4", direction=-1)
    for s in ("s1", "s2", "s3", "s4"):
        _activate(db, s)
    v = _view(db)
    task = RouteTask("T-5", "auditor", "injection", "sqli")

    # least_loaded：s2/s3 同载 1 ⇒ C* 高者胜（s2）
    load = {"s1": 3, "s2": 1, "s3": 1, "s4": 0}
    dec = route(task, v, ScriptedRng(0.0), PARAMS, load=load)
    assert dec.assignee == "s2" and dec.channel == "specialist"

    # excl_overloaded：max_load=2 剔 s1(load 5) ⇒ specs=(s2,s3)；
    # tilt 随新 specs[0]=s2 重算：基线剔 s2 = (4+1+1 正,1 负) ⇒ 6/7
    load = {"s1": 5, "s2": 1, "s3": 1, "s4": 9}
    dec = route(task, v, ScriptedRng(0.0), PARAMS, load=load, max_load=2)
    assert dec.specs == ("s2", "s3")
    assert dec.raw_tilt == pytest.approx(0.70 + float(Fraction(12497, 13122)) - Fraction(6, 7))
    assert dec.assignee == "s2"
    # 未命中走探索：池亦剔过载（s1/s4），加权抽中 s3（脚本 0.999999 → 累积权重尾部）
    dec = route(task, v, ScriptedRng(0.999, 0.5, 0.999999), PARAMS, load=load, max_load=2)
    assert dec.channel == "explore" and dec.uniform_pick is False
    assert dec.assignee == "s3"

    # 全员过载：specs 与池皆空 → 空池决策、tilt=None、不消耗任何随机数
    rng = ScriptedRng()
    dec = route(task, v, rng, PARAMS, load={s: 10 for s in "s1 s2 s3 s4".split()}, max_load=10)
    assert dec.assignee is None and dec.reason == "empty_pool" and dec.tilt is None
    assert rng.calls == []


# ── 探索通道 20% 均匀保底（验收 ac_muz07uljvsu4qhfe）───────────

def test_explore_uniform_floor_large_sample_frequency(tmp_path):
    """无 active 专员 ⇒ 恒走探索。hot(4正)≈0.9716 / cold(8负)≈0.0067 家族权重。

    理论频率：P(均匀分支)=0.20；P(cold)=0.8·w_c/(w_h+w_c)+0.2·(1/2)=0.10552。
    floor=0 对照组 P(cold)=0.00690——保底把冷者中签率抬升 ~15 倍可被频率分辨。
    """
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "hot", direction=1)
    for _ in range(8):
        _dep(db, "cold", direction=-1)
    v = _view(db)
    task = RouteTask("T-S", "auditor", "injection", "sqli")
    pool = ["cold", "hot"]

    # 手算期望（家族层 C* = 收缩 3 次后的链值）
    w_hot = float(Fraction(4269, 4394))                  # n=8,  C_raw=1
    w_cold = float(Fraction(125, 18522))                 # n=16, C_raw=0
    p_cold_weighted = w_cold / (w_hot + w_cold)
    p_cold = (1 - PARAMS.explore.UNIFORM_FLOOR) * p_cold_weighted \
        + PARAMS.explore.UNIFORM_FLOOR * 0.5

    n_trials = 40_000
    n_uniform = n_cold = 0
    rng = random.Random(20260929)
    for _ in range(n_trials):
        dec = route(task, v, rng, PARAMS, pool=pool)
        assert dec.channel == "explore" and dec.specs == () and dec.tilt is None
        n_uniform += bool(dec.uniform_pick)
        n_cold += dec.assignee == "cold"
    f_uniform, f_cold = n_uniform / n_trials, n_cold / n_trials

    assert abs(f_uniform - PARAMS.explore.UNIFORM_FLOOR) <= 0.01   # 20% 保底生效（≈5σ 内）
    assert abs(f_cold - p_cold) <= 0.01                            # 混合频率与手算一致
    assert f_cold > 0.05                                           # 远高于纯加权

    # 对照：关掉保底（floor=0）⇒ cold 只剩加权份量 ≈ 0.0069
    p0 = load_params().model_copy(deep=True)
    p0.explore.UNIFORM_FLOOR = 0.0
    n_cold0 = 0
    rng0 = random.Random(20260929)
    for _ in range(n_trials):
        dec = route(task, v, rng0, p0, pool=pool)
        assert dec.uniform_pick is False
        n_cold0 += dec.assignee == "cold"
    f_cold0 = n_cold0 / n_trials
    assert abs(f_cold0 - p_cold_weighted) <= 0.003
    assert f_cold0 < 0.02 and f_cold0 * 5 < f_cold                 # 保底抬升 ≥5 倍


# ── 同种子逐位可复现（验收 ac_muz07uljvsu4qhfe）────────────────

def test_same_seed_route_sequences_identical(tmp_path):
    """同种子两次调用整段路由序列逐位一致；异种子序列必不相同。"""
    db = tmp_path / "led.db"
    for _ in range(4):
        _dep(db, "a1", direction=1)
    for _ in range(2):
        _dep(db, "a2", direction=1)
    _dep(db, "b", direction=1)
    _dep(db, "b", direction=-1)
    _activate(db, "a1")
    _activate(db, "a2")
    v = _view(db)

    def run_seq(seed: int, n: int = 150) -> list[tuple]:
        rng = random.Random(seed)
        out = []
        for i in range(n):
            # 交替类型级/家族级任务：tilt≈0.9 专员通道与探索通道都会出现
            t = RouteTask(f"T-{i:04d}", "auditor", "injection", "sqli" if i % 2 == 0 else None)
            d = route(t, v, rng, PARAMS)
            out.append((d.assignee, d.channel, d.reason, d.tilt, d.specs, d.uniform_pick))
        return out

    seq1 = run_seq(20260929)
    seq2 = run_seq(20260929)
    assert seq1 == seq2                                 # 逐位一致（含 float tilt）
    assert seq1 != run_seq(77)                          # 异种子序列必不同
    channels = {d[1] for d in seq1}
    assert channels == {"specialist", "explore"}        # 两通道都真实出现过
    explore_assignees = {d[0] for d in seq1 if d[1] == "explore"}
    assert explore_assignees <= {"a1", "a2", "b"}       # 探索池 = 家族内全员


# ── rng 消耗顺序契约 / 家族级任务 / dispatch 载荷字段 ───────────

def test_rng_consumption_order_and_family_only_task(tmp_path):
    """契约：只调 rng.random()——专员命中 1 次；探索 3 次（tilt+分支+取样）。"""
    db = tmp_path / "led.db"
    for _ in range(3):
        _dep(db, "a", direction=1)
    _dep(db, "a", tier="S2", direction=-1, weight=0.5)
    _dep(db, "b", direction=1)
    _activate(db, "a")
    v = _view(db)
    task = RouteTask("T-6", "auditor", "injection", "sqli")

    rng = ScriptedRng(0.5)                       # 0.5 < tilt≈0.546 ⇒ 命中
    dec = route(task, v, rng, PARAMS)
    assert dec.channel == "specialist" and dec.assignee == "a"
    assert rng.calls == ["random"]

    rng = ScriptedRng(0.99, 0.1)                 # miss → 0.1 < 0.20 ⇒ 均匀分支
    dec = route(task, v, rng, PARAMS)
    assert dec.channel == "explore" and dec.uniform_pick is True
    assert dec.assignee == "a"                   # 池排序 [a, b]，脚本耗尽补 0.0 ⇒ 首位
    assert rng.calls == ["random", "random", "random"]

    rng = ScriptedRng(0.99, 0.5, 0.999999)       # 加权分支，取样值逼近总权重 ⇒ 尾部 b
    dec = route(task, v, rng, PARAMS)
    assert dec.uniform_pick is False and dec.assignee == "b"
    assert rng.calls == ["random"] * 3


def test_dispatch_payload_fields_and_family_only_node(tmp_path):
    """家族级任务 node=family；to_dispatch_payload 恰为 DispatchPayload 形状。"""
    db = tmp_path / "led.db"
    for _ in range(2):
        _dep(db, "sc1", role="scout", path=("crypto",))
    _activate(db, "sc1", node="crypto", role="scout")
    v = _view(db)

    dec = route(RouteTask("T-9", "scout", "crypto"), v, ScriptedRng(0.0), PARAMS)
    assert dec.node == "crypto" and dec.family == "crypto"          # 无类型 ⇒ 家族格
    payload = dec.to_dispatch_payload(hop_budget=2)
    assert payload == {
        "task_id": "T-9", "role": "scout", "node": "crypto",
        "assignee": "sc1", "hop_budget": 2,
    }
    validated = DispatchPayload.model_validate(payload)             # 账本载荷直校验
    assert validated.assignee == "sc1"
    assert isinstance(dec, RouteDecision)


def test_empty_ledger_returns_unassigned_and_is_read_only(tmp_path):
    """空账本：空池决策不崩溃不消耗 rng；route 纯读（cells 视图原样）。"""
    db = tmp_path / "led.db"
    v = _view(db)
    before = dict(v.cells)
    rng = ScriptedRng()
    dec = route(RouteTask("T-0", "auditor", "injection", "sqli"), v, rng, PARAMS)
    assert dec.assignee is None and dec.reason == "empty_pool"
    assert dec.channel == "explore" and dec.tilt is None and dec.uniform_pick is None
    assert rng.calls == []
    assert dict(v.cells) == before                       # 只读：视图未被改动
    # 显式空池同理
    dec = route(RouteTask("T-0", "auditor", "injection", "sqli"), v, ScriptedRng(), PARAMS, pool=[])
    assert dec.assignee is None and dec.reason == "empty_pool"
