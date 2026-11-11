"""R1-T7 打包与资源测试（HANDOVER §11；docs/R1_CHANGE_NOTE.md §1-T7）。

- **哈希同步**：包内 canonical 副本（qswarm/defaults/）与仓内 configs/ 冻结件
  逐字节一致——不维护两套默默漂移的默认值；
- **默认资源安装包可用**：importlib.resources 读取（与源码树/安装两态无关）；
- **显式路径优先且不存在即报错**（不偷偷回落默认）；
- **运行子包完整**：exams/prompts/supply/sandbox 四包（2026-10-28 审查缺件面）。

干净安装验收（sdist→wheel→非 editable 安装→无关目录导入→离线闭环）不跑在
pytest 内（需构建工具与全 venv 操作）——落
``out/r1-hardening/<run-id>/package-check/``（脚本 package_check.py）。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

#: 仓内冻结件 ↔ 包内 canonical 副本（R1-T7 哈希同步面）。
_SYNC_PAIRS = [
    ("configs/params.yaml", "qswarm/defaults/params.yaml"),
    ("configs/taxonomy.yaml", "qswarm/defaults/taxonomy.yaml"),
    ("configs/semgrep_rules.yaml", "qswarm/defaults/semgrep_rules.yaml"),
]


@pytest.mark.parametrize("repo_rel,pkg_rel", _SYNC_PAIRS)
def test_r1_t7_defaults_hash_sync_with_configs(repo_rel, pkg_rel):
    """canonical 副本与冻结配置逐字节一致（漂移即红）。"""
    a = (_REPO / repo_rel).read_bytes()
    b = (_REPO / pkg_rel).read_bytes()
    assert hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest(), (
        f"{repo_rel} 与 {pkg_rel} 漂移——同步复制并复核语义后重跑"
    )


def test_r1_t7_default_resources_load_from_package():
    """默认资源经 importlib.resources 可读（安装态/源码态同径）。"""
    from qswarm.contracts import DEFAULT_PARAMS_PATH, DEFAULT_TAXONOMY_PATH, load_params
    from qswarm.contracts.config import _packaged_default

    assert DEFAULT_PARAMS_PATH.is_file() and DEFAULT_TAXONOMY_PATH.is_file()
    assert load_params().sandbox.tier in ("process", "container")
    rules = _packaged_default("semgrep_rules.yaml")
    assert rules.is_file() and rules.suffix == ".yaml"


def test_r1_t7_explicit_path_missing_raises_no_fallback(tmp_path, monkeypatch):
    """显式配置路径不存在 → 报错（不偷偷回落默认）。"""
    from qswarm.contracts import load_params

    monkeypatch.chdir(tmp_path)          # 无关 cwd（默认资源路径不依赖 cwd）
    with pytest.raises(FileNotFoundError):
        load_params(tmp_path / "nope.yaml")


def test_r1_t7_runtime_subpackages_importable():
    """四运行子包完整（2026-10-28 审查缺件面：exams/prompts/supply/sandbox）。"""
    import qswarm.exams          # noqa: F401
    import qswarm.prompts        # noqa: F401
    import qswarm.sandbox        # noqa: F401
    import qswarm.supply         # noqa: F401
    from qswarm.contracts import schema_sql

    assert "CREATE TABLE" in schema_sql()


def test_r1_t7_fixtures_packaged():
    """九类夹具随包（oracle 判据面的安装态输入）。"""
    from qswarm.sandbox.fixtures import FAMILIES, FIXTURES_ROOT, VARIANTS

    assert FIXTURES_ROOT.is_dir()
    for fam in FAMILIES:
        for var in VARIANTS:
            assert (FIXTURES_ROOT / fam / var / "target.py").is_file()
