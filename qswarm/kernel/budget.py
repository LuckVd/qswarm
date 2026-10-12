"""kernel.budget：三档预算与熔断（SPEC-2 §4；设计 §2.4/§2.2）。

职责（SPEC-2 §4.1 机制语义逐条）：

- **三档配额判定**（纯函数，§4.2 公式直译）：
  - ``quota_l2 = min(cap, ⌊remaining / cost⌋)``——cost ≤ 0 或 remaining ≤ 0 → 0
    （未配置成本模型不派单，保守）；
  - ``quota_s2 = ⌊存活数 × remaining / cost⌋``——cost ≤ 0 → 0；
  - ``s2_rank_key``：S2 降序取的排序键 ``(SEVERITY_RANK, scarcity, confidence)``
    字典序（DECISIONS 2026-10-10-D15，不取乘积——零置信会把乘积归零吞掉
    severity 主序）；scarcity = 1/(1+本 run 类型簇数)，type_node=None 按空串计；
- **预算熔断器**：``budget_state`` 纯函数判定 ``spent ≥ budget``（含等号，保守
  闭包，D16）→ degraded=True、语义档沿 full→pr→fast 阶梯降一档（fast 到底）；
  quota 按剩余预算重算（remaining ≤ 0 → 恒 0，§4.3「不再派任何付费任务」）；
- **成本入账**（``on_cost``）：runs.spent_cents 累加（runs 表写口在本模块，
  SPEC-2 §6.1）并返回 trip 后 BudgetState。**首次** trip：SCALING 事件
  （pool=f"mode:{入口档}", delta=−1, reason="budget_breaker"）+ runs.status=
  'degraded'，与 spent 更新同事务（D17：不新增 LedgerKind，降级以既有 scaling
  事件承载可审计轨迹，§11-3「一切伸缩可追溯」）；重复 trip 零新事件（幂等，
  §6.4「on_cost trip | 首次 trip 才落 SCALING 事件」）。

纪律与边界（本模块的实现期细化，均已记 DECISIONS.md 2026-10-10 budget 行）：

- **缺省关闭 = 无预算约束路径（D18）**：本模块全部函数仅在 harness 显式装配
  :class:`BudgetPolicy` 后才会被调用；注入 None = 不接预算，M0 仿真轨迹逐字兼容。
  熔断本身是 §2.4 常设机制、无开关——「缺省关闭」的落点就是「不注入即不生效」；
- 配额是**估计值**（§8.1 COST_PER_*_CENTS 是成本锚点估计，非记账费率）——费率
  以 on_cost 实测回报为准，记账只经 cents 累加；
- ``on_cost`` 判定预算以 **policy.budget_cents** 为准（runs.budget_cents 是 run
  建立时快照；harness 追加预算 = 换新 policy，判定随之更新）；
- 熔断无「解除」迁移：spent 单调不减，trip 后恒 degraded；「解除」仅指重复
  on_cost 不再产生新事件（幂等），不存在 un-trip 状态翻转（§4.2 D17 逐字）；
- 与派单状态机的衔接（§4.3）：熔断后 BudgetState.tier 即生效语义档——dispatch
  侧以 tier 查 HOP_BUDGET_BY_MODE（kernel.dispatch 模块常量，§8.2）取低档跳数；
  quota 恒 0 使新付费派单（L2/S2）自然停发；在途任务不撤回（内核无取消语义）；
  收尾接 kernel.dispatch.expire_run（未跑项 expired 零存款，§2.4/§10.2）。

确定性（§6.5）：全部函数零随机性、不接随机数流参数、不消费任何随机源；整数除法
取地板（Python ``//``），浮点仅出现在 scarcity 与 confidence 的比较键中（JSON
repr 往返逐位保真，见 tests/golden/budget/）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Mapping

from qswarm.contracts import (
    LedgerKind,
    ParamsConfig,
    RunMode,
    RunStatus,
    ScalingPayload,
)

from .ledger import transaction

if TYPE_CHECKING:  # 仅注解用（避免 budget↔ledger 运行期环；transaction 已显式导入）
    from .ledger import KernelLedger

__all__ = [
    "BREAKER_REASON",
    "DEGRADED_TIER",
    "BudgetPolicy",
    "BudgetState",
    "SEVERITY_RANK",
    "budget_for_mode",
    "budget_state",
    "on_cost",
    "quota_l2",
    "quota_s2",
    "s2_rank_key",
]


# ── 规格常量（§4.2/§4.3 逐字；不进 params.yaml，extra=forbid 纪律）────────

#: severity → 序数（§4.2 逐字表）；S2 降序取的 severity 主序。
SEVERITY_RANK: Mapping[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}

#: 熔断降级阶梯（§4.3「语义档」行逐字）：full→pr（pr→fast，fast 到底）。
DEGRADED_TIER: Mapping[str, str] = {"full": "pr", "pr": "fast", "fast": "fast"}

#: 首次熔断 SCALING 事件的 reason 字面（D17；SPEC-5 §1.4 用例钉死）。
BREAKER_REASON = "budget_breaker"

_MODES = ("fast", "pr", "full")


# ── 数据结构（§4.2）────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetPolicy:
    """一次 run 的预算约束载体（harness 装配；注入 None = 无预算约束路径，
    M0 仿真轨迹逐字兼容——见 SPEC-2 §8 旋钮表「缺省时行为」列）。"""

    mode: str                          # 入口档 "fast"|"pr"|"full"
    budget_cents: int                  # run 总预算（= runs.budget_cents）
    cost_per_l2_cents: int             # L2 单点成本估计（§8 新键）
    cost_per_s2_cents: int             # S2 单次成本估计（§8 新键）
    l2_cap: int                        # = params.budget.L2_QUOTA_CAP（§2.4 的 45）

    def __post_init__(self) -> None:
        if str(self.mode) not in _MODES:
            raise ValueError(
                f"BudgetPolicy.mode 必须是 {'|'.join(_MODES)}，实际 {self.mode!r}"
            )
        for name in ("budget_cents", "cost_per_l2_cents", "cost_per_s2_cents", "l2_cap"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(
                    f"BudgetPolicy.{name} 必须是非负整数，实际 {value!r}"
                )

    @property
    def batch_eligible(self) -> bool:
        """§2.4 Batch 开关的档位判定（full 独占；实际 Batch 调用归供给层，
        models.yaml runtime.batch_mode_for_full，SPEC-1 §5.1——内核只暴露判定）。"""
        return RunMode(self.mode) is RunMode.FULL


@dataclass(frozen=True)
class BudgetState:
    """一次预算判定快照（§4.2；``budget_state``/``on_cost`` 的返回值）。"""

    mode: str                          # 入口档（= policy.mode）
    spent_cents: int                   # 累计已花费（美分）
    remaining_cents: int               # 预算余量（= budget − spent，可为负）
    quota_l2: int                      # L2 配额估计（按剩余预算重算）
    quota_s2: int                      # S2 容量估计（以 quota_l2 为存活数代理，见下）
    degraded: bool                     # True = 熔断已触发
    tier: str                          # 降级后的生效语义档（"full"→"pr"→"fast"）

    def as_dict(self) -> dict:
        """JSON 同构快照（黄金用例期望值同构；StrEnum→str、tuple→list 由调用方处理）。"""
        return asdict(self)


# ── 三档配额判定（§4.2 公式直译；全部纯函数）──────────────────────


def budget_for_mode(mode: RunMode | str, params: ParamsConfig) -> int:
    """入口档 → run 预算（美分）：fast→budget.FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS。"""
    key = {
        RunMode.FAST: params.budget.FAST_CENTS,
        RunMode.PR: params.budget.PR_CENTS,
        RunMode.FULL: params.budget.FULL_CENTS,
    }
    try:
        return key[RunMode(mode)]
    except ValueError:
        raise ValueError(
            f"未知入口档：{mode!r}（合法值 fast|pr|full，RunMode 词表）"
        ) from None


def quota_l2(remaining_cents: int, cost_per_l2_cents: int, *, cap: int) -> int:
    """§2.4 逐字：min(cap, ⌊remaining / cost⌋)；cost ≤ 0 或 remaining ≤ 0 → 0
    （未配置成本模型不派单，保守）。cap ≤ 0 同样归 0（无配额即不派）。"""
    if cost_per_l2_cents <= 0 or remaining_cents <= 0 or cap <= 0:
        return 0
    return min(int(cap), int(remaining_cents) // int(cost_per_l2_cents))


def quota_s2(l2_survivors: int, remaining_cents: int, cost_per_s2_cents: int) -> int:
    """§2.4 逐字：⌊存活数 × remaining / cost⌋；cost ≤ 0 → 0。
    remaining ≤ 0（熔断后余量耗尽）或存活数 ≤ 0 → 0（§4.3「不再派任何付费任务」）。"""
    if cost_per_s2_cents <= 0 or remaining_cents <= 0 or l2_survivors <= 0:
        return 0
    return (int(l2_survivors) * int(remaining_cents)) // int(cost_per_s2_cents)


def s2_rank_key(
    severity: str,
    type_node: str | None,
    confidence: float,
    *,
    run_cluster_counts: Mapping[str, int],   # 本 run 已出现的类型节点 → 簇数
) -> tuple[int, float, float]:
    """§2.4 S2 降序取的排序键（DECISIONS 2026-10-10-D15）：
    (SEVERITY_RANK[severity], scarcity, confidence) 三元组字典序降序。
    scarcity = 1 / (1 + run_cluster_counts.get(type_node, 0))——本 run 内越稀缺的
    类型越优先；不取乘积（零置信会把乘积归零吞掉 severity 主序）。
    type_node 为 None（家族级）按空串计（scarcity 最高，与「类型稀缺度」的稀疏优先一致）。
    平手序由调用方以 finding_id 字典序收口（§6.5，排序键本身不含 id）。
    """
    try:
        rank = SEVERITY_RANK[str(severity)]
    except KeyError:
        raise ValueError(
            f"未知 severity：{severity!r}（合法值 {'|'.join(SEVERITY_RANK)}）"
        ) from None
    node = "" if type_node is None else str(type_node)
    scarcity = 1.0 / (1.0 + run_cluster_counts.get(node, 0))
    return (rank, scarcity, float(confidence))


def budget_state(
    spent_cents: int,
    policy: BudgetPolicy,
    *,
    params: ParamsConfig | None = None,
) -> BudgetState:
    """熔断判定（纯函数）：spent ≥ budget → degraded=True（含等号，保守闭包，
    DECISIONS 2026-10-10-D16），tier 沿 full→pr→fast 阶梯落一档；quota 按剩余预算
    重算。未触发 → tier=policy.mode。

    - remaining_cents = budget − spent，可为负（超支量如实呈现；配额公式对
      remaining ≤ 0 恒 0，语义与 §4.3「不再派任何付费任务」一致）；
    - ``quota_s2`` 以当前 ``quota_l2`` 为「存活数代理」的容量上限估计（真实派单
      以实测存活数现算 :func:`quota_s2`——BudgetState 是无输入依赖的纯快照，
      代理口径记 DECISIONS 2026-10-10 budget 行）；
    - ``params`` 形参为 §4.2 签名兼容保留（成本估计经 policy 携带，当前无
      params 依赖；未来成本模型回填时不改签名）。
    """
    spent = int(spent_cents)
    remaining = int(policy.budget_cents) - spent
    degraded = spent >= int(policy.budget_cents)          # D16：含等号
    mode = str(policy.mode)
    tier = mode if not degraded else DEGRADED_TIER[mode]
    q_l2 = quota_l2(remaining, policy.cost_per_l2_cents, cap=policy.l2_cap)
    return BudgetState(
        mode=mode,
        spent_cents=spent,
        remaining_cents=remaining,
        quota_l2=q_l2,
        quota_s2=quota_s2(q_l2, remaining, policy.cost_per_s2_cents),
        degraded=degraded,
        tier=tier,
    )


# ── 成本入账与首次熔断（runs 表写口；SPEC-2 §6.1）─────────────────


def on_cost(
    ledger: KernelLedger,
    run_id: str,
    cents: int,
    *,
    policy: BudgetPolicy,
    note: str = "",
) -> BudgetState:
    """成本入账（runs.spent_cents += cents，runs 表写口在本模块，§6.1）并返回
    trip 后 BudgetState。**首次** trip：SCALING 事件（pool=f"mode:{policy.mode}",
    delta=-1, reason="budget_breaker"）+ runs.status='degraded'，与 spent 更新同事务
    （DECISIONS 2026-10-10-D17：不新增 LedgerKind，降级以既有 scaling 事件承载可审计
    轨迹，§11-3「一切伸缩可追溯」）；重复 trip 零新事件（幂等）。

    事务边界（§7.1/D24）：spent 更新、status 翻转、meta 注记与 SCALING 事件在
    单一 :func:`kernel.ledger.transaction` 边界内原子生效——外层已有事务时沿用
    外层边界（可重入），异常整体 ROLLBACK，不存在半写态。

    判定与守卫（记 DECISIONS 2026-10-10 budget 行）：
    - ``cents < 0`` → ValueError（成本只增不减，负数即契约违规）；
    - run_id 不在 runs 表 → ValueError；
    - policy.mode 与 runs.mode 不一致 → ValueError（pool 字面与入口档必须如实）；
    - 预算判据以 policy.budget_cents 为准；「已 trip」判定 =
      ``spent_before ≥ policy.budget_cents 或 status 已 degraded``——命中即只更新
      spent，不再产生任何新事件（§6.4 幂等）；
    - ``note`` 非空时写入 runs.meta_json 的 ``budget_note`` 键（审计注记；SCALING
      payload 词表冻结不承载自由文本）。
    """
    cents = int(cents)
    if cents < 0:
        raise ValueError(f"on_cost 拒绝负成本：{cents}（spent 单调不减）")
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT spent_cents, status, mode FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"on_cost：未知 run_id {run_id!r}（runs 表无该行）")
        spent_before = int(row[0])
        status_before = str(row[1])
        if str(row[2]) != str(policy.mode):
            raise ValueError(
                f"on_cost：policy.mode ({policy.mode!r}) 与 runs.mode "
                f"({row[2]!r}) 不一致（入口档必须如实）"
            )
        spent_after = spent_before + cents
        state = budget_state(spent_after, policy)
        tripped_before = (
            spent_before >= int(policy.budget_cents)
            or status_before == str(RunStatus.DEGRADED)
        )
        first_trip = state.degraded and not tripped_before
        sets = ["spent_cents = ?"]
        args: list[object] = [spent_after]
        if first_trip:
            sets.append("status = ?")
            args.append(str(RunStatus.DEGRADED))
        if note:
            meta_row = ledger.conn.execute(
                "SELECT meta_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            meta = json.loads(meta_row[0] or "{}")
            meta["budget_note"] = str(note)
            sets.append("meta_json = ?")
            args.append(json.dumps(meta, sort_keys=True, ensure_ascii=False))
        args.append(run_id)
        ledger.conn.execute(
            f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", tuple(args)
        )
        if first_trip:
            ledger.append(
                LedgerKind.SCALING,
                ScalingPayload(
                    pool=f"mode:{policy.mode}",
                    delta=-1,
                    reason=BREAKER_REASON,
                ),
                run_id=run_id,
            )
    return state
