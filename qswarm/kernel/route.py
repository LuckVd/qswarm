"""kernel.route：自适应 TILT 路由与探索通道（设计 §5.2；SPEC-4 §0 仿真复用本体）。

``route(task, cells, rng, params)`` 是派单决策的**纯函数**：读 :class:`CellView`
的 active 格子与 C*/LOO 基线，产出 :class:`RouteDecision`。**不落账**——
dispatch / re_route 事件所需字段由 decision 携带（``to_dispatch_payload()`` 直给
DispatchPayload 形状），落账由调用方经 ``KernelLedger.append`` 执行（SPEC-1 §6-2
写口纪律：ledger/cells 写口只在聚合器与账本本体）。

机制语义（设计文档 v0.3.4 §5.2，逐条可溯源）：
- 路由节点：``node = type_node or family_node``——任务带类型则路由类型格，只有
  家族则家族格（§7.2 家族级默认派单，粒度由流量长出）。
- 专员集合：该 node 的 **active** 格子按 C* 降序取前 K=3（§7.4 单类型专员上限；
  激活 ≠ 垄断，K 内冗余备份）。C* 平手按 agent_id 字典序，保证确定性。
- 自适应 TILT：``tilt = clamp(T_BASE + GAMMA·(C*(specs[0]) − C_base(node)),
  T_MIN, T_MAX)``（默认 0.70 起浮、[0.50, 0.90] 夹取）。基线 C_base 取 **LOO**
  均值——同 ``(role, node)`` 全体存款剔除 specs[0] 自身（§5.1「基线剔除自身」；
  DECISIONS 2026-10-08）；基线总量 < ε 或无数据时取 ROOT_PRIOR=0.5 收底，
  冷启动 tilt 恰为 T_BASE 起浮。
- 专员通道：``specs 非空且 rng.random() < tilt`` → 专员间 least_loaded（注入
  load 时取在途负载最小者；平手依次比 C* 降序、agent_id 字典序）。
- 探索通道：家族级 **80% 按家族格 C* 加权抽样 + 20% 均匀保底**
  （params.explore.UNIFORM_FLOOR，§5.2 量化）——探索流量是其他 agent 持续攒
  影子账的唯一来源（§7.3 护栏「激活 ≠ 垄断」）。权重全 ≤ 0（极端全负格子）时
  加权分支回退均匀。

随机性纪律（SPEC-4 §6：随机种子显式注入、可复现）：
- 全部随机性只经注入的 ``random.Random(seed)``，且**只调 ``rng.random()``**
  （uniform/加权抽样都由同一原语构造），消耗顺序固定：
  ① specs 非空时先 1 次判 tilt（命中即返回，不再消耗；specs 为空短路不消耗，
  照 §5.2 伪码 ``if specs and rand() < tilt``）；
  ② 走到探索且池非空时再 1 次判 UNIFORM_FLOOR 分支；
  ③ 均匀分支与加权分支各恰消耗 1 次取样本。
  同 (种子, cells, task 序列) ⇒ 路由序列逐位一致。

参数语义（DECISIONS 2026-10-08）：
- ``load``：agent → 在途任务数（least_loaded 依据）；``max_load``：在途上限，
  给定时候选专员与探索池中 ``load ≥ max_load`` 者剔除（§5.2 伪码
  ``excl_overloaded``）。M0 仿真单线程派单，两者默认 None（零载、不剔除）。
- ``pool``：显式探索池（agent_id 序列）；缺省从 cells 派生 = 同 role 且格子
  节点落在该家族（family 或 family/*）的全部 agent。池先排序再抽样，跨运行
  稳定；池中无格子的 agent 权重取收缩链根先验 ROOT_PRIOR（§5.1 永不死锁）。
- 无 active 专员时 ``tilt=None``（无专员通道可言）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from qswarm.contracts import CellStatus, ParamsConfig, TiltParams

from .cells import ROOT_PRIOR, CellView

__all__ = [
    "SPEC_CAP",
    "RouteDecision",
    "active_specialists",
    "adaptive_tilt",
    "route",
]

#: 单类型专员上限 K（§7.4 参数表：K=3，冗余备份而非浪费）。
#: params.yaml 无此键（TiltParams extra=forbid），M0 定稿如需参数化随回填放开。
SPEC_CAP = 3


# ── 任务归一化（task 鸭子类型：taxonomy_path 或 family[/type]）────────

def _taxonomy_of(task) -> tuple[str, ...]:
    """task → 类型树路径。优先 ``taxonomy_path``，退 ``family``(+/``type``)。"""
    path = getattr(task, "taxonomy_path", None)
    if path:
        parts = tuple(str(p) for p in path)
        if not parts or not parts[0]:
            raise ValueError(f"task.taxonomy_path 必须以非空家族开头：{path!r}")
        return parts
    family = getattr(task, "family", None)
    if family:
        typ = getattr(task, "type", None)
        return (str(family), str(typ)) if typ else (str(family),)
    raise TypeError(
        "task 须带 taxonomy_path（至少到 family）或 family[/type] 属性："
        f"{type(task).__name__}"
    )


def _role_of(task) -> str:
    role = getattr(task, "role", None)
    if role is None:
        raise TypeError(f"task 须带 role 属性（§5.2 pool=agents_with(role=task.role)）")
    return str(role)


# ── 专员集合与自适应 TILT（§5.2 公式本体，黄金用例直接对照）────────

def active_specialists(
    view: CellView, role: str, node: str
) -> list[tuple[str, float]]:
    """某 (role, node) 的全部 active 专员，按 C* 降序（平手按 agent_id）。

    不截 K——K 截断是 route 的派单语义（§5.2 ``[:K]``）；晋升/孵化侧可复用本
    函数取全量排序。
    """
    scored = [
        (key.agent_id, view.c_star(key.agent_id, role, node))
        for key, cell in view.cells.items()
        if key.role == role and key.node == node and cell.status == CellStatus.ACTIVE.value
    ]
    scored.sort(key=lambda t: (-t[1], t[0]))
    return scored


def adaptive_tilt(top: float, base: float, *, tilt: TiltParams) -> tuple[float, float]:
    """自适应倾斜（§5.2）：返回 ``(raw, clamped)``。

    ``raw = T_BASE + GAMMA·(top − base)``；clamped 夹取到 ``[T_MIN, T_MAX]``。
    专员优势边际大 → 上浮收割（→T_MAX），优势收窄 → 回落加大探索（→T_MIN）。
    """
    raw = tilt.T_BASE + tilt.GAMMA * (top - base)
    return raw, min(tilt.T_MAX, max(tilt.T_MIN, raw))


# ── 抽样原语（全部只消耗 rng.random()，各恰 1 次）─────────────────

def _uniform_pick(rng: random.Random, pool: Sequence[str]) -> str:
    """均匀抽一个：``pool[int(u·n)]``（u ∈ [0,1)，n ≤ 池长，偏差可忽略）。"""
    idx = int(rng.random() * len(pool))
    return pool[min(idx, len(pool) - 1)]


def _weighted_pick(
    rng: random.Random, pool: Sequence[str], weights: Sequence[float]
) -> str:
    """按权重抽一个（单次 ``rng.random()`` 累积权重扫描）；全零权重回退均匀。"""
    total = math.fsum(weights)
    if total <= 0.0:
        return _uniform_pick(rng, pool)
    x = rng.random() * total
    acc = 0.0
    for agent, w in zip(pool, weights):
        acc += w
        if x < acc:
            return agent
    return pool[-1]                       # 浮点尾部兜底（x 逼近 total）


def _default_pool(view: CellView, role: str, family: str) -> list[str]:
    """探索池缺省派生：同 role 且格子节点落在该家族（family 或 family/*）。"""
    prefix = family + "/"
    return sorted(
        {
            key.agent_id
            for key in view.cells
            if key.role == role and (key.node == family or key.node.startswith(prefix))
        }
    )


# ── 决策结果（dispatch/re_route 事件字段的唯一来源）───────────────

@dataclass(frozen=True)
class RouteDecision:
    """一次派单决策的全部可观测字段（调用方据此落 dispatch/re_route 事件）。

    - ``node``：实际路由节点（type_node or family_node）；``family``：家族。
      re_route 载荷的 from_node/to_node 即由这两者按调用方语义取用。
    - ``tilt`` / ``raw_tilt``：夹取后生效值 / 夹取前值（黄金对照与敏感性采集）；
      无 active 专员时两者为 None。
    - ``specs`` / ``spec_c_stars``：专员集合（C* 降序，≤K）与对齐的 C* 值。
    - ``uniform_pick``：探索通道内是否走了 20% 均匀保底分支（诊断字段，专供
      统计验收与度量采集，不入账本载荷）；专员通道或空池为 None。
    """

    task_id: str
    role: str
    family: str
    node: str
    assignee: str | None
    channel: str                            # "specialist" | "explore"
    reason: str                             # tilt_hit|tilt_miss|no_specs|empty_pool
    tilt: float | None
    raw_tilt: float | None
    specs: tuple[str, ...]
    spec_c_stars: tuple[float, ...]
    uniform_pick: bool | None = None

    def to_dispatch_payload(self, *, hop_budget: int = 0) -> dict:
        """DispatchPayload 形状字段（contracts.models.DispatchPayload 可直校验）。"""
        return {
            "task_id": self.task_id,
            "role": self.role,
            "node": self.node,
            "assignee": self.assignee,
            "hop_budget": hop_budget,
        }


# ── 主入口 ──────────────────────────────────────────────────────

def route(
    task,
    cells: CellView,
    rng: random.Random,
    params: ParamsConfig,
    *,
    load: Mapping[str, float] | None = None,
    max_load: float | None = None,
    pool: Sequence[str] | None = None,
) -> RouteDecision:
    """按 §5.2 为一个任务选 assignee（纯函数；随机性只经注入 rng）。

    流程：归一 task → 收集 active 专员（剔过载）→ 算自适应 tilt →
    ``rng.random() < tilt`` 走专员通道（least_loaded），否则探索通道
    （家族级 UNIFORM_FLOOR 均匀保底 + 按家族格 C* 加权）。见模块 docstring
    的语义与 rng 消耗顺序契约。
    """
    role = _role_of(task)
    path = _taxonomy_of(task)
    family = path[0]
    node = "/".join(path[:2]) if len(path) > 1 else family

    def _overloaded(agent: str) -> bool:
        return max_load is not None and load is not None and load.get(agent, 0) >= max_load

    # 专员集合：active 格子按 C* 降序，剔过载后截 K（§5.2 [:K]）
    scored = [(a, c) for a, c in active_specialists(cells, role, node) if not _overloaded(a)]
    top = scored[:SPEC_CAP]
    specs = tuple(a for a, _ in top)
    spec_scores = tuple(c for _, c in top)

    tilt = raw = None
    if specs:
        # C_base(node)：LOO 基线剔 specs[0] 自身（§5.1）；击穿地板/无数据 → 根先验
        base = cells.baseline_mean(role, node, exclude_agent=specs[0])
        if base is None:
            base = ROOT_PRIOR
        raw, tilt = adaptive_tilt(spec_scores[0], base, tilt=params.tilt)

    if specs and rng.random() < tilt:
        score_of = dict(zip(specs, spec_scores))
        assignee = min(
            specs,
            key=lambda a: (load.get(a, 0) if load is not None else 0, -score_of[a], a),
        )
        return RouteDecision(
            task_id=str(getattr(task, "task_id", "")),
            role=role, family=family, node=node,
            assignee=assignee, channel="specialist", reason="tilt_hit",
            tilt=tilt, raw_tilt=raw, specs=specs, spec_c_stars=spec_scores,
        )

    # 探索通道（specs 为空 / tilt 未命中）：家族级加权 + 均匀保底
    pool_ids = sorted(set(pool)) if pool is not None else _default_pool(cells, role, family)
    pool_ids = [a for a in pool_ids if not _overloaded(a)]
    reason = "tilt_miss" if specs else "no_specs"
    if not pool_ids:
        return RouteDecision(
            task_id=str(getattr(task, "task_id", "")),
            role=role, family=family, node=node,
            assignee=None, channel="explore", reason="empty_pool",
            tilt=tilt, raw_tilt=raw, specs=specs, spec_c_stars=spec_scores,
        )
    uniform_pick = rng.random() < params.explore.UNIFORM_FLOOR
    if uniform_pick:
        assignee = _uniform_pick(rng, pool_ids)
    else:
        weights = [max(cells.c_star(a, role, family), 0.0) for a in pool_ids]
        assignee = _weighted_pick(rng, pool_ids, weights)
    return RouteDecision(
        task_id=str(getattr(task, "task_id", "")),
        role=role, family=family, node=node,
        assignee=assignee, channel="explore", reason=reason,
        tilt=tilt, raw_tilt=raw, specs=specs, spec_c_stars=spec_scores,
        uniform_pick=uniform_pick,
    )
