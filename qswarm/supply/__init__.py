"""supply：供给适配层（SPEC-6 §5；M2 新增包）。

models.yaml 契约（ModelsConfig）+ 候选模型补全协议（CandidateAdapter）与 stdlib
实现（UrllibAdapter，全仓唯一网络落点）。机制层（kernel/contracts/simulator）
零反向依赖本包（SPEC-5 M2 卷 §2.4）；本包不触任何账本/表格写口。
"""

from .adapter import AdapterError, CandidateAdapter, MAX_OUTPUT_TOKENS, UrllibAdapter
from .config import (
    PROVIDER_BASE_URLS,
    ROLE_TIER,
    TIERS,
    EntryExamGate,
    ModelEntry,
    ModelsConfig,
    RuntimeConfig,
    TierConfig,
    is_anthropic_wire,
    load_models,
)

__all__ = [
    # 配置（§5.1）
    "EntryExamGate",
    "ModelEntry",
    "ModelsConfig",
    "PROVIDER_BASE_URLS",
    "ROLE_TIER",
    "RuntimeConfig",
    "TIERS",
    "TierConfig",
    "is_anthropic_wire",
    "load_models",
    # 适配层（§5.2）
    "AdapterError",
    "CandidateAdapter",
    "MAX_OUTPUT_TOKENS",
    "UrllibAdapter",
]
