"""kernel.bootstrap 测试（SPEC-7 §3.1/D28；M3 卷 C 组判据）。

钉死：空账本引导导入（4 active + 20 考试存款、payload 逐字等值 + src_seq 溯源、
route 据此可选出非均匀目标）、二次导入幂等零重复、种子库只读不触碰、
verify_ready 防空账本启动（fail-closed）。
"""

from __future__ import annotations

import sqlite3

import pytest

from qswarm.contracts import LedgerKind
from qswarm.kernel import FixedClock, KernelLedger, agg_cells, route
from qswarm.kernel.bootstrap import (
    MIN_ACTIVE_AGENTS,
    MIN_EXAM_DEPOSITS,
    init_prod_ledger,
    verify_ready,
)

import m2_harness
import m3_harness


@pytest.fixture()
def seed_db(tmp_path):
    bank_root = tmp_path / "bank"
    return m3_harness.make_seed_db(tmp_path / "seed.db", bank_root)


def test_constants_match_spec():
    """开工校验常量 = §3.1 逐字（4 active / ≥20 存款）。"""
    assert MIN_ACTIVE_AGENTS == 4 and MIN_EXAM_DEPOSITS == 20


def test_bootstrap_import(tmp_path, seed_db):
    """M3 卷 C 组：空库导入 4 active + 20 考试存款，payload 逐字等值、src_seq
    溯源在位，route() 据此可选出非均匀目标。"""
    prod_path = tmp_path / "prod.db"
    report = init_prod_ledger(prod_path, seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    assert report.agents_imported == 4 and report.agents_skipped == 0
    assert report.deposits_imported == 20

    # payload 逐字等值（src_seq 注记除外）+ seq 重编配 + src_seq 溯源
    seed = sqlite3.connect(f"file:{seed_db}?mode=ro", uri=True)
    seed_rows = seed.execute(
        "SELECT seq, payload_json FROM ledger WHERE kind='deposit' ORDER BY seq"
    ).fetchall()
    seed.close()
    with KernelLedger(prod_path, clock=FixedClock(m3_harness.T0)) as prod:
        events = prod.read(kinds=(LedgerKind.DEPOSIT,))
        assert len(events) == 20
        for (src_seq, payload_json), ev in zip(seed_rows, events):
            expected = m2_harness.json.loads(payload_json)
            assert ev.payload == {**expected, "src_seq": src_seq}
            assert ev.payload["origin"] == "exam"
        ready = verify_ready(prod)
        assert len(ready["active"]) == 4 and ready["exam_deposits"] == 20
        # route() 据存款可选出非均匀目标（格子已折叠，探索池非空）
        view = agg_cells(prod, "2030-03-01T00:00:00Z", )
        assert view.cells                                    # 考试存款已折叠成格

        class _Task:
            task_id = "T-probe"
            role = "scout"
            taxonomy_path = ["injection"]

        import random

        picks = {
            route(_Task(), view, random.Random(s), __import__(
                "qswarm.contracts", fromlist=["load_params"]
            ).load_params()).assignee
            for s in range(20)
        }
        assert len(picks) >= 1 and None not in picks         # 池非空可派


def test_bootstrap_idempotent(tmp_path, seed_db):
    """M3 卷 C 组：二次导入零重复行零重复存款。"""
    prod_path = tmp_path / "prod.db"
    r1 = init_prod_ledger(prod_path, seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    with KernelLedger(prod_path, clock=FixedClock(m3_harness.T0)) as led:
        n_events_1 = led.latest_seq()
        n_agents_1 = led.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    r2 = init_prod_ledger(prod_path, seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    assert r2.agents_imported == 0 and r2.agents_skipped == 4
    assert r2.deposits_imported == 0 and r2.deposits_skipped == 20
    with KernelLedger(prod_path, clock=FixedClock(m3_harness.T0)) as led:
        assert led.latest_seq() == n_events_1
        assert led.conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == n_agents_1
        assert led.conn.execute("SELECT COUNT(*) FROM hats").fetchone()[0] == 4


def test_bootstrap_seed_db_readonly(tmp_path, seed_db):
    """播种原库只读不触碰：导入前后种子账本逐位不变（seq/行数/文件字节）。"""
    before = seed_db.read_bytes()
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))
    # WAL 收敛后比内容面（seq 与事件数）
    seed = sqlite3.connect(f"file:{seed_db}?mode=ro", uri=True)
    n = seed.execute("SELECT COUNT(*), COALESCE(MAX(seq),0) FROM ledger").fetchone()
    n_agents = seed.execute("SELECT COUNT(*) FROM agents").fetchone()[0]
    seed.close()
    assert n == (20, 20) and n_agents == 4                 # 播种库自身形态不变（20 存款事件）
    _ = before


def test_verify_ready_fail_closed(tmp_path):
    """防空账本启动：空库 / 欠存款账本 verify_ready 一律 RuntimeError。"""
    with KernelLedger(tmp_path / "empty.db", clock=FixedClock(m3_harness.T0)) as led:
        with pytest.raises(RuntimeError, match="生产账本未就绪"):
            verify_ready(led)
        # 单蜂在编仍不够
        led.conn.execute(
            "INSERT INTO agents (agent_id, model_ref, contract_version, status,"
            " created_at) VALUES ('a@cv1', 'cheap:x/y', 'cv1', 'active', 't')"
        )
        with pytest.raises(RuntimeError, match="生产账本未就绪"):
            verify_ready(led)


def test_bootstrap_missing_seed(tmp_path):
    with pytest.raises(ValueError, match="种子账本不存在"):
        init_prod_ledger(tmp_path / "p.db", seed_db_path=tmp_path / "nope.db")


def test_bootstrap_reimport_new_seed_db_imports_new_cells(tmp_path, seed_db):
    """跨播种库重导（2026-10-20 修复）：幂等键 = 格键 (agent, role, taxonomy_path)
    ——新播种库 seq 从 1 重编不与旧库 src_seq 碰撞；旧格跳过、新格（重考/补播
    产生）导入。修复前 (origin=exam, src_seq) 查重使新库 seq 1..N 与首导全量
    碰撞、「新库重导更新编制」通道断裂。"""
    prod_path = tmp_path / "prod.db"
    init_prod_ledger(prod_path, seed_db_path=seed_db, clock=FixedClock(m3_harness.T0))

    # 第二播种库：同编制行 + 一笔新格考试存款（seq=1——与旧库首笔 src_seq=1 相同，
    # 旧逻辑据此误判「已导入」；格键按 (agent, role, family) 判定为新格）。
    new_seed = tmp_path / "seed2.db"
    with KernelLedger(new_seed, clock=FixedClock(m3_harness.T0)) as led:
        src = sqlite3.connect(f"file:{seed_db}?mode=ro", uri=True)
        agents = src.execute(
            "SELECT agent_id, model_ref, contract_version, status, created_at,"
            " last_active_at FROM agents ORDER BY agent_id"
        ).fetchall()
        hats = src.execute(
            "SELECT agent_id, role, pool_class FROM hats ORDER BY agent_id, role"
        ).fetchall()
        src.close()
        led.conn.executemany(
            "INSERT INTO agents (agent_id, model_ref, contract_version, status,"
            " created_at, last_active_at) VALUES (?, ?, ?, ?, ?, ?)", agents)
        led.conn.executemany(
            "INSERT INTO hats (agent_id, role, pool_class) VALUES (?, ?, ?)", hats)
        led.conn.commit()
        led.append("deposit", {
            "finding": None, "cluster": None,
            "agent": agents[0][0], "role": "reviewer",
            "taxonomy_path": ["injection"], "signal_tier": "S1",
            "direction": 1, "weight": 0.3, "origin": "exam",
        })

    report = init_prod_ledger(prod_path, seed_db_path=new_seed,
                              clock=FixedClock(m3_harness.T0))
    assert report.agents_imported == 0 and report.agents_skipped == 4
    assert report.deposits_imported == 1 and report.deposits_skipped == 0
    with KernelLedger(prod_path, clock=FixedClock(m3_harness.T0)) as prod:
        n = sum(1 for ev in prod.read(kinds=(LedgerKind.DEPOSIT,))
                if ev.payload.get("origin") == "exam")
        assert n == 21                                   # 20 旧格 + 1 新格，零重复
