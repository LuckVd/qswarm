"""kernel.route_thompson：Thompson sampling 对照路由（SPEC-4 §3 附加实验臂）。

对照臂定位（DECISIONS 2026-09-29「Thompson sampling 作对照组而非替代」）：
默认路由保持 C*+TILT（kernel.route）**不变**；本模块只提供同签名的对照路由器
``route_thompson(task, cells, rng, params, *, load, max_load, pool)``，供 §3 附加
单变量实验「同参数，路由换 TS，比 regret」（§5.4 的实验承诺）使用。纯读不落账
（同 route() 写口纪律）：决策字段由 :class:`RouteDecision` 携带、调用方落账。

机制语义（逐 (role, node) 独立后验的伯努利老虎机）：
- 后验：候选 a 在任务节点格 ``(a, role, node)`` 的挥发存款伪计数 (pos_w, neg_w)
  → ``Beta(α, β)``，α = pos_w + λ/4、β = neg_w + λ/4（λ = params.shrink.LAMBDA，
  不新增 params.yaml 键——extra=forbid 纪律）。λ/4 对称先验使**后验均值 ≡ §5.1
  单层收缩公式 ``(n_eff·C_raw + λ·0.5)/(n_eff+λ)``**（父先验取 ROOT_PRIOR=0.5）
  逐位成立：TS 臂的点估计与机制的收缩读数同构，对照结论才干净归因于
  「后验采样探索 vs TILT 双通道」。无格子（冷启动 / 显式池外 agent）→ 纯先验
  ``Beta(λ/4, λ/4)``：均值 0.5、方差最大——TS 天然先探索无证据者（§5.1 永不死锁
  的同款收底）。
- 决策：``node = type_node or family_node``（与 route() 同归一，复用
  ``_taxonomy_of``）；候选 = ``pool`` 显式 or 缺省派生（同 route() 的
  ``_default_pool``：同 role 且格子节点落在该家族），剔过载同 route()
  （``load ≥ max_load`` 剔除）；候选按 agent_id 字典序，逐个恰 1 次
  ``θ_a ~ Beta(α_a, β_a)``，argmax θ 当选（连续分布平手概率零；真平手取字典序
  首个，确定性兜底）。
- 与默认臂的结构差异（对照实验的设计本意，非缺陷）：TS 无专员/探索双通道、
  无 TILT——后验采样本身就是探索-利用权衡（不确定性大者偶尔胜出）；候选集、
  过载语义与任务归一与默认臂完全一致，保证两臂决策流同构可比。

字段映射（RouteDecision 同构，regret 采集器两臂通用）：
- ``specs`` = 候选集（字典序）；``spec_c_stars`` = 各候选后验均值 α/(α+β)
  （对应默认臂的 C* 点估计位）；``tilt``/``raw_tilt`` = None（无 TILT 机制）；
  ``uniform_pick`` = None。
- ``channel="thompson"``；``reason ∈ {"ts_sample", "empty_pool"}``（空池不消耗
  随机数，同 route() 契约）。

随机性纪律（SPEC-4 §6；与 route() 同「种子显式注入」约定）：
- 全部随机性只经注入的 ``random.Random``：逐候选 ``rng.betavariate(α, β)``
  （stdlib 实现 = 两次 gammavariate 拒绝采样，内部只消费 rng.random() 序列，
  固定 (α, β) 下消耗次数确定）。消耗顺序 = 候选字典序，一次决策恰 len(候选)
  次 betavariate。同 (种子, cells, task 序列) ⇒ 决策序列逐位一致。
- 跨进程可复现口径：betavariate 的实现算法 stdlib 仅承诺同版本稳定
  （random() 序列有显式兼容承诺，分布方法无）；M0 实验矩阵以同一运行内重放为
  验收口径，种子寻址经 simulator.rng.derive_seed 派生（blake2b，跨平台稳定）。
"""

from __future__ import annotations

import random
from typing import Mapping, Sequence

from qswarm.contracts import ParamsConfig

from .cells import CellView
from .route import RouteDecision, _default_pool, _role_of, _taxonomy_of

__all__ = [
    "TS_CHANNEL",
    "beta_posterior",
    "posterior_mean",
    "route_thompson",
]

#: TS 对照臂的 channel 词表值（决策流与默认臂 "specialist"/"explore" 区分）。
TS_CHANNEL = "thompson"


# ── Beta 后验（存款伪计数 → (α, β)，黄金用例直接对照 §5.1 收缩式）──

def beta_posterior(pos_w: float, neg_w: float, *, lam: float) -> tuple[float, float]:
    """(role, node) 格子的存款伪计数 → Beta 后验形状 ``(α, β)``。

    ``α = pos_w + λ/4``、``β = neg_w + λ/4``。λ/4 对称先验的取法使后验均值与
    §5.1 单层收缩（父 = ROOT_PRIOR=0.5）逐位相等（证明见模块 docstring）。
    ``pos_w``/``neg_w`` 为 cells 视图的挥发加权存款质量；无格子取 0/0 = 纯先验
    ``Beta(λ/4, λ/4)``（均值 ROOT_PRIOR、方差最大 → 冷启动天然探索）。
    """
    prior = lam / 4.0
    return (pos_w + prior, neg_w + prior)


def posterior_mean(alpha: float, beta: float) -> float:
    """``Beta(α, β)`` 后验均值（TS 臂在 ``spec_c_stars`` 里的点估计位）。"""
    return alpha / (alpha + beta)


# ── 主入口（与 kernel.route.route 同签名同返回型）────────────────

def route_thompson(
    task,
    cells: CellView,
    rng: random.Random,
    params: ParamsConfig,
    *,
    load: Mapping[str, float] | None = None,
    max_load: float | None = None,
    pool: Sequence[str] | None = None,
) -> RouteDecision:
    """按 Thompson sampling 为一个任务选 assignee（纯函数；随机性只经注入 rng）。

    流程：归一 task（同 route()）→ 收集候选（pool 显式 or 家族派生，剔过载，
    字典序）→ 逐候选从 (role, node) 存款伪计数构造 Beta 后验并采样 θ →
    argmax θ 当选。空池返回 empty_pool 决策、不消耗随机数。
    """
    role = _role_of(task)
    path = _taxonomy_of(task)
    family = path[0]
    node = "/".join(path[:2]) if len(path) > 1 else family

    def _overloaded(agent: str) -> bool:
        return max_load is not None and load is not None and load.get(agent, 0) >= max_load

    candidates = sorted(set(pool)) if pool is not None else _default_pool(cells, role, family)
    candidates = [a for a in candidates if not _overloaded(a)]

    task_id = str(getattr(task, "task_id", ""))
    if not candidates:
        return RouteDecision(
            task_id=task_id, role=role, family=family, node=node,
            assignee=None, channel=TS_CHANNEL, reason="empty_pool",
            tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
        )

    lam = params.shrink.LAMBDA
    means: list[float] = []
    samples: list[float] = []
    for agent in candidates:                       # 字典序 = rng 消耗顺序（契约）
        cell = cells.get(agent, role, node)
        alpha, beta = beta_posterior(
            cell.pos_w if cell is not None else 0.0,
            cell.neg_w if cell is not None else 0.0,
            lam=lam,
        )
        means.append(posterior_mean(alpha, beta))
        samples.append(rng.betavariate(alpha, beta))   # 每候选恰 1 次后验采样

    # argmax θ（max 平手取迭代序首个 = 字典序最小，确定性兜底）
    best = max(range(len(candidates)), key=samples.__getitem__)
    return RouteDecision(
        task_id=task_id, role=role, family=family, node=node,
        assignee=candidates[best], channel=TS_CHANNEL, reason="ts_sample",
        tilt=None, raw_tilt=None,
        specs=tuple(candidates), spec_c_stars=tuple(means),
    )
