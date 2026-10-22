"""exams.roster：候选集展开 + agents/hats 注册与 QC 流转（SPEC-6 §4.1/§4.5）。

候选集展开（S10 拍板）：角色 → 档位映射（§4.1 五型分工逐字：scout→cheap /
auditor→strong / reviewer→mid / verifier→engineering）；每档 primary + fallbacks
去重展开为候选模型；同 (role, model_ref) 唯一 candidate（§16.7-2「agent_id ≡
(模型, 契约版本)」的直接推论——同模型同契约不可能是两只蜂）。params.roster 是
M0 仿真规模（§16.7-7「仿真器 M0 校准规模」），不约束真实编制数；真实编制 =
供给面展开结果。

编制注册（agents + hats 两表唯一写口在本模块，SQL 直落）：幂等——agent_id 已
存在且字段全等 → 跳过；存在但字段不等 → ValueError（身份冲突，人工介入）。

角色资格写口（SPEC-10 §3/D46，P0）：role_qualifications 表唯一写口同在本模块
（record_qualification / revoke_qualification / migrate_qualifications）——帽
（attach_hat）只声明角色不授予资格；资格状态机 pending→passed/failed、
stale/revoked 与乱序写守卫见 SPEC-10 §3.2/§3.3。
"""

from __future__ import annotations

from dataclasses import dataclass

from qswarm.contracts import AgentStatus, PoolClass, QualificationStatus, Role
from qswarm.kernel import KernelLedger, iso_utc, transaction
from qswarm.prompts import CONTRACT_VERSION
from qswarm.supply import ROLE_TIER, ModelsConfig

__all__ = [
    "Candidate",
    "expand_candidates",
    "import_roster_rows",
    "register_roster",
    "apply_qc_decision",
    "attach_hat",
    "record_qualification",
    "revoke_qualification",
    "migrate_qualifications",
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


def attach_hat(ledger: KernelLedger, agent_id: str, role: str) -> bool:
    """给既有 agent 加戴角色帽（D31 帽兼任扩池；SPEC-8 §7）。

    - 帽注册唯一写口仍在本模块（hats INSERT 面与 register_roster 同表同纪律）；
    - agent 必须已存在且 status='active'（帽不授予未入编蜂）；
    - role 越五角色词表 → ValueError；同 (agent_id, role) 帽已在 → 幂等跳过；
    - pool_class：scout=temp、其余 formal（register_roster 同式）；
    - 返回是否新注册（False = 已在）。
    """
    if role not in {r.value for r in Role}:
        raise ValueError(
            f"角色越词表：{role!r}（合法值：{sorted(r.value for r in Role)}）"
        )
    pool_class = PoolClass.TEMP.value if role == Role.SCOUT.value else PoolClass.FORMAL.value
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"agent 不存在：{agent_id!r}（帽只授予在编蜂）")
        if str(row[0]) != AgentStatus.ACTIVE.value:
            raise ValueError(
                f"agent {agent_id!r} 非 active（status={row[0]!r}）——帽兼任只对在编蜂开放"
            )
        cur = ledger.conn.execute(
            "INSERT OR IGNORE INTO hats (agent_id, role, pool_class) VALUES (?, ?, ?)",
            (agent_id, role, pool_class),
        )
        return cur.rowcount > 0


# ── 角色资格写口（SPEC-10 §3/D46；role_qualifications 表唯一写口）──────────

_TERMINAL_STATUSES = frozenset(
    {QualificationStatus.PASSED, QualificationStatus.FAILED,
     QualificationStatus.STALE, QualificationStatus.REVOKED}
)


def _ensure_rq_table(ledger: KernelLedger) -> None:
    """既有账本自愈建表（SPEC-10 §3.4）：DDL 逐字取自 contracts/schema.sql
    单一真源，IF NOT EXISTS 幂等——新库由全量建库承载，旧库首写补齐。"""
    from qswarm.contracts import schema_sql

    for stmt in schema_sql().split(";"):
        s = stmt.strip()
        if s and "role_qualifications" in s and (
            "CREATE TABLE" in s.upper() or "CREATE INDEX" in s.upper()
        ):
            ledger.conn.execute(s)


def _next_rq_id(ledger: KernelLedger) -> str:
    row = ledger.conn.execute(
        "SELECT MAX(CAST(substr(id, 4) AS INTEGER)) FROM role_qualifications"
    ).fetchone()
    return f"RQ-{(int(row[0]) if row and row[0] is not None else 0) + 1}"


def _effective_passed(
    ledger: KernelLedger, agent_id: str, role: str, profile_id: str, policy_version: str
) -> tuple[str, str] | None:
    """最新 effective passed 行 → (id, started_at)；查无 → None（SPEC-10 §3.1）。"""
    row = ledger.conn.execute(
        "SELECT id, started_at FROM role_qualifications"
        " WHERE agent_id = ? AND role = ? AND profile_id = ? AND policy_version = ?"
        " AND status = 'passed' AND superseded_by IS NULL"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (agent_id, role, profile_id, policy_version),
    ).fetchone()
    return (str(row[0]), str(row[1])) if row is not None else None


def record_qualification(
    ledger: KernelLedger,
    *,
    agent_id: str,
    role: str,
    profile_id: str,
    policy_version: str,
    status: str,
    started_at: str,
    reason: str | None = None,
    exam_run_id: str | None = None,
    bank_version: int | None = None,
    paper_id: str | None = None,
    scoring_version: str | None = None,
    accuracy: float | None = None,
) -> str:
    """资格行签发（pending / 终态；SPEC-10 §3.2/§3.3）。返回新行 id。

    - ``status`` ∈ QualificationStatus（pending 不替代旧行；终态行替代同键全部
      未替代 pending/passed）；
    - 乱序写守卫：同键存在 effective passed 且其 started_at 严格晚于本行 →
      ValueError（旧考试轮不得覆盖新资格；并发考试/资格更新不互相降级）。"""
    if status not in {s.value for s in QualificationStatus}:
        raise ValueError(
            f"资格状态越词表：{status!r}（合法值：{sorted(s.value for s in QualificationStatus)}）"
        )
    with transaction(ledger):
        _ensure_rq_table(ledger)
        prior = _effective_passed(ledger, agent_id, role, profile_id, policy_version)
        if prior is not None and status != QualificationStatus.PASSED.value:
            if str(prior[1]) > started_at:
                raise ValueError(
                    f"乱序资格写被拒：agent {agent_id!r} role {role!r} 已有更晚 "
                    f"passed（started_at={prior[1]} > {started_at}）——旧结果不得覆盖新资格"
                )
        new_id = _next_rq_id(ledger)
        ledger.conn.execute(
            "INSERT INTO role_qualifications (id, agent_id, role, profile_id,"
            " policy_version, status, reason, exam_run_id, bank_version, paper_id,"
            " scoring_version, accuracy, started_at, superseded_by, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                new_id, agent_id, role, profile_id, policy_version, status, reason,
                exam_run_id, bank_version, paper_id, scoring_version, accuracy,
                started_at, iso_utc(ledger._clock.now()),
            ),
        )
        if status in {s.value for s in _TERMINAL_STATUSES}:
            ledger.conn.execute(
                "UPDATE role_qualifications SET superseded_by = ?"
                " WHERE agent_id = ? AND role = ? AND profile_id = ?"
                " AND policy_version = ? AND status IN ('pending', 'passed')"
                " AND superseded_by IS NULL AND id != ?",
                (new_id, agent_id, role, profile_id, policy_version, new_id),
            )
    return new_id


def revoke_qualification(
    ledger: KernelLedger,
    *,
    agent_id: str,
    role: str,
    profile_id: str,
    policy_version: str,
    reason: str,
    started_at: str | None = None,
) -> str:
    """管理撤销（SPEC-10 §3.2；终态 revoked 替代既有 passed——dispatch 前复验
    据此阻断排队中任务）。返回新行 id。"""
    if not reason:
        raise ValueError("revoke_qualification 需要 reason（撤销不留无因行）")
    return record_qualification(
        ledger,
        agent_id=agent_id, role=role, profile_id=profile_id,
        policy_version=policy_version, status=QualificationStatus.REVOKED.value,
        started_at=started_at if started_at is not None else iso_utc(ledger._clock.now()),
        reason=reason,
    )


def migrate_qualifications(
    ledger: KernelLedger,
    *,
    dry_run: bool = True,
    policy_version: str = "qp-v1",
    production_temp: float = 0.2,
    supply_temp: float | None = None,
    now: str | None = None,
    profile_fn=None,
) -> dict:
    """历史编制 → 资格投影迁移（SPEC-10 §4；方案 §3.3 逐字：无法证明者 stale）。

    - 判定器恒判 ``stale(reason=legacy_unverifiable)``：历史行无 profile 记录，
      不通过既有帽或正 cells 反推合格；profile_id 取当前配置计算的现值（标记
      「现配置下已知未证」）；
    - ``dry_run=True``（缺省）零写只出 manifest；幂等：同键已有非替代 stale 行
      跳过；同键已有 effective passed（迁移后考取）→ 跳过（迁移永不降级真资格）；
    - ``supply_temp``：models.yaml runtime 参考值（可注入对账）——与
      ``production_temp`` 不等时 manifest 顶层 ``temp_anchor_mismatch=true``
      如实披露（SPEC-10 §4.3，不静默）。"""
    profile_fn = profile_fn if profile_fn is not None else _default_profile_fn
    stamp = now if now is not None else iso_utc(ledger._clock.now())
    rows_out: list[dict] = []
    written = would = skipped = 0
    with transaction(ledger):
        _ensure_rq_table(ledger)
        pairs = ledger.conn.execute(
            "SELECT h.agent_id, h.role, a.model_ref FROM hats h"
            " JOIN agents a ON a.agent_id = h.agent_id ORDER BY h.agent_id, h.role"
        ).fetchall()
        for agent_id, role, model_ref in pairs:
            pid = profile_fn(str(model_ref), str(role), production_temp=production_temp)
            existing = ledger.conn.execute(
                "SELECT status, reason, superseded_by FROM role_qualifications"
                " WHERE agent_id = ? AND role = ? AND profile_id = ?"
                " AND policy_version = ?"
                " ORDER BY started_at DESC, id DESC LIMIT 1",
                (agent_id, role, pid, policy_version),
            ).fetchone()
            if existing is not None and (
                existing[2] is None and existing[0] in
                (QualificationStatus.PASSED.value, QualificationStatus.STALE.value,
                 QualificationStatus.FAILED.value, QualificationStatus.REVOKED.value)
            ):
                action = "skip"
                skipped += 1
            elif dry_run:
                action = "would_write"
                would += 1
            else:
                record_qualification(
                    ledger,
                    agent_id=str(agent_id), role=str(role), profile_id=pid,
                    policy_version=policy_version,
                    status=QualificationStatus.STALE.value,
                    started_at=stamp, reason="legacy_unverifiable",
                )
                action = "written"
                written += 1
            rows_out.append({
                "agent_id": str(agent_id),
                "role": str(role),
                "profile_id": pid,
                "status": "stale",
                "reason": "legacy_unverifiable",
                "action": action,
            })
    return {
        "kind": "qualification_migration_manifest",
        "spec": "SPEC-10 §4",
        "dry_run": bool(dry_run),
        "policy_version": policy_version,
        "profile_temp_anchor": production_temp,
        "supply_temp": supply_temp,
        "temp_anchor_mismatch": (
            supply_temp is not None and float(supply_temp) != float(production_temp)
        ),
        "pairs_total": len(rows_out),
        "stale_written": written,
        "would_write": would,
        "skipped": skipped,
        "rows": rows_out,
    }


def _default_profile_fn(model_ref: str, role: str, *, production_temp: float) -> str:
    """延迟导入内核只读面（exams → kernel 单向依赖，与既有 import 同向）。"""
    from qswarm.kernel.qualification import profile_id as _profile_id

    return _profile_id(model_ref, role, production_temp=production_temp)
