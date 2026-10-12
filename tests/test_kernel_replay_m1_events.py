"""M1 重放等价专项回归：含 cluster_merge/cluster_split/re_route 事件账本的
任意时点 rebuild == 在线 cells（逐字段全等）+ 收尾全账本重放与 cells 物理表比对。

对应验收（taskflow）：

- ac_mv1s9fkj6ks1gae5  3 种子 × 每种子 ≥3 个时点检查点：任意前缀
  ``rebuild_cells(ledger, k)`` 与在线 ``agg_cells`` 逐字段全等（视图默认字段 +
  CellAgg 全字段 + cells 物理表行三重口径），簇/finding 投影同口径全等；收尾
  全账本重放与 cells 物理表（SQLite 表逐行读回 → CellAgg）逐字段比对全等。
- ac_mv1s9fkjfoznzu1k  split/merge 后簇状态与存款归属正确性：拆分父簇 members
  清空 + split 标记；拆分前存款归属父簇 verdict（CL2 历史投影保留）、拆分后存款
  归属子簇 verdict（父簇零新增）——事件折叠与物理表双侧断言；findings 归属随
  拆分迁移；re_route 弱存款恰归属猜测格（scout 格 −0.05）。
- ac_mv1s9fkjvss7hnia  常规 pytest 测试（非手工脚本）：本文件随 ``pytest``
  全量收集执行，任何回归可由 ``pytest tests/test_kernel_replay_m1_events.py``
  直接复现。

轨迹来源与既有口径（tests/test_kernel_replay.py、scripts/verify_ac_cells.py）
一致：simulator 世界流（AgentPool + TaskGenerator + run_findings_flow，种子驱动
逐位可复现）经确定性等距子采样后，全部经**内核唯一写口**推进——findings 建档走
``kernel.dispatch.open_finding``、聚类归属走 ``kernel.cluster.attribute_findings``
（真实 cluster_merge，含扩簇增量成员）、verdict 存款走 ``propagate_verdict`` /
``apply_human_verdict``（§6.5 归属矩阵全行展开，src_seq 幂等键）、人工拆分走
``split_cluster``（cluster_split + 子簇 merge，拆分零存款）、类型错配走
``re_route_finding``（re_route 事件 + 猜测格弱存款）、崩溃窗修复走
``propagate_pending``——测试仅播种 runs 行（open_run），零表直写。

重放等价判定复用 qswarm/kernel/replay.py 的单一实现（rebuild_* / diff_* /
*_projection）：浮点与一切字段按 ``==`` 逐位比较，无容差——与内核直跑口径一致。
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from qswarm.contracts import LedgerKind, Role, SignalTier, load_params, load_taxonomy
from qswarm.kernel import (
    CellAgg,
    CellKey,
    FindingFacts,
    KernelLedger,
    MemberFacts,
    SteppedClock,
    VerdictContext,
    agg_cells,
    apply_human_verdict,
    attribute_findings,
    diff_cells,
    open_finding,
    open_run,
    propagate_pending,
    propagate_verdict,
    re_route_finding,
    split_cluster,
)
from qswarm.kernel.replay import (
    cluster_projection,
    diff_clusters,
    diff_findings,
    finding_projection,
    rebuild_cells,
    rebuild_clusters,
    rebuild_findings,
)
from qswarm.simulator import AgentPool, TaskGenerator, run_findings_flow
from qswarm.simulator.engine import (
    WORLD_COMMIT,
    WORLD_GRADE,
    WORLD_LANG,
    WORLD_REPO,
    WORLD_SEVERITY,
    finding_facts,
)

#: 3 种子（验收 ≥3；与 tests/test_kernel_replay.py 的 (11,22,33) 错开，覆盖不同世界流）。
SEEDS = (7, 101, 2026)
N_DAYS = 8                 # 短轨迹（世界流按日推进）
STRIDE = 8                 # 世界 finding 等距子采样步长（控制账本规模，确定性）
HARNESS_DAYS = (2, 5)      # 注入 harness finding 的仿真日（真实归并保证：同槽位共簇）
T0 = datetime(2032, 5, 6, tzinfo=timezone.utc)   # 轨迹时间轴原点（SteppedClock 起点）
RUN_ID = "R-REPLAY-EVENTS"
REROUTE_FROM = "offroute"  # 猜测格弱存款的孤立 node（无任何其他存款会落此格）

ALL_CELL_FIELDS = tuple(CellAgg.__dataclass_fields__)   # 全字段口径（含 updated_at）


# ── 物理表读取（比对基准的表口径）─────────────────────────────

def _rows(db) -> list[tuple]:
    """cells 表全行（含 updated_at），行序稳定——「物理表逐字段比对」的行口径。"""
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells"
            " ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


def _cells_table_map(led: KernelLedger) -> dict[CellKey, CellAgg]:
    """cells 物理表逐行读回 → {CellKey: CellAgg}（收尾全账本比对的对侧）。"""
    out: dict[CellKey, CellAgg] = {}
    for r in led.conn.execute(
        "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
        " activated_at, last_event_seq, updated_at FROM cells"
    ):
        out[CellKey(str(r[0]), str(r[1]), str(r[2]))] = CellAgg(
            agent_id=str(r[0]), role=str(r[1]), node=str(r[2]),
            pos_w=r[3], neg_w=r[4], n_eff=r[5], n_eff_s2s3=r[6],
            status=str(r[7]), activated_at=r[8], last_event_seq=r[9], updated_at=r[10],
        )
    return out


def _dt(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


# ── 事件化账本装配（种子驱动，全部经内核写口）─────────────────

@dataclass(frozen=True)
class _Snap:
    """在线快照：检查点 seq + 该刻的在线 cells（视图/表行）与簇/finding 投影。"""

    tag: str
    seq: int
    cells: dict
    rows: list
    clusters: dict
    findings: dict


@dataclass(frozen=True)
class _RunInfo:
    """装配结果：快照列 + 拆分/错配关键事实（存款归属断言的锚点）。"""

    marks: tuple[_Snap, ...]
    latest_seq: int
    cluster_of: dict
    split_target: str
    split_children: tuple
    groups: tuple
    parent_members: tuple
    split_seq: int
    split_ts: str
    h1: str
    reroute_scouts: tuple
    reroute_src_seq: int
    reroute_last_seq: int
    crash_cluster: str


def _run_eventful(db, seed: int) -> _RunInfo:
    """按仿真日推进一台含 merge/split/re_route/崩溃窗的事件化账本（确定性）。"""
    params = load_params()
    tax = load_taxonomy()
    pool = AgentPool.from_params(params, tax, seed=seed)
    tasks = TaskGenerator(params, tax, seed=seed).generate(N_DAYS)
    flow = run_findings_flow(tasks, pool, seed=seed, params=params)
    world = [rec for i, rec in enumerate(flow) if i % STRIDE == 0]   # 确定性子采样
    by_day: dict[int, list] = {}
    for ctx, res in world:
        by_day.setdefault(ctx.task.day, []).append((ctx, res))
    assert world, "世界流子采样后为空（轨迹覆盖不足）"

    led = KernelLedger(db, clock=SteppedClock(T0, 1.0))
    open_run(led, run_id=RUN_ID)
    auditors = [a.agent_id for a in pool.by_role(Role.AUDITOR)]
    verifiers = [a.agent_id for a in pool.by_role(Role.VERIFIER)] or ["verifier-01"]

    marks: list[_Snap] = []
    info: dict = {}

    def mark(tag: str) -> None:
        seq = led.latest_seq()
        evs = led.read(from_seq=seq, to_seq=seq)
        as_of = _dt(evs[0].ts) if evs else T0     # as_of = 第 seq 条事件 ts（在线侧锚）
        marks.append(
            _Snap(
                tag=tag, seq=seq,
                cells=dict(agg_cells(led, as_of, params=params).cells),
                rows=_rows(db),
                clusters=dict(cluster_projection(led)),
                findings=dict(finding_projection(led)),
            )
        )

    def member_ctx(cid: str, *, res=None) -> VerdictContext:
        """簇谱系装配：成员事实面取 findings 表行（表行同源），votes/scouts/verifier
        取锚点 finding 的世界记录（种子驱动的 §6.5 侦察/评审/验证行）。"""
        members = sorted(
            str(r[0])
            for r in led.conn.execute(
                "SELECT finding_id FROM cluster_members WHERE cluster_id = ?", (cid,)
            )
        )
        rows = {
            str(r[0]): (r[1], r[2])
            for r in led.conn.execute(
                f"SELECT id, reporter, reporter_role FROM findings"
                f" WHERE id IN ({','.join('?' * len(members))})",
                tuple(members),
            ).fetchall()
        }
        mf = tuple(MemberFacts(fid, str(rows[fid][0]), str(rows[fid][1])) for fid in members)
        # S3 验证行前提：簇存在先验 S2 verdict 时 verifier 必须在谱系——锚点 finding
        # 为 harness/FAST 档（无 verifier）时回退验证蜂编制首员（确定性）。
        default_verifier = verifiers[0]
        res_pair = next((world_ctx[f] for f in members if f in world_ctx), None)
        if res_pair is None:
            return VerdictContext(members=mf, verifier_id=default_verifier)
        ctx0, res0 = res_pair
        votes = tuple((v.reviewer_id, v.direction) for v in res0.votes)
        return VerdictContext(
            members=mf, scouts=tuple(ctx0.cluster_scouts), votes=votes,
            verifier_id=ctx0.verifier_id or default_verifier,
        )

    opened: list[tuple] = []          # 本日已建档 (fid, facts, ctx, res)
    world_ctx: dict[str, tuple] = {}  # world finding_id → (ctx, res)
    earliest: tuple | None = None     # (fid, facts, ctx) 按 task.t 最早（harness 归并锚）
    cluster_of: dict[str, str] = {}
    harness_no = 0

    for day in range(N_DAYS):
        # ── 晨：当日世界 finding 建档（kernel.dispatch 唯一写口）──
        todays: list[tuple] = []
        for ctx, res in by_day.get(day, []):
            facts = finding_facts(ctx.task)
            open_finding(
                led,
                finding_id=facts.finding_id, run_id=RUN_ID,
                reporter=ctx.reporter_id, reporter_role=ctx.reporter_role.value,
                family=ctx.task.family, type=ctx.task.type, lang=WORLD_LANG,
                repo=WORLD_REPO, path=facts.path, line_start=facts.line_start,
                line_end=facts.line_end, title=f"{ctx.task.family}/{ctx.task.type}",
                severity=WORLD_SEVERITY, grade=WORLD_GRADE, commit_sha=WORLD_COMMIT,
                cwe=facts.cwe,
            )
            rec = (facts.finding_id, facts, ctx, res)
            todays.append(rec)
            opened.append(rec)
            world_ctx[facts.finding_id] = (ctx, res)
            if earliest is None or ctx.task.t < earliest[2].task.t:
                earliest = (facts.finding_id, facts, ctx)

        # ── harness finding：与既有 finding 同 path/行窗 → 真实归并/扩簇保证 ──
        if day in HARNESS_DAYS and earliest is not None:
            tfid, tfacts, _ = earliest
            for _j in range(2):
                harness_no += 1
                fid = f"F-EV-{seed}-{harness_no:02d}"
                facts = FindingFacts(
                    finding_id=fid, path=tfacts.path, line_start=tfacts.line_start,
                    line_end=tfacts.line_end, cwe=tfacts.cwe, lang=tfacts.lang,
                    family=tfacts.family, type=tfacts.type,
                )
                open_finding(
                    led,
                    finding_id=fid, run_id=RUN_ID,
                    reporter=auditors[harness_no % len(auditors)], reporter_role="auditor",
                    family=facts.family, type=facts.type, lang=facts.lang,
                    repo=WORLD_REPO, path=facts.path, line_start=facts.line_start,
                    line_end=facts.line_end, title="harness merge guarantee",
                    severity=WORLD_SEVERITY, grade=WORLD_GRADE,
                    commit_sha=WORLD_COMMIT, cwe=facts.cwe,
                )
                rec = (fid, facts, None, None)
                todays.append(rec)
                opened.append(rec)

        # ── 与 cells 无关的窗口事件（重放须忽略其聚合效果、计入窗口）──
        led.append("dispatch", {"task_id": f"T-N{day}", "role": "scout", "node": "recon"})

        # ── 昼末：内核真实聚类归属（cluster_merge，含扩簇增量成员）──
        if todays:
            assignment = attribute_findings(
                led, [facts for _, facts, _, _ in todays], run_id=RUN_ID
            )
            cluster_of.update(assignment)
            mark(f"d{day}:merge")

        # ── 日程动作（真实 verdict/拆分/错配/修复，全部经内核口）──
        live_cids = lambda: sorted(
            str(r[0])
            for r in led.conn.execute(
                "SELECT DISTINCT cluster_id FROM cluster_members"
            )
        )

        if day == 2:
            # 拆分目标的簇（含 harness 对，≥2 成员）先取 S2(+1)：父簇拆分前存款
            h1 = next(fid for fid, *_ in opened if str(fid).startswith("F-EV-"))
            info["h1"] = h1
            target = cluster_of[h1]
            info["split_target"] = target
            vsev = led.append(
                "verify_result", {"cluster_id": target, "outcome": "verified_real"}
            )
            propagate_verdict(
                led, target, tier=SignalTier.S2, direction=1, source_seq=vsev.seq,
                source_kind=LedgerKind.VERIFY_RESULT, ctx=member_ctx(target),
                run_id=RUN_ID,
            )
            mark("d2:s2-parent-pre-split")

        if day == 3:
            # re_route：产出类型与派单契约错配 → re_route 事件 + 猜测格弱存款
            h1 = info["h1"]
            to_node = next(f"{facts.family}/{facts.type}" for fid, facts, _, _ in opened
                           if fid == h1)
            evs = re_route_finding(
                led, h1, from_node=REROUTE_FROM, to_node=to_node,
                scout_ids=("scout-01", "scout-02"), run_id=RUN_ID,
            )
            assert evs, "re_route 必须落事件"
            info["reroute_scouts"] = ("scout-01", "scout-02")
            info["reroute_src_seq"] = evs[0].seq
            info["reroute_last_seq"] = evs[-1].seq
            mark("d3:re-route")

        if day == 4:
            # 人工拆分：父簇 → 两组（cluster_split + 子簇 merge，拆分零存款）
            target = info["split_target"]
            members = sorted(
                str(r[0]) for r in led.conn.execute(
                    "SELECT finding_id FROM cluster_members WHERE cluster_id = ?",
                    (target,),
                )
            )
            assert len(members) >= 2, "拆分目标成员不足（归并保证失效）"
            half = (len(members) + 1) // 2
            groups = [members[:half], members[half:]]
            events = split_cluster(led, target, groups, actor="human", run_id=RUN_ID)
            split_ev = next(e for e in events if e.kind is LedgerKind.CLUSTER_SPLIT)
            info["split_children"] = tuple(split_ev.payload["new_clusters"])
            info["groups"] = tuple(tuple(sorted(g)) for g in groups)
            info["parent_members"] = tuple(members)
            info["split_seq"] = split_ev.seq
            info["split_ts"] = split_ev.ts
            mark("d4:split")

        if day == 5:
            # 子簇承接拆分后存款：verify_fp → S2(−1)（存款归属判定的正样本）
            child0 = info["split_children"][0]
            vsev = led.append(
                "verify_result", {"cluster_id": child0, "outcome": "verified_fp"}
            )
            propagate_verdict(
                led, child0, tier=SignalTier.S2, direction=-1, source_seq=vsev.seq,
                source_kind=LedgerKind.VERIFY_RESULT, ctx=member_ctx(child0),
                run_id=RUN_ID,
            )
            mark("d5:s2-child-post-split")
            # 崩溃窗：源事件已落、派生存款未落（day-6 propagate_pending 修复）
            target = info["split_target"]
            children = info["split_children"]
            others = [c for c in live_cids() if c != target and c not in children]
            assert len(others) >= 4, "其余活簇不足（日程动作覆盖不足）"
            info["crash_cluster"] = others[0]
            led.append(
                "verify_result",
                {"cluster_id": others[0], "outcome": "verified_real"},
            )

        if day == 6:
            # 其余簇的常规 S2（含 verify_error 零存款路径）+ 崩溃窗批处理修复
            target = info["split_target"]
            children = info["split_children"]
            others = [c for c in live_cids() if c != target and c not in children]
            info["verdict_clusters"] = tuple(others[1:4])
            for i, cid in enumerate(info["verdict_clusters"]):
                outcome = "verify_error" if i == 2 else "verified_real"
                vsev = led.append("verify_result", {"cluster_id": cid, "outcome": outcome})
                if outcome != "verify_error":
                    propagate_verdict(
                        led, cid, tier=SignalTier.S2, direction=1, source_seq=vsev.seq,
                        source_kind=LedgerKind.VERIFY_RESULT, ctx=member_ctx(cid),
                        run_id=RUN_ID,
                    )
            rep = propagate_pending(led, lineage=lambda c: member_ctx(c) if c in set(live_cids()) else None)
            assert any(
                e.payload.get("cluster") == info["crash_cluster"] for e in rep.events
            ), "崩溃窗修复必须补出 crash 簇的派生存款"
            mark("d6:s2-others+repair")

        if day == 7:
            # S3 人工裁决（±1 双向 + 状态流转 confirmed/rejected）
            for i, cid in enumerate(info["verdict_clusters"]):
                apply_human_verdict(
                    led, cid, direction=1 if i % 2 == 0 else -1, actor="human",
                    ctx=member_ctx(cid), run_id=RUN_ID,
                )
            mark("d7:final-s3")

    info["marks"] = tuple(marks)
    info["latest_seq"] = led.latest_seq()
    info["cluster_of"] = dict(sorted(cluster_of.items()))
    ledger_snapshot = _RunInfo(
        marks=info["marks"],
        latest_seq=info["latest_seq"],
        cluster_of=info["cluster_of"],
        split_target=info["split_target"],
        split_children=info["split_children"],
        groups=info["groups"],
        parent_members=info["parent_members"],
        split_seq=info["split_seq"],
        split_ts=info["split_ts"],
        h1=info["h1"],
        reroute_scouts=info["reroute_scouts"],
        reroute_src_seq=info["reroute_src_seq"],
        reroute_last_seq=info["reroute_last_seq"],
        crash_cluster=info["crash_cluster"],
    )
    led.close()
    return ledger_snapshot


# ── 验收 ac_mv1s9fkj6ks1gae5：3 种子 × ≥3 检查点 + 收尾全账本物理表比对 ──

@pytest.mark.parametrize("seed", SEEDS)
def test_eventful_ledger_arbitrary_prefix_replay_equivalent(tmp_path, seed: int):
    """任意时点 rebuild == 在线 cells（逐字段全等）；收尾全账本重放 == 物理表。"""
    db = tmp_path / "ledger.db"
    info = _run_eventful(db, seed)
    marks = info.marks
    # 检查点非平凡性：≥3 个时点（本场景 ≥14），seq 互异且含轨迹终点
    assert len(marks) >= 3, f"检查点数不足（验收 ≥3）：{len(marks)}"
    assert len({m.seq for m in marks}) == len(marks)

    with KernelLedger(db) as led:
        assert led.latest_seq() == info.latest_seq == marks[-1].seq
        kinds = Counter(e.kind.value for e in led.read())
        # 账本事件面非平凡：merge/split/re_route/deposit 齐备
        assert kinds["cluster_merge"] >= 20, kinds
        assert kinds["cluster_split"] == 1, kinds
        assert kinds["re_route"] == 1, kinds
        assert kinds["deposit"] >= 10, kinds
        assert kinds["verify_result"] >= 5 and kinds["human_verdict"] >= 1, kinds

        for m in marks:
            rebuilt = rebuild_cells(led, m.seq)          # 从空账重放前 m.seq 条
            # ① 视图默认字段口径
            assert diff_cells(m.cells, rebuilt) == [], (
                f"{m.tag}(seq={m.seq}) 重放不等价：前 5 条差异 "
                f"{[str(d) for d in diff_cells(m.cells, rebuilt)[:5]]}"
            )
            # ② CellAgg 全字段口径（含 updated_at/身份字段）
            assert diff_cells(m.cells, rebuilt, fields=ALL_CELL_FIELDS) == [], (
                f"{m.tag}(seq={m.seq}) 全字段口径重放不等价"
            )
            # ③ cells 物理表行口径：重放写表恰好复原在线快照的表态
            assert _rows(db) == m.rows, f"{m.tag}(seq={m.seq}) 重放写表 ≠ 在线表态"
            # ④ 幂等：二次重放逐位一致
            assert diff_cells(rebuilt, rebuild_cells(led, m.seq)) == []
            # ⑤ 簇/finding 投影同口径全等（merge/split/re_route 不破坏重放）
            assert diff_clusters(m.clusters, rebuild_clusters(led, m.seq)) == [], (
                f"{m.tag}(seq={m.seq}) 簇投影重放不等价"
            )
            assert diff_findings(m.findings, rebuild_findings(led, m.seq)) == [], (
                f"{m.tag}(seq={m.seq}) finding 投影重放不等价"
            )

        # ── 收尾：全账本重放 vs 在线终态 vs cells 物理表（SQLite）逐字段全等 ──
        latest = info.latest_seq
        rows_before = _rows(db)
        final = marks[-1]
        rebuilt = rebuild_cells(led, latest)
        assert diff_cells(final.cells, rebuilt, fields=ALL_CELL_FIELDS) == [], (
            "全账本重放 ≠ 在线终态（全字段口径）"
        )
        assert rebuilt.cells, "终态格子为空（轨迹覆盖不足）"
        assert _rows(db) == rows_before, "收尾重放改写了物理表终态（重写应无损）"
        # 物理表逐行读回 → CellAgg，与重放结果逐字段比对（SQLite REAL 回读逐位）
        table_map = _cells_table_map(led)
        assert len(table_map) == len(rebuilt.cells)
        assert diff_cells(table_map, rebuilt, fields=ALL_CELL_FIELDS) == [], (
            "cells 物理表与全账本重放逐字段比对不等价"
        )
        # 簇/finding 投影：全账本折叠 == 物理表（收尾同口径）
        assert diff_clusters(cluster_projection(led), rebuild_clusters(led)) == []
        assert diff_findings(finding_projection(led), rebuild_findings(led)) == []
        # 非平凡性：终态投影规模（子采样世界流 → 数十单例簇 + 归并簇 + 子簇）
        assert len(final.clusters) >= 10 and len(final.findings) >= 10

        # 空窗口：无事件 → 无格子/无投影（置于收尾后：rebuild(0) 整表清空 cells；
        # 探针后再全量重建一次，重放仍须复原终态——顺带覆盖「DROP 后重建」形态）
        assert rebuild_cells(led, 0).cells == {}
        assert rebuild_clusters(led, 0) == {} and rebuild_findings(led, 0) == {}
        assert diff_cells(rebuilt, rebuild_cells(led, latest), fields=ALL_CELL_FIELDS) == []


# ── 验收 ac_mv1s9fkjfoznzu1k：split/merge 后簇状态与存款归属正确性 ──

def test_split_merge_cluster_state_and_deposit_ownership(tmp_path):
    """拆分后：父簇 members 清空 + split 标记；拆分前存款归属父簇 verdict（历史
    投影保留），拆分后存款归属子簇 verdict（父簇零新增）；findings 归属随拆分
    迁移；re_route 弱存款恰归属猜测格——事件折叠与物理表双侧断言。"""
    seed = SEEDS[0]
    db = tmp_path / "ledger.db"
    info = _run_eventful(db, seed)
    parent, children = info.split_target, info.split_children

    with KernelLedger(db) as led:
        proj = rebuild_clusters(led)                  # 事件折叠侧
        live = cluster_projection(led)                # 物理表侧
        # ① 拆分后簇状态：父簇成员清空 + split 标记（两侧一致）
        for side, p in (("rebuild", proj[parent]), ("table", live[parent])):
            assert p.split is True, f"{side}: 父簇未标记 split"
            assert p.members == (), f"{side}: 父簇成员未清空"
        # ② 子簇成员恰为拆分组（并集 = 原成员全集，次序 = 组内最小 finding_id）
        assert tuple(sorted(proj[c].members for c in children)) == tuple(
            sorted(info.groups)
        )
        assert sorted(m for c in children for m in proj[c].members) == list(
            info.parent_members
        )
        # ③ 存款归属：父簇 verdict = 拆分前 S2(+1)（CL2 历史保留），子簇 = 拆分后 S2(−1)
        pp, cc = proj[parent], proj[children[0]]
        assert (pp.verdict_tier, pp.verdict_dir) == ("S2", 1)
        assert (cc.verdict_tier, cc.verdict_dir) == ("S2", -1)
        split_dt = _dt(info.split_ts)
        assert _dt(pp.verdict_at) < split_dt, "父簇 verdict 时点应早于拆分"
        assert _dt(cc.verdict_at) > split_dt, "子簇 verdict 时点应晚于拆分"
        # 物理表同判（折叠 == 表，存款归属在两侧同一口径）
        lp, lc = live[parent], live[children[0]]
        assert (lp.verdict_tier, lp.verdict_dir) == ("S2", 1)
        assert (lc.verdict_tier, lc.verdict_dir) == ("S2", -1)
        # ④ findings 归属随拆分迁移：h1 → 子簇0（事件折叠 == findings 物理表）
        fproj, ftab = rebuild_findings(led), finding_projection(led)
        assert fproj[info.h1].cluster_id == children[0]
        assert ftab[info.h1].cluster_id == children[0]
        assert diff_findings(ftab, fproj) == []
        # ⑤ 拆分后的簇锚定存款：全部落子簇，父簇零新增（§6.2 无回滚、拆分零存款）
        posts = [
            e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
            if e.seq > info.split_seq and e.payload.get("cluster") is not None
        ]
        assert posts, "拆分后必须存在簇锚定存款（子簇 S2 传播）"
        assert all(e.payload["cluster"] != parent for e in posts), (
            "拆分后不得再有任何存款归属父簇"
        )
        member_child = {m: c for c in children for m in proj[c].members}
        anchored = [e for e in posts if e.payload.get("finding") in member_child]
        assert anchored, "拆分后原成员的报告者行存款缺失"
        assert all(
            e.payload["cluster"] == member_child[e.payload["finding"]] for e in anchored
        ), "拆分后成员报告者行存款未归属其所在子簇"
        assert all(e.payload.get("src_seq") is not None for e in anchored)

        # ⑥ re_route 弱存款归属猜测格：scout 格 −0.05——按每笔弱存款自身 ts 取
        # as_of（零龄期 decay 恒等 1），幅值/恒等式逐位精确；格子末事件 = 该弱存款。
        weak = [
            e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
            if e.payload.get("src_seq") == info.reroute_src_seq
        ]
        assert len(weak) == len(info.reroute_scouts)
        by_scout = {e.payload["agent"]: e for e in weak}
        for s in info.reroute_scouts:
            ev = by_scout[s]
            assert ev.payload["role"] == "scout"
            assert ev.payload["taxonomy_path"] == [REROUTE_FROM]
            assert ev.payload["direction"] == -1 and ev.payload["weight"] == 0.05
            v = agg_cells(led, _dt(ev.ts), params=load_params())
            cell = v.get(s, "scout", REROUTE_FROM)
            assert cell.pos_w == 0.0 and cell.neg_w == 0.05, (
                f"{s} 猜测格弱存款幅值应为 0.05：{cell!r}"
            )
            assert cell.n_eff == 0.1 and cell.n_eff_s2s3 == 0.0, (
                "弱存款为 S1：计 n_eff、不计 n_eff_s2s3"
            )
            assert cell.last_event_seq == ev.seq, "猜测格末事件应为该笔弱存款"
