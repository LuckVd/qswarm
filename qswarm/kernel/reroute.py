"""kernel.reroute：re_route 事件与侦察蜂类型猜测格弱存款（SPEC-2 §3；设计 §6.5 附带）。

机制语义（§3.1 逐条可溯源）：

- **侦察蜂的初步类型猜测**作为 L2 路由的 taxonomy 初值（§2.1 L1 初筛）；
- **路由初值随 guesses 口碑加权**（§6.5 附带）：猜测格 = ``(scout, role=scout,
  node=猜测路径)``，其口碑（C*）参与猜测聚合（:func:`guess_taxonomy`）；
- **re_route 事件**（审查蜂产出与派单契约类型错配）→ 对猜测格记 **−0.05 弱存款**
  （§6.5 附带逐字；:data:`RE_ROUTE_WEAK_W` 为幅值，方向由 direction=−1 承载）；
- **触发裁定**（DECISIONS 2026-10-10-D12）：内核**确定性比对**——产出 finding 的
  ``(family, type)`` 与派单契约 ``taxonomy_node`` 不一致即触发，无需蜂显式声明
  字段：错配 = 家族段不同，**或**（家族相同 ∧ 双方都含类型段 ∧ 类型不同）；
  家族级契约 + 更细类型产出 = **细化不是错配**，不触发；每个 finding 至多触发
  一次（首错配生效）。

弱存款标准形（§3.2/§5.2 表末行，D13）：每 scout_id 一笔 DepositPayload——
``agent=scout_id, role=scout, taxonomy_path=from_node 路径段（猜测即被证伪的
路由初值）, signal_tier=S1, direction=−1, weight=0.05, origin=prod,
src_seq=<re_route 事件 seq>``。记 S1 档的理由（D13）：弱存款是生产面最低档信号，
且 S1 不计晋升硬门槛 n_eff_s2s3（§6.1/§7.3）——「弱」字的结构化表达。存款经
:func:`KernelLedger.append` 落账，cells 投影由既有唯一聚合写口
（``kernel.cells.agg_cells``，§6.1 写口表）折叠——本模块零表写，只落账本事件。

确定性（§6.5）：纯函数零随机源（不接随机数流参数、不消费任何随机源）；多 scout 存款
按 agent 字典序落账（§6.5 侦察行
agent 字典序）；guess_taxonomy 平手按票数 → C* 和 → 路径字典序全序裁决。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from qswarm.contracts import (
    DepositOrigin,
    DepositPayload,
    LedgerKind,
    Role,
    ReRoutePayload,
    ScoutReport,
    SignalTier,
)

from .cells import ROOT_PRIOR, CellView
from .ledger import KernelLedger, LedgerEvent, transaction

__all__ = [
    "RE_ROUTE_WEAK_W",
    "guess_taxonomy",
    "is_mismatch",
    "re_route_finding",
]

#: 猜测错配弱存款幅值（§6.5 附带逐字 -0.05；模块常量不进 params）。
RE_ROUTE_WEAK_W: float = 0.05


def _split_node(node: str) -> tuple[str, str | None]:
    """``'family[/type]'`` → (family, type|None)；空段视为无类型段。"""
    family, _, typ = str(node).partition("/")
    family = family.strip()
    typ = typ.strip() if typ else None
    if not family:
        raise ValueError(f"taxonomy 节点必须以非空家族开头：{node!r}")
    return family, (typ or None)


def is_mismatch(from_node: str, to_node: str) -> bool:
    """§3.1 触发裁定的纯函数（D12）：家族段不同，或同族双类型段且类型不同。

    家族级契约（from 无类型段）+ 更细类型产出（to 有类型段）= 细化不是错配；
    同理，一方缺类型段即不构成「双类型段不同」。两侧节点形状非法（空家族段）
    → ValueError。
    """
    from_family, from_type = _split_node(from_node)
    to_family, to_type = _split_node(to_node)
    if from_family != to_family:
        return True
    if from_type is not None and to_type is not None and from_type != to_type:
        return True
    return False


def re_route_finding(
    ledger: KernelLedger,
    finding_id: str,
    *,
    from_node: str,                        # 派单契约 taxonomy_node
    to_node: str,                          # 产出 finding 的 (family[/type])
    scout_ids: Sequence[str] = (),         # 猜测来源侦察蜂（弱存款归属；空 = L0 直出无猜测者）
    reason: str = "type_mismatch",
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """re_route 事件 + 猜测格弱存款，同事务（§3.2；SPEC-5 §1.3 标准形）：

    - 事件：kind=re_route，payload {finding_id, from_node, to_node, reason}
      （ReRoutePayload 逐字，SPEC-1 §4.3）；
    - 弱存款：每个 scout_id 一笔 DepositPayload——
      ``agent=scout_id, role=scout, taxonomy_path=from_node 路径段（猜测即被证伪的
      路由初值）, signal_tier=S1, direction=-1, weight=RE_ROUTE_WEAK_W, origin=prod,
      src_seq=<re_route 事件 seq>``；多 scout 按 agent 字典序落账（§6.5）；
    - 幂等：同 finding 已有 re_route 事件 → 零新事件（§6.4，读侧幂等 → 跳过）；
    - 触发守卫（D12 内核确定性比对落位在本模块唯一公共入口）：
      ``is_mismatch(from_node, to_node)`` 为假（含家族级→类型级细化）→ ValueError
      零落库——细化是正常流程，不是 re_route 业务事实。
    """
    existing = ledger.read(kinds=(LedgerKind.RE_ROUTE,))
    if any(ev.payload.get("finding_id") == str(finding_id) for ev in existing):
        return []                            # 每 finding 至多一次（§3.2/§6.4）
    if not is_mismatch(from_node, to_node):
        raise ValueError(
            f"非错配不触发 re_route：{from_node!r} → {to_node!r}"
            "（家族级→类型级细化是正常流程，D12/§3.1）"
        )
    taxonomy_path = [seg for seg in str(from_node).split("/") if seg]
    events: list[LedgerEvent] = []
    with transaction(ledger):
        ev = ledger.append(
            LedgerKind.RE_ROUTE,
            ReRoutePayload(
                finding_id=str(finding_id),
                from_node=str(from_node),
                to_node=str(to_node),
                reason=reason,
            ),
            run_id=run_id,
        )
        events.append(ev)
        for scout in sorted({str(s) for s in scout_ids}):
            events.append(
                ledger.append(
                    LedgerKind.DEPOSIT,
                    DepositPayload(
                        finding=str(finding_id),
                        cluster=None,
                        agent=scout,
                        role=Role.SCOUT,
                        taxonomy_path=taxonomy_path,
                        signal_tier=SignalTier.S1,
                        direction=-1,
                        weight=RE_ROUTE_WEAK_W,
                        origin=DepositOrigin.PROD,
                        src_seq=ev.seq,
                    ),
                    run_id=run_id,
                )
            )
    return events


def guess_taxonomy(
    reports: Sequence[ScoutReport],
    cells: CellView,
    *,
    scout_of: Mapping[str, str] | None = None,
) -> tuple[str, ...] | None:
    """L1 猜测聚合 → L2 路由 taxonomy 初值（§2.1/§6.5「路由初值随 guesses 口碑加权」）。

    - 候选 = reports 中 (guess_family, guess_type) 非空的 distinct 猜测路径集合；
    - 得分序（降序取首）：票数 → 猜测格 C* 之和 → 猜测路径字典序升序。猜测格
      C* = Σ cells.c_star(scout, "scout", 猜测node)，**无格子按 ROOT_PRIOR 计**
      ——CellView.c_star 对无存款链路本就收敛到 root_prior；猜测来源谱系
      （candidate→scout）由调用方经 ``scout_of`` 显式接参（D14 谱系显式接参、
      不改 SPEC-1 DDL 的同型落位；缺省/未命中 = 无格子 → ROOT_PRIOR，DECISIONS
      2026-10-10 reroute 行）；
    - 无任何猜测 → None（调用方退家族级路由）。纯函数零随机源；C* 和用 math.fsum
      按票面序聚合（同输入序列逐位一致）。
    """
    votes: dict[tuple[str, ...], int] = {}
    cvals: dict[tuple[str, ...], list[float]] = {}
    lineage = scout_of or {}
    for r in reports:
        if r.guess_family is None:
            continue                          # 无家族段不成路径
        path = (str(r.guess_family),)
        if r.guess_type:
            path = path + (str(r.guess_type),)
        votes[path] = votes.get(path, 0) + 1
        scout = lineage.get(r.candidate_id)
        if scout is None:
            cvals.setdefault(path, []).append(ROOT_PRIOR)   # 无谱系 ≡ 无格子（保守同型）
        else:
            cvals.setdefault(path, []).append(
                cells.c_star(str(scout), str(Role.SCOUT), "/".join(path))
            )
    if not votes:
        return None
    def order_key(path: tuple[str, ...]):
        return (-votes[path], -math.fsum(cvals.get(path, ())), "/".join(path))
    return min(votes, key=order_key)
