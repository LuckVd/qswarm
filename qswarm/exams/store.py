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
import re
import sqlite3
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


#: 工件 kind 词表（M2 两类 + M3 感知层两类 + M4 会诊两类：repo_map 切片与
#: verifier 预裁决单，SPEC-8 §1.3/§2.6——写口审计词表不变（artifacts 不在
#: WRITE_TABLES），零账本直写纪律由 tests/test_m3_invariants.py 钉死）。
ARTIFACT_KINDS: frozenset[str] = frozenset(
    {"seed", "exam_log", "l0_candidates", "l0_index", "repo_map", "pre_verdict"}
)


def artifact_put(
    ledger: KernelLedger,
    store_dir: Path,
    content: bytes,
    *,
    mime: str,
    kind: str,
    producer: str,
) -> str:
    """写内容寻址工件（文件 + artifacts 索引行；幂等：同 hash 跳过）。返回 hash。

    ``kind`` 限定 :data:`ARTIFACT_KINDS`（M2 seed|exam_log + M3 l0_candidates|
    l0_index + M4 repo_map|pre_verdict；其余 kind 属后续里程碑的工件面）。"""
    if kind not in ARTIFACT_KINDS:
        raise ValueError(
            f"工件 kind 越词表：{kind!r}（合法值：{sorted(ARTIFACT_KINDS)}）"
        )
    h = artifact_hash(content)
    path = Path(store_dir) / h[:2] / h
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    now = iso_utc(ledger._clock.now())
    with transaction(ledger):
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
