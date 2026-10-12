"""prompts.parse 测试（SPEC-3 §4；SPEC-5 M2 卷 §1.1 最低用例集）。

钉死：两段式提取（全文 → 最后一个 ```json 围栏 → no_json）、只提取不修补、
五角色模型分发、未知角色 ValueError、ref 非 ^artifact:// 拒绝、"artifact://pending"
放行、词表越界拒绝（reason 可断言）、同 raw 恒等（含异常类型与 reason）。
"""

from __future__ import annotations

import json

import pytest

from qswarm.contracts import ArbitrationDraft, AuditorFinding, ReviewerVote, ScoutReport, VerifierReport
from qswarm.prompts import SchemaRejected, extract_json, parse_output


def scout_raw(**over) -> str:
    payload = {
        "candidate_id": "CD-1",
        "suspicious": True,
        "guess_family": "injection",
        "guess_type": "sqli",
        "reason_ref": "artifact://pending",
        "injection_flag": False,
    }
    payload.update(over)
    return json.dumps(payload, ensure_ascii=False)


def auditor_raw(**over) -> str:
    payload = {
        "title": "SQL 注入",
        "severity": "high",
        "cwe": "CWE-89",
        "family": "injection",
        "type": "sqli",
        "evidence_chain": [
            {"kind": "source", "where_path": "a.py", "where_line": 1, "found": None, "ref": "artifact://pending"},
            {"kind": "propagation", "where_path": "a.py", "where_line": 2, "found": None, "ref": "artifact://pending"},
            {"kind": "sanitizer", "where_path": "a.py", "where_line": 3, "found": False, "ref": "artifact://pending"},
            {"kind": "sink", "where_path": "b.py", "where_line": 4, "found": None, "ref": "artifact://pending"},
        ],
        "confidence": 0.8,
        "disagreement_note": None,
        "artifacts": ["artifact://pending"],
    }
    payload.update(over)
    return json.dumps(payload, ensure_ascii=False)


def vote_raw() -> str:
    return json.dumps(
        {
            "dim_completeness": 0.8,
            "dim_reachability": 0.9,
            "dim_exploitability": 0.7,
            "dim_fp_suspicion": 0.2,
            "direction": 1,
            "comment_ref": "artifact://pending",
        }
    )


def verifier_raw(**over) -> str:
    payload = {
        "cluster_id": "C-1",
        "outcome": "verified_real",
        "env_base": "python:3.11",
        "deps_ref": "artifact://pending",
        "trigger_path": ["entry: a.py:1"],
        "assertions": [{"expect": "e", "observed": True, "log": "artifact://pending"}],
        "blocking": None,
        "error_kind": None,
        "attempts": 1,
        "duration_s": 1.5,
        "logs_ref": "artifact://pending",
    }
    payload.update(over)
    return json.dumps(payload, ensure_ascii=False)


def advisor_raw() -> str:
    return json.dumps(
        {
            "run_id": "R-1",
            "cluster_ids": ["C-1"],
            "suggestions": [{"cluster_id": "C-1", "action": "merge", "rationale": "r"}],
            "summary_ref": "artifact://pending",
        }
    )


# ── extract_json（E3 两段式）────────────────────────────────────

def test_extract_json_full_then_last_fence():
    """全文 JSON 成功 → 原文（strip 后）；围栏 → 取最后一个（不贪婪更早围栏）；
    无 JSON → SchemaRejected('no_json')；只提取不修补（尾逗号不去）。"""
    raw = '{"a": 1}'
    assert extract_json(raw) == raw
    assert extract_json(f"  \n{raw}\n  ") == raw
    # 围栏：取最后一个
    fenced = '思考过程 {"a": 0}\n```json\n{"a": 1}\n```\n中段\n```json\n{"b": 2}\n```\n收尾'
    assert extract_json(fenced) == '{"b": 2}'
    # 无围栏且全文非 JSON → no_json
    with pytest.raises(SchemaRejected) as ei:
        extract_json("没有 JSON 的输出")
    assert ei.value.reason == "no_json"
    # 最后围栏内容非法 → no_json（不回退更早围栏——不贪婪）
    bad_last = '```json\n{"a": 1}\n```\n```json\n{"b": \n```'
    with pytest.raises(SchemaRejected) as ei2:
        extract_json(bad_last)
    assert ei2.value.reason == "no_json"
    # 只提取不修补：尾逗号不修补 → no_json
    with pytest.raises(SchemaRejected):
        extract_json('```json\n{"a": 1,}\n```')
    # 未闭合围栏 → no_json
    with pytest.raises(SchemaRejected):
        extract_json('```json\n{"a": 1}\n')
    # 空串 → no_json
    with pytest.raises(SchemaRejected):
        extract_json("")


# ── parse_output：分发与守卫 ────────────────────────────────────

def test_parse_output_role_dispatch_and_ref_guard():
    """五角色模型分发；未知 role → ValueError（词表错）；ref 非 ^artifact:// →
    SchemaRejected(...ref_not_artifact)；"artifact://pending" 放行。"""
    assert isinstance(parse_output("scout", scout_raw()), ScoutReport)
    assert isinstance(parse_output("auditor", auditor_raw()), AuditorFinding)
    assert isinstance(parse_output("reviewer", vote_raw()), ReviewerVote)
    assert isinstance(parse_output("verifier", verifier_raw()), VerifierReport)
    assert isinstance(parse_output("queen_advisor", advisor_raw()), ArbitrationDraft)
    assert isinstance(parse_output("queen_advisor", advisor_raw()), ArbitrationDraft)
    with pytest.raises(ValueError, match="未知输出角色"):
        parse_output("nobody", scout_raw())
    # ref 守卫：逐角色 ref 类字段
    for role, raw in (
        ("scout", scout_raw(reason_ref="repo://a.py")),
        ("reviewer", vote_raw().replace("artifact://pending", "http://x")),
        ("verifier", verifier_raw(deps_ref="file:///tmp/x")),
        ("queen_advisor", advisor_raw().replace("artifact://pending", "misc")),
    ):
        with pytest.raises(SchemaRejected) as ei:
            parse_output(role, raw)
        assert ei.value.reason.endswith(":ref_not_artifact"), (role, ei.value.reason)
    # auditor：evidence_chain[].ref 与 artifacts[]
    with pytest.raises(SchemaRejected) as ei:
        parse_output("auditor", auditor_raw(artifacts=["artifact://pending", "pending"]))
    assert ei.value.reason == "schema:AuditorFinding:ref_not_artifact"
    with pytest.raises(SchemaRejected) as ei2:
        bad_chain = json.loads(auditor_raw())
        bad_chain["evidence_chain"][1]["ref"] = "repo:b"
        parse_output("auditor", json.dumps(bad_chain, ensure_ascii=False))
    assert ei2.value.reason == "schema:AuditorFinding:ref_not_artifact"
    # ref 守卫在 schema 校验之后：schema 错优先（缺必填字段 + 坏 ref → schema 错）
    with pytest.raises(SchemaRejected) as ei3:
        bad = json.loads(scout_raw(reason_ref="nope"))
        del bad["candidate_id"]
        parse_output("scout", json.dumps(bad, ensure_ascii=False))
    assert ":ref_not_artifact" not in ei3.value.reason
    assert ei3.value.reason.startswith("schema:ScoutReport:")


def test_parse_output_rejects_out_of_vocabulary():
    """severity/guess_family/outcome 越词表 → SchemaRejected 且 reason 可断言
    （pydantic 枚举守卫；reason 含模型名与字段定位）。"""
    with pytest.raises(SchemaRejected) as e1:
        parse_output("auditor", auditor_raw(severity="oops"))
    assert e1.value.reason.startswith("schema:AuditorFinding:")
    assert "severity" in e1.value.reason
    with pytest.raises(SchemaRejected) as e2:
        parse_output("scout", scout_raw(guess_family="nope"))
    assert e2.value.reason.startswith("schema:ScoutReport:")
    assert "guess_family" in e2.value.reason
    with pytest.raises(SchemaRejected) as e3:
        parse_output("verifier", verifier_raw(outcome="maybe"))
    assert e3.value.reason.startswith("schema:VerifierReport:")
    assert "outcome" in e3.value.reason
    # 字段缺失（schema 形态错）
    with pytest.raises(SchemaRejected) as e4:
        parse_output("reviewer", '{"dim_completeness": 0.5}')
    assert e4.value.reason.startswith("schema:ReviewerVote:")
    # 无 JSON
    with pytest.raises(SchemaRejected) as e5:
        parse_output("scout", "lorem ipsum")
    assert e5.value.reason == "no_json"


def test_parse_output_deterministic():
    """同 raw 恒等（含异常类型与 reason）；解析零随机零 IO。"""
    raw = scout_raw()
    a = parse_output("scout", raw)
    b = parse_output("scout", raw)
    assert a == b and a.model_dump() == b.model_dump()
    for bad in ("no json at all", scout_raw(guess_family="nope"), '```json\n{"x":\n```'):
        with pytest.raises(SchemaRejected) as ea:
            parse_output("scout", bad)
        with pytest.raises(SchemaRejected) as eb:
            parse_output("scout", bad)
        assert type(ea.value) is type(eb.value)
        assert ea.value.reason == eb.value.reason
        assert str(ea.value) == str(eb.value)
    # 信封 declined 通道是考试运输层（E4）——本模块不管信封，parse 只消费生产
    # schema 载荷（信封解析归 exams.runner）
    raw2 = f'前言\n```json\n{auditor_raw()}\n```\n后记'
    assert parse_output("auditor", raw2).family == "injection"
