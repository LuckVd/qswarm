"""perception.indexer 测试（SPEC-7 §1.2；M3 卷 A 组 §1.2/§1.3 判据）。

钉死：tier-1 调用点精确收集（clues 表命中 sink/source/serialization）、
无 grammar 语言通道 A 缺席（parse_ok=False 显式承载）、内容寻址缓存跳过
（内容未变解析计数不变、内容变重析）、cache_key 内容定址。
"""

from __future__ import annotations

import pytest

from qswarm.perception.indexer import (
    IndexCache,
    lang_for_path,
    parse_index,
    probe_languages,
)
from qswarm.perception.clues import match_name

PY_SRC = b'''import os


def run(cmd):
    os.system(cmd)
    subprocess.call(cmd, shell=True)


def load(data):
    return pickle.loads(data)
'''

C_SRC = b'''#include <string.h>
void copy(char *dst, const char *src) {
    strcpy(dst, src);
}
'''


def calls_of(entry, name=None):
    return [(n, line, hits) for n, line, hits in entry.calls if name is None or n == name]


def test_probe_languages_discovers_installed_grammars():
    """探测式分级（D25）：已装六门 grammar 全部发现；键 = 语言名（非模块名）。"""
    langs = probe_languages()
    assert {"python", "javascript", "go", "java", "c", "cpp"} <= set(langs)
    for lang, module in langs.items():
        assert module.__name__.startswith("tree_sitter_")
        assert hasattr(module, "language")


def test_lang_for_path_routing():
    assert lang_for_path("a/b.py") == "python"
    assert lang_for_path("x.CPP") == "cpp"
    assert lang_for_path("notes.md") is None
    assert lang_for_path("main.go") == "go"


def test_channel_a_tier1_callpoints():
    """M3 卷 §1.2：tier-1 语言样例收出 sink/serialization 调用点（clues 命中）。"""
    entry = parse_index("src/api.py", "python", PY_SRC)
    assert entry.parse_ok is True
    system_hits = calls_of(entry, "system")
    assert len(system_hits) == 1
    name, line, hits = system_hits[0]
    assert line == 5                                 # 1-based
    assert ("injection", "sink") in hits
    subprocess_hits = calls_of(entry, "subprocess_call")
    assert subprocess_hits and subprocess_hits[0][1] == 6
    loads = calls_of(entry, "pickle_loads")
    assert loads and loads[0][1] == 10
    assert loads[0][2] == (("deser", "serialization"),)
    # 函数定义在位（通道 B 函数边界分片 / pr 档邻域消费面）
    assert ("run", 4, 6) in entry.defs
    assert ("load", 9, 10) in entry.defs


def test_channel_a_identifiers_match_source_clues():
    """标识符档命中 source/危险名（sink 档不走标识符防同位双计）。"""
    src = b'password = "x"\nuser_password = "y"\n'
    entry = parse_index("a.py", "python", src)
    idents = [(n, line, tuple(hits)) for n, line, hits in entry.idents]
    assert any("password" == n for n, _l, _h in idents)
    for _n, _line, hits in idents:
        for family, kind in hits:
            assert kind in ("source", "dangerous_call", "serialization")


def test_channel_a_no_grammar_absent():
    """M3 卷 §1.2：无 grammar 语言通道 A 缺席（parse_ok=False 空索引），不抛错。"""
    entry = parse_index("x.rb", "ruby", b"def f\n  system('x')\nend\n")
    assert entry.parse_ok is False
    assert entry.calls == () and entry.idents == () and entry.defs == ()
    # 未知扩展名（无语言路由）同样缺席
    assert lang_for_path("x.rb") is None


def test_c_cpp_callpoints():
    """c 语言调用点：strcpy → mem_safety/dangerous_call；定义恰一条（declarator 不双计）。"""
    entry = parse_index("src/util.c", "c", C_SRC)
    assert entry.parse_ok is True
    hits = calls_of(entry, "strcpy")
    assert hits and hits[0][2] == (("mem_safety", "dangerous_call"),)
    assert hits[0][1] == 3
    assert entry.defs == (("copy", 2, 4),)


def test_index_content_hash_skip():
    """M3 卷 §1.3：文件未变走缓存（解析计数不变），内容变重析。"""
    cache = IndexCache()
    e1 = cache.get_or_parse("a.py", "python", PY_SRC)
    assert cache.parse_count == 1
    e2 = cache.get_or_parse("a.py", "python", PY_SRC)
    assert cache.parse_count == 1                    # 未变不重析
    assert e2.to_json() == e1.to_json()
    e3 = cache.get_or_parse("a.py", "python", PY_SRC + b"\n# tail\n")
    assert cache.parse_count == 2                    # 内容变重析
    assert e3.parse_ok is True


def test_index_cache_dir_roundtrip_and_corruption(tmp_path):
    """盘上缓存往返：命中零解析；损坏缓存 fail-open 重析（内容寻址保证一致）。"""
    cache = IndexCache(tmp_path)
    e1 = cache.get_or_parse("a.py", "python", PY_SRC)
    assert cache.parse_count == 1
    cache2 = IndexCache(tmp_path)                    # 新实例同目录 → 命中
    e2 = cache2.get_or_parse("a.py", "python", PY_SRC)
    assert cache2.parse_count == 0
    assert e2.to_json() == e1.to_json()
    # 损坏缓存 → 新实例重析（零抛错；内容寻址保证重建一致）
    key = cache2.file_key(PY_SRC)
    (tmp_path / f"{key}.json").write_text("{broken", encoding="utf-8")
    cache3 = IndexCache(tmp_path)
    e3 = cache3.get_or_parse("a.py", "python", PY_SRC)
    assert cache3.parse_count == 1
    assert e3.to_json() == e1.to_json()


def test_clue_match_shape():
    """clues 匹配形态：全等匹配、跨家族命中、未命中空、非小写输入不保证命中。"""
    assert match_name("system") == (("injection", "sink"),)
    assert match_name("password") == (
        ("config_dep", "source"), ("crypto", "source"),
    )
    assert match_name("not_a_sink") == ()
    assert match_name("pickle_loads") == (("deser", "serialization"),)


@pytest.mark.parametrize("name", ["strcpy", "gets", "sprintf", "memcpy"])
def test_mem_safety_vocab_frozen(name):
    """v1 词表锚点：mem_safety 四经典函数恒在（允许扩充不收缩的收缩侧红）。"""
    assert any(f == "mem_safety" and k == "dangerous_call" for f, k in match_name(name))
