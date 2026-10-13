"""perception.repomap：仓库结构索引切片——L2 会诊的上下文供给（SPEC-8 §1；设计 §2.1）。

**名称绑定近似级（D37，2026-10-13 负责人拍板）**：跨文件 N 跳邻域图 = 调用点名 ↔
函数定义名字典匹配 + import 正则边；不做 stack-graphs 级精确解析。依据：现有
IndexEntry 三档索引（calls/defs/idents，内容寻址缓存）直接支撑；精确解析成本不成
比例且跨函数推理本就归 L2 agent（§2.1 边界逐字）——切片误差由深诊吸收。

纯度纪律（perception 包纪律逐字）：本模块**纯函数零 IO 零随机**——图构建与切片
全部确定性；仓库文件读取与工件写入在调用方（kernel.consult / scan 编排边缘）。
绑定语义（宁缺勿错）：
- 被调名同文件有 def → 仅同文件边（最强绑定，跨文件同名不引入）；
- 否则全仓 defs 恰一个同名定义 → 跨文件边；
- 零个或 ≥2 个 → 不成边（歧义保守丢弃）；
- import 边全语言正则统一（§2.1「无 grammar 退化为 import 正则」的推广），
  只保留能对到同仓文件路径的边。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import blake2b
from pathlib import PurePosixPath

from .indexer import IndexEntry

__all__ = [
    "MODULE_NODE",
    "SliceFile",
    "RepoSlice",
    "NameGraph",
    "build_name_graph",
    "slice_for",
    "slice_to_json",
]


#: 文件顶层（无所属 def）的虚节点名。
MODULE_NODE = "<module>"

# ── import 正则（全语言统一；命中组 = 模块名字符串）─────────────────
_IMPORT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:from|import)\s+([\w.]+)", re.MULTILINE),          # py
    re.compile(r'^\s*import\s+(?:[\w$]+\s*,\s*)*([\w$]+)', re.MULTILINE),  # js/ts/java/go
    re.compile(r'^\s*#\s*include\s*[<"]([\w./]+)[>"]', re.MULTILINE),      # c/cpp
)

#: 模块名尾段 → 同仓候选文件的匹配：a.b → a/b.py 与尾段 b 的任意文件（§1.1）。


@dataclass(frozen=True)
class SliceFile:
    """切片内单文件载荷（行闭区间 1-based；截断语义见 RepoSlice）。"""

    path: str
    lang: str
    spans: tuple[tuple[int, int], ...]        # def 全文 / import 段 / 整文件窗
    truncated: bool = False                   # 本文件载荷被 max_lines 截断


@dataclass(frozen=True)
class RepoSlice:
    """一次邻域切片（内容寻址；JSON 可序列化）。"""

    files: tuple[SliceFile, ...]              # BFS 层序 + 层内字典序
    hop: int
    digest: str
    truncated_files: int = 0
    truncated_lines: int = 0


@dataclass(frozen=True)
class NameGraph:
    """名称绑定图（D37；构建一次、多 anchor 复用）。

    - call_edges: caller=(path, def_name) → callee=(path, def_name)；
    - import_edges: 文件 → 文件（双向查询由调用方展开）。
    """

    call_edges: tuple[tuple[tuple[str, str], tuple[str, str]], ...]
    import_edges: tuple[tuple[str, str], ...]
    defs_by_name: dict[str, tuple[tuple[str, int, int], ...]]   # name → [(path, start, end)]
    def_of_line: dict[str, tuple[tuple[int, int, str], ...]]    # path → [(start, end, name)]


def build_name_graph(
    entries: list[IndexEntry], sources: dict[str, str]
) -> NameGraph:
    """IndexEntry 集 + 源文 → 名称绑定图（纯函数；entries 顺序无关，输出规范化）。

    ``sources`` = path → 源文本（import 正则面）；缺文件的 path 按无 import 边计。"""
    defs_by_name: dict[str, list[tuple[str, int, int]]] = {}
    def_of_line: dict[str, list[tuple[int, int, str]]] = {}
    for e in entries:
        for name, start, end in e.defs:
            defs_by_name.setdefault(name, []).append((e.path, start, end))
            def_of_line.setdefault(e.path, []).append((start, end, name))
    for k in defs_by_name:
        defs_by_name[k].sort()
    for k in def_of_line:
        def_of_line[k].sort()

    path_by_stem: dict[str, list[str]] = {}
    path_by_mod: dict[str, str] = {}
    for e in entries:
        p = PurePosixPath(e.path)
        path_by_stem.setdefault(p.stem, []).append(e.path)
        if p.suffix == ".py" and len(p.parts) > 1:
            path_by_mod[".".join((*p.parts[:-1], p.stem))] = e.path

    call_edges: set[tuple[tuple[str, str], tuple[str, str]]] = set()
    import_edges: set[tuple[str, str]] = set()
    for e in entries:
        own_defs = {n for n, _, _ in e.defs}
        # import 边：正则命中的模块名 → 同仓文件（尾段或点路径匹配；对不上丢弃）
        own_sources = sources.get(e.path)
        if own_sources is not None:
            for rx in _IMPORT_RES:
                for mod in rx.findall(own_sources):
                    target = path_by_mod.get(mod)
                    if target is None:
                        stem = mod.rsplit(".", 1)[-1]
                        cands = path_by_stem.get(stem, ())
                        if len(cands) == 1:
                            target = cands[0]
                    if target is not None and target != e.path:
                        import_edges.add((e.path, target))
        # 调用边：同文件 def 优先；否则全仓唯一定义成边；歧义/缺失丢弃。
        # 图基座 = all_calls 全量调用点名（D37）；e.calls 是线索命中稀疏子集
        # （通道 A 语义），做邻域图会漏普通调用。
        for callee, line in e.all_calls:
            caller = _enclosing_def(def_of_line.get(e.path, ()), line)
            src = (e.path, caller)
            if callee in own_defs:
                call_edges.add((src, (e.path, callee)))
                continue
            targets = defs_by_name.get(callee, ())
            if len(targets) == 1:
                call_edges.add((src, (targets[0][0], callee)))
    return NameGraph(
        call_edges=tuple(sorted(call_edges)),
        import_edges=tuple(sorted(import_edges)),
        defs_by_name={k: tuple(v) for k, v in sorted(defs_by_name.items())},
        def_of_line={k: tuple(v) for k, v in sorted(def_of_line.items())},
    )


def _enclosing_def(
    spans: tuple[tuple[int, int, str], ...], line: int
) -> str:
    """行所属 def 名（起止行包含；顶层 → MODULE_NODE）。spans 已按 start 排序。"""
    best = MODULE_NODE
    for start, end, name in spans:
        if start <= line <= end:
            best = name
            break
        if start > line:
            break
    return best


# ── 邻域切片（SPEC-8 §1.2）───────────────────────────────────────

def slice_for(
    anchors: list[tuple[str, int]],
    graph: NameGraph,
    entries: dict[str, IndexEntry],
    *,
    hop: int,
    max_files: int,
    max_lines: int,
    sources: dict[str, str] | None = None,
) -> RepoSlice:
    """候选点 ±hop 邻域切片（纯函数确定性；SPEC-8 §1.2 逐字）。

    - BFS：调用图双向 + import 图双向；文件收集序 = BFS 层序 + 层内字典序，
      max_files 截断（种子文件恒在且永远占序）；
    - 每文件载荷 = 路径经过的 def 全文 ∪ 种子所在 def ∪ 文件头 import 段；
      无 def 命中 → 整文件截 max_lines；
    - sources：文件源文（缺省用模块注册表）；载荷行从源文切。
    """
    if not anchors:
        return RepoSlice(files=(), hop=hop, digest=_digest(()),
                         truncated_files=0, truncated_lines=0)
    src = sources
    seed_files = sorted({a[0] for a in anchors})
    seen: set[str] = set()
    layers: list[list[str]] = []
    frontier = list(seed_files)
    for f in seed_files:
        seen.add(f)
    layers.append(sorted(frontier))
    depth = 0
    while frontier and depth < hop:
        nxt: set[str] = set()
        for f in frontier:
            nxt |= _neighbors(f, graph)
        frontier = sorted(n for n in nxt if n not in seen)
        for n in frontier:
            seen.add(n)
        if frontier:
            layers.append(frontier)
        depth += 1

    ordered: list[str] = []
    for layer in layers:
        for f in layer:
            if f not in ordered:
                ordered.append(f)
    # 种子文件恒在：无论如何保留（BFS 第一层即种子，截断只可能波及后续层）
    ordered = ordered[:max_files]
    truncated_files = len([1 for layer in layers for _ in layer]) - len(ordered)

    # 需要保留的 def：种子/路径经过节点所在 def 名集合（按文件聚合）
    keep_defs: dict[str, set[str]] = {f: set() for f in ordered}
    wanted_nodes = _nodes_on_paths(anchors, graph, hop)
    for path, name in wanted_nodes:
        if path in keep_defs:
            if name != MODULE_NODE:
                keep_defs[path].add(name)
            else:
                keep_defs[path].add(MODULE_NODE)

    slice_files: list[SliceFile] = []
    trunc_lines = 0
    for f in ordered:
        entry = entries.get(f)
        lang = entry.lang if entry is not None else ""
        lines = src.get(f, "").splitlines()
        total = len(lines)
        spans: list[tuple[int, int]] = []
        whole = False
        if entry is None or not entry.defs or MODULE_NODE in keep_defs.get(f, set()):
            whole = True
        for name, start, end in (entry.defs if entry is not None else ()):
            if not whole and name in keep_defs.get(f, set()):
                spans.append((start, end))
        if not spans and not whole:
            whole = True                      # 路径未经过任何 def → 整文件兜底
        if whole:
            spans = [(1, total)]
        # 并包络（重叠/相邻合并）+ import 段（1..最后一个 import 行）
        spans = _merge_spans(sorted(spans))
        imp_end = _import_end(lines)
        if imp_end and all(s > imp_end for s, _ in spans):
            spans = [(1, imp_end), *spans]
        # max_lines 截断
        f_trunc = False
        budget = max_lines
        kept: list[tuple[int, int]] = []
        for s, e in spans:
            if budget <= 0:
                f_trunc = True
                break
            take = min(e, s + budget - 1)
            kept.append((s, take))
            budget -= take - s + 1
            if take < e:
                f_trunc = True
                break
        trunc_lines += sum(e - s + 1 for s, e in kept)
        slice_files.append(
            SliceFile(path=f, lang=lang, spans=tuple(kept), truncated=f_trunc)
        )

    return RepoSlice(
        files=tuple(slice_files),
        hop=hop,
        digest=_digest(slice_files),
        truncated_files=max(truncated_files, 0)
        + sum(1 for sf in slice_files if sf.truncated),
        truncated_lines=trunc_lines,
    )


def _neighbors(path: str, graph: NameGraph) -> set[str]:
    out: set[str] = set()
    for (pa, _na), (pb, _nb) in graph.call_edges:
        if pa == path:
            out.add(pb)
        if pb == path:
            out.add(pa)
    for a, b in graph.import_edges:
        if a == path:
            out.add(b)
        if b == path:
            out.add(a)
    return out


def _nodes_on_paths(
    anchors: list[tuple[str, int]], graph: NameGraph, hop: int
) -> set[tuple[str, str]]:
    """种子与 BFS 路径经过的图节点（(path, def_name) 面；供 keep_defs 聚合）。"""
    seed_nodes: set[tuple[str, str]] = set()
    by_path: dict[str, list[tuple[int, int, str]]] = {}
    for p, spans in graph.def_of_line.items():
        by_path[p] = list(spans)
    for path, line in anchors:
        spans = sorted(by_path.get(path, ()))
        name = MODULE_NODE
        for start, end, n in spans:
            if start <= line <= end:
                name = n
                break
            if start > line:
                break
        seed_nodes.add((path, name))
    frontier = {(p, n) for p, n in seed_nodes}
    seen = set(frontier)
    depth = 0
    while frontier and depth < hop:
        nxt: set[tuple[str, str]] = set()
        for (pa, na), (pb, nb) in graph.call_edges:
            if (pa, na) in frontier:
                nxt.add((pb, nb))
            if (pb, nb) in frontier:
                nxt.add((pa, na))
        frontier = {n for n in nxt if n not in seen}
        seen |= frontier
        depth += 1
    return seen


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[list[int]] = []
    for s, e in spans:
        if out and s <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(s, e) for s, e in out]


def _import_end(lines: list[str]) -> int:
    last = 0
    for i, line in enumerate(lines, start=1):
        if any(rx.match(line + "\n") for rx in _IMPORT_RES):
            last = i
    return last


def _digest(files: tuple[SliceFile, ...]) -> str:
    h = blake2b(digest_size=16)
    for sf in files:
        h.update(sf.path.encode())
        for s, e in sf.spans:
            h.update(f"{s}-{e}".encode())
    return h.hexdigest()


def slice_to_json(slice_: RepoSlice) -> str:
    """JSON 序列化（工件面；字段与 RepoSlice 一一对应）。"""
    import json

    return json.dumps(
        {
            "hop": slice_.hop,
            "digest": slice_.digest,
            "truncated_files": slice_.truncated_files,
            "truncated_lines": slice_.truncated_lines,
            "files": [
                {
                    "path": f.path,
                    "lang": f.lang,
                    "spans": [list(s) for s in f.spans],
                    "truncated": f.truncated,
                }
                for f in slice_.files
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
