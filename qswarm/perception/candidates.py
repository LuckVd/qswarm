"""perception.candidates：L0 候选契约与归并/优先级（SPEC-7 §1.1/§1.5）。

全链确定性纪律（§5-5）：同仓库内容 + 同参数 → 同候选集——
- ``candidate_id`` 内容寻址：blake2b(path, span, kind, clue) 16B hex（与
  exams.runner.cache_key 同型构造，禁内建 hash——跨 PYTHONHASHSEED 稳定）；
- 候选集排序 = ``(path, span)`` 字典序（稳定、显式，通道产出序不外溢）；
- 归并（§1.5）：同文件、同 kind、span 重叠或间距 ≤ 5 行 → 合并：channels 并集、
  span 取并集、clue 取排序去重联接、candidate_id 重算、
  ``priority = len(channels) + (1 if "C" in channels else 0)``。

本模块纯函数零 IO 零随机（§5-3 纯度纪律；测试以 import 钩子钉死）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

__all__ = [
    "CANDIDATE_KINDS",
    "L0_CHANNELS",
    "MERGE_GAP_LINES",
    "Candidate",
    "candidate_id",
    "merge_candidates",
    "candidates_to_json",
    "candidates_from_json",
]

#: kind 词表（SPEC-7 §1.1 逐字；越表 = 编程错误，构造即拒）。
CANDIDATE_KINDS: frozenset[str] = frozenset(
    {"source", "sink", "dangerous_call", "serialization", "rule_hit", "model_flag"}
)

#: 通道词表（SPEC-7 §1.1：channels ⊆ {"A","B","C"}）。
L0_CHANNELS: frozenset[str] = frozenset({"A", "B", "C"})

#: 归并行距（§1.5 逐字：span 重叠**或间距 ≤ 5 行**的同 kind 同文件候选合并）。
MERGE_GAP_LINES = 5


def candidate_id(path: str, span: tuple[int, int], kind: str, clue: str) -> str:
    """内容寻址 id（blake2b 16B hex；同内容恒同 id、内容变 id 变）。"""
    h = hashlib.blake2b(digest_size=16)
    for part in (path, f"{span[0]}-{span[1]}", kind, clue):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


@dataclass(frozen=True)
class Candidate:
    """L0 候选点（SPEC-7 §1.1 契约逐字；frozen 保哈希与集合可用性）。"""

    path: str                    # 仓库相对路径（posix，/ 分隔）
    span: tuple[int, int]        # 1-based 行闭区间 [start, end]
    kind: str                    # CANDIDATE_KINDS 内
    clue: str                    # 产出线索（sink 名/规则 id/模型 flag 说明）
    channels: frozenset[str] = field(default_factory=frozenset)  # ⊆ {"A","B","C"}
    taxonomy_hint: str | None = None   # 可选家族提示（仅供 L1 参考，不约束）
    candidate_id: str = ""       # 缺省按内容寻址重算（归并后显式重算传入）

    def __post_init__(self) -> None:
        if self.kind not in CANDIDATE_KINDS:
            raise ValueError(
                f"candidate kind 越词表：{self.kind!r}（合法值：{sorted(CANDIDATE_KINDS)}）"
            )
        unknown = set(self.channels) - L0_CHANNELS
        if unknown:
            raise ValueError(
                f"candidate 通道越词表：{sorted(unknown)}（合法值：{sorted(L0_CHANNELS)}）"
            )
        if not self.channels:
            raise ValueError("candidate channels 为空（至少命中一通道）")
        if self.span[0] < 1 or self.span[1] < self.span[0]:
            raise ValueError(f"candidate span 非法：{self.span}（1-based 闭区间）")
        if not self.candidate_id:
            object.__setattr__(
                self, "candidate_id",
                candidate_id(self.path, self.span, self.kind, self.clue),
            )

    @property
    def priority(self) -> int:
        """优先级（§1.5 逐字）：len(channels) + 规则通道命中再 +1。"""
        return len(self.channels) + (1 if "C" in self.channels else 0)

    def sort_key(self) -> tuple[str, int, int]:
        """候选集排序键：(path, span) 字典序（§1.1 确定性排序）。"""
        return (self.path, self.span[0], self.span[1])


def _mergeable(a: Candidate, b: Candidate) -> bool:
    """§1.5 归并判据：同文件 ∧ 同 kind ∧（span 重叠 ∨ 间距 ≤ MERGE_GAP_LINES）。"""
    if a.path != b.path or a.kind != b.kind:
        return False
    return a.span[0] <= b.span[1] + MERGE_GAP_LINES and b.span[0] <= a.span[1] + MERGE_GAP_LINES


def merge_candidates(cands: list[Candidate]) -> list[Candidate]:
    """归并 + 确定性排序（§1.5；纯函数）。

    - 输入序无关：先按 (path, span, kind, clue) 排序，同族（可链式合并的相邻
      同 kind 候选）自左向右折叠——span 并集随折叠增长，链式重叠（A≈B≈C 而
      A̸≈C）经排序后自然并入同族；
    - channels 并集；clue 排序去重以 ``+`` 联接（candidate_id 重算输入）；
    - taxonomy_hint：族内非空 hint 唯一时保留，冲突置 None（不猜）；
    - 输出按 (path, span) 字典序（同族已折叠为单候选）。"""
    ordered = sorted(cands, key=lambda c: (c.path, c.span, c.kind, c.clue))
    out: list[Candidate] = []
    for cand in ordered:
        if out and _mergeable(out[-1], cand):
            prev = out[-1]
            start = min(prev.span[0], cand.span[0])
            end = max(prev.span[1], cand.span[1])
            clue = "+".join(sorted({*prev.clue.split("+"), *cand.clue.split("+")}))
            hints = {h for h in (prev.taxonomy_hint, cand.taxonomy_hint) if h}
            hint = hints.pop() if len(hints) == 1 else None
            out[-1] = Candidate(
                path=prev.path,
                span=(start, end),
                kind=prev.kind,
                clue=clue,
                channels=prev.channels | cand.channels,
                taxonomy_hint=hint,
                candidate_id=candidate_id(prev.path, (start, end), prev.kind, clue),
            )
        else:
            out.append(cand)
    out.sort(key=Candidate.sort_key)
    return out


def candidates_to_json(cands: list[Candidate]) -> str:
    """候选集序列化（工件库 l0_candidates 载荷；键序规范化 + 字典序候选序）。"""
    payload = {
        "kind": "l0_candidates",
        "candidates": [
            {
                "candidate_id": c.candidate_id,
                "path": c.path,
                "span": [c.span[0], c.span[1]],
                "kind": c.kind,
                "clue": c.clue,
                "channels": sorted(c.channels),
                "priority": c.priority,
                "taxonomy_hint": c.taxonomy_hint,
            }
            for c in sorted(cands, key=Candidate.sort_key)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)


def candidates_from_json(raw: str) -> list[Candidate]:
    """候选集反序列化（S9 位级对照的读取侧；形状不符 → ValueError）。"""
    import json as _json

    data = _json.loads(raw)
    if not isinstance(data, dict) or data.get("kind") != "l0_candidates":
        raise ValueError("l0_candidates 工件载荷形状非法")
    out = [
        Candidate(
            path=it["path"],
            span=(int(it["span"][0]), int(it["span"][1])),
            kind=it["kind"],
            clue=it["clue"],
            channels=frozenset(it["channels"]),
            taxonomy_hint=it.get("taxonomy_hint"),
            candidate_id=it["candidate_id"],
        )
        for it in data["candidates"]
    ]
    return out
