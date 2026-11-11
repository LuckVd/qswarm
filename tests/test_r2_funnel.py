"""R2 funnel regressions: historical isolation, identity and honest attrition."""

from __future__ import annotations

import sqlite3

import pytest

from qswarm.contracts import FindingState, LedgerKind
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.dispatch import open_finding, open_run, transition_finding
from qswarm.kernel.funnel import KnownItem, assemble_trace, first_loss_layer


@pytest.fixture()
def ledger(tmp_path):
    with KernelLedger(tmp_path / "funnel.db", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        open_run(led, run_id="R", mode="pr", repo_url="repo://x", commit_sha="abc")
        yield led


def finding(ledger, fid, *, family="injection", path="src/a.py"):
    return open_finding(
        ledger, finding_id=fid, run_id="R", reporter="bee", reporter_role="auditor",
        family=family, type=None, lang="python", repo="repo://x", path=path,
        line_start=1, line_end=2, title="t", severity="high",
    )


def stages(ledger, seq):
    return {r["stage"]: r for r in assemble_trace(ledger=ledger, as_of_seq=seq)}


def test_historical_counts_ignore_future_findings_and_state_changes(ledger):
    opened = finding(ledger, "F-old")
    before = stages(ledger, opened.seq)
    transition_finding(ledger, "F-old", FindingState.REJECTED)
    finding(ledger, "F-future")
    transition_finding(ledger, "F-future", FindingState.CONFIRMED)
    assert stages(ledger, opened.seq) == before
    assert before["review_rejected"]["reasons"]["findings_rejected"] == 0
    assert before["confirmed_reported"]["reasons"]["findings_confirmed"] == 0
    current = stages(ledger, ledger.latest_seq())
    assert current["review_rejected"]["reasons"]["findings_rejected"] == 1
    assert current["confirmed_reported"]["reasons"]["findings_confirmed"] == 1


def test_historical_counts_fold_latest_state_per_finding(ledger):
    finding(ledger, "F")
    confirmed = transition_finding(ledger, "F", FindingState.CONFIRMED)
    ledger.append(LedgerKind.FINDING, {
        "finding_id": "F", "reporter": "bee", "state": "confirmed", "cluster": None,
    }, run_id="R")
    assert stages(ledger, confirmed.seq)["confirmed_reported"]["out_count"] == 1
    assert stages(ledger, ledger.latest_seq())["confirmed_reported"]["out_count"] == 1


def test_historical_replay_does_not_enrich_identity_from_current_table(ledger):
    opened = finding(ledger, "F")
    item = KnownItem("K", "src/a.py", "injection")
    before = first_loss_layer([item], ledger, as_of_seq=opened.seq)
    transition_finding(ledger, "F", FindingState.REJECTED)
    finding(ledger, "future")
    assert first_loss_layer([item], ledger, as_of_seq=opened.seq) == before
    assert before[0]["first_loss_layer"] == "not_assessable"
    assert before[0]["assessable_layers"]["L2_unreported"] == "unknown"
    assert before[0]["evidence_refs"] == []


def test_historical_replay_before_creation_is_not_a_claim_of_l2_loss(ledger):
    cutoff = ledger.latest_seq()
    finding(ledger, "future")
    row = first_loss_layer([KnownItem("K", "src/a.py", "injection")],
                           ledger, as_of_seq=cutoff)[0]
    assert row["first_loss_layer"] == "not_assessable"
    assert row["assessable_layers"]["L2_unreported"] == "unknown"
    assert row["evidence_refs"] == []


def test_historical_queries_never_read_current_findings(ledger):
    opened = finding(ledger, "F")

    def deny_findings(action, table, _column, _database, _trigger):
        if action == sqlite3.SQLITE_READ and table == "findings":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    ledger.conn.set_authorizer(deny_findings)
    try:
        assert stages(ledger, opened.seq)["review_rejected"]["out_count"] == 0
        assert stages(ledger, 0)["confirmed_reported"]["out_count"] == 0
        row = first_loss_layer([KnownItem("K", "src/a.py", "injection")],
                               ledger, as_of_seq=opened.seq)[0]
        assert row["first_loss_layer"] == "not_assessable"
    finally:
        ledger.conn.set_authorizer(None)


def test_other_family_rejection_does_not_pollute_same_path(ledger):
    finding(ledger, "F-target")
    finding(ledger, "F-other", family="auth")
    transition_finding(ledger, "F-other", FindingState.REJECTED)
    row = first_loss_layer([KnownItem("K", "src/a.py", "injection")], ledger)[0]
    assert row["first_loss_layer"] == "s2_error_or_not_run"
    assert row["evidence_refs"] == ["F-target"]


def test_matching_family_rejection_remains_assessable_without_cutoff(ledger):
    finding(ledger, "F-target")
    transition_finding(ledger, "F-target", FindingState.REJECTED)
    row = first_loss_layer([KnownItem("K", "src/a.py", "injection")], ledger)[0]
    assert row["first_loss_layer"] == "review_rejected"
    assert row["evidence_refs"] == ["F-target"]


def test_family_and_path_both_required(ledger):
    finding(ledger, "F-other", family="auth")
    finding(ledger, "F-elsewhere", path="src/b.py")
    row = first_loss_layer([KnownItem("K", "src/a.py", "injection")], ledger)[0]
    assert row["first_loss_layer"] == "not_assessable"
    assert row["evidence_refs"] == []


def test_missing_family_does_not_mix_multiple_families(ledger):
    finding(ledger, "F-target")
    finding(ledger, "F-other", family="auth")
    transition_finding(ledger, "F-other", FindingState.REJECTED)
    row = first_loss_layer([KnownItem("K", "src/a.py")], ledger)[0]
    assert row["family"] == "unknown"
    assert row["first_loss_layer"] == "not_assessable"
    assert row["evidence_refs"] == []


@pytest.mark.parametrize("failures", [
    {"l2_quota_skipped": 4, "l2_route_empty_pool": 3},
    {"l2_model_ref_missing": 2, "l2_gate_revoked": 5},
    {},
])
def test_l1_does_not_invent_scout_decisions_from_dispatch_gap(failures):
    trace = assemble_trace(consult_report={
        "funnel": {"candidates_total": 10, "l2_dispatched": 3},
        "failures": failures,
    })
    l1 = next(r for r in trace if r["stage"] == "L1_discarded")
    assert l1["reasons"]["scout_not_suspicious"] == "unknown"
    assert l1["reasons"]["not_dispatched"] == 7
    for key, count in failures.items():
        assert l1["reasons"][key] == count
    assert l1["per_item_available"] is False
