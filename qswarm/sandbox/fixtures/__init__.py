"""sandbox.fixtures：九类验收集（方案 §6.3；P1 工作包 D 的受控合成夹具）。

**边界（§6.3 逐字）**：process 诊断只运行人工审核的受控合成夹具、零生产写——
本目录全部目标为合成件（非题库、非真实仓库），配 :mod:`sandbox.oracle` 的冻结
驱动与宿主侧观察器构成成对/负对照判据。模型生成 PoC 与不可信仓库从首次执行
起进受限 container（SPEC-9 红线），不使用本夹具面。

九类（方案 §6.3 清单逐字；期望面 = E0 确定性判据）：

== ==== ======================== ====================== =====================================
类  名称                       形态（目标 × 驱动）      期望
== ==== ======================== ====================== =====================================
①   漏洞版                     vuln × effective        成对（与②）validate_pair → supported
②   修复版                     fix × effective         violation=False（负对照门）
③   仅回显安全版               echo_safe × effective   violation=False（旧仪器击穿件）
④   不可达 sink                unreachable × effective 无违规 → inconclusive、绝不 fp
⑤   缺依赖                     missing_dep × effective 目标启动失败 → inconclusive
⑥   超时                       timeout × effective     墙钟收割 → inconclusive
⑦   无条件 PASS                vuln × unconditional    自报 PASS 不获信 → not supported
⑧   目标未调用                 vuln × no_target_call   无金丝雀 → not supported
⑨   真实漏洞但 PoC 无效        vuln × ineffective      **必须 inconclusive**、绝不 fp
== ==== ======================== ====================== =====================================
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["FIXTURES_ROOT", "FixtureSpec", "NINE_CLASSES", "VARIANTS"]

#: 夹具根目录（runner 的 repo 实参；目标路径 = <family>/<variant>/target.py）。
FIXTURES_ROOT = Path(__file__).resolve().parent

#: 目标变体词表（§6.3 ①–⑥ 目标面；⑦–⑨ 经驱动变体对 vuln 目标成立）。
VARIANTS = ("vuln", "fix", "echo_safe", "unreachable", "missing_dep", "timeout")

#: 两族词表（D 首批收窄面：Python 命令注入 + 路径穿越，不宣称全覆盖）。
FAMILIES = ("injection", "path_traversal")


@dataclass(frozen=True)
class FixtureSpec:
    """一类对照的完整描述（oracle.run_control 的入参形态）。"""

    klass: int                    # 九类编号 ①–⑨
    name: str                     # 期望表名（方案 §6.3 清单名）
    family: str
    variant: str                  # 目标变体
    driver_kind: str              # effective | unconditional_pass | no_target_call | ineffective
    claim: str                    # 期望 claim_validation：supported | inconclusive
    violation: bool               # 期望观察器 violation_observed
    notes: str = ""

    @property
    def key(self) -> str:
        return f"{self.klass}:{self.name}"


def _expand() -> tuple[FixtureSpec, ...]:
    rows: list[FixtureSpec] = []
    table = [
        (1, "vuln_effective_poc", "vuln", "effective", "supported", True,
         "成对判据：validate_pair(①, ②) → supported（单独运行不判）"),
        (2, "fix_negative_control", "fix", "effective", "inconclusive", False,
         "负对照门：同驱动对修复版必须不触发（violation=False 是 ① 的前提）"),
        (3, "echo_safe_only", "echo_safe", "effective", "inconclusive", False,
         "仅回显安全版：回显满足不了副作用金丝雀（旧 echo-marker 仪器缺陷击穿件）"),
        (4, "unreachable_sink", "unreachable", "effective", "inconclusive", False,
         "不可达 sink：无违规 → inconclusive；不足以 refuted（§6.1）"),
        (5, "missing_dep", "missing_dep", "effective", "inconclusive", False,
         "缺依赖：目标启动失败（target_exit_code=1）→ 不入任何可信面"),
        (6, "timeout_wallclock", "timeout", "effective", "inconclusive", False,
         "超时：runner 墙钟收割（timed_out=True）→ inconclusive"),
        (7, "unconditional_pass_poc", "vuln", "unconditional_pass", "inconclusive", False,
         "无条件 PASS：自报断言不参与判定 → not supported（结构性不信自报）"),
        (8, "target_not_called", "vuln", "no_target_call", "inconclusive", False,
         "目标未调用：无金丝雀 → not supported（判别力证明：金丝雀依赖目标执行）"),
        (9, "real_vuln_ineffective_poc", "vuln", "ineffective", "inconclusive", False,
         "真实漏洞但 PoC 无效：必须 inconclusive、绝不 verified_fp（§6.3 硬判据）"),
    ]
    for family in FAMILIES:
        for klass, name, variant, driver, claim, violation, notes in table:
            rows.append(FixtureSpec(
                klass=klass, name=name, family=family, variant=variant,
                driver_kind=driver, claim=claim, violation=violation, notes=notes,
            ))
    return tuple(rows)


#: 九类期望表（E0 确定性判据的消费面；① 的 supported 由 validate_pair(①,②) 产生）。
NINE_CLASSES: tuple[FixtureSpec, ...] = _expand()
