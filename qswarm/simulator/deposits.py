"""存款归属（设计 §6.5 全角色归属矩阵；SPEC-4 §1.4）。

三条规格纪律的直译：

- 账本语义极简：存款只有 S1/S2/S3 三种信号源，但**归属四类角色**（§6.2/§6.5）；
- verify_error / expired **零存款**（§6.2：不产生任何进化压力；expired 同理，
  归 SPEC-2 状态机管，本模块只保证 verify_error 不发存款）；
- 评审蜂投票当下零收益（纪律 2）：verdict 到达时回看投票方向——与 verdict 同向
  +、反向 −，系数 REVIEWER_X；验证蜂只在人工裁决时记：S3 推翻 S2 → −VERIFIER_CONFLICT、
  S3 确认 S2 → +VERIFIER_CONFIRM（防 S2 自证，§6.5 验证行）。

角色系数取 params.yaml deposits 节（§1.4：系数取 params）；reporter 系数按其角色：
审查蜂 ×1.0（AUDITOR_X，规格定值）/ 侦察蜂 ×0.2（SCOUT_X）。§16.7-1：fast 档
reporter=侦察蜂，verdict 按侦察行记——fast 档是侦察蜂口碑的主供血通道。

落账经 kernel.ledger.append（SPEC-4 §0：仿真复用内核实现；append 是 ledger 唯一
写口，payload 先过 contracts.PAYLOAD_SCHEMA 校验）。S3 行落账时刻为
arrival_day（task.day + lag），S1/S2 行为 task.day——deposit_schedule 给出 (日, payload)
调度，供后续 harness 按日喂账本与挥发计算。
"""

from __future__ import annotations

from dataclasses import dataclass

from qswarm.contracts import (
    DepositOrigin,
    DepositPayload,
    LedgerKind,
    ParamsConfig,
    Role,
    SignalTier,
)
from qswarm.kernel import KernelLedger, LedgerEvent

from .verdict import FindingContext, S2_SOURCE_WEIGHT, S3_SOURCE_WEIGHT, VerdictResult

AUDITOR_X = 1.0  # §6.5 审查蜂行系数（规格定值，非 params 旋钮）


def reporter_coefficient(ctx: FindingContext, params: ParamsConfig) -> float:
    """reporter 行系数：审查蜂 ×1.0 / 侦察蜂 ×SCOUT_X（§6.5 + §16.7-1）。"""
    if ctx.reporter_role is Role.AUDITOR:
        return AUDITOR_X
    if ctx.reporter_role is Role.SCOUT:
        return params.deposits.SCOUT_X
    raise ValueError(f"reporter 角色必须是 auditor/scout，实际 {ctx.reporter_role}")


@dataclass(frozen=True)
class DepositRecord:
    """一笔存款及其落账日（S1/S2 = task.day；S3 = task.day + lag）。"""

    day: float
    payload: DepositPayload


def deposit_schedule(
    ctx: FindingContext, result: VerdictResult, params: ParamsConfig
) -> list[DepositRecord]:
    """按 §6.5 矩阵把一个 finding 的三层 verdict 展开成存款调度表。

    行序固定：S1（reporter → scouts → reviewers）→ S2（同序）→ S3（同序 + 验证蜂行）；
    同日内保持该序，S3 因 lag ≥ 1d 恒落在 S1/S2 之后。
    """
    tax = [ctx.task.family, ctx.task.type]
    day = float(ctx.task.day)
    rep_coef = reporter_coefficient(ctx, params)
    out: list[DepositRecord] = []

    def rows(tier: SignalTier, direction: int, source_w: float, at_day: float) -> None:
        # 审查蜂行：簇 verdict 方向，源权重 ×1.0（或 fast 侦察 reporter 按侦察行 ×SCOUT_X）
        out.append(
            DepositRecord(
                at_day,
                DepositPayload(
                    finding=ctx.finding_id,
                    cluster=ctx.cluster_id,
                    agent=ctx.reporter_id,
                    role=ctx.reporter_role,
                    taxonomy_path=list(tax),
                    signal_tier=tier,
                    direction=direction,
                    weight=source_w * rep_coef,
                    origin=DepositOrigin.PROD,
                ),
            )
        )
        # 侦察蜂行：其标记候选点所在簇的 verdict，×SCOUT_X（precision 型防滥报，§6.5）
        for sid in ctx.cluster_scouts:
            out.append(
                DepositRecord(
                    at_day,
                    DepositPayload(
                        finding=ctx.finding_id,
                        cluster=ctx.cluster_id,
                        agent=sid,
                        role=Role.SCOUT,
                        taxonomy_path=list(tax),
                        signal_tier=tier,
                        direction=direction,
                        weight=source_w * params.deposits.SCOUT_X,
                        origin=DepositOrigin.PROD,
                    ),
                )
            )
        # 评审蜂行：回看投票方向，与 verdict 同向 + / 反向 −，×REVIEWER_X（§6.5）
        for v in result.votes:
            sign = 1 if v.direction == direction else -1
            out.append(
                DepositRecord(
                    at_day,
                    DepositPayload(
                        finding=ctx.finding_id,
                        cluster=ctx.cluster_id,
                        agent=v.reviewer_id,
                        role=Role.REVIEWER,
                        taxonomy_path=list(tax),
                        signal_tier=tier,
                        direction=sign,
                        weight=source_w * params.deposits.REVIEWER_X,
                        origin=DepositOrigin.PROD,
                    ),
                )
            )

    # ── S1：panel 共识（fast 档无 panel → 无 S1 事件）────────────
    if result.s1_direction is not None:
        rows(SignalTier.S1, result.s1_direction, result.s1_weight, day)

    # ── S2：仅确定结论入账；verify_error 零存款（§6.2）──────────
    if result.s2_concluded:
        assert result.s2_direction is not None
        rows(SignalTier.S2, result.s2_direction, S2_SOURCE_WEIGHT, day)

    # ── S3：延迟到达，按 arrival_day 落账；验证蜂行只在此记（§6.5）──
    if result.s3 is not None:
        assert result.s3_direction is not None
        rows(SignalTier.S3, result.s3_direction, S3_SOURCE_WEIGHT, result.s3.arrival_day)
        if result.s2_concluded:
            # 验证蜂行：S3 推翻 S2 → −VERIFIER_CONFLICT；确认 → +VERIFIER_CONFIRM
            if ctx.verifier_id is None:
                raise ValueError("S2 有结论且 S3 到达，但 ctx.verifier_id 缺失（无法归属验证蜂）")
            if result.s3.direction == result.s2_direction:
                v_dir, v_weight = 1, params.deposits.VERIFIER_CONFIRM
            else:
                v_dir, v_weight = -1, params.deposits.VERIFIER_CONFLICT
            out.append(
                DepositRecord(
                    result.s3.arrival_day,
                    DepositPayload(
                        finding=ctx.finding_id,
                        cluster=ctx.cluster_id,
                        agent=ctx.verifier_id,
                        role=Role.VERIFIER,
                        taxonomy_path=list(tax),
                        signal_tier=SignalTier.S3,
                        direction=v_dir,
                        weight=v_weight,
                        origin=DepositOrigin.PROD,
                    ),
                )
            )
    return out


def attribute_deposits(
    ctx: FindingContext, result: VerdictResult, params: ParamsConfig
) -> list[DepositPayload]:
    """§6.5 归属：返回按落账日稳定排序的存款载荷（S1 → S2 → S3）。"""
    records = sorted(deposit_schedule(ctx, result, params), key=lambda r: r.day)
    return [r.payload for r in records]


def append_deposits(
    ledger: KernelLedger,
    payloads: list[DepositPayload],
    *,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """存款经 kernel.ledger.append 落账（唯一写口；payload 过 PAYLOAD_SCHEMA 校验）。"""
    return [ledger.append(LedgerKind.DEPOSIT, p, run_id=run_id) for p in payloads]


__all__ = [
    "AUDITOR_X",
    "DepositRecord",
    "append_deposits",
    "attribute_deposits",
    "deposit_schedule",
    "reporter_coefficient",
]
