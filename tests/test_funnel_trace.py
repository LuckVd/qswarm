"""漏斗 trace 与金标准回查测试（方案 §5；SPEC-10 §8；P1 工作包 C）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.dispatch import open_run
from qswarm.kernel.funnel import (
    STAGES,
    KnownItem,
    assemble_trace,
    first_loss_layer,
    write_trace,
)


@pytest.fixture()
def ledger(tmp_path):
    with KernelLedger(tmp_path / "led.db",
                      clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        open_run(led, run_id="R1", mode="pr", repo_url="repo://x",
                 commit_sha="deadbeef")
        yield led


SCAN = {"counts": {"files_total": 100, "files_scanned": 90,
                   "files_skipped_binary": 5},
        "channel_a_absent_langs": ["kotlin"], "artifact_ref": "art://l0"}
CONSULT = {"funnel": {"candidates_total": 40, "l2_dispatched": 12,
                      "l2_clean": 4, "findings_opened": 8},
           "failures": {"l2_schema_fail": 1, "l2_infra_fail": 0,
                        "l2_route_empty_pool": 0}}
VERIFY = {"funnel": {"skipped_budget": 2, "skipped_no_verifier": 1,
                     "gate_revoked_midrun": 0}}


def test_seven_stages_ordered_with_reasons(ledger):
    trace = assemble_trace(scan_report=SCAN, consult_report=CONSULT,
                           verify_report=VERIFY, ledger=ledger)
    assert tuple(r["stage"] for r in trace) == STAGES
    l0 = trace[0]
    assert l0["in_count"] == 100 and l0["out_count"] == 90
    assert l0["reasons"]["skipped_binary"] == 5
    assert l0["reasons"]["not_scanned_other"] == 5
    assert l0["reasons"]["channel_a_absent_langs"] == ["kotlin"]
    assert l0["selection_probability"] == "unknown"
    assert l0["per_item_available"] is False
    l1 = trace[1]
    assert l1["reasons"]["scout_not_suspicious"] == "unknown"
    assert l1["reasons"]["not_dispatched"] == 28  # 不能据派单缺口推断 scout 决策
    l2 = trace[2]
    assert l2["reasons"]["l2_clean"] == 4
    assert l2["reasons"]["l2_schema_fail"] == 1
    budget = next(r for r in trace if r["stage"] == "budget_not_run")
    assert budget["out_count"] == 3
    assert budget["reasons"]["gate_revoked_midrun"] == 0


def test_report_faces_optional_and_ordered():
    """报告缺面如实省略（不伪造层）；给面时层序稳定。"""
    trace = assemble_trace(scan_report=SCAN)
    assert [r["stage"] for r in trace] == ["L0_uncovered"]


def test_ledger_layers_count_votes_and_verdicts(ledger):
    ledger.conn.execute(
        "INSERT INTO clusters (cluster_id, key_path, key_lang, line_window,"
        " level, created_at) VALUES ('C-1','p','py','1-2','fine','2030-01-01')"
    )
    ledger.conn.execute(
        "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
        " lang, repo, path, line_start, line_end, commit_sha, title, severity,"
        " grade, state, created_at, updated_at, cluster_id)"
        " VALUES ('F-1','R1','bee-a','auditor','injection','python','repo://x',"
        " 'src/a.py', 1, 2, 'deadbeef', 't', 'high', 'L2', 'rejected',"
        " '2030-01-01', '2030-01-01', 'C-1')"
    )
    ledger.conn.commit()
    ledger.append(LedgerKind.VOTE,
                  {"finding_id": "F-1", "reviewer": "bee-r", "direction": -1},
                  run_id="R1")
    ledger.append(LedgerKind.VERIFY_RESULT,
                  {"cluster_id": "C-1", "outcome": "verify_error",
                   "error_kind": "timeout"}, run_id="R1")
    trace = assemble_trace(ledger=ledger)
    by = {r["stage"]: r for r in trace}
    assert by["review_rejected"]["reasons"]["findings_rejected"] == 1
    assert by["review_rejected"]["reasons"]["negative_votes"] == 1
    assert by["s2_error"]["reasons"] == {"timeout": 1}
    assert by["confirmed_reported"]["out_count"] == 0
    ledger.append(LedgerKind.HUMAN_VERDICT,
                  {"cluster_id": "C-1", "direction": 1, "actor": "owner"},
                  run_id="R1")
    trace2 = assemble_trace(ledger=ledger)
    assert {r["stage"]: r for r in trace2}["confirmed_reported"]["out_count"] == 1


def test_first_loss_layer_honest_without_finding(ledger):
    """无 finding：L2 后必失；L0/L1 逐条不可得 → not_assessable（不伪造）。"""
    rows = first_loss_layer([KnownItem("K-1", "src/missing.py", "injection")],
                            ledger)
    r = rows[0]
    assert r["first_loss_layer"] == "not_assessable"
    assert r["assessable_layers"]["L2_unreported"] == "lost"
    assert r["assessable_layers"]["L0_uncovered"] == "not_assessable"
    assert "不伪造" in r["note"]


def test_first_loss_layer_full_pass_and_s2_loss(ledger):
    """有 finding：S2 过+确认 → passed_all；S2 未过 → s2 层首失。"""
    for cid in ("C-1", "C-2"):
        ledger.conn.execute(
            "INSERT INTO clusters (cluster_id, key_path, key_lang, line_window,"
            " level, created_at) VALUES (?,'p','py','1-2','fine','2030-01-01')",
            (cid,),
        )
    for fid, path, cid in (("F-1", "src/a.py", "C-1"),
                           ("F-2", "src/b.py", "C-2")):
        ledger.conn.execute(
            "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
            " lang, repo, path, line_start, line_end, commit_sha, title,"
            " severity, grade, state, created_at, updated_at, cluster_id)"
            f" VALUES ('{fid}','R1','bee-a','auditor','injection','python',"
            f" 'repo://x','{path}', 1, 2, 'deadbeef', 't', 'high', 'L2',"
            f" 'voted', '2030-01-01', '2030-01-01', '{cid}')"
        )
    ledger.conn.commit()
    ledger.append(LedgerKind.VERIFY_RESULT,
                  {"cluster_id": "C-1", "outcome": "verified_real"}, run_id="R1")
    ledger.append(LedgerKind.HUMAN_VERDICT,
                  {"cluster_id": "C-1", "direction": 1, "actor": "owner"},
                  run_id="R1")
    rows = first_loss_layer(
        [KnownItem("K-1", "src/a.py"), KnownItem("K-2", "src/b.py")], ledger)
    by_path = {r["path"]: r for r in rows}
    assert by_path["src/a.py"]["first_loss_layer"] == "passed_all_layers"
    assert by_path["src/a.py"]["clusters"] == ["C-1"]

    # 未确认项：finding 在 C-2（无 S2/确认）→ S2 层首失（账本不可分如实）
    rows2 = first_loss_layer([KnownItem("K-3", "src/b.py")], ledger)
    assert rows2[0]["first_loss_layer"] == "s2_error_or_not_run"


def test_write_trace_artifacts(ledger, tmp_path):
    trace = assemble_trace(scan_report=SCAN, consult_report=CONSULT,
                           verify_report=VERIFY, ledger=ledger)
    replay = first_loss_layer([KnownItem("K-1", "src/x.py")], ledger)
    path = write_trace(trace, replay, tmp_path)
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(trace) + len(replay)
    kinds = [json.loads(x).get("stage") or json.loads(x).get("kind")
             for x in lines]
    assert "L0_uncovered" in kinds and "gold_replay" in kinds
