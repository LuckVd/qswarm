"""R2: content-addressed artifact integrity and filesystem boundaries."""

import hashlib
import os

import pytest

from qswarm.exams import store
from qswarm.kernel import KernelLedger


@pytest.fixture
def artifacts(tmp_path):
    with KernelLedger(tmp_path / "ledger.db") as ledger:
        yield ledger, tmp_path / "store"


def put(ledger, root, content=b"evidence", **kwargs):
    return store.artifact_put(
        ledger, root, content, mime="text/plain", kind="poc_log",
        producer="r2-test", **kwargs,
    )


def indexed(ledger, digest):
    return ledger.conn.execute(
        "SELECT size, kind FROM artifacts WHERE hash = ?", (digest,)
    ).fetchone()


def test_verified_roundtrip_and_expected_kind(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    assert store.artifact_get_verified(ledger, root, digest) == b"evidence"
    assert store.artifact_get_verified(
        ledger, root, digest, expected_kind="poc_log"
    ) == b"evidence"
    assert store.artifact_get_verified(
        ledger, root, digest, expected_kind="verify_auth"
    ) is None


@pytest.mark.parametrize("digest", ["", "../escape", "a" * 63, "a" * 65,
                                    "g" * 64, "A" * 64, None, 123])
def test_verified_rejects_noncanonical_hash(artifacts, digest):
    ledger, root = artifacts
    assert store.artifact_get_verified(ledger, root, digest) is None


def test_verified_rejects_missing_file_and_index(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    path = root / digest[:2] / digest
    path.unlink()
    assert store.artifact_get_verified(ledger, root, digest) is None
    path.write_bytes(b"evidence")
    ledger.conn.execute("DELETE FROM artifacts WHERE hash = ?", (digest,))
    assert store.artifact_get_verified(ledger, root, digest) is None


@pytest.mark.parametrize("corruption", ["bytes", "size"])
def test_verified_rechecks_content_and_size(artifacts, corruption):
    ledger, root = artifacts
    digest = put(ledger, root)
    if corruption == "bytes":
        (root / digest[:2] / digest).write_bytes(b"tampered")
    else:
        ledger.conn.execute("UPDATE artifacts SET size = 99 WHERE hash = ?", (digest,))
    assert store.artifact_get_verified(ledger, root, digest) is None


def test_verified_and_put_enforce_configured_limit(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    assert store.artifact_get_verified(ledger, root, digest, max_bytes=7) is None
    assert store.artifact_get_verified(ledger, root, digest, max_bytes=8) == b"evidence"
    assert store.artifact_get_verified(ledger, root, digest, max_bytes=0) is None
    with pytest.raises(ValueError):
        put(ledger, root, b"longer-than-limit", max_bytes=3)
    assert indexed(ledger, hashlib.sha256(b"longer-than-limit").hexdigest()) is None


@pytest.mark.parametrize("location", ["root", "prefix", "file", "ancestor"])
def test_verified_and_put_reject_symlinks(artifacts, tmp_path, location):
    ledger, root = artifacts
    digest = put(ledger, root)
    path = root / digest[:2] / digest
    if location == "file":
        outside = tmp_path / "outside-file"
        outside.write_bytes(b"evidence")
        path.unlink()
        path.symlink_to(outside)
    elif location == "prefix":
        outside = tmp_path / "outside-prefix"
        (root / digest[:2]).rename(outside)
        (root / digest[:2]).symlink_to(outside, target_is_directory=True)
    elif location == "root":
        outside = tmp_path / "outside-root"
        root.rename(outside)
        root.symlink_to(outside, target_is_directory=True)
    else:
        link = tmp_path / "ancestor-link"
        link.symlink_to(tmp_path, target_is_directory=True)
        root = link / "store"
    assert store.artifact_get_verified(ledger, root, digest) is None
    with pytest.raises(ValueError):
        put(ledger, root)


@pytest.mark.parametrize("node", ["directory", "fifo"])
def test_verified_and_put_reject_non_regular_files(artifacts, node):
    ledger, root = artifacts
    digest = put(ledger, root)
    path = root / digest[:2] / digest
    path.unlink()
    if node == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    assert store.artifact_get_verified(ledger, root, digest) is None
    with pytest.raises(ValueError):
        put(ledger, root)


def test_put_rejects_existing_pollution_before_index_write(artifacts):
    ledger, root = artifacts
    digest = hashlib.sha256(b"evidence").hexdigest()
    path = root / digest[:2] / digest
    path.parent.mkdir(parents=True)
    path.write_bytes(b"tampered")
    with pytest.raises(ValueError):
        put(ledger, root)
    assert path.read_bytes() == b"tampered"
    assert indexed(ledger, digest) is None


def test_put_rejects_corrupt_index_size_without_overwrite(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    ledger.conn.execute("UPDATE artifacts SET size = 99 WHERE hash = ?", (digest,))
    ledger.conn.commit()
    with pytest.raises(ValueError):
        put(ledger, root)
    assert indexed(ledger, digest) == (99, "poc_log")


def test_put_preserves_first_nonsecurity_kind_but_rejects_auth_alias(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    # Existing content sharing among legacy non-auth artifact uses is preserved.
    assert store.artifact_put(
        ledger, root, b"evidence", mime="text/plain", kind="seed", producer="other"
    ) == digest
    assert indexed(ledger, digest) == (8, "poc_log")
    with pytest.raises(ValueError):
        store.artifact_put(
            ledger, root, b"evidence", mime="application/json", kind="verify_auth",
            producer="other",
        )
    auth = store.artifact_put(
        ledger, root, b"auth", mime="application/json", kind="verify_auth", producer="r2"
    )
    with pytest.raises(ValueError):
        put(ledger, root, b"auth")
    assert indexed(ledger, auth) == (4, "verify_auth")


def test_verified_unreadable_returns_none(artifacts, monkeypatch):
    ledger, root = artifacts
    digest = put(ledger, root)
    def denied(*args, **kwargs):
        raise PermissionError("unreadable")
    monkeypatch.setattr(os, "open", denied)
    assert store.artifact_get_verified(ledger, root, digest) is None


def test_new_put_rejects_linked_prefix_without_outside_write(artifacts, tmp_path):
    ledger, root = artifacts
    content = b"new evidence"
    digest = hashlib.sha256(content).hexdigest()
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    (root / digest[:2]).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        put(ledger, root, content)
    assert not (outside / digest).exists()
    assert indexed(ledger, digest) is None


def test_put_adopts_valid_unindexed_bytes_and_handles_empty(artifacts):
    ledger, root = artifacts
    content = b"adopt me"
    digest = hashlib.sha256(content).hexdigest()
    path = root / digest[:2] / digest
    path.parent.mkdir(parents=True)
    path.write_bytes(content)
    assert put(ledger, root, content) == digest
    assert store.artifact_get_verified(ledger, root, digest) == content
    empty = put(ledger, root, b"")
    assert store.artifact_get_verified(ledger, root, empty) == b""


def test_verified_reads_in_bounded_chunks(artifacts, monkeypatch):
    ledger, root = artifacts
    content = b"x" * 200_000
    digest = put(ledger, root, content)
    original_read = os.read
    reads = []
    returned = []
    def recording_read(fd, count):
        reads.append(count)
        data = original_read(fd, count)
        returned.append(len(data))
        return data
    monkeypatch.setattr(os, "read", recording_read)
    assert store.artifact_get_verified(
        ledger, root, digest, max_bytes=len(content)
    ) == content
    assert max(reads) <= 65536
    assert sum(returned) <= len(content) + 1


def test_verified_rejects_unknown_index_kind(artifacts):
    ledger, root = artifacts
    digest = put(ledger, root)
    ledger.conn.execute("UPDATE artifacts SET kind = 'unknown' WHERE hash = ?", (digest,))
    assert store.artifact_get_verified(ledger, root, digest) is None
