"""prompts.gloss：家族词面注解——考试 claim 与生产票面共用的单一源。

背景（2026-10-20 联调实证 + 同日本轮审计）：裸家族标签与快照具体漏洞形态在
LLM 评审视角可错位（authz 族 = CWE-23/36 路径穿越件，claim「存在 authz 漏洞」
被三蜂一致拒报；同题注入注解后 direction 由 -1 翻 +1、fp_susp 0.9→0.3）。
注解只展开家族 token 的语义面：token 本身不变（judge 的「产出家族对齐答案键」
与 D32 真实服务模型解析不受影响），未知家族回退裸标签（零行为变化）。

一致性纪律（本轮修复的核心约束）：**考试面与生产面必须同源同形**——考试 claim
（exams.runner._exam_claim）与生产 reviewer 票面 / L2 深诊块（kernel.consult
._finding_block/_candidate_block）都经 :func:`family_display` 渲染，eval 分数与
生产能力读同一口径；单侧注入（旧形态：只在考试面）属 eval/production 偏移。
"""

from __future__ import annotations

__all__ = ["FAMILY_GLOSS", "family_display"]


#: 家族 token → 词面注解（六家族；与 taxonomy.yaml 家族键同名对齐）。
FAMILY_GLOSS: dict[str, str] = {
    "injection": "注入类（SQL/命令/代码注入、XSS/SSRF/XXE 等）",
    "deser": "反序列化类（java_gadget/py_pickle）",
    "crypto": "密码学误用类（弱算法/硬编码密钥/弱随机）",
    "authz": "访问控制与资源暴露类（含路径穿越 CWE-22/36、越权、会话缺陷）",
    "config_dep": "配置与依赖类（密钥泄漏/过期依赖/错误配置）",
    "mem_safety": "内存安全类（溢出/UAF/双重释放）",
}


def family_display(family: str | None) -> str:
    """家族 token → 带注解展示形（``authz（访问控制与…）``）。

    家族 token 原样保留在首位（词表解析不受影响）；未知家族 / 空值回退裸标签
    原样（或空串）——零行为变化面。"""
    text = str(family) if family is not None else ""
    if not text:
        return ""
    gloss = FAMILY_GLOSS.get(text)
    return f"{text}（{gloss}）" if gloss else text
