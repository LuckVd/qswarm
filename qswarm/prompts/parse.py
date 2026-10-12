"""prompts.parse：输出解析（SPEC-3 §4；schema gate 的文本→结构前置段）。

两段式提取纪律（E3，确定性）：全文 strip 直接 json 解析成功 → 原文；否则取**最后
一个** ```json 代码围栏内的内容（不贪婪匹配更早的围栏）；仍失败 →
SchemaRejected("no_json")。只提取不修补：不去注释、不去尾逗号、不截断——修补是
语义改写，宁可退回（§4.3「schema 校验，不合格直接退回」的文本→结构前置段）。

工件引用形态守卫（§4.5 工件纪律）：模型全部 ref 类字段值必须匹配 ^artifact://，
否则 SchemaRejected("schema:<模型名>:ref_not_artifact")；"artifact://pending" 走
派单方回填通道（模板已声明），形态守卫放行。内核 ``submit()``（SPEC-2 §2.5）是
第二道语义 gate（证据链节点数/词表/改判守卫）——parse 只管格式，两层职责不重叠
不互代。解析零随机零 IO；``parse_output`` 对同一 ``raw`` 恒等。
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Union

from pydantic import ValidationError

from qswarm.contracts import (
    ArbitrationDraft,
    AuditorFinding,
    ReviewerVote,
    ScoutReport,
    VerifierReport,
)

if TYPE_CHECKING:  # 仅注解用
    from qswarm.contracts import Role

__all__ = ["SchemaRejected", "extract_json", "parse_output"]

ParsedOutput = Union[ScoutReport, AuditorFinding, ReviewerVote, VerifierReport, ArbitrationDraft]


class SchemaRejected(ValueError):
    """② 输出格式不合格（§4.3）：零落库。

    ``reason`` 取值：``"no_json"``（提取失败）、
    ``"schema:<模型名>:<pydantic 首错>"``（校验失败）、
    ``"schema:<模型名>:ref_not_artifact"``（工件引用形态守卫失败）。
    调用方据 reason 走 re_dispatch(schema_fail)（内核 SPEC-2 §2.3 已有）。
    """

    def __init__(self, reason: str, detail: str | None = None) -> None:
        super().__init__(reason if detail is None else f"{reason}: {detail}")
        self.reason = reason


#: 角色词表 → 输出模型（SPEC-1 §4.2 唯一真源；未知 role → ValueError，词表错是
#: 编程错误非产出错误）。
_MODEL_BY_ROLE: dict[str, type] = {
    "scout": ScoutReport,
    "auditor": AuditorFinding,
    "reviewer": ReviewerVote,
    "verifier": VerifierReport,
    "queen_advisor": ArbitrationDraft,
}

_ARTIFACT_REF_RE = re.compile(r"^artifact://")
_JSON_FENCE_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)


def extract_json(raw: str) -> str:
    """从模型原文提取 JSON 文本（E3 两段式纪律，确定性）：

    1) 全文 strip 后直接 json 解析成功 → 返回全文（strip 后）；
    2) 否则取**最后一个** ```json 代码围栏内的内容（不贪婪匹配更早的围栏）；
    3) 仍失败 → SchemaRejected("no_json")。
    只提取不修补：不去注释、不去尾逗号、不截断——修补是语义改写，宁可退回。
    """
    stripped = raw.strip()
    if stripped:
        try:
            json.loads(stripped)
            return stripped
        except ValueError:
            pass
    fences = _JSON_FENCE_RE.findall(raw)
    if fences:
        candidate = fences[-1].strip()
        try:
            json.loads(candidate)
            return candidate
        except ValueError:
            pass
    raise SchemaRejected("no_json")


def _first_pydantic_error(exc: ValidationError) -> str:
    """ValidationError → 可断言的首错串 "<loc>: <msg>"（确定性；loc 逐段点连）。"""
    err = exc.errors()[0]
    loc = ".".join(str(part) for part in err.get("loc", ()))
    return f"{loc}: {err['msg']}"


def _guard_refs(model_name: str, refs: list[str]) -> None:
    """工件引用形态守卫（§4.5）：任一 ref 类字段值不匹配 ^artifact:// → 拒绝。"""
    for value in refs:
        if not _ARTIFACT_REF_RE.match(value):
            raise SchemaRejected(f"schema:{model_name}:ref_not_artifact", value)


def _ref_fields(model_name: str, parsed: ParsedOutput) -> list[str]:
    """按模型收集全部 ref 类字段值（SPEC-3 §4 逐字段清单）。"""
    refs: list[str] = []
    if isinstance(parsed, ScoutReport):
        refs.append(parsed.reason_ref)
    elif isinstance(parsed, AuditorFinding):
        refs.extend(node.ref for node in parsed.evidence_chain)
        refs.extend(parsed.artifacts)
    elif isinstance(parsed, ReviewerVote):
        refs.append(parsed.comment_ref)
    elif isinstance(parsed, VerifierReport):
        refs.append(parsed.deps_ref)
        refs.append(parsed.logs_ref)
        if parsed.blocking is not None:
            refs.append(parsed.blocking.ref)
    elif isinstance(parsed, ArbitrationDraft):
        refs.append(parsed.summary_ref)
    return refs


def parse_output(role: "Role | str", raw: str) -> ParsedOutput:
    """schema gate 前置段：extract_json → 按角色选 pydantic 模型校验 → 工件引用
    形态守卫。校验失败 → SchemaRejected("schema:<模型名>:<pydantic 首错>")。

    - 工件引用守卫（§4.5）：模型全部 ref 类字段（reason_ref/comment_ref/
      evidence_chain[].ref/artifacts[]/logs_ref/deps_ref/blocking.ref/summary_ref）
      值必须匹配 ^artifact://，否则 SchemaRejected(...ref_not_artifact)；
      "artifact://pending" 走派单方回填通道，形态守卫放行；
    - 角色无对应模型（未知 role）→ ValueError（词表错，编程错误非产出错误）。
    """
    role_key = str(getattr(role, "value", role))
    model = _MODEL_BY_ROLE.get(role_key)
    if model is None:
        raise ValueError(
            f"未知输出角色：{role_key!r}（合法值：{sorted(_MODEL_BY_ROLE)}）"
        )
    text = extract_json(raw)
    data = json.loads(text)
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        raise SchemaRejected(
            f"schema:{model.__name__}:{_first_pydantic_error(exc)}"
        ) from exc
    _guard_refs(model.__name__, _ref_fields(model.__name__, parsed))
    return parsed
