"""Offline sandbox hardening: no daemon, image, network or untrusted execution."""
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from qswarm.sandbox.config import SandboxConfig
import qswarm.sandbox.runner as R


@pytest.mark.parametrize("user", ["", " ", "root", "0", "0:0", "root:1000", "00:1000"])
def test_container_rejects_privileged_or_empty_user(user):
    with pytest.raises(ValueError, match="user"):
        SandboxConfig(tier="container", user=user)


def test_container_rejects_writable_root():
    with pytest.raises(ValueError, match="read_only_root"):
        SandboxConfig(tier="container", read_only_root=False)


@pytest.fixture()
def container(tmp_path, monkeypatch):
    cfg = SandboxConfig(tier="container", env_cache_dir=str(tmp_path / "env"))
    runner = R.ContainerRunner(cfg)
    monkeypatch.setattr(runner, "_probe_docker", lambda: True)
    monkeypatch.setattr(R, "build_container_env", lambda *a, **kw:
                        SimpleNamespace(env_key="test", env_base="local-only"))
    repo = tmp_path / "repo"
    repo.mkdir(mode=0o755)
    work = tmp_path / "temporary"
    def mkdtemp(**kw):
        work.mkdir()
        return str(work)
    monkeypatch.setattr(R.tempfile, "mkdtemp", mkdtemp)
    commands = []
    def run(cmd, **kw):
        commands.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(R.subprocess, "run", run)
    monkeypatch.setattr(R, "_run_capped", lambda cmd, **kw: (0, "ok", "", False, False))
    return runner, repo, work, commands


def test_container_hardening_flags_and_cleanup_bound(container, monkeypatch):
    runner, repo, work, commands = container
    execution = []
    def capped(cmd, **kw):
        execution.extend(cmd)
        return 0, "ok", "", False, False
    monkeypatch.setattr(R, "_run_capped", capped)
    result = runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert result.ok
    assert "--cap-drop" in execution and execution[execution.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in execution
    assert execution[execution.index("--pids-limit") + 1] == str(runner._cfg.nproc)
    assert commands[-1][0][:3] == ["docker", "rm", "-f"]
    assert 0 < commands[-1][1]["timeout"] <= R._CLEANUP_TIMEOUT_S
    assert not work.exists()


@pytest.mark.parametrize("failure", ["timeout", "nonzero", "oserror"])
def test_container_cleanup_failure_is_not_success(container, monkeypatch, failure):
    runner, repo, work, commands = container
    def cleanup(cmd, **kw):
        assert kw.get("timeout", 0) > 0
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        if failure == "oserror":
            raise OSError("cleanup unavailable")
        return subprocess.CompletedProcess(cmd, 1, "", "cannot remove")
    monkeypatch.setattr(R.subprocess, "run", cleanup)
    result = runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not result.ok
    assert result.error_kind == "env_build_fail"
    assert result.mitigations["cleanup_failed"]
    assert not work.exists()


def test_container_cleans_directory_and_container_on_execution_exception(container, monkeypatch):
    runner, repo, work, commands = container
    def interrupted(*a, **kw):
        raise RuntimeError("execution interrupted")
    monkeypatch.setattr(R, "_run_capped", interrupted)
    with pytest.raises(RuntimeError, match="execution interrupted"):
        runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert commands[-1][0][:3] == ["docker", "rm", "-f"]
    assert not work.exists()


def test_container_cleans_directory_on_staging_exception(container, monkeypatch):
    runner, repo, work, commands = container
    repo.chmod(0o700)
    def interrupted(*a, **kw):
        raise RuntimeError("staging interrupted")
    monkeypatch.setattr(R, "safe_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="staging interrupted"):
        runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not work.exists()


def test_container_temp_cleanup_failure_is_not_success(container, monkeypatch):
    runner, repo, work, commands = container
    monkeypatch.setattr(R, "_rmtree_bounded", lambda path: False)
    result = runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not result.ok and result.error_kind == "env_build_fail"
    assert result.workdir == str(work)
    assert result.mitigations["cleanup_failed"]


@pytest.mark.parametrize("failure", ["setsid", "network", "rlimit", "setgroups", "setgid", "setuid"])
def test_actual_preexec_failure_is_fail_closed(monkeypatch, failure):
    # Invoke the hook with all privileged operations mocked: never change this process.
    runner = R.ProcessRunner(SandboxConfig(tier="process", process_require_isolation=True))
    monkeypatch.setattr(R.os, "setsid", lambda: None)
    monkeypatch.setattr(R.os, "setgroups", lambda groups: None)
    monkeypatch.setattr(R.os, "setgid", lambda gid: None)
    monkeypatch.setattr(R.os, "setuid", lambda uid: None)
    monkeypatch.setattr(R.os, "write", lambda *a: 1)
    monkeypatch.setattr(R.resource, "setrlimit", lambda *a: None)
    monkeypatch.setattr(R.signal, "signal", lambda *a: None)
    import ctypes
    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw:
                        SimpleNamespace(unshare=lambda flag: -1 if failure == "network" else 0))
    def denied(*a):
        raise OSError("isolation denied")
    if failure == "rlimit":
        monkeypatch.setattr(R.resource, "setrlimit", denied)
    elif failure != "network":
        monkeypatch.setattr(R.os, failure, denied)
    with pytest.raises((OSError, R.UntrustedExecutionError)):
        runner._make_preexec((65534, 65534), 123)()


def test_root_without_drop_target_is_refused_before_build(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = R.ProcessRunner(SandboxConfig(trusted_roots=(str(tmp_path),)))
    monkeypatch.setattr(runner, "_probe_net", lambda: True)
    monkeypatch.setattr(runner, "_probe_user", lambda: None)
    monkeypatch.setattr(R.os, "geteuid", lambda: 0)
    def forbidden(*a, **kw):
        pytest.fail("environment build before privilege guard")
    monkeypatch.setattr(R, "build_process_env", forbidden)
    with pytest.raises(R.UntrustedExecutionError, match="privilege|降权"):
        runner.execute("print(1)", repo=repo, deps=[], seed="x")


@pytest.fixture()
def process(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = R.ProcessRunner(SandboxConfig(trusted_roots=(str(tmp_path),)))
    monkeypatch.setattr(runner, "_probe_net", lambda: True)
    monkeypatch.setattr(runner, "_probe_user", lambda: (65534, 65534))
    monkeypatch.setattr(R, "build_process_env", lambda *a, **kw:
                        SimpleNamespace(env_key="test", env_base="process:test", sitedeps=None))
    work = tmp_path / "temporary"
    def mkdtemp(**kw):
        work.mkdir()
        return str(work)
    monkeypatch.setattr(R.tempfile, "mkdtemp", mkdtemp)
    return runner, repo, work


def test_preexec_failure_never_executes_poc_and_cleans_temp(process, monkeypatch, tmp_path):
    runner, repo, work = process
    marker = tmp_path / "must-not-run"
    def failed_setup():
        raise OSError("mock actual isolation failure")
    monkeypatch.setattr(runner, "_make_preexec", lambda *a: failed_setup)
    result = runner.execute(f"open({str(marker)!r}, 'w').write('unsafe')",
                            repo=repo, deps=[], seed="x")
    assert not marker.exists()
    assert not result.ok and result.error_kind == "env_build_fail"
    assert "isolation setup failed" in result.stderr
    assert not work.exists()


def test_process_missing_actual_isolation_ack_is_not_success(process, monkeypatch):
    runner, repo, work = process
    # Probe success + exit zero do not substitute for actual preexec confirmation.
    monkeypatch.setattr(R, "_run_capped", lambda *a, **kw: (0, "", "", False, False))
    result = runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not result.ok and result.error_kind == "env_build_fail"
    assert "isolation not confirmed" in result.stderr
    assert not work.exists()


def test_process_copy_exception_cleans_temp(process, monkeypatch):
    runner, repo, work = process
    def interrupted(*a, **kw):
        raise RuntimeError("copy interrupted")
    monkeypatch.setattr(R, "safe_snapshot", interrupted)
    with pytest.raises(RuntimeError, match="copy interrupted"):
        runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not work.exists()


def test_process_preparation_failure_cannot_drop_privilege_requirement(process, monkeypatch):
    runner, repo, work = process
    original_chmod = R.os.chmod
    def denied(path, *a, **kw):
        if Path(path) == work:
            raise PermissionError("chmod denied")
        return original_chmod(path, *a, **kw)
    monkeypatch.setattr(R.os, "chmod", denied)
    with pytest.raises(R.UntrustedExecutionError, match="权限准备"):
        runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert not work.exists()
