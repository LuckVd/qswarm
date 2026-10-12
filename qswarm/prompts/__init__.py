"""prompts：蜂角色提示词模板与输出契约（SPEC-3；M2 新增包）。

**纯函数零 IO 零网络**：不读文件、不调模型、不发请求——调用方装配一切输入、
消费一切输出。输出契约不在本包重定义（pydantic 面以 SPEC-1 §4.2
``contracts/models.py`` 为唯一真源）；本包只承载：①契约版本锚点；②提示词模板
与装配规则；③输出解析纪律；④考试提示词变体（SPEC-6 消费）。
"""

from .parse import SchemaRejected, extract_json, parse_output
from .render import ContextBlock, Message, render, render_exam
from .templates import (
    AUDITOR_SCHEMA_FIELDS,
    AUDITOR_SYSTEM,
    EXAM_APPENDIX,
    INJECTION_GUARD,
    INJECTION_SUSPECT_NOTICE,
    QUEEN_ADVISOR_SCHEMA_FIELDS,
    QUEEN_ADVISOR_SYSTEM,
    REVIEWER_SCHEMA_FIELDS,
    REVIEWER_SYSTEM,
    SCHEMA_FIELDS_BY_ROLE,
    SCOUT_SCHEMA_FIELDS,
    SCOUT_SYSTEM,
    SYSTEM_BY_ROLE,
    UNTRUSTED_BLOCK,
    VERIFIER_SCHEMA_FIELDS,
    VERIFIER_SYSTEM,
)
from .version import CONTRACT_VERSION, EXAM_TEMPERATURE

__all__ = [
    # 版本锚点（§1）
    "CONTRACT_VERSION",
    "EXAM_TEMPERATURE",
    # 载体与装配（§3/§5）
    "ContextBlock",
    "EXAM_APPENDIX",
    "INJECTION_GUARD",
    "INJECTION_SUSPECT_NOTICE",
    "Message",
    "SCHEMA_FIELDS_BY_ROLE",
    "SYSTEM_BY_ROLE",
    "UNTRUSTED_BLOCK",
    "render",
    "render_exam",
    # 解析（§4）
    "SchemaRejected",
    "extract_json",
    "parse_output",
    # 模板常量（黄金 hash 钉死面）
    "AUDITOR_SCHEMA_FIELDS",
    "AUDITOR_SYSTEM",
    "QUEEN_ADVISOR_SCHEMA_FIELDS",
    "QUEEN_ADVISOR_SYSTEM",
    "REVIEWER_SCHEMA_FIELDS",
    "REVIEWER_SYSTEM",
    "SCOUT_SCHEMA_FIELDS",
    "SCOUT_SYSTEM",
    "VERIFIER_SCHEMA_FIELDS",
    "VERIFIER_SYSTEM",
]
