"""kernel.ledger 测试：建库/PRAGMA、触发器拒改删、预建缺触发器库补建、注入时钟、payload 校验、过滤读取、写口纪律。"""

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pydantic
import pytest

import qswarm
from qswarm.contracts import DepositPayload, LedgerKind, Role, SignalTier, schema_sql
from qswarm.kernel import (
    FixedClock,
    KernelLedger,
    SteppedClock,
    WallClock,
    iso_utc,
)

DISPATCH_OK = {"task_id": "T-1", "role": "scout", "node": "injection"}
VOTE_OK = {"finding_id": "F-1", "reviewer": "a@v1", "direction": 1}

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z$")


# ── 建库与 PRAGMA ─────────────────────────────────────────────

def test_open_file_db_applies_pragmas_and_schema(tmp_path: Path):
    db = tmp_path / "ledger.db"
    with KernelLedger(db) as led:
        tables = {
            name
            for (name,) in led.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "ledger" in tables and "cells" in tables
        assert led.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert led.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_open_memory_db_and_reopen_is_idempotent(tmp_path: Path):
    db = tmp_path / "ledger.db"
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:01Z")) as led:  # 二次打开不重建
        assert led.latest_seq() == 1
        ev = led.append("dispatch", {"task_id": "T-2", "role": "scout", "node": "authz"})
        assert ev.seq == 2  # AUTOINCREMENT 跨会话延续
    with KernelLedger(":memory:") as led:  # 内存库同样可用
        assert led.latest_seq() == 0


# ── 预建缺触发器库：打开即幂等补建（技术债②，SPEC-5 §6.2，补建口径）──

def _create_prebuilt_db_missing_triggers(db_path: Path) -> None:
    """手工建一个只有 ledger 表、没有任何触发器的库（模拟旧版本/外部工具预建）。"""
    raw = sqlite3.connect(db_path)
    try:
        raw.execute(
            "CREATE TABLE ledger ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts TEXT NOT NULL,"
            " kind TEXT NOT NULL,"
            " run_id TEXT,"
            " payload_json TEXT NOT NULL)"
        )
        raw.commit()
    finally:
        raw.close()


def _trigger_rows(conn: sqlite3.Connection) -> dict[str, str]:
    """库内全部触发器：{名字: 建语句原文（sqlite_master.sql，无尾分号）}。"""
    return {
        name: sql
        for name, sql in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger'"
        )
    }


def test_reopen_prebuilt_db_missing_triggers(tmp_path: Path):
    """预建缺触发器库 → 打开即按 schema.sql 补建两触发器，防御恢复（SPEC-5 §6.2）。"""
    db = tmp_path / "ledger.db"
    _create_prebuilt_db_missing_triggers(db)
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        # 恰好两触发器、不多不少；DDL 逐字来自 schema.sql（含 BEFORE 动词与 ABORT 文案）
        rows = _trigger_rows(led.conn)
        assert set(rows) == {"ledger_no_update", "ledger_no_delete"}
        schema_triggers = {
            m.group("name"): " ".join(m.group(0).split())
            for m in re.finditer(
                r"CREATE TRIGGER (?P<name>\w+) BEFORE (?P<verb>UPDATE|DELETE) ON ledger.*?END;",
                schema_sql(),
                re.DOTALL,
            )
        }
        assert set(schema_triggers) == set(rows)  # 正则独立切分 schema.sql 作交叉对照
        for name, sql in rows.items():
            assert " ".join(sql.split()) + ";" == schema_triggers[name]
        # 防御恢复：UPDATE / DELETE 一律 ABORT（补建口径的行为断言）
        led.append("dispatch", DISPATCH_OK)
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("UPDATE ledger SET kind = 'tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("DELETE FROM ledger")
        assert led.latest_seq() == 1  # 拒绝后原行保留
    # 幂等：再次打开零补建、零报错，触发器原文不变
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:01Z")) as led:
        assert _trigger_rows(led.conn) == rows
        assert led.latest_seq() == 1
        assert led.append("dispatch", {"task_id": "T-2", "role": "scout", "node": "authz"}).seq == 2


def test_reopen_prebuilt_db_partial_trigger_repair(tmp_path: Path):
    """只缺一个触发器（部分缺失）：只补缺的那个，已有的原样保留（幂等补建的另一半）。"""
    db = tmp_path / "ledger.db"
    _create_prebuilt_db_missing_triggers(db)
    raw = sqlite3.connect(db)
    try:
        raw.execute(
            "CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger"
            " BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;"
        )
        raw.commit()
    finally:
        raw.close()
    with KernelLedger(db) as led:
        rows = _trigger_rows(led.conn)
        assert set(rows) == {"ledger_no_update", "ledger_no_delete"}
        led.append("dispatch", DISPATCH_OK)
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("DELETE FROM ledger")  # 补上的 delete 防御生效
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("UPDATE ledger SET kind = 'tampered'")


def test_reopen_prebuilt_db_repair_failure_raises(tmp_path: Path, monkeypatch):
    """补建失败 → RuntimeError 显式拒绝打开，绝不静默降级（补建口径的兜底分支）。"""
    import qswarm.kernel.ledger as ledger_mod

    db = tmp_path / "ledger.db"
    _create_prebuilt_db_missing_triggers(db)
    # 注入坏 DDL（update 可补、delete 非法缺 BEFORE DELETE ON 子句）模拟「无法补建」
    monkeypatch.setattr(
        ledger_mod, "schema_sql",
        lambda: (
            "CREATE TRIGGER ledger_no_update BEFORE UPDATE ON ledger\n"
            " BEGIN SELECT RAISE(ABORT, 'ledger is append-only'); END;\n"
            "CREATE TRIGGER ledger_no_delete\n"
            " BEGIN SELECT 1; END;\n"
        ),
    )
    with pytest.raises(RuntimeError, match=r"ledger_no_delete.*补建失败"):
        KernelLedger(db)
    # 失败即停：坏的那个未留下半建状态，库内无任何数据写入
    raw = sqlite3.connect(db)
    try:
        assert "ledger_no_delete" not in _trigger_rows(raw)
        (count,) = raw.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert count == 0
    finally:
        raw.close()


def test_normal_db_reopen_keeps_triggers_untouched(tmp_path: Path):
    """正常库行为不变：表+触发器齐备时打开不做任何 schema 写入，触发器零改动。"""
    db = tmp_path / "ledger.db"
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:01Z")) as led:
        rows = _trigger_rows(led.conn)
        assert list(sorted(rows)) == ["ledger_no_delete", "ledger_no_update"]  # 恰两条、无重复
        assert led.append("dispatch", {"task_id": "T-2", "role": "scout", "node": "authz"}).seq == 2
    raw = sqlite3.connect(db)
    try:
        assert _trigger_rows(raw) == rows  # 关闭后复核：触发器原文与打开时逐字一致
    finally:
        raw.close()


# ── 写口：append 落库 + 触发器 append-only（验收 ac_muz07ulj6seasd7e）──

def test_append_persists_validated_payload():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        ev = led.append("dispatch", DISPATCH_OK, run_id="R-1")
        assert isinstance(ev.seq, int) and ev.seq == 1
        row = led.conn.execute(
            "SELECT ts, kind, run_id, payload_json FROM ledger WHERE seq = 1"
        ).fetchone()
        assert row == (
            "2030-01-01T00:00:00Z",
            "dispatch",
            "R-1",
            '{"task_id":"T-1","role":"scout","node":"injection","assignee":null,"hop_budget":0}',
        )
        assert row[0] == ev.ts


def test_update_rejected_by_trigger_after_append():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("UPDATE ledger SET payload_json = '{\"tampered\":true}'")
        # RAISE(ABORT) 拒绝语句且保留事务：原行未被改动
        (payload_json,) = led.conn.execute(
            "SELECT payload_json FROM ledger WHERE seq = 1"
        ).fetchone()
        assert "tampered" not in payload_json


def test_delete_rejected_by_trigger_after_append():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("DELETE FROM ledger")
        assert led.latest_seq() == 1


def test_row_targeted_update_rejected_by_trigger():
    """行级定向 UPDATE（WHERE seq=1）同样被触发器语句级 ABORT（ac_…6seasd7e 的“对行”口径）。"""
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
        led.append("vote", VOTE_OK)
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            led.conn.execute("UPDATE ledger SET ts = '1999-01-01T00:00:00Z' WHERE seq = 1")
        # 拒绝后两行原样保留：ts/kind 未被改动，seq 无空洞
        rows = led.conn.execute("SELECT seq, ts, kind FROM ledger ORDER BY seq").fetchall()
        assert rows == [
            (1, "2030-01-01T00:00:00Z", "dispatch"),
            (2, "2030-01-01T00:00:00Z", "vote"),
        ]


def test_append_only_holds_from_independent_connection(tmp_path: Path):
    """库级强制（非连接级）：另开裸连接同样改不动、删不掉。"""
    db = tmp_path / "ledger.db"
    with KernelLedger(db, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
    raw = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            raw.execute("UPDATE ledger SET kind = 'tampered'")
        with pytest.raises(sqlite3.IntegrityError, match="ledger is append-only"):
            raw.execute("DELETE FROM ledger")
        (count,) = raw.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert count == 1
    finally:
        raw.close()


# ── 内核时钟与 seq（验收 ac_muz07uljsb3ipjo0）──────────────────

def test_fixed_clock_ts_predictable_and_iso_utc():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T12:00:00Z")) as led:
        for i in range(1, 4):
            ev = led.append("vote", VOTE_OK)
            assert ev.ts == "2030-01-01T12:00:00Z"  # 固定时钟：完全可预期
            assert ISO_UTC.match(ev.ts)
            dt = datetime.fromisoformat(ev.ts)
            assert dt.utcoffset() == timezone.utc.utcoffset(dt)  # UTC 时刻
            assert ev.seq == i  # seq 严格单调递增（1,2,3 连续）


def test_seq_strictly_monotonic_across_kinds():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        payloads = [
            ("dispatch", DISPATCH_OK),
            ("vote", VOTE_OK),
            ("dispatch", {"task_id": "T-2", "role": "auditor", "node": "deser"}),
        ] * 10
        seqs = [led.append(kind, p).seq for kind, p in payloads]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
        assert all(b - a == 1 for a, b in zip(seqs, seqs[1:]))  # 无空洞


def test_stepped_clock_advances():
    with KernelLedger(":memory:", clock=SteppedClock("2030-01-01T00:00:00Z", step_s=60)) as led:
        ts_list = [led.append("vote", VOTE_OK).ts for _ in range(3)]
        assert ts_list == [
            "2030-01-01T00:00:00Z",
            "2030-01-01T00:01:00Z",
            "2030-01-01T00:02:00Z",
        ]


def test_wall_clock_emits_iso_utc():
    with KernelLedger(":memory:") as led:  # 缺省 WallClock
        ev = led.append("vote", VOTE_OK)
        assert ISO_UTC.match(ev.ts)
        assert datetime.fromisoformat(ev.ts).tzinfo is not None


def test_iso_utc_rejects_naive_datetime():
    with pytest.raises(ValueError, match="naive"):
        iso_utc(datetime(2030, 1, 1))


def test_clock_rejects_naive_input():
    with pytest.raises(ValueError, match="naive"):
        FixedClock(datetime(2030, 1, 1))


# ── payload 校验（验收 ac_muz07ulj20lei8q8）────────────────────

def test_unknown_kind_rejected_and_not_persisted():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        with pytest.raises(ValueError, match="kind"):
            led.append("gossip", {"anything": 1})
        assert led.latest_seq() == 0


def test_missing_field_rejected_and_not_persisted():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", DISPATCH_OK)
        with pytest.raises(pydantic.ValidationError, match="node"):
            led.append("dispatch", {"task_id": "T-9", "role": "scout"})  # 缺 node
        assert led.latest_seq() == 1  # 非法载荷不落库


def test_enum_out_of_range_rejected_and_not_persisted():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        # 枚举越界：role 不在 Role 词表
        with pytest.raises(pydantic.ValidationError, match="role"):
            led.append("dispatch", {"task_id": "T-9", "role": "wizard", "node": "injection"})
        # 数值越界：direction ∉ ±1
        with pytest.raises(pydantic.ValidationError, match="direction"):
            led.append("vote", {"finding_id": "F-1", "reviewer": "a@v1", "direction": 5})
        # 枚举越界：signal_tier 不在 SignalTier 词表
        with pytest.raises(pydantic.ValidationError, match="signal_tier"):
            led.append(
                "deposit",
                {
                    "finding": "F-1", "cluster": None, "agent": "a@v1", "role": "scout",
                    "taxonomy_path": ["injection"], "signal_tier": "S9",
                    "direction": 1, "weight": 0.2,
                },
            )
        assert led.latest_seq() == 0


def test_rejected_append_does_not_consume_seq():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        assert led.append("vote", VOTE_OK).seq == 1
        with pytest.raises(pydantic.ValidationError):
            led.append("vote", {"finding_id": "F-2"})  # 缺 reviewer/direction
        assert led.append("vote", VOTE_OK).seq == 2  # 无空洞


def test_append_accepts_model_instance_and_dict_equally():
    payload = DepositPayload(
        finding="F-1", cluster=None, agent="a@v1", role=Role.SCOUT,
        taxonomy_path=["injection"], signal_tier=SignalTier.S3, direction=1, weight=1.0,
    )
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        ev_model = led.append("deposit", payload)
        ev_dict = led.append(
            "deposit",
            {
                "finding": "F-1", "cluster": None, "agent": "a@v1", "role": "scout",
                "taxonomy_path": ["injection"], "signal_tier": "S3",
                "direction": 1, "weight": 1.0,
            },
        )
        assert ev_model.payload == ev_dict.payload
        assert ev_model.payload["role"] == "scout" and ev_model.payload["signal_tier"] == "S3"


# ── 读取：kind / seq 过滤（验收 ac_muz07ulj20lei8q8）───────────

@pytest.fixture()
def mixed_ledger():
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        led.append("dispatch", {"task_id": "T-1", "role": "scout", "node": "injection"})
        led.append("vote", VOTE_OK)
        led.append("dispatch", {"task_id": "T-2", "role": "auditor", "node": "deser"})
        led.append("vote", {"finding_id": "F-2", "reviewer": "b@v1", "direction": -1})
        led.append("dispatch", {"task_id": "T-3", "role": "verifier", "node": "crypto"})
        yield led


def test_read_without_filter_returns_all_ordered(mixed_ledger):
    events = mixed_ledger.read()
    assert [e.seq for e in events] == [1, 2, 3, 4, 5]
    assert all(isinstance(e.kind, LedgerKind) for e in events)


def test_read_filter_by_kind(mixed_ledger):
    votes = mixed_ledger.read(kinds=["vote"])
    assert [(e.seq, e.kind) for e in votes] == [(2, LedgerKind.VOTE), (4, LedgerKind.VOTE)]
    assert votes[0].payload == VOTE_OK
    both = mixed_ledger.read(kinds=[LedgerKind.DISPATCH, "vote"])
    assert [e.seq for e in both] == [1, 2, 3, 4, 5]


def test_read_filter_by_seq_range(mixed_ledger):
    assert [e.seq for e in mixed_ledger.read(from_seq=2, to_seq=4)] == [2, 3, 4]
    assert [e.seq for e in mixed_ledger.read(from_seq=4)] == [4, 5]  # 开区间语义：from 起全量
    assert [e.seq for e in mixed_ledger.read(to_seq=1)] == [1]
    assert mixed_ledger.read(from_seq=6) == []  # 越界为空


def test_read_filter_combined_kind_and_range(mixed_ledger):
    events = mixed_ledger.read(kinds=["dispatch"], from_seq=2, to_seq=5)
    assert [(e.seq, e.payload["task_id"]) for e in events] == [(3, "T-2"), (5, "T-3")]


def test_read_empty_kind_whitelist_returns_empty(mixed_ledger):
    assert mixed_ledger.read(kinds=[]) == []


# ── rebuild 入口（实现已由 kernel.replay 落地）────────────────

def test_rebuild_delegates_to_replay_and_rewrites_cells(tmp_path: Path):
    """KernelLedger.rebuild 委托 kernel.replay.rebuild_cells：整表重写 + 与 agg_cells 一致。"""
    from qswarm.kernel import agg_cells

    db = tmp_path / "ledger.db"
    for i, ts in enumerate(
        ["2030-01-01T00:00:00Z", "2030-01-31T00:00:00Z"], start=1
    ):
        with KernelLedger(db, clock=FixedClock(ts)) as led:
            led.append(
                "deposit",
                {
                    "finding": None, "cluster": None, "agent": f"a{i}", "role": "auditor",
                    "taxonomy_path": ["injection"], "signal_tier": "S3",
                    "direction": 1, "weight": 1.0,
                },
            )
    with KernelLedger(db) as led:
        view = led.rebuild()                       # 默认全量（from_seq=0, upto=latest）
        rows = led.conn.execute(
            "SELECT COUNT(*) FROM cells"
        ).fetchone()[0]
        assert rows == len(view.cells) == 2        # cells 表已被整表重写
        again = agg_cells(led, "2030-01-31T00:00:00Z")
        assert view.cells == again.cells           # 与直接聚合逐位一致

    with KernelLedger(db) as led, pytest.raises(ValueError, match="from_seq"):
        led.rebuild(from_seq=-1)                   # 非法窗口被拒绝
    with KernelLedger(db) as led, pytest.raises(ValueError, match="窗口为空"):
        led.rebuild(from_seq=99)                   # 空窗口（越过 latest_seq）被拒绝


# ── 写口纪律（SPEC-1 §6-2 的静态 CI 断言）──────────────────────

WRITE_STMT = re.compile(
    r"\b(INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+(ledger|cells)\b",
    re.IGNORECASE,
)


def test_write_port_static_scan():
    """ledger/cells 的写语句只允许出现在 kernel/ 内（蜂与仿真器只能读或走 append）。"""
    pkg_root = Path(qswarm.__file__).parent
    offenders: dict[str, list[str]] = {}
    for path in sorted(pkg_root.rglob("*.py")):
        hits = WRITE_STMT.findall(path.read_text(encoding="utf-8"))
        if hits:
            rel = path.relative_to(pkg_root).as_posix()
            if not rel.startswith("kernel/"):
                offenders[rel] = [f"{verb} {table}" for verb, table in hits]
    assert offenders == {}, f"ledger/cells 写口越界：{offenders}"
