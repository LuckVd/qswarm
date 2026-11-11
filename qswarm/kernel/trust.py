"""R2 verified-byte authorization and host-bound verification context.

Only r2-tv1 can authorize new S2 writes. Historical events remain immutable. A
proof is not a trust anchor: its bytes, evidence, and task's independently saved
host context must all agree. Fixture refutation is an explicitly offline,
frozen-byte mechanism test, never production FP adjudication.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from qswarm.contracts import LedgerKind
from qswarm.kernel.ledger import KernelLedger

TRUST_POLICY_VERSION = "r2-tv1"
KNOWN_TRUST_POLICIES = frozenset({TRUST_POLICY_VERSION})
KNOWN_OBSERVERS = frozenset({"trusted-fixture-v1", "independent-container-v1"})
# No arbitrary model-supplied 'safe' target or blocking JSON is an FP oracle.
FIXTURE_SAFE_TARGET = b"# qswarm offline immutable safe target v1\ndef target(value):\n    return 'safe'\n"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _blake(obj) -> str:
    return hashlib.blake2b(_canon(obj).encode(), digest_size=16).hexdigest()


def _bytes(ledger, store_dir, ref, *, kind=None):
    from qswarm.exams.store import artifact_get_verified
    if store_dir is None or not isinstance(ref, str) or not ref.startswith("artifact://"):
        raise ValueError("可信授权需要显式 store_dir 和规范 artifact:// 引用")
    content = artifact_get_verified(ledger, Path(store_dir), ref[len("artifact://"):],
                                    expected_kind=kind, max_bytes=16 * 1024 * 1024)
    if content is None:
        raise ValueError(f"授权工件完整性核验失败：{ref}")
    return content


@dataclass(frozen=True)
class VerificationContext:
    """Saved by the host before minting, never reconstructed from a proof."""
    run_id: str
    task_id: str
    cluster_id: str
    assignee: str
    execution_profile_id: str
    repo_key: str
    run_commit: str
    snapshot_digest: str
    target_digest: str
    poc_digest: str
    member_ids: tuple[str, ...]
    execution_policy: str
    snapshot_ref: str
    target_ref: str
    poc_ref: str
    pair_ref: str
    pair_digest: str

    @property
    def digest(self):
        return _blake(asdict(self))

    @classmethod
    def from_dict(cls, value):
        value = dict(value)
        value["member_ids"] = tuple(value["member_ids"])
        return cls(**value)


def build_verification_context(ledger, store_dir, *, task_id, cluster_id,
                               execution_profile_id, snapshot_ref, target_ref,
                               poc_ref, pair_ref, execution_policy):
    """Host input artifacts + actual task/run/membership, not proof metadata.

    snapshot_ref is the actual manifest artifact; its SHA256 is computed once
    from its bytes (not from a hash string). pair_ref binds the fixed control
    target/manifest, so replacing a fixed counterpart invalidates authorization.
    """
    row = ledger.conn.execute(
        "SELECT t.run_id, t.role, t.assignee, r.repo_url, r.commit_sha, t.shard_json"
        " FROM tasks t JOIN runs r ON r.run_id=t.run_id WHERE t.task_id=?", (task_id,),
    ).fetchone()
    if row is None or row[1] != "verifier" or not row[2]:
        raise ValueError("verification context 需要实际 verifier task/assignee")
    shard = json.loads(row[5])
    if shard.get("cluster_id") != cluster_id:
        raise ValueError("verification context 与 task shard 簇失配")
    members = tuple(r[0] for r in ledger.conn.execute(
        "SELECT finding_id FROM cluster_members WHERE cluster_id=? ORDER BY finding_id",
        (cluster_id,),
    ))
    if not members or not execution_profile_id or not execution_policy:
        raise ValueError("verification context 缺实际成员/profile/policy")
    commit = row[4]
    if not commit:
        commits = {r[0] for r in ledger.conn.execute(
            "SELECT f.commit_sha FROM findings f JOIN cluster_members cm ON cm.finding_id=f.id"
            " WHERE cm.cluster_id=?", (cluster_id,),
        ) if r[0]}
        commit = next(iter(commits)) if len(commits) == 1 else "unknown"
    refs = (snapshot_ref, target_ref, poc_ref, pair_ref)
    digests = tuple(hashlib.sha256(_bytes(ledger, store_dir, ref)).hexdigest() for ref in refs)
    return VerificationContext(
        run_id=row[0], task_id=task_id, cluster_id=cluster_id, assignee=row[2],
        execution_profile_id=execution_profile_id, repo_key=row[3], run_commit=commit,
        snapshot_digest=digests[0], target_digest=digests[1], poc_digest=digests[2],
        member_ids=members, execution_policy=execution_policy,
        snapshot_ref=snapshot_ref, target_ref=target_ref, poc_ref=poc_ref,
        pair_ref=pair_ref, pair_digest=digests[3],
    )


def load_verification_context(ledger, store_dir, task_id):
    row = ledger.conn.execute("SELECT shard_json FROM tasks WHERE task_id=?", (task_id,)).fetchone()
    if row is None:
        raise ValueError("verification context task 不存在")
    try:
        shard = json.loads(row[0])
        saved = VerificationContext.from_dict(shard["verification_context"])
        if saved.digest != shard["verification_context_digest"] or saved.task_id != task_id:
            raise ValueError("verification context 摘要/task 失配")
        actual = build_verification_context(
            ledger, store_dir, task_id=task_id, cluster_id=saved.cluster_id,
            execution_profile_id=saved.execution_profile_id,
            snapshot_ref=saved.snapshot_ref, target_ref=saved.target_ref,
            poc_ref=saved.poc_ref, pair_ref=saved.pair_ref,
            execution_policy=saved.execution_policy,
        )
        if saved != actual:
            raise ValueError("verification context 与实际宿主记录失配")
        return actual
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("缺失/损坏的宿主 verification context") from exc


@dataclass(frozen=True)
class AuthorizationProof:
    policy_version: str
    run_id: str
    task_id: str
    cluster_id: str
    execution_profile_id: str
    repo_key: str
    snapshot_digest: str
    target_digest: str
    poc_digest: str
    oracle_kind: str
    oracle_version: str
    observer_version: str
    control_summary: dict = field(default_factory=dict)
    evidence: tuple[tuple[str, str], ...] = ()
    execution_policy: str = ""
    observed_at: str = ""
    allowed_outcomes: tuple[str, ...] = ()
    artifact_ref: str = ""
    context_digest: str = ""

    @property
    def digest(self):
        data = asdict(self)
        data.pop("artifact_ref")
        return _blake(data)

    def to_json(self):
        return _canon({**asdict(self), "digest": self.digest})

    @staticmethod
    def from_json(raw):
        data = json.loads(raw)
        digest = data.pop("digest")
        data["evidence"] = tuple(tuple(e) for e in data.get("evidence", ()))
        data["allowed_outcomes"] = tuple(data.get("allowed_outcomes", ()))
        proof = AuthorizationProof(**data)
        if proof.digest != digest:
            raise ValueError("AuthorizationProof 摘要不匹配")
        return proof


def persist_proof(ledger, store_dir, proof):
    from qswarm.exams.store import artifact_put
    if store_dir is None:
        raise ValueError("授权持久化需要显式 store_dir")
    proof = replace(proof, artifact_ref="")
    h = artifact_put(ledger, Path(store_dir), content=proof.to_json().encode(),
                     mime="application/json", kind="verify_auth", producer=f"trust:{proof.cluster_id}")
    return replace(proof, artifact_ref=f"artifact://{h}")


def load_proof(ledger, store_dir, artifact_ref):
    try:
        content = _bytes(ledger, store_dir, artifact_ref, kind="verify_auth")
        proof = AuthorizationProof.from_json(content.decode())
        return replace(proof, artifact_ref=artifact_ref)
    except (ValueError, KeyError, TypeError, UnicodeError, OSError):
        return None


def _healthy(control, *, fixed=False):
    return (isinstance(control, dict)
            and control.get("sandbox_ok") is True
            and control.get("timed_out") is False
            and control.get("target_executed") is True
            and control.get("target_health") in (("healthy", "rejected_attack") if fixed else ("healthy",))
            and control.get("legal_control") is True)


def _validate_semantics(ledger, store_dir, proof, context):
    if proof.policy_version not in KNOWN_TRUST_POLICIES:
        raise ValueError("授权 policy_version 未知/旧版，不可静默升级")
    for key in ("run_id", "task_id", "cluster_id", "execution_profile_id", "repo_key",
                "snapshot_digest", "target_digest", "poc_digest", "execution_policy"):
        if getattr(proof, key) != getattr(context, key):
            raise ValueError(f"授权 {key} 与宿主 context 失配")
    if proof.context_digest != context.digest:
        raise ValueError("授权 context_digest 失配")
    if (proof.oracle_kind != "execution_paired" or proof.oracle_version != "oc-v2"
            or proof.observer_version not in KNOWN_OBSERVERS):
        raise ValueError("授权 oracle/observer 不在策略白名单")
    fixture = proof.observer_version == "trusted-fixture-v1"
    if fixture and not context.execution_policy.startswith("fixture:"):
        raise ValueError("trusted-fixture observer 仅限明确离线 fixture policy")
    if not fixture and not context.execution_policy.startswith("container:"):
        raise ValueError("独立 container observer 不能授权 process/fixture 执行")
    if not proof.observed_at or not proof.allowed_outcomes or set(proof.allowed_outcomes) - {"verified_real", "verified_fp"}:
        raise ValueError("授权允许面/观察时间缺失或非法")
    evidence = {}
    for entry in proof.evidence:
        if len(entry) != 2:
            raise ValueError("授权 evidence 条目格式错误")
        h, purpose = entry
        if purpose in evidence or not purpose:
            raise ValueError("授权 evidence 用途重复或为空")
        evidence[purpose] = _bytes(ledger, store_dir, f"artifact://{h}")
    if "paired_observation" not in evidence:
        raise ValueError("授权缺独立成对 observation evidence")
    try:
        observation = json.loads(evidence["paired_observation"])
        summary = observation["control_summary"]
        if (observation["context_digest"] != context.digest
                or observation["oracle_kind"] != proof.oracle_kind
                or observation["oracle_version"] != proof.oracle_version
                or observation["observer_version"] != proof.observer_version
                or summary != proof.control_summary
                or summary.get("paired") is not True
                or not _healthy(summary.get("vulnerable"))
                or not _healthy(summary.get("fixed"), fixed=True)):
            raise ValueError("配对观察内容/健康值/context 不匹配")
        if "verified_real" in proof.allowed_outcomes:
            if (summary["vulnerable"].get("violation_observed") is not True
                    or summary["fixed"].get("violation_observed") is not False):
                raise ValueError("配对观察不支持 verified_real")
        if "verified_fp" in proof.allowed_outcomes:
            # Production independent refutation has no implemented verifier yet.
            # The only implemented negative oracle is this exact offline target.
            refutation = json.loads(evidence.get("independent_refutation", b"null"))
            if (not fixture
                    or _bytes(ledger, store_dir, context.target_ref) != FIXTURE_SAFE_TARGET
                    or not isinstance(refutation, dict)
                    or refutation != {"kind": "frozen-safe-target-v1", "context_digest": context.digest,
                                      "target_digest": hashlib.sha256(FIXTURE_SAFE_TARGET).hexdigest()}):
                raise ValueError("verified_fp 缺独立 refutation；模型 blocking 不构成授权")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("配对/反证 evidence 内容无效") from exc


def mint_authorization(ledger, store_dir, *, run_id, task_id, cluster_id,
                       execution_profile_id, repo_key, snapshot_digest, target_digest,
                       poc_digest, oracle_kind, oracle_version, observer_version,
                       control_summary, evidence, execution_policy, observed_at,
                       allowed_outcomes, context=None):
    if oracle_kind != "execution_paired":
        raise ValueError("授权仅接受 execution_paired 判据")
    actual = load_verification_context(ledger, store_dir, task_id)
    if context is not None and context != actual:
        raise ValueError("调用方 context 与宿主绑定不符")
    proof = AuthorizationProof(
        policy_version=TRUST_POLICY_VERSION, run_id=run_id, task_id=task_id,
        cluster_id=cluster_id, execution_profile_id=execution_profile_id,
        repo_key=repo_key, snapshot_digest=snapshot_digest, target_digest=target_digest,
        poc_digest=poc_digest, oracle_kind=oracle_kind, oracle_version=oracle_version,
        observer_version=observer_version, control_summary=dict(control_summary),
        evidence=tuple(tuple(e) for e in evidence), execution_policy=execution_policy,
        observed_at=observed_at, allowed_outcomes=tuple(allowed_outcomes),
        context_digest=actual.digest,
    )
    _validate_semantics(ledger, store_dir, proof, actual)
    proof = persist_proof(ledger, store_dir, proof)
    from .dispatch import _seal_verification_authorization
    _seal_verification_authorization(ledger, proof)
    return proof


def validate_authorization(ledger, store_dir, proof, *, run_id=None, task_id=None,
                           cluster_id=None, outcome=None, context=None):
    if store_dir is None or proof is None:
        raise ValueError("高可信结论需要可信授权及显式 store_dir")
    if not isinstance(proof, AuthorizationProof):
        raise ValueError("高可信结论需要 AuthorizationProof 而非模型 JSON")
    if proof.policy_version not in KNOWN_TRUST_POLICIES:
        raise ValueError("授权 policy_version 未知/旧版")
    if not proof.artifact_ref:
        raise ValueError("授权未持久化")
    for key, expected in (("run_id", run_id), ("task_id", task_id), ("cluster_id", cluster_id)):
        if expected is not None and getattr(proof, key) != expected:
            label = "簇失配" if key == "cluster_id" else f"{key} 失配"
            raise ValueError(f"授权 {label}")
    loaded = load_proof(ledger, store_dir, proof.artifact_ref)
    if loaded is None or loaded != proof:
        raise ValueError("授权工件内容复核失败（缺件或载荷分叉）")
    actual = load_verification_context(ledger, store_dir, proof.task_id)
    shard = json.loads(ledger.conn.execute(
        "SELECT shard_json FROM tasks WHERE task_id=?", (proof.task_id,),
    ).fetchone()[0])
    if shard.get("verification_authorization") != {"artifact_ref": proof.artifact_ref,
                                                    "digest": proof.digest}:
        raise ValueError("授权未由受信 mint 为该 task 签发，persist/rehashed JSON 不是授权")
    if context is not None and actual != context:
        raise ValueError("授权调用 context 与宿主记录失配")
    _validate_semantics(ledger, store_dir, proof, actual)
    if outcome is not None and outcome not in proof.allowed_outcomes:
        raise ValueError("授权允许面不含该结论")


def normalize_outcome(outcome, error_kind, authorization):
    if outcome == "verify_error":
        return outcome, error_kind, "none"
    if authorization is not None:
        return outcome, error_kind, "authorized"
    return "verify_error", "poc_fail", "no_trusted_authorization"


def s2_source_authorized(ledger, store_dir, source_event, *, cluster_id=None, direction=None):
    if (store_dir is None or not hasattr(source_event, "kind")
            or source_event.kind is not LedgerKind.VERIFY_RESULT):
        return False
    p = source_event.payload
    outcome = p.get("outcome")
    expected_direction = {"verified_real": 1, "verified_fp": -1}.get(outcome)
    if (p.get("authorized") is not True or expected_direction is None
            or (cluster_id is not None and p.get("cluster_id") != cluster_id)
            or (direction is not None and direction != expected_direction)):
        return False
    proof = load_proof(ledger, store_dir, p.get("auth_digest"))
    if proof is None or not p.get("task_id") or p.get("context_digest") != proof.context_digest:
        return False
    try:
        if source_event.run_id != proof.run_id:
            return False
        validate_authorization(ledger, store_dir, proof, run_id=source_event.run_id,
                               task_id=p["task_id"], cluster_id=p["cluster_id"], outcome=outcome)
        return True
    except (ValueError, KeyError, TypeError, OSError):
        return False
