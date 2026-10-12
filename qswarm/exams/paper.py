"""exams.paper：确定性出卷（SPEC-6 §3.1）。

入职考出卷（§8.4「8–12 题（含埋雷）」）：每 (agent, family) 一卷。卷型（S3 拍板）：
ENTRY = 12 题 = 8 真 + 4 雷——对齐 M0 定稿仿真口径（ExamWorldParams.entry_items=12）；
真/雷分别从该家族 expected 池抽取。

确定性：随机源 = ``derive_seed(seed, "exam", "entry-v1", agent_id, family,
bank_version)``（SPEC-4 rng 纪律同型：blake2b 内容定址，禁内建 hash——跨进程/
跨 PYTHONHASHSEED 可复现）；池内不重复（池不足 → ValueError，§1.2 配额保证不
触发）；真池/雷池各自洗牌后按序取（洗牌 = Fisher-Yates on 派生 rng）。题序：真雷
交错固定模式 [真,真,雷,真,真,雷,…]（12 题位次固定，防「后段全是雷」的位置指纹）；
同 (bank_version, agent_id, family) 重放逐位同卷。
"""

from __future__ import annotations

import random

from qswarm.contracts import ExamPurpose
from qswarm.simulator.rng import derive_seed

from .bank import SeedBank, SeedItem

__all__ = ["ENTRY_ITEMS", "ENTRY_REAL", "ENTRY_FP", "PAPER_PATTERN", "draw_paper"]

#: ENTRY 卷型（S3）：12 题 = 8 真 + 4 雷（对齐 M0 定稿 ExamWorldParams.entry_items=12）。
ENTRY_ITEMS = 12
ENTRY_REAL = 8
ENTRY_FP = 4

#: 真雷交错固定模式（True=真题位；12 题位次固定，防位置指纹）。
PAPER_PATTERN: tuple[bool, ...] = (
    True, True, False, True, True, False, True, True, False, True, True, False,
)

#: 随机子流标签（SPEC-6 §3.1 逐字）。
_STREAM_LABELS = ("exam", "entry-v1")


def _fisher_yates(rng: random.Random, pool: list[SeedItem]) -> None:
    """Fisher-Yates 原地洗牌（确定性：rng 为派生子流；不使用内建 hash）。"""
    for i in range(len(pool) - 1, 0, -1):
        j = rng.randrange(i + 1)
        pool[i], pool[j] = pool[j], pool[i]


def draw_paper(
    bank: SeedBank,
    *,
    agent_id: str,
    role: object,
    family: str,
    purpose: ExamPurpose | str,
    bank_version: int,
    seed: int,
) -> list[SeedItem]:
    """入职考出卷：返回 12 题（8 真 + 4 雷，真雷交错固定模式）。

    - purpose 仅 ENTRY（M2 范围裁剪 S4/S8）：promotion/periodic →
      NotImplementedError（生产化随 M3 promote_scan 夜批接线）；
    - ``bank_version`` 为派生流标签（不同版本卷不同；同 (bank_version, agent_id,
      family) 恒同卷）——跨版本不可混卷由调用方钉住（seeding/CLI 恒传
      bank.bank_version，DECISIONS M2 实现期行）；
    - 池不足 → ValueError（§1.2 配额保证官方题库不触发）。
    """
    purpose_key = str(getattr(purpose, "value", purpose))
    if purpose_key != ExamPurpose.ENTRY.value:
        raise NotImplementedError(
            f"M2 出卷只实现 ENTRY（S4/S8 范围裁剪），purpose={purpose_key!r} 的"
            "出卷调度随 M3 promote_scan 夜批接线"
        )
    real_pool, fp_pool = bank.pools(family)
    if len(real_pool) < ENTRY_REAL:
        raise ValueError(
            f"[{family}] 真题池不足：{len(real_pool)} < {ENTRY_REAL}（§1.2 配额表保证）"
        )
    if len(fp_pool) < ENTRY_FP:
        raise ValueError(
            f"[{family}] 雷池不足：{len(fp_pool)} < {ENTRY_FP}（§1.2 配额表保证）"
        )
    rng = random.Random(
        derive_seed(seed, *_STREAM_LABELS, agent_id, family, bank_version)
    )
    _fisher_yates(rng, real_pool)
    _fisher_yates(rng, fp_pool)
    reals = iter(real_pool[:ENTRY_REAL])
    fps = iter(fp_pool[:ENTRY_FP])
    return [next(reals) if want_real else next(fps) for want_real in PAPER_PATTERN]
