"""kernel.cluster 聚类归并回归（SPEC-2 §1；SPEC-5 §1.1 A 组最低用例集）。

钉住的判据（用例名与 SPEC-5 §1.1 表逐一对映）：

- 两级判据：行窗重叠交集/min（D1）、粗判据四条件与 CWE 三态（D3）、精判据
  sim_fn 缺省 None 恒 False（D4）、all-pairwise 不做传递闭包（D2）、候选序
  字典序先到先得（§6.5）；
- 建簇/扩簇/拆分：cluster_id = C-{date}-{seq:06d}（D20 库内自增域）、表行与
  cluster_merge 事件同事务（§7.1/D24）、幂等键 (level, frozenset(members))、
  拆分零存款 + verdict 清空 + 守卫 ValueError；
- 簇级结论传播：§6.5 归属展开序（D7）与权重、src_seq 幂等键（D6）、冲突补记
  不回滚（§6.2）、verify_error/direction=0 零存款、崩溃窗口修复（§6.3）、
  源事件一致性校验（D23）、human_verdict 三向语义 + 终态守卫先于方向分支（D21）；
- S1 评审聚合：多数/平票/分歧（D22）与 close_panel 状态流转 + 守卫。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from qswarm.contracts import LedgerKind, SignalTier, load_params
from qswarm.kernel import (
    CLUSTER_EMBED_SIM_MIN,
    CLUSTER_OVERLAP_MIN,
    S1_SOURCE_W,
    S2_SOURCE_W,
    S3_SOURCE_W,
    ClusterProposal,
    FindingFacts,
    KernelLedger,
    MemberFacts,
    SteppedClock,
    VerdictContext,
    VoteFacts,
    aggregate_votes,
    apply_human_verdict,
    apply_merges,
    attribute_findings,
    close_panel,
    cluster_findings,
    coarse_mergeable,
    fine_mergeable,
    line_overlap,
    propagate_pending,
    propagate_verdict,
    split_cluster,
)

T0 = datetime(2031, 3, 4, tzinfo=timezone.utc)
DEPOSITS = LedgerKind.DEPOSIT


# ── 夹具：账本 + findings/votes 行种子 ────────────────────────────

def _ledger() -> KernelLedger:
    led = KernelLedger(":memory:", clock=SteppedClock(T0, 1.0))
    led.conn.execute(
        "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
        " status, started_at, meta_json) VALUES ('R-T','full','repo',600,0,"
        "'running','2031-03-04T00:00:00Z','{}')"
    )
    led.conn.commit()
    return led


def _seed_finding(
    led: KernelLedger,
    finding_id: str,
    *,
    path: str = "a.py",
    line_start: int = 10,
    line_end: int = 20,
    cwe: str | None = "CWE-79",
    lang: str = "py",
    family: str = "injection",
    type_: str | None = "xss",
    reporter: str = "A1",
    role: str = "auditor",
    state: str = "open",
) -> None:
    led.conn.execute(
        "INSERT INTO findings (id, run_id, reporter, reporter_role, family, type,"
        " lang, repo, path, func, line_start, line_end, commit_sha, cluster_id,"
        " title, severity, cwe, confidence, grade, state, disagreement_json,"
        " created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            finding_id, "R-T", reporter, role, family, type_, lang, "repo", path,
            None, line_start, line_end, "c", None, "t", "high", cwe, 0.5, "L1",
            state, None, "2031-03-04T00:00:00Z", "2031-03-04T00:00:00Z",
        ),
    )


def _seed_vote(
    led: KernelLedger,
    finding_id: str,
    reviewer: str,
    direction: int,
    *,
    dims: tuple[float, float, float, float] = (0.8, 0.7, 0.9, 0.1),
) -> None:
    led.conn.execute(
        "INSERT INTO votes (vote_id, finding_id, reviewer, dim_completeness,"
        " dim_reachability, dim_exploitability, dim_fp_suspicion, direction,"
        " comment_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"V-{finding_id}-{reviewer}", finding_id, reviewer, *dims, direction,
         None, "2031-03-04T00:00:00Z"),
    )


def _facts(fid: str, **kw) -> FindingFacts:
    base = dict(
        path="a.py", line_start=10, line_end=20, cwe="CWE-79", lang="py",
        family="injection", type="xss",
    )
    base.update(kw)
    return FindingFacts(fid, **base)


def _ctx(*members: tuple[str, str, str], scouts=(), votes=(), verifier=None) -> VerdictContext:
    return VerdictContext(
        members=tuple(MemberFacts(f, r, role) for f, r, role in members),
        scouts=tuple(scouts),
        votes=tuple(votes),
        verifier_id=verifier,
    )


def _deposits(led: KernelLedger):
    return led.read(kinds=(DEPOSITS,))


# ── §1.2 判据纯函数 ──────────────────────────────────────────────

def test_line_overlap_intersection_over_min():
    """D1：交集行数 / min(两窗行数)；子窗含于大窗 = 1.0；空窗/start>end = 0.0。"""
    assert line_overlap((10, 20), (12, 18)) == 1.0          # 子窗含于大窗
    assert line_overlap((10, 20), (15, 30)) == pytest.approx(6 / 11)
    assert line_overlap((1, 5), (10, 20)) == 0.0            # 不相交
    assert line_overlap((5, 5), (5, 9)) == 1.0              # 单行窗恰等
    assert line_overlap((0, 0), (1, 2)) == 0.0              # size ≤ 0（start>end 退化）
    assert line_overlap((7, 3), (7, 3)) == 0.0              # start>end → 0.0
    assert line_overlap((10, 20), (20, 30)) == pytest.approx(1 / 11)  # 边界行相交


def test_coarse_mergeable_requires_path_lang_cwe_overlap():
    """粗判据：同 path ∧ 同 lang ∧ CWE 相等 ∧ 重叠 ≥ 0.5；CWE 双 None 退
    (family,type)、恰一侧 None → False（D3）。"""
    fa = _facts("F1")
    assert coarse_mergeable(fa, _facts("F2", line_start=12, line_end=18))
    assert not coarse_mergeable(fa, _facts("F3", path="b.py", line_start=12, line_end=18))
    assert not coarse_mergeable(fa, _facts("F4", lang="go", line_start=12, line_end=18))
    assert not coarse_mergeable(fa, _facts("F5", cwe="CWE-89", line_start=12, line_end=18))
    # 重叠恰在阈值（含等号）：交集 6 / min(11, 11) = 0.545…；构造恰 = 0.5 → 交集/11=0.5 不可整，
    # 用 10/20：交集 1..? 直接验证阈值语义：交集 5、min 10 → 0.5 含等号
    assert coarse_mergeable(_facts("F6", line_start=1, line_end=10),
                            _facts("F7", line_start=6, line_end=15))
    assert not coarse_mergeable(_facts("F8", line_start=1, line_end=10),
                                _facts("F9", line_start=7, line_end=16))  # 4/10 < 0.5
    # 双 None：同 (family, type) 可并；异 type 不可并
    assert coarse_mergeable(_facts("FA", cwe=None), _facts("FB", cwe=None, line_start=12, line_end=18))
    assert not coarse_mergeable(_facts("FC", cwe=None), _facts("FD", cwe=None, type="sqli",
                                                                line_start=12, line_end=18))
    # 恰一侧 None → False（保守）
    assert not coarse_mergeable(fa, _facts("FE", cwe=None, line_start=12, line_end=18))
    assert CLUSTER_OVERLAP_MIN == 0.5


def test_fine_mergeable_never_merges_without_sim_fn():
    """精判据：sim_fn=None 恒 False（D4）；阈值 0.9 含等号边界。"""
    fa, fb = _facts("F1"), _facts("F2", line_start=12, line_end=18)
    assert not fine_mergeable(fa, fb, sim_fn=None)
    assert fine_mergeable(fa, fb, sim_fn=lambda a, b: 0.9)      # 含等号
    assert not fine_mergeable(fa, fb, sim_fn=lambda a, b: 0.8999)
    assert not fine_mergeable(                                  # 粗判据不过 → 恒 False
        fa, _facts("F3", cwe="CWE-89", line_start=12, line_end=18),
        sim_fn=lambda a, b: 1.0,
    )
    assert CLUSTER_EMBED_SIM_MIN == 0.9


def test_cluster_findings_all_pairwise_no_transitive_closure():
    """D2：A~B~C 而 A≁C 时不并 C（不做单链传递闭包）。"""
    a = _facts("A", line_start=1, line_end=20)
    b = _facts("B", line_start=2, line_end=10)      # 与 A 重叠 9/10、与 C 重叠 4/10 <0.5
    c = _facts("C", line_start=7, line_end=15)      # 与 A 重叠 14/15 ✓、与 B 4/10 ✗
    proposals = cluster_findings([a, b, c], level="coarse")
    assert [(p.member_ids, p.line_window) for p in proposals] == [(("A", "B"), (1, 20))]


def test_cluster_findings_deterministic_order_first_fit():
    """候选序 (path,start,end,id) 字典序；一 finding 至多一提案（先到先得）；
    assigned 已归属不参与；键/包络/最小相似度输出。"""
    f = [
        _facts("Z9", path="b.py", line_start=5, line_end=9, cwe=None, family="deser", type=None),
        _facts("A1", path="a.py", line_start=10, line_end=20),
        _facts("M5", path="a.py", line_start=12, line_end=18),
        _facts("B2", path="a.py", line_start=12, line_end=18),
        _facts("C3", path="c.py", line_start=1, line_end=5, cwe="CWE-200"),
        _facts("D4", path="c.py", line_start=3, line_end=9, cwe="CWE-200"),
    ]
    coarse = cluster_findings(f, level="coarse")
    # 候选序 a.py: A1(10,20) → B2(12,18) → M5(12,18)（同键按 finding_id 字典序）；
    # b.py: Z9 单例；c.py: C3(1,5) → D4(3,9)（重叠 3/5 ≥ 0.5）
    assert [(p.member_ids, p.key_path, p.key_cwe, p.line_window) for p in coarse] == [
        (("A1", "B2", "M5"), "a.py", "CWE-79", (10, 20)),
        (("C3", "D4"), "c.py", "CWE-200", (1, 9)),
    ]
    # B2/Z9 单例不产出提案；candidate 序按 (path,start,end,id) → a.py 三条先于 c.py、b.py
    assert [p.level for p in coarse] == ["coarse", "coarse"]
    assert all(p.sim_min is None for p in coarse)
    # assigned：A1 已归属 → B2/M5 仍在同簇（A1 不参与），A1 自身不入新提案
    coarse2 = cluster_findings(f, level="coarse", assigned={"A1": "C-1"})
    assert coarse2[0].member_ids == ("B2", "M5")
    # fine：sim 注入下成员对最小相似度进提案（sim_min）
    fine = cluster_findings(f, level="fine", sim_fn=lambda a, b: 0.95)
    assert [(p.member_ids, p.sim_min) for p in fine] == [
        (("A1", "B2", "M5"), 0.95), (("C3", "D4"), 0.95),
    ]
    # fine：sim 0.5 → 全不并（精判据收紧，宁可漏并）
    assert cluster_findings(f, level="fine", sim_fn=lambda a, b: 0.5) == []
    # sim_fn=None → 精聚类不产生任何合并（D4）
    assert cluster_findings(f, level="fine") == []
    with pytest.raises(ValueError, match="level"):
        cluster_findings(f, level="ultra")


def test_apply_merges_cluster_id_and_event_same_transaction():
    """cluster_id=C-{YYYYMMDD}-{seq:06d}；clusters/cluster_members 行 + cluster_merge
    事件同事务；members=本次并入集（D5）；findings.cluster_id 回写（占位簇号替换）。"""
    led = _ledger()
    _seed_finding(led, "F1"); _seed_finding(led, "F2", line_start=12, line_end=18)
    _seed_finding(led, "F8", line_start=11, line_end=21)
    led.conn.commit()
    prop = ClusterProposal(
        level="coarse", member_ids=("F1", "F2", "F8"), key_path="a.py",
        key_cwe="CWE-79", key_lang="py", line_window=(10, 21), sim_min=None,
    )
    events = apply_merges(led, [prop], run_id="R-T")
    assert [e.kind for e in events] == [LedgerKind.CLUSTER_MERGE]
    cid = events[0].payload["cluster_id"]
    assert cid == "C-20310304-000001"
    assert events[0].payload == {
        "cluster_id": cid, "members": ["F1", "F2", "F8"], "level": "coarse",
    }
    row = led.conn.execute(
        "SELECT key_path, key_cwe, key_lang, line_window, embedding_sim, level,"
        " verdict_tier, verdict_dir, verdict_at, created_at FROM clusters WHERE cluster_id = ?",
        (cid,),
    ).fetchone()
    assert row[:5] == ("a.py", "CWE-79", "py", "10-21", None)
    assert row[5] == "coarse" and row[6:9] == (None, None, None)
    assert row[9] == events[0].ts                       # created_at = 事件 ts（重放锚）
    members = led.conn.execute(
        "SELECT finding_id FROM cluster_members WHERE cluster_id = ? ORDER BY finding_id",
        (cid,),
    ).fetchall()
    assert [m[0] for m in members] == ["F1", "F2", "F8"]
    assert led.conn.execute(
        "SELECT COUNT(*) FROM findings WHERE cluster_id = ?", (cid,)
    ).fetchone()[0] == 3
    # 扩簇（提案 ⊇ 既有簇现员）：不新建簇，事件 = 并入集（D5 增量）
    _seed_finding(led, "F9", line_start=25, line_end=30)
    led.conn.commit()
    events2 = apply_merges(led, [ClusterProposal(
        level="coarse", member_ids=("F1", "F2", "F8", "F9"), key_path="a.py",
        key_cwe="CWE-79", key_lang="py", line_window=(10, 30), sim_min=None,
    )], run_id="R-T")
    assert events2[0].payload["cluster_id"] == cid
    assert events2[0].payload["members"] == ["F9"]      # 扩簇事件 = 并入集（D5 增量）
    # D20：同库同日期续号（新簇）
    _seed_finding(led, "G1", path="b.py", line_start=1, line_end=9)
    _seed_finding(led, "G2", path="b.py", line_start=2, line_end=8)
    led.conn.commit()
    events3 = apply_merges(led, [ClusterProposal(
        level="coarse", member_ids=("G1", "G2"), key_path="b.py",
        key_cwe="CWE-79", key_lang="py", line_window=(1, 9), sim_min=None,
    )], run_id="R-T")
    assert events3[0].payload["cluster_id"] == "C-20310304-000002"
    led.close()


def test_apply_merges_idempotent_zero_events():
    """幂等键 (level, frozenset(members)) 已存在 → 零新事件零表变更（§6.4 读侧幂等）。"""
    led = _ledger()
    _seed_finding(led, "F1"); _seed_finding(led, "F2", line_start=12, line_end=18)
    led.conn.commit()
    prop = ClusterProposal(level="coarse", member_ids=("F1", "F2"), key_path="a.py",
                           key_cwe="CWE-79", key_lang="py", line_window=(10, 20), sim_min=None)
    first = apply_merges(led, [prop])
    assert len(first) == 1
    before = (
        led.conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0],
        led.conn.execute("SELECT COUNT(*) FROM cluster_members").fetchone()[0],
        led.latest_seq(),
    )
    assert apply_merges(led, [prop]) == []              # 完全同键 → []
    # 成员集相同（顺序无关）→ 幂等
    assert apply_merges(led, [ClusterProposal(**{**prop.__dict__, "member_ids": ("F2", "F1")})]) == []
    after = (
        led.conn.execute("SELECT COUNT(*) FROM clusters").fetchone()[0],
        led.conn.execute("SELECT COUNT(*) FROM cluster_members").fetchone()[0],
        led.latest_seq(),
    )
    assert before == after
    # 部分重叠非子集 → ValueError（保守拒绝，不静默改写）
    with pytest.raises(ValueError, match="部分重叠"):
        apply_merges(led, [ClusterProposal(
            level="coarse", member_ids=("F2",), key_path="a.py", key_cwe="CWE-79",
            key_lang="py", line_window=(12, 18), sim_min=None)])
    led.close()


def test_split_cluster_zero_deposit_and_verdict_reset():
    """拆分零存款、子簇 verdict_* 清空（NULL 建行）、groups<2 / 并集≠全集 /
    簇不存在 / 已拆分 → ValueError；子簇成员进账本（cluster_merge 事件）。"""
    led = _ledger()
    _seed_finding(led, "F1"); _seed_finding(led, "F2", line_start=12, line_end=18)
    _seed_finding(led, "F3", line_start=25, line_end=30)
    led.conn.commit()
    ev = apply_merges(led, [ClusterProposal(
        level="coarse", member_ids=("F1", "F2", "F3"), key_path="a.py",
        key_cwe="CWE-79", key_lang="py", line_window=(10, 30), sim_min=None)])
    cid = ev[0].payload["cluster_id"]
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verified_real"})
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"), ("F3", "A3", "auditor"),
               verifier="V9")
    propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                      source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    n_deposits_before = len(_deposits(led))
    n_events_before = led.latest_seq()

    with pytest.raises(ValueError, match="至少需要 2 组"):
        split_cluster(led, cid, [["F1", "F2", "F3"]], actor="human")
    with pytest.raises(ValueError, match="并集"):
        split_cluster(led, cid, [["F1"], ["F2"]], actor="human")
    with pytest.raises(ValueError, match="簇不存在"):
        split_cluster(led, "C-19990101-000001", [["F1"], ["F2"]], actor="human")
    assert led.latest_seq() == n_events_before          # 守卫失败零事件

    events = split_cluster(led, cid, [["F3"], ["F1", "F2"]], actor="human")
    assert [e.kind for e in events] == [
        LedgerKind.CLUSTER_MERGE, LedgerKind.CLUSTER_MERGE, LedgerKind.CLUSTER_SPLIT,
    ]
    split_ev = events[-1]
    assert split_ev.payload["cluster_id"] == cid
    # 子簇建簇序 = 组内最小 finding_id 字典序：{F1,F2} < {F3}
    assert events[0].payload["members"] == ["F1", "F2"]
    assert events[1].payload["members"] == ["F3"]
    assert split_ev.payload["new_clusters"] == [
        events[0].payload["cluster_id"], events[1].payload["cluster_id"],
    ]
    assert len(_deposits(led)) == n_deposits_before     # 拆分零存款（§3.4 逐字）
    sub = events[0].payload["cluster_id"]
    row = led.conn.execute(
        "SELECT verdict_tier, verdict_dir, verdict_at FROM clusters WHERE cluster_id = ?",
        (sub,),
    ).fetchone()
    assert row == (None, None, None)                    # 子簇 verdict 投影清空
    assert led.conn.execute(
        "SELECT COUNT(*) FROM cluster_members WHERE cluster_id = ?", (cid,)
    ).fetchone()[0] == 0                                # 原簇成员清空
    assert led.conn.execute(
        "SELECT cluster_id FROM findings WHERE id = 'F1'"
    ).fetchone()[0] == sub                              # findings.cluster_id 随迁
    # 已拆分（现员为空）→ ValueError
    with pytest.raises(ValueError, match="已拆分"):
        split_cluster(led, cid, [["F1"], ["F2"]], actor="human")
    led.close()


# ── §1.4 簇级结论传播 ────────────────────────────────────────────

def _cluster_with_members(led: KernelLedger, ids=(("F1", "A1"), ("F2", "A2"))):
    for fid, rep in ids:
        _seed_finding(led, fid, reporter=rep)
    led.conn.commit()
    prop = ClusterProposal(
        level="coarse", member_ids=tuple(fid for fid, _ in ids), key_path="a.py",
        key_cwe="CWE-79", key_lang="py", line_window=(10, 20), sim_min=None,
    )
    ev = apply_merges(led, [prop])
    return ev[0].payload["cluster_id"]


def test_propagate_verdict_expansion_order_and_weights():
    """展开序 = 成员(finding_id 字典序)→侦察(字典序)→评审(字典序)→验证（D7）；
    scout×SCOUT_X、评审同向+/反向−×REVIEWER_X、S3 验证行 VERIFIER_CONFIRM/CONFLICT；
    每笔携带 src_seq（D6）。"""
    params = load_params()
    led = _ledger()
    cid = _cluster_with_members(led)
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verified_real"})
    ctx = _ctx(
        ("F2", "A2", "auditor"), ("F1", "A1", "auditor"),
        scouts=("S2", "S1"), votes=(("R2", 1), ("R1", -1)), verifier="V9",
    )
    events = propagate_verdict(
        led, cid, tier=SignalTier.S2, direction=1, source_seq=vsev.seq,
        source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx,
    )
    rows = [(e.payload["agent"], e.payload["role"], e.payload["direction"],
             round(e.payload["weight"], 6), e.payload["finding"], e.payload["src_seq"])
            for e in events]
    sx, rx = params.deposits.SCOUT_X, params.deposits.REVIEWER_X
    assert rows == [
        ("A1", "auditor", 1, S2_SOURCE_W * 1.0, "F1", vsev.seq),   # 成员行（finding_id 序）
        ("A2", "auditor", 1, S2_SOURCE_W * 1.0, "F2", vsev.seq),
        ("S1", "scout", 1, round(S2_SOURCE_W * sx, 6), None, vsev.seq),   # 侦察行（字典序）
        ("S2", "scout", 1, round(S2_SOURCE_W * sx, 6), None, vsev.seq),
        ("R1", "reviewer", -1, round(S2_SOURCE_W * rx, 6), None, vsev.seq),  # 评审反向 −
        ("R2", "reviewer", 1, round(S2_SOURCE_W * rx, 6), None, vsev.seq),   # 评审同向 +
    ]
    # verdict 投影：tier/dir = S2/+1，verdict_at = 最后报告者行 ts（CL3 折叠锚）
    tier, dir_, at = led.conn.execute(
        "SELECT verdict_tier, verdict_dir, verdict_at FROM clusters WHERE cluster_id = ?",
        (cid,),
    ).fetchone()
    assert (tier, dir_) == ("S2", 1)
    assert at == events[1].ts                            # F2 行（最后报告者行）
    # S1 源权重下沿
    fsev = led.append("finding", {"finding_id": "F1", "reporter": "A1",
                                  "cluster": cid, "state": "voted"})
    s1 = propagate_verdict(led, cid, tier=SignalTier.S1, direction=1,
                           source_seq=fsev.seq, source_kind=LedgerKind.FINDING, ctx=ctx)
    assert s1[0].payload["weight"] == pytest.approx(S1_SOURCE_W)
    # S3：先验 S2 → 验证行；同向 CONFIRM / 反向 CONFLICT（§6.5）
    hsev = led.append("human_verdict", {"cluster_id": cid, "direction": -1,
                                        "actor": "h"})
    s3 = propagate_verdict(led, cid, tier=SignalTier.S3, direction=-1,
                           source_seq=hsev.seq, source_kind=LedgerKind.HUMAN_VERDICT, ctx=ctx)
    vrow = s3[-1].payload
    assert (vrow["agent"], vrow["role"], vrow["signal_tier"], vrow["direction"],
            vrow["weight"]) == ("V9", "verifier", "S3", -1, params.deposits.VERIFIER_CONFLICT)
    hsev2 = led.append("human_verdict", {"cluster_id": cid, "direction": 1, "actor": "h"})
    s3b = propagate_verdict(led, cid, tier=SignalTier.S3, direction=1,
                            source_seq=hsev2.seq, source_kind=LedgerKind.HUMAN_VERDICT,
                            ctx=_ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"),
                                     verifier="V9"))
    assert s3b[-1].payload["weight"] == params.deposits.VERIFIER_CONFIRM
    assert s3b[-1].payload["direction"] == 1
    # verifier_id 缺失 + 先验存在 → ValueError
    hsev3 = led.append("human_verdict", {"cluster_id": cid, "direction": -1, "actor": "h"})
    with pytest.raises(ValueError, match="verifier_id"):
        propagate_verdict(led, cid, tier=SignalTier.S3, direction=-1,
                          source_seq=hsev3.seq, source_kind=LedgerKind.HUMAN_VERDICT,
                          ctx=_ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor")))
    led.close()


def test_propagate_verdict_idempotent_by_cluster_tier_src_seq():
    """同 (cluster, tier, src_seq) 重复 → 零新事件（§6.4）；不同 src_seq/tier 各自成立。"""
    led = _ledger()
    cid = _cluster_with_members(led)
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"))
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verified_real"})
    first = propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                              source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    assert len(first) == 2                              # 恰 2 成员报告者行（无侦察/评审行）
    assert propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                             source_seq=vsev.seq,
                             source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx) == []
    n = led.latest_seq()
    assert propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                             source_seq=vsev.seq,
                             source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx) == []
    assert led.latest_seq() == n
    # 同源事件不同 tier → 独立成立（幂等键含 tier）
    s1 = propagate_verdict(led, cid, tier=SignalTier.S1, direction=1,
                           source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    assert s1 and all(e.payload["src_seq"] == vsev.seq for e in s1)
    # 源事件校验（D23）：seq 不存在 / kind 不符 → ValueError
    with pytest.raises(ValueError, match="不存在"):
        propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                          source_seq=99999, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    with pytest.raises(ValueError, match="kind"):
        propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                          source_seq=vsev.seq, source_kind=LedgerKind.VOTE, ctx=ctx)
    with pytest.raises(ValueError, match="source_kind"):
        propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                          source_seq=vsev.seq, source_kind="nonsense", ctx=ctx)
    led.close()


def test_propagate_verdict_conflict_appends_not_rolls_back():
    """§6.2 冲突语义：S3 与 S2/S1 冲突补记更强反向存款，不回滚旧存款。"""
    led = _ledger()
    cid = _cluster_with_members(led)
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"), verifier="V9")
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verified_real"})
    s2 = propagate_verdict(led, cid, tier=SignalTier.S2, direction=1,
                           source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    hsev = led.append("human_verdict", {"cluster_id": cid, "direction": -1, "actor": "h"})
    s3 = propagate_verdict(led, cid, tier=SignalTier.S3, direction=-1,
                           source_seq=hsev.seq, source_kind=LedgerKind.HUMAN_VERDICT, ctx=ctx)
    dirs = [e.payload["direction"] for e in _deposits(led)]
    assert dirs[: len(s2)] == [1] * len(s2)             # 旧 S2 存款原样保留（无回滚）
    assert dirs[len(s2):] == [-1] * len(s3)             # S3 补记更强反向（S3 权重 1.0）
    assert s3[0].payload["weight"] == S3_SOURCE_W
    # S2 与 S3 源权重并存的净额可收敛（ append-only 折叠由 agg_cells 负责）
    assert led.conn.execute(
        "SELECT verdict_tier, verdict_dir FROM clusters WHERE cluster_id = ?", (cid,)
    ).fetchone() == ("S3", -1)
    led.close()


def test_propagate_verdict_zero_direction_no_deposit():
    """direction=0 → 零存款零投影更新（§6.2/§6.3：不产生任何进化压力）。"""
    led = _ledger()
    cid = _cluster_with_members(led)
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"))
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verify_error"})
    assert propagate_verdict(led, cid, tier=SignalTier.S2, direction=0,
                             source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT,
                             ctx=ctx) == []
    assert _deposits(led) == []
    assert led.conn.execute(
        "SELECT verdict_tier, verdict_dir, verdict_at FROM clusters WHERE cluster_id = ?",
        (cid,),
    ).fetchone() == (None, None, None)
    with pytest.raises(ValueError, match="direction"):
        propagate_verdict(led, cid, tier=SignalTier.S2, direction=2,
                          source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT, ctx=ctx)
    led.close()


def test_propagate_pending_repairs_crash_window():
    """「源事件已落、派生存款未落」构造场景逐个补齐；lineage→None 的簇入 skipped
    不报错（§6.3 崩溃窗口收敛到零）。"""
    led = _ledger()
    cid = _cluster_with_members(led)
    _seed_finding(led, "F9", path="z.py", line_start=1, line_end=2, reporter="A9")
    led.conn.commit()
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"))
    # 崩溃窗口 ①：verify_result 已落、S2 存款未落
    vsev = led.append("verify_result", {"cluster_id": cid, "outcome": "verified_fp"})
    # 崩溃窗口 ②：human_verdict 已落、S3 存款未落（verify_error 路径零存款 → 不补）
    hsev = led.append("human_verdict", {"cluster_id": cid, "direction": 1, "actor": "h"})
    # 无谱系簇（M0 世界侧形态）：verify_result 落了但 lineage 缺失 → skipped
    orphan = led.append("verify_result", {"cluster_id": "C-PLACEHOLDER", "outcome": "verified_real"})
    full_ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"), verifier="V9")
    rep = propagate_pending(led, lineage=lambda c: full_ctx if c == cid else None)
    assert set(rep.skipped) == {"C-PLACEHOLDER"}        # 缺谱系 → 跳过不报错
    paid = {(e.payload["signal_tier"], e.payload["src_seq"]) for e in rep.events}
    assert paid == {("S2", vsev.seq), ("S3", hsev.seq)}
    assert all(e.payload["cluster"] == cid for e in rep.events)
    # S2 方向：verified_fp → −1；S3：human +1（验证行对先验 S2=−1 构成冲突 → −CONFLICT）
    by_tier = {}
    for e in rep.events:
        by_tier.setdefault(e.payload["signal_tier"], []).append(e.payload["direction"])
    assert by_tier == {"S2": [-1, -1], "S3": [1, 1, -1]}
    # 收敛：二次扫描零新事件（幂等键判定）
    rep2 = propagate_pending(led, lineage=lambda c: full_ctx if c == cid else None)
    assert rep2.events == () and rep2.skipped == ("C-PLACEHOLDER",)
    # verify_error（零存款路径）不补
    led2 = _ledger()
    cid2 = _cluster_with_members(led2)
    _seed_finding(led2, "FX", path="y.py", line_start=1, line_end=3, reporter="AX")
    led2.conn.commit()
    _ = cid2
    led2_seed_cluster_id = led2.conn.execute(
        "SELECT cluster_id FROM clusters").fetchone()[0]
    led2.append("verify_result", {"cluster_id": led2_seed_cluster_id, "outcome": "verify_error"})
    rep3 = propagate_pending(led2, lineage=lambda c: _ctx(("F1", "A1", "auditor"),
                                                          ("F2", "A2", "auditor")))
    assert rep3.events == () and rep3.skipped == ()
    # S1 修复：finding(voted) + vote 事件 → 方向从账本票向重 derive
    led3 = _ledger()
    cid3 = _cluster_with_members(led3)
    _seed_vote(led3, "F1", "R1", 1); _seed_vote(led3, "F1", "R2", 1)
    _seed_vote(led3, "F1", "R3", -1)
    led3.conn.commit()
    led3.append("vote", {"finding_id": "F1", "reviewer": "R1", "direction": 1})
    led3.append("vote", {"finding_id": "F1", "reviewer": "R2", "direction": 1})
    led3.append("vote", {"finding_id": "F1", "reviewer": "R3", "direction": -1})
    led3.append("finding", {"finding_id": "F1", "reporter": "A1", "cluster": cid3,
                            "state": "voted"})
    rep4 = propagate_pending(led3, lineage=lambda c: _ctx(("F1", "A1", "auditor"),
                                                          ("F2", "A2", "auditor")))
    assert {e.payload["signal_tier"] for e in rep4.events} == {"S1"}
    assert all(e.payload["direction"] == 1 for e in rep4.events)
    led.close(); led2.close(); led3.close()


def test_apply_human_verdict_three_way_and_terminal_guard():
    """+1 全员 CONFIRMED、−1 REJECTED、0=落事件零存款零状态；终态再裁 ValueError
    且先于 direction 分支（D21：direction=0 对终态簇同样拒绝、单事务回滚零落库）。"""
    led = _ledger()
    cid = _cluster_with_members(led)
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"), verifier="V9")
    n_events = led.latest_seq()
    zero = apply_human_verdict(led, cid, direction=0, actor="h", ctx=ctx)
    assert [e.kind for e in zero] == [LedgerKind.HUMAN_VERDICT]
    assert zero[0].payload == {"cluster_id": cid, "direction": 0, "actor": "h",
                               "source": "panel"}
    assert _deposits(led) == []                         # 忽略：零存款
    assert led.conn.execute(
        "SELECT state FROM findings WHERE id IN ('F1','F2') ORDER BY id"
    ).fetchall() == [("open",), ("open",)]              # 零状态变更
    plus = apply_human_verdict(led, cid, direction=1, actor="h", ctx=ctx)
    kinds = [e.kind for e in plus]
    assert kinds.count(LedgerKind.FINDING) == 2 and LedgerKind.HUMAN_VERDICT in kinds
    assert any(e.kind is LedgerKind.DEPOSIT for e in plus)
    assert led.conn.execute(
        "SELECT state FROM findings WHERE id IN ('F1','F2') ORDER BY id"
    ).fetchall() == [("confirmed",), ("confirmed",)]
    minus_target = _ledger()
    cid_m = _cluster_with_members(minus_target)
    minus = apply_human_verdict(minus_target, cid_m, direction=-1, actor="h",
                                ctx=_ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor")))
    assert minus_target.conn.execute(
        "SELECT state FROM findings WHERE id IN ('F1','F2') ORDER BY id"
    ).fetchall() == [("rejected",), ("rejected",)]
    # 终态守卫（D21）：任何方向（含 0）→ ValueError，零落库
    n_before = led.latest_seq()
    for d in (1, 0, -1):
        with pytest.raises(ValueError, match="终态"):
            apply_human_verdict(led, cid, direction=d, actor="h", ctx=ctx)
    assert led.latest_seq() == n_before                 # 单事务回滚零落库
    # 簇不存在 → ValueError
    with pytest.raises(ValueError, match="簇不存在"):
        apply_human_verdict(led, "C-19990101-000001", direction=1, actor="h", ctx=ctx)
    led.close(); minus_target.close()


# ── §1.5 S1 评审聚合 ─────────────────────────────────────────────

def test_aggregate_votes_majority_tie_disagreement():
    """方向 = 票向符号和符号（平票 0）；confidence = 多数票四维均值之平均（无多数
    0.5）；反向票 → disagreement=True（D22）。"""
    v = lambda r, d, a=0.8, b=0.6, c=0.9, e=0.1: VoteFacts(r, d, a, b, c, e)
    cons = aggregate_votes("F1", [v("R1", 1), v("R2", 1, 0.6, 0.6, 0.6, 0.6), v("R3", -1)])
    assert (cons.direction, cons.n_votes, cons.disagreement) == (1, 3, True)
    assert cons.confidence == pytest.approx((0.6 + 0.6) / 2)   # (0.8+0.6+0.9+0.1)/4 = 0.6
    tie = aggregate_votes("F1", [v("R1", 1), v("R2", -1)])
    assert (tie.direction, tie.confidence, tie.disagreement) == (0, 0.5, True)
    unanimous = aggregate_votes("F1", [v("R1", -1, 1.0, 1.0, 1.0, 0.0)])
    assert (unanimous.direction, unanimous.confidence, unanimous.disagreement) == (-1, 0.75, False)
    empty = aggregate_votes("F1", [])
    assert (empty.direction, empty.confidence, empty.disagreement, empty.n_votes) == (0, 0.5, False, 0)


def test_close_panel_state_s1_propagation_and_guards():
    """open→voted；confidence/disagreement_json 写入；direction≠0 → S1 存款
    （S1_SOURCE_W×角色系数）；=0 零存款；票数不足 ValueError；已 voted 零事件。"""
    params = load_params()
    led = _ledger()
    cid = _cluster_with_members(led)
    _seed_vote(led, "F1", "R1", 1, dims=(0.8, 0.8, 0.8, 0.8))
    _seed_vote(led, "F1", "R2", 1, dims=(0.6, 0.6, 0.6, 0.6))
    _seed_vote(led, "F1", "R3", -1, dims=(0.4, 0.4, 0.4, 0.4))
    led.conn.commit()
    ctx = _ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"),
               votes=(("R1", 1), ("R2", 1), ("R3", -1)))
    with pytest.raises(ValueError, match="票数不足"):
        close_panel(led, "F1", ctx=_ctx(votes=(("R1", 1),)), expected_votes=3)
    events = close_panel(led, "F1", ctx=ctx, expected_votes=3)
    kinds = [e.kind for e in events]
    assert kinds[0] is LedgerKind.FINDING
    assert events[0].payload["state"] == "voted"
    assert kinds.count(LedgerKind.DEPOSIT) == 5          # 成员 2 + 评审 3（无侦察行）
    row = led.conn.execute(
        "SELECT confidence, disagreement_json, state FROM findings WHERE id = 'F1'"
    ).fetchone()
    assert row[0] == pytest.approx(0.7)                  # 多数票四维均值之平均：(0.8+0.6)/2
    assert json.loads(row[1]) == {"disagreement": True}
    assert row[2] == "voted"
    s1 = [e for e in events if e.kind is LedgerKind.DEPOSIT]
    assert s1[0].payload["weight"] == pytest.approx(S1_SOURCE_W)          # auditor ×1.0
    assert s1[-1].payload["direction"] == -1             # R3 反向票 −REVIEWER_X
    assert s1[-1].payload["weight"] == pytest.approx(S1_SOURCE_W * params.deposits.REVIEWER_X)
    assert {e.payload["src_seq"] for e in s1} == {events[0].seq}
    # 已 voted → 零新事件（读侧幂等）
    assert close_panel(led, "F1", ctx=ctx, expected_votes=3) == []
    # 平票方向 0 → 只落状态与分歧，零存款
    led2 = _ledger()
    cid2 = _cluster_with_members(led2)
    _seed_vote(led2, "F1", "R1", 1); _seed_vote(led2, "F1", "R2", -1)
    led2.conn.commit()
    ev0 = close_panel(led2, "F1", ctx=_ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"),
                                           votes=(("R1", 1), ("R2", -1))),
                      expected_votes=2)
    assert [e.kind for e in ev0] == [LedgerKind.FINDING]
    assert _deposits(led2) == []
    # 票行与谱系不一致 → ValueError（同源纪律）
    led3 = _ledger()
    cid3 = _cluster_with_members(led3)
    _seed_vote(led3, "F1", "R1", 1)
    led3.conn.commit()
    with pytest.raises(ValueError, match="不一致"):
        close_panel(led3, "F1", ctx=_ctx(("F1", "A1", "auditor"), ("F2", "A2", "auditor"),
                                         votes=(("R1", -1), ("R2", 1))),
                    expected_votes=2)
    led.close(); led2.close(); led3.close()


# ══ attribute_findings：§2.1 粗聚类归属编排（仿真接通 §0.4 交付面）════════


def _open(
    led: KernelLedger,
    finding_id: str,
    *,
    path: str = "a.py",
    line_start: int = 10,
    line_end: int = 20,
    cwe: str | None = "CWE-79",
    lang: str = "py",
    family: str = "injection",
    type_: str | None = "xss",
) -> FindingFacts:
    """经 kernel.dispatch.open_finding 建档并返回同源事实面（归属输入=表行同源）。"""
    from qswarm.kernel import open_finding

    open_finding(
        led,
        finding_id=finding_id, run_id="R-T", reporter="A1", reporter_role="auditor",
        family=family, type=type_, lang=lang, repo="repo", path=path,
        line_start=line_start, line_end=line_end, title="t", severity="high",
        grade="L1", commit_sha="c", cwe=cwe,
    )
    return FindingFacts(
        finding_id=finding_id, path=path, line_start=line_start, line_end=line_end,
        cwe=cwe, lang=lang, family=family, type=type_,
    )


def _cluster_members_of(led: KernelLedger, cluster_id: str) -> list[str]:
    return [
        r[0] for r in led.conn.execute(
            "SELECT finding_id FROM cluster_members WHERE cluster_id = ?"
            " ORDER BY finding_id", (cluster_id,)
        )
    ]


def test_attribute_findings_merge_extend_and_singleton():
    """批内提案（cluster_findings）+ 既有簇扩簇（all-pairwise 复核）+ 单例落簇；
    全部簇号内核签发（C-{date}-{seq}），findings.cluster_id 同步回写。"""
    led = _ledger()
    f1 = _open(led, "F1", line_start=10, line_end=20)
    f2 = _open(led, "F2", line_start=12, line_end=18)
    f3 = _open(led, "F3", line_start=100, line_end=110)      # 同键不重叠 → 拒并
    m1 = attribute_findings(led, [f3, f1, f2])               # 入参序无关（候选字典序）
    assert m1["F1"] == m1["F2"] != m1["F3"]                  # F1/F2 并簇、F3 单例
    cids = sorted(set(m1.values()))
    assert cids == [f"C-20310304-{n:06d}" for n in (1, 2)]   # T0=2031-03-04 → 20310304
    assert sorted(_cluster_members_of(led, m1["F1"])) == ["F1", "F2"]
    assert _cluster_members_of(led, m1["F3"]) == ["F3"]
    rows = dict(led.conn.execute("SELECT id, cluster_id FROM findings").fetchall())
    assert rows == m1                                        # 回写 = 返回映射同源

    # 次批扩簇：F4 与 F1/F2 全员可并 → 并入既有簇（不新建）；F5 单例
    f4 = _open(led, "F4", line_start=14, line_end=22)
    f5 = _open(led, "F5", line_start=300, line_end=310)
    m2 = attribute_findings(led, [f4, f5])
    assert m2["F4"] == m1["F1"]
    assert m2["F5"] not in set(m1.values())
    assert sorted(_cluster_members_of(led, m1["F1"])) == ["F1", "F2", "F4"]

    # 幂等：同批重复归属 → 零新事件、映射不变
    seq0 = led.latest_seq()
    m3 = attribute_findings(led, [f1, f2, f3, f4, f5])
    assert m3 == {**m1, **m2}
    assert led.latest_seq() == seq0
    led.close()


def test_attribute_findings_ambiguous_two_clusters_conservative_singleton():
    """新 finding 与两个既有同键簇都全员可并 → 保守单例落簇（§3.4 宁漏勿错），
    不跨簇归并、不抛错打断编排（跨簇归并须先 split，CL1 同源纪律）。"""
    led = _ledger()
    g1 = _open(led, "G1", path="c.py", line_start=20, line_end=30)
    g2 = _open(led, "G2", path="c.py", line_start=26, line_end=40)
    mg = attribute_findings(led, [g1, g2])
    assert mg["G1"] != mg["G2"]        # all-pairwise：G1 vs G2 重叠 5/11≈0.45<0.5 → 分簇
    gx = _open(led, "GX", path="c.py", line_start=24, line_end=30)
    # GX vs G1(20-30): overlap 7/7=1.0 ✓；GX vs G2(26-40): overlap 5/7≈0.71 ✓
    assert line_overlap((24, 30), (20, 30)) >= CLUSTER_OVERLAP_MIN
    assert line_overlap((24, 30), (26, 40)) >= CLUSTER_OVERLAP_MIN
    mx = attribute_findings(led, [gx])
    assert mx["GX"] not in set(mg.values())                   # 歧义 → 保守单例
    assert len(_cluster_members_of(led, mx["GX"])) == 1
    led.close()


def test_attribute_findings_empty_and_invalid_level_and_duplicate_facts():
    """空 facts → {} 零事件；level 非法 / facts 重复 → ValueError（写侧非法）。"""
    led = _ledger()
    seq0 = led.latest_seq()
    assert attribute_findings(led, []) == {}
    assert led.latest_seq() == seq0
    f = _open(led, "F1")
    with pytest.raises(ValueError, match="level"):
        attribute_findings(led, [f], level="ultra")
    with pytest.raises(ValueError, match="重复"):
        attribute_findings(led, [f, f])
    led.close()


def test_attribute_findings_deterministic_and_hashseed_free():
    """同输入序 ⇒ 簇号签发序逐位一致（两账本独立重放对账）；零随机源。"""
    def build() -> dict:
        led = _ledger()
        facts = [
            _open(led, "F1", line_start=10, line_end=20),
            _open(led, "F2", line_start=12, line_end=18),
            _open(led, "F3", line_start=100, line_end=110),
            _open(led, "F4", line_start=14, line_end=22),
        ]
        m = attribute_findings(led, facts)
        events = [
            (str(e.kind), e.payload["cluster_id"], tuple(e.payload["members"]))
            for e in led.read(kinds=(LedgerKind.CLUSTER_MERGE,))
        ]
        led.close()
        return m, events

    m1, ev1 = build()
    m2, ev2 = build()
    assert m1 == m2 and ev1 == ev2
