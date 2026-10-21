"""qswarm.sandbox 测试（SPEC-9 §2/§3；M5 卷 A/B 组判据）。

钉死：SandboxConfig 红线词表与红线落档、env 身份确定性、PoC 双围栏提取与
断言行解析、家族模板（python 注入/穿越 + 他族如实 None）、FakeRunner、
classify_result、process 档真执行（真子进程 + 近似红线如实记录）、
container 档 docker 缺失 → env_build_fail（不误判 poc_fail）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qswarm.contracts import load_params
from qswarm.sandbox import (
    EnvBuildError,
    FakeRunner,
    ProcessRunner,
    SandboxConfig,
    SandboxResult,
    classify_result,
    env_key_for,
    extract_poc,
    make_runner,
    parse_assertions,
    template_poc,
)

# ── SandboxConfig（红线词表；§2.2）───────────────────────────────


def test_sandbox_config_redline_vocabulary():
    """红线词表守卫：tier 双档、network 只收 none；越值 ValueError。"""
    cfg = SandboxConfig()
    assert cfg.tier == "process"                      # D38：缺省开发/CI 档
    assert cfg.network == "none"                      # 默认禁网（§16.7-5）
    assert cfg.read_only_root is True
    with pytest.raises(ValueError, match="tier"):
        SandboxConfig(tier="bare-metal")
    with pytest.raises(ValueError, match="network"):
        SandboxConfig(network="bridge")


def test_sandbox_config_from_params_and_redline_report():
    """params.sandbox 节 → 配置（tier 显式覆盖）+ 红线逐条落档（验收卷机器可检）。"""
    params = load_params()
    cfg = SandboxConfig.from_params(params)
    assert cfg.tier == "process"
    assert cfg.timeout_s == 340
    override = SandboxConfig.from_params(params, tier="container")
    assert override.tier == "container"
    report = override.redline_report()
    assert report["network"] == "none"
    assert report["user"] == "nobody"
    assert report["read_only_root"] is True
    assert report["docker_sock_mounted"] is False     # 红线：守护进程套接字零挂载
    assert report["timeout_s"] == 340
    assert report["mem_mb"] == 512


def test_env_key_deterministic_and_dep_sensitive():
    """env 身份：同 (base, deps) 恒等；deps 序不敏感、内容敏感（§16.4 缓存键）。"""
    k1 = env_key_for("python:3.11", ["flask", "PyYAML"])
    k2 = env_key_for("python:3.11", ["PyYAML", "flask"])
    k3 = env_key_for("python:3.11", ["flask"])
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 16


# ── PoC 生成与 harness 契约（§3；G2 生成面）──────────────────────


def test_extract_poc_takes_last_python_fence():
    """D39 一体产：```json 报告 + ```python PoC 双围栏——取最后一个 python 围栏
    （与 prompts.parse.extract_json 的「最后一个围栏」纪律同形）。"""
    raw = (
        '```json\n{"outcome": "verified_real"}\n```\n'
        "```python\nprint('first')\n```\n"
        "```python\nprint('second')\n```\n"
    )
    assert extract_poc(raw) == "print('second')"
    assert extract_poc("no fence at all") is None


def test_parse_assertions_harness_contract():
    """断言行约定：``ASSERT|名|PASS|详情``（或 FAIL）→ (名, observed, 详情)。"""
    out = "ASSERT|a1|PASS|marker=x\nnoise\nASSERT|a2|FAIL|exit=1\n"
    assert parse_assertions(out) == [("a1", True, "marker=x"), ("a2", False, "exit=1")]
    assert parse_assertions("no assertions") == []


def test_template_poc_families_and_unsupported():
    """家族模板：python 注入/穿越可生成；他族如实 None（不硬凑，§3.3）。"""
    poc = template_poc("injection", "command_injection", path="src/api.py",
                       line_start=5, lang="python", seed="ab12")
    assert poc is not None and "QSWARM_POC_ab12" in poc
    assert "importlib" in poc                         # 双级驱动（函数内 sink 形态）
    trav = template_poc("authz", "path_traversal", path="src/t.py",
                        line_start=3, lang="python", seed="cd34")
    assert trav is not None and "qswarm_canary" in trav
    assert template_poc("mem_safety", "overflow", path="x.c", line_start=1,
                        lang="c", seed="x") is None
    assert template_poc("injection", "cmdi", path="a.py", line_start=1,
                        lang="java", seed="x") is None


# ── FakeRunner 与分类切片 ────────────────────────────────────────


def test_fake_runner_scripted_and_calls():
    """FakeRunner：脚本化结果 + 入参记录（离线判据；真实子进程零启动）。"""
    runner = FakeRunner(script=True)
    res = runner.execute("print(1)", repo=Path("."), deps=["flask"], seed="s1")
    assert res.ok is True and res.tier == "fake"
    assert res.stdout.startswith("ASSERT|fake|PASS")
    assert runner.calls == [{"poc": "print(1)", "repo": ".", "deps": ["flask"],
                             "seed": "s1"}]


def _res(**kw) -> SandboxResult:
    base = dict(tier="process", ok=False, exit_code=1, timed_out=False,
                duration_s=0.1, stdout="", stderr="", env_key="k", env_base="b",
                workdir=None)
    base.update(kw)
    return SandboxResult(**base)


def test_classify_result_runner_side_slice():
    """四分类的 runner 侧切片：env_build_fail/timeout；poc_fail vs flaky 归编排层。"""
    assert classify_result(_res(error_kind="env_build_fail")) == "env_build_fail"
    assert classify_result(_res(timed_out=True, error_kind="timeout")) == "timeout"
    assert classify_result(_res(ok=True, exit_code=0)) is None
    assert classify_result(_res()) is None            # 执行红、非 env/timeout → None


# ── process 档真执行（真子进程 + 近似红线）────────────────────────


def _make_vuln_repo(root: Path) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "top_tool.py").write_text(
        'import os, sys\nos.system("echo " + sys.argv[1])\n', encoding="utf-8"
    )
    return root


def test_process_runner_real_execution_confirm_and_fail(tmp_path):
    """process 档真执行：注入靶复现 PASS（exit 0）、干净靶如实 FAIL（exit 1）。"""
    repo = _make_vuln_repo(tmp_path / "repo")
    cfg = SandboxConfig(tier="process", timeout_s=60,
                        env_cache_dir=str(tmp_path / "env"))
    runner = make_runner(cfg)
    assert isinstance(runner, ProcessRunner)
    poc = template_poc("injection", "command_injection", path="src/top_tool.py",
                       line_start=2, lang="python", seed="ab12")
    res = runner.execute(poc, repo=repo, deps=[], seed="ab12")
    assert res.ok is True and res.exit_code == 0
    asserts = parse_assertions(res.stdout)
    assert asserts and any(observed for _n, observed, _d in asserts)
    # 近似红线面如实记录（unshare 可用性随环境，键恒在）
    assert res.mitigations["network"] in ("unshare-net", "not-isolated")
    assert res.mitigations["rootfs"] == "repo-snapshot-copy"
    assert res.env_key and res.env_base.startswith("process:")
    # 干净靶：同模板 FAIL（复现失败 ≠ 误报，编排层判 poc_fail）
    (repo / "src" / "clean.py").write_text("def add(a, b):\n    return a + b\n",
                                           encoding="utf-8")
    poc2 = template_poc("injection", "command_injection", path="src/clean.py",
                        line_start=1, lang="python", seed="ab12")
    res2 = runner.execute(poc2, repo=repo, deps=[], seed="ab12")
    assert res2.ok is False and res2.exit_code != 0
    assert res2.error_kind is None                    # runner 侧不判 poc_fail
    # workdir 面按隔离实况：降权成功 → 现场保留供取证；未降级（非 root）→ 随清。
    assert res2.workdir is None or (Path(res2.workdir) / "repo").exists()


def test_process_runner_timeout_classification(tmp_path):
    """墙钟超时 → error_kind=timeout（§6.3 四分类红线项）。"""
    repo = _make_vuln_repo(tmp_path / "repo")
    cfg = SandboxConfig(tier="process", timeout_s=2,
                        env_cache_dir=str(tmp_path / "env"))
    runner = make_runner(cfg)
    res = runner.execute("import time\ntime.sleep(60)\n", repo=repo, deps=[],
                         seed="t1")
    assert res.timed_out is True
    assert classify_result(res) == "timeout"
    assert res.ok is False


def test_process_runner_env_cache_reuse(tmp_path):
    """env 缓存：同 deps 重跑复用（env_key 恒等 + 标记文件在；§16.4）。"""
    repo = _make_vuln_repo(tmp_path / "repo")
    cfg = SandboxConfig(tier="process", timeout_s=60,
                        env_cache_dir=str(tmp_path / "env"))
    runner = make_runner(cfg)
    poc = template_poc("injection", "command_injection", path="src/top_tool.py",
                       line_start=2, lang="python", seed="s")
    r1 = runner.execute(poc, repo=repo, deps=[], seed="s1")
    r2 = runner.execute(poc, repo=repo, deps=[], seed="s2")
    assert r1.env_key == r2.env_key
    marker = (Path(cfg.env_cache_dir) / "process" / r1.env_key
              / ".qswarm-env-ok")
    assert marker.exists()


def test_container_runner_docker_missing_is_env_build_fail(tmp_path, monkeypatch):
    """container 档 docker 不可达 → EnvBuildError 面 → env_build_fail
    （不误判 poc_fail；D38 双档语义：拒绝降级执行）。"""
    import qswarm.sandbox.runner as R

    monkeypatch.setattr(R.shutil, "which", lambda _n: None)
    repo = _make_vuln_repo(tmp_path / "repo")
    cfg = SandboxConfig(tier="container", timeout_s=30,
                        env_cache_dir=str(tmp_path / "env"))
    runner = R.ContainerRunner(cfg)
    res = runner.execute("print(1)", repo=repo, deps=[], seed="x")
    assert res.env_build_failed is True
    assert classify_result(res) == "env_build_fail"
    assert "docker" in res.stderr


def test_make_runner_tier_guard():
    """档位守卫：runner 构造与配置档强一致（D38 双档并存，无静默换档）。"""
    from qswarm.sandbox.runner import ContainerRunner

    assert isinstance(make_runner(SandboxConfig(tier="process")), ProcessRunner)
    assert isinstance(make_runner(SandboxConfig(tier="container")), ContainerRunner)
    with pytest.raises(ValueError, match="tier=container"):
        ContainerRunner(SandboxConfig(tier="process"))


# ── container 档真 docker（环境可用时实跑；缺 docker 跳过）─────────

import shutil as _shutil

pytestmark_real = pytest.mark.skipif(
    _shutil.which("docker") is None, reason="docker 不可用（D38-a 环境面）"
)


@pytestmark_real
def test_container_runner_real_docker_confirm(tmp_path):
    """D38-a：真 docker container 档——禁网/非特权/只读根 + tmpfs 全施加，
    注入靶复现 PASS；超时 PoC 收割为 timeout（§6.3）。"""
    repo = _make_vuln_repo(tmp_path / "repo")
    cfg = SandboxConfig(tier="container", timeout_s=120,
                        env_cache_dir=str(tmp_path / "env"))
    runner = make_runner(cfg)
    poc = template_poc("injection", "command_injection", path="src/top_tool.py",
                       line_start=2, lang="python", seed="cd34")
    res = runner.execute(poc, repo=repo, deps=[], seed="cd34")
    assert res.tier == "container"
    assert res.mitigations["network"] == "docker-network-none"
    assert res.mitigations["user"] == "nobody"
    assert res.mitigations["docker_sock_mounted"] is False
    asserts = [a for a in parse_assertions(res.stdout) if a[2].startswith("stage=argv")]
    assert res.ok is True and asserts and asserts[0][1] is True
    # 超时面（短墙钟收割）
    cfg2 = SandboxConfig(tier="container", timeout_s=3,
                         env_cache_dir=str(tmp_path / "env"))
    res2 = make_runner(cfg2).execute("import time\ntime.sleep(60)\n",
                                     repo=repo, deps=[], seed="t")
    assert res2.timed_out is True and classify_result(res2) == "timeout"
