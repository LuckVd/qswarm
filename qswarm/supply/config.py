"""supply.config：models.yaml 契约（SPEC-6 §5.1；SPEC-1 §5.1 形态定稿）。

pydantic 严格校验（extra=forbid 纪律同 params）：缺节、档位键越词表、
min_pass 越界、entry_exams 键不是 tiers 子集、entry_exams 键越 tiers 词表 →
ValueError（pydantic ValidationError ⊂ ValueError）。``api_key_env`` 对应环境
变量在**调用时**才读取（装载不触 env，值永不入账本/工件/日志承 §11.6-5）。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "PROVIDER_BASE_URLS",
    "EntryExamGate",
    "ModelEntry",
    "ModelsConfig",
    "RuntimeConfig",
    "TierConfig",
    "is_anthropic_wire",
    "load_models",
]

#: 档位词表（§5.1 逐字；models.yaml tiers/entry_exams 两节的合法键集）。
TIERS: tuple[str, ...] = ("cheap", "strong", "mid", "engineering")

#: 供应商 → 服务 base URL（v0 三家定稿；§5.2「provider ∈ {deepseek, openai, …}」
#: 的操作性口径：provider=供应商名，线格式族由 provider 推断——anthropic →
#: anthropic 线格式 POST {base}/v1/messages，其余 → openai_compatible 线格式
#: POST {base}/chat/completions。新供应商 = 本表加一行，不触 adapter 线格式代码）。
PROVIDER_BASE_URLS: dict[str, str] = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    # 智谱 BigModel（2026-10-12 负责人指定接入，DECISIONS 同日行）：OpenAI
    # chat/completions 兼容族；base 取 GLM Coding Plan 端点（负责人凭证为
    # 订阅 key，标准 paas/v4 端点报 1113 余额不足，coding 端点可用）。
    "zhipu": "https://open.bigmodel.cn/api/coding/paas/v4",
}

#: 档位 → 角色（SPEC-6 §4.1 五型分工逐字；expand_candidates 消费）。
ROLE_TIER: dict[str, str] = {
    "scout": "cheap",
    "auditor": "strong",
    "reviewer": "mid",
    "verifier": "engineering",
}


def is_anthropic_wire(provider: str) -> bool:
    """供应商 → 线格式族（SPEC-6 §5.2：anthropic 走 /v1/messages，其余走
    chat/completions 族）。"""
    return provider == "anthropic"


class ModelEntry(BaseModel):
    """models.yaml 档位条目（供应商模型名 + 密钥环境变量名）。

    ``provider`` 为供应商名（§5.2 口径；线格式族按 :func:`is_anthropic_wire`
    推断）——§5.1 注释「provider = 线格式族」与 §5.2「provider ∈ {deepseek,
    openai, …}」矛盾，取 §5.2 操作性口径（DECISIONS M2 实现期行）：base URL 必须有出处，
    且 model_ref 形如 ``cheap:deepseek/deepseek-chat``（SPEC-3 §1 示例）中
    deepseek 亦为供应商名。"""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    api_key_env: str


class TierConfig(BaseModel):
    """档位配置：primary + fallbacks（生产互备 §9.3；考试只走 primary，S6）。"""

    model_config = ConfigDict(extra="forbid")

    primary: ModelEntry
    fallbacks: list[ModelEntry] = []


class EntryExamGate(BaseModel):
    """入编线（§4.5 供给质检；v0 恒 families="all" 逐字 SPEC-1 §5.1）。"""

    model_config = ConfigDict(extra="forbid")

    min_pass: float = Field(ge=0, le=1)
    families: str


class RuntimeConfig(BaseModel):
    """运行时限参数（RPM/TPM 限速、批处理开关、生产温度上限）。

    补全面三键（2026-10-12 DECISIONS：思考型模型适配）缺省 = M2 定稿旧行为
    （4096 封顶 / 120s 超时 / 不发 thinking 参数）——参考形态与既有测试零扰动；
    ``thinking`` 为 tier → enabled|disabled，未钉档位不注入该参数（服务端缺省
    接管），词表与取值由 :func:`load_models` 校验。"""

    model_config = ConfigDict(extra="forbid")

    rpm_limit: int = 600
    tpm_limit: int = 4_000_000
    batch_mode_for_full: bool = True
    production_temp: float = 0.2
    exam_max_output_tokens: int = Field(default=4096, ge=1)
    exam_timeout_s: float = Field(default=120.0, gt=0)
    thinking: dict[str, str] = {}


class ModelsConfig(BaseModel):
    """models.yaml 根模型（extra=forbid 纪律同 params；三节缺一不可）。"""

    model_config = ConfigDict(extra="forbid")

    tiers: dict[str, TierConfig]
    entry_exams: dict[str, EntryExamGate]
    runtime: RuntimeConfig


def load_models(path: str | Path) -> ModelsConfig:
    """读取并校验 models.yaml；文件缺失 / 档位键越词表 / min_pass ∉ [0,1] /
    缺节 / entry_exams 键非 tiers 子集 → ValueError。装载不触 env。"""
    p = Path(path)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"models.yaml 文件缺失：{p}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{p}: YAML 顶层必须是映射（dict），实际为 {type(data).__name__}")
    for section in ("tiers", "entry_exams", "runtime"):
        if section not in data:
            raise ValueError(f"{p}: 缺节 {section!r}（tiers/entry_exams/runtime 三节必填）")
    bad_tiers = sorted(set(data["tiers"]) - set(TIERS))
    if bad_tiers:
        raise ValueError(f"{p}: tiers 档位键越词表：{bad_tiers}（合法值：{list(TIERS)}）")
    bad_gates = sorted(set(data["entry_exams"]) - set(TIERS))
    if bad_gates:
        raise ValueError(
            f"{p}: entry_exams 档位键越词表：{bad_gates}（合法值：{list(TIERS)}）"
        )
    orphan_gates = sorted(set(data["entry_exams"]) - set(data["tiers"]))
    if orphan_gates:
        raise ValueError(f"{p}: entry_exams 键不是 tiers 子集：{orphan_gates}")
    thinking = (data.get("runtime") or {}).get("thinking") or {}
    bad_think_tiers = sorted(set(thinking) - set(TIERS))
    if bad_think_tiers:
        raise ValueError(
            f"{p}: runtime.thinking 档位键越词表：{bad_think_tiers}（合法值：{list(TIERS)}）"
        )
    bad_think_vals = sorted(k for k, v in thinking.items() if v not in ("enabled", "disabled"))
    if bad_think_vals:
        raise ValueError(
            f"{p}: runtime.thinking 取值越词表：{bad_think_vals}"
            "（合法值：enabled/disabled）"
        )
    for tier_name, tier in sorted(data["tiers"].items()):
        for slot, entry in [("primary", tier["primary"]), *enumerate(tier.get("fallbacks", []))]:
            provider = entry["provider"]
            if provider not in PROVIDER_BASE_URLS:
                raise ValueError(
                    f"{p}: tiers.{tier_name}.{'primary' if slot == 'primary' else f'fallbacks[{slot}]'}"
                    f" provider 越词表：{provider!r}（合法值：{sorted(PROVIDER_BASE_URLS)}）"
                )
    return ModelsConfig.model_validate(data)
