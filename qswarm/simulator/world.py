"""仿真世界·合成 agent 池（SPEC-4 §1.1）。

按 params.yaml roster（种子版 6/5/4/3）生成 agent；每只 agent 在 taxonomy 每个
家族上带家族级真实能力 q_f ~ Beta(2,2)，在每个（家族,类型）上带类型级偏移
q_t = clip(q_f + ε_t, 0.05, 0.95)，ε_t ~ N(0, 0.10)。

隐藏设定（ground truth）**仅度量可读、机制不可见**：
  - 10% 「真专家」EXPERT  ：某家族 q_f ~ Beta(8,2)（应该被晋升）；
  - 10% 「运气型」LUCKY   ：q_f ~ Beta(2,2)，verdict 生成带早期正向偏差
    （ground.early_positive_bias，仅供 §1.3 verdict 生成器读取）；
  -  5% 「退化型」DEGRADED：T_drift 时刻 q_f ← q_f − 0.25（应该被熄灭）；
  - 其余 NORMAL。
机制面（kernel.route / 存款账本）永远看不到 archetype / q 本身——机制只能从存款
信号间接推断；`AgentPool.metrics_view()` 是度量采集侧的唯一 ground-truth 出口，
`AgentPool.mechanism_roster()` 是机制侧可见的全部信息（id + 角色）。

随机性：所有抽样走 rng.py 的命名子流（archetype / expert_family / family_q /
type_eps），从注入主种子派生（SPEC-4 §6：种子显式注入，结果可复现）。

退化漂移取值说明：漂移按规格直译 `q_f ← q_f − 0.25`，家族级 q_f 不再 clip
（β 分布天然在 (0,1)，漂移后理论上可低至 −0.25；概率域 clamp 由 §1.3 verdict
生成的判对概率公式负责），保证「T_drift 前后 q 差恰为 0.25」逐位成立；类型级
q_t 仍按 [0.05, 0.95] 重新 clip（能力是概率域量）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from qswarm.contracts import ParamsConfig, Role, TaxonomyConfig, load_params, load_taxonomy

from .rng import substream


# ── 隐藏设定（ground truth，仅度量可读）────────────────────────

class Archetype(StrEnum):
    """agent 隐藏设定四种型（SPEC-4 §1.1；比例 10% / 10% / 5% / 其余）。"""

    EXPERT = "expert"        # 真专家：某家族 q_f ~ Beta(8,2)
    LUCKY = "lucky"          # 运气型：verdict 早期正向偏差（§1.3 消费）
    DEGRADED = "degraded"    # 退化型：T_drift 后 q_f − 0.25
    NORMAL = "normal"


@dataclass(frozen=True)
class WorldHiddenParams:
    """世界隐藏设定旋钮（M0 定稿对象；不在 params.yaml，属仿真世界而非机制）。"""

    p_expert: float = 0.10          # 真专家占比
    p_lucky: float = 0.10           # 运气型占比
    p_degraded: float = 0.05        # 退化型占比
    expert_alpha: float = 8.0       # 真专家家族 q_f ~ Beta(8, 2)
    luck_bias: float = 0.15         # 运气型早期正向偏差幅值（verdict 生成读取）
    drift_delta: float = 0.25       # 退化幅度（SPEC-4 §1.1 固定值）
    t_drift: int = 500              # 退化时刻（全局任务序号时钟，可配）


@dataclass(frozen=True)
class GroundTruth:
    """一只 agent 的隐藏设定（度量采集专用；机制面不可见）。"""

    archetype: Archetype
    expert_family: str | None = None     # 仅 EXPERT：真专家家族
    t_drift: int | None = None           # 仅 DEGRADED：漂移时刻
    early_positive_bias: float = 0.0     # 仅 LUCKY：verdict 早期正向偏差
    type_eps: dict[str, float] = field(default_factory=dict)  # (family/type)→ε_t，校验用


# ── agent 与池 ──────────────────────────────────────────────────

@dataclass
class SimAgent:
    """合成 agent：身份 + 家族/类型级真实能力 + 隐藏设定。

    family_q 存**漂移前**基值（pre-drift）；漂移在 `capability()` 按 t 现算，
    因此同一池可在 T_drift 前后两段轨迹上复用（退化响应度量的前提）。
    """

    agent_id: str
    role: Role
    family_q: dict[str, float]            # family → q_f（Beta 抽样原值，未 clip）
    ground: GroundTruth                   # 含 type_eps（ε_t）与隐藏设定

    def capability(
        self,
        family: str,
        type_: str | None = None,
        *,
        t: int = 0,
        hidden: WorldHiddenParams | None = None,
    ) -> float:
        """真实能力 q（世界真值）：家族级返回 q_f(t)，类型级返回 clip(q_f(t)+ε_t)。

        t 为全局任务序号时钟：t ≥ t_drift 时退化型 q_f 减 drift_delta。
        """
        hp = hidden or WorldHiddenParams()
        q_f = self.family_q[family]
        if (
            self.ground.archetype is Archetype.DEGRADED
            and self.ground.t_drift is not None
            and t >= self.ground.t_drift
        ):
            q_f = q_f - hp.drift_delta
        if type_ is None:
            return q_f
        eps = self.ground.type_eps[f"{family}/{type_}"]
        return min(0.95, max(0.05, q_f + eps))


class AgentPool:
    """合成 agent 池（SPEC-4 §1.1）。按 roster 逐角色生成，家族覆盖 taxonomy 全集。"""

    def __init__(self, agents: list[SimAgent], hidden_params: WorldHiddenParams):
        self.agents = agents
        self.hidden_params = hidden_params
        self._by_id = {a.agent_id: a for a in agents}

    # ── 构造 ──

    @classmethod
    def generate(
        cls,
        roster: Mapping[str, int],
        taxonomy: TaxonomyConfig,
        seed: int,
        hidden: WorldHiddenParams | None = None,
    ) -> "AgentPool":
        """按 roster（role→数量）与 taxonomy 家族树生成池；seed 注入主种子。"""
        hp = hidden or WorldHiddenParams()
        families = list(taxonomy.families)                     # YAML 顺序，稳定
        type_paths = [
            f"{f}/{ty}" for f in families for ty in taxonomy.families[f].types
        ]

        # 独立子流：型别判定 / 专家家族 / 家族能力 / 类型偏移
        rng_arch = substream(seed, "world", "archetype")
        rng_efam = substream(seed, "world", "expert_family")
        rng_qf = substream(seed, "world", "family_q")
        rng_eps = substream(seed, "world", "type_eps")

        agents: list[SimAgent] = []
        for role_name, count in roster.items():
            role = Role(role_name)
            for i in range(1, count + 1):
                archetype = _roll_archetype(rng_arch, hp)
                expert_family = (
                    rng_efam.choice(families) if archetype is Archetype.EXPERT else None
                )
                t_drift: int | None = None
                bias = 0.0

                family_q: dict[str, float] = {}
                for f in families:
                    if archetype is Archetype.EXPERT and f == expert_family:
                        family_q[f] = rng_qf.betavariate(hp.expert_alpha, 2.0)
                    else:
                        family_q[f] = rng_qf.betavariate(2.0, 2.0)

                type_eps = {tp: rng_eps.gauss(0.0, 0.10) for tp in type_paths}

                if archetype is Archetype.LUCKY:
                    bias = hp.luck_bias
                if archetype is Archetype.DEGRADED:
                    t_drift = hp.t_drift

                agents.append(
                    SimAgent(
                        agent_id=f"{role.value}-{i:02d}",
                        role=role,
                        family_q=family_q,
                        ground=GroundTruth(
                            archetype=archetype,
                            expert_family=expert_family,
                            t_drift=t_drift,
                            early_positive_bias=bias,
                            type_eps=type_eps,
                        ),
                    )
                )
        return cls(agents, hp)

    @classmethod
    def from_params(
        cls,
        params: ParamsConfig | None = None,
        taxonomy: TaxonomyConfig | None = None,
        seed: int = 0,
        hidden: WorldHiddenParams | None = None,
    ) -> "AgentPool":
        """按 configs/params.yaml roster 与 configs/taxonomy.yaml 生成（默认 6/5/4/3）。"""
        p = params or load_params()
        tax = taxonomy or load_taxonomy()
        return cls.generate(p.roster.model_dump(), tax, seed, hidden)

    # ── 查询 ──

    def __len__(self) -> int:
        return len(self.agents)

    def __iter__(self):
        return iter(self.agents)

    def by_role(self, role: Role) -> list[SimAgent]:
        return [a for a in self.agents if a.role is role]

    def get(self, agent_id: str) -> SimAgent:
        return self._by_id[agent_id]

    def capability(
        self, agent_id: str, family: str, type_: str | None = None, *, t: int = 0
    ) -> float:
        return self.get(agent_id).capability(family, type_, t=t, hidden=self.hidden_params)

    # ── 两个受控视图 ──

    def mechanism_roster(self) -> list[tuple[str, Role]]:
        """机制侧可见的全部信息：[(agent_id, role)]。无 q、无 archetype（§1.1 纪律）。"""
        return [(a.agent_id, a.role) for a in self.agents]

    def metrics_view(self) -> dict[str, GroundTruth]:
        """度量采集侧唯一 ground-truth 出口（晋升查准/误熄灭率等 §2 度量用）。"""
        return {a.agent_id: a.ground for a in self.agents}


# ── 内部抽样 ────────────────────────────────────────────────────

def _roll_archetype(rng, hp: WorldHiddenParams) -> Archetype:
    """单次均匀抽样的互斥分桶：[0,p_e) 专家 / [p_e,p_e+p_l) 运气 / […,+p_d) 退化。"""
    u = rng.random()
    if u < hp.p_expert:
        return Archetype.EXPERT
    if u < hp.p_expert + hp.p_lucky:
        return Archetype.LUCKY
    if u < hp.p_expert + hp.p_lucky + hp.p_degraded:
        return Archetype.DEGRADED
    return Archetype.NORMAL


__all__ = [
    "AgentPool",
    "Archetype",
    "GroundTruth",
    "SimAgent",
    "WorldHiddenParams",
]
