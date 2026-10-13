"""perception.candidates 测试（SPEC-7 §1.1/§1.5；M3 卷 A 组 §1.1 最低用例集）。

钉死：span 重叠 ≤5 行同 kind 合并（channels 并集/priority 递增/span 并集/clue
联接/id 重算）、(path, span) 字典序确定性（乱序输入两跑一致）、内容寻址 id
（同内容同 id、内容变 id 变）、kind/通道词表拒绝、C 通道专属 +1 优先级。
"""

from __future__ import annotations

import pytest

from qswarm.perception import (
    CANDIDATE_KINDS,
    MERGE_GAP_LINES,
    Candidate,
    candidate_id,
    candidates_from_json,
    candidates_to_json,
    merge_candidates,
)


def cand(path, span, kind, clue, channels=("A",), hint=None) -> Candidate:
    return Candidate(
        path=path, span=span, kind=kind, clue=clue,
        channels=frozenset(channels), taxonomy_hint=hint,
    )


def test_candidate_merge_overlapping():
    """§1.5：重叠/≤5 行同 kind 合并——channels 并集、priority 递增、span 并集、
    clue 排序联接、candidate_id 重算；>5 行距与异 kind 不合并。"""
    a = cand("src/a.py", (10, 12), "sink", "system", ("A",), "injection")
    b = cand("src/a.py", (13, 15), "sink", "system", ("B",), "injection")
    merged = merge_candidates([a, b])
    assert len(merged) == 1
    m = merged[0]
    assert m.span == (10, 15)                       # span 并集
    assert m.channels == frozenset({"A", "B"})      # 并集
    assert m.priority == 2                          # len(channels)
    assert m.candidate_id == candidate_id("src/a.py", (10, 15), "sink", "system")
    # 间距恰 = MERGE_GAP_LINES（≤5）仍合并
    gap = cand("src/a.py", (15 + MERGE_GAP_LINES, 15 + MERGE_GAP_LINES + 1), "sink", "system", ("C",), "injection")
    m2 = merge_candidates([m, gap])[0]
    assert m2.channels == frozenset({"A", "B", "C"})
    assert m2.priority == 4                         # len(3) + C 加成 1
    # 间距 >5 行不合并
    far = cand("src/a.py", (30, 31), "sink", "system", ("A",))
    assert len(merge_candidates([m2, far])) == 2
    # 异 kind 同位不合并（规则命中与结构命中并存，§1.5 同 kind 族语义）
    r = cand("src/a.py", (10, 12), "rule_hit", "qsw.injection.x", ("C",), "injection")
    assert len(merge_candidates([a, r])) == 2


def test_candidate_order_deterministic():
    """§1.1：乱序输入两跑产出 (path, span) 字典序一致；归并结果与输入序无关。"""
    cands = [
        cand("src/b.py", (3, 4), "source", "argv"),
        cand("src/a.py", (9, 9), "sink", "system", ("B",)),
        cand("src/a.py", (2, 3), "dangerous_call", "md5", ("A",)),
        cand("src/a.py", (2, 2), "dangerous_call", "md5"),          # 与上行重叠同 kind
        cand("src/c.py", (1, 2), "rule_hit", "qsw.crypto.weak", ("C",), "crypto"),
    ]
    r1 = merge_candidates(list(reversed(cands)))
    r2 = merge_candidates(cands)
    assert [c.candidate_id for c in r1] == [c.candidate_id for c in r2]
    keys = [(c.path, c.span) for c in r1]
    assert keys == sorted(keys)
    assert len(r1) == 4                              # a.py 两条 dangerous 折叠为 1


def test_candidate_id_content_addressed():
    """同内容同 id、内容变 id 变（blake2b 16B hex；cache_key 同型构造）。"""
    i1 = candidate_id("a.py", (1, 2), "sink", "system")
    i2 = candidate_id("a.py", (1, 2), "sink", "system")
    assert i1 == i2 and len(i1) == 32                # 16B hex
    assert candidate_id("a.py", (1, 3), "sink", "system") != i1
    assert candidate_id("b.py", (1, 2), "sink", "system") != i1
    assert candidate_id("a.py", (1, 2), "source", "system") != i1
    # 序列化往返保 id（工件库读取侧）
    c = cand("a.py", (1, 2), "sink", "system")
    assert candidates_from_json(candidates_to_json([c]))[0].candidate_id == c.candidate_id


def test_candidate_vocabulary_guards():
    """kind/通道越词表、空通道、非法 span 一律 ValueError（构造即拒）。"""
    with pytest.raises(ValueError, match="kind 越词表"):
        cand("a.py", (1, 1), "rogue", "x")
    with pytest.raises(ValueError, match="通道越词表"):
        cand("a.py", (1, 1), "sink", "x", ("A", "Z"))
    with pytest.raises(ValueError, match="channels 为空"):
        cand("a.py", (1, 1), "sink", "x", ())
    with pytest.raises(ValueError, match="span 非法"):
        cand("a.py", (0, 1), "sink", "x")
    assert "rule_hit" in CANDIDATE_KINDS and "model_flag" in CANDIDATE_KINDS
