"""kernel.ledger.transaction()：多写原子上下文（SPEC-2 §7.1；SPEC-5 §1.5 门禁）。

钉住的行为（DECISIONS 2026-10-10 D24）：

- BEGIN IMMEDIATE 显式开事务：正常退出 COMMIT、异常 ROLLBACK，回滚后账本与
  表零残留（「不存在半写态」，§4.5 纪律 1）；
- 上下文内 append() 不再逐条 commit（conn.in_transaction 保持为真），由最外层
  边界统一提交；
- 无上下文时 append() 逐条提交，与 M0 行为逐位一致；
- 可重入（计数式嵌套）：内层不再 BEGIN/COMMIT，仅登记深度；内层异常若被外层
  捕获，不触发回滚（最外层边界统一裁决）；异常穿透到最外层才整体 ROLLBACK。
"""

import sqlite3

import pytest

from qswarm.contracts import DepositOrigin, DepositPayload, Role, SignalTier
from qswarm.kernel import KernelLedger
from qswarm.kernel.ledger import transaction

TS = "2026-10-10T00:00:00Z"


def _deposit(led: KernelLedger, agent: str = "a1") -> None:
    led.append(
        "deposit",
        DepositPayload(
            finding=None,
            cluster=None,
            agent=agent,
            role=Role.AUDITOR,
            taxonomy_path=["injection", "sqli"],
            signal_tier=SignalTier.S2,
            direction=1,
            weight=0.5,
            origin=DepositOrigin.PROD,
        ),
    )


def test_transaction_commits_or_rolls_back_atomically():
    """BEGIN IMMEDIATE：正常退出 COMMIT、异常 ROLLBACK，账本与表零残留。"""
    led = KernelLedger(":memory:")
    try:
        # 正常路径：上下文内的表行 + 账本事件一并提交。
        with transaction(led) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, mode, repo_url, budget_cents,"
                " spent_cents, status, started_at)"
                " VALUES ('R-TX-1', 'full', 'golden://t', 600, 0, 'running', ?)",
                (TS,),
            )
            _deposit(led)
        assert led.conn.in_transaction is False
        n_runs = led.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        n_events = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        assert (n_runs, n_events) == (1, 1)

        # 异常路径：整体 ROLLBACK，runs 与 ledger 零残留。
        with pytest.raises(RuntimeError, match="boom"):
            with transaction(led) as conn:
                conn.execute(
                    "INSERT INTO runs (run_id, mode, repo_url, budget_cents,"
                    " spent_cents, status, started_at)"
                    " VALUES ('R-TX-2', 'pr', 'golden://t', 100, 0, 'running', ?)",
                    (TS,),
                )
                _deposit(led)
                raise RuntimeError("boom")
        assert led.conn.in_transaction is False
        rows = led.conn.execute(
            "SELECT COUNT(*) FROM runs WHERE run_id = 'R-TX-2'"
        ).fetchone()[0]
        events = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]
        assert (rows, events) == (0, 1)  # R-TX-2 整体回滚；只剩首事务的 1 行 1 事件
    finally:
        led.close()


def test_append_inside_transaction_defers_commit():
    """上下文内 append 不逐条 commit：in_transaction 保持为真，边界统一提交。"""
    led = KernelLedger(":memory:")
    try:
        with transaction(led):
            assert led.conn.in_transaction is True
            _deposit(led)
            # append 返回后仍在同一事务内（若逐条提交则 in_transaction 已翻 False）。
            assert led.conn.in_transaction is True
            (seq,) = led.conn.execute(
                "SELECT seq FROM ledger ORDER BY seq DESC LIMIT 1"
            ).fetchone()
            assert seq >= 1
        # 边界退出后统一提交可见。
        assert led.conn.in_transaction is False
        (n,) = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert n == 1
    finally:
        led.close()


def test_append_without_context_unchanged():
    """无上下文：append 逐条提交，行为与 M0 逐位一致。"""
    led = KernelLedger(":memory:")
    try:
        assert led.conn.in_transaction is False
        _deposit(led)
        # 返回即已提交（无残留事务）。
        assert led.conn.in_transaction is False
        (n,) = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert n == 1
    finally:
        led.close()


def test_transaction_nested_reentrant_inner_defers_to_outer():
    """D24 计数式嵌套：内层不 BEGIN/COMMIT；内层异常被外层捕获时不回滚。"""
    led = KernelLedger(":memory:")
    try:
        with transaction(led):
            _deposit(led)
            try:
                with transaction(led):
                    _deposit(led, "a2")
                    raise ValueError("inner")
            except ValueError:
                pass  # 内层异常被外层捕获：不触发 ROLLBACK（最外层边界统一裁决）
            _deposit(led, "a3")
        (n,) = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert n == 3  # 内层异常零回滚，全部由最外层提交

        # 异常穿透到最外层才整体 ROLLBACK。
        with pytest.raises(ValueError, match="outer"):
            with transaction(led):
                _deposit(led, "a4")
                with transaction(led):
                    _deposit(led, "a5")
                raise ValueError("outer")
        (n,) = led.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()
        assert n == 3  # a4/a5 随最外层回滚
        assert led.conn.in_transaction is False
    finally:
        led.close()


def test_transaction_writes_visible_inside_and_isolated_outside():
    """上下文内同连接可见未提交写入；连接级隔离由 BEGIN IMMEDIATE 保证。"""
    led = KernelLedger(":memory:")
    try:
        with transaction(led) as conn:
            conn.execute(
                "INSERT INTO runs (run_id, mode, repo_url, budget_cents,"
                " spent_cents, status, started_at)"
                " VALUES ('R-TX-3', 'fast', 'golden://t', 2, 0, 'running', ?)",
                (TS,),
            )
            # 同事务内可见（尚未提交）。
            (n,) = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
            assert n == 1
            assert isinstance(conn, sqlite3.Connection) and conn.in_transaction
        (n,) = led.conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        assert n == 1
    finally:
        led.close()
