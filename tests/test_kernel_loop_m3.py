"""kernel.loop 测试（SPEC-7 §2/§3；M3 卷 A 组 §1.5 + D 组端到端判据）。

钉死：候选分片 → DispatchContract 装配字段逐项断言、temp 池 least-loaded（过载
剔除语义 = 负载均衡）、L1 verdict 存款 ×0.2 落账、promote_scan 影子挂接
（扫描痕在位 + ENTRY_TOP_FRAC=0 下零晋升事件）、端到端 fast lane 全链绿、
同仓库重跑幂等（零新候选、账本零重复存款）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind, load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger
from qswarm.kernel.loop import active_scouts, run_scan

import m2_harness
import m3_harness
from m3_harness import ScriptedScoutAdapter, fake_rules_hit, fake_rules_ok


@pytest.fixture()
def prod(tmp_path):
    """就绪生产账本（bootstrap 全链）+ 小仓库 + 参数副本。"""
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False               # L1 焦点：通道 B 关
    return tmp_path, repo, params


def _dispatch_events(led, run_id=None):
    evs = led.read(kinds=(LedgerKind.DISPATCH,))
    if run_id:
        evs = [e for e in evs if e.run_id == run_id]
    return evs


def test_loop_l0_to_l1_shard_contract(prod):
    """M3 卷 §1.5：候选分片 → DispatchContract(role=scout) 装配字段逐项断言
    （tasks 行 + DISPATCH 事件 + 渲染头行三面对账）。"""
    tmp, repo, params = prod
    adapter = ScriptedScoutAdapter()
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-M3-TEST",
            params=params, adapter=adapter, store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0),
            promote=False,
        )
        assert report.candidates_registered > 0
        assert report.tasks_dispatched == report.candidates_registered
        # tasks 行：role=scout、node=分片多数族、task_id l1: 前缀、终态 done
        rows = led.conn.execute(
            "SELECT task_id, role, node, state, assignee FROM tasks ORDER BY task_id"
        ).fetchall()
        assert rows and all(r[1] == "scout" for r in rows)
        assert all(r[0].startswith("l1:") for r in rows)
        assert all(r[3] == "done" for r in rows)
        # DISPATCH 事件：assignee 在 active scout 池内、hop_budget=0（scout 不深挖）
        pool = {a for _ref, a in active_scouts(led)}
        for ev in _dispatch_events(led, "R-M3-TEST"):
            assert ev.payload["role"] == "scout"
            assert ev.payload["assignee"] in pool
            assert ev.payload["hop_budget"] == 0
        # 渲染面：goal 头行 = L1 初筛语义（契约四件套 ①）；分片报告：full 档目录级
        assert report.shards and all("node" in s and "files" in s for s in report.shards)


def test_loop_scout_least_loaded(prod):
    """M3 卷 §1.5：temp 池 least-loaded——两侦察蜂负载差 ≤1（平手字典序）。"""
    tmp, repo, params = prod
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-LL",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0), promote=False,
        )
        n = report.tasks_dispatched
        assert n >= 2
        counts: dict[str, int] = {}
        for ev in _dispatch_events(led, "R-LL"):
            counts[ev.payload["assignee"]] = counts.get(ev.payload["assignee"], 0) + 1
        assert sum(counts.values()) == n
        assert max(counts.values()) - min(counts.values()) <= 1


def test_loop_deposit_x02(prod):
    """M3 卷 §1.5：L1 verdict 存款 ×0.2 落账——S1/scout/weight=SCOUT_X(0.2)/
    origin=prod/suspicious=+1 或拦截=-1；分片多数族 cell 归属。"""
    tmp, repo, params = prod
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-DEP",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0), promote=False,
        )
        deposits = [
            e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
            if e.payload.get("origin") == "prod"
        ]
        assert report.deposits_written == len(deposits) > 0
        for ev in deposits:
            p = ev.payload
            assert p["role"] == "scout"
            assert p["signal_tier"] == "S1"
            assert p["weight"] == pytest.approx(params.deposits.SCOUT_X)
            assert p["direction"] in (1, -1)
            assert len(p["taxonomy_path"]) == 1
        # ScriptedScoutAdapter：api.py/util.c 候选 suspicious=True（家族猜测对齐线索）
        suspicious = [e for e in deposits if e.payload["direction"] == 1]
        assert suspicious                                  # 存活判定有供血


def test_loop_promote_scan_shadow_inert(prod):
    """M3 卷 §1.5：影子挂接后状态账有扫描痕、ENTRY_TOP_FRAC=0 下零晋升事件。"""
    tmp, repo, params = prod
    assert params.exam.ENTRY_TOP_FRAC > 0                  # params 原值非零（影子置 0 才是判据）
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-SHADOW",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0),
            promote=True,
        )
        # 扫描痕：dispatch/deposit 事件在位
        assert _dispatch_events(led, "R-SHADOW")
        # 零晋升事件（影子期只产账，不动编制）
        assert led.read(kinds=(LedgerKind.PROMOTION,)) == []
        assert report.promote_transitions == 0
        # 编制未被影子扫描改动
        statuses = {s for (_a, s) in led.conn.execute("SELECT agent_id, status FROM agents")}
        assert statuses == {"active"}


def test_loop_schema_fail_retry_then_absent(tmp_path):
    """L1 schema 畸形：改派重试 1 次仍败 → l1_schema_fail 计数 + 任务 failed + 零存款
    （初派 1 + 重派 1，§16.7-8；侦察面宽松不阻塞漏斗）。"""
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False

    class _AlwaysBad:
        def complete(self, *, model_ref, messages, temperature, timeout_s=None):
            return "not-json"

    with KernelLedger(tmp_path / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-FAIL",
            params=params, adapter=_AlwaysBad(), store_dir=tmp_path / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0), promote=False,
        )
        assert report.tasks_failed == report.candidates_registered > 0
        assert report.l1_schema_fail == report.candidates_registered
        assert report.l1_infra_fail == 0
        assert report.deposits_written == 0
        states = {s for (s,) in led.conn.execute("SELECT state FROM tasks")}
        assert states == {"failed"}
        failed_rows = led.conn.execute("SELECT attempts FROM tasks").fetchall()
        assert all(a == 2 for (a,) in failed_rows)         # 初派 1 + 重派 1（§16.7-8）
        assert led.read(kinds=(LedgerKind.PROMOTION,)) == []


def test_e2e_fast_lane(tmp_path):
    tmp = tmp_path
    """M3 卷 D 组：种子小仓 → L0（A/B/C 三通道）→ L1 初筛 → 存款落账 → 扫描报告
    （通道计数/缺席/schema_fail/候选数）全链绿。"""
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()                                 # 通道 B 开（全通道 e2e）
    adapter = ScriptedScoutAdapter()
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        report = run_scan(
            led, repo_root=repo, mode="full", run_id="R-E2E",
            params=params, adapter=adapter, store_dir=tmp / "store",
            cache_dir=tmp / "cache",
            rules_runner=fake_rules_hit, clock=FixedClock(m3_harness.T0),
            promote=True,
        )
        scan = report.scan
        assert scan["counts"]["channel_a"] > 0
        assert scan["counts"]["channel_c"] == 1
        assert scan["counts"]["candidates"] == report.candidates_registered
        assert scan["artifact_ref"]
        assert report.tasks_done == report.candidates_registered
        assert report.deposits_written > 0
        # l0scan 通道 B 真的跑了（scout 兼任，D26）且零 deposit 归因（B 无对错语义）
        assert any(c[0] == "l0scan" for c in adapter.calls)
        b_deposits = [
            e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
            if e.payload.get("origin") == "exam" and e.run_id == "R-E2E"
        ]
        assert b_deposits == []                            # 账本零 deposit（B/L0）
        # 工件库：候选集工件在位
        ref = scan["artifact_ref"]
        assert (tmp / "store" / ref[:2] / ref).exists()


def test_e2e_idempotent_rerun(tmp_path):
    tmp = tmp_path
    """M3 卷 D 组：同仓库重跑零新候选、账本零重复存款。"""
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False
    with KernelLedger(tmp / "prod.db", clock=FixedClock(m3_harness.T0)) as led:
        r1 = run_scan(
            led, repo_root=repo, mode="full", run_id="R-RERUN",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0), promote=False,
        )
        seq1 = led.latest_seq()
        n_tasks1 = led.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        n_cands1 = led.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        n_deps1 = sum(
            1 for e in led.read(kinds=(LedgerKind.DEPOSIT,)) if e.payload.get("origin") == "prod"
        )
        r2 = run_scan(
            led, repo_root=repo, mode="full", run_id="R-RERUN",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(m3_harness.T0), promote=False,
        )
        assert r2.candidates_registered == 0               # 候选表零新行
        assert r2.tasks_dispatched == 0
        assert r2.deposits_written == 0
        assert led.latest_seq() == seq1
        assert led.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == n_tasks1
        assert led.conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == n_cands1
        n_deps2 = sum(
            1 for e in led.read(kinds=(LedgerKind.DEPOSIT,)) if e.payload.get("origin") == "prod"
        )
        assert n_deps2 == n_deps1 == r1.deposits_written
