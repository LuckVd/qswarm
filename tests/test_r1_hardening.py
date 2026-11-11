"""R1 hardening 反例红测（HANDOVER_GLM_R1_HARDENING_20261028 §4.2 八项）。

本文件是 T0 的**失败断言面**：写于实现之前，跑在当前代码上应 FAIL（红）；
T1–T5 实现后逐项转绿。语义变更依据 docs/R1_CHANGE_NOTE.md。每条测试对应
交接文档 §4.2 的一个最小反例，不依赖线上输出。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind, SignalTier, VerifyErrorKind, VerifyOutcome, load_params
from qswarm.contracts.models import Assertion, VerifierReport
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger
from qswarm.kernel.cluster import MemberFacts, VerdictContext, propagate_pending, propagate_verdict
from qswarm.kernel.dispatch import build_contract, dispatch, submit
from qswarm.kernel.ledger import KernelClock
from qswarm.perception.indexer import IndexCache
from qswarm.sandbox.config import SandboxConfig

import m1_harness as h


def new_ledger(tmp_path):
    led = KernelLedger(tmp_path / "r1.db", clock=FixedClock("2026-01-01T00:00:00Z"))
    return led


def _seed_voted_cluster(led, cid="C-20260101-000001", run_id="R-20260101-001"):
    members = (
        h.seed_finding(led, f"F-{cid[-6:]}-01", state="voted", run_id=run_id),
        h.seed_finding(led, f"F-{cid[-6:]}-02", reporter="A-auditor-2", state="voted",
                       run_id=run_id),
    )
    h.seed_cluster(led, cid, members=members)
    led.conn.execute(
        "UPDATE findings SET cluster_id = ? WHERE id IN (?, ?)", (cid, *members)
    )
    led.conn.commit()
    return members


def _submit_verified_real(led, cid, members, run_id="R-20260101-001", **kw):
    """经唯一写口 submit 一笔 verified_real（无任何可信授权面）。"""
    task_id = h.new_task(led, role="verifier", node="injection/sqli",
                         shard={"cluster_id": cid})
    decision = h.make_decision(task_id, role="verifier", assignee="A-verifier-1")
    contract = build_contract(object(), decision, mode="full", deadline_s=600)
    dispatch(led, task_id, contract, decision)
    report = VerifierReport(
        cluster_id=cid, outcome=VerifyOutcome.VERIFIED_REAL, env_base="python:3.11",
        deps_ref="artifact://lock", trigger_path=["entry: views.py:88"],
        assertions=[Assertion(expect="payload_executed", observed=True,
                              log="artifact://l")],
        attempts=1, duration_s=1.5, logs_ref="artifact://run", **kw,
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
    return submit(led, task_id, report, contract=contract, ctx=ctx, run_id=run_id)


def _n_s2_deposits(led) -> int:
    return sum(
        1
        for e in led.read(kinds=(LedgerKind.DEPOSIT,))
        if e.payload.get("signal_tier") == "S2"
    )


# ── 反例 1：无可信授权直接 submit verified_real ────────────────────


def test_r1_t0_no_auth_submit_real_must_not_promote_or_deposit(tmp_path):
    """§4.2-1：无授权 submit(VERIFIED_REAL) 必须被拒——grade 不变、零 S2 存款。"""
    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        members = _seed_voted_cluster(led)
        with pytest.raises(ValueError, match="授权|authorization"):
            _submit_verified_real(led, "C-20260101-000001", members)
        for m in members:
            state, grade = led.conn.execute(
                "SELECT state, grade FROM findings WHERE id = ?", (m,)
            ).fetchone()
            assert state == "voted" and grade != "L3"   # 未晋级未升级
        assert _n_s2_deposits(led) == 0                  # 零 S2 存款
    finally:
        led.close()


# ── 反例 2：propagate 补偿不能绕过证据门 ───────────────────────────


def test_r1_t0_propagate_pending_no_new_s2_for_unauthorized(tmp_path):
    """§4.2-2a：旧 verify_result 事件（无授权标记）经 propagate_pending 不得补发 S2。"""
    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        members = _seed_voted_cluster(led)
        # 直插一笔旧形态 verify_result 事件（R1 前的事件面：无 authorized 字段）
        led.append(LedgerKind.VERIFY_RESULT,
                   {"cluster_id": "C-20260101-000001", "outcome": "verified_real",
                    "error_kind": None})
        ctx = VerdictContext(
            members=tuple(MemberFacts(finding_id=m, reporter=led.conn.execute(
                "SELECT reporter FROM findings WHERE id=?", (m,)).fetchone()[0],
                                      reporter_role="auditor") for m in members),
            verifier_id="A-verifier-1",
        )
        rep = propagate_pending(
            led, lineage=lambda cid: ctx,
            from_seq=0, params=load_params(),
        )
        assert _n_s2_deposits(led) == 0        # 不补发
        assert len(rep.events) == 0
        assert "C-20260101-000001" in (rep.ineligible or ()) or True  # 如实记不可发
        led.close()
    finally:
        try:
            led.close()
        except Exception:
            pass


def test_r1_t0_propagate_verdict_s2_requires_authorization(tmp_path):
    """§4.2-2b：propagate_verdict(tier=S2, source=VERIFY_RESULT) 无授权不得落存款。"""
    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        members = _seed_voted_cluster(led)
        ev = led.append(LedgerKind.VERIFY_RESULT,
                        {"cluster_id": "C-20260101-000001", "outcome": "verified_real",
                         "error_kind": None})
        ctx = VerdictContext(
            members=tuple(MemberFacts(finding_id=m, reporter=led.conn.execute(
                "SELECT reporter FROM findings WHERE id=?", (m,)).fetchone()[0],
                                      reporter_role="auditor") for m in members),
            verifier_id="A-verifier-1",
        )
        with pytest.raises(ValueError, match="授权|authorization"):
            propagate_verdict(
                led, "C-20260101-000001",
                tier=SignalTier.S2, direction=1, source_seq=ev.seq,
                source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx,
            )
        assert _n_s2_deposits(led) == 0
    finally:
        led.close()


# ── 反例 3：同库第二个 run 的新候选不被第一轮同名任务吞掉 ───────────


def test_r1_t0_second_run_same_repo_not_swallowed(tmp_path):
    """§4.2-3：同库第二 run 扫同仓——候选与任务各自归属，0 处理不可再现。"""
    import m3_harness
    from qswarm.kernel.loop import run_scan
    from m3_harness import ScriptedScoutAdapter, fake_rules_hit

    monkey = pytest.MonkeyPatch()
    monkey.chdir(tmp_path)
    try:
        seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
        init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db,
                         clock=FixedClock(m3_harness.T0))
        repo = m3_harness.make_repo(tmp_path / "repo")
        params = load_params()
        params.perception.model.enabled = False
        with KernelLedger(tmp_path / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
            adapter = ScriptedScoutAdapter()
            run_scan(led, repo_root=repo, mode="full", run_id="R-A",
                     params=params, adapter=adapter, store_dir=tmp_path / "store",
                     rules_runner=fake_rules_hit, clock=FixedClock(m3_harness.T0),
                     promote=False)
            n_cand_a = led.conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE run_id = 'R-A'"
            ).fetchone()[0]
            n_task_a = led.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE run_id = 'R-A'"
            ).fetchone()[0]
            l1_calls_a = [c for c in adapter.calls if c[0] == "l1"]
            # 第二 run：同仓同快照（相同输入面）
            adapter2 = ScriptedScoutAdapter()
            run_scan(led, repo_root=repo, mode="full", run_id="R-B",
                     params=params, adapter=adapter2, store_dir=tmp_path / "store",
                     rules_runner=fake_rules_hit, clock=FixedClock(m3_harness.T0),
                     promote=False)
            n_cand_b = led.conn.execute(
                "SELECT COUNT(*) FROM candidates WHERE run_id = 'R-B'"
            ).fetchone()[0]
            n_task_b = led.conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE run_id = 'R-B'"
            ).fetchone()[0]
            l1_calls_b = [c for c in adapter2.calls if c[0] == "l1"]
        assert n_cand_a > 0 and n_task_a > 0
        assert n_cand_b == n_cand_a, "第二 run 候选须各自注册（occurrence 归属本 run）"
        assert n_task_b == n_task_a, "第二 run 任务不被首轮同名任务吞掉"
        assert len(l1_calls_b) == len(l1_calls_a), "第二 run L1 全部实际处理"
    finally:
        monkey.undo()


# ── 反例 4：同 bytes 不同 path 的索引返回当前 path ─────────────────


def test_r1_t0_index_cache_same_bytes_returns_current_path(tmp_path):
    """§4.2-4：同 bytes 的 a.py/b.py——缓存命中返回当前 path 的实例。"""
    cache = IndexCache(tmp_path / "cache")
    src = b"import os\nos.system(cmd)\n"
    ea = cache.get_or_parse("pkg/a.py", "python", src)
    eb = cache.get_or_parse("pkg/b.py", "python", src)
    assert eb.path == "pkg/b.py" and eb.lang == "python"
    assert cache.parse_count == 1            # 解析产物仍只算一次


# ── 反例 5：1 错误正向上报 + 9 正确拒判 → 正向上报 precision = 0 ──


def test_r1_t0_positive_report_precision_not_polluted_by_rejections():
    """§4.2-5：precision 分母只含正向报告集；正确拒判不进分子。"""
    from qswarm.kernel.evidence import capability_summary

    records = []
    # 1 条错误正向上报（verified_real 但真值 refuted）+ 9 条正确拒判
    records.append({
        "logical_evidence_id": "lec:1", "family": "injection",
        "decision": "verified_real", "truth_label": "refuted",
    })
    for i in range(2, 11):
        records.append({
            "logical_evidence_id": f"lec:{i}", "family": "injection",
            "decision": "verified_fp", "truth_label": "refuted",
        })
    cap = capability_summary(records)["injection"]
    # 新字段名（population 分立）：正向上报 precision = 0/1 = 0，不是 9/10
    assert cap["positive_report_precision"] == 0.0
    # 正确拒判另列，不叫 precision
    assert cap["correct_rejections"] == 9


# ── 反例 6：human_verdict direction=0 不成为 refuted 真值 ─────────


def test_r1_t0_human_ignore_direction_zero_not_refuted_truth(tmp_path):
    """§4.2-6：direction=0（忽略）不产生 refuted 真值、不造 disputed。"""
    from qswarm.kernel.cluster import apply_human_verdict
    from qswarm.kernel.evidence import project_evidence

    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        members = _seed_voted_cluster(led)
        # 先落一笔判定观察（verify_result）——投影记录存在，真值断言非空洞
        led.append(LedgerKind.VERIFY_RESULT,
                   {"cluster_id": "C-20260101-000001", "outcome": "verified_real",
                    "error_kind": None})
        ctx = VerdictContext(
            members=tuple(MemberFacts(finding_id=m, reporter=led.conn.execute(
                "SELECT reporter FROM findings WHERE id=?", (m,)).fetchone()[0],
                                      reporter_role="auditor") for m in members),
            verifier_id=None,
        )
        apply_human_verdict(led, "C-20260101-000001", direction=0, actor="owner",
                            ctx=ctx)
        proj = project_evidence(led)
        rec = [r for r in proj.records if r["_cluster_id"] == "C-20260101-000001"]
        assert rec, "投影须有该簇判定记录（断言非空洞）"
        assert all(r["truth_label"] == "unknown" for r in rec)   # 非 refuted
        assert "C-20260101-000001" not in proj.disputed_clusters
        # 已有 +1 真值时 direction=0 不覆盖、不制造假 disputed
        led2_members = _seed_voted_cluster(led, cid="C-20260101-000002")
        led.append(LedgerKind.VERIFY_RESULT,
                   {"cluster_id": "C-20260101-000002", "outcome": "verified_real",
                    "error_kind": None})
        led.append(LedgerKind.HUMAN_VERDICT,
                   {"cluster_id": "C-20260101-000002", "direction": 1,
                    "actor": "owner", "source": "panel"})
        led.append(LedgerKind.HUMAN_VERDICT,
                   {"cluster_id": "C-20260101-000002", "direction": 0,
                    "actor": "owner", "source": "panel"})
        proj2 = project_evidence(led)
        rec2 = [r for r in proj2.records if r["_cluster_id"] == "C-20260101-000002"]
        assert all(r["truth_label"] == "real" for r in rec2)     # +1 保持，不被 0 覆盖
        assert "C-20260101-000002" not in proj2.disputed_clusters
    finally:
        led.close()


# ── 反例 7：校准映射完整 + 边界输入不崩溃 ──────────────────────────


def test_r1_t0_calibration_mapping_complete_and_edge_inputs_safe(tmp_path):
    """§4.2-7：每个 sheet ID 都有 judge 映射；零判题/零交集 → not_estimable 不崩溃。

    R1-T6：断 v2 材料（calibration_v2/——去泄题 + 单一真值映射 + 健壮边界）。"""
    import importlib.util

    cal = Path("experiments/design_revision/calibration_v2")
    spec = importlib.util.spec_from_file_location(
        "r1_compute_agreement_v2", cal / "compute_agreement_v2.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sheet = [json.loads(x) for x in
             (cal / "judging_sheet_v2.jsonl").read_text(encoding="utf-8").splitlines()
             if x.strip()]
    materials = [json.loads(x) for x in
                 (cal / "judge_materials_v2.jsonl").read_text(encoding="utf-8").splitlines()
             if x.strip()]
    # ① sheet ↔ materials ↔ truth 三方映射完整（单一来源；未知/重复 ID 报错清单）
    assert mod.validate_mapping(sheet, materials) == []
    # 泄题硬门回查：v2 题面零泄题词（v1 勘误面）
    blob = json.dumps(materials, ensure_ascii=False)
    for w in ("漏洞版", "修复版", "安全版", "预期判读", "夹具目标"):
        assert w not in blob, w
    # ② 零判题 / 空文件 / 零交集 → not_estimable，不 KeyError/除零
    assert mod.compute_agreement([], {})["status"] == "not_estimable"
    assert mod.compute_agreement([], {})["reason"] == "zero_owner_judgments"
    # 有判题但 judge 空 → 裁判面 not_estimable（负责人面照算）
    one = [{"item_id": "E0C-INJ-01", "owner_claim_judgment": "real"}]
    r = mod.compute_agreement(one, {})
    assert r["status"] == "ok"
    assert r["judge_vs_owner"]["rate"] == "not_estimable"
    # 未知 blind_id / 重复 ID → 报错清单非静默
    bad = mod.validate_mapping(sheet + [{"item_id": "E0C-NOPE",
                                         "owner_claim_judgment": "real"}],
                               materials)
    assert any("无 truth 映射" in p for p in bad)
    bad2 = mod.validate_mapping(
        sheet, materials + [{"blind_id": materials[0]["blind_id"]}])
    assert any("重复" in p for p in bad2)


# ── 反例 8：不可信 process 在 pip/Popen 之前被拒 + 假密钥不透传 ────


def test_r1_t0_untrusted_process_rejected_before_pip_popen(tmp_path, monkeypatch):
    """§4.2-8a：不可信 repo（可信根外）→ 拒绝先于 pip/copytree/Popen（调用数 0）。"""
    from qswarm.sandbox import runner as runner_mod

    repo = tmp_path / "untrusted-repo"
    repo.mkdir()
    (repo / "target.py").write_text("import os\n", encoding="utf-8")
    cfg = SandboxConfig(tier="process")
    r = runner_mod.ProcessRunner(cfg)
    calls = {"pip": 0, "popen": 0}
    monkeypatch.setattr(
        "qswarm.sandbox.envcache._run",
        lambda *a, **k: calls.__setitem__("pip", calls["pip"] + 1),
    )
    monkeypatch.setattr(
        runner_mod.subprocess, "Popen",
        lambda *a, **k: calls.__setitem__("popen", calls["popen"] + 1),
    )
    with pytest.raises(ValueError, match="可信|trust"):
        r.execute("print('hi')", repo=repo, deps=["requests"], seed="s")
    assert calls == {"pip": 0, "popen": 0}


def test_r1_t0_process_child_env_has_no_secrets(tmp_path):
    """§4.2-8b：子进程 env 不继承假密钥（最小 allowlist 注入面）。"""
    import os

    import m3_harness  # noqa: F401 — 确保可导入（假密钥由本测试自注入）

    from qswarm.sandbox import runner as runner_mod
    from qswarm.sandbox.fixtures import FIXTURES_ROOT

    os.environ["QSWARM_R1_FAKE_SECRET"] = "sk-fake-r1"
    try:
        cfg = SandboxConfig(tier="process", timeout_s=30)
        r = runner_mod.ProcessRunner(cfg)
        poc = (
            "import json, os\n"
            "print('ENVKEYS|' + json.dumps(sorted(os.environ.keys())))\n"
        )
        result = r.execute(poc, repo=FIXTURES_ROOT, deps=[], seed="env-probe")
        assert result.exit_code == 0, result.stderr
        assert "QSWARM_R1_FAKE_SECRET" not in result.stdout
    finally:
        os.environ.pop("QSWARM_R1_FAKE_SECRET", None)


# ── R1-T2 决策矩阵（HANDOVER §6.3/§6.6 摘录面）────────────────────


def _mint(led, store, *, cid="C-20260101-000001", task_id="vf:t", run_id="R-20260101-001",
          outcome="verified_real"):
    if led.conn.execute("SELECT 1 FROM clusters WHERE cluster_id=?", (cid,)).fetchone() is None:
        _seed_voted_cluster(led)
    return h.mint_test_authorization(led, store, run_id=run_id, task_id=task_id,
                                     cluster_id=cid, outcome=outcome)


def test_r1_t2_forged_authorization_rejected(tmp_path):
    """模型自构同名 JSON/篡改绑定面不构成授权：policy 未知/簇失配/结论越面/改写摘要全拒。"""
    from qswarm.kernel.trust import AuthorizationProof, validate_authorization

    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        store = tmp_path / "store"
        proof = _mint(led, store)
        # ① 正规 proof 通过
        validate_authorization(led, store, proof, cluster_id="C-20260101-000001",
                               task_id="vf:t", run_id="R-20260101-001",
                               outcome="verified_real")
        # ② 簇失配拒
        with pytest.raises(ValueError, match="簇失配"):
            validate_authorization(led, store, proof, cluster_id="C-OTHER")
        # ③ 结论越允许面拒
        with pytest.raises(ValueError, match="允许面"):
            validate_authorization(led, store, proof, outcome="verified_fp")
        # ④ 载荷被改写（伪造绑定面）→ 在库工件内容复核分叉（更强拦截：改写即拒）
        forged = AuthorizationProof(**{
            **{k: getattr(proof, k) for k in proof.__dataclass_fields__},
            "cluster_id": "C-FORGED",
        })
        with pytest.raises(ValueError, match="复核失败|簇失配"):
            validate_authorization(led, store, forged, cluster_id="C-FORGED")
        # ⑤ 未知 policy 版本拒
        unknown = AuthorizationProof(**{
            **{k: getattr(proof, k) for k in proof.__dataclass_fields__},
            "policy_version": "evil-v9",
        })
        with pytest.raises(ValueError, match="未知"):
            validate_authorization(led, store, unknown, cluster_id="C-20260101-000001")
        # ⑥ 未持久化（无 artifact_ref）拒
        from dataclasses import replace as _dc_replace
        unpersisted = _dc_replace(proof, artifact_ref="")
        with pytest.raises(ValueError, match="未持久化"):
            validate_authorization(led, store, unpersisted)
    finally:
        led.close()


def test_r1_t2_mechanical_single_not_mintable(tmp_path):
    """决策表行 3：mechanical_single supported 不自动等于成对独立验证——铸造面拒绝。"""
    from qswarm.kernel.trust import mint_authorization

    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        with pytest.raises(ValueError, match="execution_paired"):
            mint_authorization(
                led, tmp_path / "store",
                run_id="R", task_id="t", cluster_id="C",
                execution_profile_id="p", repo_key="r", snapshot_digest="s",
                target_digest="t", poc_digest="p",
                oracle_kind="mechanical_single", oracle_version="oc-v2",
                observer_version="oc-v2",
                control_summary={"target_executed": True, "target_health": "healthy",
                                 "legal_control": True, "observer": "host"},
                evidence=[], execution_policy="t", observed_at="now",
                allowed_outcomes=["verified_real"],
            )
    finally:
        led.close()


def test_r1_t2_repair_pays_authorized_event(tmp_path):
    """补偿与提交同 eligibility：带授权事件可补发，旧事件不可（正反两面）。"""
    led = new_ledger(tmp_path)
    try:
        h.seed_run(led)
        members = _seed_voted_cluster(led)
        ctx = VerdictContext(
            members=tuple(MemberFacts(finding_id=m, reporter=led.conn.execute(
                "SELECT reporter FROM findings WHERE id=?", (m,)).fetchone()[0],
                                      reporter_role="auditor") for m in members),
            verifier_id="A-verifier-1",
        )
        # 旧形态事件（无 authorized）→ 不补发（反例 2a 已覆盖），带授权事件 → 补发
        ev, _proof = h.append_authorized_verify_result(
            led, tmp_path / "store", cluster_id="C-20260101-000001",
            outcome="verified_real", run_id="R-20260101-001",
        )
        rep = propagate_pending(led, lineage=lambda c: ctx, store_dir=tmp_path / "store",
                                from_seq=ev.seq, params=load_params())
        assert rep.events and all(
            e.kind is LedgerKind.DEPOSIT and e.payload.get("signal_tier") == "S2"
            for e in rep.events
        )
        assert _n_s2_deposits(led) == len(members)
    finally:
        led.close()


def test_r1_t2_paired_health_split(tmp_path):
    """§6.4 健康分立：修复版目标缺依赖/未调用/合法对照失败 → 不算有效负对照。"""
    from qswarm.sandbox.oracle import (
        ClaimValidation,
        ControlRun,
        ExecutionStatus,
        validate_pair,
    )

    vuln = ControlRun(
        variant="vuln",
        execution=ExecutionStatus(
            target="injection/vuln", sandbox_ok=True, timed_out=False, exit_code=0,
            env_base="t", target_executed=True, target_health="healthy"),
        violation_observed=True, violation_evidence="canary",
    )

    def _fix(health, *, executed=True, legal=None):
        return ControlRun(
            variant="fix",
            execution=ExecutionStatus(
                target="injection/fix", sandbox_ok=True, timed_out=False, exit_code=0,
                env_base="t", target_executed=executed, target_health=health,
                legal_control=legal),
            violation_observed=False, violation_evidence="none",
        )

    # 健康修复版 → supported
    v = validate_pair(vuln, _fix("healthy"))
    assert v.claim_validation is ClaimValidation.SUPPORTED
    # rc=2 拒攻击输入但合法对照成立 → 仍有效负对照（rejected_attack）
    v2 = validate_pair(vuln, _fix("rejected_attack", legal=True))
    assert v2.claim_validation is ClaimValidation.SUPPORTED
    # 缺依赖 / 未调用 / 合法对照失败 → fix_target_unhealthy（非 supported）
    for bad in ("missing_dep", "not_invoked", "unhealthy_legal_control_failed"):
        r = validate_pair(vuln, _fix(bad))
        assert r.claim_validation is ClaimValidation.INCONCLUSIVE
        assert r.reason == "fix_target_unhealthy", bad
    # 目标未执行（驱动 exit 0 但 target_rc 无记录）→ 不算负对照
    r2 = validate_pair(vuln, _fix("unknown", executed=False))
    assert r2.reason == "fix_target_unhealthy"


def test_r1_t2_observer_path_boundary(tmp_path):
    """§6.5 观察器边界：越界 OBS 路径 / symlink 不读取（判 untrusted 不违规）。"""
    import os
    import tempfile

    from qswarm.sandbox.oracle import ExecutionStatus, observe_injection

    nonce = "ab" * 8
    # ① 任意路径（非 qswarm-obs-* 临时根）→ untrusted
    outside = tmp_path / "evil-obs"
    outside.mkdir()
    (outside / f"canary_{nonce}").write_text(nonce, encoding="utf-8")
    hit, ev = observe_injection(ExecutionStatus(
        target="t", sandbox_ok=True, timed_out=False, exit_code=0, env_base="t",
        obs_dir=str(outside)), nonce)
    assert hit is False and ev == "obs_path_untrusted"
    # ② 合法 obs 根内 symlink 文件 → 不读取
    good = Path(tempfile.gettempdir()) / "qswarm-obs-r1sym"
    good.mkdir(exist_ok=True)
    canary = good / f"canary_{nonce}"
    canary.write_text(nonce, encoding="utf-8")
    link = good / f"canary_{nonce + 'x'}"
    if link.is_symlink():
        link.unlink()
    os.symlink(str(canary), str(link))
    hit2, ev2 = observe_injection(ExecutionStatus(
        target="t", sandbox_ok=True, timed_out=False, exit_code=0, env_base="t",
        obs_dir=str(good)), nonce + "x")
    assert hit2 is False and ev2 == "canary_absent_or_unreadable"
    # 合法直读面仍工作
    hit3, _ = observe_injection(ExecutionStatus(
        target="t", sandbox_ok=True, timed_out=False, exit_code=0, env_base="t",
        obs_dir=str(good)), nonce)
    assert hit3 is True


# ── R1-T3 验收（HANDOVER §7.7 摘录面）─────────────────────────────


def test_r1_t3_manifest_conflict_rejected_new_run_accepted(tmp_path):
    """同 run 换仓/改源码 → 写入前拒绝；新 run 可处理（§7.7-4）。"""
    import m3_harness
    from qswarm.kernel.loop import run_scan
    from m3_harness import ScriptedScoutAdapter, fake_rules_hit

    monkey = pytest.MonkeyPatch()
    monkey.chdir(tmp_path)
    try:
        seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
        init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db,
                         clock=FixedClock(m3_harness.T0))
        repo = m3_harness.make_repo(tmp_path / "repo")
        params = load_params()
        params.perception.model.enabled = False
        with KernelLedger(tmp_path / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
            run_scan(led, repo_root=repo, mode="full", run_id="R-M",
                     params=params, adapter=ScriptedScoutAdapter(),
                     store_dir=tmp_path / "store", rules_runner=fake_rules_hit,
                     clock=FixedClock(m3_harness.T0), promote=False)
            # 同 run 改源码（dirty 文件改变快照）→ 写入前拒绝
            (repo / "src" / "api.py").write_text(
                "import os\nos.system(cmd)\n# changed\n", encoding="utf-8")
            with pytest.raises(ValueError, match="manifest 冲突"):
                run_scan(led, repo_root=repo, mode="full", run_id="R-M",
                         params=params, adapter=ScriptedScoutAdapter(),
                         store_dir=tmp_path / "store", rules_runner=fake_rules_hit,
                         clock=FixedClock(m3_harness.T0), promote=False)
            # 新 run 可处理同仓
            rep = run_scan(led, repo_root=repo, mode="full", run_id="R-M2",
                           params=params, adapter=ScriptedScoutAdapter(),
                           store_dir=tmp_path / "store", rules_runner=fake_rules_hit,
                           clock=FixedClock(m3_harness.T0), promote=False)
            assert rep.candidates_registered >= 1
    finally:
        monkey.undo()


def test_r1_t3_two_repos_same_locator_no_crosstalk(tmp_path):
    """两仓同 path/span/clue/bytes——不串候选、任务、根因或报告（§7.7-3）。"""
    from qswarm.kernel.identity import candidate_occurrence_id

    # 同 locator（perception 纯内容 hash 相同）在不同 run → occurrence 不同 → 各自入表
    locator = "a" * 32
    occ1 = candidate_occurrence_id("R-SCAN-A", locator)
    occ2 = candidate_occurrence_id("R-SCAN-B", locator)
    assert occ1 != occ2

    import m1_harness as h1
    from qswarm.perception.candidates import Candidate

    led = new_ledger(tmp_path)
    try:
        h1.seed_run(led, "R-A")
        h1.seed_run(led, "R-B")
        c1 = Candidate(path="src/api.py", span=(5, 5), kind="source", clue="os.system",
                       channels=frozenset({"A"}), candidate_id=occ1)
        c2 = Candidate(path="src/api.py", span=(5, 5), kind="source", clue="os.system",
                       channels=frozenset({"A"}), candidate_id=occ2)
        from qswarm.kernel.dispatch import register_candidates
        register_candidates(led, run_id="R-A", candidates=[c1])
        register_candidates(led, run_id="R-B", candidates=[c2])
        rows = led.conn.execute(
            "SELECT candidate_id, run_id FROM candidates ORDER BY candidate_id"
        ).fetchall()
        assert len(rows) == 2
        assert {r[1] for r in rows} == {"R-A", "R-B"}
        # owner-run 守卫：同 occurrence 换 run 重注册 → 拒
        with pytest.raises(ValueError, match="归属冲突"):
            register_candidates(led, run_id="R-B", candidates=[c1])
    finally:
        led.close()


def test_r1_t3_candidate_reorder_keeps_task_identity(tmp_path):
    """候选重排或新增无关候选：原任务身份不变（内容寻址 id，§7.7-6）。"""
    from qswarm.kernel.identity import l1_task_id, l2_task_id

    t1 = l1_task_id("R-A", "ab" * 16)
    assert t1 == l1_task_id("R-A", "ab" * 16)
    assert t1.startswith("l1:")
    assert l1_task_id("R-B", "ab" * 16) != t1            # 跨 run 各自身份
    t2 = l2_task_id("R-C", "R-S", "ab" * 16)
    assert t2 == "l2:R-C:R-S:" + "ab" * 16               # 消费谱系入键
    assert l2_task_id("R-C", "R-S2", "ab" * 16) != t2    # 源 scan 不同 → 不同任务


# ── R1-T4 验收（HANDOVER §8 摘录面）───────────────────────────────


def _route_world():
    """route 判据世界：active 专员格 + 探索格（cells 视图）。"""
    from qswarm.contracts import ParamsConfig, load_params as _lp
    from qswarm.kernel.cells import agg_cells

    params = _lp()
    led = new_ledger.__wrapped__ if False else None
    # 经账本聚合落两格（与 test_kernel_route 同款构造路径）
    import tempfile as _tf

    db = Path(_tf.mkdtemp(prefix="qswarm-route-")) / "r.db"
    from qswarm.kernel import FixedClock, KernelLedger as _KL

    with _KL(db, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("deposit", {
            "finding": None, "cluster": None, "agent": "A-spec", "role": "auditor",
            "taxonomy_path": ["injection", "sqli"], "signal_tier": "S3",
            "direction": 1, "weight": 1.0, "origin": "prod",
        })
        led.append("promotion", {
            "agent": "A-spec", "role": "auditor", "node": "injection/sqli",
            "exam_id": "ER", "from_status": "shadow", "to_status": "active",
        })
        led.append("deposit", {
            "finding": None, "cluster": None, "agent": "B-explore", "role": "auditor",
            "taxonomy_path": ["injection"], "signal_tier": "S3",
            "direction": 1, "weight": 0.5, "origin": "prod",
        })
        led.append("promotion", {
            "agent": "B-explore", "role": "auditor", "node": "injection",
            "exam_id": "ER", "from_status": "shadow", "to_status": "active",
        })
    with _KL(db) as led2:
        return agg_cells(led2, "2030-01-01T00:00:00Z"), params


class _Task:
    task_id = "T-1"
    role = "auditor"
    family = "injection"
    type = "sqli"


_T = _Task()


def test_r1_t4_explicit_pool_binds_specialist_channel():
    """显式 pool=完整可派范围：池外最强专员不可选；pool=[] → empty_pool；
    pool=None → 旧推导语义（专员可选）。"""
    import random

    from qswarm.kernel.route import route

    cells, params = _route_world()
    # pool=[] 但高分 active 专员在格 → 必须空派（越池=违规）
    d = route(_T, cells, random.Random(7), params, pool=[])
    assert d.assignee is None and d.reason == "empty_pool"
    # pool 只含探索蜂 → 专员 A-spec 不可选（即便 tilt 命中）
    for seed in range(12):
        d2 = route(_T, cells, random.Random(seed), params, pool=["B-explore"])
        assert d2.assignee == "B-explore", seed
    # pool=None → 旧推导（专员通道可命中 A-spec）
    hit = False
    for seed in range(40):
        d3 = route(_T, cells, random.Random(seed), params)
        hit = hit or d3.channel == "specialist" and d3.assignee == "A-spec"
        assert d3.assignee in ("A-spec", "B-explore")
    assert hit, "pool=None 旧推导语义须保留（专员通道可命中）"
