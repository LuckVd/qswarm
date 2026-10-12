"""DDL 测试：schema.sql 在内存 SQLite 建库成功；ledger 行 UPDATE/DELETE 被触发器拒绝。"""

import sqlite3

import pytest

from qswarm.contracts import schema_sql

EXPECTED_TABLES = {
    "runs", "tasks", "candidates", "findings", "evidence_nodes",
    "clusters", "cluster_members", "votes", "verify_results",
    "ledger", "cells", "snapshots", "agents", "hats",
    "exam_items", "exam_results", "artifacts", "org_metrics",
}

INSERT_LEDGER = (
    "INSERT INTO ledger (ts, kind, run_id, payload_json) "
    "VALUES ('2026-10-08T00:00:00Z', 'dispatch', NULL, '{}')"
)


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("PRAGMA foreign_keys = ON")
    c.executescript(schema_sql())
    yield c
    c.close()


def test_schema_builds_in_memory(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    tables = {name for (name,) in rows}
    missing = EXPECTED_TABLES - tables
    assert not missing, f"建库后缺表：{sorted(missing)}"


def test_ledger_triggers_exist(conn):
    names = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'"
        ).fetchall()
    }
    assert {"ledger_no_update", "ledger_no_delete"} <= names


def test_ledger_append_ok(conn):
    conn.execute(INSERT_LEDGER)
    (count,) = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert count == 1


def test_ledger_update_rejected(conn):
    conn.execute(INSERT_LEDGER)
    with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
        conn.execute("UPDATE ledger SET kind = 'tampered'")
    # RAISE(ABORT) 拒绝语句但保留事务：原行未被改动
    (kind,) = conn.execute("SELECT kind FROM ledger").fetchone()
    assert kind == "dispatch"


def test_ledger_delete_rejected(conn):
    conn.execute(INSERT_LEDGER)
    with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
        conn.execute("DELETE FROM ledger")
    (count,) = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert count == 1


def test_ledger_append_only_survives_seeded_rows(conn):
    """多行场景下 UPDATE/DELETE 同样整体被拒（语句级 ABORT）。"""
    conn.executemany(
        "INSERT INTO ledger (ts, kind, payload_json) VALUES (?, 'deposit', '{}')",
        [(f"2026-10-0{d}T00:00:00Z",) for d in (1, 2, 3)],
    )
    with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
        conn.execute("UPDATE ledger SET payload_json = '{}'")
    with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
        conn.execute("DELETE FROM ledger WHERE kind = 'deposit'")
    (count,) = conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
    assert count == 3


def test_foreign_keys_enforced(conn):
    """PRAGMA foreign_keys=ON 生效：无父 runs 行时插 tasks 必须失败。"""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO tasks (task_id, run_id, role, node, shard_json, created_at) "
            "VALUES ('T-x', 'R-none', 'scout', 'injection', '[]', '2026-10-08T00:00:00Z')"
        )
