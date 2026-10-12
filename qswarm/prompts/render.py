"""prompts.render：提示装配纯函数（SPEC-3 §3；零 IO 零随机零时钟）。

调用方装配一切输入（数据块内容已从工件库/仓库读取）、消费一切输出——本模块
不读文件、不调模型、不发请求。``render`` 返回即定形（E5：调用方不得再拼改；
blocks 顺序 = 传入顺序，不排序——上下文序是装配方精度决策，内核 route/promote
的字典序纪律不外溢）。

注入嫌疑扫描（§11.6-3 通道）：任一 block.content 的行命中伪造数据块分隔符模式 →
渲染照常（原文保留，E6：分隔符伪造不转义改写），但 Message 列表附加第三条
system 消息 INJECTION_SUSPECT_NOTICE；无命中不附加。判定留给模型、记录留给
injection_flag 通道——与 §11.6-2 权限兜底（蜂零写权）组合，注入最坏结果 =
垃圾产出，过不了内核 schema/评审/复现三道闸。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from string import Template
from typing import TYPE_CHECKING, Sequence

from ..kernel.dispatch import ROLE_TASK_BOUNDS, ROLE_TOOL_BOUNDS
from .templates import (
    EXAM_APPENDIX,
    INJECTION_GUARD,
    INJECTION_SUSPECT_NOTICE,
    SCHEMA_FIELDS_BY_ROLE,
    SYSTEM_BY_ROLE,
    UNTRUSTED_BLOCK,
)

if TYPE_CHECKING:  # 仅注解用
    from qswarm.contracts import DispatchContract, Role

__all__ = ["ContextBlock", "Message", "render", "render_exam"]


@dataclass(frozen=True)
class ContextBlock:
    """一段待注入数据（调用方已从工件库/仓库读取，render 不做任何 IO）。"""

    ref: str            # 来源引用：artifact:// 或 repo 路径（渲染进分隔符头）
    content: str        # 原文内容


@dataclass(frozen=True)
class Message:
    """一条装配产物消息（role: "system" | "user"）。"""

    role: str
    content: str


#: 注入嫌疑行模式（SPEC-3 §3 逐字两式；MULTILINE 逐行判定）。
_FORGED_DELIMITER_RE = re.compile(
    r"^<<<UNTRUSTED_DATA(.*)?$|^<<<UNTRUSTED_DATA end>>>$",
    re.MULTILINE,
)


def _substitute_strict(template_text: str, mapping: dict[str, str]) -> str:
    """Template.substitute 的 ValueError 包装（SPEC-3 §2.1：缺占位符实参 →
    ValueError，非 safe_substitute 的静默缺省；Template 原生 KeyError 包装上抛）。"""
    try:
        return Template(template_text).substitute(mapping)
    except KeyError as exc:
        missing = str(exc.args[0]) if exc.args else "?"
        raise ValueError(f"模板缺占位符实参：${missing}") from exc


def render(
    role: "Role | str",
    *,
    contract: "DispatchContract",
    blocks: Sequence[ContextBlock],
) -> list[Message]:
    """生产装配：返回 [system, user] 两条消息（E5：渲染即定形，调用方不得再拼改）。

    - system = SYSTEM_BY_ROLE[role].substitute(task_bounds=…, tool_bounds=…,
      schema_fields=…) + "\\n\\n" + INJECTION_GUARD；
      task_bounds/tool_bounds 逐字取 kernel.dispatch 同名常量（缺角色键 → KeyError；
      M2 时点四派单角色全覆盖——queen_advisor 仅登记输出 schema 名，其 ③④ 段
      常量随 M3 顾问接线落位，SPEC-3 §6/E7）；
    - user = 任务头 + 数据块序：任务头行（goal / taxonomy_node / hop_budget /
      deadline_s，每字段一行 "字段名: 值"）+ 每块按 UNTRUSTED_BLOCK 渲染
      （blocks 顺序 = 传入顺序，不排序）；
    - 注入嫌疑扫描（§11.6-3 通道）：任一 block.content 的行命中伪造分隔符模式 →
      渲染照常（原文保留），Message 列表附第三条 system 消息
      INJECTION_SUSPECT_NOTICE；无命中不附加。零随机源、零时钟（deadline 由
      contract 承载）。
    """
    role_key = str(getattr(role, "value", role))
    template_text = SYSTEM_BY_ROLE[role_key]          # 未知角色 → KeyError
    system = _substitute_strict(
        template_text,
        {
            "task_bounds": ROLE_TASK_BOUNDS[role_key],
            "tool_bounds": "、".join(ROLE_TOOL_BOUNDS[role_key]),
            "schema_fields": SCHEMA_FIELDS_BY_ROLE[role_key],
        },
    ) + "\n\n" + INJECTION_GUARD

    header = "\n".join(
        (
            f"goal: {contract.goal}",
            f"taxonomy_node: {contract.taxonomy_node}",
            f"hop_budget: {contract.hop_budget}",
            f"deadline_s: {contract.deadline_s}",
        )
    )
    rendered_blocks = "\n\n".join(
        _substitute_strict(UNTRUSTED_BLOCK, {"ref": b.ref, "content": b.content})
        for b in blocks
    )
    user = header + "\n\n" + rendered_blocks if blocks else header

    suspect = any(_FORGED_DELIMITER_RE.search(b.content) for b in blocks)
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    if suspect:
        messages.append(Message(role="system", content=INJECTION_SUSPECT_NOTICE))
    return messages


def render_exam(
    role: "Role | str",
    *,
    contract: "DispatchContract",
    blocks: Sequence[ContextBlock],
) -> list[Message]:
    """考试装配（SPEC-3 §5；SPEC-6 §3.2 消费）：render 结果上，system 尾部追加
    "\\n\\n" + EXAM_APPENDIX；其余（任务头/数据块/注入嫌疑扫描）与生产完全同源。

    题号标注由 SPEC-6 runner 放进每题数据块首行（prompts 不感知题库 schema）。"""
    messages = render(role, contract=contract, blocks=blocks)
    system, rest = messages[0], messages[1:]
    return [
        Message(role=system.role, content=system.content + "\n\n" + EXAM_APPENDIX),
        *rest,
    ]
