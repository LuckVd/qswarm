"""枚举逐字对照测试：每个 StrEnum 的成员名与字符串值逐一断言与 SPEC-1 §1 一致。

直接从 docs/spec/SPEC-1_data_contracts.md §1 的 python 源码块 ast 解析出
「规格侧真值表」，与 qswarm/contracts/enums.py 的运行时枚举逐成员比对——
SPEC 改了枚举而代码没跟（或反之），本测试立即红。
"""

import ast
import re
from enum import StrEnum
from pathlib import Path

import qswarm.contracts.enums as enums_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "docs" / "spec" / "SPEC-1_data_contracts.md"


def _spec_section1_source() -> str:
    text = SPEC_PATH.read_text(encoding="utf-8")
    section = text.split("## 1. 枚举", 1)[1].split("## 2.", 1)[0]
    match = re.search(r"```python\n(.*?)```", section, re.DOTALL)
    assert match, "SPEC-1 §1 中未找到 python 源码块"
    return match.group(1)


def _spec_enum_table() -> dict[str, dict[str, str]]:
    """ast 解析 §1 代码块 → {枚举类名: {成员名: 字符串值}}（保持声明顺序）。"""
    tree = ast.parse(_spec_section1_source())
    table: dict[str, dict[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        members: dict[str, str] = {}
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                members[stmt.targets[0].id] = stmt.value.value
        assert members, f"SPEC-1 §1 的 {node.name} 未解析出任何成员"
        table[node.name] = members
    return table


SPEC_ENUMS: dict[str, dict[str, str]] = _spec_enum_table()


def _runtime_enums() -> dict[str, type[StrEnum]]:
    out = {}
    for name in dir(enums_module):
        obj = getattr(enums_module, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, StrEnum)
            and obj.__module__ == enums_module.__name__
        ):
            out[name] = obj
    return out


def test_spec_parse_sanity_check():
    """护栏：解析器本身没坏——SPEC-1 §1 必须解析出全部 19 个枚举类（显式点名）。"""
    assert set(SPEC_ENUMS) == {
        "RunMode", "RunStatus", "Role", "PoolClass", "AgentStatus",
        "Family", "Severity", "Grade", "FindingState", "SignalTier",
        "CellStatus", "LedgerKind", "DepositOrigin", "VerifyOutcome",
        "VerifyErrorKind", "BlockingKind", "ExamPurpose", "ExamItemSource",
        "L0Channel",
    }


def test_every_spec_enum_class_exists_and_is_strenum():
    runtime = _runtime_enums()
    for name in SPEC_ENUMS:
        cls = runtime.get(name)
        assert cls is not None, f"enums.py 缺少 SPEC-1 §1 定义的枚举 {name}"
        assert issubclass(cls, StrEnum), f"{name} 必须是 StrEnum"


def test_no_extra_enum_classes_beyond_spec():
    runtime = set(_runtime_enums())
    extra = runtime - set(SPEC_ENUMS)
    assert not extra, f"enums.py 多出 SPEC-1 §1 之外的枚举：{sorted(extra)}"


def test_each_member_name_and_value_verbatim():
    """逐枚举、逐成员断言：成员名集合与顺序、成员字符串值，均与 §1 逐字一致。"""
    for enum_name, spec_members in SPEC_ENUMS.items():
        cls = _runtime_enums()[enum_name]
        runtime_members = cls.__members__  # 含顺序
        assert list(runtime_members) == list(spec_members), (
            f"{enum_name} 成员名或顺序与 SPEC-1 §1 不一致："
            f"spec={list(spec_members)} runtime={list(runtime_members)}"
        )
        for member_name, member_value in spec_members.items():
            runtime_value = cls[member_name].value
            assert runtime_value == member_value, (
                f"{enum_name}.{member_name} 值不一致："
                f"spec={member_value!r} runtime={runtime_value!r}"
            )


def test_member_counts_match_spec():
    for enum_name, spec_members in SPEC_ENUMS.items():
        cls = _runtime_enums()[enum_name]
        assert len(cls) == len(spec_members), (
            f"{enum_name} 成员数不一致：spec={len(spec_members)} runtime={len(cls)}"
        )
