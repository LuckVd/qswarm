"""perception.prompts：通道 B 直扫提示词模板与装配（SPEC-7 §1.3；纯函数零 IO）。

同 prompts 包纪律：模板文本模块常量承载（无文件 IO）、string.Template 占位符、
渲染缺实参 → ValueError、渲染即定形调用方不得再拼改、注入防线段复用
``prompts.templates``（UNTRUSTED_BLOCK / INJECTION_GUARD 单一源，E7 不复制）。

输出契约（§1.3 逐字）：候选列表 JSON ``{"candidates": [{path, start_line,
end_line, kind, family_hint, reason≤200字}]}``；温度沿用 ``EXAM_TEMPERATURE``(0)。
解析在 channels 层（需计数与重试语义），本模块只装配不解析。
"""

from __future__ import annotations

from dataclasses import dataclass
from string import Template

from qswarm.prompts.render import Message
from qswarm.prompts.templates import INJECTION_GUARD, UNTRUSTED_BLOCK

__all__ = ["L0_SCAN_SYSTEM", "L0Block", "render_l0_scan"]


#: 通道 B system 模板（§1.3 语义：结构候选收集，无对错语义、宁多勿漏）。
L0_SCAN_SYSTEM: str = """你是代码安全检测蜂群中的侦察蜂，正在执行 L0 直扫（结构候选收集）。
你的职责是对注入的代码分片做快速结构扫描，列出所有可能值得人工或后续分析关注的可疑位置（候选点）。你处在检测漏斗的最前端：候选收集没有对错语义、不记入你的考评——宁多勿漏，明显无关的样板代码不必列。

【任务边界】只做结构级扫描与候选收集；不做深挖、不下漏洞结论、不产 finding。

【工具与信息源边界】你只能依据注入的数据块内容做判断，边界外的一切操作都禁止：repo:ro

【输出格式】你的最终回答必须是且仅是一个 JSON 对象，不包含任何解释性文字：
字段清单（输出必须包含以下全部字段，不得新增字段）：
- candidates <array> 必填：候选点列表，可为空数组（分片干净时）；每个元素：
  path <string>（数据块标注的文件路径，逐字抄写）、
  start_line <integer>（1-based 起始行）、end_line <integer>（1-based 结束行，≥start_line）、
  kind <string>（取值域逐字：source | sink | dangerous_call | serialization | model_flag）、
  family_hint <string|null>（家族提示，取值域：injection | deser | crypto | authz | config_dep | mem_safety，猜不出填 null）、
  reason <string>（一句话依据，≤200 字）。

最小示例：
{"candidates": [{"path": "src/api.py", "start_line": 42, "end_line": 45, "kind": "sink", "family_hint": "injection", "reason": "请求参数直接拼入 shell 调用"}]}"""


@dataclass(frozen=True)
class L0Block:
    """一段待扫分片（ref = repo 相对路径 + 行窗标注；content = 源码原文）。"""

    ref: str
    content: str


def render_l0_scan(blocks: list[L0Block]) -> list[Message]:
    """通道 B 装配：返回 [system, user]（+ 注入嫌疑通告）Message 列表。

    - system = L0_SCAN_SYSTEM + INJECTION_GUARD（防线段单一源复用）；
    - user = 分片头行 + 每块按 UNTRUSTED_BLOCK 渲染（块序 = 传入序）；
    - 任一块内容命中伪造分隔符模式 → 附加第三条 system 通告（与 prompts.render
      同口径；检测逻辑本地实现以维持 perception 不反向依赖 prompts.render 的
      渲染内部——模式两式与 prompts.templates 语义逐字一致）。"""
    import re

    suspect_re = re.compile(r"^<<<UNTRUSTED_DATA(.*)?$|^<<<UNTRUSTED_DATA end>>>$", re.MULTILINE)
    system = L0_SCAN_SYSTEM + "\n\n" + INJECTION_GUARD
    header = f"shards: {len(blocks)}"
    rendered = "\n\n".join(
        Template(UNTRUSTED_BLOCK).substitute(ref=b.ref, content=b.content)
        for b in blocks
    )
    user = header + "\n\n" + rendered if blocks else header
    messages = [Message(role="system", content=system), Message(role="user", content=user)]
    if any(suspect_re.search(b.content) for b in blocks):
        from qswarm.prompts.templates import INJECTION_SUSPECT_NOTICE

        messages.append(Message(role="system", content=INJECTION_SUSPECT_NOTICE))
    return messages
