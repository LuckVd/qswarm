"""exams.store / roster 测试（SPEC-6 §4.1/§4.3；SPEC-5 M2 卷 §1.6 最低用例集）。

钉死：exam_items UPSERT-by-version（高版本全列 UPDATE、retired_at 仅移出题库时
置位）、exam_results ER-{seq} 库内 max+1 与行级幂等键、roster 注册身份/编制分类/
幂等/身份冲突、候选集去重展开。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qswarm.contracts import AgentStatus, ExamPurpose, PoolClass, Role
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.prompts import CONTRACT_VERSION
from qswarm.supply import load_models
from qswarm.exams.bank import SeedBank
from qswarm.exams.roster import Candidate, apply_qc_decision, expand_candidates, register_roster
from qswarm.exams.store import retire_exam_items, upsert_exam_items, write_exam_result

from m2_harness import MODELS_YAML, T0, make_bank


@pytest.fixture(scope="module")
def bank(tmp_path_factory) -> SeedBank:
    return make_bank(tmp_path_factory.mktemp("store"))


def test_exam_items_upsert_version_advance(bank, tmp_path):
    """同 item_id 高 version → 全列 UPDATE（created_at/retired_at 保留）；低/同
    version → 跳过；retired_at 仅 retire_exam_items（移出题库）时置位且只置一次。"""
    with KernelLedger(tmp_path / "up.db", clock=FixedClock(T0)) as led:
        n = upsert_exam_items(led, bank)
        assert n == 100
        row0 = led.conn.execute(
            "SELECT version, canary, created_at, retired_at FROM exam_items WHERE item_id = ?",
            (bank.items[0].item_id,),
        ).fetchone()
        assert row0[0] == 1 and row0[3] is None
        # 高版本换内容 → 全列 UPDATE
        bumped = bank.items[0].model_copy(
            update={"version": 2, "canary": "qsw-canary-SEED-0001-cafe1234",
                    "difficulty": "hard"}
        )
        bank2 = SeedBank.model_validate(
            {"bank_version": 1, "generated_at": bank.generated_at, "items": [bumped]}
        )
        assert upsert_exam_items(led, bank2) == 1
        row1 = led.conn.execute(
            "SELECT version, canary, difficulty, created_at, retired_at FROM exam_items WHERE item_id = ?",
            (bank.items[0].item_id,),
        ).fetchone()
        assert row1[0] == 2 and row1[1] == "qsw-canary-SEED-0001-cafe1234"
        assert row1[2] == "hard"
        assert row1[3] == row0[2]           # created_at 保留
        assert row1[4] is None              # 轮换换版不清 retired_at
        # 同/低版本 → 跳过
        assert upsert_exam_items(led, bank2) == 0
        assert upsert_exam_items(led, bank) == 0
        # retired_at 仅移出题库时置位（且幂等）
        assert retire_exam_items(led, [bank.items[0].item_id]) == 1
        assert retire_exam_items(led, [bank.items[0].item_id]) == 0
        (retired,) = led.conn.execute(
            "SELECT retired_at FROM exam_items WHERE item_id = ?", (bank.items[0].item_id,)
        ).fetchone()
        assert retired is not None


def test_exam_results_seq_and_row_idempotency(bank, tmp_path):
    """ER-{seq:06d} 按库内同前缀 max+1（D20 同型）；行幂等键 (agent_id, purpose,
    item_id) 重跑跳过；model_ref/ts 由实参与注入时钟签发。"""
    with KernelLedger(tmp_path / "er.db", clock=FixedClock(T0)) as led:
        upsert_exam_items(led, bank)
        it = bank.items[0]
        id1 = write_exam_result(
            led, purpose=ExamPurpose.ENTRY, agent_id="A@cv1", role="scout",
            node="injection", item_id=it.item_id, correct=True, model_ref="m1",
        )
        assert id1 == "ER-000001"
        id2 = write_exam_result(
            led, purpose=ExamPurpose.ENTRY, agent_id="A@cv1", role="scout",
            node="injection", item_id=bank.items[1].item_id, correct=False, model_ref="m1",
        )
        assert id2 == "ER-000002"
        # 幂等：同 (agent, purpose, item) 重跑 → 同 id 零新行
        assert write_exam_result(
            led, purpose=ExamPurpose.ENTRY, agent_id="A@cv1", role="scout",
            node="injection", item_id=it.item_id, correct=True, model_ref="m1",
        ) == id1
        assert led.conn.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0] == 2
        # D20 同型：外部预置高 seq 行后接 max+1
        led.conn.execute(
            "INSERT INTO exam_results (id, purpose, agent_id, role, node, item_id,"
            " correct, score, model_ref, ts) VALUES ('ER-000420', 'entry', 'B@cv1',"
            " 'scout', 'crypto', ?, 1, NULL, 'm2', ?)",
            (bank.items[2].item_id, T0),
        )
        id3 = write_exam_result(
            led, purpose=ExamPurpose.ENTRY, agent_id="C@cv1", role="scout",
            node="crypto", item_id=bank.items[3].item_id, correct=True, model_ref="m1",
        )
        assert id3 == "ER-000421"
        row = led.conn.execute(
            "SELECT purpose, agent_id, role, node, item_id, correct, model_ref, ts"
            " FROM exam_results WHERE id = ?", (id1,)
        ).fetchone()
        assert row[0] == "entry" and row[1] == "A@cv1" and row[2] == "scout"
        assert row[4] == it.item_id and row[5] == 1 and row[6] == "m1" and row[7] == T0


def _cands() -> list[Candidate]:
    return [
        Candidate(role="scout", tier="cheap", model_ref="cheap:deepseek/deepseek-chat",
                  agent_id=f"cheap:deepseek/deepseek-chat@{CONTRACT_VERSION}"),
        Candidate(role="auditor", tier="strong", model_ref="strong:anthropic/claude-sonnet-4-5",
                  agent_id=f"strong:anthropic/claude-sonnet-4-5@{CONTRACT_VERSION}"),
    ]


def test_register_roster_identity_pool_class_idempotent(tmp_path):
    """agent_id=model_ref@cv；hats pool_class scout=temp 余 formal；同身份重注册
    跳过；字段冲突 ValueError（身份冲突人工介入）；QC 流转 active/provisioning。"""
    with KernelLedger(tmp_path / "roster.db", clock=FixedClock(T0)) as led:
        cands = _cands()
        assert register_roster(led, cands) == [c.agent_id for c in cands]
        rows = led.conn.execute(
            "SELECT agent_id, model_ref, contract_version, status FROM agents"
        ).fetchall()
        assert {r[0] for r in rows} == {c.agent_id for c in cands}
        by_id = {r[0]: r for r in rows}
        assert by_id[cands[0].agent_id][1] == "cheap:deepseek/deepseek-chat"
        assert by_id[cands[0].agent_id][2] == CONTRACT_VERSION
        assert by_id[cands[0].agent_id][3] == AgentStatus.PROVISIONING.value
        hats = dict(led.conn.execute("SELECT agent_id || '|' || role, pool_class FROM hats").fetchall())
        assert hats[f"{cands[0].agent_id}|scout"] == PoolClass.TEMP.value
        assert hats[f"{cands[1].agent_id}|auditor"] == PoolClass.FORMAL.value
        # 幂等重注册：跳过（行数不变）
        assert register_roster(led, cands) == []
        assert led.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 2
        # 身份冲突：同 agent_id 不同 model_ref → ValueError
        conflict = [Candidate(role="scout", tier="cheap",
                              model_ref="cheap:openai/gpt-4o-mini",
                              agent_id=cands[0].agent_id)]
        with pytest.raises(ValueError, match="身份冲突"):
            register_roster(led, conflict)
        # QC 流转
        apply_qc_decision(led, cands[0].agent_id, passed=True)
        (status, last_active) = led.conn.execute(
            "SELECT status, last_active_at FROM agents WHERE agent_id = ?",
            (cands[0].agent_id,),
        ).fetchone()
        assert status == AgentStatus.ACTIVE.value and last_active is not None
        apply_qc_decision(led, cands[1].agent_id, passed=False)
        (status2,) = led.conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?", (cands[1].agent_id,)
        ).fetchone()
        assert status2 == AgentStatus.PROVISIONING.value


def test_expand_candidates_dedup_by_model(tmp_path):
    """primary+fallbacks 去重展开 × 角色档位映射；同 (role, model_ref) 唯一；
    params.roster 不约束（展开由供给面决定）；roles 裁剪（v0 播种 scout/auditor）。"""
    p = tmp_path / "models.yaml"
    p.write_text(MODELS_YAML, encoding="utf-8")
    models = load_models(p)
    all_c = expand_candidates(models)
    assert [(c.role, c.model_ref) for c in all_c] == [
        ("scout", "cheap:deepseek/deepseek-chat"),
        ("scout", "cheap:openai/gpt-4o-mini"),
        ("auditor", "strong:anthropic/claude-sonnet-4-5"),
        ("auditor", "strong:openai/gpt-4o"),
        ("reviewer", "mid:deepseek/deepseek-reasoner"),
        ("verifier", "engineering:openai/gpt-4o"),
    ]
    assert len({(c.role, c.model_ref) for c in all_c}) == len(all_c)
    assert all(c.agent_id == f"{c.model_ref}@{CONTRACT_VERSION}" for c in all_c)
    # 档位条目内去重（primary 与 fallback 同条目 → 只展开一次）
    import yaml

    data = yaml.safe_load(MODELS_YAML)
    data["tiers"]["cheap"]["fallbacks"] = [data["tiers"]["cheap"]["primary"]]
    p2 = tmp_path / "models2.yaml"
    p2.write_text(yaml.safe_dump(data), encoding="utf-8")
    dedup = expand_candidates(load_models(p2))
    assert [c.model_ref for c in dedup if c.role == "scout"] == ["cheap:deepseek/deepseek-chat"]
    # roles 裁剪（S10 调用方裁剪面）
    scoped = expand_candidates(models, roles=("scout", "auditor"))
    assert {c.role for c in scoped} == {"scout", "auditor"}
    # 缺档位 → ValueError
    data2 = yaml.safe_load(MODELS_YAML)
    del data2["tiers"]["cheap"]
    del data2["entry_exams"]["cheap"]
    p3 = tmp_path / "models3.yaml"
    p3.write_text(yaml.safe_dump(data2), encoding="utf-8")
    with pytest.raises(ValueError, match="缺档位"):
        expand_candidates(load_models(p3))
    # params.roster 不约束真实编制（RosterParams 是仿真规模，展开不读它）
    from qswarm.contracts import load_params

    params = load_params()
    assert params.roster.scout == 6 and params.roster.scout != len(
        [c for c in all_c if c.role == "scout"]
    )


def test_artifact_put_get_roundtrip(tmp_path):
    """内容寻址工件 put/get 往返：同内容幂等（INSERT OR IGNORE + 文件复用）、
    kind 词表限定 seed|exam_log、未索引 hash 读 None。"""
    from qswarm.exams.store import artifact_get, artifact_put

    with KernelLedger(tmp_path / "art.db", clock=FixedClock(T0)) as led:
        store = tmp_path / "store"
        h1 = artifact_put(led, store, b"payload-alpha", mime="text/plain",
                          kind="exam_log", producer="A@cv1")
        h2 = artifact_put(led, store, b"payload-alpha", mime="text/plain",
                          kind="exam_log", producer="A@cv1")
        assert h1 == h2
        rows = led.conn.execute("SELECT COUNT(*) FROM artifacts WHERE hash = ?", (h1,)).fetchone()[0]
        assert rows == 1                       # 幂等索引行
        assert artifact_get(led, store, h1) == b"payload-alpha"
        assert artifact_get(led, store, "0" * 64) is None
        with pytest.raises(ValueError, match="kind 越词表"):
            artifact_put(led, store, b"x", mime="text/plain", kind="misc", producer="p")
