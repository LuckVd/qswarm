"""kernel.verify 测试（SPEC-9 §4/§5；M5 卷 C/D 组判据）。

钉死：D43-a 双档落账口径（process 零账本写 / container 全链写口）、
quota_s2 verifier 实编（R7-①）、reconciliation 全分支（纯函数）、
已知答案比对（G2 前置离线试跑判据）、谱系回读（scouts/votes）、幂等选取面。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind, SignalTier, VerifyErrorKind, VerifyOutcome, load_params
from qswarm.contracts.models import (
    Assertion,
    BlockingEvidence,
    VerifierReport,
)
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger
from qswarm.kernel.consult import run_consult
from qswarm.kernel.loop import run_scan
from qswarm.kernel.verify import (
    PRODUCTION_TIER,
    VerifyRunReport,
    assert_production_tier,
    eligible_clusters,
    reconcile,
    run_verify,
)
from qswarm.sandbox import FakeRunner, SandboxResult, template_poc

import m3_harness
from m3_harness import ScriptedScoutAdapter, fake_rules_ok
from qswarm.exams.roster import attach_hat

T0 = m3_harness.T0

AUDITOR = "strong:anthropic/claude-sonnet-4-5@cv1"
SCOUT_A = "cheap:deepseek/deepseek-chat@cv1"
SCOUT_B = "cheap:openai/gpt-4o-mini@cv1"


# ── 夹具：consult 全链跑完的账本（voted 簇 + 预裁决单，M4 e2e 同款）──────


class ScriptedConsultAdapter:
    """auditor/reviewer/verifier 三面脚本化适配器（test_kernel_consult 同款）。"""

    def __init__(self, audit_script=None, vote_direction=1, pv_verdict="confirmed",
                 verify_response=None):
        self.audit_script = audit_script or {}
        self.vote_direction = vote_direction
        self.pv_verdict = pv_verdict
        self.verify_response = verify_response
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _finding(candidate_id):
        return {
            "title": f"test finding {candidate_id}",
            "severity": "high",
            "cwe": None,
            "family": "injection",
            "type": "sqli",
            "evidence_chain": [
                {"kind": "source", "where_path": "src/api.py", "where_line": 7,
                 "found": True, "ref": "artifact://pending"},
                {"kind": "propagation", "where_path": "src/api.py", "where_line": 8,
                 "found": True, "ref": "artifact://pending"},
                {"kind": "sanitizer", "where_path": "src/api.py", "where_line": 9,
                 "found": False, "ref": "artifact://pending"},
                {"kind": "sink", "where_path": "src/api.py", "where_line": 10,
                 "found": True, "ref": "artifact://pending"},
            ],
            "confidence": 0.8,
            "artifacts": ["artifact://pending"],
        }

    def complete(self, *, model_ref, messages, temperature=0.0, **_kw):
        system = messages[0].content
        user = messages[1].content
        if "审查蜂" in system:
            path = Path(user.split("path: ", 1)[1].split("\n", 1)[0]).name
            self.calls.append(("audit", path))
            scripted = self.audit_script.get(path)
            if scripted is None:
                return json.dumps(self._finding(path))
            return json.dumps(scripted)
        if "评审蜂" in system:
            ref = ""
            for line in user.splitlines():
                if line.startswith("finding_id: "):
                    ref = line.split(": ", 1)[1]
            self.calls.append(("vote", ref))
            return json.dumps({
                "dim_completeness": 0.8, "dim_reachability": 0.8,
                "dim_exploitability": 0.7, "dim_fp_suspicion": 0.2,
                "direction": self.vote_direction,
                "comment_ref": "artifact://pending",
            })
        if "验证蜂" in system and "真验证" not in user:
            self.calls.append(("pv", ""))
            return json.dumps({
                "cluster_id": user.split("簇 ", 1)[1].split("：", 1)[0].split(" ", 1)[0],
                "expected_verdict": self.pv_verdict,
                "poc_shape": "python subprocess with shell payload",
                "env_requirements": [],
            })
        if "验证蜂" in system:                    # S2 真验证（D39 双围栏一体产）
            self.calls.append(("verify", ""))
            if self.verify_response is not None:
                return self.verify_response
            poc = template_poc("injection", "sqli", path="src/api.py",
                               line_start=5, lang="python", seed="bee")
            report = {
                "cluster_id": user.split("vf:", 1)[-1].split("\n", 1)[0].strip()
                or "C-X",
                "outcome": "verified_real",
                "env_base": "python:3.11",
                "deps_ref": "artifact://pending",
                "trigger_path": [f"entry: src/api.py:5", "sink: src/api.py:8"],
                "assertions": [
                    {"expect": "payload_executed", "observed": True,
                     "log": "artifact://pending"}
                ],
                "blocking": None,
                "error_kind": None,
                "attempts": 1,
                "duration_s": 1.0,
                "logs_ref": "artifact://pending",
            }
            return (
                "```json\n" + json.dumps(report, ensure_ascii=False) + "\n```\n"
                "```python\n" + poc + "\n```\n"
            )
        raise AssertionError(f"未知会诊场景：{system[:40]}")


def _promote_auditor(led, node):
    led.append(
        "promotion",
        {"agent": AUDITOR, "role": "auditor", "node": node, "exam_id": "test:promo",
         "from_status": "shadow", "to_status": "active"},
    )


def _add_hats(led):
    attach_hat(led, SCOUT_A, "reviewer")
    attach_hat(led, SCOUT_B, "reviewer")
    attach_hat(led, AUDITOR, "reviewer")
    attach_hat(led, SCOUT_A, "verifier")


@pytest.fixture()
def consulted(tmp_path, monkeypatch):
    """就绪账本 + 小仓库 + consult 全链跑完（voted 簇 + 预裁决单工件在库）。"""
    monkeypatch.chdir(tmp_path)
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False
    params.tilt.T_BASE = 1.0
    params.tilt.T_MAX = 1.0
    with KernelLedger(tmp_path / "prod.db", clock=FixedClock(T0)) as led:
        run_scan(
            led, repo_root=repo, mode="full", run_id="R-SCAN",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp_path / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(T0), promote=False,
        )
        _add_hats(led)
        _promote_auditor(led, "injection")
        report = run_consult(
            led, repo_root=repo, mode="full", run_id="R-CONSULT", scan_run_id="R-SCAN",
            seed=11,
            adapter=ScriptedConsultAdapter(
                audit_script={"util.c": {"declined": True}},   # c 件干净（python 面聚焦）
            ),
            store_dir=tmp_path / "store", params=params, clock=FixedClock(T0),
            cache_dir=tmp_path / "cache", report_dir=tmp_path / "out-r",
        )
        assert report.findings_voted >= 1
        assert report.preverdicts >= 1
    return tmp_path, repo, params


def _ledger_state(led) -> dict:
    return {
        "events": [(e.kind.value, json.dumps(e.payload, sort_keys=True))
                   for e in led.read()],
        "tasks": led.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
        "vr": led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0],
        "cells": led.conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0],
    }


# ── 单元：选取面 / 谱系回读 / reconcile（纯函数）──────────────────


def test_assert_production_tier_gate():
    """D43-a 写口门（机器可检）：非 container 档触碰生产账本即 ValueError。"""
    assert_production_tier("container")
    with pytest.raises(ValueError, match="D43-a"):
        assert_production_tier("process")
    with pytest.raises(ValueError, match="D43-a"):
        assert_production_tier("fake")
    assert PRODUCTION_TIER == "container"


def test_reconcile_all_branches():
    """内核 owned reconciliation 全分支（SPEC-9 §4.5 数值钉）。"""
    R = reconcile
    ok = SandboxResult(tier="fake", ok=True, exit_code=0, timed_out=False,
                       duration_s=1.0, stdout="", stderr="", env_key="k",
                       env_base="b", workdir=None)
    red = SandboxResult(tier="fake", ok=False, exit_code=1, timed_out=False,
                        duration_s=1.0, stdout="", stderr="", env_key="k",
                        env_base="b", workdir=None)
    blocking = BlockingEvidence(kind="sanitizer", where="x", ref="artifact://a")
    VR, FP, ER = VerifyOutcome.VERIFIED_REAL, VerifyOutcome.VERIFIED_FP, VerifyOutcome.VERIFY_ERROR
    # 无 PoC 面
    assert R(ER, VerifyErrorKind.TIMEOUT, None, None, flaky=False, poc_available=False) == (
        ER, VerifyErrorKind.TIMEOUT)
    assert R(VR, None, None, None, flaky=False, poc_available=False) == (ER, VerifyErrorKind.POC_FAIL)
    # env / timeout 直接判
    envfail = SandboxResult(tier="fake", ok=False, exit_code=None, timed_out=False,
                            duration_s=1.0, stdout="", stderr="", env_key="",
                            env_base="b", workdir=None, error_kind="env_build_fail")
    assert R(VR, None, None, envfail, flaky=False, poc_available=True) == (
        ER, VerifyErrorKind.ENV_BUILD_FAIL)
    timeout = SandboxResult(tier="fake", ok=False, exit_code=None, timed_out=True,
                            duration_s=1.0, stdout="", stderr="", env_key="k",
                            env_base="b", workdir=None, error_kind="timeout")
    assert R(VR, None, None, timeout, flaky=False, poc_available=True) == (
        ER, VerifyErrorKind.TIMEOUT)
    # 执行绿：real 升格成立 / bee=None（试跑面）纯执行语义 / fp 不升格 / error 如实保留
    assert R(VR, None, None, ok, flaky=False, poc_available=True) == (VR, None)
    assert R(None, None, None, ok, flaky=False, poc_available=True) == (VR, None)
    assert R(FP, None, blocking, ok, flaky=False, poc_available=True) == (FP, None)
    assert R(ER, VerifyErrorKind.FLAKY, None, ok, flaky=False, poc_available=True) == (
        ER, VerifyErrorKind.FLAKY)
    # 执行红：fp+blocking 维持 / flaky / 稳定 poc_fail
    assert R(FP, None, blocking, red, flaky=False, poc_available=True) == (FP, None)
    assert R(VR, None, None, red, flaky=True, poc_available=True) == (ER, VerifyErrorKind.FLAKY)
    assert R(VR, None, None, red, flaky=False, poc_available=True) == (ER, VerifyErrorKind.POC_FAIL)
    # 执行红 + fp 无 blocking → poc_fail（submit 侧 §10.4 守卫兜底双保险）
    assert R(FP, None, None, red, flaky=False, poc_available=True) == (ER, VerifyErrorKind.POC_FAIL)


def test_eligible_clusters_selection_face(consulted):
    """选取面：全员 voted ∧ 无 verify_results 行；S2 后排除（幂等重跑面）。"""
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        clusters = eligible_clusters(led, consult_run_id="R-CONSULT")
        assert clusters
        for c in clusters:
            rows = led.conn.execute(
                "SELECT f.id, f.state FROM cluster_members cm JOIN findings f"
                " ON f.id = cm.finding_id WHERE cm.cluster_id = ?",
                (c["cluster_id"],),
            ).fetchall()
            states = {fid: state for fid, state in rows}
            assert set(states.values()) == {"voted"}
            # 谱系回读：S1 面板侦察行 / votes 表（M4 存款面回放）
            from qswarm.kernel.verify import _cluster_scouts, _cluster_votes

            scouts = _cluster_scouts(led, c["cluster_id"])
            votes = _cluster_votes(led, c["members"])
            assert isinstance(scouts, list) and isinstance(votes, list)


# ── D43-a：process 档零账本写（G2 前置离线试跑同款）────────────────


def test_process_tier_zero_ledger_writes_with_fake_runner(consulted):
    """process 档（FakeRunner 绿）：只产工件文件与报告——账本零新事件/零新行。"""
    tmp, repo, params = consulted
    runner = FakeRunner(script=True)              # 绿执行 + PASS 断言行
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        before = _ledger_state(led)
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-TRIAL", consult_run_id="R-CONSULT",
            adapter=None, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="process", runner=runner,
            report_dir=tmp / "out-v",
        )
        after = _ledger_state(led)
    assert after == before                        # D43-a：零账本写（机器可检）
    assert rep.tier == "process"
    assert rep.clusters_eligible >= 1 and rep.clusters_selected >= 1
    assert rep.verified_real == rep.clusters_selected
    for c in rep.detail:
        assert c["poc_source"] == "template"
        assert c["match"] is True                 # 已知答案比对（pv confirmed × 绿执行）
        assert c["expected_source"] == "pre_verdict"
    # 工件文件面（零索引）
    trial_dirs = list((tmp / "store" / "trial").iterdir())
    assert trial_dirs
    for d in trial_dirs:
        assert {p.name for p in d.iterdir()} == {"poc.py", "run.log", "result.json"}
    blob = json.loads((tmp / "out-v" / "verify_report.json").read_text())
    assert blob["tier"] == "process"
    assert blob["sandbox_disclosure"]["sandbox_runs"] >= 1


def test_process_tier_real_sandbox_known_answer(consulted):
    """G2 判据：process 档真沙箱（真子进程）——模板 PoC 对注入靶复现，
    已知答案（预裁决单 confirmed）比对命中；结论如实不外推。"""
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-REAL", consult_run_id="R-CONSULT",
            adapter=None, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="process",
        )
    assert rep.tier == "process"
    assert rep.verified_real >= 1                 # m3 仓 api.py run(cmd) 注入可复现
    assert all(c["match"] is True for c in rep.detail)
    assert rep.sandbox_wall_s > 0                 # R7-② 披露面在位
    # 零账本写（重申：真执行也不破 D43-a）
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        assert led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0] == 0


# ── container 档生产全链（FakeRunner + 脚本蜂；写口/映射/存款）──────


def test_container_tier_full_chain_verified_real(consulted):
    """container 档（D43-a 唯一生产口径）：蜂一体产 + 沙箱绿 → submit 全链——
    VR 行 + verify_result 事件 + §2.4 映射（verified→L3）+ S2 存款 ×0.5 + 任务 done。"""
    tmp, repo, params = consulted
    runner = FakeRunner(script=True)
    adapter = ScriptedConsultAdapter()
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-PROD", consult_run_id="R-CONSULT",
            adapter=adapter, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=runner,
            report_dir=tmp / "out-vp",
        )
        assert rep.tier == "container"
        assert rep.verified_real == rep.clusters_selected >= 1
        # 写口面（全走 dispatch：tasks/vote/verify_results）
        assert led.conn.execute(
            "SELECT COUNT(*) FROM verify_results").fetchone()[0] == rep.clusters_selected
        rows = led.conn.execute(
            "SELECT task_id, role, state FROM tasks WHERE task_id LIKE 'vf:%'"
        ).fetchall()
        assert len(rows) == rep.clusters_selected
        assert all(r[1] == "verifier" and r[2] == "done" for r in rows)
        # §2.4 映射：簇成员 verified + grade L3
        states = dict(led.conn.execute("SELECT id, state FROM findings").fetchall())
        grades = dict(led.conn.execute("SELECT id, grade FROM findings").fetchall())
        assert set(states.values()) == {"verified"}
        assert set(grades.values()) == {"L3"}
        # verify_result 事件 + S2 存款（×0.5 源权重：成员 0.25 / 侦察 0.05 / 评审 0.125）
        vr_events = [e for e in led.read(kinds=(LedgerKind.VERIFY_RESULT,))]
        assert len(vr_events) == rep.clusters_selected
        s2 = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
              if e.payload.get("signal_tier") == "S2" and e.run_id == "R-VERIFY-PROD"]
        assert s2
        expected = {"auditor": 0.5 * 1.0, "scout": 0.5 * 0.2, "reviewer": 0.5 * 0.5}
        for ev in s2:
            assert ev.payload["weight"] == pytest.approx(expected[ev.payload["role"]],
                                                         rel=1e-6)
        # 工件三件套（poc_deps/poc_log/poc_script）+ 断言 log 回填真实引用
        kinds = {r[0] for r in led.conn.execute(
            "SELECT DISTINCT kind FROM artifacts WHERE kind LIKE 'poc_%'")}
        assert kinds == {"poc_deps", "poc_log", "poc_script"}
        vr_row = led.conn.execute(
            "SELECT assertions_json, logs_ref FROM verify_results LIMIT 1").fetchone()
        assert vr_row[1].startswith("artifact://")
        for a in json.loads(vr_row[0]):
            assert a["log"].startswith("artifact://") and not a["log"].endswith("pending")
        # 预算面：on_cost 入账（R-VERIFY-PROD run）
        spent = led.conn.execute(
            "SELECT spent_cents FROM runs WHERE run_id = 'R-VERIFY-PROD'"
        ).fetchone()[0]
        assert spent == rep.clusters_selected * int(params.budget.COST_PER_S2_CENTS)
        # D39：蜂调用面发生（verify 场景）
        assert ("verify", "") in adapter.calls


def test_container_tier_env_build_fail_zero_deposits(consulted):
    """env 构建失败 → verify_error(env_build_fail)：inconclusive 映射、零存款。"""
    tmp, repo, params = consulted

    def fail_env(**kw):
        return SandboxResult(tier="fake", ok=False, exit_code=None, timed_out=False,
                             duration_s=0.5, stdout="", stderr="docker down",
                             env_key="", env_base="python:3.11", workdir=None,
                             error_kind="env_build_fail", log_text="docker down")

    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-ENVFAIL", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=FakeRunner(script=fail_env),
        )
        assert rep.verify_error == rep.clusters_selected >= 1
        assert rep.err_env_build_fail == rep.clusters_selected
        states = dict(led.conn.execute("SELECT id, state FROM findings").fetchall())
        assert set(states.values()) == {"inconclusive"}   # §6.2：复现失败 ≠ 误报
        s2 = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
              if e.payload.get("signal_tier") == "S2"]
        assert s2 == []                                    # verify_error 零存款
        kind = led.conn.execute(
            "SELECT error_kind FROM verify_results LIMIT 1").fetchone()[0]
        assert kind == "env_build_fail"


def test_container_tier_verified_fp_with_blocking(consulted):
    """verified_fp + blocking（§10.4）：rejected 映射 + S2 反向存款。"""
    tmp, repo, params = consulted
    blocking = {"kind": "sanitizer", "where": "src/api.py:9", "ref": "artifact://pending"}
    report = {
        "cluster_id": "C-X", "outcome": "verified_fp", "env_base": "python:3.11",
        "deps_ref": "artifact://pending", "trigger_path": [],
        "assertions": [{"expect": "sanitizer_holds", "observed": True,
                        "log": "artifact://pending"}],
        "blocking": blocking, "error_kind": None,
        "attempts": 1, "duration_s": 1.0, "logs_ref": "artifact://pending",
    }

    def red(**kw):
        return SandboxResult(tier="fake", ok=False, exit_code=1, timed_out=False,
                             duration_s=0.5, stdout="ASSERT|s|FAIL|x\n", stderr="",
                             env_key="k", env_base="python:3.11", workdir=None,
                             log_text="ASSERT|s|FAIL|x")

    response = ("```json\n" + json.dumps(report) + "\n```\n```python\nprint('x')\n```\n")
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-FP", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(verify_response=response),
            store_dir=tmp / "store", params=params, clock=FixedClock(T0),
            tier="container", runner=FakeRunner(script=red),
        )
        assert rep.verified_fp == rep.clusters_selected >= 1
        states = dict(led.conn.execute("SELECT id, state FROM findings").fetchall())
        assert set(states.values()) == {"rejected"}
        s2 = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
              if e.payload.get("signal_tier") == "S2"]
        assert s2 and all(ev.payload["direction"] == -1 for ev in s2)


def test_container_tier_quota_by_verifier_headcount(consulted):
    """R7-①：quota_s2 存活数 = verifier 实编——预算 9 cents（< 单次 10）× 1 蜂
    → 配额 0 → 全部 skipped_budget、零任务零 VR 行（容量不高估）。"""
    tmp, repo, params = consulted
    params.verify.budget_cents = 9
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-QUOTA", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=FakeRunner(script=True),
        )
        assert rep.clusters_eligible >= 1
        assert rep.skipped_budget == rep.clusters_selected
        assert led.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_id LIKE 'vf:%'").fetchone()[0] == 0
        assert led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0] == 0


def test_container_tier_no_verifier_failclosed(consulted):
    """无 active verifier → fail-closed 如实缺席（skipped_no_verifier），零硬凑。"""
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        led.conn.execute("DELETE FROM hats WHERE role = 'verifier'")
        led.conn.commit()
        rep = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-NOVER", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=FakeRunner(script=True),
        )
        assert rep.skipped_no_verifier == rep.clusters_eligible >= 1
        assert rep.clusters_selected == 0


def test_verify_idempotent_rerun(consulted):
    """幂等：首轮全验 → 二轮选取面为空（已验簇排除），账本零新 verify 行。"""
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        rep1 = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-R1", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=FakeRunner(script=True),
        )
        n_vr_1 = led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0]
        rep2 = run_verify(
            led, repo_root=repo, run_id="R-VERIFY-R2", consult_run_id="R-CONSULT",
            adapter=ScriptedConsultAdapter(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), tier="container", runner=FakeRunner(script=True),
        )
        n_vr_2 = led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0]
    assert rep1.clusters_selected >= 1
    assert rep2.clusters_eligible == 0 and rep2.clusters_selected == 0
    assert n_vr_1 == n_vr_2


def test_report_dataclass_defaults():
    """报告面默认值 + JSON 同构（落盘面契约）。"""
    rep = VerifyRunReport(run_id="R", tier="process")
    assert rep.to_dict()["funnel"]["clusters_eligible"] == 0
    assert "sandbox_disclosure" in rep.to_dict()
