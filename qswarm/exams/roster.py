"""exams.roster：候选集展开 + agents/hats 注册与 QC 流转（SPEC-6 §4.1/§4.5）。

候选集展开（S10 拍板）：角色 → 档位映射（§4.1 五型分工逐字：scout→cheap /
auditor→strong / reviewer→mid / verifier→engineering）；每档 primary + fallbacks
去重展开为候选模型；同 (role, model_ref) 唯一 candidate（§16.7-2「agent_id ≡
(模型, 契约版本)」的直接推论——同模型同契约不可能是两只蜂）。params.roster 是
M0 仿真规模（§16.7-7「仿真器 M0 校准规模」），不约束真实编制数；真实编制 =
供给面展开结果。

编制注册（agents + hats 两表唯一写口在本模块，SQL 直落）：幂等——agent_id 已
存在且字段全等 → 跳过；存在但字段不等 → ValueError（身份冲突，人工介入）。
"""

from __future__ import annotations

from dataclasses import dataclass

from qswarm.contracts import AgentStatus, PoolClass, Role
from qswarm.kernel import KernelLedger, iso_utc, transaction
from qswarm.prompts import CONTRACT_VERSION
from qswarm.supply import ROLE_TIER, ModelsConfig

__all__ = [
    "Candidate",
    "expand_candidates",
    "import_roster_rows",
    "register_roster",
    "apply_qc_decision",
]


@dataclass(frozen=True)
class Candidate:
    """一只候选蜂（展开结果；agent_id ≡ model_ref@CONTRACT_VERSION）。"""

    role: str            # Role.value
    tier: str            # 档位（ROLE_TIER 映射）
    model_ref: str       # "{tier}:{provider}/{model}"
    agent_id: str        # f"{model_ref}@{CONTRACT_VERSION}"


def expand_candidates(
    models: ModelsConfig,
    *,
    roles: tuple[str, ...] | None = None,
) -> list[Candidate]:
    """候选集展开（S10）：角色档位映射 × 档位条目（primary+fallbacks）去重。

    - roles 缺省 = 全部四角色（ROLE_TIER 键序：scout/auditor/reviewer/verifier）；
      v0 播种由调用方裁剪为 (scout, auditor)（S4：reviewer/verifier 注册不参考，
      随其生产面在 M3 补播时注册）；
    - 档位在 models.yaml 缺失 → ValueError（编制展开需该档位就位）；
    - 同档条目按 primary → fallbacks 序去重（同 model_ref 只展开一次）。"""
    role_keys = tuple(roles) if roles is not None else tuple(ROLE_TIER)
    out: list[Candidate] = []
    for role_key in role_keys:
        tier = ROLE_TIER.get(role_key)
        if tier is None:
            raise ValueError(f"角色无档位映射：{role_key!r}（§4.1 五型分工表）")
        tier_cfg = models.tiers.get(tier)
        if tier_cfg is None:
            raise ValueError(
                f"models.yaml 缺档位 {tier!r}（角色 {role_key!r} 无法展开编制）"
            )
        seen: set[str] = set()
        for entry in (tier_cfg.primary, *tier_cfg.fallbacks):
            model_ref = f"{tier}:{entry.provider}/{entry.model}"
            if model_ref in seen:
                continue
            seen.add(model_ref)
            out.append(
                Candidate(
                    role=role_key,
                    tier=tier,
                    model_ref=model_ref,
                    agent_id=f"{model_ref}@{CONTRACT_VERSION}",
                )
            )
    return out


def register_roster(
    ledger: KernelLedger,
    candidates: list[Candidate],
) -> list[str]:
    """agents + hats 注册（注册序 = 传入序 = agents 表插入序；返回注册清单）。

    - agents: agent_id = f"{model_ref}@{CONTRACT_VERSION}"、status='provisioning'、
      model_ref、contract_version=CONTRACT_VERSION；
    - hats: (agent_id, role, pool_class)——scout=temp（临时工，§9.2）、
      auditor/reviewer/verifier=formal（verifier v0 取骨干口径）；
    - 幂等：agent_id 已存在且字段全等 → 跳过；存在但字段不等 → ValueError
      （身份冲突，人工介入）。"""
    registered: list[str] = []
    with transaction(ledger):
        for cand in candidates:
            row = ledger.conn.execute(
                "SELECT model_ref, contract_version FROM agents WHERE agent_id = ?",
                (cand.agent_id,),
            ).fetchone()
            if row is not None:
                if str(row[0]) != cand.model_ref or str(row[1]) != CONTRACT_VERSION:
                    raise ValueError(
                        f"身份冲突：agent {cand.agent_id!r} 已存在且字段不等"
                        f"（model_ref={row[0]!r} cv={row[1]!r}）——人工介入"
                    )
                continue
            ledger.conn.execute(
                "INSERT INTO agents (agent_id, model_ref, contract_version, status,"
                " created_at, last_active_at) VALUES (?, ?, ?, ?, ?, NULL)",
                (
                    cand.agent_id, cand.model_ref, CONTRACT_VERSION,
                    AgentStatus.PROVISIONING.value, iso_utc(ledger._clock.now()),
                ),
            )
            pool_class = PoolClass.TEMP if cand.role == Role.SCOUT.value else PoolClass.FORMAL
            ledger.conn.execute(
                "INSERT INTO hats (agent_id, role, pool_class) VALUES (?, ?, ?)",
                (cand.agent_id, cand.role, pool_class.value),
            )
            registered.append(cand.agent_id)
    return registered


def import_roster_rows(
    ledger: KernelLedger,
    *,
    agents: list[tuple],
    hats: list[tuple],
) -> tuple[int, int]:
    """agents/hats 全字段逐字导入（D28 生产账本引导；SPEC-7 §3.1）。

    agents/hats 两表唯一写口仍在本模块（SPEC-2 §6.1 + M2 写口表扩展纪律）——
    生产账本的编制导入不得在词表外长出第二写口，故由 kernel.bootstrap（编排）
    读源库后调本函数落行（DECISIONS M3 实现期行）。

    - ``agents`` 行形 = (agent_id, model_ref, contract_version, status,
      created_at, last_active_at)；``hats`` 行形 = (agent_id, role, pool_class)；
    - 幂等：同 id 全字段相等 → 跳过；字段不等 → ValueError（生产账本编制不可
      被导入静默改写，与 :func:`register_roster` 同口径）；
    - 返回 (agents_imported, agents_skipped)。"""
    imported = skipped = 0
    with transaction(ledger):
        for agent_id, model_ref, cv, status, created_at, last_active_at in agents:
            row = ledger.conn.execute(
                "SELECT model_ref, contract_version, status, created_at,"
                " last_active_at FROM agents WHERE agent_id = ?",
                (agent_id,),
            ).fetchone()
            if row is not None:
                same = (
                    str(row[0]) == model_ref
                    and str(row[1]) == cv
                    and str(row[2]) == status
                    and str(row[3]) == created_at
                    and (row[4] == last_active_at
                         or (row[4] is None and last_active_at is None))
                )
                if not same:
                    raise ValueError(
                        f"编制身份冲突：agent {agent_id!r} 已在生产账本且字段不等"
                        "（人工介入——生产账本编制不可被导入静默改写）"
                    )
                skipped += 1
                continue
            ledger.conn.execute(
                "INSERT INTO agents (agent_id, model_ref, contract_version, status,"
                " created_at, last_active_at) VALUES (?, ?, ?, ?, ?, ?)",
                (agent_id, model_ref, cv, status, created_at, last_active_at),
            )
            imported += 1
        for agent_id, role, pool_class in hats:
            ledger.conn.execute(
                "INSERT OR IGNORE INTO hats (agent_id, role, pool_class) VALUES (?, ?, ?)",
                (agent_id, role, pool_class),
            )
    return imported, skipped


def apply_qc_decision(ledger: KernelLedger, agent_id: str, *, passed: bool) -> None:
    """QC 流转（§4.5）：通过 → active + last_active_at 置位；不通过 → 留
    provisioning + 告警（告警清单进播种报告，调用方承载）。

    质检线是入编闸门，不是播种开关：无论 QC 结果，全部有效格的考试存款照写
    （S11——落选者的历史存款随 dormant 语义衰减）。"""
    with transaction(ledger):
        if passed:
            ledger.conn.execute(
                "UPDATE agents SET status = ?, last_active_at = ? WHERE agent_id = ?",
                (AgentStatus.ACTIVE.value, iso_utc(ledger._clock.now()), agent_id),
            )
        else:
            ledger.conn.execute(
                "UPDATE agents SET status = ? WHERE agent_id = ?",
                (AgentStatus.PROVISIONING.value, agent_id),
            )
