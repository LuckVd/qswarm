"""Safe snapshot regressions; synthetic files only, no Docker or target execution."""
import importlib
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import qswarm.sandbox.runner as R
from qswarm.sandbox.config import SandboxConfig


def api():
    return importlib.import_module("qswarm.sandbox.snapshot")


@pytest.fixture()
def tree(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "safe.py").write_text("safe = 1\n")
    return root, tmp_path / "copy"


def test_snapshot_stable_digest_permissions_and_exclusions(tree):
    root, dest = tree
    for name in (".git", ".venv", "__pycache__", "node_modules"):
        (root / name).mkdir()
        (root / name / "ignored").symlink_to("/not-followed")
    snap = api().safe_snapshot(root, dest)
    assert (dest / "src" / "safe.py").read_text() == "safe = 1\n"
    assert sorted(p.name for p in dest.iterdir()) == ["src"]
    assert (dest.stat().st_mode & 0o777) == 0o755
    assert ((dest / "src" / "safe.py").stat().st_mode & 0o777) == 0o444
    again = api().safe_snapshot(root, dest.with_name("copy2"))
    assert snap.digest == again.digest
    assert snap.file_count == 1 and snap.total_bytes == len(b"safe = 1\n")
    (root / "src" / "safe.py").write_text("safe = 2\n")
    assert api().safe_snapshot(root, dest.with_name("copy3")).digest != snap.digest
    assert (dest / "src" / "safe.py").read_text() == "safe = 1\n"


@pytest.mark.parametrize("kind", ["file_link", "dir_link", "inside_link", "hardlink", "fifo"])
def test_snapshot_rejects_links_and_special_files(tree, tmp_path, kind):
    root, dest = tree
    outside = tmp_path / "outside-secret"
    outside.write_text("must not copy")
    if kind == "file_link":
        (root / "bad").symlink_to(outside)
    elif kind == "dir_link":
        (root / "bad").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "inside_link":
        (root / "bad").symlink_to(root / "src" / "safe.py")
    elif kind == "hardlink":
        os.link(outside, root / "bad")
    else:
        os.mkfifo(root / "bad")
    with pytest.raises(api().SnapshotError):
        api().safe_snapshot(root, dest)
    assert not (dest / "bad").exists()


def test_source_ancestor_symlink_is_not_followed(tree, tmp_path):
    root, dest = tree
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    with pytest.raises(api().SnapshotError):
        api().safe_snapshot(alias / "src", dest)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_replacement_between_stat_and_open_cannot_follow_link(tree, tmp_path, monkeypatch, kind):
    root, dest = tree
    mod = api()
    victim = root / ("victim" if kind == "directory" else "victim.py")
    if kind == "directory":
        victim.mkdir()
        (victim / "safe").write_text("safe")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret").write_text("must not copy")
    else:
        victim.write_text("safe")
        outside = tmp_path / "outside"
        outside.write_text("must not copy")
    original = mod.os.open
    replaced = False
    def racing_open(path, flags, *args, **kwargs):
        nonlocal replaced
        if path == victim.name and kwargs.get("dir_fd") is not None and not replaced:
            replaced = True
            victim.rename(victim.with_name("old-victim"))
            victim.symlink_to(outside, target_is_directory=(kind == "directory"))
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(mod.os, "open", racing_open)
    with pytest.raises(mod.SnapshotError):
        mod.safe_snapshot(root, dest)
    assert replaced
    if kind == "directory":
        assert not (dest / victim.name / "secret").exists()


@pytest.mark.parametrize("overrides", [
    {"max_files": 1}, {"max_file_bytes": 2}, {"max_total_bytes": 2},
    {"max_entries": 1}, {"max_depth": 1},
])
def test_snapshot_resource_limits(tree, overrides):
    root, dest = tree
    (root / "src" / "second.py").write_text("second")
    mod = api()
    with pytest.raises(mod.SnapshotError, match="limit"):
        mod.safe_snapshot(root, dest, limits=mod.SnapshotLimits(**overrides))


def test_snapshot_wall_clock_limit(tree, monkeypatch):
    root, dest = tree
    mod = api()
    ticks = iter([0.0, 2.0])
    monkeypatch.setattr(mod.time, "monotonic", lambda: next(ticks, 2.0))
    with pytest.raises(mod.SnapshotError, match="limit"):
        mod.safe_snapshot(root, dest, limits=mod.SnapshotLimits(timeout_s=1))


def test_snapshot_detects_mutation_during_file_read(tree, monkeypatch):
    root, dest = tree
    mod = api()
    original = mod.os.read
    mutated = False
    def racing_read(fd, size):
        nonlocal mutated
        data = original(fd, size)
        if data and not mutated:
            mutated = True
            (root / "src" / "safe.py").write_text("changed during read")
        return data
    monkeypatch.setattr(mod.os, "read", racing_read)
    with pytest.raises(mod.SnapshotError):
        mod.safe_snapshot(root, dest)


def test_file_replaced_by_fifo_uses_nonblocking_open(tree, monkeypatch):
    root, dest = tree
    mod = api()
    original = mod.os.open
    replaced = False
    def racing_open(path, flags, *a, **kw):
        nonlocal replaced
        if path == "safe.py" and not replaced:
            replaced = True
            assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
            victim = root / "src" / "safe.py"
            victim.rename(victim.with_name("old.py"))
            os.mkfifo(victim)
        return original(path, flags, *a, **kw)
    monkeypatch.setattr(mod.os, "open", racing_open)
    with pytest.raises(mod.SnapshotError):
        mod.safe_snapshot(root, dest)
    assert replaced


def test_open_directory_replacement_cannot_rebase_recursive_copy(tree, tmp_path, monkeypatch):
    root, dest = tree
    mod = api()
    original = mod.os.open
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("must not copy")
    replaced = False
    def racing_open(path, flags, *a, **kw):
        nonlocal replaced
        fd = original(path, flags, *a, **kw)
        if path == "src" and flags & os.O_DIRECTORY and not replaced:
            replaced = True
            (root / "src").rename(root / "old-src")
            (root / "src").symlink_to(outside, target_is_directory=True)
        return fd
    monkeypatch.setattr(mod.os, "open", racing_open)
    with pytest.raises(mod.SnapshotError):
        mod.safe_snapshot(root, dest)
    assert replaced
    assert not (dest / "src" / "secret").exists()


def test_empty_directory_and_path_changes_affect_manifest(tree):
    root, dest = tree
    mod = api()
    first = mod.safe_snapshot(root, dest)
    (root / "empty").mkdir()
    second = mod.safe_snapshot(root, dest.with_name("copy2"))
    assert second.digest != first.digest
    (root / "src" / "safe.py").rename(root / "src" / "renamed.py")
    third = mod.safe_snapshot(root, dest.with_name("copy3"))
    assert third.digest != second.digest


def test_snapshot_rejects_destination_in_source(tree):
    root, _ = tree
    with pytest.raises(api().SnapshotError):
        api().safe_snapshot(root, root / "copy")


def test_container_always_mounts_frozen_copy(tree, tmp_path, monkeypatch):
    root, _ = tree
    root.chmod(0o755)
    runner = R.ContainerRunner(SandboxConfig(tier="container"))
    monkeypatch.setattr(runner, "_probe_docker", lambda: True)
    monkeypatch.setattr(R, "build_container_env", lambda *a, **kw:
                        SimpleNamespace(env_key="test", env_base="local-only"))
    work = tmp_path / "work"
    def mkdtemp(**kw):
        work.mkdir()
        return str(work)
    monkeypatch.setattr(R.tempfile, "mkdtemp", mkdtemp)
    monkeypatch.setattr(R.subprocess, "run", lambda cmd, **kw:
                        subprocess.CompletedProcess(cmd, 0, "", ""))
    def fake_execution(cmd, **kwargs):
        mount = next(x for x in cmd if x.endswith(":/repo:ro"))
        mounted = Path(mount.removesuffix(":/repo:ro"))
        assert mounted == work / "repo" and mounted != root
        (root / "src" / "safe.py").write_text("live mutation")
        assert (mounted / "src" / "safe.py").read_text() == "safe = 1\n"
        return 0, "", "", False, False
    monkeypatch.setattr(R, "_run_capped", fake_execution)
    result = runner.execute("print(1)", repo=root, deps=[], seed="s")
    assert result.ok and result.mitigations["snapshot_digest"]
    assert not work.exists()


@pytest.mark.parametrize("tier", ["container", "process"])
def test_runners_reject_symlink_before_execution_and_clean(tree, tmp_path, monkeypatch, tier):
    root, _ = tree
    (root / "secret").symlink_to(tmp_path / "outside")
    cfg = SandboxConfig(tier=tier, trusted_roots=(str(root),))
    runner = R.make_runner(cfg)
    env = SimpleNamespace(env_key="test", env_base="local-only", sitedeps=None)
    if tier == "container":
        monkeypatch.setattr(runner, "_probe_docker", lambda: True)
        monkeypatch.setattr(R, "build_container_env", lambda *a, **kw: env)
    else:
        monkeypatch.setattr(runner, "_probe_net", lambda: True)
        monkeypatch.setattr(runner, "_probe_user", lambda: (65534, 65534))
        monkeypatch.setattr(R, "build_process_env", lambda *a, **kw: env)
    work = tmp_path / "work"
    def mkdtemp(**kw):
        work.mkdir()
        return str(work)
    monkeypatch.setattr(R.tempfile, "mkdtemp", mkdtemp)
    def forbidden(*a, **kw):
        pytest.fail("unsafe snapshot reached execution")
    monkeypatch.setattr(R, "_run_capped", forbidden)
    result = runner.execute("print(1)", repo=root, deps=[], seed="s")
    assert not result.ok and result.error_kind == "env_build_fail"
    assert not work.exists()
