"""exams.store：exam_items / exam_results 表唯一写口 + 内容寻址工件库（SPEC-6 §4.3/§1.3）。

写口纪律（SPEC-2 §6.1 经 S4 修订）：``agents`` / ``hats``（roster.py）与
``exam_items`` / ``exam_results``（本模块）四表唯一写口 = ``qswarm/exams``；
``scripts/audit_write_ports.py`` 词表与 ``tests/test_kernel_write_guard.py`` 同步
钉死。ledger/cells 写口不变（播种存款走 ``KernelLedger.append`` + ``agg_cells``，
与 M0 世界侧同路径）。``artifacts`` 表不在写口词表（内容寻址库全量实现随后续
里程碑），本模块的索引行写入仅限 kind ∈ {seed, exam_log}。

- ``exam_items``：bank loader 装载时按 item_id UPSERT——同 item_id 载入更高
  ``version`` → 该行全列 UPDATE（内容换新，DDL 主键即 item_id，一题一行）；
  ``retired_at`` 只在题被移出题库时置位（:func:`retire_exam_items`），轮换换版不清位；
- ``exam_results``：``id = f"ER-{seq:06d}"``，seq = 库内 ``ER-`` 前缀 max+1（D20
  同型口径）；``model_ref`` = 实际服务模型（§3.2 只走 primary，故恒 = agent
  的 model_ref，字段留痕防未来放宽）；``ts`` = 注入时钟；行级幂等键 =
  ``(agent_id, purpose, role, item_id)`` 查重跳过（部分作答重跑收敛，§4.3；含
  role——帽考行不被主角色旧行遮蔽，2026-10-20 修复）；
- 工件库：内容寻址文件库（``<store_dir>/<hash[:2]>/<hash>``）+ ``artifacts`` 表
  索引行——seed 快照（§1.3）与 exam_log 原始响应（§3.4/S9：判分可独立复核、
  replay 可复现的真源载体）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path

from qswarm.contracts import ExamPurpose
from qswarm.kernel import KernelLedger, iso_utc, transaction

from .bank import SeedBank

__all__ = [
    "ingest_bank_artifacts",
    "retire_exam_items",
    "upsert_exam_items",
    "write_exam_result",
    "artifact_put",
    "artifact_get",
    "artifact_hash",
    "artifact_get_verified",
    "ARTIFACT_MAX_BYTES",
]

_ER_SEQ_RE = re.compile(r"^ER-(\d{6,})$")


# ── exam_items：UPSERT-by-version（§4.3）────────────────────────


def upsert_exam_items(ledger: KernelLedger, bank: SeedBank) -> int:
    """题库 → exam_items 表（bank loader 装载时调用；返回新写行数）。

    - 行缺失 → INSERT（created_at = 时钟签发，retired_at NULL）；
    - 同 item_id 且新 version 更高 → 全列内容 UPDATE（created_at/retired_at 保留）；
    - 同/低 version → 跳过（行内容换新只进不退）。
    """
    now = iso_utc(ledger._clock.now())
    written = 0
    with transaction(ledger):
        for it in bank.items:
            row = ledger.conn.execute(
                "SELECT version FROM exam_items WHERE item_id = ?", (it.item_id,)
            ).fetchone()
            taxonomy_json = json.dumps(it.taxonomy_path, ensure_ascii=False)
            if row is None:
                ledger.conn.execute(
                    "INSERT INTO exam_items (item_id, taxonomy_json, lang,"
                    " repo_snapshot_ref, planted, expected, difficulty, version,"
                    " canary, source, created_at, retired_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        it.item_id, taxonomy_json, it.lang, it.repo_snapshot_ref,
                        int(it.planted), it.expected, it.difficulty, it.version,
                        it.canary, str(it.source.value), now,
                    ),
                )
                written += 1
            elif it.version > int(row[0]):
                ledger.conn.execute(
                    "UPDATE exam_items SET taxonomy_json = ?, lang = ?,"
                    " repo_snapshot_ref = ?, planted = ?, expected = ?,"
                    " difficulty = ?, version = ?, canary = ?, source = ?"
                    " WHERE item_id = ?",
                    (
                        taxonomy_json, it.lang, it.repo_snapshot_ref,
                        int(it.planted), it.expected, it.difficulty, it.version,
                        it.canary, str(it.source.value), it.item_id,
                    ),
                )
                written += 1
    return written


def retire_exam_items(ledger: KernelLedger, item_ids: list[str]) -> int:
    """题移出题库时置 retired_at（轮换换版不清位；已置位跳过）。返回置位行数。"""
    now = iso_utc(ledger._clock.now())
    n = 0
    with transaction(ledger):
        for item_id in item_ids:
            cur = ledger.conn.execute(
                "UPDATE exam_items SET retired_at = ?"
                " WHERE item_id = ? AND retired_at IS NULL",
                (now, item_id),
            )
            n += cur.rowcount
    return n


# ── exam_results：行签发（D20 库内同前缀 max+1）＋行级幂等 ────────


def _next_exam_result_seq(conn: sqlite3.Connection) -> int:
    """库内 ``ER-`` 前缀 max+1（D20 同型口径；空表从 1 起）。"""
    seq = 0
    for (row_id,) in conn.execute("SELECT id FROM exam_results WHERE id LIKE 'ER-%'"):
        m = _ER_SEQ_RE.match(row_id)
        if m:
            seq = max(seq, int(m.group(1)))
    return seq + 1


def write_exam_result(
    ledger: KernelLedger,
    *,
    purpose: ExamPurpose | str,
    agent_id: str,
    role: str,
    node: str,
    item_id: str,
    correct: bool,
    model_ref: str,
    score: float | None = None,
) -> str:
    """签发一行 exam_results（返回行 id；幂等命中返回既有行 id）。

    幂等键 = (agent_id, purpose, role, item_id)：重跑覆盖同批行时查重跳过（§4.3）。
    含 role（2026-10-20 修复，DECISIONS 同日行）：不含 role 时同蜂第二角色（帽考）
    的行被主角色旧行遮蔽——correct 值丢失、exam_results 审计面与报告面背离。"""
    purpose_key = str(getattr(purpose, "value", purpose))
    existing = ledger.conn.execute(
        "SELECT id FROM exam_results WHERE agent_id = ? AND purpose = ?"
        " AND role = ? AND item_id = ?",
        (agent_id, purpose_key, role, item_id),
    ).fetchone()
    if existing is not None:
        return str(existing[0])
    seq = _next_exam_result_seq(ledger.conn)
    row_id = f"ER-{seq:06d}"
    ledger.conn.execute(
        "INSERT INTO exam_results (id, purpose, agent_id, role, node, item_id,"
        " correct, score, model_ref, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            row_id, purpose_key, agent_id, role, node, item_id,
            int(bool(correct)), score, model_ref, iso_utc(ledger._clock.now()),
        ),
    )
    return row_id


# ── 内容寻址工件库（§1.3 seed / §3.4 exam_log）────────────────────


def artifact_hash(content: bytes) -> str:
    """内容寻址 hash（sha256 hex）。"""
    return hashlib.sha256(content).hexdigest()


#: 工件 kind 词表（M2 两类 + M3 感知层两类 + M4 会诊两类 + M5 沙箱三类：PoC 脚本/
#: 执行日志/依赖清单，仲裁件一类——SPEC-9 §1.3；写口审计词表不变（artifacts 不在
#: WRITE_TABLES），零账本直写纪律由 tests/test_m3_invariants.py 钉死）。
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {"seed", "exam_log", "l0_candidates", "l0_index", "repo_map", "pre_verdict",
     "poc_script", "poc_log", "poc_deps", "arbitration", "verify_auth"}
)


# R2：默认每件 64 MiB，可由调用方按证据类型显式收紧/调整，禁止无限读取。
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024
_ARTIFACT_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")


@contextmanager
def _artifact_directory(store_dir: Path, digest: str, *, create: bool = False):
    """逐级 dirfd + O_NOFOLLOW；包括库根祖先也不接受链接，避免检查/打开竞态。

    POSIX/Linux 工件库边界；缺少安全 open 标志的平台拒绝操作而不降级。
    返回的 fd 固定目录，不再从未经核验的绝对路径打开工件。
    """
    path = Path(store_dir).absolute()
    if ".." in path.parts:
        raise ValueError("工件库路径不得含 '..'")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(path.anchor, flags)
    try:
        for part in (*path.parts[1:], digest[:2]):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory
    finally:
        os.close(directory)


def _read_artifact_bytes(directory: int, digest: str, max_bytes: int) -> bytes:
    """只读普通文件；NONBLOCK 防 FIFO 卡住，读取量永不超过 max_bytes+1。"""
    fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=directory)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError("工件不是有界普通文件")
        data = bytearray()
        while len(data) <= max_bytes:
            chunk = os.read(fd, min(65536, max_bytes + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(fd)
        if (len(data) > max_bytes or len(data) != before.st_size
                or after.st_size != before.st_size
                or after.st_mtime_ns != before.st_mtime_ns
                or artifact_hash(data) != digest):
            raise ValueError("工件内容/大小/摘要不一致")
        return bytes(data)
    finally:
        os.close(fd)


def artifact_get_verified(
    ledger: KernelLedger,
    store_dir: Path,
    hash_hex: str,
    *,
    expected_kind: str | None = None,
    max_bytes: int = ARTIFACT_MAX_BYTES,
) -> bytes | None:
    """R2 安全读：规范小写 SHA256、索引 size/kind、普通文件及实际摘要全部复核。

    缺索引/缺文件/不可读/越界/链接/超限/损坏一律 None；不修复或覆盖工件。
    ``expected_kind=None`` 只检查 kind 在已知词表；安全调用者应指名用途。
    旧 artifact_get 的兼容语义不变。
    """
    if (not isinstance(hash_hex, str) or not _ARTIFACT_HASH_RE.fullmatch(hash_hex)
            or type(max_bytes) is not int or max_bytes < 1):
        return None
    row = ledger.conn.execute(
        "SELECT size, kind FROM artifacts WHERE hash = ?", (hash_hex,)
    ).fetchone()
    if (row is None or type(row[0]) is not int or not 0 <= row[0] <= max_bytes
            or row[1] not in ARTIFACT_KINDS
            or (expected_kind is not None and row[1] != expected_kind)):
        return None
    try:
        with _artifact_directory(store_dir, hash_hex) as directory:
            content = _read_artifact_bytes(directory, hash_hex, max_bytes)
        return content if len(content) == row[0] else None
    except (OSError, ValueError, TypeError, AttributeError, NotImplementedError):
        return None


def artifact_put(
    ledger: KernelLedger,
    store_dir: Path,
    content: bytes,
    *,
    mime: str,
    kind: str,
    producer: str,
    max_bytes: int = ARTIFACT_MAX_BYTES,
) -> str:
    """写入或复核内容寻址工件；既存污染拒绝、绝不静默覆盖，检查先于索引写。

    兼容策略：非授权用途共享相同内容时保留首次 kind/mime/producer；涉及
    verify_auth 的 kind 冲突拒绝（不能把普通工件当授权，或反向复用授权）。
    已有索引 size 必须一致；文件新建用 O_EXCL，目录逐级拒绝符号链接。
    """
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"工件 kind 越词表：{kind!r}（合法值：{sorted(ARTIFACT_KINDS)}）"
        )
    if (type(max_bytes) is not int or max_bytes < 1
            or not isinstance(content, bytes) or len(content) > max_bytes):
        raise ValueError("工件内容或大小上限非法/超限")
    h = artifact_hash(content)
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT size, kind FROM artifacts WHERE hash = ?", (h,)
        ).fetchone()
        if row is not None and (
            row[0] != len(content) or row[1] not in ARTIFACT_KINDS
            or (row[1] != kind and "verify_auth" in (row[1], kind))
        ):
            raise ValueError("工件索引 size/kind 冲突")
        try:
            with _artifact_directory(store_dir, h, create=True) as directory:
                try:
                    fd = os.open(h, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
                except FileExistsError:
                    if _read_artifact_bytes(directory, h, max_bytes) != content:
                        raise ValueError("既存工件污染")
                else:
                    try:
                        view = memoryview(content)
                        while view:
                            written = os.write(fd, view)
                            if written <= 0:
                                raise OSError("工件写入未完成")
                            view = view[written:]
                        os.fsync(fd)
                    finally:
                        os.close(fd)
        except (OSError, AttributeError, NotImplementedError) as exc:
            raise ValueError("工件路径不安全或无法读写") from exc
        now = iso_utc(ledger._clock.now())
        ledger.conn.execute(
            "INSERT OR IGNORE INTO artifacts (hash, size, mime, kind, producer, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (h, len(content), mime, kind, producer, now),
        )
    return h


def artifact_get(ledger: KernelLedger, store_dir: Path, hash_hex: str) -> bytes | None:
    """按 hash 读工件内容（缺失/未索引 → None）。"""
    row = ledger.conn.execute(
        "SELECT 1 FROM artifacts WHERE hash = ?", (hash_hex,)
    ).fetchone()
    if row is None:
        return None
    path = Path(store_dir) / hash_hex[:2] / hash_hex
    if not path.exists():
        return None
    return path.read_bytes()


# ── bank 工件摄取（§1.3：快照文件 + item.json + 清单工件）──────────


def ingest_bank_artifacts(
    bank: SeedBank,
    root: Path,
    ledger: KernelLedger,
    *,
    store_dir: Path,
) -> int:
    """题库盘上文件 → 内容寻址工件库（kind=seed；返回写入工件数）。

    每题：逐快照文件 + item.json 入库；另写该题快照清单工件（name→sha256 索引，
    ``repo_snapshot_ref`` 的反查目标）。"""
    root = Path(root)
    written = 0
    for it in bank.items:
        item_dir = root / "items" / it.item_id
        for name in it.snapshot_files:
            content = (item_dir / "snapshot" / name).read_bytes()
            artifact_put(
                ledger, store_dir, content,
                mime="text/plain", kind="seed", producer="seed_bank",
            )
            written += 1
        item_json = (item_dir / "item.json").read_bytes()
        artifact_put(
            ledger, store_dir, item_json,
            mime="application/json", kind="seed", producer="seed_bank",
        )
        written += 1
        from .bank import manifest_content

        artifact_put(
            ledger, store_dir, manifest_content(it, root),
            mime="application/json", kind="seed", producer="seed_bank",
        )
        written += 1
    return written
