"""仿真世界·任务流生成器（SPEC-4 §1.2）。

到达：逐仿真日 n ~ Poisson(λ)（λ 默认 50 任务/日）。
家族混合：taxonomy.yaml v1_weight 归一化后抽样（mem_safety 0.3 → p=0.3/5.3）。
难度：d ~ Beta(2, 3)（世界真值，供 §1.3 判对概率 q·(1−0.3·d) 使用；机制不可见）。
模式混合：fast 50% / pr 35% / full 15%。
周期重扫：以 params.budget.RESCAN_WEEKLY（0.07）比例生成重扫任务，
  cache_hit=True —— 走缓存、无新 finding，只用于测路由（预算友好）。

随机性：到达计数 / 家族 / 类型 / 难度 / 模式 / 重扫各自持独立命名子流，
从注入主种子派生（见 rng.py）——任何子流内部序列不受其它子流增减影响。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterator

from qswarm.contracts import (
    ParamsConfig,
    RunMode,
    TaxonomyConfig,
    load_params,
    load_taxonomy,
)

from .rng import poisson, substream


# ── 配置 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TaskMix:
    """模式混合（SPEC-4 §1.2：fast 50% / pr 35% / full 15%）。"""

    fast: float = 0.50
    pr: float = 0.35
    full: float = 0.15

    def __post_init__(self) -> None:
        w = (self.fast, self.pr, self.full)
        if any(w_i < 0 for w_i in w):
            raise ValueError(f"模式权重必须非负：{w}")
        if not math.isclose(sum(w), 1.0, rel_tol=0, abs_tol=1e-9):
            raise ValueError(f"模式权重之和必须为 1：sum={sum(w)!r}")


@dataclass(frozen=True)
class TaskStreamParams:
    """任务流旋钮（λ 不在 params.yaml，属仿真世界参数；其余默认取 SPEC 值）。"""

    lam: float = 50.0               # 到达率：任务/仿真日
    mix: TaskMix = field(default_factory=TaskMix)


# ── 任务对象 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorldTask:
    """世界级任务（喂给 kernel.route 的输入；difficulty 为世界真值，机制不可见）。"""

    task_id: str                    # 'T-<序号:06d>'，世界内唯一
    t: int                          # 全局序号（0 起），即退化时钟 T_drift 的坐标
    day: int                        # 仿真日（0 起）
    family: str                     # 家族（taxonomy 键）
    type: str                       # 家族内类型（路由默认家族级，类型为世界真值）
    difficulty: float               # d ~ Beta(2,3)
    mode: RunMode                   # fast / pr / full
    cache_hit: bool                 # True=周期重扫任务：无新 finding，只测路由

    @property
    def produces_finding(self) -> bool:
        """重扫任务命中缓存、无新 finding（SPEC-4 §1.2）。"""
        return not self.cache_hit

    def to_record(self) -> dict:
        """逐位比较用平铺记录（float 保真：repr 往返精确）。"""
        return {
            "task_id": self.task_id, "t": self.t, "day": self.day,
            "family": self.family, "type": self.type,
            "difficulty": self.difficulty, "mode": str(self.mode),
            "cache_hit": self.cache_hit,
        }


# ── 生成器 ──────────────────────────────────────────────────────

class TaskGenerator:
    """任务流生成器（SPEC-4 §1.2）。`generate(n_days)` 整段生成；`iter_days` 逐日喂内核。"""

    def __init__(
        self,
        params: ParamsConfig | None = None,
        taxonomy: TaxonomyConfig | None = None,
        seed: int = 0,
        stream: TaskStreamParams | None = None,
    ) -> None:
        self.params = params or load_params()
        self.taxonomy = taxonomy or load_taxonomy()
        self.seed = seed
        self.sp = stream or TaskStreamParams()

        # 家族混合：v1_weight 归一化（缺省 1.0；mem_safety 0.3 → p=0.3/Σ）
        self._families = list(self.taxonomy.families)
        raw = [self.taxonomy.families[f].v1_weight for f in self._families]
        total = sum(raw)
        self.family_weights = {f: w / total for f, w in zip(self._families, raw)}
        self._weight_list = [self.family_weights[f] for f in self._families]
        self._types = {f: list(self.taxonomy.families[f].types) for f in self._families}

        self._rescan_p = self.params.budget.RESCAN_WEEKLY
        self._mode_names = [RunMode.FAST, RunMode.PR, RunMode.FULL]
        self._mode_weights = [self.sp.mix.fast, self.sp.mix.pr, self.sp.mix.full]

        # 独立子流：到达 / 家族 / 类型 / 难度 / 模式 / 重扫
        self._rng_arrival = substream(seed, "task", "arrival")
        self._rng_family = substream(seed, "task", "family")
        self._rng_type = substream(seed, "task", "type")
        self._rng_diff = substream(seed, "task", "difficulty")
        self._rng_mode = substream(seed, "task", "mode")
        self._rng_rescan = substream(seed, "task", "rescan")

    # ── 抽样原语 ──

    def _draw_family(self) -> str:
        return self._rng_family.choices(self._families, weights=self._weight_list, k=1)[0]

    def _draw_type(self, family: str) -> str:
        return self._rng_type.choice(self._types[family])

    def _draw_difficulty(self) -> float:
        return self._rng_diff.betavariate(2.0, 3.0)

    def _draw_mode(self) -> RunMode:
        return self._rng_mode.choices(self._mode_names, weights=self._mode_weights, k=1)[0]

    def _is_rescan(self) -> bool:
        return self._rng_rescan.random() < self._rescan_p

    # ── 流式 / 整段 ──

    def iter_days(self, n_days: int) -> Iterator[list[WorldTask]]:
        """逐日生成：第 d 日到达 n_d ~ Poisson(λ) 个任务（日内按 t 递增排序）。"""
        t = 0
        for day in range(n_days):
            n = poisson(self._rng_arrival, self.sp.lam)
            batch: list[WorldTask] = []
            for _ in range(n):
                family = self._draw_family()
                batch.append(
                    WorldTask(
                        task_id=f"T-{t:06d}",
                        t=t,
                        day=day,
                        family=family,
                        type=self._draw_type(family),
                        difficulty=self._draw_difficulty(),
                        mode=self._draw_mode(),
                        cache_hit=self._is_rescan(),
                    )
                )
                t += 1
            yield batch

    def generate(self, n_days: int) -> list[WorldTask]:
        """整段生成 n_days 个仿真日的任务流（拼接逐日批次，保持全局序）。"""
        return [task for batch in self.iter_days(n_days) for task in batch]


__all__ = [
    "TaskGenerator",
    "TaskMix",
    "TaskStreamParams",
    "WorldTask",
]
