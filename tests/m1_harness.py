"""m1_harness：M1 dispatch/reroute 测试与黄金夹具的共享装配面（非 pytest 收集物）。

角色定位：本模块扮演 §4.5 意义上的 **harness**——run/finding 行的播种（run 生命
周期入口与 findings 写口均在 kernel 内；测试播种仅存在于 tests/，写口静态审计
只扫 ``qswarm/`` 包，见 qswarm.kernel.write_guard.AuditWritePorts 口径），任务行
一律经 :func:`qswarm.kernel.dispatch.create_task` 内核口装配。蜂与仿真器侧零直写
的纪律不受影响。

确定性：全部时间取注入时钟（缺省 FixedClock 整秒），行内容与事件序逐位可复现。
"""

from __future__ import annotations

import json

from qswarm.kernel import KernelLedger, create_task
from qswarm.kernel.ledger import iso_utc
from qswarm.kernel.route import RouteDecision

_DEFAULT_CLOCK_TS = "2026-01-01T00:00:00Z"


def seed_run(
    ledger: KernelLedger,
    run_id: str = "R-20260101-001",
    *,
    mode: str = "full",
    budget_cents: int = 600,
    spent_cents: int = 0,
    status: str = "running",
) -> str:
    """播种一条 runs 行（harness 装配面；见模块 docstring）。"""
    ledger.conn.execute(
        "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents, status,"
        " started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, mode, "https://example.invalid/repo", budget_cents, spent_cents, status, _DEFAULT_CLOCK_TS),
    )
    ledger.conn.commit()
    return run_id


def seed_finding(
    ledger: KernelLedger,
    finding_id: str,
    run_id: str = "R-20260101-001",
    *,
    reporter: str = "A-auditor-1",
    reporter_role: str = "auditor",
    family: str = "injection",
    type_: str | None = "sqli",
    lang: str = "py",
    path: str = "src/app/db.py",
    line_start: int = 10,
    line_end: int = 20,
    title: str = "tainted SQL query",
    severity: str = "high",
    cwe: str | None = "CWE-89",
    grade: str = "L1",
    state: str = "open",
    cluster_id: str | None = None,
) -> str:
    """播种一条 findings 行（harness 装配面，state 可指定任意 FindingState 值）。"""
    now = iso_utc(ledger._clock.now())
    ledger.conn.execute(
        "INSERT INTO findings (id, run_id, reporter, reporter_role, family, type, lang,"
        " repo, path, func, line_start, line_end, commit_sha, cluster_id, title, severity,"
        " cwe, confidence, grade, state, disagreement_json, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, NULL, ?, ?)",
        (
            finding_id, run_id, reporter, reporter_role, family, type_, lang,
            "https://example.invalid/repo", path, line_start, line_end, "0c0ffee",
            cluster_id, title, severity, cwe, grade, state, now, now,
        ),
    )
    ledger.conn.commit()
    return finding_id


def new_task(
    ledger: KernelLedger,
    run_id: str = "R-20260101-001",
    *,
    role: str = "auditor",
    node: str = "injection/sqli",
    shard: dict | None = None,
) -> str:
    """经内核唯一写口装配 queued 任务行（§6.1 tasks 写口 = kernel.dispatch）。"""
    return create_task(
        ledger, run_id=run_id, role=role, node=node,
        shard=shard if shard is not None else {"paths": ["src/app/db.py"]},
    )


def make_decision(
    task_id: str,
    *,
    role: str = "auditor",
    family: str = "injection",
    node: str = "injection/sqli",
    assignee: str | None = "A-auditor-1",
    channel: str = "explore",
    reason: str = "no_specs",
) -> RouteDecision:
    """构造确定性 RouteDecision（route() 纯函数的产物形状；M1 状态机不重跑路由）。"""
    return RouteDecision(
        task_id=task_id,
        role=role,
        family=family,
        node=node,
        assignee=assignee,
        channel=channel,
        reason=reason,
        tilt=None if reason == "no_specs" else 0.7,
        raw_tilt=None if reason == "no_specs" else 0.7,
        specs=(),
        spec_c_stars=(),
    )


def seed_candidate(
    ledger: KernelLedger,
    candidate_id: str,
    run_id: str = "R-20260101-001",
    *,
    path: str = "src/app/db.py",
    line_start: int = 10,
    line_end: int = 20,
    channels: str = "struct,rule",
    channel_score: float = 0.5,
    rule_id: str | None = None,
    guess_family: str | None = None,
    guess_type: str | None = None,
    triaged: int = 0,
) -> str:
    """播种一条 candidates 行（harness 装配面；candidates 内核写口在 submit(scout)）。"""
    ledger.conn.execute(
        "INSERT INTO candidates (candidate_id, run_id, path, line_start, line_end,"
        " channels, channel_score, rule_id, guess_family, guess_type, triaged, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidate_id, run_id, path, line_start, line_end, channels,
            channel_score, rule_id, guess_family, guess_type, triaged, _DEFAULT_CLOCK_TS,
        ),
    )
    ledger.conn.commit()
    return candidate_id


def seed_cluster(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    members: tuple[str, ...] = (),
    level: str = "coarse",
    key_path: str = "src/app/db.py",
    key_cwe: str | None = "CWE-89",
    key_lang: str = "py",
    line_window: str = "10-20",
) -> str:
    """播种一条 clusters 行 + 成员行（harness 装配面；成员 findings 行须先播种，
    库级 FK；clusters/cluster_members 内核写口在 kernel.cluster）。"""
    ledger.conn.execute(
        "INSERT INTO clusters (cluster_id, key_path, key_cwe, key_lang, line_window,"
        " level, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cluster_id, key_path, key_cwe, key_lang, line_window, level, _DEFAULT_CLOCK_TS),
    )
    for finding_id in members:
        ledger.conn.execute(
            "INSERT INTO cluster_members (cluster_id, finding_id, merged_at)"
            " VALUES (?, ?, ?)",
            (cluster_id, finding_id, _DEFAULT_CLOCK_TS),
        )
    ledger.conn.commit()
    return cluster_id


def candidate_row(ledger: KernelLedger, candidate_id: str) -> dict:
    row = ledger.conn.execute(
        "SELECT candidate_id, triaged, guess_family, guess_type, channel_score"
        " FROM candidates WHERE candidate_id = ?",
        (candidate_id,),
    ).fetchone()
    assert row is not None, f"候选点行缺失：{candidate_id}"
    keys = ("candidate_id", "triaged", "guess_family", "guess_type", "channel_score")
    return dict(zip(keys, row))


def task_row(ledger: KernelLedger, task_id: str) -> dict:
    row = ledger.conn.execute(
        "SELECT task_id, run_id, role, node, state, assignee, attempts, dispatched_at"
        " FROM tasks WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    assert row is not None, f"任务行缺失：{task_id}"
    keys = ("task_id", "run_id", "role", "node", "state", "assignee", "attempts", "dispatched_at")
    return dict(zip(keys, row))


def finding_state(ledger: KernelLedger, finding_id: str) -> str:
    (state,) = ledger.conn.execute(
        "SELECT state FROM findings WHERE id = ?", (finding_id,)
    ).fetchone()
    return state


def shard_json_of(ledger: KernelLedger, task_id: str) -> dict:
    (raw,) = ledger.conn.execute(
        "SELECT shard_json FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    return json.loads(raw)


__all__ = [
    "make_decision",
    "new_task",
    "seed_candidate",
    "seed_cluster",
    "seed_finding",
    "seed_run",
    "shard_json_of",
    "candidate_row",
    "finding_state",
    "task_row",
]

#: 供外部断言用的时间锚（FixedClock 缺省整秒时刻）。
DEFAULT_CLOCK_TS = _DEFAULT_CLOCK_TS


# ── R1-T2 测试面：可信授权铸造辅助（经 kernel.trust 正规铸造，非自构）────────


def mint_test_authorization(
    ledger,
    store_dir,
    *,
    cluster_id: str,
    task_id: str,
    run_id: str,
    outcome: str = "verified_real",
):
    """经 kernel.trust.mint_authorization 正规铸造测试授权（夹具成对判据面）。"""
    from pathlib import Path as _P

    from qswarm.kernel.trust import FIXTURE_SAFE_TARGET, mint_authorization
    from qswarm.kernel.dispatch import bind_verification_context
    from qswarm.kernel import FixedClock
    from qswarm.exams.store import artifact_put

    # Harness-only immutable artifacts do not consume the event clock or seq.
    clock = ledger._clock
    ledger._clock = FixedClock(_DEFAULT_CLOCK_TS)
    try:
        if ledger.conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone() is None:
            ledger.conn.execute(
                "INSERT INTO tasks (task_id,run_id,role,node,shard_json,state,assignee,created_at)"
                " VALUES (?,?,'verifier','injection',?,'dispatched','fixture-verifier',?)",
                (task_id, run_id, json.dumps({"cluster_id": cluster_id}), _DEFAULT_CLOCK_TS),
            )
            ledger.conn.commit()

        def put(content, kind):
            # Existing public kind vocabulary; purpose is separately proof-bound.
            artifact_kind = "poc_script" if kind in ("poc", "target") else "poc_log"
            return artifact_put(ledger, _P(store_dir), content=content,
                                mime="application/json", kind=artifact_kind, producer="offline-fixture")

        snapshot = put(json.dumps({"members": [r[0] for r in ledger.conn.execute(
            "SELECT finding_id FROM cluster_members WHERE cluster_id=? ORDER BY finding_id",
            (cluster_id,))]}, sort_keys=True).encode(), "snapshot_manifest")
        target = put(FIXTURE_SAFE_TARGET if outcome == "verified_fp" else b"offline vulnerable fixture v1", "target")
        poc = put(b"offline host driver v1", "poc")
        pair = put(b"offline fixed counterpart manifest v1", "pair_manifest")
        context = bind_verification_context(
            ledger, task_id, store_dir=_P(store_dir), cluster_id=cluster_id,
            execution_profile_id="fixture:profile", snapshot_ref=f"artifact://{snapshot}",
            target_ref=f"artifact://{target}", poc_ref=f"artifact://{poc}",
            pair_ref=f"artifact://{pair}", execution_policy="fixture:offline-mechanism-v1",
        )
        health = {"sandbox_ok": True, "timed_out": False, "target_executed": True,
                  "target_health": "healthy", "legal_control": True}
        summary = {"paired": True,
                   "vulnerable": {**health, "violation_observed": outcome == "verified_real"},
                   "fixed": {**health, "violation_observed": False}}
        observation = put(json.dumps({
            "context_digest": context.digest, "oracle_kind": "execution_paired",
            "oracle_version": "oc-v2", "observer_version": "trusted-fixture-v1",
            "control_summary": summary,
        }, sort_keys=True).encode(), "paired_observation")
        evidence = [(observation, "paired_observation")]
        if outcome == "verified_fp":
            refutation = put(json.dumps({
                "kind": "frozen-safe-target-v1", "context_digest": context.digest,
                "target_digest": context.target_digest,
            }, sort_keys=True).encode(), "independent_refutation")
            evidence.append((refutation, "independent_refutation"))
        return mint_authorization(
            ledger, _P(store_dir), context=context,
            run_id=run_id, task_id=task_id, cluster_id=cluster_id,
            execution_profile_id=context.execution_profile_id,
            repo_key=context.repo_key, snapshot_digest=context.snapshot_digest,
            target_digest=context.target_digest, poc_digest=context.poc_digest,
            oracle_kind="execution_paired", oracle_version="oc-v2",
            observer_version="trusted-fixture-v1", control_summary=summary,
            evidence=evidence, execution_policy=context.execution_policy,
            observed_at=_DEFAULT_CLOCK_TS, allowed_outcomes=[outcome],
        )
    finally:
        ledger._clock = clock


def append_authorized_verify_result(
    ledger,
    store_dir,
    *,
    cluster_id: str,
    outcome: str = "verified_real",
    run_id: str | None = None,
):
    """落一笔**带授权**的 verify_result 事件（S2 派生面测试的播种口）。

    授权工件（verify_auth）随铸造落库；事件载荷携带 authorized/auth_digest——
    与 kernel.dispatch._submit_verifier 的写入口径一致。"""
    from qswarm.contracts import LedgerKind
    from qswarm.contracts.models import VerifyResultPayload
    from qswarm.contracts.enums import VerifyErrorKind

    if run_id is None:
        row = ledger.conn.execute(
            "SELECT f.run_id FROM findings f JOIN cluster_members cm ON cm.finding_id=f.id"
            " WHERE cm.cluster_id=? ORDER BY f.id LIMIT 1", (cluster_id,),
        ).fetchone()
        if row is None:
            raise ValueError("fixture 需要实际簇成员/run")
        run_id = row[0]
    proof = mint_test_authorization(
        ledger, store_dir, cluster_id=cluster_id,
        task_id=f"vf:{cluster_id}:{ledger.latest_seq()}", run_id=run_id,
        outcome=outcome,
    )
    return ledger.append(
        LedgerKind.VERIFY_RESULT,
        VerifyResultPayload(
            cluster_id=cluster_id,
            outcome=outcome,
            error_kind=None if outcome != "verify_error" else VerifyErrorKind.POC_FAIL,
            authorized=True,
            auth_digest=proof.artifact_ref,
            task_id=proof.task_id,
            context_digest=proof.context_digest,
        ),
        run_id=run_id,
    ), proof
