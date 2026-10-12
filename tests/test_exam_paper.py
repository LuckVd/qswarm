"""exams.paper 测试（SPEC-6 §3.1；SPEC-5 M2 卷 §1.4 最低用例集）。

钉死：ENTRY=12=8 真+4 雷、真雷位次固定交错、同 (seed, bank_version, agent, family)
重放逐位同卷（含跨 PYTHONHASHSEED）、不同 agent / bank_version 卷不同、卷内不重复、
池不足 ValueError、非 ENTRY purpose NotImplementedError。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from qswarm.contracts import ExamPurpose
from qswarm.exams.paper import ENTRY_FP, ENTRY_ITEMS, ENTRY_REAL, PAPER_PATTERN, draw_paper

from m2_harness import make_bank

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def bank(tmp_path_factory):
    return make_bank(tmp_path_factory.mktemp("paper"))


def test_draw_paper_shape_and_positions(bank):
    """ENTRY=12 题=8 真 4 雷；真雷位次固定交错模式 [真,真,雷,…]（S3）。"""
    paper = draw_paper(
        bank, agent_id="cheap:deepseek/deepseek-chat@cv1", role="scout",
        family="injection", purpose=ExamPurpose.ENTRY, bank_version=1, seed=7,
    )
    assert len(paper) == ENTRY_ITEMS == 12
    flags = [it.expected == "real" for it in paper]
    assert flags == list(PAPER_PATTERN)
    assert sum(flags) == ENTRY_REAL == 8 and sum(not f for f in flags) == ENTRY_FP == 4
    assert all(it.family == "injection" for it in paper)


def test_draw_paper_deterministic_same_inputs(bank, tmp_path):
    """同 (seed, bank_version, agent_id, family) 重放逐位同卷；跨 PYTHONHASHSEED
    一致（Fisher-Yates on 派生 rng，禁内建 hash）。"""
    kw = dict(
        agent_id="cheap:deepseek/deepseek-chat@cv1", role="scout",
        family="crypto", purpose=ExamPurpose.ENTRY, bank_version=1, seed=7,
    )
    a = [it.item_id for it in draw_paper(bank, **kw)]
    b = [it.item_id for it in draw_paper(bank, **kw)]
    assert a == b

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import sys\n"
        "sys.path.insert(0, %r)\n"      # 仓根（qswarm 可导入）
        "sys.path.insert(0, %r)\n"      # tests/（m2_harness 可导入）
        "from m2_harness import make_bank\n"
        "from qswarm.contracts import ExamPurpose\n"
        "from qswarm.exams.paper import draw_paper\n"
        "bank = make_bank(%r)\n"
        "print(','.join(it.item_id for it in draw_paper(\n"
        "    bank, agent_id='cheap:deepseek/deepseek-chat@cv1', role='scout',\n"
        "    family='crypto', purpose=ExamPurpose.ENTRY, bank_version=1, seed=7)))\n"
        % (str(REPO_ROOT), str(REPO_ROOT / "tests"), str(Path(str(bank._root)))),
        encoding="utf-8",
    )
    outs = []
    for seed in ("0", "987654"):
        proc = subprocess.run(
            [sys.executable, str(probe)], capture_output=True, text=True,
            cwd=REPO_ROOT, env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed, "HOME": "/tmp"},
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout.strip())
    assert outs[0] == outs[1]


def test_draw_paper_varies_across_agents_and_versions(bank):
    """不同 agent / 不同 bank_version 卷不同（非全同抽查——派生流标签入种子）。"""
    base = dict(role="scout", family="authz", purpose=ExamPurpose.ENTRY, seed=7)
    p1 = [it.item_id for it in draw_paper(bank, agent_id="cheap:a@cv1", bank_version=1, **base)]
    p2 = [it.item_id for it in draw_paper(bank, agent_id="cheap:b@cv1", bank_version=1, **base)]
    p3 = [it.item_id for it in draw_paper(bank, agent_id="cheap:a@cv1", bank_version=2, **base)]
    assert p1 != p2
    assert p1 != p3
    # 同 bank 的 bank_version 形参只作派生标签（跨版本不可混卷由调用方钉住；
    # 出卷面按 SPEC-3 §1 派生语义消费标签，DECISIONS M2 实现期行）
    draw_paper(bank, agent_id="cheap:a@cv1", bank_version=1, **base)
    with pytest.raises(NotImplementedError):
        draw_paper(bank, agent_id="cheap:a@cv1", bank_version=1,
                   role="scout", family="authz", purpose=ExamPurpose.PROMOTION, seed=7)
    with pytest.raises(NotImplementedError):
        draw_paper(bank, agent_id="cheap:a@cv1", bank_version=1,
                   role="scout", family="authz", purpose=ExamPurpose.PERIODIC, seed=7)


def test_draw_paper_no_duplicate_within_paper(bank, tmp_path):
    """卷内题不重复（12 题全异）；池不足 → ValueError（两池分别判）。"""
    for family in ("injection", "crypto", "authz", "config_dep", "mem_safety"):
        paper = draw_paper(bank, agent_id="cheap:x@cv1", role="scout", family=family,
                           purpose=ExamPurpose.ENTRY, bank_version=1, seed=1)
        ids = [it.item_id for it in paper]
        assert len(set(ids)) == 12

    def variant(drop):
        from qswarm.exams.bank import SeedBank

        return SeedBank.model_validate(
            {"bank_version": bank.bank_version, "generated_at": bank.generated_at,
             "items": [it for it in bank.items if not drop(it)]}
        )

    # 真池 < 8（mem_safety 真题 8 恰好达下限——抽掉一题即红，§3.1 池不足路径）
    thin_real = variant(lambda it: it.family == "mem_safety" and it.expected == "real"
                        and it.item_id == "SEED-0095")
    with pytest.raises(ValueError, match="真题池不足"):
        draw_paper(thin_real, agent_id="cheap:x@cv1", role="scout", family="mem_safety",
                   purpose=ExamPurpose.ENTRY, bank_version=1, seed=1)
    # 雷池 < 4
    thin_fp = variant(lambda it: it.family == "mem_safety" and it.expected == "fp"
                      and it.item_id in ("SEED-0097", "SEED-0099"))
    with pytest.raises(ValueError, match="雷池不足"):
        draw_paper(thin_fp, agent_id="cheap:x@cv1", role="scout", family="mem_safety",
                   purpose=ExamPurpose.ENTRY, bank_version=1, seed=1)


def test_draw_paper_rejects_non_entry_purpose(bank):
    """promotion/periodic → NotImplementedError（M2 范围裁剪，S4/S8）。"""
    for purpose in (ExamPurpose.PROMOTION, ExamPurpose.PERIODIC):
        with pytest.raises(NotImplementedError, match="ENTRY"):
            draw_paper(bank, agent_id="cheap:x@cv1", role="scout", family="injection",
                       purpose=purpose, bank_version=1, seed=7)
