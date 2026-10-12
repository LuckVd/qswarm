"""随机子流派生（SPEC-4 §1.1/§1.2/§6）。

全部随机源从注入主种子派生**独立子流**：每个消费方（agent 池、到达过程、家族
混合、难度、模式、重扫……）持有自己的 `random.Random`，互不消耗彼此的随机数。
因此新增一个随机源不会移动其它源的取值序列，任何一次生成的轨迹都可按
`(主种子, 子流名)` 复现。派生用 blake2b（内容定址，跨进程/跨平台稳定），
不用内建 `hash()`（受 PYTHONHASHSEED 影响，不可复现）。
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Sequence


def derive_seed(master_seed: int, *labels: str | int) -> int:
    """从主种子与子流路径派生稳定子种子（同输入必同输出）。"""
    h = hashlib.blake2b(digest_size=8)
    h.update(str(master_seed).encode("utf-8"))
    for label in labels:
        h.update(b"\x00")
        h.update(str(label).encode("utf-8"))
    return int.from_bytes(h.digest(), "big")


def substream(master_seed: int, *labels: str | int) -> random.Random:
    """开一条命名子流：`random.Random(derive_seed(master_seed, *labels))`。"""
    return random.Random(derive_seed(master_seed, *labels))


def pick(rng: random.Random, population: Sequence[str]) -> str:
    """均匀抽一个（population 非空；顺序由调用方保证稳定）。"""
    return rng.choice(population)


def poisson(rng: random.Random, lam: float) -> int:
    """精确 Poisson(λ) 采样（stdlib 无此原语，自行实现）。

    Knuth 乘积法要求 exp(-λ) 不下溢：λ 分块为若干 ≤12 的独立段
    （独立 Poisson 之和仍为 Poisson，分布精确），逐段累加。
    """
    if lam < 0:
        raise ValueError(f"λ 必须 ≥ 0：{lam}")
    if lam == 0:
        return 0
    total = 0
    remaining = lam
    while remaining > 0:
        part = min(remaining, 12.0)
        remaining -= part
        L = math.exp(-part)
        k, p = 0, 1.0
        while True:
            p *= rng.random()
            if p <= L:
                break
            k += 1
        total += k
    return total


__all__ = ["derive_seed", "substream", "pick", "poisson"]
