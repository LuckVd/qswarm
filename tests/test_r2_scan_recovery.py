"""Same-run L1 recovery uses dispatch state, not mere task existence."""
from __future__ import annotations

import pytest

from qswarm.contracts import LedgerKind, load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger
import qswarm.kernel.loop as loop
import m3_harness
from m3_harness import ScriptedScoutAdapter, fake_rules_ok


@pytest.fixture()
def scan_env(tmp_path):
    seed = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "test.db", seed_db_path=seed,
                     clock=FixedClock(m3_harness.T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False
    with KernelLedger(tmp_path / "test.db", clock=FixedClock(m3_harness.T0)) as ledger:
        def scan(adapter=None):
            return loop.run_scan(
                ledger, repo_root=repo, mode="full", run_id="R-RECOVER",
                params=params, adapter=adapter or ScriptedScoutAdapter(),
                store_dir=tmp_path / "store", rules_runner=fake_rules_ok,
                promote=False,
            )
        yield ledger, scan


def deposits(ledger):
    return [e for e in ledger.read(kinds=(LedgerKind.DEPOSIT,))
            if e.run_id == "R-RECOVER"]


def assert_complete_and_idempotent(ledger, scan):
    report = scan()
    assert report.tasks_done > 0
    assert {r[0] for r in ledger.conn.execute("SELECT state FROM tasks")} == {"done"}
    count = len(deposits(ledger))
    assert count > 0
    seq = ledger.latest_seq()
    adapter = ScriptedScoutAdapter()
    again = scan(adapter)
    assert again.tasks_done == again.tasks_dispatched == again.deposits_written == 0
    assert adapter.calls == []
    assert len(deposits(ledger)) == count
    assert ledger.latest_seq() == seq


def test_queued_task_recovers_after_empty_pool(scan_env, monkeypatch):
    ledger, scan = scan_env
    with monkeypatch.context() as patch:
        patch.setattr(loop, "active_scouts", lambda *a, **kw: [])
        report = scan()
    assert report.tasks_dispatched == 0
    assert {r[0] for r in ledger.conn.execute("SELECT state FROM tasks")} == {"queued"}
    assert_complete_and_idempotent(ledger, scan)


def test_queued_task_recovers_after_dispatch_interruption(scan_env, monkeypatch):
    ledger, scan = scan_env
    def interrupted(*args, **kwargs):
        raise RuntimeError("before-dispatch")
    with monkeypatch.context() as patch:
        patch.setattr(loop, "dispatch", interrupted)
        with pytest.raises(RuntimeError, match="before-dispatch"):
            scan()
    assert_complete_and_idempotent(ledger, scan)


@pytest.mark.parametrize("attempt", [1, 2])
def test_submit_deposit_rollback_recovers_remaining_attempt(scan_env, monkeypatch, attempt):
    ledger, scan = scan_env
    original = ledger.append
    def interrupted(kind, *args, **kwargs):
        if str(kind) == "deposit":
            raise RuntimeError("deposit-interruption")
        return original(kind, *args, **kwargs)
    class RetryOnce(ScriptedScoutAdapter):
        failed = False
        def complete(self, **kwargs):
            if attempt == 2 and not self.failed:
                self.failed = True
                return "invalid-json"
            return super().complete(**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", interrupted)
        with pytest.raises(RuntimeError, match="deposit-interruption"):
            scan(RetryOnce())
    assert ledger.conn.execute("SELECT state, attempts FROM tasks").fetchall() == [
        ("dispatched", attempt)]
    assert deposits(ledger) == []
    assert ledger.conn.execute("SELECT SUM(triaged) FROM candidates").fetchone()[0] == 0
    assert_complete_and_idempotent(ledger, scan)
    assert ledger.conn.execute("SELECT attempts FROM tasks ORDER BY created_at, rowid LIMIT 1").fetchone()[0] == attempt


def test_resumed_second_attempt_cannot_gain_extra_retry(scan_env, monkeypatch):
    ledger, scan = scan_env
    original = ledger.append
    def interrupted(kind, *args, **kwargs):
        if str(kind) == "deposit":
            raise RuntimeError("deposit-interruption")
        return original(kind, *args, **kwargs)
    class RetryOnce(ScriptedScoutAdapter):
        failed = False
        def complete(self, **kwargs):
            if not self.failed:
                self.failed = True
                return "invalid-json"
            return super().complete(**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ledger, "append", interrupted)
        with pytest.raises(RuntimeError):
            scan(RetryOnce())
    class Bad:
        calls = 0
        def complete(self, **kwargs):
            self.calls += 1
            return "invalid-json"
    bad = Bad()
    report = scan(bad)
    rows = ledger.conn.execute("SELECT state, attempts FROM tasks").fetchall()
    assert all(state == "failed" and attempts == 2 for state, attempts in rows)
    assert bad.calls == 1 + 2 * (len(rows) - 1)
    assert report.tasks_failed == len(rows)
    assert deposits(ledger) == []
    # Terminal failure must remain visible on rerun; do not invent failed->dispatched.
    again = scan()
    assert again.tasks_failed == len(rows)
    assert again.tasks_dispatched == again.tasks_done == 0
    assert again.to_dict()["tasks"]["recovery_blocked"] == len(rows)
    assert len(again.recovery_notes) == len(rows)
    assert all("new run" in note for note in again.recovery_notes)


def test_completed_prefix_is_not_called_or_deposited_again(scan_env):
    ledger, scan = scan_env
    class Interrupted(ScriptedScoutAdapter):
        count = 0
        def complete(self, **kwargs):
            self.count += 1
            if self.count == 2:
                raise KeyboardInterrupt("process-interruption")
            return super().complete(**kwargs)
    with pytest.raises(KeyboardInterrupt):
        scan(Interrupted())
    assert ledger.conn.execute("SELECT state FROM tasks ORDER BY rowid").fetchall() == [
        ("done",), ("dispatched",)]
    before = deposits(ledger)
    assert len(before) == 1
    report = scan()
    total = ledger.conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert report.tasks_done == total - 1
    assert len(deposits(ledger)) == total
    assert deposits(ledger)[0] == before[0]
    seq = ledger.latest_seq()
    scan()
    assert ledger.latest_seq() == seq


@pytest.mark.parametrize("attempt", [1, 2])
def test_inactive_assignee_recovery_uses_remaining_dispatch_budget(scan_env, monkeypatch, attempt):
    ledger, scan = scan_env
    class Interrupted(ScriptedScoutAdapter):
        count = 0
        def complete(self, **kwargs):
            self.count += 1
            if attempt == 2 and self.count == 1:
                return "invalid-json"
            raise KeyboardInterrupt("process-interruption")
    with pytest.raises(KeyboardInterrupt):
        scan(Interrupted())
    task, old_agent = ledger.conn.execute("SELECT task_id, assignee FROM tasks").fetchone()
    pool = [pair for pair in loop.active_scouts(ledger) if pair[1] != old_agent]
    assert pool
    with monkeypatch.context() as patch:
        patch.setattr(loop, "active_scouts", lambda *a, **kw: pool)
        report = scan()
    state, attempts, agent = ledger.conn.execute(
        "SELECT state, attempts, assignee FROM tasks WHERE task_id = ?", (task,)
    ).fetchone()
    assert attempts == 2
    if attempt == 1:
        assert state == "done" and agent != old_agent
        assert report.tasks_failed == 0
    else:
        assert state == "failed" and agent == old_agent
        assert report.tasks_failed == report.l1_infra_fail == 1
