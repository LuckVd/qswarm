"""重放等价专项验证：3 种子 × ≥3 检查点 rebuild==cells + 收尾全账本物理表比对。

账本构成（每种子一条独立确定性轨迹）：simulator 真实事件流（AgentPool +
TaskGenerator + run_findings_flow + deposit_schedule 的 deposit 存款），叠加 M1
编排内核复合操作——open_finding 建档（finding 事件）、cluster_findings 提案 +
apply_merges（cluster_merge：建簇/扩簇）、propagate_verdict（verify_result +
簇级 S2 存款）、re_route_finding（re_route 事件 + 侦察猜测格 S1 弱存款）、
split_cluster（cluster_split + 子簇 cluster_merge）、apply_human_verdict（拆分后
子簇 S3 直答存款），以及 promotion/demotion（status 重放跨时点非平凡）。

对应验收（taskflow「重放等价专项验证」子任务）：

- ac_mv1s9fkj6ks1gae5  专项测试存在、纳入常规全量并通过：含 cluster/split/merge/
                       re_route 事件的账本，3 种子 × 每种子 ≥3 个时点检查点做任意
                       时点 rebuild，与在线 cells 逐字段全等（CellAgg 全字段 +
                       簇/finding 投影）；收尾再做全账本重放，与 cells 物理表
                       （SQLite 表行）逐字段比对全等
- ac_mv1s9fkjfoznzu1k  至少一条断言针对 split/merge 后簇状态与存款归属正确性：
                       父簇拆空保历史投影（CL2）、子簇承接成员并吸收拆分后存款、
                       findings.cluster_id 随迁
- ac_mv1s9fkjvss7hnia  非手工脚本：本文件即常规 pytest 用例（pyproject
                       testpaths=tests），任何回归 ``pytest`` 直接复现

复用纪律：重放/比对唯一实现 qswarm/kernel/replay.py（rebuild_* / diff_*），聚合
唯一实现 kernel.cells.agg_cells——本文件零聚合逻辑复制；在线快照采集与 cells
物理表行口径沿用 tests/test_kernel_replay.py 与 scripts/verify_ac_cells.py。
"""

from __future__ import annotations

import random
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest

from qswarm.contracts import LedgerKind, SignalTier, load_params, load_taxonomy
from qswarm.kernel import (
    CellAgg,
    CellKey,
    ClusterProposal,
    FindingFacts,
    KernelLedger,
    MemberFacts,
    SteppedClock,
    VerdictContext,
    agg_cells,
    apply_human_verdict,
    apply_merges,
    cluster_findings,
    cluster_projection,
    diff_cells,
    diff_clusters,
    diff_findings,
    finding_projection,
    open_finding,
    propagate_verdict,
    rebuild_cells,
    rebuild_clusters,
    rebuild_findings,
    re_route_finding,
    split_cluster,
)
from qswarm.simulator import (
    AgentPool,
    TaskGenerator,
    deposit_schedule,
    run_findings_flow,
)

SEEDS = (11, 22, 33)                    # 3 种子（验收：3 种子 × 每种子 ≥3 检查点）
N_DAYS = 12                             # 短轨迹
_T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)   # 轨迹时间轴原点
_RUN = "R-REQ"
ALL_CELL_FIELDS = tuple(CellAgg.__dataclass_fields__)   # 全字段口径（含 updated_at）

# ── M1 复合操作的确定性事实面 ────────────────────────────────────

_FA, _FB, _FC, _FD, _F4 = "F-REQ-1", "F-REQ-2", "F-REQ-3", "F-REQ-4", "F-REQ-5"
_FINDINGS = (   # (finding_id, reporter, line_start, line_end)
    (_FA, "A1", 1, 40),         # 同族同型近窗三元组 → cluster_findings 真归并
    (_FB, "A2", 5, 44),         #   两两 overlap ≥ 0.9/0.8/0.9（阈值 CLUSTER_OVERLAP_MIN=0.5）
    (_FC, "A3", 9, 48),
    (_FD, "A4", 201, 240),      # 异窗 → 保守拒并（单例，无簇提案）；re_route 触发 finding
    (_F4, "A5", 100, 109),      # 扩簇成员（与三元组异窗，不参与首并提案；apply_merges 扩簇无重叠门槛）
)
_FACTS = tuple(
    FindingFacts(fid, "injection/sqli", ls, le, "CWE-79", "py", "injection", "sqli")
    for fid, _rep, ls, le in _FINDINGS
)


# ── 轨迹构造：simulator 存款流 + M1 复合操作 + status 事件 ────────

@dataclass(frozen=True)
class _Step:
    """轨迹步：apply 为单条事件落账或一次内核复合操作；mark=采集在线快照。"""

    label: str
    apply: Callable[[KernelLedger], object]
    mark: bool = False


def _ctx(fids: tuple[str, ...]) -> VerdictContext:
    """verdict 传播上下文：成员报告者行（finding_id → findings 行 reporter）。"""
    rep = {fid: rep for fid, rep, _ls, _le in _FINDINGS}
    return VerdictContext(
        members=tuple(MemberFacts(f, rep[f], "auditor") for f in fids),
        scouts=("SC1",),
        verifier_id="V9",
    )


def _trajectory(seed: int) -> tuple[list[_Step], dict]:
    """种子驱动确定性轨迹：返回 (轨迹步序列, 元数据)。

    布局：前半存款流 → merge/extend/verdict(S2)/re_route/split/拆分后 S3 直答 →
    后半存款流 → 2 晋升 + 2 降级；检查点 = 2 个种子随机存款步 + 全部复合操作步
    + 终点（每种子 ≥3，实际 ≥12）。
    """
    params, tax = load_params(), load_taxonomy()
    pool = AgentPool.from_params(params, tax, seed=seed)
    tasks = TaskGenerator(params, tax, seed=seed).generate(N_DAYS)
    deposits = []
    for ctx, res in run_findings_flow(tasks, pool, seed=seed, params=params):
        for rec in deposit_schedule(ctx, res, params):   # S1/S2=task.day，S3=day+lag
            deposits.append((rec.day, rec.payload.model_dump()))
    deposits.sort(key=lambda d: d[0])                    # 稳定排序：同日保持流序
    assert len(deposits) >= 8, f"种子 {seed} 轨迹存款过少：{len(deposits)}"

    # status 重放键：最早/最晚建格的格子（promotion/demotion 跨时点生效可断言）
    first_day: dict[tuple, float] = {}
    for day, p in deposits:
        key = (p["agent"], str(p["role"]), p["taxonomy_path"][0])
        first_day.setdefault(key, day)
    key_early = min(first_day, key=first_day.get)
    key_late = max(first_day, key=first_day.get)
    meta: dict = {"key_early": key_early, "key_late": key_late}
    out = meta                                          # 复合操作产出就地写入元数据

    def dep(payload: dict) -> _Step:
        return _Step("deposit", lambda led: led.append("deposit", payload))

    steps: list[_Step] = []
    half = len(deposits) // 2
    steps += [dep(p) for _day, p in deposits[:half]]

    def _merge() -> _Step:
        def apply(led: KernelLedger) -> None:
            for fid, rep, ls, le in _FINDINGS:           # 内核唯一建档写口：行+事件同事务
                open_finding(led, finding_id=fid, run_id=_RUN, reporter=rep,
                             reporter_role="auditor", family="injection", type="sqli",
                             lang="py", repo="repo", path="injection/sqli",
                             line_start=ls, line_end=le, title="t", severity="high",
                             cwe="CWE-79")
            evs = apply_merges(led, cluster_findings(_FACTS, level="coarse"),
                               run_id=_RUN)
            assert evs, "轨迹必须发生真实归并（同窗三元组）"
            out["cid1"] = next(e.payload["cluster_id"] for e in evs
                               if _FA in e.payload["members"])
        return _Step("merge", apply, mark=True)

    def _extend() -> _Step:
        def apply(led: KernelLedger) -> None:
            evs = apply_merges(led, [ClusterProposal(
                level="coarse", member_ids=(_FA, _FB, _FC, _F4),
                key_path="injection/sqli", key_cwe="CWE-79", key_lang="py",
                line_window=(1, 109), sim_min=None)], run_id=_RUN)
            assert len(evs) == 1                          # 扩簇：一条增量 cluster_merge
        return _Step("extend", apply, mark=True)

    def _verdict_s2() -> _Step:
        def apply(led: KernelLedger) -> None:
            vsev = led.append("verify_result",
                              {"cluster_id": out["cid1"], "outcome": "verified_real"})
            deps = [e for e in propagate_verdict(
                led, out["cid1"], tier=SignalTier.S2, direction=1,
                source_seq=vsev.seq, source_kind=LedgerKind.VERIFY_RESULT,
                ctx=_ctx((_FA, _FB, _FC, _F4)))
                if e.kind is LedgerKind.DEPOSIT]
            assert deps, "S2 传播必须产出簇级存款"
            out["cid1_deposit_seqs"] = [e.seq for e in deps]
        return _Step("verdict_s2", apply, mark=True)

    def _reroute() -> _Step:
        def apply(led: KernelLedger) -> None:
            evs = re_route_finding(led, _FD, from_node="crypto",
                                   to_node="injection/sqli",
                                   scout_ids=("SC1", "SC2"), run_id=_RUN)
            assert len(evs) == 3 and evs[0].kind is LedgerKind.RE_ROUTE
        return _Step("re_route", apply, mark=True)

    def _split() -> _Step:
        def apply(led: KernelLedger) -> None:
            evs = split_cluster(led, out["cid1"], [[_FA, _FB], [_FC, _F4]],
                                actor="test", run_id=_RUN)
            out["split_seq"] = evs[-1].seq                # 末条 = cluster_split 事件
            out["child_a"] = evs[0].payload["cluster_id"]  # 子簇序 = 组内最小 id：{FA,FB}
            out["child_b"] = evs[1].payload["cluster_id"]  # {FC,F4}
        return _Step("split", apply, mark=True)

    def _verdict_s3_post_split() -> _Step:
        def apply(led: KernelLedger) -> None:
            deps = [e for e in apply_human_verdict(
                led, out["child_a"], direction=1, actor="human",
                ctx=_ctx((_FA, _FB)), run_id=_RUN)
                if e.kind is LedgerKind.DEPOSIT]
            assert deps, "拆分后子簇 S3 直答必须产出存款"
            out["child_a_deposit_seqs"] = [e.seq for e in deps]
        return _Step("verdict_s3_post_split", apply, mark=True)

    steps += [_merge(), _extend(), _verdict_s2(), _reroute(), _split(),
              _verdict_s3_post_split()]
    steps += [dep(p) for _day, p in deposits[half:]]

    def promo(exam: str, key: tuple, to_status: str) -> _Step:
        return _Step(f"promotion:{exam}", lambda led: led.append(
            "promotion", {"agent": key[0], "role": key[1], "node": key[2],
                          "exam_id": exam, "from_status": "shadow",
                          "to_status": to_status}), mark=True)

    def demo(key: tuple, reason: str) -> _Step:
        return _Step(f"demotion:{reason}", lambda led: led.append(
            "demotion", {"agent": key[0], "role": key[1], "node": key[2],
                         "reason": reason}), mark=True)

    steps += [promo("ER-P1", key_late, "candidate"), promo("ER-P2", key_early, "active"),
              demo(key_early, "dormant"), demo(key_late, "decay")]

    # 任意时点检查点：2 个种子随机存款步 + 全部复合操作/status 步（终点即末步）
    rng = random.Random(0xA11CE + seed)
    dep_positions = [i for i, st in enumerate(steps) if st.label == "deposit"]
    marks = set(rng.sample(dep_positions, 2))
    steps = [_Step(st.label, st.apply, mark=True) if i in marks else st
             for i, st in enumerate(steps)]
    meta["marks"] = {i for i, st in enumerate(steps, start=1) if st.mark}  # 1-based
    return steps, meta


# ── 在线侧：逐条落账 + 检查点快照（沿用 test_kernel_replay 口径）──

def _rows(db: Path) -> list[tuple]:
    """cells 物理表全行（verify_ac_cells 表行口径；列序 = CellAgg 字段序）。"""
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells"
            " ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


def _rows_as_cells(rows: list[tuple]) -> dict[CellKey, CellAgg]:
    """cells 物理表行 → CellAgg 映射（物理表比对侧）。"""
    return {CellKey(r[0], r[1], r[2]): CellAgg(**dict(zip(ALL_CELL_FIELDS, r)))
            for r in rows}


def _as_of(led: KernelLedger) -> datetime:
    """观测时点 = 最后一条事件 ts（「落完第 k 条即以该刻为 as_of」快照语义）。"""
    seq = led.latest_seq()
    evs = led.read(from_seq=seq, to_seq=seq)
    return datetime.fromisoformat(evs[0].ts.replace("Z", "+00:00"))


def _run_online(db: Path, steps: list[_Step], marks: set[int]) -> dict[int, tuple]:
    """按序落账（SteppedClock 逐事件递增 ts），在每个检查点快照在线三投影+表行。"""
    clock = SteppedClock(_T0, 1.0)
    snaps: dict[int, tuple] = {}
    with KernelLedger(db, clock=clock) as led:
        led.conn.execute(
            "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
            " status, started_at, meta_json) VALUES (?,'full','repo',600,0,"
            "'running','2030-01-01T00:00:00Z','{}')", (_RUN,))
        led.conn.commit()
        for i, st in enumerate(steps, start=1):
            st.apply(led)
            if i in marks:
                snaps[i] = (led.latest_seq(),
                            dict(agg_cells(led, _as_of(led)).cells),
                            dict(cluster_projection(led)),
                            dict(finding_projection(led)),
                            _rows(db))
    return snaps


# ── 主验证：3 种子 × ≥3 检查点 rebuild==cells + 收尾全账本×物理表 ──

@pytest.mark.parametrize("seed", SEEDS)
def test_seeded_replay_equivalence_cluster_split_reroute(tmp_path: Path, seed: int):
    """含 cluster/split/merge/re_route 事件的账本：任意时点 rebuild 与在线 cells
    逐字段全等；收尾全账本重放与 cells 物理表逐字段全等；split/merge 后簇状态与
    存款归属正确。"""
    steps, meta = _trajectory(seed)
    marks: set[int] = meta["marks"]
    assert len(marks) >= 3, f"种子 {seed} 检查点不足（验收 ≥3）：{sorted(marks)}"
    db = tmp_path / f"ledger-{seed}.db"
    snaps = _run_online(db, steps, marks)
    assert len(snaps) == len(marks)

    with KernelLedger(db) as led:
        # 事件面非平凡性守卫（验收：账本须含 cluster/split/merge/re_route 事件）
        kinds = Counter(e.kind.value for e in led.read())
        assert kinds["cluster_merge"] >= 4        # 建簇 + 扩簇 + 拆分两子簇
        assert kinds["cluster_split"] == 1
        assert kinds["re_route"] == 1
        assert kinds["finding"] >= 5 and kinds["deposit"] >= 10
        assert kinds["promotion"] == 2 and kinds["demotion"] == 2

        # ── 任意时点 rebuild == 在线 cells（逐字段全等，验收主判据）────
        for i in sorted(marks):
            seq, cells_online, clus_online, find_online, rows_online = snaps[i]
            assert cells_online, f"seed={seed} 时点 {i} 在线格子为空"
            rebuilt = rebuild_cells(led, seq)                  # 从空账重放前 seq 条
            assert diff_cells(cells_online, rebuilt, fields=ALL_CELL_FIELDS) == [], (
                f"seed={seed} upto_seq={seq}（步 {i}）rebuild 与在线 cells 不等价")
            assert diff_cells(rebuilt, rebuild_cells(led, seq)) == []   # 幂等
            assert diff_clusters(clus_online, rebuild_clusters(led, seq)) == [], (
                f"seed={seed} upto_seq={seq} 簇投影重放不等价")
            assert diff_findings(find_online, rebuild_findings(led, seq)) == [], (
                f"seed={seed} upto_seq={seq} finding 投影重放不等价")
            # 快照时点的 cells 物理表行 = 在线视图（agg_cells 落表语义）
            assert diff_cells(cells_online, _rows_as_cells(rows_online),
                              fields=ALL_CELL_FIELDS) == []

        # ── split/merge 后簇状态与存款归属正确性（ac_mv1s9fkjfoznzu1k）──
        proj = rebuild_clusters(led)
        assert diff_clusters(cluster_projection(led), proj) == []
        cid1, ca, cb, split_seq = (meta["cid1"], meta["child_a"],
                                   meta["child_b"], meta["split_seq"])
        assert proj[cid1].members == () and proj[cid1].split is True   # 父簇拆空
        assert (proj[cid1].verdict_tier, proj[cid1].verdict_dir) == ("S2", 1), (
            "父簇拆分后保留历史 verdict 投影（CL2）")
        assert proj[ca].members == (_FA, _FB) and proj[ca].split is False
        assert proj[cb].members == (_FC, _F4) and proj[cb].split is False
        assert proj[cb].verdict_tier is None and proj[cb].verdict_dir is None, (
            "未承接存款的子簇 verdict 置空（建行 NULL 承载）")
        assert (proj[ca].verdict_tier, proj[ca].verdict_dir) == ("S3", 1), (
            "拆分后存款归属必须落到承接子簇")
        # 存款归属时序：父簇级存款全部先于拆分；拆分后簇级存款全部落在子簇
        clustered = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
                     if e.payload.get("cluster")]
        pre = [e for e in clustered if e.payload["cluster"] == cid1]
        assert pre and all(e.seq < split_seq for e in pre), "父簇存款必须全部先于拆分"
        post = [e for e in clustered if e.payload["cluster"] in (ca, cb)]
        assert post and all(e.seq > split_seq for e in post), (
            "拆分后不得再对已拆空父簇存款")
        assert {e.payload["cluster"] for e in post} == {ca}
        rep_rows = [e for e in post if e.payload.get("finding") is not None]
        assert rep_rows and all(e.payload.get("finding") in (_FA, _FB)
                                for e in rep_rows), (
            "拆分后直答存款的成员报告者行必须是子簇成员")
        # findings 归属随迁：merge 事件折叠 = 物理表 findings.cluster_id
        fproj = rebuild_findings(led)
        assert fproj[_FA].cluster_id == fproj[_FB].cluster_id == ca
        assert fproj[_FC].cluster_id == fproj[_F4].cluster_id == cb
        # merge 非平凡性：建簇即真归并（首并快照 cid1 已含三元组）
        merge_i = min(i for i, st in enumerate(steps, start=1) if st.label == "merge")
        extend_i = min(i for i, st in enumerate(steps, start=1) if st.label == "extend")
        assert tuple(snaps[merge_i][2][cid1].members) == (_FA, _FB, _FC)
        assert snaps[merge_i][2][cid1].split is False
        assert tuple(snaps[extend_i][2][cid1].members) == (_FA, _FB, _FC, _F4)

        # ── 收尾：全账本重放 vs 在线视图 vs cells 物理表（逐字段全等）──
        view_final = agg_cells(led, _as_of(led))       # 在线收口（写物理表）
        rows_final = _rows(db)
        rebuilt_full = rebuild_cells(led)              # 全账本重放（0..latest）
        assert diff_cells(view_final, rebuilt_full, fields=ALL_CELL_FIELDS) == [], (
            "全账本重放与在线终态视图不等价")
        rows_map = _rows_as_cells(rows_final)
        assert rows_map, "收尾 cells 物理表为空（轨迹覆盖不足）"
        assert diff_cells(rebuilt_full, rows_map, fields=ALL_CELL_FIELDS) == [], (
            "全账本重放与 cells 物理表逐字段不等价")
        assert _rows(db) == rows_final, "重放落表与在线落表物理行不全等"
        assert diff_clusters(cluster_projection(led), rebuild_clusters(led)) == []
        assert diff_findings(finding_projection(led), rebuild_findings(led)) == []

        # 非平凡性：格子规模 + re_route 猜测格弱存款（0.05 幅、按 as_of 挥发）+
        # status 重放跨时点生效
        assert len(view_final.cells) >= 10
        for scout in ("SC1", "SC2"):
            weak = view_final.get(scout, "scout", "crypto")
            assert weak is not None and weak.pos_w == 0.0 and 0.0 < weak.neg_w <= 0.05, (
                f"re_route 侦察猜测格弱存款缺失：{scout} -> {weak}")
        early, late = view_final.get(*meta["key_early"]), view_final.get(*meta["key_late"])
        assert (early.status, early.activated_at is not None) == ("dormant", True)
        assert (late.status, late.activated_at) == ("shadow", None)
        print(f"\nseed={seed}: events={led.latest_seq()}, checkpoints={len(marks)}, "
              f"cells={len(view_final.cells)}")
