"""kernel.dispatch 单元测试：委托契约四件套 + 派单/结论双状态机（SPEC-2 §2、SPEC-5 §1.2）。

范围：§2.2 四件套与 hop_budget、§2.3 任务派单状态机（dispatch / re_dispatch /
expire_run）、§2.4 Finding 结论状态机（迁移表逐字 + D8 直达边 + 非法迁移负用例）、
§2.5 submit 代落账四分支（L1 处置 2026-10-11 落位：scout 三角化回填 / auditor 建档
+ SchemaRejected + 错配自动 re_route / reviewer 自评守卫 + 票满自动 close_panel /
verifier §10.4 改判守卫 + S2 映射与存款）。

纪律钉子（SPEC-2 §9）：幂等双跑（读侧幂等 → []；写侧非法 → ValueError 两侧都要）、
expired 零存款负断言、事件与表行同事务、SchemaRejected 零落库。
"""

from __future__ import annotations

import pytest

import m1_harness as h
from qswarm.contracts import (
    BlockingKind,
    Family,
    FindingState,
    LedgerKind,
    ReviewerVote,
    ScoutReport,
    Severity,
    VerifyErrorKind,
    VerifyOutcome,
)
from qswarm.contracts.models import (
    Assertion,
    AuditorFinding,
    BlockingEvidence,
    EvidenceNode,
    VerifierReport,
)
from qswarm.kernel import (
    DISPATCH_MAX_ATTEMPTS,
    FINDING_TRANSITIONS,
    HOP_BUDGET_BY_MODE,
    KernelLedger,
    MemberFacts,
    ROLE_OUTPUT_SCHEMA,
    ROLE_TASK_BOUNDS,
    ROLE_TOOL_BOUNDS,
    SchemaRejected,
    TASK_STATE_TERMINAL,
    VerdictContext,
    build_contract,
    dispatch,
    expire_run,
    hop_budget_for,
    re_dispatch,
    submit,
    transition_finding,
)
from qswarm.kernel.ledger import FixedClock

CLOCK_TS = "2026-01-01T00:00:00Z"


def new_ledger() -> KernelLedger:
    return KernelLedger(":memory:", clock=FixedClock(CLOCK_TS))


def full_setup(led: KernelLedger, *, task_node: str = "injection/sqli", assignee: str | None = "A-auditor-1"):
    """标准场景：run + queued 任务 + 契约 + 决策。返回 (task_id, contract, decision)。"""
    h.seed_run(led)
    task_id = h.new_task(led, node=task_node)
    decision = h.make_decision(task_id, node=task_node, assignee=assignee)
    contract = build_contract(object(), decision, mode="full", deadline_s=600)
    return task_id, contract, decision


# ── §2.2 四件套与 hop_budget ─────────────────────────────────────


def test_role_literal_tables():
    """SPEC-5 §1.2：ROLE_* 三表与 FINDING_TRANSITIONS 逐字表（§2.2/§2.4）。

    ROLE_OUTPUT_SCHEMA 含 queen_advisor 行（SPEC-3 §6 契约面登记，DECISIONS
    2026-10-11）；ROLE_TOOL_BOUNDS / ROLE_TASK_BOUNDS 的 advisor 行为 M5 顾问
    接线落位（SPEC-3 §6/E7 预告；SPEC-9 §6，2026-10-21——③④ 段常量供 render
    装配），build_contract 对 advisor 仍显式 ValueError（顾问不进派单状态机）。"""
    assert ROLE_OUTPUT_SCHEMA == {
        "scout": "ScoutReport",
        "auditor": "AuditorFinding",
        "reviewer": "ReviewerVote",
        "verifier": "VerifierReport",
        "queen_advisor": "ArbitrationDraft",
    }
    assert ROLE_TOOL_BOUNDS == {
        "scout": ("repo:ro",),
        "auditor": ("repo:ro", "repomap:ro"),
        "reviewer": ("finding:ro", "artifact:ro"),
        "verifier": ("repo:ro", "sandbox:rw"),
        "queen_advisor": ("finding:ro", "artifact:ro"),
    }
    assert ROLE_TASK_BOUNDS == {
        "scout": "只分诊给定候选点并给出类型猜测；不做深挖、不产 finding。",
        "auditor": "只分析派单节点指名的可疑点×类型并产带证据链 finding；不扩面、不改结论分级。",
        "reviewer": "只按四维独立打票；不复用报告者结论、不产新 finding。",
        "verifier": "只对指名簇做沙箱复现并产三分类结构化结果；不投票、不产 finding。",
        "queen_advisor": "只按注入的簇材料起草结构化仲裁摘要建议稿；不产 finding、不投票、"
                         "不改变任何系统状态（仅建议权，§16.7-9）。",
    }
    assert {
        state.value: {to.value for to in outs}
        for state, outs in FINDING_TRANSITIONS.items()
    } == {
        "open": {"voted", "confirmed", "rejected", "expired"},          # D8 直达边在内
        "voted": {"verified", "inconclusive", "confirmed", "rejected", "expired"},
        "inconclusive": {"confirmed", "rejected", "expired"},
        "verified": {"confirmed", "rejected", "expired"},               # S3 推翻/确认（§6.2）
        "confirmed": set(),
        "rejected": set(),
        "expired": set(),
    }


def test_hop_budget_by_mode_and_unknown_raises():
    """SPEC-5 §1.2：fast=0/pr=1/full=2；未知档 ValueError（§2.2）。"""
    assert HOP_BUDGET_BY_MODE == {"fast": 0, "pr": 1, "full": 2}
    assert hop_budget_for("fast") == 0
    assert hop_budget_for("pr") == 1
    assert hop_budget_for("full") == 2
    from qswarm.contracts import RunMode

    assert hop_budget_for(RunMode.FULL) == 2           # StrEnum 同型接受
    with pytest.raises(ValueError, match="未知档位"):
        hop_budget_for("mega")


def test_build_contract_four_pieces_pure():
    """SPEC-5 §1.2：①goal ②output_schema_ref ③tool_bounds ④task_bounds + hop_budget +
    taxonomy_node 直出；纯函数零 IO（§2.2/§4.3）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        task_id = h.new_task(led)
        decision = h.make_decision(task_id)
        contract = build_contract(object(), decision, mode="pr", deadline_s=120)
        assert contract.task_id == task_id
        assert contract.role == "auditor"
        assert contract.goal == f"对 {decision.node} 以 auditor 职责完成本次分片"       # ①
        assert contract.output_schema_ref == ROLE_OUTPUT_SCHEMA["auditor"]              # ②
        assert list(contract.tool_bounds) == list(ROLE_TOOL_BOUNDS["auditor"])          # ③
        assert contract.task_bounds == ROLE_TASK_BOUNDS["auditor"]                      # ④
        assert contract.hop_budget == 1                                                 # pr 档
        assert contract.taxonomy_node == "injection/sqli"
        assert contract.deadline_s == 120
        assert contract.context_refs == []
        # 覆盖式装配：goal/task_bounds/node/context_refs 显式给定时直出
        custom = build_contract(
            object(), decision, mode="fast", deadline_s=30,
            node="crypto", goal="g", task_bounds="b", context_refs=["artifact://x"],
        )
        assert (custom.goal, custom.task_bounds, custom.taxonomy_node, custom.context_refs) == (
            "g", "b", "crypto", ["artifact://x"]
        )
        assert custom.hop_budget == 0                       # fast 档跳数预算天生耗尽
        # 纯函数零 IO：重复装配结果逐字段一致（无隐藏状态）
        again = build_contract(object(), decision, mode="pr", deadline_s=120)
        assert again.model_dump() == contract.model_dump()
        # queen_advisor / 未知角色不在角色表 → ValueError
        bad = h.make_decision(task_id, role="queen_advisor")
        with pytest.raises(ValueError, match="未知派单角色"):
            build_contract(object(), bad, mode="pr", deadline_s=1)
    finally:
        led.close()


# ── §2.3 任务派单状态机 ──────────────────────────────────────────


def test_dispatch_guard_rejects_non_queued():
    """SPEC-5 §1.2：tasks.state≠queued → ValueError（§2.3/§6.4）。"""
    led = new_ledger()
    try:
        task_id, contract, decision = full_setup(led)
        dispatch(led, task_id, contract, decision)
        with pytest.raises(ValueError, match="状态守卫拒绝"):
            dispatch(led, task_id, contract, decision)      # dispatched 重复派单
        with pytest.raises(ValueError, match="任务不存在"):
            ghost = h.make_decision("T-R-20260101-001-999")
            ghost_contract = build_contract(object(), ghost, mode="full", deadline_s=1)
            dispatch(led, "T-R-20260101-001-999", ghost_contract, ghost)
        # 决策/契约与 task_id 不一致 → 装配面守卫
        other = h.make_decision("T-other")
        with pytest.raises(ValueError, match="派单面不一致"):
            dispatch(led, task_id, contract, other)
    finally:
        led.close()


def test_dispatch_empty_pool_audits_stays_queued():
    """SPEC-5 §1.2：assignee=null 的 DISPATCH 事件落账、tasks 留 queued、attempts 不消耗
    （§2.3/D10）；改派由调用方重入 dispatch()。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        task_id = h.new_task(led)
        decision = h.make_decision(task_id, assignee=None, reason="empty_pool")
        contract = build_contract(object(), decision, mode="pr", deadline_s=60)
        ev = dispatch(led, task_id, contract, decision)
        assert ev.kind is LedgerKind.DISPATCH
        assert ev.payload["assignee"] is None
        assert ev.payload["hop_budget"] == 1
        row = h.task_row(led, task_id)
        assert row["state"] == "queued" and row["assignee"] is None and row["attempts"] == 0
        # 改派重入：同一 queued 任务可再次 dispatch（状态守卫只拒非 queued）
        decision2 = h.make_decision(task_id, assignee="A-auditor-2")
        contract2 = build_contract(object(), decision2, mode="pr", deadline_s=60)
        dispatch(led, task_id, contract2, decision2)
        row = h.task_row(led, task_id)
        assert row["state"] == "dispatched" and row["assignee"] == "A-auditor-2" and row["attempts"] == 1
        kinds = [e.kind for e in led.read()]
        assert kinds == [LedgerKind.DISPATCH, LedgerKind.DISPATCH]
    finally:
        led.close()


def test_re_dispatch_attempts_limit_then_failed():
    """SPEC-5 §1.2：attempts<2 重派（attempts+1、state=dispatched）+ RE_DISPATCH 事件；
    ≥2 → failed 且 ValueError、零存款（§2.3/§16.7-8）。"""
    led = new_ledger()
    try:
        task_id, contract, decision = full_setup(led)
        dispatch(led, task_id, contract, decision)
        ev1 = re_dispatch(led, task_id, to_agent="A-auditor-2", reason="timeout")
        assert ev1.kind is LedgerKind.RE_DISPATCH
        assert ev1.payload == {
            "task_id": task_id, "from_agent": "A-auditor-1",
            "to_agent": "A-auditor-2", "reason": "timeout",
        }
        row = h.task_row(led, task_id)
        assert row["state"] == "dispatched" and row["assignee"] == "A-auditor-2"
        assert row["attempts"] == 2 and row["attempts"] == DISPATCH_MAX_ATTEMPTS
        n_events = len(led.read())
        with pytest.raises(ValueError, match="重派通道已关闭"):
            re_dispatch(led, task_id, to_agent="A-auditor-3", reason="schema_fail")
        row = h.task_row(led, task_id)
        assert row["state"] == "failed"                     # 通道关闭落终态，不永悬在途
        # 零存款 + 关闭分支自身只多一条 RE_DISPATCH 审计事件（DECISIONS dispatch 行）
        assert not led.read(kinds=(LedgerKind.DEPOSIT,))
        kinds = [e.kind for e in led.read()]
        assert kinds.count(LedgerKind.RE_DISPATCH) == 2
        assert len(led.read()) == n_events + 1
    finally:
        led.close()


def test_re_dispatch_requires_dispatched_state():
    """重派前件守卫：queued 走 dispatch()、终态不可流转（§2.3 状态机图）；非法 reason
    在任何写库前拒绝（写侧非法 → ValueError，§6.4 界线）。"""
    led = new_ledger()
    try:
        task_id, contract, decision = full_setup(led)
        with pytest.raises(ValueError, match="≠ dispatched"):
            re_dispatch(led, task_id, to_agent="X", reason="timeout")   # queued 走 dispatch()
        dispatch(led, task_id, contract, decision)
        ev = re_dispatch(led, task_id, to_agent="A2", reason="timeout")
        assert ev.payload["to_agent"] == "A2"
        h.seed_run(led, "R-20260101-002")
        fresh = h.new_task(led, run_id="R-20260101-002")
        with pytest.raises(ValueError, match="词表"):
            re_dispatch(led, fresh, to_agent="X", reason="alien")   # 先于一切写库
        row = h.task_row(led, fresh)
        assert row["state"] == "queued" and row["attempts"] == 0    # 零副作用
    finally:
        led.close()


def test_create_task_sequential_ids_queued():
    """tasks 行装配口：T-<run>-<seq> 同 run 自增（D20 同域口径）、queued 初态、
    shard_json 键序规范化落库。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        t1 = h.new_task(led, shard={"b": 2, "a": 1})
        t2 = h.new_task(led, shard={"paths": ["x"]})
        assert (t1, t2) == ("T-R-20260101-001-001", "T-R-20260101-001-002")
        for t in (t1, t2):
            row = h.task_row(led, t)
            assert row["state"] == "queued" and row["attempts"] == 0 and row["dispatched_at"] is None
        assert h.shard_json_of(led, t1) == {"a": 1, "b": 2}
        assert h.task_row(led, t1)["dispatched_at"] is None
    finally:
        led.close()


# ── §2.4 Finding 结论状态机 ──────────────────────────────────────


def test_finding_transitions_table_and_terminal_guard():
    """SPEC-5 §1.2：§10.2 迁移表逐字 + D8 直达边；非法迁移/终态流转 ValueError（§2.4）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        # D8 S3 直达：open → confirmed / open → rejected 均合法
        f1 = h.seed_finding(led, "F-20260101-000001")
        transition_finding(led, f1, FindingState.CONFIRMED)
        assert h.finding_state(led, f1) == "confirmed"
        f2 = h.seed_finding(led, "F-20260101-000002")
        transition_finding(led, f2, FindingState.REJECTED)
        assert h.finding_state(led, f2) == "rejected"
        # 正常主链：open → voted → verified → confirmed
        f3 = h.seed_finding(led, "F-20260101-000003")
        for st in (FindingState.VOTED, FindingState.VERIFIED, FindingState.CONFIRMED):
            transition_finding(led, f3, st)
        assert h.finding_state(led, f3) == "confirmed"
        # 非法迁移负用例：未在迁移表出边内 / 终态再流转 / 自迁移
        f4 = h.seed_finding(led, "F-20260101-000004", state="open")
        with pytest.raises(ValueError, match="非法结论迁移"):
            transition_finding(led, f4, FindingState.VERIFIED)      # open ↛ verified
        with pytest.raises(ValueError, match="非法结论迁移"):
            transition_finding(led, f4, FindingState.OPEN)          # 自迁移不在出边
        with pytest.raises(ValueError, match="终态不可再流转"):
            transition_finding(led, f3, FindingState.EXPIRED)       # confirmed 终态
        with pytest.raises(ValueError, match="不存在"):
            transition_finding(led, "F-missing", FindingState.EXPIRED)
        # 表内出边全可走：verified → inconclusive 不合法（迁移表无此边）
        f5 = h.seed_finding(led, "F-20260101-000005", state="voted")
        transition_finding(led, f5, FindingState.VERIFIED)
        with pytest.raises(ValueError, match="非法结论迁移"):
            transition_finding(led, f5, FindingState.INCONCLUSIVE)
    finally:
        led.close()


def test_transition_finding_event_carries_state_zero_deposit():
    """SPEC-5 §1.2：kind=finding 事件 payload.state=新状态；从不产 deposit（§2.4/D9）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        fid = h.seed_finding(led, "F-20260101-000001", cluster_id=None)
        before = led.read()
        assert not [e for e in before if e.kind is LedgerKind.DEPOSIT]
        ev = transition_finding(led, fid, FindingState.VOTED)
        assert ev.kind is LedgerKind.FINDING
        assert ev.payload == {
            "finding_id": fid, "reporter": "A-auditor-1",
            "cluster": None, "state": "voted",
        }
        assert ev.run_id == "R-20260101-001"                 # run 归属取行值
        assert not led.read(kinds=(LedgerKind.DEPOSIT,))     # 零存款
        # updated_at 投影同步刷新（同事务）
        (updated_at,) = led.conn.execute(
            "SELECT updated_at FROM findings WHERE id = ?", (fid,)
        ).fetchone()
        assert updated_at == CLOCK_TS
    finally:
        led.close()


def test_expire_run_zero_deposit_terminal_idempotent():
    """SPEC-5 §1.2：非终态 finding→expired（零存款）、未完任务→failed；已终态 → []（§2.3/D19）。"""
    led = new_ledger()
    try:
        task_id, contract, decision = full_setup(led)
        dispatch(led, task_id, contract, decision)
        f_open = h.seed_finding(led, "F-20260101-000001", state="open")
        f_voted = h.seed_finding(led, "F-20260101-000002", state="voted")
        f_inconcl = h.seed_finding(led, "F-20260101-000003", state="inconclusive")
        f_verified = h.seed_finding(led, "F-20260101-000004", state="verified")   # D19：verified 非终态
        f_done = h.seed_finding(led, "F-20260101-000005", state="confirmed")      # 终态不动
        queued = h.new_task(led)
        events = expire_run(led, "R-20260101-001", reason="budget_breaker")
        assert [e.payload["finding_id"] for e in events] == [
            "F-20260101-000001", "F-20260101-000002",
            "F-20260101-000003", "F-20260101-000004",             # 字典序；含 verified（D19）
        ]
        assert all(e.kind is LedgerKind.FINDING and e.payload["state"] == "expired" for e in events)
        for f in (f_open, f_voted, f_inconcl, f_verified):
            assert h.finding_state(led, f) == "expired"
        assert h.finding_state(led, f_done) == "confirmed"        # 终态不动
        assert h.task_row(led, task_id)["state"] == "failed"      # dispatched → failed
        assert h.task_row(led, queued)["state"] == "failed"       # queued → failed
        assert not led.read(kinds=(LedgerKind.DEPOSIT,))          # expired 路径零存款
        # 幂等：全部已终态 → []（读侧幂等，§6.4）
        assert expire_run(led, "R-20260101-001") == []
        assert len(led.read()) == 1 + len(events)                 # dispatch + 流转事件，无新增
    finally:
        led.close()


def test_dispatch_replay_rebuild_equivalence():
    """事件流重放等价（§6.3 cells 投影面）：dispatch/re_dispatch 流水不产 deposit，
    cells 重放重建与在线投影 diff 为空。"""
    led = new_ledger()
    try:
        from qswarm.kernel import agg_cells, diff_cells, rebuild_cells

        task_id, contract, decision = full_setup(led)
        dispatch(led, task_id, contract, decision)
        re_dispatch(led, task_id, to_agent="A-auditor-2", reason="timeout")
        view = agg_cells(led, FixedClock(CLOCK_TS).now())
        assert view.cells == {}                       # 无 deposit 事件 → 无格子
        assert diff_cells(view, rebuild_cells(led, None)) == []
        assert not led.read(kinds=(LedgerKind.DEPOSIT,))
    finally:
        led.close()


# ── §2.5 submit 代落账（L1 处置 2026-10-11 落位）──────────────────


def _ensure_run(led: KernelLedger) -> None:
    """测试助手：默认 run 行幂等播种（同库多场景复用）。"""
    if led.conn.execute(
        "SELECT 1 FROM runs WHERE run_id = 'R-20260101-001'"
    ).fetchone() is None:
        h.seed_run(led)


def _scout_submit(led: KernelLedger, candidate_id: str, **report_kwargs):
    """scout 分支标准场景：run + 候选点 + scout 任务派单 → submit。"""
    _ensure_run(led)
    h.seed_candidate(led, candidate_id)
    task_id = h.new_task(led, role="scout", node="injection",
                         shard={"candidate_id": candidate_id})
    decision = h.make_decision(task_id, role="scout", node="injection",
                               assignee="A-scout-1")
    contract = build_contract(object(), decision, mode="fast", deadline_s=30)
    dispatch(led, task_id, contract, decision)
    report = ScoutReport(
        candidate_id=candidate_id, suspicious=True,
        guess_family=Family.INJECTION, guess_type="sqli",
        reason_ref="artifact://reason", **report_kwargs,
    )
    return task_id, contract, report


def test_submit_scout_triage_and_guess_backfill():
    """SPEC-5 §1.2：candidates.triaged=1、guess 回填；injection_flag 候选保持候选态
    标高优先（§2.5/§11.6-3）；分诊投影回填零账本事件（词表无对应 kind）。"""
    led = new_ledger()
    try:
        task_id, contract, report = _scout_submit(
            led, "CD-R-20260101-001-001"
        )
        n_events = len(led.read())
        assert submit(led, task_id, report, contract=contract) == []
        assert len(led.read()) == n_events                 # 零事件（DECISIONS submit 行）
        row = h.candidate_row(led, "CD-R-20260101-001-001")
        assert row["triaged"] == 1                         # 侦察处理后置 1
        assert row["guess_family"] == "injection" and row["guess_type"] == "sqli"
        assert row["channel_score"] == 0.5                 # 非注入不动优先级
        assert h.task_row(led, task_id)["state"] == "done"
        # 注入痕迹候选（§11.6-3）：保持候选态（照常 triaged=1 回填，不丢弃）+ 标高优先
        h.seed_candidate(led, "CD-R-20260101-001-002")
        tid2 = h.new_task(led, role="scout", node="injection",
                          shard={"candidate_id": "CD-R-20260101-001-002"})
        dec2 = h.make_decision(tid2, role="scout", node="injection", assignee="A-scout-2")
        con2 = build_contract(object(), dec2, mode="fast", deadline_s=30)
        dispatch(led, tid2, con2, dec2)
        submit(led, tid2, ScoutReport(
            candidate_id="CD-R-20260101-001-002", suspicious=False,
            guess_family=None, guess_type=None, reason_ref="artifact://r2",
            injection_flag=True,
        ), contract=con2)
        row2 = h.candidate_row(led, "CD-R-20260101-001-002")
        assert row2["triaged"] == 1                        # 不做任何丢弃
        assert row2["channel_score"] == 1.0                # 加权上界 = 最高优先
        # 候选点不存在 → ValueError 零副作用（写侧非法，§6.4 界线）
        tid3 = h.new_task(led, role="scout", node="injection",
                          shard={"candidate_id": "CD-missing"})
        dec3 = h.make_decision(tid3, role="scout", node="injection", assignee="A-scout-3")
        con3 = build_contract(object(), dec3, mode="fast", deadline_s=30)
        dispatch(led, tid3, con3, dec3)
        n_events = len(led.read())
        with pytest.raises(ValueError, match="候选点不存在"):
            submit(led, tid3, ScoutReport(
                candidate_id="CD-missing", suspicious=True,
                reason_ref="artifact://r3",
            ), contract=con3)
        assert h.task_row(led, tid3)["state"] == "dispatched"
        assert len(led.read()) == n_events
    finally:
        led.close()


def _auditor_submit(led: KernelLedger, *, node: str = "injection/sqli",
                    chain: list[EvidenceNode] | None = None, **finding_kwargs):
    """auditor 分支标准场景：run + 分片含建档字段的 auditor 任务派单 → submit 输入。"""
    _ensure_run(led)
    task_id = h.new_task(led, node=node, shard={
        "path": "src/app/db.py", "line_start": 10, "line_end": 20,
        "lang": "py", "func": "query", "commit_sha": "0c0ffee",
    })
    decision = h.make_decision(task_id, node=node, assignee="A-auditor-1")
    contract = build_contract(object(), decision, mode="full", deadline_s=600)
    dispatch(led, task_id, contract, decision)
    if chain is None:
        chain = [
            EvidenceNode(kind="source", where_path="src/app/db.py", where_line=10,
                         ref="artifact://a1"),
            EvidenceNode(kind="propagation", where_path="src/app/db.py", where_line=12,
                         ref="artifact://a2"),
            EvidenceNode(kind="sink", where_path="src/app/db.py", where_line=20,
                         ref="artifact://a3"),
            EvidenceNode(kind="sanitizer", where_path="src/app/db.py", where_line=15,
                         found=False, ref="artifact://a4"),
        ]
    output = AuditorFinding(
        title="tainted SQL query", severity=Severity.HIGH, cwe="CWE-89",
        family=Family.INJECTION, type="sqli", evidence_chain=chain,
        confidence=0.8, artifacts=["artifact://a1"], **finding_kwargs,
    )
    return task_id, contract, output


def test_submit_auditor_issues_id_and_evidence_chain_guard():
    """SPEC-5 §1.2：finding_id=`F-{date}-{seq:06d}` 库内同前缀自增（D20，单 run 独库
    与「run 内自增」逐位同值）；证据链 <3 节点或缺 source/sink/sanitizer →
    SchemaRejected 零落库（§2.5/SPEC-1 §4.2）。"""
    led = new_ledger()
    try:
        valid_chain = [
            EvidenceNode(kind="source", where_path="src/app/db.py", where_line=10,
                         ref="artifact://a1"),
            EvidenceNode(kind="propagation", where_path="src/app/db.py", where_line=12,
                         ref="artifact://a2"),
            EvidenceNode(kind="sink", where_path="src/app/db.py", where_line=20,
                         ref="artifact://a3"),
            EvidenceNode(kind="sanitizer", where_path="src/app/db.py", where_line=15,
                         found=False, ref="artifact://a4"),
        ]
        task_id, contract, output = _auditor_submit(led, chain=valid_chain)
        events = submit(led, task_id, output, contract=contract)
        assert [e.kind for e in events] == [LedgerKind.FINDING]
        fid = "F-20260101-000001"                          # 注入时钟当日 + 库内 max+1
        assert events[0].payload["finding_id"] == fid
        assert events[0].payload["state"] == "open"
        row = led.conn.execute(
            "SELECT id, reporter, reporter_role, family, type, lang, repo, path,"
            " line_start, line_end, func, commit_sha, severity, cwe, grade, state"
            " FROM findings WHERE id = ?", (fid,),
        ).fetchone()
        assert row == (
            fid, "A-auditor-1", "auditor", "injection", "sqli", "py",
            "https://example.invalid/repo",                # repo 缺省回落 runs.repo_url
            "src/app/db.py", 10, 20, "query", "0c0ffee",
            "high", "CWE-89", "L1", "open",
        )
        nodes = led.conn.execute(
            "SELECT id, ord, kind, found FROM evidence_nodes WHERE finding_id = ?"
            " ORDER BY ord", (fid,),
        ).fetchall()
        assert [n[0] for n in nodes] == [f"EV-{fid}-{i}" for i in range(1, 5)]
        assert [n[2] for n in nodes] == ["source", "propagation", "sink", "sanitizer"]
        assert nodes[3][3] == 0                            # sanitizer found=False 落库
        assert h.task_row(led, task_id)["state"] == "done"
        # 同日期前缀自增：第二个 auditor 任务签发 seq+1
        tid2, con2, out2 = _auditor_submit(led, chain=valid_chain)
        events2 = submit(led, tid2, out2, contract=con2)
        assert events2[0].payload["finding_id"] == "F-20260101-000002"
        # 证据链守卫：五类不合格 → SchemaRejected，零落库（任务留 dispatched）
        bad_chains = {
            "节点不足": valid_chain[:2],
            "缺 source": [c for c in valid_chain if c.kind != "source"],
            "缺 sink": [c for c in valid_chain if c.kind != "sink"],
            "缺 sanitizer": [c for c in valid_chain if c.kind != "sanitizer"],
            "kind 越词表": valid_chain[:2] + [
                EvidenceNode(kind="taint", where_path="p", where_line=1, ref="artifact://x")
            ],
        }
        for name, bad in bad_chains.items():
            tid, con, out = _auditor_submit(led)
            n_findings = len(led.read())
            with pytest.raises(SchemaRejected, match="证据链不合格"):
                submit(led, tid, out.model_copy(update={"evidence_chain": bad}),
                       contract=con)
            assert len(led.read()) == n_findings           # 零事件
            assert h.task_row(led, tid)["state"] == "dispatched"   # 退回不吞任务
            assert led.conn.execute(
                "SELECT COUNT(*) FROM findings"
            ).fetchone()[0] == 2                                   # 仅两例成功建档
    finally:
        led.close()


def test_submit_auditor_mismatch_auto_reroute():
    """SPEC-5 §1.2：产出 (family,type) 与契约节点错配 → 自动 re_route + 猜测格弱存款
    （§2.5/§3.1；细化不是错配 → 不触发，D12）。"""
    led = new_ledger()
    try:
        task_id, contract, output = _auditor_submit(led)
        ctx = VerdictContext(members=(), scouts=("A-scout-1", "A-scout-2"))
        events = submit(led, task_id, output.model_copy(
            update={"family": Family.CRYPTO, "type": None}          # 家族段不同 = 错配
        ), contract=contract, ctx=ctx)
        assert [e.kind for e in events] == [LedgerKind.FINDING, LedgerKind.RE_ROUTE,
                                            LedgerKind.DEPOSIT, LedgerKind.DEPOSIT]
        fid = events[0].payload["finding_id"]
        assert events[1].payload == {
            "finding_id": fid, "from_node": "injection/sqli",
            "to_node": "crypto", "reason": "type_mismatch",
        }
        # 弱存款标准形（D13）：每 scout 一笔 S1 / −1 / 0.05，src_seq=re_route seq
        for ev, scout in zip(events[2:], ("A-scout-1", "A-scout-2")):
            assert ev.payload["agent"] == scout
            assert ev.payload["role"] == "scout"
            assert ev.payload["signal_tier"] == "S1"
            assert ev.payload["direction"] == -1
            assert ev.payload["weight"] == 0.05
            assert ev.payload["taxonomy_path"] == ["injection", "sqli"]  # from_node 路径段
            assert ev.payload["origin"] == "prod"
            assert ev.payload["src_seq"] == events[1].seq
        assert h.task_row(led, task_id)["state"] == "done"
        # 家族级契约 + 更细类型产出 = 细化不是错配 → 只落 finding 事件
        tid2, con2, out2 = _auditor_submit(led, node="injection")
        events2 = submit(led, tid2, out2, contract=con2)          # injection → injection/sqli
        assert [e.kind for e in events2] == [LedgerKind.FINDING]
        assert len(led.read(kinds=(LedgerKind.RE_ROUTE,))) == 1   # 仅首例错配的一条
    finally:
        led.close()


def test_submit_reviewer_self_vote_rejected_and_auto_close_panel():
    """SPEC-5 §1.2：reviewer==reporter → ValueError（纪律 2）；票满 params.panel.SIZE
    自动 close_panel（§2.5/§4.4，缺省 SIZE=3）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        fid = h.seed_finding(led, "F-20260101-000001", reporter="A-auditor-1")
        h.seed_cluster(led, "C-20260101-000001", members=(fid,))
        led.conn.execute(
            "UPDATE findings SET cluster_id = ? WHERE id = ?",
            ("C-20260101-000001", fid),
        )
        led.conn.commit()
        vote = ReviewerVote(
            dim_completeness=0.9, dim_reachability=0.8, dim_exploitability=0.7,
            dim_fp_suspicion=0.2, direction=1, comment_ref="artifact://v1",
        )
        # 自评守卫：reviewer 即报告者 → ValueError，零落库
        tid_self = h.new_task(led, role="reviewer", node="injection/sqli",
                              shard={"finding_id": fid})
        dec_self = h.make_decision(tid_self, role="reviewer", assignee="A-auditor-1")
        con_self = build_contract(object(), dec_self, mode="pr", deadline_s=60)
        dispatch(led, tid_self, con_self, dec_self)
        n_events = len(led.read())
        with pytest.raises(ValueError, match="纪律 2"):
            submit(led, tid_self, vote, contract=con_self, ctx=VerdictContext(
                members=(MemberFacts(finding_id=fid, reporter="A-auditor-1",
                                     reporter_role="auditor"),),
            ))
        assert len(led.read()) == n_events
        assert led.conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0] == 0
        # 三票满员：前两票不收口（< SIZE=3），第三票自动 close_panel
        def make_vote_task(i: int):
            tid = h.new_task(led, role="reviewer", node="injection/sqli",
                             shard={"finding_id": fid})
            dec = h.make_decision(tid, role="reviewer", assignee=f"A-reviewer-{i}")
            con = build_contract(object(), dec, mode="pr", deadline_s=60)
            dispatch(led, tid, con, dec)
            return tid, con

        def ctx_with(n: int) -> VerdictContext:
            return VerdictContext(
                members=(MemberFacts(finding_id=fid, reporter="A-auditor-1",
                                     reporter_role="auditor"),),
                votes=tuple((f"A-reviewer-{j}", 1) for j in range(1, n + 1)),
            )

        tid1, con1 = make_vote_task(1)
        evs1 = submit(led, tid1, vote, contract=con1, ctx=ctx_with(1))
        assert [e.kind for e in evs1] == [LedgerKind.VOTE]
        assert h.task_row(led, tid1)["state"] == "done"
        assert h.finding_state(led, fid) == "open"          # 1 < SIZE 不收口
        tid2, con2 = make_vote_task(2)
        evs2 = submit(led, tid2, vote, contract=con2, ctx=ctx_with(2))
        assert [e.kind for e in evs2] == [LedgerKind.VOTE]
        assert h.finding_state(led, fid) == "open"          # 2 < SIZE 不收口
        # 第三票：vote + finding(voted) + S1 存款（报告者 + 评审回校）
        tid3, con3 = make_vote_task(3)
        evs3 = submit(led, tid3, vote, contract=con3, ctx=ctx_with(3))
        assert [e.kind for e in evs3] == [LedgerKind.VOTE, LedgerKind.FINDING,
                                          LedgerKind.DEPOSIT, LedgerKind.DEPOSIT,
                                          LedgerKind.DEPOSIT, LedgerKind.DEPOSIT]
        assert evs3[1].payload["state"] == "voted"
        assert h.task_row(led, tid3)["state"] == "done"
        vote_ids = [r[0] for r in led.conn.execute(
            "SELECT vote_id FROM votes ORDER BY vote_id"
        )]
        assert vote_ids == [f"V-{fid}-001", f"V-{fid}-002", f"V-{fid}-003"]
        (conf, disagg) = led.conn.execute(
            "SELECT confidence, disagreement_json FROM findings WHERE id = ?", (fid,)
        ).fetchone()
        assert conf == pytest.approx(0.65) and disagg == '{"disagreement": false}'
        deposits = [e.payload for e in evs3 if e.kind is LedgerKind.DEPOSIT]
        assert {d["agent"] for d in deposits} == {
            "A-auditor-1", "A-reviewer-1", "A-reviewer-2", "A-reviewer-3",
        }
        assert all(d["signal_tier"] == "S1" and d["direction"] == 1 for d in deposits)
        assert all(d["src_seq"] == evs3[1].seq for d in deposits)    # 源=finding 收口事件
        assert deposits[0]["weight"] == 0.05                          # S1_SOURCE_W × auditor
        # 幂等：对已 voted finding 重复提交 → close_panel 读侧幂等零新事件，仅 vote 事件
        tid4 = h.new_task(led, role="reviewer", node="injection/sqli",
                          shard={"finding_id": fid})
        dec4 = h.make_decision(tid4, role="reviewer", assignee="A-reviewer-4")
        con4 = build_contract(object(), dec4, mode="pr", deadline_s=60)
        dispatch(led, tid4, con4, dec4)
        n_events = len(led.read())
        submit(led, tid4, vote, contract=con4, ctx=VerdictContext(
            members=(MemberFacts(finding_id=fid, reporter="A-auditor-1",
                                 reporter_role="auditor"),),
            votes=tuple((f"A-reviewer-{j}", 1) for j in range(1, 5)),
        ))
        assert len(led.read()) == n_events + 1                        # 仅第 4 笔 vote 事件
        assert h.finding_state(led, fid) == "voted"
    finally:
        led.close()


def test_submit_verifier_fp_without_blocking_forced_verify_error():
    """SPEC-5 §1.2：§10.4 改判守卫——verified_fp 缺 blocking_evidence → 强制改判
    verify_error(error_kind=poc_fail)，零存款零投影（§2.4/§2.5）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        f1 = h.seed_finding(led, "F-20260101-000001", state="voted")
        f2 = h.seed_finding(led, "F-20260101-000002", reporter="A-auditor-2", state="voted")
        h.seed_cluster(led, "C-20260101-000001", members=(f1, f2))
        led.conn.execute(
            "UPDATE findings SET cluster_id = ? WHERE id IN (?, ?)",
            ("C-20260101-000001", f1, f2),
        )
        led.conn.commit()
        task_id = h.new_task(led, role="verifier", node="injection/sqli",
                             shard={"cluster_id": "C-20260101-000001"})
        decision = h.make_decision(task_id, role="verifier", assignee="A-verifier-1")
        contract = build_contract(object(), decision, mode="full", deadline_s=600)
        dispatch(led, task_id, contract, decision)
        report = VerifierReport(
            cluster_id="C-20260101-000001", outcome=VerifyOutcome.VERIFIED_FP,
            env_base="python:3.11", deps_ref="artifact://lock",
            trigger_path=["entry: views.py:88"],
            assertions=[Assertion(expect="500", observed=False, log="artifact://log")],
            blocking=None, error_kind=None, attempts=1, duration_s=1.5,
            logs_ref="artifact://run",
        )
        ctx = VerdictContext(
            members=(MemberFacts(finding_id=f1, reporter="A-auditor-1",
                                 reporter_role="auditor"),
                     MemberFacts(finding_id=f2, reporter="A-auditor-2",
                                 reporter_role="auditor")),
            verifier_id="A-verifier-1",
        )
        events = submit(led, task_id, report, contract=contract, ctx=ctx)
        # 改判后事件面：verify_result(verify_error) + 两成员 inconclusive，零存款
        assert [e.kind for e in events] == [LedgerKind.VERIFY_RESULT,
                                            LedgerKind.FINDING, LedgerKind.FINDING]
        assert events[0].payload == {
            "cluster_id": "C-20260101-000001",
            "outcome": "verify_error", "error_kind": "poc_fail",   # §10.4 强制改判
        }
        vr = led.conn.execute(
            "SELECT outcome, error_kind, blocking_kind, blocking_where, blocking_ref"
            " FROM verify_results WHERE id = 'VR-C-20260101-000001-1'"
        ).fetchone()
        assert vr == ("verify_error", "poc_fail", None, None, None)
        assert h.finding_state(led, f1) == "inconclusive"
        assert h.finding_state(led, f2) == "inconclusive"
        assert not led.read(kinds=(LedgerKind.DEPOSIT,))
        (vtier, vdir) = led.conn.execute(
            "SELECT verdict_tier, verdict_dir FROM clusters WHERE cluster_id = ?",
            ("C-20260101-000001",),
        ).fetchone()
        assert vtier is None and vdir is None            # 零投影更新（§6.2/§6.3）
        assert h.task_row(led, task_id)["state"] == "done"
    finally:
        led.close()


def test_submit_verifier_outcome_mapping_and_s2_deposit():
    """SPEC-5 §1.2：verified_real→verified+grade L3+tier=S2 dir=+1、verified_fp→
    rejected+dir=−1、verify_error→inconclusive 零存款零投影（§2.4 表）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        blocking = BlockingEvidence(kind=BlockingKind.SANITIZER, where="views.py:80",
                                    ref="artifact://block")

        # 三簇各两 voted 成员，分别走三种 outcome
        specs = []
        for i, cid in enumerate(("C-20260101-000001", "C-20260101-000002",
                                 "C-20260101-000003")):
            members = (
                h.seed_finding(led, f"F-20260101-{i}00001", state="voted"),
                h.seed_finding(led, f"F-20260101-{i}00002", reporter="A-auditor-2",
                               state="voted"),
            )
            h.seed_cluster(led, cid, members=members)
            led.conn.execute(
                "UPDATE findings SET cluster_id = ? WHERE id IN (?, ?)", (cid, *members)
            )
            specs.append((cid, members))
        led.conn.commit()

        def run_case(cid: str, members, outcome: VerifyOutcome, **kwargs):
            task_id = h.new_task(led, role="verifier", node="injection/sqli",
                                 shard={"cluster_id": cid})
            decision = h.make_decision(task_id, role="verifier", assignee="A-verifier-1")
            contract = build_contract(object(), decision, mode="full", deadline_s=600)
            dispatch(led, task_id, contract, decision)
            report = VerifierReport(
                cluster_id=cid, outcome=outcome, env_base="python:3.11",
                deps_ref="artifact://lock", trigger_path=["entry: views.py:88"],
                assertions=[Assertion(expect="500", observed=True, log="artifact://l")],
                attempts=1, duration_s=1.5, logs_ref="artifact://run", **kwargs,
            )
            ctx = VerdictContext(
                members=tuple(
                    MemberFacts(finding_id=m,
                                reporter="A-auditor-1" if k == 0 else "A-auditor-2",
                                reporter_role="auditor")
                    for k, m in enumerate(members)
                ),
                verifier_id="A-verifier-1",
            )
            return submit(led, task_id, report, contract=contract, ctx=ctx)

        # verified_real：成员 verified + grade→L3 + S2 正向存款 + verdict 投影
        cid, members = specs[0]
        events = run_case(cid, members, VerifyOutcome.VERIFIED_REAL)
        assert [e.kind for e in events] == [
            LedgerKind.VERIFY_RESULT, LedgerKind.FINDING, LedgerKind.FINDING,
            LedgerKind.DEPOSIT, LedgerKind.DEPOSIT,
        ]
        for m in members:
            assert h.finding_state(led, m) == "verified"
            (grade,) = led.conn.execute(
                "SELECT grade FROM findings WHERE id = ?", (m,)
            ).fetchone()
            assert grade == "L3"                                   # §10.1 结论升级
        deps = [e.payload for e in events if e.kind is LedgerKind.DEPOSIT]
        assert all(d["signal_tier"] == "S2" and d["direction"] == 1 for d in deps)
        assert all(d["weight"] == 0.5 for d in deps)               # S2_SOURCE_W × auditor
        assert all(d["src_seq"] == events[0].seq for d in deps)    # 源=verify_result 事件
        (vtier, vdir) = led.conn.execute(
            "SELECT verdict_tier, verdict_dir FROM clusters WHERE cluster_id = ?",
            (cid,),
        ).fetchone()
        assert vtier == "S2" and vdir == 1
        # verified_fp（携 blocking）：成员 rejected + S2 反向存款 + verdict 投影 −1
        cid, members = specs[1]
        events = run_case(cid, members, VerifyOutcome.VERIFIED_FP, blocking=blocking)
        assert [e.kind for e in events] == [
            LedgerKind.VERIFY_RESULT, LedgerKind.FINDING, LedgerKind.FINDING,
            LedgerKind.DEPOSIT, LedgerKind.DEPOSIT,
        ]
        for m in members:
            assert h.finding_state(led, m) == "rejected"
        deps = [e.payload for e in events if e.kind is LedgerKind.DEPOSIT]
        assert all(d["direction"] == -1 for d in deps)
        (vtier, vdir) = led.conn.execute(
            "SELECT verdict_tier, verdict_dir FROM clusters WHERE cluster_id = ?",
            (cid,),
        ).fetchone()
        assert vtier == "S2" and vdir == -1
        # verify_error：inconclusive 零存款零投影；error_kind 必填缺失 → SchemaRejected
        cid, members = specs[2]
        events = run_case(cid, members, VerifyOutcome.VERIFY_ERROR,
                          error_kind=VerifyErrorKind.ENV_BUILD_FAIL)
        assert [e.kind for e in events] == [
            LedgerKind.VERIFY_RESULT, LedgerKind.FINDING, LedgerKind.FINDING,
        ]
        for m in members:
            assert h.finding_state(led, m) == "inconclusive"
        assert not [e for e in events if e.kind is LedgerKind.DEPOSIT]
        (vtier, vdir) = led.conn.execute(
            "SELECT verdict_tier, verdict_dir FROM clusters WHERE cluster_id = ?",
            (cid,),
        ).fetchone()
        assert vtier is None and vdir is None
        with pytest.raises(SchemaRejected, match="error_kind"):
            run_case("C-20260101-000003", members, VerifyOutcome.VERIFY_ERROR)
    finally:
        led.close()


def test_submit_rejects_task_not_dispatched():
    """SPEC-5 §1.2：task 非 dispatched → ValueError（§2.5；queued 走 dispatch()，
    终态不可代落账），零副作用。"""
    led = new_ledger()
    try:
        task_id, contract, decision = full_setup(led)
        output = AuditorFinding(
            title="t", severity=Severity.HIGH, cwe=None, family=Family.INJECTION,
            type="sqli",
            evidence_chain=[
                EvidenceNode(kind="source", where_path="p", where_line=1, ref="a://1"),
                EvidenceNode(kind="propagation", where_path="p", where_line=2, ref="a://2"),
                EvidenceNode(kind="sink", where_path="p", where_line=3, ref="a://3"),
                EvidenceNode(kind="sanitizer", where_path="p", where_line=4,
                             found=False, ref="a://4"),
            ],
            confidence=0.5, artifacts=[],
        )
        n_events = len(led.read())
        with pytest.raises(ValueError, match="≠ dispatched"):
            submit(led, task_id, output, contract=contract)        # queued
        assert len(led.read()) == n_events
        dispatch(led, task_id, contract, decision)
        expire_run(led, "R-20260101-001")                          # dispatched → failed
        with pytest.raises(ValueError, match="≠ dispatched"):
            submit(led, task_id, output, contract=contract)        # failed 终态
        with pytest.raises(ValueError, match="任务不存在"):
            submit(led, "T-R-20260101-001-999", output, contract=contract)
        # 未知输出类型 → SchemaRejected（② 词表外）
        tid2 = h.new_task(led)
        dec2 = h.make_decision(tid2)
        con2 = build_contract(object(), dec2, mode="full", deadline_s=1)
        dispatch(led, tid2, con2, dec2)
        with pytest.raises(SchemaRejected, match="未知输出类型"):
            submit(led, tid2, object(), contract=con2)
        assert h.task_row(led, tid2)["state"] == "dispatched"
    finally:
        led.close()


def test_submit_verifier_vr_attempts_derived_and_terminal_member_skipped():
    """VR 行号派生 + 终态成员守卫（2026-10-20 修复）：

    - ``attempts`` = 同簇库内 MAX+1（D20 同型口径）——模型自报 attempts 不再作
      主键段：同簇第二次验证自报同值时撞主键抛 IntegrityError（非 ValueError，
      编排层 except 接不住、整编排崩）；
    - 簇内已被 S3 直答进终态（D8 voted→confirmed 边）的成员不阻断整笔验证——
      只流转迁移表合法成员，终态成员保持原判（append-only）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        f1 = h.seed_finding(led, "F-20260101-000001", state="voted")
        f2 = h.seed_finding(led, "F-20260101-000002", reporter="A-auditor-2",
                            state="confirmed")            # S3 直答终态
        h.seed_cluster(led, "C-20260101-000001", members=(f1, f2))
        led.conn.execute(
            "UPDATE findings SET cluster_id = ? WHERE id IN (?, ?)",
            ("C-20260101-000001", f1, f2),
        )
        led.conn.commit()
        ctx = VerdictContext(
            members=(
                MemberFacts(finding_id=f1, reporter="A-auditor-1", reporter_role="auditor"),
                MemberFacts(finding_id=f2, reporter="A-auditor-2", reporter_role="auditor"),
            ),
            verifier_id="A-verifier-1",
        )

        def _verify(outcome: VerifyOutcome, **kwargs):
            task_id = h.new_task(led, role="verifier", node="injection/sqli",
                                 shard={"cluster_id": "C-20260101-000001"})
            decision = h.make_decision(task_id, role="verifier", assignee="A-verifier-1")
            contract = build_contract(object(), decision, mode="full", deadline_s=600)
            dispatch(led, task_id, contract, decision)
            report = VerifierReport(
                cluster_id="C-20260101-000001", outcome=outcome,
                env_base="python:3.11", deps_ref="artifact://lock",
                trigger_path=[], assertions=[], attempts=1, duration_s=1.5,
                logs_ref="artifact://run", **kwargs,
            )
            return submit(led, task_id, report, contract=contract, ctx=ctx)

        # 第一次：verify_error——f1(voted→inconclusive 合法) 流转，f2 终态跳过
        events = _verify(VerifyOutcome.VERIFY_ERROR, error_kind=VerifyErrorKind.POC_FAIL)
        assert [e.kind for e in events] == [LedgerKind.VERIFY_RESULT, LedgerKind.FINDING]
        assert h.finding_state(led, f1) == "inconclusive"
        assert h.finding_state(led, f2) == "confirmed"   # 终态保持原判（append-only）
        # 第二次：verified_fp（自报 attempts 仍 1）——行号派生 VR-…-2 不撞主键；
        # f1 inconclusive→rejected 合法流转
        _verify(VerifyOutcome.VERIFIED_FP, blocking=BlockingEvidence(
            kind=BlockingKind.SANITIZER, where="views.py:80", ref="artifact://b"))
        assert h.finding_state(led, f1) == "rejected"
        rows = led.conn.execute(
            "SELECT id, attempts FROM verify_results"
            " WHERE cluster_id = 'C-20260101-000001' ORDER BY attempts"
        ).fetchall()
        assert rows == [("VR-C-20260101-000001-1", 1), ("VR-C-20260101-000001-2", 2)]
    finally:
        led.close()
