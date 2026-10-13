"""perception.indexer：tree-sitter 探测分级 + 调用点索引 + 内容寻址缓存（SPEC-7 §1.2）。

语言分级（D25 探测式）：运行时探测 grammar 可用性（import 成功即用）——
- tier-1：官方六门 grammar（python/js/go/java/c/c++）装通 → 调用点/标识符精确收集；
- tier-2：其余任何 ``tree-sitter-*`` 发行版装通 → 同机（泛化节点名策略）；
- 无 grammar → 该语言**通道 A 缺席**（显式计数 ``channel_a_absent``，通道 B 兜底）。
实现不硬编码语言清单：可探测集 = 运行时已装的 ``tree_sitter_*`` 模块按机械规则
（发行版名 ``-``→``_``）反推，不维护语言白名单。

索引（逐文件）：解析产出 ``(调用点, 标识符, 函数定义)`` 三档条目——
- 调用点：call/call_expression/method_invocation 节点的被调名（属性调用取尾段
  标识符，如 ``os.system`` → ``system``）+ 行号 + 命中 (family, kind)（clues 表）；
- 标识符：identifier 节点名命中 clues（source/danger 档；
  调用点已覆盖的 sink/serialization 档在调用侧匹配，避免同位点双计）；
- 函数定义：函数名 + 行区间（通道 B 函数边界分片与 pr 档 ±1 调用邻域消费）。

缓存（§2.1 逐字）：``key = blake2b(file_bytes)``，未变不重析——
``IndexCache`` 盘上 JSON（``<key>.json``）承载解析结果，命中即零解析（测试以
解析计数断言）；缓存键内容寻址 ⇒ 缓存永不给出过期结果。
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path

from .clues import match_name

__all__ = [
    "IndexEntry",
    "IndexCache",
    "probe_languages",
    "lang_for_path",
    "index_file",
    "index_repo",
]

#: 扩展名 → 语言名（文件路由用；语言是否可用仍以 probe 为准）。
_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "javascript", ".ts": "javascript", ".tsx": "javascript",
    ".go": "go",
    ".java": "java",
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
}

#: 调用节点类型（跨语言泛化集；tier-2 语言按同名泛化命中，不识别即缺席该语言
#: 的调用点档、仅剩标识符档——缺席语义显式、不静默）。
_CALL_NODE_TYPES = frozenset(
    {"call", "call_expression", "method_invocation", "new_expression"}
)

#: 函数定义节点类型（跨语言泛化集；不含 function_declarator——C 的 definition
#: 节点经 declarator 字段取名，声明子节点并入会双计）。
_DEF_NODE_TYPES = frozenset(
    {
        "function_definition", "function_declaration", "method_definition",
        "function_item", "method_declaration", "constructor_declaration",
    }
)

#: 标识符档只消费 source/dangerous/serialization 命中（sink 档走调用点，防同位双计）。
_IDENT_KINDS = frozenset({"source", "dangerous_call", "serialization"})


@dataclass(frozen=True)
class IndexEntry:
    """单文件索引（解析产物；JSON 可序列化）。"""

    path: str
    lang: str
    calls: tuple[tuple[str, int, tuple[tuple[str, str], ...]], ...]
    # ^ (被调名, 1-based 行, 命中[(family, kind), ...])——线索词表命中面（通道 A）
    idents: tuple[tuple[str, int, tuple[tuple[str, str], ...]], ...]
    defs: tuple[tuple[str, int, int], ...]        # (函数名, 起行, 止行)
    all_calls: tuple[tuple[str, int], ...] = ()   # 全量调用点名（M4/D37 repo map
    #   名称绑定图地基；缺省 () 保 M3 缓存 JSON 向后兼容——from_json 容缺失）
    parse_ok: bool = True                         # 无 grammar/解析失败 = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "path": self.path,
                "lang": self.lang,
                "calls": [list(c) for c in self.calls],
                "idents": [list(i) for i in self.idents],
                "defs": [list(d) for d in self.defs],
                "all_calls": [list(c) for c in self.all_calls],
                "parse_ok": self.parse_ok,
            },
            ensure_ascii=False, sort_keys=True,
        )

    @staticmethod
    def from_json(raw: str) -> "IndexEntry":
        data = json.loads(raw)
        return IndexEntry(
            path=data["path"],
            lang=data["lang"],
            calls=tuple(
                (name, int(line), tuple((f, k) for f, k in hits))
                for name, line, hits in data["calls"]
            ),
            idents=tuple(
                (name, int(line), tuple((f, k) for f, k in hits))
                for name, line, hits in data["idents"]
            ),
            defs=tuple((str(n), int(a), int(b)) for n, a, b in data["defs"]),
            all_calls=tuple(
                (str(n), int(line)) for n, line in data.get("all_calls", ())
            ),
            parse_ok=bool(data.get("parse_ok", True)),
        )


# ── 语言探测（D25：运行时探测，不硬编码清单）──────────────────────

_LANG_PROBE: dict[str, object | None] = {}


def _dist_language_name(dist_name: str) -> str | None:
    """发行版名 → 语言名（机械规则：tree-sitter-X → X；非 language 包 → None）。"""
    if not dist_name.startswith("tree-sitter-"):
        return None
    tail = dist_name[len("tree-sitter-"):]
    # 官方 grammar 包名恰为单段语言名；带第二段的（如 -with-source）非语言包。
    if not tail or "-" in tail or "." in tail or tail in ("core",):
        return None
    return tail


def probe_languages() -> dict[str, str]:
    """已装 tree-sitter 语言包 → {语言名: 模块名}（运行时探测；结果进程内缓存）。

    语言名 = 发行版名 ``tree-sitter-<lang>`` 的 ``<lang>`` 段（机械规则反推，
    不维护语言白名单）；``hasattr(module, "language")`` 在位才计入（入口函数
    是 grammar 加载的唯一消费面）。"""
    if _LANG_PROBE:
        return {k: v for k, v in _LANG_PROBE.items() if v is not None}  # type: ignore[misc]
    for dist in importlib.metadata.distributions():
        lang = _dist_language_name((dist.metadata or {}).get("Name", ""))
        if lang is None:
            continue
        module = "tree_sitter_" + lang.replace("-", "_").replace(".", "_")
        if lang in _LANG_PROBE:
            continue
        try:
            imported = importlib.import_module(module)
            if not hasattr(imported, "language"):
                raise AttributeError(module)
            _LANG_PROBE[lang] = imported
        except (ImportError, AttributeError):
            _LANG_PROBE[lang] = None
    return {k: v for k, v in _LANG_PROBE.items() if v is not None}  # type: ignore[misc]


def _load_parser(lang: str):
    """语言 → Parser（探测不可用 → None；加载失败按缺席计，不抛）。"""
    mods = probe_languages()
    module = mods.get(lang)
    if module is None:
        return None
    try:
        from tree_sitter import Language, Parser

        ts_lang = Language(module.language())   # tree-sitter ≥0.23 单参形态
        return Parser(ts_lang)
    except Exception:                            # noqa: BLE001 — 缺席语义显式计数
        return None


def lang_for_path(path: str) -> str | None:
    """路径 → 语言名（扩展名路由；未知扩展 → None = 通道 A 缺席）。"""
    suffix = Path(path).suffix.lower()
    return _EXT_LANG.get(suffix)


# ── 解析与线索匹配 ────────────────────────────────────────────────

def _call_func_name(node) -> str | None:
    """调用节点 → 被调名文本（无词表过滤；M4/D37 全量调用点名面）。

    function/name 字段优先，兜底首个标识符/属性类子节点；取不出 → None。"""
    func = node.child_by_field_name("function") or node.child_by_field_name("name")
    if func is None:
        for child in node.children:
            if child.type in ("identifier", "attribute", "field_expression",
                              "member_access_function", "qualified_identifier"):
                func = child
                break
    if func is None:
        return None
    text = (func.text or b"").decode("utf-8", "replace").strip("()[] \t\r\n")
    return text or None


def _call_name_hits(node) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    """调用节点 → (匹配名, 命中)：尾段标识符与分隔符归一全名两档匹配（取先命中）。

    - 尾段：``os.system`` → ``system``（命中 sinks 词表短名）；
    - 全名归一：分隔符 ::/->/./: → ``_``：``pickle.loads`` → ``pickle_loads``
      （命中 serialization 词表复合名）；
    - 两档皆不命中 → None。"""
    text = _call_func_name(node)
    if text is None:
        return None
    tail = text
    for sep in ("::", "->", ".", ":"):
        if sep in tail:
            tail = tail.rsplit(sep, 1)[-1]
    normalized = text
    for sep in ("::", "->", ".", ":"):
        normalized = normalized.replace(sep, "_")
    for candidate in (tail.casefold(), normalized.casefold()):
        hits = match_name(candidate)
        if hits:
            return candidate, hits
    return None


def _def_name(node) -> str | None:
    """定义节点 → 函数名（field name 兜底首个 identifier 子节点）。"""
    for field_name in ("name", "declarator"):
        child = node.child_by_field_name(field_name)
        if child is not None:
            if child.type == "identifier":
                return (child.text or b"").decode("utf-8", "replace")
            for sub in ([child, *child.children]):
                if sub.type == "identifier":
                    return (sub.text or b"").decode("utf-8", "replace")
    for child in node.children:
        if child.type == "identifier":
            return (child.text or b"").decode("utf-8", "replace")
    return None


def _walk(node, sink):
    """先序遍历（迭代式，防深递归）。"""
    stack = [node]
    while stack:
        cur = stack.pop()
        sink(cur)
        for child in reversed(cur.children):
            stack.append(child)


def parse_index(path: str, lang: str, source: bytes) -> IndexEntry:
    """单文件解析 → IndexEntry（无 grammar → parse_ok=False 空索引，缺席语义）。"""
    parser = _load_parser(lang)
    if parser is None:
        return IndexEntry(path=path, lang=lang, calls=(), idents=(), defs=(), all_calls=(), parse_ok=False)
    try:
        tree = parser.parse(source)
    except Exception:                            # noqa: BLE001 — 坏文件按缺席计
        return IndexEntry(path=path, lang=lang, calls=(), idents=(), defs=(), all_calls=(), parse_ok=False)
    calls: list[tuple[str, int, tuple[tuple[str, str], ...]]] = []
    idents: list[tuple[str, int, tuple[tuple[str, str], ...]]] = []
    defs: list[tuple[str, int, int]] = []
    all_calls: list[tuple[str, int]] = []

    def visit(node) -> None:
        ntype = node.type
        if ntype in _CALL_NODE_TYPES:
            name = _call_func_name(node)
            if name:
                all_calls.append((name, node.start_point[0] + 1))
            hit = _call_name_hits(node)
            if hit is not None:
                name, hits = hit
                calls.append((name, node.start_point[0] + 1, hits))
            return
        if ntype in _DEF_NODE_TYPES:
            name = _def_name(node)
            if name:
                defs.append((name, node.start_point[0] + 1, node.end_point[0] + 1))
            return
        if ntype == "identifier":
            name = (node.text or b"").decode("utf-8", "replace")
            hits = tuple((f, k) for f, k in match_name(name.casefold()) if k in _IDENT_KINDS)
            if hits:
                idents.append((name, node.start_point[0] + 1, hits))

    _walk(tree.root_node, visit)
    return IndexEntry(
        path=path,
        lang=lang,
        calls=tuple(calls),
        idents=tuple(idents),
        defs=tuple(sorted(defs)),
        all_calls=tuple(all_calls),
        parse_ok=True,
    )


# ── 内容寻址缓存（§2.1：key=blake2b(file_bytes)，未变不重析）─────────

class IndexCache:
    """盘上解析缓存：<cache_dir>/<key>.json（key = blake2b(file_bytes) hex）。

    - ``get_or_parse``：命中 → (entry, parse_count=0)；未中 → 解析并落缓存；
      返回解析计数供测试断言「内容未变不重析」（时间断言的确定性替身）；
    - 缓存损坏（陈旧 JSON/半写）→ 视为未命中重析（fail-open 重建，内容寻址
      保证重建结果与首析逐位一致）。"""

    def __init__(self, cache_dir: str | Path | None = None) -> None:
        self._dir = Path(cache_dir) if cache_dir is not None else None
        # 进程内 memo：同 key 同对象（同仓库两跑同进程场景零重复解析）。
        self._memo: dict[str, IndexEntry] = {}
        # 累计解析计数（测试面：缓存跳过断言）。
        self.parse_count = 0

    def file_key(self, source: bytes) -> str:
        h = hashlib.blake2b(digest_size=16)
        h.update(source)
        return h.hexdigest()

    def get_or_parse(self, path: str, lang: str, source: bytes) -> IndexEntry:
        key = self.file_key(source)
        memo = self._memo.get(key)
        if memo is not None:
            return memo
        if self._dir is not None:
            cache_file = self._dir / f"{key}.json"
            if cache_file.exists():
                try:
                    entry = IndexEntry.from_json(cache_file.read_text(encoding="utf-8"))
                    self._memo[key] = entry
                    return entry
                except (ValueError, KeyError, TypeError):
                    pass                     # 损坏缓存 → 重析（见类注）
        self.parse_count += 1
        entry = parse_index(path, lang, source)
        if self._dir is not None:
            cache_file = self._dir / f"{key}.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(entry.to_json(), encoding="utf-8")
        self._memo[key] = entry
        return entry


def index_repo(
    files: list[tuple[str, bytes]],
    *,
    cache: IndexCache | None = None,
) -> list[IndexEntry]:
    """逐文件索引（files = (repo 相对路径, 内容) 列表；调用方保证已字典序）。"""
    cache = cache if cache is not None else IndexCache()
    out: list[IndexEntry] = []
    for path, source in files:
        lang = lang_for_path(path)
        if lang is None:
            out.append(IndexEntry(path=path, lang="", calls=(), idents=(), defs=(), all_calls=(), parse_ok=False))
            continue
        out.append(cache.get_or_parse(path, lang, source))
    return out
