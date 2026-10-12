"""kernel.write_guard 测试：读写并发纪律护栏与写口审计（SPEC-2 §4.5/§6.1/§7）。

三层钉死：

- **运行时护栏** ``read_only_guard``：读侧会话对 cells/ledger 的任何写（直连 SQL、
  DDL、append、agg_cells 整表重写）一律被 SQLite 以 "attempt to write a readonly
  database" 拒绝——负向护栏：非唯一写口路径物理不可写；读与 view_cells 只读投影
  不受影响；退出/异常/嵌套/预先只读四路护栏语义逐一对账（可重入、fail-closed）；
- **静态审计** ``audit_write_ports``：真包全 11 表词表零违规、写口清单恰含 §6.1
  三处 M0 圣化写口、strict 零漂移；负向注入（kernel/ 外伪造写语句、kernel/ 内
  非圣化模块写语句）必被一键命令拦截（退出码 1/2）——护栏自身可检不可骗；
- **读侧约定**（§4.5：读走重放投影、不落盘）：view_cells 与写路径视图逐位相等
  （diff_cells 空）且零写副作用——行集全列逐位不变、total_changes 零增量、
  空 cells 表保持为空。
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import qswarm
from qswarm.kernel import FixedClock, KernelLedger, diff_cells
from qswarm.kernel.cells import agg_cells, view_cells
from qswarm.kernel.write_guard import (
    EXPECTED_WRITE_PORTS,
    WRITE_TABLES,
    audit_write_ports,
    read_only_guard,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = "2030-01-01T00:00:00Z"
AS_OF = "2030-03-01T00:00:00Z"

DEPOSIT = {
    "finding": None,
    "cluster": None,
    "agent": "alice",
    "role": "auditor",
    "taxonomy_path": ["injection"],
    "signal_tier": "S3",
    "direction": 1,
    "weight": 1.0,
}

CELLS_COLS = (
    "SELECT agent_id, role, node, pos_w, neg_w, n_eff, n_eff_s2s3,"
    " status, activated_at, last_event_seq, updated_at FROM cells"
    " ORDER BY agent_id, role, node"
)


def _deposit_ledger(db_path: Path) -> Path:
    """最小口碑账本：三笔存款（两 agent、两时点、含一笔负向 S2）。"""
    with KernelLedger(db_path, clock=FixedClock(T0)) as led:
        led.append("deposit", DEPOSIT)
    with KernelLedger(db_path, clock=FixedClock("2030-02-01T00:00:00Z")) as led:
        led.append(
            "deposit",
            {
                **DEPOSIT,
                "agent": "bob",
                "role": "scout",
                "taxonomy_path": ["injection", "sqli"],
                "signal_tier": "S2",
                "weight": 0.5,
            },
        )
        led.append("deposit", {**DEPOSIT, "signal_tier": "S2", "direction": -1, "weight": 0.25})
    return db_path


def _make_pkg(root: Path, files: dict[str, str]) -> Path:
    """造一个最小假包树供静态审计负向注入（键=包根相对路径）。"""
    pkg = root / "qswarm"
    for rel, body in files.items():
        target = pkg / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return pkg


# ══ 词表与写口映射：对齐 SPEC-2 §6.1 / SPEC-5 §2.1 判据 2 ══════════

def test_wordlist_and_port_map_match_spec():
    """词表 15 表逐字对齐 §6.1 穷尽枚举 + M2 四表扩展（SPEC-6 §0.2；词表自身
    漂移也要红）；内核两表与 M2 考试域四表的圣化写口模块逐字钉死。"""
    assert WRITE_TABLES == (
        "ledger", "cells", "findings", "evidence_nodes", "votes",
        "verify_results", "clusters", "cluster_members", "tasks",
        "candidates", "runs", "agents", "hats", "exam_items", "exam_results",
    )
    assert EXPECTED_WRITE_PORTS["ledger"] == ("kernel/ledger.py",)
    assert EXPECTED_WRITE_PORTS["cells"] == ("kernel/cells.py",)
    assert EXPECTED_WRITE_PORTS["agents"] == ("exams/roster.py",)
    assert EXPECTED_WRITE_PORTS["hats"] == ("exams/roster.py",)
    assert EXPECTED_WRITE_PORTS["exam_items"] == ("exams/store.py",)
    assert EXPECTED_WRITE_PORTS["exam_results"] == ("exams/store.py",)


# ══ 运行时护栏：非唯一写口路径物理不可写（负向护栏）══════════════

def test_read_side_session_cannot_write_cells(tmp_path):
    """护栏内对 cells 的写一律 OperationalError(readonly database)——读侧会话
    写 cells 即失败；拒绝后表内容逐位不变（零半写态）。"""
    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        agg_cells(led, AS_OF)                       # 写口会话先落一次表
        before = led.conn.execute(CELLS_COLS).fetchall()
        assert before                               # 防空表上空转
        with read_only_guard(led):
            for sql in (
                "INSERT INTO cells SELECT * FROM cells",
                "UPDATE cells SET pos_w = 99.0",
                "DELETE FROM cells",
            ):
                with pytest.raises(sqlite3.OperationalError, match="readonly database"):
                    led.conn.execute(sql)
            after = led.conn.execute(CELLS_COLS).fetchall()
        assert after == before                      # 三连拒后逐位不变


def test_guard_blocks_append_ddl_and_write_port_itself(tmp_path):
    """护栏内连唯一写口本体也拒：KernelLedger.append、agg_cells 整表重写、DDL
    全部失败且零副作用（seq 不消耗、cells 不变、旁路表建不出来）。"""
    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        agg_cells(led, AS_OF)
        seq0, changes0 = led.latest_seq(), led.conn.total_changes
        with read_only_guard(led):
            with pytest.raises(sqlite3.OperationalError, match="readonly database"):
                led.append("deposit", DEPOSIT)
            with pytest.raises(sqlite3.OperationalError, match="readonly database"):
                agg_cells(led, AS_OF)
            with pytest.raises(sqlite3.OperationalError, match="readonly database"):
                led.conn.execute("CREATE TABLE rogue_probe (x INTEGER)")
            assert led.latest_seq() == seq0         # 拒绝不消耗 seq
        assert led.conn.total_changes == changes0   # 全程零写副作用


def test_guard_allows_reads_and_projection(tmp_path):
    """护栏内读路径完整可用：read() 与 view_cells 只读投影照常，且与无护栏重放
    逐位一致（读只需要读权限，不需要落盘）。"""
    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        expected_view = view_cells(led, AS_OF)
        expected_seq = [e.seq for e in led.read()]
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        with read_only_guard(led):
            guarded_view = view_cells(led, AS_OF)
            assert [e.seq for e in led.read()] == expected_seq
        assert diff_cells(guarded_view, expected_view) == []


def test_guard_release_on_exit_exception_and_nesting(tmp_path):
    """释放语义三路对账：正常退出恢复可写；with 内异常同样恢复；嵌套时内层退出
    仍受外层护栏保护、最外层退出才恢复（计数式可重入，D24 同型纪律）。"""
    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        # 嵌套：内层退出后外层仍只读；外层退出后写口恢复
        with read_only_guard(led):
            with read_only_guard(led):
                pass
            with pytest.raises(sqlite3.OperationalError, match="readonly database"):
                led.conn.execute("DELETE FROM cells")
        agg_cells(led, AS_OF)                       # 最外层退出 → 写口恢复
        # 异常路径：护栏退出仍执行（fail-closed 的反面=正常恢复）
        with pytest.raises(RuntimeError, match="boom"):
            with read_only_guard(led):
                raise RuntimeError("boom")
        agg_cells(led, AS_OF)                       # 仍可写


def test_guard_respects_preexisting_read_only_state(tmp_path):
    """进入时已只读的连接：护栏不越权解除（退出后仍只读）——谁翻转谁恢复。"""
    with KernelLedger(":memory:", clock=FixedClock(T0)) as led:
        led.conn.execute("PRAGMA query_only = 1")
        with read_only_guard(led):
            assert led.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        assert led.conn.execute("PRAGMA query_only").fetchone()[0] == 1
        led.conn.execute("PRAGMA query_only = 0")   # 恢复现场，teardown 可用


def test_guard_accepts_raw_connection_and_default_writer_unaffected(tmp_path):
    """裸连接同样可护栏；缺省（不装护栏）会话写口照常——M0 行为零影响。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE probe (x INTEGER)")
    with read_only_guard(conn):
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            conn.execute("INSERT INTO probe VALUES (1)")
    conn.execute("INSERT INTO probe VALUES (1)")    # 退出即恢复
    conn.commit()
    (count,) = conn.execute("SELECT COUNT(*) FROM probe").fetchone()
    assert count == 1

    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:   # 无护栏：写口全通
        assert led.append("dispatch", {"task_id": "T-1", "role": "scout", "node": "inj"}).seq == 4
        assert agg_cells(led, AS_OF).cells


def test_read_side_under_guard_equals_writer_view(tmp_path):
    """护栏内取到的只读投影与写口会话的落表视图逐位一致，且读侧没写过 cells
    （表保持为空）——读侧不落盘也不失真（§4.5 纪律 2/3 合取）。"""
    db = _deposit_ledger(tmp_path / "l.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        expected = agg_cells(led, AS_OF)
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        before = led.conn.execute(CELLS_COLS).fetchall()
        changes0 = led.conn.total_changes
        with read_only_guard(led):
            guarded = view_cells(led, AS_OF)
        after = led.conn.execute(CELLS_COLS).fetchall()
        assert after == before                        # 读侧零写副作用（逐位不变）
        assert led.conn.total_changes == changes0
    assert diff_cells(guarded, expected) == []


# ══ 静态审计：真包零违规 + 写口清单对账 §6.1 ═════════════════════

def test_real_tree_audit_zero_violations_and_inventory():
    """真包审计（全 11 表词表）：kernel/ 外零违规（SPEC-5 §2.1 判据 2 空输出）、
    strict 零漂移；写口清单恰含 M0 三处圣化写口（cells 清/插在 kernel/cells.py、
    ledger 插在 kernel/ledger.py；M1 新表落位后按 §6.1 映射自然扩容）。"""
    audit = audit_write_ports(Path(qswarm.__file__).parent)
    assert audit.violations == []
    assert audit.mismatches == []
    assert audit.scanned_files >= 20                # 全包扫描而非空转
    present = {(h.rel_path, h.stmt) for h in audit.inventory}
    assert {
        ("kernel/cells.py", "DELETE FROM cells"),
        ("kernel/cells.py", "INSERT INTO cells"),
        ("kernel/ledger.py", "INSERT INTO ledger"),
    } <= present


# ══ 静态审计：负向注入必被拦截（护栏可检不可骗）══════════════════

def test_scan_intercepts_injected_out_of_kernel_writer(tmp_path):
    """负向护栏（CI 静态层）：kernel/ 外注入写语句 → 审计必红、定位到文件:行；
    一键命令退出码 1。证明「非唯一写口路径写 cells 即失败」在静态层成立。"""
    pkg = _make_pkg(
        tmp_path,
        {
            "kernel/ledger.py": 'def go(c):\n    c.execute("INSERT INTO ledger (ts) VALUES (1)")\n',
            "simulator/rogue.py": "def go(c):\n    c.execute('INSERT INTO cells VALUES (1)')\n",
        },
    )
    audit = audit_write_ports(pkg)
    assert audit.ok is False
    assert [(h.rel_path, h.lineno, h.stmt) for h in audit.violations] == [
        ("simulator/rogue.py", 2, "INSERT INTO cells")
    ]
    assert [(h.rel_path, h.stmt) for h in audit.inventory] == [
        ("kernel/ledger.py", "INSERT INTO ledger")
    ]
    proc = subprocess.run(
        [sys.executable, "-m", "qswarm.kernel.write_guard", "--root", str(pkg)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert proc.returncode == 1
    assert "VIOLATION simulator/rogue.py:2  INSERT INTO cells" in proc.stdout
    assert "WRITE-PORT AUDIT FAIL violations=1" in proc.stdout


def test_strict_flags_write_outside_sanctioned_module(tmp_path):
    """负向护栏（strict 层）：kernel/ 内落在该表非圣化模块的写语句——非 strict
    放行（判据 2 口径：违规空即过），--strict 记 DRIFT 且退出码 2：内核内部也
    不许长出第二写口（§6.1 表→模块对账）。"""
    pkg = _make_pkg(
        tmp_path,
        {
            "kernel/cells.py": 'def w(c):\n    c.execute("DELETE FROM cells")\n',
            "kernel/promote_scan.py": 'def w(c):\n    c.execute("UPDATE cells SET status = 1")\n',
        },
    )
    audit = audit_write_ports(pkg)
    assert audit.ok and audit.violations == []
    assert [(h.rel_path, h.lineno, h.stmt) for h in audit.mismatches] == [
        ("kernel/promote_scan.py", 2, "UPDATE cells")
    ]
    plain = subprocess.run(
        [sys.executable, "-m", "qswarm.kernel.write_guard", "--root", str(pkg)],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    strict = subprocess.run(
        [sys.executable, "-m", "qswarm.kernel.write_guard", "--root", str(pkg), "--strict"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert plain.returncode == 0 and "WRITE-PORT AUDIT PASS" in plain.stdout
    assert strict.returncode == 2
    assert "DRIFT kernel/promote_scan.py:2  UPDATE cells" in strict.stdout
    assert "WRITE-PORT AUDIT FAIL violations=0 mismatches=1" in strict.stdout


def test_scan_skips_pycache_and_reports_line_numbers(tmp_path):
    """__pycache__ 不扫描（陈旧字节码不制造幽灵违规）；行号取写语句所在行。"""
    body = 'x = "DELETE FROM votes"\n'
    pkg = _make_pkg(tmp_path, {"simulator/real.py": "\n\n" + body})
    cache = pkg / "__pycache__"
    cache.mkdir()
    (cache / "stale.py").write_text(body, encoding="utf-8")
    audit = audit_write_ports(pkg)
    assert [(h.rel_path, h.lineno, h.stmt) for h in audit.violations] == [
        ("simulator/real.py", 3, "DELETE FROM votes")
    ]


def test_cli_one_click_pass_text_and_json():
    """一键命令（python -m 与 scripts/ 薄封装同判据）：真包文本 PASS / JSON
    ok=True violations=[] 退出码 0——终检复用入口的可检表达。"""
    mod = subprocess.run(
        [sys.executable, "-m", "qswarm.kernel.write_guard"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert mod.returncode == 0
    assert "WRITE-PORT AUDIT PASS" in mod.stdout
    assert "VIOLATION" not in mod.stdout
    # 写口清单行数与审计本体自洽（M1 起新内核模块按 §6.1 圣化表滚动扩清单——
    # 钉死具体行数会随兄弟模块落地过期而误红；M2 起清单含考试域 exams/ 端口，
    # 计数取全部 PORT 行。越界 0 条与清单↔模块圣化映射的一致性才是本判据的
    # 固有点，DECISIONS 2026-10-10 golden-m1 行 + M2 写口表扩展行）。
    from qswarm.kernel.write_guard import audit_write_ports as _audit

    assert mod.stdout.count("\n    PORT ") == len(
        _audit(Path(qswarm.__file__).parent).inventory
    )

    script = subprocess.run(
        [sys.executable, "scripts/audit_write_ports.py", "--json"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert script.returncode == 0
    payload = json.loads(script.stdout)
    assert payload["ok"] is True and payload["exit_code"] == 0
    assert payload["violations"] == [] and payload["mismatches"] == []
    # M0 三条原始写口恒在位（子集断言；清单总量随 §6.1 圣化模块落地增长），
    # 且 JSON 清单与审计本体逐条一致（同一扫描的两种出口）。
    from qswarm.kernel.write_guard import audit_write_ports as _audit2

    fresh = _audit2(Path(qswarm.__file__).parent).inventory
    assert [(h["path"], h["stmt"]) for h in payload["inventory"]] == [
        (h.rel_path, h.stmt) for h in fresh
    ]
    assert {("kernel/cells.py", "DELETE FROM cells"),
            ("kernel/cells.py", "INSERT INTO cells"),
            ("kernel/ledger.py", "INSERT INTO ledger")} <= {
        (h["path"], h["stmt"]) for h in payload["inventory"]
    }


# ══ 读侧约定：读走重放投影、不落盘（§4.5 纪律 2）══════════════════

def test_view_cells_read_side_projection_does_not_persist(tmp_path):
    """view_cells 与 agg_cells 同折叠同 status 重放（diff_cells 空），但零写副作用：
    已有物理 cells 表 → 行集全列逐位不变 + total_changes 零增量；空 cells 表
    （账本有事件、从未重写）→ 保持为空，无隐式落表。"""
    db = _deposit_ledger(tmp_path / "a.db")
    with KernelLedger(db, clock=FixedClock(T0)) as led:
        written = agg_cells(led, AS_OF)
        before = led.conn.execute(CELLS_COLS).fetchall()
        assert before
        changes0 = led.conn.total_changes
        readonly = view_cells(led, AS_OF)
        changes1 = led.conn.total_changes
        after = led.conn.execute(CELLS_COLS).fetchall()
    assert diff_cells(readonly, written) == []          # 与写路径视图逐位相等
    assert after == before and changes1 == changes0     # 不落盘

    db2 = _deposit_ledger(tmp_path / "b.db")
    with KernelLedger(db2, clock=FixedClock(T0)) as led:
        changes0 = led.conn.total_changes
        fresh = view_cells(led, AS_OF)
        changes1 = led.conn.total_changes
        assert led.conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0] == 0
        assert led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 3
    assert changes1 == changes0 and fresh.cells         # 空表保持为空、零副作用
    with KernelLedger(db2, clock=FixedClock(T0)) as led:
        assert diff_cells(view_cells(led, AS_OF), agg_cells(led, AS_OF)) == []
