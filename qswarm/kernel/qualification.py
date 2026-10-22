"""kernel.qualification：角色资格读取面（SPEC-10 §2/§5；只读，零 SQL 写）。

身份模型（方案 §3.1）：**execution_profile_id = 执行配置的规范化哈希**，涵盖
provider、请求模型、角色、prompt 内容哈希、上下文策略版本、契约版本与采样
温度锚——profile 变更（prompt 改版 / contract 升版 / 调温）⇒ 新 profile，旧
资格不自动沿用（读侧按当前 profile 查 effective passed，查无即拒派，fail-closed）。

三概念分立（SPEC-10 §1）：本模块只回答 Qualification（这个配置是否被允许执行
这个角色）；不回答 finding verdict，也不冒充能力估计。写口在 exams/roster.py
（SPEC-10 §3.4），本模块零 INSERT/UPDATE/DELETE。

门禁（§5）：`enforce=False` 或 role ∉ enforce_roles → :func:`gate_from_params`
返回 None，调用方零行为差（M5 逐位兼容）；启用后缺记录/pending/failed/stale/
revoked 一律拒派——缺配置不回退漏洞模式（安全门例外，方案 §8）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from qswarm.contracts import ParamsConfig
from qswarm.kernel.ledger import KernelLedger
from qswarm.prompts import CONTRACT_VERSION, FAMILY_GLOSS, SCHEMA_FIELDS_BY_ROLE, SYSTEM_BY_ROLE

__all__ = [
    "CTX_STRATEGY_VERSION",
    "QualificationGate",
    "current_prompt_hash",
    "gate_from_params",
    "profile_id",
]


#: 工具/上下文策略版本（SPEC-10 §2.1；repomap/切片策略语义变更时升版）。
CTX_STRATEGY_VERSION = "ctx-v1"


def current_prompt_hash(role: str) -> str:
    """角色提示词内容哈希（SPEC-10 §2.1）：system 模板 + 输出 schema 字段面 +
    家族词表渲染源——三者任一改版即新 profile（prompt 内容锚，非版本号锚）。"""
    payload = json.dumps(
        {
            "system": SYSTEM_BY_ROLE[role],
            "schema_fields": SCHEMA_FIELDS_BY_ROLE[role],
            "family_gloss": FAMILY_GLOSS,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _split_model_ref(model_ref: str) -> tuple[str, str]:
    """``"{tier}:{provider}/{model}"`` → ``(provider, model)``（形态非法 ValueError）。"""
    _tier, sep, rest = model_ref.partition(":")
    if not sep or "/" not in rest:
        raise ValueError(f"model_ref 形态非法：{model_ref!r}（期望 tier:provider/model）")
    provider, _sep, model = rest.partition("/")
    return provider, model


def profile_id(
    model_ref: str,
    role: str,
    *,
    prompt_hash: str | None = None,
    production_temp: float = 0.2,
) -> str:
    """execution_profile_id（SPEC-10 §2.1 公式逐字；确定性纯函数）。"""
    provider, model = _split_model_ref(model_ref)
    ph = current_prompt_hash(role) if prompt_hash is None else prompt_hash
    canonical = json.dumps(
        {
            "provider": provider,
            "model": model,
            "role": role,
            "prompt": ph,
            "ctx_strategy": CTX_STRATEGY_VERSION,
            "contract_version": CONTRACT_VERSION,
            "production_temp": production_temp,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "ep-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class QualificationGate:
    """资格门（SPEC-10 §5）：enforce_roles 内角色按 effective passed 行过滤。"""

    policy_version: str
    enforce_roles: tuple[str, ...]
    production_temp: float

    def applies(self, role: str) -> bool:
        return role in self.enforce_roles

    def _profile(self, model_ref: str, role: str) -> str:
        return profile_id(model_ref, role, production_temp=self.production_temp)

    def effective_row(
        self, ledger: KernelLedger, agent_id: str, role: str, model_ref: str
    ) -> dict | None:
        """effective passed 行（SPEC-10 §3.1：最新未替代 passed）；查无 → None。"""
        row = self._latest(ledger, agent_id, role, model_ref, status="passed")
        if row is None:
            return None
        return {
            "qualification_id": str(row[0]),
            "status": str(row[1]),
            "reason": row[2],
            "accuracy": row[3],
            "exam_run_id": row[4],
            "policy_version": str(row[5]),
            "profile_id": str(row[6]),
        }

    def latest_row(
        self, ledger: KernelLedger, agent_id: str, role: str, model_ref: str
    ) -> dict | None:
        """当前资格状态行（任意状态；pending/failed/stale/revoked 的如实读面，
        播种报告 role_qualification 分列用——报告呈现真状态而非 passed-only）。"""
        row = self._latest(ledger, agent_id, role, model_ref, status=None)
        if row is None:
            return None
        return {
            "qualification_id": str(row[0]),
            "status": str(row[1]),
            "reason": row[2],
            "accuracy": row[3],
            "exam_run_id": row[4],
            "policy_version": str(row[5]),
            "profile_id": str(row[6]),
        }

    def _latest(
        self, ledger: KernelLedger, agent_id: str, role: str, model_ref: str,
        *, status: str | None,
    ):
        if not self.applies(role):
            return None
        sql = (
            "SELECT id, status, reason, accuracy, exam_run_id, policy_version,"
            " profile_id FROM role_qualifications"
            " WHERE agent_id = ? AND role = ? AND profile_id = ?"
            " AND policy_version = ? AND superseded_by IS NULL"
        )
        args: list = [agent_id, role, self._profile(model_ref, role),
                      self.policy_version]
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        row = ledger.conn.execute(sql + " ORDER BY started_at DESC, id DESC LIMIT 1",
                                  args).fetchone()
        return row

    def filter_pairs(
        self, ledger: KernelLedger, role: str, pairs: list[tuple[str, str]]
    ) -> tuple[list[tuple[str, str]], int]:
        """蜂池过滤（§5.2）：``[(model_ref, agent_id)]`` → (合格, 过滤数)。

        gate 不适用该角色（applies=False）→ 原样返回（零过滤）。"""
        if not self.applies(role):
            return pairs, 0
        kept: list[tuple[str, str]] = []
        for model_ref, agent_id in pairs:
            if self.effective_row(ledger, agent_id, role, model_ref) is not None:
                kept.append((model_ref, agent_id))
        return kept, len(pairs) - len(kept)

    def recheck(self, ledger: KernelLedger, agent_id: str, role: str, model_ref: str) -> bool:
        """dispatch 前复验（§5.3）：排队后被撤资格/改 profile 的任务在此阻断。"""
        return self.effective_row(ledger, agent_id, role, model_ref) is not None


def gate_from_params(params: ParamsConfig | None) -> QualificationGate | None:
    """params → gate；enforce=False 返回 None（调用方零行为差，SPEC-10 §5.1）。"""
    qual = getattr(params, "qualification", None)
    if qual is None or not qual.enforce:
        return None
    return QualificationGate(
        policy_version=str(qual.policy_version),
        enforce_roles=tuple(qual.enforce_roles),
        production_temp=float(qual.production_temp),
    )
