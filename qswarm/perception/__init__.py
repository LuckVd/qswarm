"""perception：感知层 L0 三通道候选收集（SPEC-7 §1；M3 新增包）。

纯度纪律（§5-3）：clues / candidates / indexer 的线索匹配 / prompts 渲染 =
纯函数零 IO 零随机；IO 只落 channels（供给面调用 / semgrep 子进程）与 scan
（仓库文件读取 / 工件库写入）两个编排边缘。**写口纪律（§5-2）**：本包零账本
直写——候选集/索引经 ``exams.store.artifact_put`` 形态入工件库（kind ∈
{l0_candidates, l0_index}），账本事件仍只经 kernel 写口。
"""

from .candidates import (
    CANDIDATE_KINDS,
    L0_CHANNELS,
    MERGE_GAP_LINES,
    Candidate,
    candidates_from_json,
    candidates_to_json,
    candidate_id,
    merge_candidates,
)
from .channels import (
    L0_CHANNEL_PREFIX,
    ChannelBFailure,
    RulesOutcome,
    Shard,
    build_model_shards,
    channel_a_candidates,
    channel_b_scan,
    channel_c_scan,
    default_rules_runner,
)
from .clues import CHANNEL_A_CLUES, FamilyClues, clue_families, match_name
from .indexer import IndexCache, IndexEntry, index_repo, lang_for_path, parse_index, probe_languages
from .prompts import L0Block, L0_SCAN_SYSTEM, render_l0_scan
from .scan import MODES, ScanReport, default_scout_picker, scan_repo

__all__ = [
    # candidates（§1.1/§1.5）
    "CANDIDATE_KINDS",
    "L0_CHANNELS",
    "MERGE_GAP_LINES",
    "Candidate",
    "candidate_id",
    "merge_candidates",
    "candidates_to_json",
    "candidates_from_json",
    # clues（§1.2 纯数据）
    "CHANNEL_A_CLUES",
    "FamilyClues",
    "clue_families",
    "match_name",
    # indexer（§1.2 探测分级 + 缓存）
    "IndexEntry",
    "IndexCache",
    "probe_languages",
    "lang_for_path",
    "parse_index",
    "index_repo",
    # channels（§1.2–1.4 三通道）
    "L0_CHANNEL_PREFIX",
    "Shard",
    "ChannelBFailure",
    "RulesOutcome",
    "channel_a_candidates",
    "channel_b_scan",
    "channel_c_scan",
    "build_model_shards",
    "default_rules_runner",
    # prompts（§1.3 模板）
    "L0Block",
    "L0_SCAN_SYSTEM",
    "render_l0_scan",
    # scan（§1.6 档位入口）
    "MODES",
    "ScanReport",
    "default_scout_picker",
    "scan_repo",
]
