"""exams.judge：逐角色判分纯函数 + 及格线函数（SPEC-6 §3.3；SPEC-7 §4.2 M3 扩展）。

判分 = 考官答案对齐（§8.2）：expected 才是判分依据（S1 决策注——expected="real"
的题 planted 可真可假）。M2 播种覆盖 scout/auditor（S4）；M3 补播 reviewer/
verifier（SPEC-7 §4：纸面考试——reviewer 卷 = 快照 + 预置 finding，verifier 卷 =
预置簇证据链、无沙箱执行 D29）。题面不给 family/type（S4）：判分 = 产出家族
对齐题库答案（信任自由分析，不退化为复述假设）。

injection_flag 不参与判分（记录进播种报告——注入行为是观测面，不是考项）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qswarm.contracts import ExamParams

if TYPE_CHECKING:  # 仅注解用
    from qswarm.contracts import (
        AuditorFinding,
        ReviewerVote,
        Role,
        ScoutReport,
        VerifierReport,
    )

    from .bank import SeedItem

__all__ = [
    "entry_qc_pass",
    "judge_answer",
    "periodic_exam_passes",
    "promotion_exam_passes",
]


def judge_answer(
    item: "SeedItem",
    role: "Role | str",
    parsed: "ScoutReport | AuditorFinding | ReviewerVote | VerifierReport | None",
) -> bool:
    """单题判对与否（§8.2 考官答案对齐；M3 起四角色全覆盖）。

    - **scout**：parsed=None（信封 declined 或 output 缺失）= 未标记任何候选，
      按 suspicious=False / guess_family=None 判定（real → 判错；fp → 判对，
      对埋雷拒报是正确行为）；产出时：
      real 判对 = suspicious=True ∧ guess_family == item.taxonomy_path[0]；
      fp 判对 = suspicious=False 或 guess_family 为 None（未猜出家族=没有可路由
      猜测，与拒报同效）；injection_flag 不参与判分（记录进播种报告）；
    - **auditor**：real 判对 = 产出 finding ∧ family == item.taxonomy_path[0]
      （type 不判——家族级播种）；fp 判对 = declined（output=null）；
      产出但家族错 → 判错；产出多题任一 family 命中不放宽（信封单 finding 形态）；
    - **reviewer**（SPEC-7 §4.2，M3 补播）：parsed=None = 判错（评审蜂必须表态）；
      real 判对 = direction == +1（ENDORSE——送审 finding 立得住）；fp 判对 =
      direction == -1（REJECT——送审 finding 不成立）；direction == 0（证据不足）
      双侧均判错（表态弃权 ≠ 判对，§8.1 淘汰机制同根基）；「多维意见中 family
      维与答案键家族对齐（与 auditor 同口径）」由卷面构造侧承载——real 面预置
      finding 声称家族 ≡ 答案键家族（构造即对齐，见 runner._exam_claim）；
      ReviewerVote 输出契约无 family 字段（词表不改，SPEC-7 §4.3），判分不读
      模型侧家族（DECISIONS M3 实现期行）；
    - **verifier**（SPEC-7 §4.2，D29 纸面考）：parsed=None = 判错（验证蜂考试
      不含弃权）；real 簇判对 = outcome == verified_real（CONFIRMED）；fp 簇判对
      = outcome == verified_fp（REFUTED）；verify_error（INCONCLUSIVE）恒判错
      ——区分能力与弃权是 §8.1 淘汰机制的根基（逐字）。
    """
    role_key = str(getattr(role, "value", role))
    if role_key == "scout":
        if parsed is None:
            return item.expected == "fp"
        if item.expected == "real":
            return bool(parsed.suspicious) and parsed.guess_family is not None and (
                str(parsed.guess_family) == item.taxonomy_path[0]
            )
        return (not parsed.suspicious) or parsed.guess_family is None
    if role_key == "auditor":
        if item.expected == "real":
            return parsed is not None and str(parsed.family) == item.taxonomy_path[0]
        return parsed is None
    if role_key == "reviewer":
        if parsed is None:
            return False
        if item.expected == "real":
            return parsed.direction == 1
        return parsed.direction == -1
    if role_key == "verifier":
        if parsed is None:
            return False
        if item.expected == "real":
            return str(parsed.outcome) == "verified_real"
        return str(parsed.outcome) == "verified_fp"
    raise NotImplementedError(
        f"判分角色越词表：role={role_key!r}（四考试角色 scout/auditor/reviewer/"
        "verifier；queen_advisor 不参考，SPEC-3 §6）"
    )


def promotion_exam_passes(
    real_correct: int, fp_rejected: int, *, real_items: int = 3, min_reject_fp: int = 1
) -> bool:
    """晋升暗考及格线（§8.2 逐字：真题全对 且 拒雷 ≥1；M2 考试面字面形）。

    params 驱动的仿真变体（PROMO_REAL/PROMO_MIN_REJECT_FP 取 params.yaml）留在
    ``simulator.exam.promotion_exam_passes``（M0 定稿，黄金已锚）；M3 上移 kernel
    时与本函数合一（S5 范围注）。"""
    return real_correct >= real_items and fp_rejected >= min_reject_fp


def periodic_exam_passes(
    real_correct: int,
    fp_rejected: int,
    total: int,
    params: ExamParams,
    *,
    real_n: int,
    fp_n: int,
) -> bool:
    """周期复核考及格线（§8.2 双侧下限逐字）：总分 ≥ PERIODIC_PASS（0.75）且
    真题侧 ≥ 2/3 且雷题侧 ≥ 1/2。

    ``real_n``/``fp_n`` 为卷面真题/雷题数（出卷侧已知；total = real_n + fp_n，不
    一致 → ValueError）——SPEC 签名 ``(real_correct, fp_rejected, total, params)``
    未携带卷面真/雷配比，故以关键字实参补足（滚动协议 2，M2 实现期拍板）。执行随
    M3 周期考通道接线；本函数纯判分、M2 即定稿。"""
    if real_n + fp_n != total:
        raise ValueError(
            f"卷面配比不一致：real_n({real_n}) + fp_n({fp_n}) ≠ total({total})"
        )
    if total <= 0:
        raise ValueError(f"卷面总题数必须 > 0：{total}")
    score = (real_correct + fp_rejected) / total
    real_rate = real_correct / real_n if real_n else 0.0
    fp_rate = fp_rejected / fp_n if fp_n else 0.0
    return (
        score >= params.PERIODIC_PASS
        and real_rate >= 2.0 / 3.0
        and fp_rate >= 1.0 / 2.0
    )


def entry_qc_pass(accuracy: float, min_pass: float) -> bool:
    """供给质检线（§4.5：agent 全家族真题总正确率 ≥ entry_exams.min_pass）。

    质检线是入编闸门，不是播种开关：无论 QC 结果，全部有效格的考试存款照写
    （路由供血先于入编裁决，S11）。"""
    return accuracy >= min_pass
