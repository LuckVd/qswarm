"""exams：种子题库 v0 与考试播种（SPEC-6；M2 新增包）。

考试与编制子域——``agents`` / ``hats`` / ``exam_items`` / ``exam_results`` 四表
唯一写口（写口纪律扩展，SPEC-2 §6.1 经 S4 修订）；播种存款走
``KernelLedger.append`` + ``agg_cells``（与 M0 世界侧同路径）。机制层
（kernel/contracts/simulator）零反向依赖本包。
"""

from .bank import (
    V0_QUOTA,
    MutationMeta,
    SeedBank,
    SeedItem,
    UpstreamRef,
    load_bank,
    manifest_content,
    manifest_sha256,
    quota_table,
    validate_bank,
)
from .judge import (
    entry_qc_pass,
    judge_answer,
    periodic_exam_passes,
    promotion_exam_passes,
)
from .paper import ENTRY_FP, ENTRY_ITEMS, ENTRY_REAL, PAPER_PATTERN, draw_paper
from .roster import Candidate, apply_qc_decision, expand_candidates, register_roster
from .runner import (
    CellOutcome,
    ItemOutcome,
    SnapshotStore,
    cache_get,
    cache_key,
    cache_put,
    deposit_exists,
    run_entry_cell,
)
from .seeding import seed_entry, write_report
from .store import (
    artifact_get,
    artifact_hash,
    artifact_put,
    ingest_bank_artifacts,
    retire_exam_items,
    upsert_exam_items,
    write_exam_result,
)

__all__ = [
    # bank（§1）
    "ENTRY_FP",
    "ENTRY_ITEMS",
    "ENTRY_REAL",
    "MutationMeta",
    "PAPER_PATTERN",
    "SeedBank",
    "SeedItem",
    "UpstreamRef",
    "V0_QUOTA",
    "load_bank",
    "manifest_content",
    "manifest_sha256",
    "quota_table",
    "validate_bank",
    # paper（§3.1）
    "draw_paper",
    # judge（§3.3）
    "entry_qc_pass",
    "judge_answer",
    "periodic_exam_passes",
    "promotion_exam_passes",
    # store（§4.3/§1.3）
    "artifact_get",
    "artifact_hash",
    "artifact_put",
    "ingest_bank_artifacts",
    "retire_exam_items",
    "upsert_exam_items",
    "write_exam_result",
    # roster（§4.1）
    "Candidate",
    "apply_qc_decision",
    "expand_candidates",
    "register_roster",
    # runner（§3.4/§3.5）
    "CellOutcome",
    "ItemOutcome",
    "SnapshotStore",
    "cache_get",
    "cache_key",
    "cache_put",
    "deposit_exists",
    "run_entry_cell",
    # seeding（§4.2/§4.4）
    "seed_entry",
    "write_report",
]
