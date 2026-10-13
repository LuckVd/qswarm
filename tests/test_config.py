"""配置加载器测试：真实 configs 两份 YAML 过校验；缺字段/未知字段/越界/交叉约束必报错。"""

import copy

import pytest
from pydantic import ValidationError

from qswarm.contracts import (
    ParamsConfig,
    TaxonomyConfig,
    load_params,
    load_taxonomy,
)


# ── 真实文件 ────────────────────────────────────────────────────

def test_load_default_params():
    params = load_params()
    # 抽查关键值：扫描坐标 = M0 定稿推荐组（SPEC-4 §4-1 选择器，2026-10-09）；
    # §5.2 设计默认的语义回归由各测试文件的 spec_literal 钉住。
    assert params.promote.N == 6
    assert params.promote.N_S2S3 == 3
    assert params.promote.DELTA == pytest.approx(0.05)
    assert params.tilt.T_BASE == pytest.approx(0.60)
    assert params.shrink.LAMBDA == 5
    assert params.decay.T_HALF_DAYS == 30
    assert params.decay.T_HALF_DAYS_EXAM == 15
    assert params.exam.PROMO_ITEMS == 5
    assert params.exam.PROMO_REAL == 3
    assert params.explore.UNIFORM_FLOOR == pytest.approx(0.20)
    assert params.budget.FULL_CENTS == 600
    assert params.deposits.SCOUT_X == pytest.approx(0.2)
    assert params.deposits.VERIFIER_CONFIRM == pytest.approx(0.25)
    assert params.panel.SIZE == 3
    assert params.roster.scout == 6
    assert params.roster.auditor == 5
    assert params.roster.reviewer == 4
    assert params.roster.verifier == 3


# ── v2 定稿旋钮逐键钉值（技术债④；DECISIONS 2026-10-09 v2 定稿回填）─────────
# 只读钉值：configs/params.yaml 现存全部 53 个键在此逐键断言（yaml 零改动）。
# 改动任一定稿值 → 对应用例红；新增/删除定稿键 → 键集守卫红（须同步补钉）。

V2_FINAL_KNOBS: dict[str, dict[str, object]] = {
    "promote": {
        "N": 6, "N_S2S3": 3, "DELTA": 0.05, "M": 30, "M_window_days": 90,
        "M_PRORATE": True, "UNIQUE_PER_FAMILY": True, "WINDOW_ADVANTAGE": 2,
    },
    "tilt": {"T_BASE": 0.60, "GAMMA": 1.0, "T_MIN": 0.50, "T_MAX": 0.90},
    "shrink": {"LAMBDA": 5},
    "decay": {"T_HALF_DAYS": 30, "T_HALF_DAYS_EXAM": 15, "EPS_FLOOR": 0.5},
    "demote": {
        "WINDOWS": 2, "DORMANT_DAYS": 60, "REACTIVATE_N": 5,
        "FC_DAYS": 1, "FC_ITEMS": 60, "FC_MARGIN": 0.15,
        "FC_CONSEC": 2, "FC_BASE_EXAMS": 2,
    },
    "exam": {
        "PROMO_ITEMS": 5, "PROMO_REAL": 3, "PROMO_MIN_REJECT_FP": 1,
        "PERIODIC_ITEMS": 10, "PERIODIC_PASS": 0.75, "COOLDOWN_DAYS": 1,
        "ENTRY_DIRECT": 0.30, "ENTRY_TOP_FRAC": 0.2, "ENTRY_MARGIN": 1,
        "TYPE_ENTRY": 0.30, "ENTRY_GRACE_DAYS": 3, "ENTRY_CORROB_S2S3": 2.0,
        "ENTRY_WINDOW_DAYS": 12, "TYPE_ENTRY_COMBINE": 0.25,
    },
    "explore": {"UNIFORM_FLOOR": 0.20},
    "budget": {
        "PR_CENTS": 100, "FULL_CENTS": 600, "L2_QUOTA_CAP": 45,
        "RESCAN_WEEKLY": 0.07,
    },
    "deposits": {
        "SCOUT_X": 0.2, "REVIEWER_X": 0.5,
        "VERIFIER_CONFLICT": 0.5, "VERIFIER_CONFIRM": 0.25,
    },
    "panel": {"SIZE": 3, "MIN_FAMILIES": 2},
    "roster": {"scout": 6, "auditor": 5, "reviewer": 4, "verifier": 3},
}


def _pin_cases() -> list[tuple[str, object]]:
    return [
        (f"{section}.{name}", value)
        for section, pins in V2_FINAL_KNOBS.items()
        for name, value in pins.items()
    ]


@pytest.mark.parametrize("key,expected", _pin_cases())
def test_v2_final_knob_pinned(key, expected):
    """逐键钉住 params.yaml 既有 v2 定稿值：改任一定稿值即红。"""
    section, name = key.split(".")
    actual = getattr(getattr(load_params(), section), name)
    if isinstance(expected, float):
        assert actual == pytest.approx(expected), key
    else:
        assert actual == expected, key


def test_v2_final_knob_pins_cover_every_yaml_key():
    """钉值表与 yaml 键集逐节相等：params.yaml 每个既有键都必须被钉住。"""
    data = load_params().model_dump()
    assert set(ALL_PINNED_KNOBS) == set(data), (
        f"节集不一致：缺钉 {sorted(set(data) - set(ALL_PINNED_KNOBS))} / "
        f"多钉 {sorted(set(ALL_PINNED_KNOBS) - set(data))}"
    )
    for section, fields in data.items():
        pinned = set(ALL_PINNED_KNOBS[section])
        assert pinned == set(fields), (
            f"{section} 节键集不一致：缺钉 {sorted(set(fields) - pinned)} / "
            f"多钉 {sorted(pinned - set(fields))}"
        )


# ── M1 新旋钮逐键钉值（SPEC-2 §8.1；DECISIONS 2026-10-10「旋钮缺省纪律落地」
# 行回填）─────────────────────────────────────────────────────
# budget 节 3 个新键，缺省值=规格逐字（§2.2 成本锚点换算：$0.02/$0.05/$0.10）；
# 熔断为 §2.4 常设机制、无开关——「缺省关闭」的 M1 落点是无预算约束路径
# （kernel.BudgetPolicy 注入缺省 None = M0 仿真轨迹逐字兼容），三键只是成本
# 锚点估计值。v2 表保持 53 键不动；键集守卫按 ALL_PINNED_KNOBS 合并两表。

M1_FINAL_KNOBS: dict[str, dict[str, object]] = {
    "budget": {"FAST_CENTS": 2, "COST_PER_L2_CENTS": 5, "COST_PER_S2_CENTS": 10},
}

# ── M3 感知层新旋钮逐键钉值（SPEC-7 §6；实现子任务回填，DECISIONS M3 行）──────
# perception 节 7 键，缺省=规格逐字值；嵌套节面与 SPEC-7 §6 表逐字对应。

M3_FINAL_KNOBS: dict[str, dict[str, object]] = {
    "perception": {
        "model": {"enabled": True},
        "rules": {"enabled": True, "timeout_s": 120},
        "l0": {"max_file_lines": 800},
        "l1": {"fast_top_k": 3, "files_per_shard": 20},
        "scan": {"rescan_sample_frac": 0.05},
        "repomap": {"max_files": 12, "max_lines": 1500},
    },
}

ALL_PINNED_KNOBS: dict[str, dict[str, object]] = {
    section: {**pins, **M1_FINAL_KNOBS.get(section, {}), **M3_FINAL_KNOBS.get(section, {})}
    for section, pins in V2_FINAL_KNOBS.items()
}
# M3 新节（V2 无此节，推导式不含）：perception 整节并入钉值对账面。
for _section, _pins in M3_FINAL_KNOBS.items():
    ALL_PINNED_KNOBS.setdefault(_section, dict(_pins))

# ── M4 会诊新旋钮逐键钉值（SPEC-8 §6；D31–D33 拍板回填，2026-10-13）────────

M4_FINAL_KNOBS: dict[str, dict[str, object]] = {
    "consult": {
        "wall_clock_budget_s": 3600,
        "min_panel": 2,
        "s1_rho": 0.4,
        "l2_context_lines": 40,
    },
}
for _section, _pins in M4_FINAL_KNOBS.items():
    ALL_PINNED_KNOBS.setdefault(_section, dict(_pins))


def test_m3_perception_knobs_pinned():
    """M3 perception 节 7 键逐键钉值（SPEC-7 §6 回填；extra=forbid 由根模型管）。"""
    p = load_params().perception
    assert p.model.enabled is True                       # 通道 B 开（D26 侦察蜂兼任）
    assert p.rules.enabled is True                       # semgrep 必选件缺省开（D30）
    assert p.rules.timeout_s == 120
    assert p.l0.max_file_lines == 800                    # 通道 B 单片行上限
    assert p.l1.fast_top_k == 3                          # fast 档 Top-3（§2.2）
    assert p.l1.files_per_shard == 20                    # L1 分片（§5.3）
    assert p.scan.rescan_sample_frac == pytest.approx(0.05)


def test_params_perception_section_optional_with_spec_defaults():
    """黄金兼容面：无 perception 节的旧参数集回落规格缺省（golden manifest_params
    用旧参数校验全模型，缺键必须回落——BudgetParams 同款纪律）。"""
    data = _params_dict()
    data.pop("perception")
    params = ParamsConfig.model_validate(data)
    assert params.perception.l1.fast_top_k == 3          # 缺节 → 规格缺省
    assert params.perception.rules.enabled is True
    assert params.perception.l0.max_file_lines == 800


def test_params_perception_extra_forbidden():
    """perception 节越键拒绝（extra=forbid 纪律，M3 卷 B 组判据）。"""
    data = _params_dict()
    data["perception"]["rogue_knob"] = 1
    with pytest.raises(ValidationError):
        ParamsConfig.model_validate(data)


def test_m4_consult_knobs_pinned():
    """M4 consult 节 4 键 + perception.repomap 子节逐键钉值（SPEC-8 §6 回填）。"""
    p = load_params()
    assert p.consult.wall_clock_budget_s == 3600         # D33 pr 墙钟预算
    assert p.consult.min_panel == 2                      # 面板 fail-closed 下限
    assert p.consult.s1_rho == pytest.approx(0.4)        # D32（=M0 Z_PANEL_W）
    assert p.consult.l2_context_lines == 40              # L2 深诊档窗
    assert p.perception.repomap.max_files == 12          # D37 切片预算
    assert p.perception.repomap.max_lines == 1500


def test_params_consult_section_optional_with_spec_defaults():
    """黄金兼容面：无 consult 节的旧参数集回落规格缺省（M3 perception 同款）。"""
    data = _params_dict()
    data.pop("consult")
    params = ParamsConfig.model_validate(data)
    assert params.consult.wall_clock_budget_s == 3600
    assert params.consult.min_panel == 2
    assert params.consult.s1_rho == pytest.approx(0.4)


def test_params_consult_extra_forbidden():
    """consult 节越键拒绝（extra=forbid 纪律，M4 卷 B 组判据）。"""
    data = _params_dict()
    data["consult"]["rogue_knob"] = 1
    with pytest.raises(ValidationError):
        ParamsConfig.model_validate(data)


@pytest.mark.parametrize(
    "key,expected",
    [
        (f"{section}.{name}", value)
        for section, pins in M1_FINAL_KNOBS.items()
        for name, value in pins.items()
    ],
)
def test_m1_final_knob_pinned(key, expected):
    """逐键钉住 params.yaml M1 新键回填值：改任一规格逐字值即红。"""
    section, name = key.split(".")
    actual = getattr(getattr(load_params(), section), name)
    assert actual == expected, key


def test_m1_knob_pydantic_default_equals_spec():
    """缺 M1 新键的旧参数（黄金 manifest_params 形态）经 pydantic 回落规格缺省。

    回落值必须与 params.yaml 回填值逐键相等（2/5/10）：黄金 digest 逐位不变的
    前提，也是「缺省=规格逐字」在模型层的钉死。
    """
    data = _params_dict()
    for name in M1_FINAL_KNOBS["budget"]:
        data["budget"].pop(name)
    budget = ParamsConfig.model_validate(data).budget
    assert budget.FAST_CENTS == M1_FINAL_KNOBS["budget"]["FAST_CENTS"] == 2
    assert budget.COST_PER_L2_CENTS == M1_FINAL_KNOBS["budget"]["COST_PER_L2_CENTS"] == 5
    assert budget.COST_PER_S2_CENTS == M1_FINAL_KNOBS["budget"]["COST_PER_S2_CENTS"] == 10


def test_load_default_taxonomy():
    tax = load_taxonomy()
    assert set(tax.families) >= {
        "injection", "deser", "crypto", "authz", "config_dep", "mem_safety"
    }
    assert tax.families["injection"].types == ["sqli", "cmdi", "xss", "ssrf", "xxe"]
    assert tax.families["mem_safety"].v1_weight == pytest.approx(0.3)
    # 未显式给 v1_weight 的家族缺省 1.0
    assert tax.families["injection"].v1_weight == pytest.approx(1.0)


# ── 缺字段 / 未知字段 / 越界 ────────────────────────────────────

def _params_dict() -> dict:
    return load_params().model_dump()


def _taxonomy_dict() -> dict:
    return load_taxonomy().model_dump()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["promote"].pop("N"),                 # 缺叶子字段
        lambda d: d.pop("tilt"),                         # 缺整节
        lambda d: d["roster"].pop("verifier"),           # 缺 roster 项
        lambda d: d.update(tiltx=d["tilt"]),             # 未知字段（拼写错误）
        lambda d: d["promote"].update(DELTA=1.5),        # 越界：DELTA>1
        lambda d: d["promote"].update(DELTA=0.0),        # 越界：DELTA=0
        lambda d: d["promote"].update(N=0),              # 越界：N<1
        lambda d: d["shrink"].update(LAMBDA=-1),         # 越界：负收缩系数
        lambda d: d["decay"].update(EPS_FLOOR=2.0),      # 越界：概率域
        lambda d: d["budget"].update(RESCAN_WEEKLY=1.2), # 越界：占比>1
        lambda d: d["budget"].update(FAST_CENTS=-1),         # M1 越界：负预算
        lambda d: d["budget"].update(FAST_CENTS=0.5),        # M1 越界：非整数预算
        lambda d: d["budget"].update(COST_PER_L2_CENTS=-1),  # M1 越界：负成本估计
        lambda d: d["budget"].update(COST_PER_S2_CENTS=-1),  # M1 越界：负成本估计
        lambda d: d.pop("budget"),                           # 缺整节（M1 预算节）
        lambda d: d["tilt"].update(T_MIN=0.95, T_MAX=0.90),  # 交叉：T_MIN>T_MAX
        lambda d: d["exam"].update(PROMO_REAL=6),        # 交叉：PROMO_REAL>PROMO_ITEMS
        lambda d: d["panel"].update(MIN_FAMILIES=4),     # 交叉：MIN_FAMILIES>SIZE
    ],
)
def test_params_invalid(mutate, tmp_path):
    data = _params_dict()
    mutate(data)
    bad = tmp_path / "params.yaml"
    bad.write_text(_to_yaml(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_params(bad)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.pop("families"),                          # 缺根字段
        lambda d: d["families"]["injection"].pop("types"),    # 缺 types
        lambda d: d["families"]["injection"].update(types=[]),  # 空 types
        lambda d: d["families"]["injection"].update(v1_weight=0.0),  # 越界：权重≤0
        lambda d: d["families"]["mem_safety"].update(v1_weight=-0.3),
        lambda d: d.update(namspaces={}),                     # 未知字段
    ],
)
def test_taxonomy_invalid(mutate, tmp_path):
    data = _taxonomy_dict()
    mutate(data)
    bad = tmp_path / "taxonomy.yaml"
    bad.write_text(_to_yaml(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_taxonomy(bad)


def _to_yaml(data: dict) -> str:
    import yaml

    return yaml.safe_dump(data, allow_unicode=True)


def test_params_model_validate_rejects_directly():
    """不落盘也能拦住：直接 model_validate 同样报错（加载器与模型一体）。"""
    data = _params_dict()
    data["promote"].pop("M")
    with pytest.raises(ValidationError):
        ParamsConfig.model_validate(data)
    tax = _taxonomy_dict()
    tax["families"]["crypto"]["v1_weight"] = -1
    with pytest.raises(ValidationError):
        TaxonomyConfig.model_validate(tax)


def test_non_mapping_yaml_rejected(tmp_path):
    bad = tmp_path / "params.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ValueError, match="映射"):
        load_params(bad)


def test_copy_of_valid_params_still_valid():
    assert ParamsConfig.model_validate(_params_dict()) is not None
    assert copy.deepcopy(_taxonomy_dict())["families"]
