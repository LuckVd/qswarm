"""D30 必选门禁：通道 C 实跑判据（SPEC-7 §1.4/§7；M3 卷 A 组 §1.2 gate 判据）。

semgrep 必选件口径：装通 + 家族规则集映射 + tier-1 语言样例**实跑**出 rule_hit
候选——本文件直接以生产运行器（subprocess CLI + 仓内规则包）执行，不用注入替身。
环境缺 semgrep → 显式 FAIL（不静默降级为可选；D30 负责人裁决）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import qswarm
from qswarm.contracts import load_params
from qswarm.perception.channels import default_rules_runner, channel_c_scan
from qswarm.perception.indexer import probe_languages
from qswarm.perception.scan import scan_repo

RULES = Path(qswarm.__file__).resolve().parents[1] / "configs" / "semgrep_rules.yaml"

if shutil.which("semgrep") is None:  # pragma: no cover — 本环境已装通
    pytest.fail("D30 必选件 semgrep 未装通（不得静默降级为可选）")


def test_rules_pack_exists_with_family_metadata():
    """规则包在位且每条规则带 metadata.family（家族映射载体；词表六家族）。"""
    import yaml

    data = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    rules = data["rules"]
    assert len(rules) >= 10
    vocab = {"injection", "deser", "crypto", "authz", "config_dep", "mem_safety"}
    mapped = set()
    for rule in rules:
        family = rule["metadata"]["family"]
        assert family in vocab, rule["id"]
        assert rule["id"].startswith("qsw."), rule["id"]
        mapped.add(family)
    assert {"injection", "crypto", "config_dep", "mem_safety"} <= mapped


def test_channel_c_real_run_tier1_rule_hits(tmp_path):
    """tier-1 语言样例实跑：python/c 样例经生产运行器出 rule_hit（家族映射正确）。"""
    src = tmp_path / "src"
    src.mkdir()
    (src / "api.py").write_text(
        "import os\n\n\ndef run(cmd):\n    os.system(cmd)\n", encoding="utf-8"
    )
    (src / "util.c").write_text(
        "#include <string.h>\nvoid f(char *d, char *s) { strcpy(d, s); }\n",
        encoding="utf-8",
    )
    runner = default_rules_runner(120, rules_path=RULES)
    out = channel_c_scan(
        [str(src / "api.py"), str(src / "util.c")],
        rules_runner=runner,
        repo_root=tmp_path,
    )
    assert out.candidates is not None and out.skip_reason is None
    checks = {(c.taxonomy_hint, c.kind) for c in out.candidates}
    assert ("injection", "rule_hit") in checks
    assert ("mem_safety", "rule_hit") in checks
    assert all(c.priority == 2 for c in out.candidates)      # C 专属 +1


def test_channel_c_gate_through_scan(tmp_path):
    """全链形态：scan_repo 以生产 semgrep 运行器跑 tier-1 仓 → 通道 C 计数 > 0、
    零缺席计数（装通环境下规则通道全勤）。"""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "api.py").write_text(
        "import hashlib\n\n\ndef h(x):\n    return hashlib.md5(x).hexdigest()\n",
        encoding="utf-8",
    )
    rep = scan_repo(
        root, mode="full", params=load_params(),
        rules_runner=default_rules_runner(120, rules_path=RULES),
    )
    assert rep.channel_c_count >= 1
    assert rep.rule_channel_skipped is None
    assert {"python"} <= set(probe_languages())              # tier-1 探测在位
