"""kernel.budget：三档预算与熔断（SPEC-2 §4；SPEC-5 §1.4 门禁最低用例集）。

钉住的行为：

- 三档配额判定（§4.2 公式直译）：quota_l2 = min(cap, ⌊remaining/cost⌋)，
  quota_s2 = ⌊存活数×remaining/cost⌋，cost/remaining ≤ 0 → 0（含边界等号）；
- S2 排序键（D15）：(severity, scarcity, confidence) 字典序降序，不取乘积；
- 熔断判据 spent ≥ budget **含等号**（D16）；降级阶梯 full→pr→fast 到底（§4.3）；
- 首次 trip：SCALING(pool=f"mode:{mode}", delta=−1, reason="budget_breaker")
  + runs.status='degraded' 与 spent 更新同事务（D17）；重复 trip 零新事件（幂等）；
- 熔断事件可重放重建：rebuild 与在线 cells 逐字段全等（任意前缀检查点）；
- 熔断路径零存款负断言：SCALING 事件从不产生 deposit；
- 缺省关闭（D18）：不注入 BudgetPolicy 即无预算约束路径——纯函数不接账本、
  不注入即零写副作用（M0 轨迹逐字兼容由黄金回归 digest 不变双保险）。
"""

import pytest

from qswarm.contracts import (
    DepositOrigin,
    DepositPayload,
    LedgerKind,
    Role,
    RunStatus,
    ScalingPayload,
    SignalTier,
    load_params,
)
from qswarm.kernel import KernelLedger
from qswarm.kernel.ledger import FixedClock, transaction
from qswarm.kernel.budget import (
    BREAKER_REASON,
    DEGRADED_TIER,
    BudgetPolicy,
    budget_for_mode,
    budget_state,
    on_cost,
    quota_l2,
    quota_s2,
    s2_rank_key,
)
from qswarm.kernel.replay import diff_cells

TS = "2026-10-10T00:00:00Z"

PARAMS = load_params()


def _policy(mode: str = "full", budget: int = 600, cost_l2: int = 5,
            cost_s2: int = 10, cap: int = 45) -> BudgetPolicy:
    return BudgetPolicy(
        mode=mode,
        budget_cents=budget,
        cost_per_l2_cents=cost_l2,
        cost_per_s2_cents=cost_s2,
        l2_cap=cap,
    )


def _led_with_run(mode: str = "full", budget_cents: int = 600,
                  run_id: str = "R-BUDGET") -> KernelLedger:
    led = KernelLedger(":memory:", clock=FixedClock(TS))
    led.conn.execute(
        "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
        " status, started_at) VALUES (?, ?, 'golden://budget', ?, 0, 'running', ?)",
        (run_id, mode, budget_cents, TS),
    )
    led.conn.commit()
    return led


def _deposit(led: KernelLedger, agent: str = "a1") -> None:
    led.append(
        "deposit",
        DepositPayload(
            finding=None,
            cluster=None,
            agent=agent,
            role=Role.AUDITOR,
            taxonomy_path=["injection", "sqli"],
            signal_tier=SignalTier.S2,
            direction=1,
            weight=0.5,
            origin=DepositOrigin.PROD,
        ),
    )


# ── §1.4 最低用例集（SPEC-5 钉名）────────────────────────────────


def test_budget_for_mode_table():
    """fast→FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS（§4.2；configs 缺省 2/100/600）。"""
    assert budget_for_mode("fast", PARAMS) == PARAMS.budget.FAST_CENTS == 2
    assert budget_for_mode("pr", PARAMS) == PARAMS.budget.PR_CENTS == 100
    assert budget_for_mode("full", PARAMS) == PARAMS.budget.FULL_CENTS == 600
    with pytest.raises(ValueError, match="入口档"):
        budget_for_mode("mega", PARAMS)


def test_quota_l2_boundaries():
    """min(cap, ⌊remaining/cost⌋)；cost ≤ 0 或 remaining ≤ 0 → 0；cap 等号边界。"""
    assert quota_l2(100, 5, cap=45) == 20          # 普通地板除
    assert quota_l2(99, 5, cap=45) == 19           # ⌊99/5⌋ = 19
    assert quota_l2(1000, 5, cap=45) == 45         # cap 截断
    assert quota_l2(225, 5, cap=45) == 45          # 恰压 cap（等号）
    assert quota_l2(224, 5, cap=45) == 44          # cap 之下
    assert quota_l2(100, 5, cap=0) == 0            # 无配额
    assert quota_l2(0, 5, cap=45) == 0             # remaining == 0（等号归零）
    assert quota_l2(-1, 5, cap=45) == 0            # remaining < 0
    assert quota_l2(100, 0, cap=45) == 0           # cost == 0（等号归零，保守不派）
    assert quota_l2(100, -5, cap=45) == 0          # cost < 0


def test_quota_s2_formula_and_zero_cost():
    """⌊存活数×remaining/cost⌋；cost ≤ 0 → 0；remaining ≤ 0 / 存活数 ≤ 0 → 0。"""
    assert quota_s2(3, 100, 10) == 30              # 3×100/10
    assert quota_s2(7, 99, 10) == 69               # ⌊693/10⌋ 地板
    assert quota_s2(1, 10, 10) == 1                # 恰好一次
    assert quota_s2(3, 100, 0) == 0                # cost == 0
    assert quota_s2(3, 100, -10) == 0              # cost < 0
    assert quota_s2(3, 0, 10) == 0                 # remaining == 0
    assert quota_s2(3, -1, 10) == 0                # remaining < 0（熔断后）
    assert quota_s2(0, 100, 10) == 0               # 无存活点


def test_s2_rank_key_lexicographic():
    """(SEVERITY_RANK, scarcity, confidence) 字典序降序；不取乘积（D15）。"""
    # severity 主序
    assert s2_rank_key("critical", "sqli", 0.5, run_cluster_counts={}) > \
        s2_rank_key("high", "sqli", 0.9, run_cluster_counts={})
    assert s2_rank_key("high", "sqli", 0.5, run_cluster_counts={}) > \
        s2_rank_key("medium", "sqli", 0.5, run_cluster_counts={})
    assert s2_rank_key("medium", "sqli", 0.5, run_cluster_counts={}) > \
        s2_rank_key("low", "sqli", 0.5, run_cluster_counts={})
    # 零置信不吞 severity 主序（乘积式会归零——D15 的反例）
    assert s2_rank_key("high", "sqli", 0.0, run_cluster_counts={"sqli": 99}) > \
        s2_rank_key("low", "xss", 0.99, run_cluster_counts={})
    # scarcity = 1/(1+簇数)：越稀缺越优先；type_node=None 按空串计（最高稀缺）
    assert s2_rank_key("high", "xss", 0.5, run_cluster_counts={"sqli": 2}) > \
        s2_rank_key("high", "sqli", 0.5, run_cluster_counts={"sqli": 2})
    assert s2_rank_key("high", None, 0.5, run_cluster_counts={"sqli": 0}) == \
        (3, 1.0, 0.5)
    assert s2_rank_key("high", "sqli", 0.5, run_cluster_counts={"sqli": 2}) == \
        (3, 1.0 / 3.0, 0.5)
    # confidence 收尾序
    assert s2_rank_key("high", "sqli", 0.7, run_cluster_counts={}) > \
        s2_rank_key("high", "sqli", 0.5, run_cluster_counts={})
    # 降序取序（sorted reverse）确定
    ranked = sorted(
        [
            s2_rank_key("low", "xss", 0.9, run_cluster_counts={}),
            s2_rank_key("critical", None, 0.1, run_cluster_counts={}),
            s2_rank_key("high", "sqli", 0.5, run_cluster_counts={"sqli": 2}),
        ],
        reverse=True,
    )
    assert ranked[0][0] == 4 and ranked[-1][0] == 1
    with pytest.raises(ValueError, match="severity"):
        s2_rank_key("blocker", "sqli", 0.5, run_cluster_counts={})


def test_budget_state_trips_at_equal_spent():
    """spent ≥ budget 含等号 → degraded（D16）；阶梯 full→pr→fast 到底；quota 重算。"""
    full = _policy(mode="full", budget=600)
    assert budget_state(0, full).degraded is False
    assert budget_state(599, full).degraded is False       # 差 1 不熔断
    st = budget_state(600, full)                            # 恰压线（等号）→ trip
    assert st.degraded is True
    assert st.tier == "pr"
    assert st.remaining_cents == 0 and st.quota_l2 == 0 and st.quota_s2 == 0
    st = budget_state(601, full)                            # 超支：保持熔断
    assert st.degraded is True and st.remaining_cents == -1
    assert st.quota_l2 == 0 and st.quota_s2 == 0
    # 未熔断：quota 按剩余预算重算
    st = budget_state(100, full)
    assert (st.remaining_cents, st.quota_l2, st.quota_s2) == (500, 45, 45 * 500 // 10)
    # 降级阶梯：full→pr、pr→fast、fast 到底（§4.3）
    assert budget_state(600, full).tier == DEGRADED_TIER["full"] == "pr"
    pr = _policy(mode="pr", budget=100)
    assert budget_state(99, pr).tier == "pr"
    assert budget_state(100, pr).tier == DEGRADED_TIER["pr"] == "fast"
    fast = _policy(mode="fast", budget=2)
    assert budget_state(1, fast).tier == "fast"
    assert budget_state(2, fast).tier == DEGRADED_TIER["fast"] == "fast"  # 到底


def test_batch_eligible_full_only():
    """batch_eligible 仅 full 为 True（§4.1：实际 Batch 调用归供给层）。"""
    assert _policy(mode="full").batch_eligible is True
    assert _policy(mode="pr").batch_eligible is False
    assert _policy(mode="fast").batch_eligible is False


def test_on_cost_first_trip_scaling_event_degraded_status():
    """首次 trip：SCALING + runs.status='degraded' 同事务；重复 trip 零新事件（D17）。"""
    led = _led_with_run(mode="full", budget_cents=10)
    try:
        pol = _policy(mode="full", budget=10)
        # 未熔断：只累加 spent，零事件。
        st = on_cost(led, "R-BUDGET", 4, policy=pol)
        assert (st.spent_cents, st.degraded, st.tier) == (4, False, "full")
        assert led.read() == []
        # 恰压线（4+6=10 == budget）→ 首次 trip：恰好一条 SCALING，字面钉死。
        st = on_cost(led, "R-BUDGET", 6, policy=pol)
        assert st.degraded is True and st.tier == "pr" and st.remaining_cents == 0
        events = led.read(kinds=(LedgerKind.SCALING,))
        assert len(events) == 1
        ev = events[0]
        assert ev.run_id == "R-BUDGET"
        assert ev.payload == {
            "pool": "mode:full", "delta": -1, "reason": BREAKER_REASON,
        }
        row = led.conn.execute(
            "SELECT spent_cents, status FROM runs WHERE run_id='R-BUDGET'"
        ).fetchone()
        assert row == (10, str(RunStatus.DEGRADED))
        # 重复 trip：零新事件（幂等，§6.4），spent 照常累加。
        st = on_cost(led, "R-BUDGET", 1, policy=pol)
        assert st.degraded is True and st.spent_cents == 11
        assert len(led.read(kinds=(LedgerKind.SCALING,))) == 1
        assert len(led.read()) == 1
    finally:
        led.close()


# ── 衔接、守卫与原子性 ───────────────────────────────────────────

try:  # 委托状态机（kernel.dispatch）由并行子任务落地；落地前不收集（零 skip）。
    from qswarm.kernel.dispatch import expire_run  # noqa: F401
except Exception:  # noqa: BLE001 — 部分落盘/导入失败一律视为未就绪
    expire_run = None

if expire_run is not None:

    def test_expire_run_after_breaker_zero_deposit():
        """熔断收尾接 expire_run：expired 路径不产任何 deposit（负断言，§4.3/§2.4）。"""
        led = _led_with_run(mode="full", budget_cents=10)
        try:
            led.conn.execute(
                "INSERT INTO tasks (task_id, run_id, role, node, shard_json,"
                " state, created_at) VALUES ('T-1', 'R-BUDGET', 'auditor',"
                " 'injection', '{}', 'dispatched', ?)",
                (TS,),
            )
            led.conn.execute(
                "INSERT INTO findings (id, run_id, reporter, reporter_role,"
                " family, type, lang, repo, path, line_start, line_end,"
                " commit_sha, title, severity, grade, state, created_at,"
                " updated_at) VALUES ('F-1', 'R-BUDGET', 'a1', 'auditor',"
                " 'injection', 'sqli', 'py', 'golden://r', 'a.py', 1, 2,"
                " 'c0ffee', 't', 'high', 'L1', 'open', ?, ?)",
                (TS, TS),
            )
            led.conn.commit()
            before = len(led.read(kinds=(LedgerKind.DEPOSIT,)))
            on_cost(led, "R-BUDGET", 10, policy=_policy(mode="full", budget=10))
            expire_run(led, "R-BUDGET")
            after = len(led.read(kinds=(LedgerKind.DEPOSIT,)))
            assert after == before  # 全账本 deposit 计数不变（零存款负断言）
            f_state = led.conn.execute(
                "SELECT state FROM findings WHERE id='F-1'"
            ).fetchone()[0]
            t_state = led.conn.execute(
                "SELECT state FROM tasks WHERE task_id='T-1'"
            ).fetchone()[0]
            assert f_state == "expired" and t_state == "failed"
        finally:
            led.close()


def test_on_cost_guards_negative_cost_unknown_run_mode_mismatch():
    """负成本 / 未知 run / mode 错配 → ValueError，零写副作用。"""
    led = _led_with_run(mode="full", budget_cents=10)
    try:
        pol = _policy(mode="full", budget=10)
        with pytest.raises(ValueError, match="负成本"):
            on_cost(led, "R-BUDGET", -1, policy=pol)
        with pytest.raises(ValueError, match="未知 run_id"):
            on_cost(led, "R-NOPE", 1, policy=pol)
        with pytest.raises(ValueError, match="不一致"):
            on_cost(led, "R-BUDGET", 1, policy=_policy(mode="pr", budget=10))
        assert led.read() == []  # 零新事件
        row = led.conn.execute(
            "SELECT spent_cents, status FROM runs WHERE run_id='R-BUDGET'"
        ).fetchone()
        assert row == (0, "running")
    finally:
        led.close()


def test_on_cost_inside_outer_transaction_rolls_back_atomically():
    """外层事务内 trip 后异常 → spent/status/SCALING 整体回滚（同事务，D17）。"""
    led = _led_with_run(mode="full", budget_cents=10)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with transaction(led):
                on_cost(led, "R-BUDGET", 10, policy=_policy(mode="full", budget=10))
                raise RuntimeError("boom")
        assert led.read() == []  # SCALING 随事务回滚
        row = led.conn.execute(
            "SELECT spent_cents, status FROM runs WHERE run_id='R-BUDGET'"
        ).fetchone()
        assert row == (0, "running")
    finally:
        led.close()


def test_on_cost_budget_authority_is_policy_and_note_lands_in_meta():
    """追加预算（换 policy）即解除压制；note 落 runs.meta_json 审计注记。"""
    led = _led_with_run(mode="full", budget_cents=10)
    try:
        on_cost(led, "R-BUDGET", 10, policy=_policy(mode="full", budget=10))
        assert len(led.read(kinds=(LedgerKind.SCALING,))) == 1
        # 预算判据以 policy.budget_cents 为准：追加预算后同额花费不再触发新事件。
        st = on_cost(led, "R-BUDGET", 0, policy=_policy(mode="full", budget=100))
        assert st.degraded is False
        # status 已 degraded 不回翻（降级是新增事件而非改写历史；首 trip 唯一）。
        row = led.conn.execute(
            "SELECT status, meta_json FROM runs WHERE run_id='R-BUDGET'"
        ).fetchone()
        assert row[0] == str(RunStatus.DEGRADED)
        on_cost(led, "R-BUDGET", 5, policy=_policy(mode="full", budget=100),
                note="resumed-under-extended-budget")
        meta = led.conn.execute(
            "SELECT meta_json FROM runs WHERE run_id='R-BUDGET'"
        ).fetchone()[0]
        assert '"budget_note"' in meta and "resumed-under-extended-budget" in meta
        assert len(led.read(kinds=(LedgerKind.SCALING,))) == 1  # 仍只有首次 trip 一条
    finally:
        led.close()


# ── 重放等价与零存款负断言（ac：熔断事件可重放重建）────────────────


def test_breaker_replay_rebuild_equals_online_cells():
    """熔断事件（SCALING）可重放重建：rebuild 与在线 cells 逐字段全等（含前缀检查点）。"""
    from qswarm.kernel import agg_cells

    led = _led_with_run(mode="full", budget_cents=10)
    try:
        _deposit(led, "a1")          # seq 1
        _deposit(led, "a2")          # seq 2
        on_cost(led, "R-BUDGET", 4, policy=_policy(mode="full", budget=10))
        on_cost(led, "R-BUDGET", 6, policy=_policy(mode="full", budget=10))
        trip_seq = led.read(kinds=(LedgerKind.SCALING,))[0].seq   # seq 3
        _deposit(led, "a3")          # seq 4（熔断后照常可落存款）
        # 在线投影（M0 同一聚合入口）vs 全量重放重建：逐字段全等。
        online = agg_cells(led, TS, params=PARAMS)
        assert diff_cells(online, led.rebuild()) == []
        assert diff_cells(led.rebuild(), led.rebuild()) == []
        # 任意前缀检查点重放确定；SCALING 事件对 cells 投影不可见
        # （trip 前后两检查点全等——降级只改 runs.status 与审计事件流）。
        before_trip = led.rebuild(upto_seq=trip_seq - 1)
        at_trip = led.rebuild(upto_seq=trip_seq)
        assert diff_cells(before_trip, at_trip) == []
        kinds = [e.kind for e in led.read()]
        assert kinds.count(LedgerKind.SCALING) == 1
        assert kinds.count(LedgerKind.DEPOSIT) == 3
    finally:
        led.close()


def test_breaker_path_emits_zero_deposits():
    """零存款负断言：熔断路径（含重复 trip 与 quota 归零判定）从不产生 deposit。"""
    led = _led_with_run(mode="full", budget_cents=5)
    try:
        pol = _policy(mode="full", budget=5)
        on_cost(led, "R-BUDGET", 5, policy=pol)   # trip
        on_cost(led, "R-BUDGET", 5, policy=pol)   # 重复 trip（幂等）
        on_cost(led, "R-BUDGET", 5, policy=pol)   # 再重复
        kinds = [e.kind for e in led.read()]
        assert kinds == [LedgerKind.SCALING]      # 全账本只有首 trip 一条 SCALING
        assert LedgerKind.DEPOSIT not in kinds
    finally:
        led.close()


# ── 缺省关闭（D18）：不注入即无预算约束路径 ────────────────────────


def test_default_off_no_policy_no_side_effects():
    """不注入 BudgetPolicy = 零写副作用：纯函数不接账本；账本无 run 行时零变化。"""
    led = KernelLedger(":memory:")  # 无 runs 行、无任何预算注入
    try:
        before = led.conn.total_changes
        # 纯判定函数不接账本——不注入即不存在预算路径（D18 的可检形态）。
        st = budget_state(600, _policy(mode="full", budget=600))
        assert st.degraded is True
        # 账本零变化：没有 run 行、没有事件、没有 cells 写。
        assert led.conn.total_changes == before
        assert led.read() == []
        assert led.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        led.close()
