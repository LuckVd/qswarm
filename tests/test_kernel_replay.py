"""kernel.replay 回归：截断重放等价、diff_cells 逐字段比对、篡改检出、CLI 摘要。

轨迹来源：simulator 短轨迹（AgentPool + TaskGenerator + run_findings_flow +
deposit_schedule 的真实事件流），混入 promotion/demotion（status 重放跨时点）与
dispatch（与 cells 无关的窗口事件），事件 ts 严格随落账序递增（day + 序号秒），
与在线侧「落完第 k 条即以该刻为 as_of 聚合」的快照语义严格同构。

对应验收：
- ac_muz07uljya2ie7aq  ≥3 个不同时点 × ≥2 种子的重放等价断言全绿
                       （逐字段比对全部 cells 行：视图默认字段 + CellAgg 全字段
                       + 表行三重口径；时点确定性覆盖每条 promotion/demotion
                       生效之后，使 status 重放在截断点非平凡）
- ac_muz07ulj2yfniyfs  篡改账本一条存款后 diff_cells 返回差异（测试断言可检出）
- ac_muz07ulj9yggyf8h  CLI 对任一账本文件可执行并输出重建摘要
"""

from __future__ import annotations

import json
import os
import random
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from qswarm.contracts import load_params, load_taxonomy
from qswarm.kernel import (
    CellAgg,
    CellKey,
    CellView,
    KernelLedger,
    agg_cells,
    diff_cells,
    rebuild_cells,
)
from qswarm.simulator import (
    AgentPool,
    TaskGenerator,
    deposit_schedule,
    run_findings_flow,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2030, 1, 1, tzinfo=timezone.utc)   # 轨迹时间轴原点（仿真 0 日）
SEEDS = (11, 22, 33)                              # 3 种子（验收 ≥2）
N_DAYS = 20                                       # 短轨迹
ALL_CELL_FIELDS = tuple(CellAgg.__dataclass_fields__)   # 全字段口径（含 updated_at）


# ── 轨迹构造：simulator 事件流 + 晋升/降级 + 无关事件 ──────────

@dataclass(frozen=True)
class _Planned:
    """计划事件：day 为仿真日（浮点），落账 ts = T0 + day 天 + 落账序号秒。"""

    day: float
    kind: str
    payload: dict


def _trajectory(seed: int, n_days: int = N_DAYS) -> tuple[list[_Planned], tuple, tuple, list[int]]:
    """种子驱动的短仿真轨迹（确定性）。

    返回 (事件计划, 早期格子键, 晚期格子键, status 事件位置)——status 位置为
    升序排序后 promotion/demotion 的 1-based seq，供检查点确定性覆盖。
    """
    params = load_params()
    tax = load_taxonomy()
    pool = AgentPool.from_params(params, tax, seed=seed)
    tasks = TaskGenerator(params, tax, seed=seed).generate(n_days)
    planned: list[_Planned] = []
    for ctx, res in run_findings_flow(tasks, pool, seed=seed, params=params):
        for rec in deposit_schedule(ctx, res, params):     # S1/S2=task.day，S3=day+lag
            planned.append(_Planned(rec.day, "deposit", rec.payload.model_dump()))
    assert len(planned) >= 12, f"轨迹太短（{len(planned)} 条），无法多时点截断"

    # 与 cells 无关的事件：重放须忽略其聚合效果、但计入窗口与摘要计数
    for i in range(4):
        planned.append(
            _Planned(
                1.5 * i + 0.25,
                "dispatch",
                {"task_id": f"T-D{i}", "role": "scout", "node": "injection"},
            )
        )

    # 晋升/降级：取「最早建格」与「最晚建格」两个键，status 重放跨时点可断言
    first_day: dict[tuple, float] = {}
    for p in planned:
        if p.kind == "deposit":
            key = (p.payload["agent"], str(p.payload["role"]), p.payload["taxonomy_path"][0])
            first_day.setdefault(key, p.day)
    key_early = min(first_day, key=first_day.get)
    key_late = max(first_day, key=first_day.get)
    max_day = max(p.day for p in planned)
    planned.append(_Planned(first_day[key_late] + 0.5, "promotion",
                            {"agent": key_late[0], "role": key_late[1], "node": key_late[2],
                             "exam_id": "ER-P1", "from_status": "shadow", "to_status": "candidate"}))
    planned.append(_Planned(first_day[key_early] + 2.0, "promotion",
                            {"agent": key_early[0], "role": key_early[1], "node": key_early[2],
                             "exam_id": "ER-P2", "from_status": "shadow", "to_status": "active"}))
    planned.append(_Planned(max_day + 1.0, "demotion",
                            {"agent": key_early[0], "role": key_early[1], "node": key_early[2],
                             "reason": "dormant"}))
    planned.append(_Planned(max_day + 2.0, "demotion",
                            {"agent": key_late[0], "role": key_late[1], "node": key_late[2],
                             "reason": "decay"}))
    planned.sort(key=lambda p: p.day)          # 稳定排序：同日保持计划序
    status_positions = [
        i for i, p in enumerate(planned, start=1)
        if p.kind in ("promotion", "demotion")
    ]
    assert len(status_positions) == 4, "status 事件数不足，无法覆盖 status 重放"
    return planned, key_early, key_late, status_positions


class _ManualClock:
    """测试注入时钟：逐条改写当前时刻（内核无时间源，ts 全由时钟签发）。"""

    def __init__(self) -> None:
        self.dt = T0

    def now(self) -> datetime:
        return self.dt


def _rows(db: Path) -> list[tuple]:
    """cells 表全行（含 updated_at），行序稳定——「逐字段比对全部 cells 行」的表口径。"""
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3, status,"
            " activated_at, last_event_seq, updated_at FROM cells"
            " ORDER BY agent_id, role, node"
        ).fetchall()
    finally:
        con.close()


def _run_online(db: Path, planned: list[_Planned], checkpoints: set[int]) -> dict[int, tuple]:
    """按 day 序逐条落账（ts 严格递增），在每个 checkpoint 后快照在线 cells（视图+表行）。"""
    clock = _ManualClock()
    snaps: dict[int, tuple] = {}
    with KernelLedger(db, clock=clock) as led:
        for i, ev in enumerate(planned, start=1):
            clock.dt = T0 + timedelta(days=ev.day, seconds=i)   # 严格递增，无并列 ts
            led.append(ev.kind, ev.payload)
            if i in checkpoints:
                snaps[i] = (agg_cells(led, clock.dt), _rows(db))
    return snaps


# ── 验收 ac_muz07uljya2ie7aq：≥3 时点 × ≥2 种子 重放等价 ──────

@pytest.mark.parametrize("seed", SEEDS)
def test_truncated_replay_equivalent_to_online_cells(tmp_path: Path, seed: int):
    """多时点截断重放，与在线 cells 逐字段 100% 一致（全字段+表行+幂等+入口一致）。"""
    planned, key_early, key_late, status_positions = _trajectory(seed)
    n = len(planned)
    rng = random.Random(97531 + seed)
    checkpoints = sorted(
        set(rng.sample(range(1, n), 2))        # 2 个随机时点
        | set(status_positions)                # 每条 promotion/demotion 刚生效的时点
        | {n}                                  # 轨迹终点
    )
    assert len(checkpoints) >= 3, f"时点数不足（验收 ≥3）：{checkpoints}"
    assert len(set(checkpoints)) == len(checkpoints)

    db = tmp_path / "ledger.db"
    snaps = _run_online(db, planned, set(checkpoints))

    with KernelLedger(db) as led:
        assert led.latest_seq() == n
        for k in checkpoints:
            view_online, rows_online = snaps[k]
            assert view_online.cells, f"时点 {k} 在线格子为空（轨迹覆盖不足）"
            rebuilt = rebuild_cells(led, k)                     # 从空账重放前 k 条
            diffs = diff_cells(view_online, rebuilt)
            assert diffs == [], f"upto_seq={k} 重放不等价：前 5 条差异 {diffs[:5]}"
            # 全字段口径（含 updated_at/身份字段）：CellAgg 每个字段逐一比对
            assert diff_cells(view_online, rebuilt, fields=ALL_CELL_FIELDS) == [], (
                f"upto_seq={k} 全字段口径重放不等价"
            )
            assert _rows(db) == rows_online                     # cells 表行级同样全等
            assert diff_cells(rebuilt, rebuild_cells(led, k)) == []   # 幂等：二次重放逐位一致
            if k == n:
                assert diff_cells(rebuilt, led.rebuild()) == []       # ledger.rebuild 入口同口径

    # 非平凡性守卫：终点快照的 status 确由 promotion/demotion 重放而来
    final_view = snaps[n][0]
    early, late = final_view.get(*key_early), final_view.get(*key_late)
    assert (early.status, early.activated_at is not None) == ("dormant", True)
    assert (late.status, late.activated_at) == ("shadow", None)
    print(f"\nseed={seed}: n={n} events, {len(checkpoints)} checkpoints {checkpoints}, "
          f"final cells={len(final_view.cells)}")


# ── diff_cells 单元：逐字段/缺格/非法输入 ──────────────────────

def _cell(**kw) -> CellAgg:
    base = dict(
        agent_id="a", role="auditor", node="injection",
        pos_w=1.0, neg_w=0.0, n_eff=2.0, n_eff_s2s3=2.0,
        status="shadow", activated_at=None,
        last_event_seq=3, updated_at="2030-01-01T00:00:00Z",
    )
    base.update(kw)
    return CellAgg(**base)


def test_diff_cells_detects_every_field_and_presence():
    key = CellKey("a", "auditor", "injection")
    left = {key: _cell()}
    assert diff_cells(left, {key: _cell()}) == []               # 完全一致 → 空差异

    for field, value in [
        ("pos_w", 1.5), ("neg_w", 0.25), ("n_eff", 2.5), ("n_eff_s2s3", 1.0),
        ("last_event_seq", 4), ("status", "active"),
        ("activated_at", "2030-02-01T00:00:00Z"),
    ]:
        diffs = diff_cells(left, {key: _cell(**{field: value})})
        assert [(d.field, d.left, d.right) for d in diffs] == [(field, getattr(_cell(), field), value)]

    other = CellKey("b", "scout", "crypto")
    # 键集双向不同 → 两侧各记一条 presence（多出的与缺失的都算差异）
    assert [(d.field, d.agent_id) for d in diff_cells(left, {other: _cell()})] == [
        ("presence", "a"), ("presence", "b"),
    ]
    diffs_lr = diff_cells(left, {other: _cell()})
    assert [(d.field, d.left is not None, d.right is not None) for d in diffs_lr] == [
        ("presence", True, False), ("presence", False, True),
    ]
    diffs_rl = diff_cells({other: _cell()}, left)
    assert [(d.field, d.left is not None, d.right is not None) for d in diffs_rl] == [
        ("presence", False, True), ("presence", True, False),
    ]
    # CellView 形态输入同样接受
    params = load_params()
    v1 = CellView(as_of=T0, params=params, cells=left)
    v2 = CellView(as_of=T0, params=params, cells={key: _cell(pos_w=9.0)})
    assert [d.field for d in diff_cells(v1, v2)] == ["pos_w"]

    with pytest.raises(TypeError):
        diff_cells([1], [2])
    with pytest.raises(ValueError, match="未知比对字段"):
        diff_cells(left, left, fields=("nope",))


# ── 验收 ac_muz07ulj2yfniyfs：篡改一条存款必须被检出 ──────────

def _copy_db(src: Path, dst: Path) -> None:
    a, b = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def test_tampered_deposit_detected_by_diff_cells(tmp_path: Path):
    planned, *_ = _trajectory(22)
    n = len(planned)
    db = tmp_path / "ledger.db"
    _run_online(db, planned, checkpoints=set())
    with KernelLedger(db) as led:
        baseline = rebuild_cells(led, n)                        # 基线：全量重放终态

    # 对照组：未篡改拷贝的重放与基线逐位一致（拷贝无损，防「永远能检出」平凡通过）
    clean = tmp_path / "clean.db"
    _copy_db(db, clean)
    with KernelLedger(clean) as led:
        assert diff_cells(baseline, rebuild_cells(led, n)) == []

    # 篡改：绕过 append-only 触发器（模拟库级损坏），改第一条存款的 weight
    tampered = tmp_path / "tampered.db"
    _copy_db(db, tampered)
    con = sqlite3.connect(tampered)
    try:
        con.execute("DROP TRIGGER ledger_no_update")
        seq, payload_json = con.execute(
            "SELECT seq, payload_json FROM ledger WHERE kind='deposit' ORDER BY seq LIMIT 1"
        ).fetchone()
        payload = json.loads(payload_json)
        payload["weight"] = round(payload["weight"] + 2.5, 6)
        cur = con.execute(
            "UPDATE ledger SET payload_json = ? WHERE seq = ?", (json.dumps(payload), seq)
        )
        con.commit()
        assert cur.rowcount == 1
    finally:
        con.close()

    with KernelLedger(tampered) as led:
        rebuilt = rebuild_cells(led, n)
    diffs = diff_cells(baseline, rebuilt)
    assert diffs, "篡改一条存款后 diff_cells 必须返回差异"
    # 差异可读且指向被篡改存款的格子；n_eff ≡ 2×(pos+neg) 必随权重漂移
    assert any(d.agent_id == payload["agent"] and d.role == payload["role"] for d in diffs)
    assert {d.field for d in diffs} <= {"pos_w", "neg_w", "n_eff", "n_eff_s2s3"}
    assert "n_eff" in {d.field for d in diffs}
    assert len({str(d) for d in diffs}) == len(diffs)           # 差异行可读且不重复


# ── 验收 ac_muz07ulj9yggyf8h：CLI 对任一账本可执行并输出摘要 ──

def _cli(db, *flags: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "qswarm.kernel.replay", str(db), *flags],
        capture_output=True, text=True, cwd=REPO_ROOT, env=env, timeout=120,
    )


@pytest.fixture()
def ledger_file(tmp_path: Path) -> tuple[Path, int]:
    planned, *_ = _trajectory(11)
    db = tmp_path / "ledger.db"
    _run_online(db, planned, checkpoints=set())
    return db, len(planned)


def test_cli_rebuild_summary_text(ledger_file):
    db, n = ledger_file
    r = _cli(db, "--from", "0")                                 # 规范形态：--from 0 全量重建
    assert r.returncode == 0, r.stderr
    assert "kernel replay" in r.stdout
    assert f"events={n}" in r.stdout and "from_seq=0" in r.stdout
    (cells_n,) = sqlite3.connect(db).execute("SELECT COUNT(*) FROM cells").fetchone()
    assert cells_n > 0
    assert f"rebuilt={cells_n}" in r.stdout                     # 摘要与库内实况一致


def test_cli_json_and_upto_window(ledger_file):
    db, n = ledger_file
    r = _cli(db, "--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    assert (data["from_seq"], data["upto_seq"], data["events"]) == (0, n, n)
    assert data["cells"] > 0 and data["kinds"]["deposit"] > 0

    k = max(1, n // 2)
    data2 = json.loads(_cli(db, "--upto", str(k), "--json").stdout)
    assert (data2["from_seq"], data2["upto_seq"], data2["events"]) == (0, k, k)
    (cells_k,) = sqlite3.connect(db).execute("SELECT COUNT(*) FROM cells").fetchone()
    assert data2["cells"] == cells_k                            # 截断重放写表与摘要一致


def test_cli_error_paths_and_empty_ledger(tmp_path: Path):
    assert _cli(tmp_path).returncode == 1                       # 目录非账本文件 → 失败
    assert "失败" in _cli(tmp_path).stderr

    empty = tmp_path / "empty.db"
    with KernelLedger(empty):
        pass
    r = _cli(empty)                                             # 空账本：可执行，0 格子
    assert r.returncode == 0 and "rebuilt=0" in r.stdout

    r2 = _cli(empty, "--from", "5")                             # 空窗口 → 明确报错
    assert r2.returncode == 1 and "窗口为空" in r2.stderr


# ── M1 三投影重放等价（SPEC-2 §6.3；SPEC-5 §3.1 最低用例集）───────
#
# 场景：kernel.cluster 全复合流（建簇/扩簇 + S1/S2/S3 传播 + human_verdict 三向 +
# 拆分 + 崩溃窗修复）在 SteppedClock 账本上推进；findings 行建档与 dispatch.submit
# 同纪律——行创建同事务携带 kind=finding(state=open) 事件（「不存在无账本事件的状态
# 变更」），任意前缀 rebuild 三投影与在线逐字段 == 全等。

from qswarm.kernel import (  # noqa: E402  本节专用（M1 聚类面）
    ClusterProposal,
    MemberFacts,
    VerdictContext,
    apply_human_verdict,
    apply_merges,
    close_panel,
    cluster_findings,
    cluster_projection,
    diff_clusters,
    diff_findings,
    finding_projection,
    propagate_pending,
    propagate_verdict,
    rebuild_clusters,
    rebuild_findings,
    split_cluster,
)
from qswarm.kernel import FindingFacts, SteppedClock, ClusterProj, FindingProj  # noqa: E402
from qswarm.kernel.cells import view_cells  # noqa: E402  只读视图（技术债① 清偿面）
from qswarm.contracts import LedgerKind as _LK, SignalTier as _ST  # noqa: E402

_CT0 = datetime(2031, 3, 4, tzinfo=timezone.utc)


def _cluster_scenario():
    """确定性聚类场景：返回 (ledger, 检查点 seq 列表)。

    事件面覆盖：cluster_merge（建簇×2 + 扩簇）、cluster_split、verify_result、
    finding（open/voted/confirmed 流转）、human_verdict（含 direction=0）、vote、
    deposit（S1/S2/S3 全层级 + src_seq），以及「源事件已落、派生存款未落」的崩溃窗
    （propagate_pending 修复）。
    """
    led = KernelLedger(":memory:", clock=SteppedClock(_CT0, 1.0))
    led.conn.execute(
        "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
        " status, started_at, meta_json) VALUES ('R-C','full','repo',600,0,"
        "'running','2031-03-04T00:00:00Z','{}')"
    )

    def seed(fid, s, e, reporter="A1"):
        led.conn.execute(
            "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
            " type, lang, repo, path, func, line_start, line_end, commit_sha,"
            " cluster_id, title, severity, cwe, confidence, grade, state,"
            " disagreement_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, "R-C", reporter, "auditor", "injection", "xss", "py", "repo",
             "a.py", None, s, e, "c", None, "t", "high", "CWE-79", 0.5, "L1",
             "open", None, "2031-03-04T00:00:00Z", "2031-03-04T00:00:00Z"),
        )
        led.append("finding", {"finding_id": fid, "reporter": reporter,
                               "cluster": None, "state": "open"})

    for fid, s, e in [("F1", 10, 20), ("F2", 12, 18), ("F3", 100, 110),
                      ("F4", 200, 210), ("F5", 202, 212)]:
        seed(fid, s, e)
    for r, d in [("R1", 1), ("R2", 1), ("R3", -1)]:
        led.conn.execute(
            "INSERT INTO votes (vote_id, finding_id, reviewer, dim_completeness,"
            " dim_reachability, dim_exploitability, dim_fp_suspicion, direction,"
            " comment_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"V-F1-{r}", "F1", r, 0.8, 0.7, 0.9, 0.1, d, None,
             "2031-03-04T00:00:00Z"),
        )
    led.conn.commit()

    def facts(fid, s, e):
        return FindingFacts(fid, "a.py", s, e, "CWE-79", "py", "injection", "xss")

    checkpoints: list[int] = []
    snaps: list[tuple] = []

    def mark():
        """在线侧快照：检查点 seq + 当时的三投影（物理表不回卷，快照须随事件落账采集）。"""
        seq = led.latest_seq()
        evs = led.read(from_seq=seq, to_seq=seq)
        as_of = (datetime.fromisoformat(evs[0].ts.replace("Z", "+00:00"))
                 if evs else _CT0)
        checkpoints.append(seq)
        snaps.append((seq, dict(view_cells(led, as_of, params=load_params()).cells),
                      dict(cluster_projection(led)), dict(finding_projection(led))))

    ctx = VerdictContext(
        members=(MemberFacts("F1", "A1", "auditor"), MemberFacts("F2", "A2", "auditor"),
                 MemberFacts("F3", "A3", "auditor")),
        scouts=("S1",), votes=(("R1", 1), ("R2", 1), ("R3", -1)), verifier_id="V9",
    )
    # ① 建簇（coarse：{F1,F2}）+ 扩簇（F3 并入）
    props = cluster_findings(
        [facts("F1", 10, 20), facts("F2", 12, 18), facts("F3", 100, 110)],
        level="coarse",
    )
    ev = apply_merges(led, props, run_id="R-C")
    cid1 = ev[0].payload["cluster_id"]
    apply_merges(led, [ClusterProposal(
        level="coarse", member_ids=("F1", "F2", "F3"), key_path="a.py",
        key_cwe="CWE-79", key_lang="py", line_window=(10, 110), sim_min=None,
    )], run_id="R-C")
    mark()
    # ② S2 传播（verify_result → S2 存款 + verdict 投影）
    vsev = led.append("verify_result", {"cluster_id": cid1, "outcome": "verified_real"})
    propagate_verdict(led, cid1, tier=_ST.S2, direction=1, source_seq=vsev.seq,
                      source_kind=_LK.VERIFY_RESULT, ctx=ctx)
    mark()
    # ③ panel 收口（open→voted + S1 存款）
    close_panel(led, "F1", ctx=ctx, expected_votes=3)
    mark()
    # ④ S3 直答 +1（全员 confirmed + S3 存款 + 验证行 CONFIRM）
    apply_human_verdict(led, cid1, direction=1, actor="human", ctx=ctx)
    mark()
    # ⑤ 拆分：{F1,F2} 与 {F3} 各成子簇（成员表改写 + cluster_split 事件，零存款）
    split_cluster(led, cid1, [["F1", "F2"], ["F3"]], actor="human")
    mark()
    # ⑥ 崩溃窗：verify_result(verified_fp) 已落、派生存款未落 → propagate_pending 补齐
    led.append("verify_result", {"cluster_id": cid1, "outcome": "verified_fp"})
    rep = propagate_pending(led, lineage=lambda c: ctx if c == cid1 else None)
    assert rep.events, "崩溃窗修复必须补出存款"
    mark()
    # ⑦ fine 建簇（F4/F5）+ S3 直答 0（忽略：落事件零存款零状态）
    apply_merges(led, [ClusterProposal(
        level="fine", member_ids=("F4", "F5"), key_path="a.py", key_cwe="CWE-79",
        key_lang="py", line_window=(200, 212), sim_min=0.95,
    )], run_id="R-C")
    ev_all = led.read()
    cid2 = next(e.payload["cluster_id"] for e in reversed(ev_all)
                if e.kind is _LK.CLUSTER_MERGE and e.payload["members"] == ["F4", "F5"])
    apply_human_verdict(led, cid2, direction=0, actor="human",
                        ctx=VerdictContext(members=(MemberFacts("F4", "A4", "auditor"),
                                                    MemberFacts("F5", "A5", "auditor"))))
    mark()
    return led, snaps


def test_rebuild_three_projections_arbitrary_prefix():
    """任意时点（0 / 每个复合操作后 / 最新）rebuild 三投影 vs 在线逐字段全等。

    在线快照在事件落账时采集（mark()）；rebuild 从空账重放至同一 seq 截断——
    「任意时点」判据的两侧都钉在该时点，而非终态物理表。"""
    led, snaps = _cluster_scenario()
    latest = led.latest_seq()
    cases = [(0, dict(), dict(), dict())] + list(snaps) + [(latest, None, None, None)]
    for seq, cells_online, clusters_online, findings_online in cases:
        rebuilt_cells = rebuild_cells(led, seq)
        if seq == 0:
            assert rebuilt_cells.cells == {} and rebuild_clusters(led, 0) == {}
            assert rebuild_findings(led, 0) == {}
            continue
        if seq == latest:                             # 终点：物理表即在线态
            cells_online = dict(view_cells(
                led, datetime.fromisoformat(
                    led.read(from_seq=latest, to_seq=latest)[0].ts.replace("Z", "+00:00")),
                params=load_params()).cells)
            clusters_online = dict(cluster_projection(led))
            findings_online = dict(finding_projection(led))
        assert diff_cells(cells_online, rebuilt_cells) == [], f"upto={seq} cells 重放不等价"
        dc = diff_clusters(clusters_online, rebuild_clusters(led, seq))
        df = diff_findings(findings_online, rebuild_findings(led, seq))
        assert dc == [], f"upto={seq} 簇投影重放不等价：{[str(d) for d in dc[:5]]}"
        assert df == [], f"upto={seq} finding 投影重放不等价：{[str(d) for d in df[:5]]}"
    assert len(cases) >= 9                           # 0 + 7 复合操作检查点 + 终点
    led.close()


def test_rebuild_clusters_from_events():
    """clusters/cluster_members/verdict_* ← cluster_merge/cluster_split + deposit
    折叠；members 真源 = 事件增量（D5）；split 清空成员并标记；verdict 投影由
    成员报告者行存款折叠（CL3）。"""
    led, _ = _cluster_scenario()
    proj = rebuild_clusters(led)
    assert len(proj) == 4                              # C1（已拆）+ 两子簇 + C2(F4/F5)
    assert sum(1 for c in proj.values() if c.split) == 1
    c1 = next(c for c in proj.values() if c.split)
    assert c1.members == ()                            # 拆分后成员清空
    # C1 verdict 折叠取最后报告者行：S3(+1) 之后又被崩溃窗修复的 S2(fp −1) 覆盖
    assert (c1.verdict_tier, c1.verdict_dir) == ("S2", -1)
    live = [c for c in proj.values() if not c.split]
    assert {c.level for c in live} == {"coarse", "fine"}
    c2 = next(c for c in live if "F4" in c.members)
    assert (c2.verdict_tier, c2.verdict_dir, c2.verdict_at) == (None, None, None)  # 忽略态
    assert diff_clusters(cluster_projection(led), proj) == []
    led.close()


def test_rebuild_findings_last_state_wins():
    """findings.state 取每 finding 最后一条 finding 事件 payload.state（D9）；
    cluster 归属 = merge 事件折叠（拆分随迁）；与在线物理表逐字段全等。"""
    led, _ = _cluster_scenario()
    proj = rebuild_findings(led)
    assert proj["F1"].state == "confirmed"             # open→voted（收口）→ S3 直答 confirmed
    assert proj["F2"].state == "confirmed"             # open→confirmed（D8 直达边）
    assert proj["F3"].state == "confirmed"
    assert proj["F4"].state == "open" and proj["F5"].state == "open"   # 忽略态零变更
    # cluster 归属 = 最后一条包含该成员的 merge 事件（拆分后 F1/F2 与 F3 分属两子簇）
    assert proj["F1"].cluster_id == proj["F2"].cluster_id
    assert proj["F1"].cluster_id != proj["F3"].cluster_id
    assert proj["F4"].cluster_id == proj["F5"].cluster_id
    assert diff_findings(finding_projection(led), proj) == []
    led.close()


def test_diff_clusters_diff_findings_bit_exact():
    """字段 `==` 全等无容差；presence/每字段差异逐条可读且确定序。"""
    c1 = _cluster_proj("C-1", members=("F1",))
    c2 = _cluster_proj("C-1", members=("F1", "F2"))
    diffs = diff_clusters({"C-1": c1}, {"C-1": c2})
    assert [(d.field, d.left, d.right) for d in diffs] == [
        ("members", ("F1",), ("F1", "F2")),
    ]
    f1 = FindingProj(finding_id="F1", state="open", cluster_id=None)
    f2 = FindingProj(finding_id="F1", state="voted", cluster_id="C-1")
    assert [(d.field, d.left, d.right) for d in diff_findings({"F1": f1}, {"F1": f2})] == [
        ("state", "open", "voted"),
        ("cluster_id", None, "C-1"),
    ]
    # 键集双向 presence
    assert [d.field for d in diff_clusters({"A": c1}, {"B": c1})] == ["presence", "presence"]
    with pytest.raises(ValueError, match="未知比对字段"):
        diff_clusters({"A": c1}, {"A": c1}, fields=("nope",))
    with pytest.raises(ValueError, match="未知比对字段"):
        diff_findings({"F": f1}, {"F": f1}, fields=("grade",))


def _cluster_proj(cid: str, *, members=("F1",), level="coarse",
                  verdict=None, created_at="2031-03-04T00:00:00Z"):
    return ClusterProj(
        cluster_id=cid, level=level, members=tuple(members),
        verdict_tier=verdict[0] if verdict else None,
        verdict_dir=verdict[1] if verdict else None,
        verdict_at=verdict[2] if verdict else None,
        created_at=created_at, split=False,
    )
