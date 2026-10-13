"""perception.scan：扫描入口——repo 档 / diff 档三档行为（SPEC-7 §1.6）。

档位行为（§2.2 表逐字）：
- **fast**：三通道高置信命中（A 结构线索 + C 规则；两者皆无的语言 = B 直扫）
  → 归并后按 priority 降序取 Top-``l1.fast_top_k``；
- **pr**：diff ± 1 调用邻域（索引跳 1 跳）；A/B/C 全开；
- **full**：整仓枚举 + 全部规则；分片供 L1 目录级（分片器落 kernel.loop）。

编排纯度边界：本模块是 perception 的 IO 编排层（读仓库文件 / 供给面调用 /
工件库写入）；A 通道产出、归并、排序全走纯函数。**确定性**：文件遍历字典序、
分片确定性、B 通道选蜂经注入 picker、归并排序显式——同仓库内容 + 同参数 +
同 picker 决策 → 候选集工件逐位一致（C 组判据）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.kernel import KernelLedger  # noqa: TC001 — 仅类型注解

from .candidates import Candidate, candidates_to_json, merge_candidates
from .channels import (
    ChannelBFailure,
    Shard,
    build_model_shards,
    channel_a_candidates,
    channel_b_scan,
    channel_c_scan,
)
from .indexer import IndexCache, lang_for_path, parse_index, probe_languages

__all__ = ["ScanReport", "scan_repo", "default_scout_picker"]

MODES = ("fast", "pr", "full")


@dataclass
class ScanReport:
    """扫描报告（确定性；候选集 + 通道计数 + 缺席计数，§5-6 缺席显式不静默）。"""

    mode: str
    candidates: list[Candidate] = field(default_factory=list)
    files_total: int = 0
    files_scanned: int = 0
    files_skipped_binary: int = 0
    channel_a_count: int = 0
    channel_b_count: int = 0
    channel_c_count: int = 0
    shard_count: int = 0
    channel_a_absent_langs: list[str] = field(default_factory=list)
    model_channel_fail: list[dict] = field(default_factory=list)
    rule_channel_skipped: str | None = None
    artifact_ref: str | None = None          # l0_candidates 工件 sha256（可选）

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "counts": {
                "files_total": self.files_total,
                "files_scanned": self.files_scanned,
                "files_skipped_binary": self.files_skipped_binary,
                "channel_a": self.channel_a_count,
                "channel_b": self.channel_b_count,
                "channel_c": self.channel_c_count,
                "shards": self.shard_count,
                "candidates": len(self.candidates),
            },
            "channel_a_absent_langs": list(self.channel_a_absent_langs),
            "model_channel_fail": list(self.model_channel_fail),
            "rule_channel_skipped": self.rule_channel_skipped,
            "artifact_ref": self.artifact_ref,
        }


def default_scout_picker(scouts: list[tuple[str, str]]):
    """缺省选蜂：shard 序号对蜂池轮转（串行执行下负载天然均衡；确定性）。

    返回 ``picker(shard_id, shard_index) -> (model_ref, agent_id)``。"""

    def _picker(shard_id: str, shard_index: int) -> tuple[str, str]:
        _ = shard_id
        return scouts[shard_index % len(scouts)]

    return _picker


def _walk_repo(root: Path) -> list[Path]:
    """仓库文件字典序遍历（跳 .git 等版本库目录；posix 相对路径排序）。"""
    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if any(part in (".git", ".hg", ".svn", "__pycache__", "node_modules") for part in rel.parts):
            continue
        files.append(rel)
    files.sort(key=lambda r: r.as_posix())
    return files


def _neighbors_1hop(
    changed: list[str],
    entries: dict[str, object],
) -> set[str]:
    """diff ± 1 调用邻域（索引跳 1 跳）：变更文件的调用点 ↔ 函数定义跨文件引用。

    - ``entries``：path → IndexEntry（parse_ok 者）；
    - name→defs 全局倒排一次；变更文件 F 的邻域 = 定义了 F 所调名字的文件 ∪
      调用了 F 所定义名字的文件（双向 1 跳）。"""
    def_owner: dict[str, set[str]] = {}
    for path, entry in entries.items():
        for name, _line, _hits in entry.defs:
            def_owner.setdefault(name, set()).add(path)
    changed_set = set(changed)
    changed_defs: set[str] = set()
    for path in changed:
        entry = entries.get(path)
        if entry is not None:
            changed_defs |= {name for name, _l, _h in entry.defs}
    out: set[str] = set()
    for path in changed:
        entry = entries.get(path)
        if entry is None:
            continue
        for name, _line, _hits in entry.calls:
            for owner in def_owner.get(name, ()):
                if owner != path:
                    out.add(owner)               # callee 侧：F 调入的文件
    for path, entry in entries.items():
        if path in changed_set:
            continue
        for name, _line, _hits in entry.calls:
            if name in changed_defs:
                out.add(path)                    # caller 侧：调入 F 的文件
                break
    return out


def scan_repo(
    root: str | Path,
    *,
    mode: str,
    params,
    adapter=None,
    scouts: list[tuple[str, str]] | None = None,
    scout_picker=None,
    cache_dir: str | Path | None = None,
    ledger: KernelLedger | None = None,
    store_dir: str | Path | None = None,
    changed_paths: list[str] | None = None,
    rules_runner=None,
) -> ScanReport:
    """扫描入口（§1.6 档位行为；返回 ScanReport，候选集确定性）。

    - ``adapter`` 缺省 None = 通道 B 关（无供给面即缺席——fast 档 A 缺席语言
      无兜底通道时如实计数，不抛错）；
    - ``scouts``：通道 B 可用蜂池 [(model_ref, agent_id)]；``scout_picker`` 缺省
      轮转（loop 可注入 least-loaded picker）；
    - ``changed_paths``：pr 档必填（diff 变更文件，仓库相对路径）；
    - ``rules_runner``：通道 C 运行器（缺省 semgrep CLI；可注入离线）；规则关
      （params.perception.rules.enabled=false）→ 不执行、不计缺席；
    - ``ledger``/``store_dir`` 同给 → 候选集整体入工件库（kind=l0_candidates，
      内容寻址，复用 exams.store.artifact_put 形态）。"""
    mode_key = str(mode)
    if mode_key not in MODES:
        raise ValueError(f"未知扫描档位：{mode!r}（合法值：{MODES}）")
    if mode_key == "pr" and not changed_paths:
        raise ValueError("pr 档扫描需要 changed_paths（diff 变更文件）")
    root_path = Path(root)
    rel_files = _walk_repo(root_path)
    report = ScanReport(mode=mode_key)
    report.files_total = len(rel_files)

    # 读文件 + 索引（内容寻址缓存；不可解码文件跳过并计数——二进制面缺席显式）。
    cache = IndexCache(cache_dir)
    contents: dict[str, str] = {}
    raws: dict[str, bytes] = {}
    for rel in rel_files:
        source = (root_path / rel).read_bytes()
        raws[rel.as_posix()] = source
        try:
            contents[rel.as_posix()] = source.decode("utf-8")
        except UnicodeDecodeError:
            report.files_skipped_binary += 1
    report.files_scanned = len(contents)

    available = probe_languages()
    entries: dict[str, object] = {}
    absent_langs: set[str] = set()
    for path in sorted(contents):
        lang = lang_for_path(path)
        if lang is None:
            continue
        entry = cache.get_or_parse(path, lang, raws[path])
        entries[path] = entry
        if not entry.parse_ok:
            absent_langs.add(lang)
    report.channel_a_absent_langs = sorted(absent_langs)
    del available                                # 探测即加载；缺席以 parse_ok 承载

    # 通道 A（结构）。
    a_raw = channel_a_candidates(list(entries.values()))
    report.channel_a_count = len(a_raw)

    # 通道 C（规则；D30 必选件缺省开；运行期故障缺席计数）。
    c_out = None
    if params.perception.rules.enabled and rules_runner is not None and contents:
        targets = sorted(contents)
        c_out = channel_c_scan(
            [str(root_path / rel) for rel in targets],
            rules_runner=rules_runner,
            repo_root=root_path,
        )
        if c_out.candidates is not None:
            report.channel_c_count = len(c_out.candidates)
        else:
            report.rule_channel_skipped = c_out.skip_reason

    # 通道 B（模型直扫；档位决定分片范围）。
    b_out = None
    if params.perception.model.enabled and adapter is not None and scouts:
        if mode_key == "pr":
            if not changed_paths:
                raise ValueError("pr 档扫描需要 changed_paths（diff 变更文件）")
            scope = sorted(set(changed_paths) | _neighbors_1hop(sorted(changed_paths), entries))
        elif mode_key == "fast":
            # 两者皆无的语言 = B 直扫：A 缺席语言 ∪ A 有 grammar 但零命中语言的
            # 文件不进 B（高置信通道已覆盖；§1.6 fast 档逐字）。
            no_grammar = {p for p in sorted(contents)
                          if lang_for_path(p) is None or lang_for_path(p) in absent_langs}
            scope = sorted(no_grammar)
        else:                                     # full：整仓枚举
            scope = sorted(contents)
        files = [(p, contents[p]) for p in scope if p in contents]
        defs_by_path = {
            p: [tuple(d) for d in entries[p].defs] if p in entries else []
            for p in scope
        }
        shards = build_model_shards(
            files, max_file_lines=params.perception.l0.max_file_lines, defs_by_path=defs_by_path,
        )
        report.shard_count = len(shards)
        picker = scout_picker or default_scout_picker(scouts)
        b_out = _run_channel_b(shards, adapter=adapter, picker=picker)
        report.channel_b_count = len(b_out[0])
        report.model_channel_fail = [
            {"shard_id": f.shard_id, "agent_id": f.agent_id, "reason": f.reason}
            for f in b_out[1]
        ]

    # 归并 + 档位裁剪。
    merged = merge_candidates([*a_raw, *(c_out.candidates if c_out and c_out.candidates else [])])
    b_merged = merge_candidates(b_out[0]) if b_out else []
    all_merged = merge_candidates([*merged, *b_merged])
    if mode_key == "fast":
        top_k = params.perception.l1.fast_top_k
        by_priority = sorted(all_merged, key=lambda c: (-c.priority, c.sort_key()))
        report.candidates = by_priority[:top_k]
    else:
        report.candidates = all_merged

    # 工件（l0_candidates；内容寻址，复用 exams.store.artifact_put 形态）。
    if ledger is not None and store_dir is not None:
        from qswarm.exams.store import artifact_put

        payload = json.dumps(
            {"kind": "l0_candidates", "mode": mode_key, "report": report.to_dict()},
            ensure_ascii=False, sort_keys=True,
        )
        # 工件载荷 = 候选集本体（不含报告计数——候选集位级可对照，S9/C 组口径）。
        artifact_ref = artifact_put(
            ledger, Path(store_dir),
            candidates_to_json(report.candidates).encode("utf-8"),
            mime="application/json", kind="l0_candidates", producer="perception",
        )
        report.artifact_ref = artifact_ref
    return report


def _run_channel_b(shards: list[Shard], *, adapter, picker):
    """按 picker 逐片执行通道 B（返回 (candidates, failures)）。"""
    from .channels import ChannelBOutcome

    outcome = ChannelBOutcome()
    for idx, shard in enumerate(shards):
        model_ref, agent_id = picker(shard.shard_id, idx)
        part = channel_b_scan([shard], adapter=adapter, model_ref=model_ref, agent_id=agent_id)
        outcome.candidates.extend(part.candidates)
        outcome.failures.extend(part.failures)
    return outcome.candidates, outcome.failures
