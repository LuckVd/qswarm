"""supply.config 测试（SPEC-6 §5.1；SPEC-5 M2 卷 §1.2 最低用例集）。

钉死：三节装载 + extra=forbid、min_pass 值域 / 档位键越词表 / 缺节 / provider 越
base URL 词表 → ValueError、装载不触 env。
"""

from __future__ import annotations

import pytest
import yaml

from qswarm.supply import TIERS, load_models
from qswarm.supply.config import PROVIDER_BASE_URLS, ROLE_TIER

from m2_harness import MODELS_YAML


def write_yaml(tmp_path, text: str):
    p = tmp_path / "models.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_models_config_loads_and_forbids_extra(tmp_path):
    """参考形态三节装载；未知键 extra=forbid 拒绝；档位词表与角色映射在位。"""
    p = write_yaml(tmp_path, MODELS_YAML)
    cfg = load_models(p)
    assert set(cfg.tiers) == {"cheap", "strong", "mid", "engineering"}
    assert cfg.entry_exams["cheap"].min_pass == 0.8
    assert cfg.entry_exams["cheap"].families == "all"
    assert cfg.runtime.production_temp == 0.2
    assert cfg.tiers["cheap"].primary.model == "deepseek-chat"
    assert [f.model for f in cfg.tiers["cheap"].fallbacks] == ["gpt-4o-mini"]
    # 未知键拒绝（根节 / 节内 / 条目内三层）
    for text in (
        MODELS_YAML + "\nunknown_root: 1\n",
        MODELS_YAML.replace("runtime: {rpm_limit: 600, production_temp: 0.2}",
                            "runtime: {rpm_limit: 600, production_temp: 0.2, unknown_k: 1}"),
        MODELS_YAML.replace("model: deepseek-chat,", "model: deepseek-chat, level: 1,"),
    ):
        p_bad = write_yaml(tmp_path, text)
        with pytest.raises(ValueError):
            load_models(p_bad)
    # 词表面在位
    assert TIERS == ("cheap", "strong", "mid", "engineering")
    assert ROLE_TIER == {"scout": "cheap", "auditor": "strong", "reviewer": "mid", "verifier": "engineering"}
    assert set(PROVIDER_BASE_URLS) >= {"deepseek", "openai", "anthropic"}


def test_models_config_domain_validation(tmp_path):
    """min_pass ∉ [0,1] / 档位键越词表 / 缺节 / entry_exams 非 tiers 子集 /
    provider 越 base URL 词表 / 文件缺失 → ValueError（装载不触 env）。"""
    bad_cases = [
        # min_pass 越界
        MODELS_YAML.replace("cheap: {min_pass: 0.8,", "cheap: {min_pass: 1.5,"),
        MODELS_YAML.replace("cheap: {min_pass: 0.8,", "cheap: {min_pass: -0.1,"),
        # tiers 键越词表
        MODELS_YAML.replace("  strong:\n", "  strong:\n  hyper:\n    primary: {provider: openai, model: x, api_key_env: K}\n"),
        # entry_exams 键越词表 + 非 tiers 子集
        MODELS_YAML.replace("  strong: {min_pass: 0.8, families: all}",
                            "  strong: {min_pass: 0.8, families: all}\n  hyper: {min_pass: 0.8, families: all}"),
        # 缺节
        MODELS_YAML.replace("runtime: {rpm_limit: 600, production_temp: 0.2}", ""),
        MODELS_YAML.replace("entry_exams:\n  cheap: {min_pass: 0.8, families: all}\n  strong: {min_pass: 0.8, families: all}\n  mid: {min_pass: 0.8, families: all}\n  engineering: {min_pass: 0.8, families: all}", ""),
        # provider 无 base URL 映射
        MODELS_YAML.replace("provider: deepseek, model: deepseek-chat", "provider: mistral, model: mistral-large"),
    ]
    for i, text in enumerate(bad_cases):
        p = write_yaml(tmp_path, text)
        with pytest.raises(ValueError):
            load_models(p)
    # 文件缺失 → ValueError（包装 FileNotFoundError）
    with pytest.raises(ValueError, match="缺失"):
        load_models(tmp_path / "nope.yaml")
    # 装载不触 env：构造后 env 未读取（complete() 才读，见 adapter 测试）
    p = write_yaml(tmp_path, MODELS_YAML)
    cfg = load_models(p)
    assert cfg.tiers["cheap"].primary.api_key_env == "M2_TEST_KEY_CHEAP"


def test_models_config_exam_thinking_keys(tmp_path):
    """补全面三键（2026-10-12 DECISIONS）：缺省 = 旧行为（4096/120s/不钉思考）；
    thinking 档位键或取值越词表 → ValueError。"""
    cfg = load_models(write_yaml(tmp_path, MODELS_YAML))
    assert cfg.runtime.exam_max_output_tokens == 4096
    assert cfg.runtime.exam_timeout_s == 120.0
    assert cfg.runtime.thinking == {}
    tuned = MODELS_YAML.replace(
        "runtime: {rpm_limit: 600, production_temp: 0.2}",
        "runtime: {rpm_limit: 600, production_temp: 0.2, exam_max_output_tokens: 32768,"
        " exam_timeout_s: 600, thinking: {cheap: enabled, strong: disabled}}",
    )
    cfg2 = load_models(write_yaml(tmp_path, tuned))
    assert cfg2.runtime.exam_max_output_tokens == 32768
    assert cfg2.runtime.exam_timeout_s == 600.0
    assert cfg2.runtime.thinking == {"cheap": "enabled", "strong": "disabled"}
    for bad in (
        "thinking: {hyper: enabled}",   # 档位越词表
        "thinking: {cheap: on}",        # 取值越词表
    ):
        text = MODELS_YAML.replace(
            "runtime: {rpm_limit: 600, production_temp: 0.2}",
            f"runtime: {{rpm_limit: 600, production_temp: 0.2, {bad}}}",
        )
        with pytest.raises(ValueError):
            load_models(write_yaml(tmp_path, text))
