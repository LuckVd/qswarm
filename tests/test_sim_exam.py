"""simulator.exam 验收测试（SPEC-4 §1.5、设计 §8.2/§8.4/§7.6 转换②）。

对照子任务验收标准：
  - ac_muz07ulj2v5frbxk：seeding=True 时 t=0 各 agent 家族格存在 origin=exam 存款
    且权重幅值落在 ±[0.2, 0.5] 映射域；seeding=False 时无任何 exam 存款；
  - ac_muz07uljpju09ymj：暗考规则逐条断言——3 真全对且拒 ≥1 雷才过；真题错或
    0 雷拒不过；不过回 shadow 并冷却（冷却期内不重复触发）；
  - ac_muz07uljqffnk5fk：固定种子可复现；exam 存款按 15d 半衰期折算
    （与 kernel.cells 用例衔接断言：origin=exam + params.decay.T_HALF_DAYS_EXAM
    同源 + w_eff = w·2^(−age/15) 黄金值）。

边界控制：暗考确定性用 StubPool 把格上真实能力 q 钉到 1.0 / 0.0（p_clip 放开到
[0,1]），配合 `_draw_item` 缝点脚本化作答，逐条验证及格规则的四个象限。
"""

import math
from itertools import product

import pytest

from qswarm.contracts import ParamsConfig, CellStatus, ExamPurpose, LedgerKind, Role, load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.simulator import (
    AgentPool,
    ExamCenter,
    ExamWorldParams,
    WorldHiddenParams,
    centered_exam_weight,
    exam_item_pass_p,
    promotion_exam_passes,
    seed_entry_exams,
)

PARAMS = load_params()                      # configs/params.yaml（exam/decay 节）


def literal_params(**over):
    """§8.2/§8.4 字面口径基线：v2 定稿旋钮全部关闭（本文件的播种/暗考/冷却
    语义不随 params.yaml 定稿漂移；需要点值再以 over 覆盖）。"""
    data = PARAMS.model_dump()
    for key, value in {
        "exam.ENTRY_DIRECT": 0.0, "exam.ENTRY_TOP_FRAC": 0.0,
        "exam.TYPE_ENTRY": 0.0, "exam.ENTRY_MARGIN": 0,
        "exam.ENTRY_GRACE_DAYS": 0, "exam.ENTRY_CORROB_S2S3": 0.0,
        "exam.ENTRY_WINDOW_DAYS": 0, "exam.TYPE_ENTRY_COMBINE": 0.0,
        "exam.COOLDOWN_DAYS": 14,
        "demote.FC_DAYS": 0,
    }.items():
        sec, name = key.split(".")
        data[sec][name] = value
    for key, value in over.items():
        sec, name = key.split(".")
        data[sec][name] = value
    return ParamsConfig.model_validate(data)
FAMILIES = [                                # taxonomy 顺序（world.py 的遍历序）
    "injection", "deser", "crypto", "authz", "config_dep", "mem_safety",
]
N_AGENTS = 18                               # roster 6/5/4/3
N_CELLS = N_AGENTS * len(FAMILIES)          # 108 个 (agent, family) 格


# ── 桩件：把格上真实能力钉死，暗考结局因此可确定预测 ─────────────

class StubAgent:
    """最小 agent：capability 只按 (family[/type]) 查表；ExamCenter 所需的全部接口。"""

    def __init__(self, agent_id: str, role: Role, q_by_node: dict[str, float]):
        self.agent_id = agent_id
        self.role = role
        self.q_by_node = q_by_node

    def capability(self, family, type_=None, *, t=0, hidden=None) -> float:
        return self.q_by_node[family if type_ is None else f"{family}/{type_}"]


class StubPool:
    hidden_params = WorldHiddenParams()

    def __init__(self, *agents: StubAgent):
        self._by_id = {a.agent_id: a for a in agents}

    def get(self, agent_id: str) -> StubAgent:
        return self._by_id[agent_id]


def seeded_pool(seed: int = 42) -> AgentPool:
    return AgentPool.from_params(PARAMS, seed=seed)


# ══ ac_muz07ulj2v5frbxk：入学播种存在性 / 映射域 / 对照组关闭 ══

def test_entry_seeding_writes_origin_exam_deposits_in_domain(tmp_path):
    """seeding=True：t=0 每 agent × 每家族格恰一笔 origin=exam 存款，幅值 ∈ [0.2, 0.5]。"""
    pool = seeded_pool(42)
    with KernelLedger(tmp_path / "l.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        outcomes = seed_entry_exams(pool, led, seed=42, params=literal_params())

        # ① 覆盖性：恰 N_CELLS 笔存款，(agent, family) 全积且不重不漏
        assert len(outcomes) == N_CELLS
        events = led.read(kinds=[LedgerKind.DEPOSIT])
        assert [e.seq for e in events] == [o.seq for o in outcomes]   # 全账本只此一批
        cells = {(e.payload["agent"], e.payload["taxonomy_path"][0]) for e in events}
        assert cells == {
            (a.agent_id, f) for a in pool.agents for f in FAMILIES
        }

        # ② 载荷面：origin=exam、signal_tier=S1（只影响路由）、direction 存符号
        for ev, o in zip(events, outcomes):
            assert ev.payload["origin"] == "exam"
            assert ev.payload["signal_tier"] == "S1"
            assert ev.payload["direction"] == o.direction and o.direction in (-1, 1)
            assert ev.payload["taxonomy_path"] == [o.family]
            assert ev.payload["weight"] == o.weight
            assert ev.ts == "2030-01-01T00:00:00Z"                    # t=0 时点（固定钟）

        # ③ 映射域：幅值逐笔落在 ±[0.2, 0.5]（符号在 direction，weight 恒正幅值）
        assert all(0.2 <= o.weight <= 0.5 for o in outcomes)
        assert {o.direction for o in outcomes} == {-1, 1}             # 两个方向都实际出现

        # ④ 播种确实量能力：q_f 与实考正确率正相关（固定种子下确定性断言）
        qs = [o.q_f for o in outcomes]
        accs = [o.accuracy for o in outcomes]
        mq, ma = sum(qs) / N_CELLS, sum(accs) / N_CELLS
        cov = sum((q - mq) * (a - ma) for q, a in zip(qs, accs))
        assert cov > 0
        # 每格题数 = entry_items=12（§8.4「8–12 题」上限 = M0 定稿缺省）
        assert all(len(o.answers) == 12 for o in outcomes)


def test_entry_seeding_disabled_writes_nothing(tmp_path):
    """seeding=False（对照组整体关闭）：零事件、零随机消耗。"""
    pool = seeded_pool(42)
    cfg = ExamWorldParams(seeding=False)
    assert ExamWorldParams().seeding is True          # 默认开启
    with KernelLedger(tmp_path / "l.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        out = seed_entry_exams(pool, led, seed=42, params=literal_params(), config=cfg)
        assert out == []
        assert led.latest_seq() == 0                  # 账本一个事件都没有
        assert led.read() == []


def test_centered_exam_weight_mapping():
    """正确率中心化映射的纯函数性质：幅值域、tie 规则、单调性、黄金值。"""
    # 黄金值：a=1.0 → +0.5；a=0.0 → −0.5；a=0.875 → +0.425；a=0.5 → +0.2（平票归正）
    assert centered_exam_weight(1.0) == (1, 0.5)
    assert centered_exam_weight(0.0) == (-1, 0.5)
    assert centered_exam_weight(0.875) == (1, 0.2 + 0.3 * 0.375 / 0.5)
    assert centered_exam_weight(0.5) == (1, 0.2)
    assert centered_exam_weight(0.25) == (-1, 0.35)
    # 幅值恒在映射域内且随 |a−0.5| 单调
    for a in [i / 16 for i in range(17)]:
        d, w = centered_exam_weight(a)
        assert d in (-1, 1) and 0.2 <= w <= 0.5
    up = [centered_exam_weight(0.5 + i / 20)[1] for i in range(1, 11)]
    down = [centered_exam_weight(0.5 - i / 20)[1] for i in range(1, 11)]
    assert up == sorted(up) and down == sorted(down)
    # 自定义幅值域（实验矩阵的 exam 权重上限 0.3 档）
    d, w = centered_exam_weight(1.0, w_low=0.2, w_high=0.3)
    assert (d, w) == (1, 0.3)
    with pytest.raises(ValueError):
        centered_exam_weight(1.2)


def test_exam_item_pass_p_centered():
    """单题判对概率 = 0.5 锚点中心化映射：q=0.5 → 0.5；斜率/截断黄金值。"""
    assert exam_item_pass_p(0.5) == 0.5
    assert exam_item_pass_p(1.0) == 0.95                        # 默认截断 [0.05, 0.95]
    assert exam_item_pass_p(0.0) == 0.05
    assert exam_item_pass_p(0.9, clip=(0.0, 1.0)) == 0.9        # slope=1 → p=q（截断前）
    assert exam_item_pass_p(0.9, slope=0.5, clip=(0.0, 1.0)) == 0.7   # 考试发挥压缩
    assert exam_item_pass_p(0.9, slope=0.0) == 0.5              # 零斜率 = 与能力无关


# ══ ac_muz07uljpju09ymj：晋升暗考规则逐条 ══

def test_promotion_pass_rule_table():
    """及格规则真值表：3 真全对且拒 ≥1 雷才过；真题缺一或 0 雷拒一律不过。"""
    exam = PARAMS.exam
    assert (exam.PROMO_ITEMS, exam.PROMO_REAL, exam.PROMO_MIN_REJECT_FP) == (5, 3, 1)
    for real_correct, fp_rejected in product(range(4), range(3)):
        expected = real_correct >= 3 and fp_rejected >= 1
        assert promotion_exam_passes(real_correct, fp_rejected, exam) is expected
    # 关键象限显式钉死（逐字对照 §8.2「真题全对 且 雷题至少拒 1」）
    assert promotion_exam_passes(3, 1, exam) is True            # 全对 + 拒 1 雷
    assert promotion_exam_passes(3, 2, exam) is True            # 全对 + 拒 2 雷
    assert promotion_exam_passes(3, 0, exam) is False           # 全对但 0 雷拒 → 不过
    assert promotion_exam_passes(2, 2, exam) is False           # 真题错 1 → 不过


def _center(db_path, q: float, *, node="injection/sqli", agent_id="auditor-01", params=None):
    pool = StubPool(
        StubAgent(agent_id, Role.AUDITOR, {"injection": q, "injection/sqli": q})
    )
    led = KernelLedger(db_path, clock=FixedClock("2030-01-01T00:00:00Z"))
    cfg = ExamWorldParams(p_clip=(0.0, 1.0))                    # 放开概率域做边界控制
    return ExamCenter(pool, led, seed=7, params=params or PARAMS, config=cfg), led


def test_promotion_pass_activates_and_writes_events(tmp_path):
    """q=1.0：3 真全对 + 2 雷全拒 → 及格 → to_status=active + promotion/exam_result 事件。"""
    center, led = _center(tmp_path / "pass.db", q=1.0)
    out = center.run_promotion_exam("auditor-01", Role.AUDITOR, "injection/sqli", day=0)

    assert out is not None and out.passed
    assert (out.real_correct, out.fp_rejected) == (3, 2)        # 真题全对、雷题全拒
    assert out.score == 1.0
    assert out.from_status is CellStatus.CANDIDATE
    assert out.to_status is CellStatus.ACTIVE                   # 过 → active
    assert out.q == 1.0 and out.attempt == 1
    assert out.seq_promotion is not None
    # 类型级节点：capability 按 family/type 查表（node 解析正确）
    assert out.node == "injection/sqli"

    exam_results = led.read(kinds=[LedgerKind.EXAM_RESULT])
    assert len(exam_results) == 1
    assert exam_results[0].payload == {
        "exam_id": out.exam_id, "purpose": ExamPurpose.PROMOTION.value,
        "agent": "auditor-01", "node": "injection/sqli",
        "correct": True, "score": 1.0,
    }
    promotions = led.read(kinds=[LedgerKind.PROMOTION])
    assert len(promotions) == 1
    assert promotions[0].payload == {
        "agent": "auditor-01", "role": "auditor", "node": "injection/sqli",
        "exam_id": out.exam_id,
        "from_status": CellStatus.CANDIDATE.value,
        "to_status": CellStatus.ACTIVE.value,
    }
    # §8.2：暗考表现不进路由格子——除 exam_result/promotion 外无任何存款事件
    assert led.read(kinds=[LedgerKind.DEPOSIT]) == []
    led.close()


def test_promotion_fail_real_wrong_returns_to_shadow(tmp_path):
    """q=0.0：真题全错 → 不过 → 退回 shadow + 进冷却；不落 promotion 事件。"""
    center, led = _center(tmp_path / "fail.db", q=0.0)
    out = center.run_promotion_exam("auditor-01", Role.AUDITOR, "injection/sqli", day=10)

    assert out is not None and not out.passed
    assert out.real_correct == 0                                 # 真题一道没对
    assert out.to_status is CellStatus.SHADOW                    # 不过 → 退回 shadow
    assert out.seq_promotion is None                             # 不写 promotion 事件
    assert [e.kind for e in led.read()] == [LedgerKind.EXAM_RESULT]
    assert led.read(kinds=[LedgerKind.EXAM_RESULT])[0].payload["correct"] is False
    # 冷却登记：day=10 失败 → 截止日 = 失败日 + COOLDOWN_DAYS（期内拒绝、期满重考）
    assert center.cooldown_until("auditor-01", Role.AUDITOR, "injection/sqli") \
        == out.day + PARAMS.exam.COOLDOWN_DAYS
    assert out.day == 10
    led.close()
    # 冷却行为在 test_cooldown_blocks_retrigger_until_expiry 内逐日断言


def test_promotion_fail_zero_decoy_rejected_fails(tmp_path):
    """脚本化作答：真题 3/3 全对但雷题 0/2 拒 → 仍不过（拒雷是独立必要条件）。"""
    center, led = _center(tmp_path / "decoy0.db", q=1.0)

    script = iter([True, True, True, False, False])              # 3 真对 + 2 雷全没拒
    center._draw_item = lambda rng, p: next(script)
    out = center.run_promotion_exam("auditor-01", Role.AUDITOR, "injection/sqli", day=0)
    assert (out.real_correct, out.fp_rejected) == (3, 0)
    assert out.passed is False
    assert out.to_status is CellStatus.SHADOW
    assert out.score == 3 / 5

    # 对照边界：同样全对真题、恰好拒 1 雷 → 及格（§8.2 下限）
    center2, led2 = _center(tmp_path / "decoy1.db", q=1.0)
    script2 = iter([True, True, True, False, True])
    center2._draw_item = lambda rng, p: next(script2)
    out2 = center2.run_promotion_exam("auditor-01", Role.AUDITOR, "injection/sqli", day=0)
    assert (out2.real_correct, out2.fp_rejected) == (3, 1)
    assert out2.passed is True and out2.to_status is CellStatus.ACTIVE
    led.close()
    led2.close()


def test_cooldown_blocks_retrigger_until_expiry(tmp_path):
    """不过 → 冷却 COOLDOWN_DAYS 天：期内同格拒绝再触发（零事件零消耗），期满可重考且换题。"""
    cd = literal_params().exam.COOLDOWN_DAYS
    center, led = _center(tmp_path / "cd.db", q=0.0, params=literal_params())
    fail = center.run_promotion_exam("auditor-01", Role.AUDITOR, "injection/sqli", day=10)
    assert fail is not None and not fail.passed

    seq_after_fail = led.latest_seq()
    until = 10 + cd                                              # 冷却截止日（含）
    for day in (10, 11, until - 1):                              # 冷却期内（[10, until)）
        assert center.in_cooldown("auditor-01", Role.AUDITOR, "injection/sqli", day=day)
        again = center.run_promotion_exam(
            "auditor-01", Role.AUDITOR, "injection/sqli", day=day
        )
        assert again is None                                     # 拒绝触发
    assert led.latest_seq() == seq_after_fail                    # 冷却期内零新事件

    day = until                                                  # 期满：允许重考
    assert not center.in_cooldown("auditor-01", Role.AUDITOR, "injection/sqli", day=day)
    assert center.cooldown_until("auditor-01", Role.AUDITOR, "injection/sqli") == until
    retake = center.run_promotion_exam(
        "auditor-01", Role.AUDITOR, "injection/sqli", day=day
    )
    assert retake is not None and retake.attempt == 2            # 第 2 次attempt
    assert retake.exam_id.endswith(":a2")                        # 换题：新 attempt 入卷
    assert led.latest_seq() == seq_after_fail + 1                # 重考又落一条 exam_result
    # 其他格不受该格冷却影响
    assert not center.in_cooldown("auditor-01", Role.AUDITOR, "crypto", day=10)
    led.close()


# ══ ac_muz07uljqffnk5fk：固定种子可复现 + 15d 半衰期折算 ══

def _run_seeding(db_path, seed: int):
    pool = seeded_pool(seed)
    with KernelLedger(db_path, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        return seed_entry_exams(pool, led, seed=seed, params=literal_params())


def test_seeding_reproducible_bitwise_and_seed_sensitive(tmp_path):
    """同种子两次播种逐位一致（repr 往返最强证据）；异种子轨迹不同。"""
    a1 = _run_seeding(tmp_path / "a1.db", seed=42)
    a2 = _run_seeding(tmp_path / "a2.db", seed=42)
    assert [repr(o) for o in a1] == [repr(o) for o in a2]
    assert a1 == a2                                              # dataclass 逐字段相等

    b = _run_seeding(tmp_path / "b.db", seed=43)
    assert [repr(o) for o in a1] != [repr(o) for o in b]
    assert any(x.answers != y.answers for x, y in zip(a1, b))    # 作答模式确实变了


def test_promotion_exam_reproducible(tmp_path):
    """暗考同种子同日程逐位一致；attempt 入派生路径 → 重考卷不同。"""
    def schedule(db_path):
        center, led = _center(db_path, q=0.37)                   # 中间能力：结局靠种子
        first = center.run_promotion_exam(
            "auditor-01", Role.AUDITOR, "injection/sqli", day=0
        )                                                        # 首考
        retake = center.run_promotion_exam(
            "auditor-01", Role.AUDITOR, "injection/sqli", day=14
        )                                                        # 满冷却后续考（attempt=2）
        led.close()
        return [first, retake]

    r1 = schedule(tmp_path / "r1.db")
    r2 = schedule(tmp_path / "r2.db")
    assert [repr(o) for o in r1] == [repr(o) for o in r2]
    assert r1[1] is not None                                     # 冷却期满，重考必发生
    assert r1[0].real_answers != r1[1].real_answers or (
        r1[0].fp_rejections != r1[1].fp_rejections
    ) or r1[0].passed is True                                    # attempt 入派生 → 换卷


def test_exam_deposit_decays_with_15d_half_life(tmp_path):
    """exam 存款按 15d 半衰期折算（kernel.cells 用例衔接）：origin=exam 是聚合器开关，
    T_HALF_DAYS_EXAM 同源 params.decay，w_eff = w·2^(−age/15) 黄金值（15d 恰折半）。"""
    # ① 参数源契约：15d 由 configs/params.yaml 单一来源钉住（SPEC-1 §5.2 固定值）
    assert PARAMS.decay.T_HALF_DAYS_EXAM == 15.0
    assert PARAMS.decay.T_HALF_DAYS == 30.0                      # prod 对照：30d

    pool = seeded_pool(42)
    with KernelLedger(tmp_path / "l.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        outcomes = seed_entry_exams(pool, led, seed=42, params=literal_params())
        events = led.read(kinds=[LedgerKind.DEPOSIT])

        # ② 聚合器开关契约：全部存款 origin=exam → cells 聚合时选 T_half=15d
        assert all(e.payload["origin"] == "exam" for e in events)

        # ③ 折算黄金值（SPEC-4 §1.4 / 父验收①公式 w_eff = w·2^(−age/T_half)）：
        #    age=15d → 恰折半（2^(-1) 二进制精确，等号成立）；age=30d → 1/4。
        for o in outcomes[:8]:
            w = o.weight
            assert w * 2.0 ** (-15.0 / PARAMS.decay.T_HALF_DAYS_EXAM) == w * 0.5
            assert w * 2.0 ** (-30.0 / PARAMS.decay.T_HALF_DAYS_EXAM) == w * 0.25
            assert w * 2.0 ** (-7.0 / PARAMS.decay.T_HALF_DAYS_EXAM) > w * 0.5
        # ④ exam 信息保鲜期短于生产：同龄折算 exam 降幅 > prod 降幅
        assert 2.0 ** (-15.0 / 15.0) < 2.0 ** (-15.0 / 30.0)
        assert math.isclose(2.0 ** (-15.0 / 30.0), math.sqrt(0.5), rel_tol=1e-15)


def test_exam_deposit_decay_via_kernel_cells(tmp_path):
    """kernel.cells 衔接断言（真实聚合器路径，非手算）：

    ① decay_factor 按 origin 选半衰期：exam 15d（age=15d → 恰 0.5），prod 30d；
    ② 播种账本 → agg_cells：age=0 格值=权重原值；age=15d 恰折半（exam 半衰期）；
       同账本内合成 prod 存款同龄只折到 √0.5 —— origin 分档逐位可辨；
    ③ S1 档播种存款 n_eff_s2s3 ≡ 0：§8.4「只影响路由」在聚合器侧真实成立，
       晋升判据的 S2/S3 硬门槛不被播种污染。
    """
    from datetime import datetime, timedelta, timezone

    from qswarm.contracts import DepositOrigin, DepositPayload, SignalTier
    from qswarm.kernel.cells import agg_cells, decay_factor

    d0 = datetime(2030, 1, 1, tzinfo=timezone.utc)
    day_s = 86400

    # ① 挥发因子直接对账（kernel.cells 公共 API）
    assert decay_factor(DepositOrigin.EXAM, 15 * day_s, decay=PARAMS.decay) == 0.5
    assert decay_factor(DepositOrigin.EXAM, 30 * day_s, decay=PARAMS.decay) == 0.25
    assert decay_factor(DepositOrigin.PROD, 15 * day_s, decay=PARAMS.decay) == math.sqrt(0.5)

    pool = seeded_pool(42)
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(d0)) as led:
        outcomes = seed_entry_exams(pool, led, seed=42, params=literal_params())
        # 同账本放一笔合成 prod 存款做 origin 对照——挂在池外合成 agent 名下，
        # 保证该格只有这一笔存款（池内每格都已被播种存款占据）
        o0 = outcomes[0]
        probe_agent, probe_family = "probe-prod-only", o0.family
        probe_role = o0.role
        prod_w = 0.4
        led.append(
            LedgerKind.DEPOSIT,
            DepositPayload(
                finding=None, cluster=None, agent=probe_agent, role=probe_role,
                taxonomy_path=[probe_family], signal_tier=SignalTier.S1,
                direction=1, weight=prod_w, origin=DepositOrigin.PROD,
            ),
        )

        # ② age=0：exam 格值 = 权重原值（因子 1.0）
        view0 = agg_cells(led, as_of=d0)
        cell0 = view0.get(o0.agent_id, o0.role.value, o0.family)
        assert cell0 is not None
        assert (cell0.pos_w if o0.direction == 1 else cell0.neg_w) == o0.weight
        assert cell0.n_eff == pytest.approx(2.0 * o0.weight)        # n_eff ≡ 2×(pos+neg)
        assert cell0.n_eff_s2s3 == 0.0                              # ③ S1 播种不进 S2/S3

        # ② age=15d：exam 格恰折半；同账 prod 格只折到 √0.5 —— 分档逐位可辨
        d15 = d0 + timedelta(days=15)
        view15 = agg_cells(led, as_of=d15)
        cell15 = view15.get(o0.agent_id, o0.role.value, o0.family)
        got_exam = cell15.pos_w if o0.direction == 1 else cell15.neg_w
        assert got_exam == o0.weight * 0.5                          # 15d 半衰期（exam）
        cell_prod = view15.get(probe_agent, probe_role.value, probe_family)
        assert cell_prod is not None
        got_prod = cell_prod.pos_w if cell_prod.pos_w else cell_prod.neg_w
        assert got_prod == prod_w * math.sqrt(0.5)                  # 同龄 prod：30d 半衰期
        assert got_exam < got_prod                                  # exam 保鲜期更短


def test_exam_world_params_validation():
    """世界旋钮边界：权重域/题数/斜率/概率域非法即拒。"""
    assert ExamWorldParams(weight_high=0.3).weight_high == 0.3   # 实验矩阵 0.3 档可用
    with pytest.raises(ValueError):
        ExamWorldParams(entry_items=0)
    with pytest.raises(ValueError):
        ExamWorldParams(weight_low=0.5, weight_high=0.2)
    with pytest.raises(ValueError):
        ExamWorldParams(weight_high=1.5)
    with pytest.raises(ValueError):
        ExamWorldParams(p_slope=-1.0)
    with pytest.raises(ValueError):
        ExamWorldParams(p_clip=(0.9, 0.1))
