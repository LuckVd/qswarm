"""S2 oracle 九类负对照判据测试（方案 §6.2/§6.3；SPEC-10 §6/§8；P1 工作包 D）。

三面：
- **判层单测**（零执行）：validate_pair 四分支、assess_single 判据白名单/
  对照门/蜂产非独立、观察器判读、九类期望表完整性；
- **真机夹具面**（process 档实跑，同 test_m5_sandbox 惯例）：两族代表性对照
  × run_control + 成对判据（全矩阵归 run_e0_checks.py，已真机归档）；
- **模板 v2 回归**（R1 钉）：仅回显目标必须 FAIL。
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path

import pytest

from qswarm.sandbox.config import SandboxConfig
from qswarm.sandbox.fixtures import FAMILIES, FIXTURES_ROOT, NINE_CLASSES, VARIANTS
from qswarm.sandbox.oracle import (
    ORACLE_VERSION,
    ClaimValidation,
    ControlRun,
    ExecutionStatus,
    OracleKind,
    assess_single,
    build_fixture_driver,
    observe_injection,
    observe_traversal,
    run_control,
    validate_pair,
)
from qswarm.sandbox.pocgen import (
    CONTROL_ASSERTIONS,
    VIOLATION_ASSERTIONS,
    parse_assertions,
    template_poc,
)
from qswarm.sandbox.runner import ProcessRunner


# ── 判层单测（零执行）───────────────────────────────────────────

def _ctrl(variant: str, *, violation: bool, ok: bool = True,
          timed_out: bool = False, target_executed: bool = True,
          target_health: str = "healthy", legal_control: bool | None = None,
          ) -> ControlRun:
    """oc-v2 构造辅助：健康分立字段缺省健康（§6.4 判据面）。"""
    return ControlRun(
        variant=variant,
        execution=ExecutionStatus(
            target=f"injection/{variant}", sandbox_ok=ok, timed_out=timed_out,
            exit_code=0 if ok else 1, env_base="test",
            target_executed=target_executed, target_health=target_health,
            legal_control=legal_control,
        ),
        violation_observed=violation, violation_evidence="synthetic",
    )


def test_validate_pair_supported_requires_vuln_only():
    v = validate_pair(_ctrl("vuln", violation=True), _ctrl("fix", violation=False))
    assert v.claim_validation is ClaimValidation.SUPPORTED
    assert v.oracle_kind is OracleKind.EXECUTION_PAIRED
    assert v.oracle_version == ORACLE_VERSION
    assert v.reason == "vuln_violates_fix_does_not"


def test_validate_pair_fix_env_failure_is_not_negative_control():
    """§6.2 逐字：修复版环境失败不算负对照通过（→inconclusive）。"""
    v = validate_pair(_ctrl("vuln", violation=True), _ctrl("fix", violation=False, ok=False))
    assert v.claim_validation is ClaimValidation.INCONCLUSIVE
    assert v.reason == "fix_control_invalid"


def test_validate_pair_no_violation_is_inconclusive_never_refuted():
    """第 ⑨ 类硬判据：真实漏洞但 PoC 无效 → inconclusive（执行判据不出 refuted）。"""
    v = validate_pair(_ctrl("vuln", violation=False), _ctrl("fix", violation=False))
    assert v.claim_validation is ClaimValidation.INCONCLUSIVE
    assert v.reason == "violation_not_observed"
    both = validate_pair(_ctrl("vuln", violation=True), _ctrl("fix", violation=True))
    assert both.claim_validation is ClaimValidation.INCONCLUSIVE
    assert both.reason == "poc_not_discriminating"
    vuln_fail = validate_pair(_ctrl("vuln", violation=False, ok=False), _ctrl("fix", violation=False))
    assert vuln_fail.reason == "vuln_execution_failed"


def test_assess_single_frozen_whitelist_and_control_gate():
    """判据只认冻结断言名；对照断言 FAIL 只降不升；蜂产自报非独立。"""
    viol = VIOLATION_ASSERTIONS["injection"]
    ok = assess_single(
        sandbox_ok=True, timed_out=False, exit_code=0,
        assertions=[(viol[0], True, "d")], violation_assertions=list(viol),
        poc_source="template",
    )
    assert ok.claim_validation is ClaimValidation.SUPPORTED
    assert ok.oracle_kind is OracleKind.MECHANICAL_SINGLE
    assert ok.reason == "frozen_harness_violation_asserted"

    # 自造断言名（不在白名单）——无条件 PASS 第 ⑦ 类：不构成判据
    fake = assess_single(
        sandbox_ok=True, timed_out=False, exit_code=0,
        assertions=[("i_am_totally_real", True, "self-reported")],
        violation_assertions=list(viol), poc_source="template",
    )
    assert fake.claim_validation is ClaimValidation.INCONCLUSIVE
    assert fake.reason == "violation_not_observed"

    # 对照门：traversal legal_control FAIL → violation 不认
    trav = VIOLATION_ASSERTIONS["authz"]
    ctrl = CONTROL_ASSERTIONS["authz"]
    gated = assess_single(
        sandbox_ok=True, timed_out=False, exit_code=0,
        assertions=[(trav[0], True, "d"), (ctrl[0], False, "d")],
        violation_assertions=list(trav), control_assertions=list(ctrl),
        poc_source="template",
    )
    assert gated.claim_validation is ClaimValidation.INCONCLUSIVE
    assert gated.reason == "control_failed"

    # 蜂产 PoC：断言自报非独立判据 → 如实 inconclusive（不改 outcome 行为）
    bee = assess_single(
        sandbox_ok=True, timed_out=False, exit_code=0,
        assertions=[(viol[0], True, "d")], violation_assertions=list(viol),
        poc_source="bee",
    )
    assert bee.claim_validation is ClaimValidation.INCONCLUSIVE
    assert bee.reason == "judge_not_independent_self_reported"

    for bad, reason in (
        (dict(sandbox_ok=False, timed_out=False, exit_code=1), "execution_failed"),
        (dict(sandbox_ok=False, timed_out=True, exit_code=None), "execution_timeout"),
    ):
        r = assess_single(
            assertions=[(viol[0], True, "d")], violation_assertions=list(viol),
            poc_source="template", **bad,
        )
        assert r.claim_validation is ClaimValidation.INCONCLUSIVE
        assert r.reason == reason


def test_observers_judge_from_obs_dir_not_self_report(tmp_path):
    """观察器只读观察目录（金丝雀/目标输出）；缺目录/缺文件 → 不违规。

    oc-v2 §6.5：观察目录须为系统临时根直下 ``qswarm-obs-*``（宿主侧边界）——
    越界路径（含 pytest tmp 根）判 untrusted 不读取。"""
    import tempfile as _tf

    nonce = "n" * 16
    obs = Path(_tf.mkdtemp(prefix="qswarm-obs-r1ctrl-"))   # 每 run 独立（无跨 run 残留）
    ex = ExecutionStatus(
        target="t", sandbox_ok=True, timed_out=False, exit_code=0,
        env_base="t", obs_dir=str(obs),
        assertions=[("injection_payload_executed", True, "self-reported")],
    )
    hit, ev = observe_injection(ex, nonce)
    assert hit is False and ev == "canary_absent_or_unreadable"   # 自报不算

    canary = obs / f"canary_{nonce}"
    canary.write_text(nonce, encoding="utf-8")
    hit, ev = observe_injection(ex, nonce)
    assert hit is True and "content=nonce" in ev
    canary.write_text("wrong", encoding="utf-8")
    assert observe_injection(ex, nonce) == (False, "canary_content_mismatch")

    ex2 = ExecutionStatus(
        target="t", sandbox_ok=True, timed_out=False, exit_code=0,
        env_base="t", obs_dir=str(obs),
    )
    assert observe_traversal(ex2, nonce)[0] is False   # 无 target_output.txt
    (obs / "target_output.txt").write_text(
        "qsw-trav-" + nonce, encoding="utf-8")
    assert observe_traversal(ex2, nonce)[0] is True
    assert observe_injection(
        ExecutionStatus(target="t", sandbox_ok=True, timed_out=False,
                        exit_code=0, env_base="t", obs_dir=None),
        nonce,
    ) == (False, "obs_dir_missing")


def test_nine_classes_table_integrity():
    """期望表：两族 × 九类 = 18 行；硬判据——③⑦⑧ violation=False、⑨ 必须
    inconclusive；①的 supported 只出自成对（单控件 claim=inconclusive）。"""
    assert len(NINE_CLASSES) == len(FAMILIES) * 9 == 18
    by_klass = {}
    for spec in NINE_CLASSES:
        key = (spec.klass, spec.family)
        assert key not in by_klass
        by_klass[key] = spec
        assert spec.variant in VARIANTS
    for family in FAMILIES:
        for klass in (3, 7, 8, 9):
            spec = by_klass[(klass, family)]
            assert spec.violation is False
            assert spec.claim == "inconclusive"
        assert by_klass[(1, family)].claim == "supported"   # 成对产物
        assert by_klass[(1, family)].variant == "vuln"
        assert by_klass[(2, family)].variant == "fix"


def test_driver_templates_parse_all_kinds():
    for family in FAMILIES:
        for variant in ("vuln", "fix", "echo_safe"):
            for kind in ("effective", "unconditional_pass", "no_target_call",
                         "ineffective"):
                src = build_fixture_driver(
                    family, variant, driver_kind=kind, nonce="deadbeef")
                ast.parse(src)
    with pytest.raises(ValueError, match="driver_kind"):
        build_fixture_driver("injection", "vuln", driver_kind="cheat")


def test_pocgen_whitelist_tables():
    """冻结断言名与模板产物一致（判据面单一真源）。"""
    inj = template_poc("injection", None, path="a.py", line_start=1,
                       lang="python", seed="s")
    assert "ASSERT|injection_payload_executed|" in inj
    trav = template_poc("authz", None, path="a.py", line_start=1,
                        lang="python", seed="s")
    assert "ASSERT|path_traversal_read|" in trav
    assert "ASSERT|legal_control|" in trav
    assert set(VIOLATION_ASSERTIONS) == {"injection", "authz", "config_dep"}
    assert VIOLATION_ASSERTIONS["authz"] == VIOLATION_ASSERTIONS["config_dep"]


# ── 真机夹具面（process 档实跑；全矩阵归 run_e0_checks.py）──────

@pytest.fixture(scope="module")
def runner():
    with tempfile.TemporaryDirectory(prefix="oracle-test-") as td:
        cfg = SandboxConfig(tier="process", timeout_s=30,
                            env_cache_dir=str(Path(td) / "env"))
        yield ProcessRunner(cfg)


@pytest.mark.parametrize("family", FAMILIES)
def test_real_fixture_pair_supported(runner, family):
    """①+② 成对：漏洞版违规 ∧ 修复版不违规 → supported（真机）。"""
    vuln = run_control(runner, FIXTURES_ROOT, family=family, variant="vuln",
                       nonce="pytestpair01")
    fix = run_control(runner, FIXTURES_ROOT, family=family, variant="fix",
                      nonce="pytestpair01")
    assert vuln.violation_observed is True
    assert fix.violation_observed is False
    assert (validate_pair(vuln, fix).claim_validation
            is ClaimValidation.SUPPORTED)


@pytest.mark.parametrize("family", FAMILIES)
def test_real_echo_safe_not_supported(runner, family):
    """第 ③ 类（R1 回归）：仅回显安全版无违规——旧 echo-marker 会被击穿。"""
    echo = run_control(runner, FIXTURES_ROOT, family=family,
                       variant="echo_safe", nonce="pytestecho01")
    assert echo.violation_observed is False
    self_reported = [n for n, ok, _ in echo.execution.assertions if ok]
    assert not self_reported                    # 自报也如实 FAIL


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("driver_kind", ["unconditional_pass", "no_target_call",
                                         "ineffective"])
def test_real_driver_negative_classes(runner, family, driver_kind):
    """⑦⑧⑨：不调用目标/自报 PASS/无效驱动 → 无违规（⑨ 绝不 verified_fp）。"""
    ctrl = run_control(runner, FIXTURES_ROOT, family=family, variant="vuln",
                       driver_kind=driver_kind, nonce="pytestneg01")
    assert ctrl.violation_observed is False


@pytest.mark.parametrize("family", FAMILIES)
def test_real_missing_dep_records_target_failure(runner, family):
    """第 ⑤ 类：目标启动失败如实入执行面（target_exit_code=1），不入可信面。"""
    ctrl = run_control(runner, FIXTURES_ROOT, family=family,
                       variant="missing_dep", nonce="pytestdep01")
    assert ctrl.violation_observed is False
    assert ctrl.execution.target_exit_code == 1


def test_real_template_injection_regression(runner):
    """模板 v2 × 夹具：漏洞版 PASS、修复版/仅回显版 FAIL（R1 钉）。"""
    for variant, expect in (("vuln", True), ("fix", False), ("echo_safe", False)):
        poc = template_poc("injection", None,
                           path=f"injection/{variant}/target.py",
                           line_start=1, lang="python", seed="pytest-tpl")
        res = runner.execute(poc, repo=FIXTURES_ROOT, deps=[],
                             seed=f"pytest-tpl-{variant}")
        got = dict(
            (n, ok) for n, ok, _ in parse_assertions(res.stdout)
        ).get("injection_payload_executed")
        assert got is expect
