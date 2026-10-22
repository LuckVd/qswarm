"""角色资格门测试（SPEC-10 §3/§4/§5/§10；P0 卷判据）。

钉死：profile_id 确定性与敏感性、资格状态机（pending→passed/failed、stale/
revoked）、五类不合格态全部拒派（无行/pending/failed/stale/revoked）、per-role
隔离、profile/判据版本失配失效、乱序写守卫（旧结果不得覆盖新资格）、supersede、
迁移 dry-run 零写 + apply 全 stale(legacy_unverifiable) + 迁移永不降级真资格、
播种报告 agent_status / role_qualification 分列（离线 FakeAdapter，D27 纪律）、
served_model 元数据留档（不回显 → unknown）。
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from qswarm.contracts import load_params
from qswarm.exams.roster import (
    Candidate,
    apply_qc_decision,
    migrate_qualifications,
    record_qualification,
    register_roster,
    revoke_qualification,
)
from qswarm.exams.seeding import seed_entry
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.qualification import (
    CTX_STRATEGY_VERSION,
    QualificationGate,
    current_prompt_hash,
    gate_from_params,
    profile_id,
)
from qswarm.prompts import CONTRACT_VERSION, Message
from qswarm.supply import UrllibAdapter, load_models

from m2_harness import FakeAdapter, T0, build_models_yaml, make_bank, make_resolver
from test_supply_adapter import _fake_opener

GATE = QualificationGate(
    policy_version="qp-v1", enforce_roles=("verifier",), production_temp=0.2,
)
VERIFIER_MODEL = "engineering:zhipu/glm-5.3-flash"
VERIFIER_ID = f"{VERIFIER_MODEL}@{CONTRACT_VERSION}"


def _cand(role: str, tier: str, model_ref: str) -> Candidate:
    return Candidate(
        role=role, tier=tier, model_ref=model_ref,
        agent_id=f"{model_ref}@{CONTRACT_VERSION}",
    )


def _active_verifier(led) -> str:
    """注册一只 active verifier（帽 = register_roster 既有写口）。"""
    register_roster(led, [_cand("verifier", "engineering", VERIFIER_MODEL)])
    apply_qc_decision(led, VERIFIER_ID, passed=True)
    return VERIFIER_ID


# ── §2 身份模型：profile_id ─────────────────────────────────────


def test_profile_id_determinism_and_sensitivity():
    """同入同出（确定性）；provider/model/role/prompt/温度/策略任一分量变则变。"""
    a = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
    b = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
    assert a == b
    assert a.startswith("ep-") and len(a) == 3 + 16
    assert profile_id(VERIFIER_MODEL, "verifier", production_temp=0.5) != a
    assert profile_id("engineering:zhipu/glm-5.3", "verifier", production_temp=0.2) != a
    assert profile_id(VERIFIER_MODEL, "reviewer", production_temp=0.2) != a
    assert (
        profile_id(VERIFIER_MODEL, "verifier", prompt_hash="deadbeef",
                   production_temp=0.2)
        != a
    )
    # prompt_hash 缺省 = 当前 prompt 面哈希（内容锚非版本号锚）
    assert profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2) == profile_id(
        VERIFIER_MODEL, "verifier",
        prompt_hash=current_prompt_hash("verifier"), production_temp=0.2,
    )
    with pytest.raises(ValueError, match="model_ref 形态非法"):
        profile_id("no-wire-form", "verifier", production_temp=0.2)


def test_ctx_strategy_version_is_pinned_constant():
    """上下文策略版本常量在位（profile 分量；改策略语义须升版并触发重认证）。"""
    assert CTX_STRATEGY_VERSION == "ctx-v1"


# ── §5 资格门：五类不合格态 + passed + per-role 隔离 ─────────────


def test_gate_filters_all_unqualified_statuses(tmp_path):
    """无行/pending/failed/stale/revoked 全部不入合格池；passed 入池；
    gate=None 零过滤（M5 逐位兼容）。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        pid = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
        pool = [(VERIFIER_MODEL, agent)]
        base = dict(
            agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", started_at="2030-01-01T00:00:01Z",
        )
        # 无行
        assert GATE.filter_pairs(led, "verifier", list(pool)) == ([], 1)
        for status, reason in (
            ("pending", None), ("failed", "qc_below_line"),
            ("stale", "legacy_unverifiable"), ("revoked", "manual"),
        ):
            record_qualification(led, status=status, reason=reason, **base)
            kept, filtered = GATE.filter_pairs(led, "verifier", list(pool))
            assert (kept, filtered) == ([], 1), status
        # passed → 入池；gate=None → 原池
        record_qualification(
            led, status="passed", accuracy=0.9, exam_run_id="entry-seed1",
            bank_version=1, scoring_version="s11-agent-total", **base,
        )
        assert GATE.filter_pairs(led, "verifier", list(pool)) == (pool, 0)
        from qswarm.kernel.consult import _active_by_role

        assert _active_by_role(led, "verifier", GATE) == pool
        assert agent in [a for _r, a in _active_by_role(led, "verifier")]


def test_gate_none_is_zero_behavior(tmp_path):
    """gate=None（缺省 enforce=False）：无任何资格行时池照旧——黄金零触碰。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        from qswarm.kernel.consult import _active_by_role

        assert _active_by_role(led, "verifier") == [(VERIFIER_MODEL, agent)]
        assert _active_by_role(led, "verifier", None) == [(VERIFIER_MODEL, agent)]


def test_role_isolation_failed_verifier_keeps_other_roles(tmp_path):
    """某角色资格失败不改变其他角色池（§10-3）：verifier failed，reviewer 池
    （未启用资格门）照旧含同蜂。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        from qswarm.kernel.consult import active_reviewers, active_verifiers
        from qswarm.exams.roster import attach_hat

        attach_hat(led, agent, "reviewer")
        record_qualification(
            led, agent_id=agent, role="verifier",
            profile_id=profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2),
            policy_version="qp-v1", status="failed", reason="qc_below_line",
            started_at="2030-01-01T00:00:01Z",
        )
        assert active_verifiers(led, gate=GATE) == []
        assert agent in [a for _r, a in active_reviewers(led, gate=GATE)]


def test_profile_and_policy_change_invalidate(tmp_path):
    """profile 失配（温度锚漂移）与判据版本升版 ⇒ 旧 passed 对新键不可见
    （SPEC-10 §2；查无即拒派，fail-closed）。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        record_qualification(
            led, agent_id=agent, role="verifier",
            profile_id=profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2),
            policy_version="qp-v1", status="passed", accuracy=0.9,
            started_at="2030-01-01T00:00:01Z",
        )
        hot = QualificationGate(
            policy_version="qp-v1", enforce_roles=("verifier",), production_temp=0.5,
        )
        assert hot.filter_pairs(led, "verifier", [(VERIFIER_MODEL, agent)])[0] == []
        v2 = QualificationGate(
            policy_version="qp-v2", enforce_roles=("verifier",), production_temp=0.2,
        )
        assert v2.filter_pairs(led, "verifier", [(VERIFIER_MODEL, agent)])[0] == []


# ── §3.3 写守卫：乱序 / supersede / revoke ──────────────────────


def test_out_of_order_write_rejected(tmp_path):
    """乱序写守卫：effective passed（T2）在位时，更早 started_at（T1）的
    failed/pending 写被拒——旧考试轮不得覆盖新资格。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        pid = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
        record_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", status="passed", accuracy=0.9,
            started_at="2030-01-01T00:00:02Z",
        )
        for status in ("failed", "pending"):
            with pytest.raises(ValueError, match="乱序资格写被拒"):
                record_qualification(
                    led, agent_id=agent, role="verifier", profile_id=pid,
                    policy_version="qp-v1", status=status,
                    started_at="2030-01-01T00:00:01Z",
                )


def test_supersede_keeps_single_effective_passed(tmp_path):
    """同键重考：新 passed 替代旧 passed（superseded_by 回填），effective 唯一。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        pid = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
        old_id = record_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", status="passed", accuracy=0.775,
            started_at="2030-01-01T00:00:01Z",
        )
        new_id = record_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", status="passed", accuracy=0.9,
            started_at="2030-01-01T00:00:03Z",
        )
        row = led.conn.execute(
            "SELECT superseded_by, accuracy FROM role_qualifications WHERE id = ?",
            (old_id,),
        ).fetchone()
        assert row[0] == new_id
        assert GATE.effective_row(led, agent, "verifier", VERIFIER_MODEL)[
            "qualification_id"
        ] == new_id


def test_revoke_blocks_recheck(tmp_path):
    """管理撤销（终态 revoked 替代既有 passed）→ dispatch 前复验不通过
    （§5.3 排队后 revoked 阻断）。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        pid = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
        record_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", status="passed", accuracy=0.9,
            started_at="2030-01-01T00:00:01Z",
        )
        assert GATE.recheck(led, agent, "verifier", VERIFIER_MODEL)
        with pytest.raises(ValueError, match="reason"):
            revoke_qualification(
                led, agent_id=agent, role="verifier", profile_id=pid,
                policy_version="qp-v1", reason="",
            )
        revoke_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", reason="manual_revoke",
            started_at="2030-01-01T00:00:04Z",
        )
        assert not GATE.recheck(led, agent, "verifier", VERIFIER_MODEL)


# ── §4 迁移：dry-run 零写 / apply 全 stale / 不降级真资格 ────────


def test_migration_dry_run_then_apply(tmp_path):
    """dry-run 零写出 manifest（全 stale/legacy_unverifiable）；apply 落行后
    门禁全拒；幂等重跑全 skip；supply_temp 锚失配如实披露。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        auditor = _cand("auditor", "strong", "strong:zhipu/glm-5.3-flash")
        register_roster(led, [auditor])

        m = migrate_qualifications(
            led, dry_run=True, policy_version="qp-v1", production_temp=0.2,
            supply_temp=0.5,
        )
        assert m["dry_run"] and m["temp_anchor_mismatch"]
        assert m["pairs_total"] == 2 and m["would_write"] == 2
        assert all(r["status"] == "stale" and r["reason"] == "legacy_unverifiable"
                   for r in m["rows"])
        assert led.conn.execute(
            "SELECT COUNT(*) FROM role_qualifications"
        ).fetchone()[0] == 0                      # dry-run 零写

        m2 = migrate_qualifications(
            led, dry_run=False, policy_version="qp-v1", production_temp=0.2,
        )
        assert m2["stale_written"] == 2
        assert led.conn.execute(
            "SELECT COUNT(*) FROM role_qualifications WHERE status='stale'"
        ).fetchone()[0] == 2
        # 门禁全拒（stale 非 effective passed）
        assert GATE.filter_pairs(
            led, "verifier", [(VERIFIER_MODEL, agent)]
        ) == ([], 1)
        # 幂等：重跑全 skip
        m3 = migrate_qualifications(
            led, dry_run=False, policy_version="qp-v1", production_temp=0.2,
        )
        assert m3["stale_written"] == 0 and m3["skipped"] == 2


def test_migration_never_demotes_real_qualification(tmp_path):
    """迁移后考取的 effective passed 不被二次迁移降级（skip 分支）。"""
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        agent = _active_verifier(led)
        pid = profile_id(VERIFIER_MODEL, "verifier", production_temp=0.2)
        record_qualification(
            led, agent_id=agent, role="verifier", profile_id=pid,
            policy_version="qp-v1", status="passed", accuracy=0.9,
            started_at="2030-01-01T00:00:01Z",
        )
        m = migrate_qualifications(
            led, dry_run=False, policy_version="qp-v1", production_temp=0.2,
        )
        assert m["stale_written"] == 0 and m["skipped"] == 1
        assert GATE.recheck(led, agent, "verifier", VERIFIER_MODEL)


def test_gate_from_params_default_off(tmp_path):
    """缺省 params（enforce=False）→ gate None；显式开启 → 只对 enforce_roles
    生效（verifier 先行）。"""
    params = load_params()
    assert gate_from_params(params) is None
    params.qualification.enforce = True
    gate = gate_from_params(params)
    assert gate is not None and gate.applies("verifier")
    assert not gate.applies("auditor") and not gate.applies("scout")


# ── §3.2-7 播种报告分列（离线 FakeAdapter，D27 纪律）────────────


@pytest.fixture()
def seed_env(tmp_path):
    bank = make_bank(tmp_path / "seeds")
    models = load_models(build_models_yaml(tmp_path / "models.yaml"))
    resolve = make_resolver(tmp_path / "seeds")
    return tmp_path, bank, models, resolve


def test_seed_entry_report_splits_agent_and_role_status(seed_env):
    """QC 通过：报告逐 agent 带 agent_status=active 与 role_qualification
    effective 行（status=passed）；账本内 pending 行被终态替代。"""
    tmp, bank, models, resolve = seed_env
    with KernelLedger(tmp / "a.db", clock=FixedClock(T0)) as led:
        report = seed_entry(
            led, bank, models, FakeAdapter(tmp / "seeds"),
            seed=7, resolve_text=resolve,
        )
        for agent in report["agents"]:
            assert agent["agent_status"] == "active"
            rq = agent["role_qualification"]
            assert rq["status"] == "passed"
            assert rq["policy_version"] == "qp-v1"
            assert rq["exam_run_id"] == f"entry-seed{report['seed']}"
        # 每行恰一 effective passed（无未替代 pending 残留）
        stray = led.conn.execute(
            "SELECT COUNT(*) FROM role_qualifications"
            " WHERE status = 'pending' AND superseded_by IS NULL"
        ).fetchone()[0]
        assert stray == 0


def test_seed_entry_qc_fail_marks_role_qualification_failed(seed_env):
    """QC 未过：agent_status 留 provisioning、role_qualification=failed、
    本体不 active（帽/本体/资格三面分立）。"""
    tmp, bank, models, resolve = seed_env

    def all_decline(model_ref, item_id, payload):
        return json.dumps(
            {"answers": [{"item_id": item_id, "declined": True, "output": None}]},
            ensure_ascii=False,
        )

    with KernelLedger(tmp / "b.db", clock=FixedClock(T0)) as led:
        report = seed_entry(
            led, bank, models, FakeAdapter(tmp / "seeds", script=all_decline),
            seed=7, resolve_text=resolve,
        )
        for agent in report["agents"]:
            assert agent["agent_status"] == "provisioning"
            assert agent["role_qualification"]["status"] == "failed"
            assert agent["role_qualification"]["accuracy"] == 0.0
            assert agent["qc"]["passed"] is False


# ── §2/D46 配套：served_model 身份元数据留档 ────────────────────


def test_complete_with_meta_captures_served_model(tmp_path, monkeypatch):
    """供应商回显 model → served_model 原样留档；不回显 → unknown（不由请求名
    推定，D46 红线配套）。"""
    models_path = build_models_yaml(tmp_path / "models.yaml")
    cfg = load_models(models_path)
    monkeypatch.setenv("M2_TEST_KEY_CHEAP", "sk-test")

    echoed = UrllibAdapter(
        cfg, opener=_fake_opener({}, {
            "model": "served-name-x",
            "choices": [{"message": {"content": "ok"}}],
        })
    )
    text, meta = echoed.complete_with_meta(
        model_ref="cheap:deepseek/deepseek-chat",
        messages=[Message(role="user", content="hi")], temperature=0,
    )
    assert text == "ok"
    assert meta["served_model"] == "served-name-x"
    assert meta["requested_model"] == "deepseek-chat"
    assert meta["wire"] == "openai_compatible"
    assert meta["observed_at"].endswith("Z")

    silent = UrllibAdapter(
        cfg, opener=_fake_opener({}, {"choices": [{"message": {"content": "ok"}}]})
    )
    _text, meta2 = silent.complete_with_meta(
        model_ref="cheap:deepseek/deepseek-chat",
        messages=[Message(role="user", content="hi")], temperature=0,
    )
    assert meta2["served_model"] == "unknown"
