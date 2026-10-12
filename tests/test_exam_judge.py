"""exams.judge 测试（SPEC-6 §3.3；SPEC-5 M2 卷 §1.5 最低用例集）。

钉死：scout/auditor 判分负例矩阵（real 命中/漏报、fp 拒报/误报、parsed=None 语义、
家族错配、injection_flag 不判分）、reviewer/verifier NotImplementedError、
及格线三函数对 §8.2 逐字（边界值）。
"""

from __future__ import annotations

import pytest

from qswarm.contracts import ExamParams, ScoutReport
from qswarm.exams.judge import (
    entry_qc_pass,
    judge_answer,
    periodic_exam_passes,
    promotion_exam_passes,
)
from qswarm.exams.bank import SeedItem

from m2_harness import auditor_answer, scout_answer


def item(expected: str, family: str = "injection") -> SeedItem:
    return SeedItem(
        item_id="SEED-0001", taxonomy_path=[family], lang="python",
        repo_snapshot_ref=f"artifact://seeds/SEED-0001/", planted=False,
        expected=expected, difficulty="easy", version=1,
        canary="qsw-canary-SEED-0001-deadbeef", source="cve_rollback",
        snapshot_files=["main.c"],
        upstream={"cve": "CVE-2099-0001", "parent_sha": "a" * 40, "commit_date": "2098-01-01"},
    )


REAL = item("real")
FP = item("fp")


def scout_parsed(suspicious: bool, family: str | None = "injection") -> ScoutReport:
    return ScoutReport(
        candidate_id="CD-1", suspicious=suspicious, guess_family=family,
        guess_type=None, reason_ref="artifact://pending", injection_flag=False,
    )


def test_judge_scout_matrix():
    """real: suspicious∧guess_family 命中=对；fp: suspicious=False∨guess=None=对；
    parsed=None 按 real 错/fp 对；家族错配=错；injection_flag 不判分。"""
    # real
    assert judge_answer(REAL, "scout", scout_parsed(True, "injection")) is True
    assert judge_answer(REAL, "scout", scout_parsed(True, "crypto")) is False      # 家族错
    assert judge_answer(REAL, "scout", scout_parsed(True, None)) is False          # 无家族
    assert judge_answer(REAL, "scout", scout_parsed(False, None)) is False         # 漏报
    assert judge_answer(REAL, "scout", None) is False                              # declined
    # fp
    assert judge_answer(FP, "scout", scout_parsed(False, None)) is True            # 拒报
    assert judge_answer(FP, "scout", scout_parsed(True, None)) is True             # 可疑但无可路由猜测=与拒报同效
    assert judge_answer(FP, "scout", scout_parsed(True, "injection")) is False     # 对埋雷报了家族
    assert judge_answer(FP, "scout", scout_parsed(True, "crypto")) is False
    assert judge_answer(FP, "scout", None) is True                                 # declined=对
    # injection_flag 不参与判分（记录面）
    flagged = scout_parsed(True, "injection")
    assert judge_answer(REAL, "scout", flagged) is True
    assert flagged.injection_flag is False
    # Role 枚举与字符串等价
    from qswarm.contracts import Role

    assert judge_answer(REAL, Role.SCOUT, scout_parsed(True, "injection")) is True
    # 家族级判分：taxonomy_path[0]（类型级不判，S4）
    assert REAL.taxonomy_path == ["injection"]


def test_judge_auditor_matrix():
    """real: 产出∧family 命中=对；产出但家族错=错；fp: declined=对、产出=错
    （产出多题任一 family 命中不放宽——信封单 finding 形态）。"""
    from qswarm.contracts import AuditorFinding

    def finding(payload: dict, **kw) -> AuditorFinding:
        return AuditorFinding.model_validate(auditor_answer(payload, **kw))

    real_payload = {"taxonomy_path": ["injection"], "expected": "real"}
    fp_payload = {"taxonomy_path": ["injection"], "expected": "fp"}
    assert judge_answer(REAL, "auditor", finding(real_payload)) is True
    assert judge_answer(REAL, "auditor", finding(real_payload, family="crypto")) is False
    assert judge_answer(REAL, "auditor", None) is False                            # declined=漏报
    assert judge_answer(FP, "auditor", None) is True                               # declined=正确拒报
    assert judge_answer(FP, "auditor", finding(fp_payload)) is False
    assert judge_answer(FP, "auditor", finding(fp_payload, family="crypto")) is False
    with pytest.raises(NotImplementedError):
        judge_answer(REAL, "reviewer", None)
    with pytest.raises(NotImplementedError):
        judge_answer(REAL, "verifier", None)


def test_judge_rejects_reviewer_verifier():
    """reviewer/verifier → NotImplementedError（S4：v0 不播种，角色随生产面补播）。"""
    for role in ("reviewer", "verifier"):
        with pytest.raises(NotImplementedError, match="scout/auditor"):
            judge_answer(REAL, role, None)


def test_pass_line_functions_literal():
    """及格线三函数边界逐字：promotion（真题全对 3/3 ∧ 拒雷≥1）/ periodic
    （总分≥PERIODIC_PASS ∧ 真≥2/3 ∧ 雷≥1/2）/ entry_qc_pass。"""
    # promotion_exam_passes：真题全对（3/3）且拒雷 ≥1（§8.2 逐字）
    assert promotion_exam_passes(3, 0) is False
    assert promotion_exam_passes(3, 1) is True
    assert promotion_exam_passes(3, 2) is True
    assert promotion_exam_passes(2, 3) is False        # 漏一道真题即不过
    assert promotion_exam_passes(0, 0) is False
    # periodic：总分 ≥ 0.75 且 真题侧 ≥ 2/3 且 雷题侧 ≥ 1/2（§8.2 双侧下限）
    exam = ExamParams(PROMO_ITEMS=5, PROMO_REAL=3, PROMO_MIN_REJECT_FP=1,
                      PERIODIC_ITEMS=10, PERIODIC_PASS=0.75, COOLDOWN_DAYS=1)
    assert periodic_exam_passes(8, 1, 12, exam, real_n=8, fp_n=4) is False # 9/12=0.75 但雷 1/4<1/2 ✗
    assert periodic_exam_passes(8, 2, 12, exam, real_n=8, fp_n=4) is True  # 10/12, 1.0, 0.5
    assert periodic_exam_passes(5, 4, 12, exam, real_n=8, fp_n=4) is False # 9/12 但真 5/8<2/3
    assert periodic_exam_passes(6, 2, 12, exam, real_n=8, fp_n=4) is False # 8/12 < 0.75
    assert periodic_exam_passes(6, 3, 12, exam, real_n=8, fp_n=4) is True  # 9/12, 0.75, 0.75
    with pytest.raises(ValueError):
        periodic_exam_passes(1, 1, 3, exam, real_n=2, fp_n=2)              # 配比不一致
    # entry_qc_pass：accuracy ≥ min_pass（边界含等号）
    assert entry_qc_pass(0.8, 0.8) is True
    assert entry_qc_pass(0.79, 0.8) is False
    assert entry_qc_pass(1.0, 0.0) is True


def test_exam_math_pure_move_reexport_identity():
    """C-3.1 判据 2 的测试形态：kernel.exam_math 与 simulator.exam 的
    centered_exam_weight 为同一函数对象（纯移动 + re-export 单一实现，S5）。"""
    from qswarm.kernel.exam_math import centered_exam_weight as from_kernel
    from qswarm.simulator.exam import centered_exam_weight as from_simulator

    assert from_kernel is from_simulator
    assert from_kernel(0.875) == (1, 0.2 + 0.3 * 0.375 / 0.5)
