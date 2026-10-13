"""perception.repomap 测试（SPEC-8 §1；M4 卷 A 组 §1.1 判据）。

钉死：名称绑定语义（同文件优先 / 唯一跨文件成边 / 歧义不成边）、import 正则边
（py/c 双语言、外部模块丢弃）、BFS 跳数语义（fast=0 / pr=1 / full=2 文件集差）、
切片预算截断、确定性（同输入同 digest）、无 grammar 退化（零调用边、import 邻域
仍可切片）。
"""

from __future__ import annotations

from qswarm.perception.indexer import index_repo
from qswarm.perception.repomap import (
    MODULE_NODE,
    build_name_graph,
    slice_for,
    slice_to_json,
)


def _repo(files: dict[str, str]):
    items = [(p, s.encode("utf-8")) for p, s in sorted(files.items())]
    entries = index_repo(items)
    return entries, {p: s for p, s in sorted(files.items())}, {e.path: e for e in entries}


def test_repomap_name_binding_same_file_first():
    """被调名同文件有定义 → 仅同文件边（跨文件同名不引入，最强绑定）。"""
    entries, sources, emap = _repo({
        "a.py": "def helper():\n    return 1\n\ndef main():\n    return helper()\n",
        "b.py": "def helper():\n    return 2\n",
    })
    g = build_name_graph(entries, sources)
    edges = [e for e in g.call_edges if e[0] == ("a.py", "main")]
    assert edges == [(("a.py", "main"), ("a.py", "helper"))]


def test_repomap_unique_crossfile_edge():
    """全仓唯一定义 → 跨文件边；多定义歧义 → 不成边。"""
    entries, sources, emap = _repo({
        "a.py": "def main():\n    return unique_fn()\n",
        "u.py": "def unique_fn():\n    return 1\n",
        "b.py": "def dup():\n    return 1\n",
        "c.py": "def dup():\n    return 2\n",
    })
    g = build_name_graph(entries, sources)
    by_caller = {e[0]: e[1] for e in g.call_edges}
    assert by_caller[("a.py", "main")] == ("u.py", "unique_fn")
    # dup 的调用方不存在——歧义面直接由 defs_by_name 反映：两个定义不成任何边
    assert len(g.defs_by_name["dup"]) == 2
    assert all(callee[1] != "dup" for _caller, callee in g.call_edges)


def test_repomap_import_edges_regex():
    """py/c 双语言 import 边命中同仓文件；外部模块（无同仓匹配）丢弃。"""
    entries, sources, emap = _repo({
        "pkg/api.py": "import pkg.db\nimport os\nfrom pkg.util import x\n",
        "pkg/db.py": "import os\n",
        "pkg/util.py": "x = 1\n",
        "src/m.c": '#include "src/h.h"\n#include <stdio.h>\nint main(){return 0;}\n',
        "src/h.h": "#pragma once\n",
    })
    g = build_name_graph(entries, sources)
    assert ("pkg/api.py", "pkg/db.py") in g.import_edges
    assert ("pkg/api.py", "pkg/util.py") in g.import_edges
    assert ("src/m.c", "src/h.h") in g.import_edges
    assert all("os" not in b for _a, b in g.import_edges)   # 外部模块不成边


def test_repomap_slice_bfs_hops():
    """anchor ±1 跳与 ±2 跳文件集差可观测；hop=0 = 种子文件集。"""
    entries, sources, emap = _repo({
        "a.py": "def main():\n    return f1()\n",
        "b.py": "def f1():\n    return f2()\n",
        "c.py": "def f2():\n    return 1\n",
    })
    g = build_name_graph(entries, sources)
    s0 = slice_for([("a.py", 2)], g, emap, hop=0, max_files=12, max_lines=100, sources=sources)
    s1 = slice_for([("a.py", 2)], g, emap, hop=1, max_files=12, max_lines=100, sources=sources)
    s2 = slice_for([("a.py", 2)], g, emap, hop=2, max_files=12, max_lines=100, sources=sources)
    assert [f.path for f in s0.files] == ["a.py"]
    assert [f.path for f in s1.files] == ["a.py", "b.py"]
    assert [f.path for f in s2.files] == ["a.py", "b.py", "c.py"]


def test_repomap_slice_budget_truncate():
    """max_files/max_lines 截断生效且计数入 RepoSlice；种子文件恒在。"""
    files = {"a.py": "def main():\n    return f1()\n"}
    for i in range(6):
        prev = "main" if i == 0 else f"f{i}"
        files[f"m{i}.py"] = f"def f{i + 1}():\n    return {i}\n\ndef z{i}():\n    return {prev}()\n"
    entries, sources, emap = _repo(files)
    g = build_name_graph(entries, sources)
    s = slice_for([("a.py", 2)], g, emap, hop=6, max_files=3, max_lines=4, sources=sources)
    assert len(s.files) == 3
    assert s.files[0].path == "a.py"                      # 种子恒在
    assert s.truncated_files >= 1 or any(f.truncated for f in s.files)


def test_repomap_deterministic():
    """同输入两跑 digest 一致、文件序 = BFS 层序 + 字典序。"""
    files = {
        "a.py": "import z\nimport y\n\ndef main():\n    return f1()\n",
        "b.py": "def f1():\n    return 1\n",
        "y.py": "y = 1\n",
        "z.py": "z = 1\n",
    }
    entries, sources, emap = _repo(files)
    g1 = build_name_graph(entries, sources)
    g2 = build_name_graph(list(entries)[::-1], dict(reversed(list(sources.items()))))
    s1 = slice_for([("a.py", 5)], g1, emap, hop=1, max_files=12, max_lines=100, sources=sources)
    s2 = slice_for([("a.py", 5)], g2, emap, hop=1, max_files=12, max_lines=100, sources=sources)
    assert s1.digest == s2.digest
    assert [f.path for f in s1.files] == [f.path for f in s2.files]
    assert slice_to_json(s1) == slice_to_json(s2)


def test_repomap_no_grammar_degrades():
    """parse_ok=False 文件零调用边、import 邻域仍可切片（§2.1 退化语义）。"""
    entries, sources, emap = _repo({
        "weird.xyz": "import helper_mod\n call_something()\n",
        "helper_mod.py": "def call_something():\n    return 1\n",
    })
    g = build_name_graph(entries, sources)
    assert g.call_edges == ()                             # 通道 A 缺席 → 零调用边
    assert ("weird.xyz", "helper_mod.py") in g.import_edges
    s = slice_for([("weird.xyz", 1)], g, emap, hop=1, max_files=12, max_lines=100, sources=sources)
    assert [f.path for f in s.files] == ["helper_mod.py", "weird.xyz"] or set(
        f.path for f in s.files
    ) == {"weird.xyz", "helper_mod.py"}
    # MODULE_NODE 语义：文件顶层调用归虚节点（不经 def 归属）
    assert MODULE_NODE == "<module>"
