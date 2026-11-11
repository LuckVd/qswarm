"""Bounded, no-follow repository staging for both sandbox tiers (POSIX only).

The caller supplies a fresh destination beneath its private temporary directory and
owns cleanup, including partial copies on failure. Source ancestors and descendants
are opened component-by-component with dirfds; no source path is followed after an
entry check. All symlinks, hard-linked files, special files and cross-device trees
are rejected. Excluded names are never opened.

The manifest identifies the copied bytes, not a commit, root cause, or atomic
filesystem snapshot. Concurrent changes detected during copying fail closed; this
is not a filesystem transaction. Deadline checks bound cooperative local I/O, not
an uninterruptible kernel/filesystem operation. Same-UID/root tampering with the
caller's private destination or privileged mount manipulation is outside scope.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

__all__ = ["SnapshotError", "SnapshotLimits", "SnapshotManifest", "safe_snapshot"]

_EXCLUDED = frozenset({".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
                       "node_modules", ".mypy_cache", ".pytest_cache"})
_CHUNK = 64 * 1024


class SnapshotError(ValueError):
    """Unsafe, changing or oversized source; never execute a partial snapshot."""


@dataclass(frozen=True)
class SnapshotLimits:
    max_files: int = 20_000
    max_entries: int = 100_000
    max_depth: int = 64
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    timeout_s: float = 30.0

    def __post_init__(self):
        for name in ("max_files", "max_entries", "max_depth", "max_file_bytes", "max_total_bytes"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(self.timeout_s) or self.timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")


@dataclass(frozen=True)
class SnapshotManifest:
    digest: str
    file_count: int
    total_bytes: int
    # (relative POSIX path, kind, size, sha256); directories include empty ones.
    entries: tuple[tuple[str, str, int, str], ...]
    version: str = "qswarm-snapshot-v1"


def _identity(info):
    return info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)


def _revision(info):
    return (_identity(info), info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)


@contextmanager
def _directory(path: Path):
    """Open even ancestors without following links (not merely the final basename)."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open("/", flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def safe_snapshot(source: str | Path, destination: str | Path, *,
                  limits: SnapshotLimits | None = None) -> SnapshotManifest:
    """Copy regular files through pinned dirfds; return a stable content manifest.

    ``source`` must already be a resolved/authorized root at the runner boundary.
    This function itself does not resolve symlinks. Destination must not exist.
    On any error the caller must discard/clean the entire staging directory.
    """
    limits = limits or SnapshotLimits()
    source = Path(os.path.abspath(source))
    destination = Path(os.path.abspath(destination))
    if source == destination or source in destination.parents:
        raise SnapshotError("snapshot destination must be outside source")
    deadline = time.monotonic() + limits.timeout_s
    file_count = total_bytes = entry_count = 0
    entries: list[tuple[str, str, int, str]] = []
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC

    def check():
        if time.monotonic() > deadline:
            raise SnapshotError("snapshot time limit exceeded")

    def unchanged(fd, before, parent=None, name=None):
        if _revision(os.fstat(fd)) != _revision(before):
            raise SnapshotError("source changed while snapshotting")
        if parent is not None:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _revision(current) != _revision(before):
                raise SnapshotError("source entry replaced while snapshotting")

    def walk(src_fd, dst_fd, rel, depth, device):
        nonlocal file_count, total_bytes, entry_count
        check()
        before_dir = os.fstat(src_fd)
        names = []
        with os.scandir(src_fd) as items:
            for item in items:
                check()
                entry_count += 1
                if entry_count > limits.max_entries:
                    raise SnapshotError("snapshot entry limit exceeded")
                if item.name not in _EXCLUDED:
                    names.append(item.name)
        for name in sorted(names):
            check()
            if depth + 1 > limits.max_depth:
                raise SnapshotError("snapshot depth limit exceeded")
            path = f"{rel}/{name}" if rel else name
            initial = os.stat(name, dir_fd=src_fd, follow_symlinks=False)
            if initial.st_dev != device:
                raise SnapshotError(f"cross-device source rejected: {path}")
            if stat.S_ISDIR(initial.st_mode):
                child = os.open(name, dir_flags, dir_fd=src_fd)
                try:
                    opened = os.fstat(child)
                    if _revision(opened) != _revision(initial):
                        raise SnapshotError(f"directory replaced: {path}")
                    os.mkdir(name, mode=0o755, dir_fd=dst_fd)
                    out_dir = os.open(name, dir_flags, dir_fd=dst_fd)
                    try:
                        os.fchmod(out_dir, 0o755)
                        entries.append((path, "directory", 0, ""))
                        walk(child, out_dir, path, depth + 1, device)
                        unchanged(child, opened, src_fd, name)
                    finally:
                        os.close(out_dir)
                finally:
                    os.close(child)
            elif stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1:
                file_count += 1
                if file_count > limits.max_files:
                    raise SnapshotError("snapshot file count limit exceeded")
                # O_NONBLOCK prevents a swapped-in FIFO from blocking open; fstat
                # still rejects anything except the original single-link regular file.
                src = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                              dir_fd=src_fd)
                try:
                    opened = os.fstat(src)
                    if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                            or _revision(opened) != _revision(initial)):
                        raise SnapshotError(f"file replaced: {path}")
                    if opened.st_size > limits.max_file_bytes:
                        raise SnapshotError("snapshot file size limit exceeded")
                    if total_bytes + opened.st_size > limits.max_total_bytes:
                        raise SnapshotError("snapshot total size limit exceeded")
                    out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                                  | os.O_CLOEXEC, 0o444, dir_fd=dst_fd)
                    try:
                        hasher = hashlib.sha256()
                        size = 0
                        while True:
                            check()
                            data = os.read(src, _CHUNK)
                            if not data:
                                break
                            size += len(data)
                            total_bytes += len(data)
                            if size > limits.max_file_bytes or total_bytes > limits.max_total_bytes:
                                raise SnapshotError("snapshot byte limit exceeded")
                            hasher.update(data)
                            pending = memoryview(data)
                            while pending:
                                check()
                                written = os.write(out, pending)
                                if written <= 0:
                                    raise SnapshotError("snapshot short write")
                                pending = pending[written:]
                        unchanged(src, opened, src_fd, name)
                        if size != opened.st_size:
                            raise SnapshotError(f"file size changed: {path}")
                        os.fchmod(out, 0o444)
                        entries.append((path, "file", size, hasher.hexdigest()))
                    finally:
                        os.close(out)
                finally:
                    os.close(src)
            else:
                raise SnapshotError(f"links/special files rejected: {path}")
        unchanged(src_fd, before_dir)
        check()

    try:
        with _directory(source) as src_fd, _directory(destination.parent) as parent:
            os.mkdir(destination.name, mode=0o755, dir_fd=parent)
            dst_fd = os.open(destination.name, dir_flags, dir_fd=parent)
            try:
                os.fchmod(dst_fd, 0o755)
                walk(src_fd, dst_fd, "", 0, os.fstat(src_fd).st_dev)
            finally:
                os.close(dst_fd)
    except (OSError, NotImplementedError) as exc:
        raise SnapshotError(f"snapshot refused: {exc}") from exc
    canonical = tuple(sorted(entries))
    payload = json.dumps(["qswarm-snapshot-v1", canonical], ensure_ascii=True,
                         separators=(",", ":")).encode("ascii")
    return SnapshotManifest(hashlib.sha256(payload).hexdigest(), file_count, total_bytes, canonical)
