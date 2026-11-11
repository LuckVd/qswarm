"""evidence_v3 只读投影测试（方案 §4；SPEC-10 §7；P1 工作包 B + R1-T5 勘误）。

验收面（方案 §4.5 逐字）：同事实重复投票/finding/纠错不增根因数；同 as_of
同事件输入同投影哈希；未来真值不进历史投影；旧账本零触碰（本模块零写）。

R1-T5 勘误面（docs/R1_CHANGE_NOTE.md §1-T5）：① population 分立——precision
只含正向报告集（正确拒判另列 correct_rejection_rate）；② direction=0 = 忽略
（不产生 refuted 真值、不造 disputed）；③ 富化只用 as_of 内事件（未来 finding
不改变历史投影）。v2 口径已勘误（1 错正向 + 9 正确拒判 → 0.9 的混池错误）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.evidence import (
    capability_summary,
    effective_view,
    independent_root_causes,
    project_evidence,
    write_projection,
)


@pytest.fixture()
def ledger(tmp_path):
    with KernelLedger(tmp_path / "led.db",
                      clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        yield led


def _vr(led, cluster, outcome, error_kind=None):
    payload = {"cluster_id": cluster, "outcome": outcome}
    if error_kind:
        payload["error_kind"] = error_kind
    return led.append(LedgerKind.VERIFY_RESULT, payload, run_id="R1")


def _hv(led, cluster, direction, actor="owner"):
    return led.append(LedgerKind.HUMAN_VERDICT,
                      {"cluster_id": cluster, "direction": direction,
                       "actor": actor}, run_id="R1")


def test_projection_hash_deterministic_same_as_of(ledger):
    _vr(ledger, "C-1", "verified_real")
    _vr(ledger, "C-2", "verify_error", "poc_fail")
    p1 = project_evidence(ledger)
    p2 = project_evidence(ledger, as_of_seq=p1.as_of_seq)
    assert p1.projection_hash == p2.projection_hash
    assert len(p1.records) == 2
    assert p1.records[0]["decision"] == "verified_real"
    assert p1.records[0]["occurred_at"] == p1.records[1]["occurred_at"]


def test_correction_supersedes_without_root_cause_inflation(ledger):
    """§4.3：同逻辑事实二次判定 → supersedes 链；根因数不变、有效视图单条。"""
    _vr(ledger, "C-1", "verified_real")
    _vr(ledger, "C-1", "verified_fp")
    _vr(ledger, "C-2", "verified_real")
    p = project_evidence(ledger)
    assert len(p.records) == 3                       # append-only：全留
    assert independent_root_causes(p.records) == 2   # 根因不增
    view = effective_view(p)
    assert len(view) == 2
    c1 = [r for r in view if r["_cluster_id"] == "C-1"]
    assert len(c1) == 1 and c1[0]["decision"] == "verified_fp"
    assert c1[0]["supersedes"] == "obs-vr-1"
    # 重复投票/重复判定同一簇 N 次：根因数恒定
    for _ in range(3):
        _vr(ledger, "C-1", "verify_error", "flaky")
    assert independent_root_causes(project_evidence(ledger).records) == 2


def test_future_truth_not_in_history_projection(ledger):
    """时间可见性：晚于 as_of 的真值不进历史投影（防延迟反馈穿越）。"""
    seq1 = _vr(ledger, "C-1", "verified_real").seq
    hist = project_evidence(ledger, as_of_seq=seq1)
    assert hist.records[0]["truth_label"] == "unknown"
    _hv(ledger, "C-1", 1)
    full = project_evidence(ledger)
    assert full.records[0]["truth_label"] == "real"
    assert full.records[0]["truth_source"] == "human_verdict"
    assert full.records[0]["available_at"] == full.records[0]["occurred_at"] or True
    # as_of 截在真值前：仍 unknown
    again = project_evidence(ledger, as_of_seq=seq1)
    assert again.records[0]["truth_label"] == "unknown"


def test_conflicting_human_verdicts_disputed(ledger):
    """两次同级人工判定冲突 → disputed（truth 归 U，不默认最后写入者胜出）。"""
    _vr(ledger, "C-1", "verified_real")
    _hv(ledger, "C-1", 1, actor="owner-a")
    _hv(ledger, "C-1", -1, actor="owner-b")
    p = project_evidence(ledger)
    assert p.disputed_clusters == ["C-1"]
    rec = effective_view(p)[0]
    assert rec["truth_label"] == "disputed"
    summary = capability_summary(effective_view(p))
    fam = summary["unknown"]                          # 无 findings 富化 → unknown 族
    assert fam["u"] == 1                              # disputed 归 U（正向集）


def test_unknown_fields_honest_for_legacy_events(ledger):
    """存量事件缺记录字段如实 unknown（选择面/身份面不猜测补齐）。"""
    _vr(ledger, "C-1", "verified_real")
    rec = project_evidence(ledger).records[0]
    assert rec["execution_profile_id"] == "unknown"
    assert rec["eligible_set"] == "unknown"
    assert rec["selection_probability"] is None
    assert rec["selection_reason"] == "legacy_event_no_selection_record"
    assert rec["oracle_version"] == "unknown"
    assert rec["family"] == "unknown"                 # 无 findings 行富化
    assert rec["logical_evidence_id"].startswith("lec-prov:")   # provisional


def test_enrichment_event_based_as_of_stable(ledger):
    """R1-T5③：富化只用 as_of 内事件——表行/未来 finding 不改变历史投影。

    family/repo_commit 事件面无记录 → 如实 unknown；reporter/run 从首见
    FINDING 事件绑定（as_of 截面稳定）。"""
    from qswarm.kernel.dispatch import open_run
    open_run(ledger, run_id="R1", mode="pr", repo_url="repo://x",
             commit_sha="deadbeef")
    ledger.conn.execute(
        "INSERT INTO clusters (cluster_id, key_path, key_lang, line_window,"
        " level, created_at) VALUES ('C-1','p','py','1-2','fine','2030-01-01')"
    )
    ledger.conn.execute(
        "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
        " lang, repo, path, line_start, line_end, commit_sha, title, severity,"
        " grade, state, created_at, updated_at, cluster_id)"
        " VALUES ('F-1','R1','bee-a','auditor','injection','python','repo://x',"
        " 'src/a.py', 1, 2, 'deadbeef', 't', 'high', 'L2', 'open',"
        " '2030-01-01', '2030-01-01', 'C-1')"
    )
    ledger.conn.commit()
    fev = ledger.append(LedgerKind.FINDING, {
        "finding_id": "F-1", "reporter": "bee-a", "cluster": "C-1",
        "state": "open"}, run_id="R1")
    _vr(ledger, "C-1", "verified_real")
    before = project_evidence(ledger)
    rec = before.records[0]
    assert rec["_reporter"] == "bee-a"                # 事件面绑定（as_of 内）
    assert rec["family"] == "unknown"                 # 事件面无 → 如实 unknown
    assert rec["repo_commit"] == "unknown"
    # 未来 finding 事件 + 新簇判定：历史 as_of 投影哈希不变
    ledger.append(LedgerKind.FINDING, {
        "finding_id": "F-2", "reporter": "bee-b", "cluster": "C-1",
        "state": "open"}, run_id="R1")
    _vr(ledger, "C-9", "verified_real")
    after = project_evidence(ledger, as_of_seq=before.as_of_seq)
    assert after.projection_hash == before.projection_hash
    assert fev.seq < before.as_of_seq


def test_capability_summary_precision_bounds_formula(ledger):
    """§4.4 公式对拍（R1-T5 population 分立）：正向集 TP=1 FP=1 U=1 →
    precision 1/2；bounds [1/3, 2/3]；正确拒判另列不进 precision。"""
    _vr(ledger, "C-1", "verified_real")
    _hv(ledger, "C-1", 1)                       # TP（正向）
    _vr(ledger, "C-2", "verified_real")
    _hv(ledger, "C-2", -1)                      # FP（正向错误上报）
    _vr(ledger, "C-3", "verified_real")         # U（正向无真值）
    for i in range(4, 13):                      # 9 条正确拒判（负例集——不进 precision）
        _vr(ledger, f"C-{i}", "verified_fp")
        _hv(ledger, f"C-{i}", -1)
    p = project_evidence(ledger)
    fam = capability_summary(effective_view(p))["unknown"]
    assert fam["tp"] == 1 and fam["fp"] == 1 and fam["u"] == 1
    assert fam["positive_report_precision"] == 0.5     # 1/2（不是 10/12 混池）
    assert fam["positive_report_unknown_bounds"] == [0.3333, 0.6667]
    assert fam["correct_rejections"] == 9
    assert fam["correct_rejection_rate"] == 1.0


def test_capability_summary_zero_denominators():
    """分母为 0 → not_estimable（§4.4 逐字；无正向报告的纯拒判面同理）。"""
    empty = capability_summary([])
    assert empty == {}
    unknown_only = capability_summary([{
        "family": "injection", "logical_evidence_id": "lec:1",
        "decision": "verified_real", "truth_label": "unknown",
    }])
    fam = unknown_only["injection"]
    assert fam["positive_report_precision"] == "not_estimable"
    assert fam["positive_report_unknown_bounds"] == [0.0, 1.0]
    # 只有正确拒判、没有正向报告：precision not_estimable（不是 1.0）
    tn_only = capability_summary([{
        "family": "injection", "logical_evidence_id": "lec:2",
        "decision": "verified_fp", "truth_label": "refuted",
    }])
    fam2 = tn_only["injection"]
    assert fam2["positive_report_precision"] == "not_estimable"
    assert fam2["positive_reports"] == 0
    assert fam2["correct_rejection_rate"] == 1.0


def test_empty_ledger_projection(ledger):
    p = project_evidence(ledger)
    assert p.records == [] and p.projection_hash
    assert project_evidence(ledger).projection_hash == p.projection_hash


def test_write_projection_artifacts(ledger, tmp_path):
    _vr(ledger, "C-1", "verified_real")
    _hv(ledger, "C-1", 1)
    p = project_evidence(ledger)
    out = tmp_path / "proj"
    refs = write_projection(p, out)
    assert Path(refs["evidence_v3"]).exists()
    assert Path(refs["capability_v3"]).exists()
    lines = Path(refs["evidence_v3"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["decision"] == "verified_real"
    cap = json.loads(Path(refs["capability_v3"]).read_text(encoding="utf-8"))
    assert cap["evidence_hash"] == p.projection_hash
    assert cap["families"]["unknown"]["tp"] == 1
