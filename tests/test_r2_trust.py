"""R2 authorization: verified bytes, independently bound task context, S2 parity."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from qswarm.contracts import LedgerKind, SignalTier
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.cluster import propagate_verdict
from qswarm.kernel.trust import persist_proof, s2_source_authorized, validate_authorization
from tests import m1_harness as h


@pytest.fixture()
def world(tmp_path):
    with KernelLedger(tmp_path / "trust.db", clock=FixedClock("2026-01-01T00:00:00Z")) as led:
        run = h.seed_run(led)
        h.seed_finding(led, "F", run_id=run, cluster_id=None)
        h.seed_cluster(led, "C", members=("F",))
        store = tmp_path / "store"
        proof = h.mint_test_authorization(led, store, cluster_id="C", task_id="VF",
                                          run_id=run)
        yield led, store, proof


def test_missing_store_is_not_an_index_only_authorization(world):
    led, _store, proof = world
    with pytest.raises(ValueError):
        validate_authorization(led, None, proof)


@pytest.mark.parametrize("field,value", [
    ("policy_version", "r1-tv1"),
    ("oracle_kind", "mechanical_single"),
    ("oracle_version", "untrusted"),
    ("observer_version", "untrusted"),
    ("snapshot_digest", "different-snapshot"),
    ("execution_profile_id", "different-profile"),
    ("poc_digest", "different-poc"),
    ("control_summary", {"target_executed": False, "target_health": "unknown",
                         "legal_control": False, "observer": "model"}),
    ("evidence", ()),
    ("evidence", (("0" * 32, "paired_observation"),)),
])
def test_rehashed_proof_cannot_replace_trusted_context_or_policy(world, field, value):
    led, store, proof = world
    changed = persist_proof(led, store, replace(proof, **{field: value}, artifact_ref=""))
    with pytest.raises(ValueError):
        validate_authorization(led, store, changed)


def test_proof_content_must_equal_memory(world):
    led, store, proof = world
    changed = replace(proof, observed_at="2040-01-01T00:00:00Z")
    with pytest.raises(ValueError):
        validate_authorization(led, store, changed)


def test_model_blocking_never_authorizes_fp(world):
    led, store, proof = world
    fp = persist_proof(led, store, replace(proof, allowed_outcomes=("verified_fp",),
                                          artifact_ref=""))
    with pytest.raises(ValueError):
        validate_authorization(led, store, fp, outcome="verified_fp")


def test_source_requires_actual_verify_result_kind_and_store(world):
    led, store, proof = world
    payload = {"authorized": True, "auth_digest": proof.artifact_ref,
               "cluster_id": "C", "outcome": "verified_real", "task_id": "VF"}
    fake = SimpleNamespace(kind=LedgerKind.HUMAN_VERDICT, payload=payload, run_id=proof.run_id)
    assert not s2_source_authorized(led, store, fake)
    fake.kind = LedgerKind.VERIFY_RESULT
    assert not s2_source_authorized(led, None, fake)


def test_s2_cannot_use_vote_as_authorization(world):
    led, store, proof = world
    event = led.append(LedgerKind.VOTE, {"finding_id": "F", "reviewer": "r", "direction": 1},
                       run_id=proof.run_id)
    from qswarm.kernel.cluster import VerdictContext, MemberFacts
    ctx = VerdictContext(members=(MemberFacts("F", "a", "auditor"),),
                         scouts=(), votes=())
    before = led.latest_seq()
    with pytest.raises(ValueError):
        propagate_verdict(led, "C", tier=SignalTier.S2, direction=1,
                          source_seq=event.seq, source_kind=LedgerKind.VOTE, ctx=ctx)
    assert led.latest_seq() == before


def _source(led, proof, **overrides):
    payload = {"authorized": True, "auth_digest": proof.artifact_ref,
               "cluster_id": proof.cluster_id, "outcome": "verified_real",
               "task_id": proof.task_id, "context_digest": proof.context_digest}
    payload.update(overrides)
    return led.append(LedgerKind.VERIFY_RESULT, payload, run_id=proof.run_id)


def _lineage():
    from qswarm.kernel.cluster import VerdictContext, MemberFacts
    return VerdictContext(members=(MemberFacts("F", "A-auditor-1", "auditor"),))


def test_valid_source_repairs_only_with_explicit_store(world):
    from qswarm.kernel.cluster import propagate_pending
    led, store, proof = world
    event = _source(led, proof)
    assert s2_source_authorized(led, store, event, cluster_id="C", direction=1)
    assert not s2_source_authorized(led, store, event, cluster_id="OTHER", direction=1)
    assert not s2_source_authorized(led, store, event, cluster_id="C", direction=-1)
    before = led.latest_seq()
    denied = propagate_pending(led, lineage=lambda _c: _lineage())
    assert denied.events == () and denied.ineligible == ("C",)
    assert led.latest_seq() == before
    paid = propagate_pending(led, lineage=lambda _c: _lineage(), store_dir=store)
    assert len(paid.events) == 1
    assert propagate_pending(led, lineage=lambda _c: _lineage(), store_dir=store).events == ()


@pytest.mark.parametrize("overrides", [
    {"cluster_id": "OTHER"}, {"outcome": "verified_fp"}, {"outcome": "verify_error"},
    {"task_id": "OTHER"}, {"context_digest": "wrong"},
])
def test_source_payload_cannot_rebind_authorization(world, overrides):
    led, store, proof = world
    event = _source(led, proof, **overrides)
    assert not s2_source_authorized(led, store, event)
    before = led.latest_seq()
    with pytest.raises(ValueError):
        propagate_verdict(led, "C", tier=SignalTier.S2, direction=1, source_seq=event.seq,
                          source_kind=LedgerKind.VERIFY_RESULT, ctx=_lineage(), store_dir=store)
    assert led.latest_seq() == before


@pytest.mark.parametrize("ref_kind", ["proof", "evidence", "snapshot", "pair"])
def test_corrupt_actual_bytes_block_submit_and_repair(world, ref_kind):
    from qswarm.kernel.cluster import propagate_pending
    from qswarm.kernel.trust import load_verification_context
    led, store, proof = world
    event = _source(led, proof)
    context = load_verification_context(led, store, proof.task_id)
    refs = {"proof": proof.artifact_ref, "evidence": f"artifact://{proof.evidence[0][0]}",
            "snapshot": context.snapshot_ref, "pair": context.pair_ref}
    digest = refs[ref_kind].removeprefix("artifact://")
    path = store / digest[:2] / digest
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])  # equal size, wrong hash
    before = led.latest_seq()
    with pytest.raises(ValueError):
        validate_authorization(led, store, proof)
    assert not s2_source_authorized(led, store, event)
    repair = propagate_pending(led, store_dir=store, lineage=lambda _c: _lineage())
    assert repair.events == () and repair.ineligible == ("C",)
    assert led.latest_seq() == before


@pytest.mark.parametrize("column,value", [("assignee", "different-verifier"), ("run_id", "R-OTHER")])
def test_actual_task_identity_cannot_drift(world, column, value):
    led, store, proof = world
    if column == "run_id":
        h.seed_run(led, run_id=value)
    led.conn.execute(f"UPDATE tasks SET {column}=? WHERE task_id=?", (value, proof.task_id))
    led.conn.commit()
    with pytest.raises(ValueError):
        validate_authorization(led, store, proof)


def test_membership_change_invalidates_saved_context(world):
    led, store, proof = world
    h.seed_finding(led, "F-new", run_id=proof.run_id)
    led.conn.execute("INSERT INTO cluster_members VALUES ('C','F-new','2026-01-01')")
    led.conn.commit()
    with pytest.raises(ValueError):
        validate_authorization(led, store, proof)


def test_cannot_upgrade_verify_result_to_s3_or_retarget_human_verdict(world):
    led, store, proof = world
    source = _source(led, proof)
    human = led.append(LedgerKind.HUMAN_VERDICT,
                       {"cluster_id": "C", "direction": 1, "actor": "owner"})
    before = led.latest_seq()
    for event, cid, direction in ((source, "C", 1), (human, "OTHER", 1), (human, "C", -1)):
        with pytest.raises(ValueError):
            propagate_verdict(led, cid, tier=SignalTier.S3, direction=direction,
                              source_seq=event.seq, source_kind=event.kind, ctx=_lineage(),
                              store_dir=store)
        assert led.latest_seq() == before


def test_submit_store_and_run_binding_before_all_writes(world):
    from qswarm.contracts import VerifierReport
    from qswarm.kernel.dispatch import submit, build_contract
    led, store, proof = world
    decision = h.make_decision(proof.task_id, role="verifier", assignee="fixture-verifier")
    contract = build_contract(object(), decision, mode="full", deadline_s=60)
    output = VerifierReport(cluster_id="C", outcome="verified_real", env_base="fixture",
                            deps_ref="artifact://deps", trigger_path=[], assertions=[],
                            attempts=1, duration_s=1, logs_ref="artifact://logs")
    before = led.latest_seq()
    for kwargs in ({}, {"store_dir": store, "run_id": "OTHER"}):
        with pytest.raises(ValueError):
            submit(led, proof.task_id, output, contract=contract, ctx=_lineage(),
                   authorization=proof, **kwargs)
        assert led.latest_seq() == before
        assert led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0] == 0
    events = submit(led, proof.task_id, output, contract=contract, ctx=_lineage(),
                    authorization=proof, store_dir=store)
    assert events[0].payload["context_digest"] == proof.context_digest
    assert any(e.kind is LedgerKind.DEPOSIT for e in events)
