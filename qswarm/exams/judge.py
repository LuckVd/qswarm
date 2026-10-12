"""exams.judge：逐角色判分纯函数 + 及格线函数（SPEC-6 §3.3）。

判分 = 考官答案对齐（§8.2）：expected 才是判分依据（S1 决策注——expected="real"
的题 planted 可真可假）。v0 播种只覆盖 scout/auditor（S4：reviewer 需 staged
finding 工件面 → M3 随 L2/评审开工补播；verifier 需沙箱 → 依赖序第 8 位；两角色
cells 冷启动走家族级收缩既有机制兜底）。题面不给 family/type（S4）：判分 = 产出
家族对齐题库答案（信任自由分析，不退化为复述假设）。

injection_flag 不参与判分（记录进播种报告——注入行为是观测面，不是考项）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qswarm.contracts import ExamParams

if TYPE_CHECKING:  # 仅注解用
    from qswarm.contracts import AuditorFinding, Role, ScoutReport

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
    parsed: "ScoutReport | AuditorFinding | None",
) -> bool:
    """单题判对与否（§8.2 考官答案对齐；v0 覆盖 scout/auditor 两角色）。

    - **scout**：parsed=None（信封 declined 或 output 缺失）= 未标记任何候选，
      按 suspicious=False / guess_family=None 判定（real → 判错；fp → 判对，
      对埋雷拒报是正确行为）；产出时：
      real 判对 = suspicious=True ∧ guess_family == item.taxonomy_path[0]；
      fp 判对 = suspicious=False 或 guess_family 为 None（未猜出家族=没有可路由
      猜测，与拒报同效）；injection_flag 不参与判分（记录进播种报告）；
    - **auditor**：real 判对 = 产出 finding ∧ family == item.taxonomy_path[0]
      （type 不判——家族级播种）；fp 判对 = declined（output=null）；
      产出但家族错 → 判错；产出多题任一 family 命中不放宽（信封单 finding 形态）；
    - reviewer/verifier：NotImplementedError（S4：v0 不播种——角色随其生产面补播，
      M3 起沿本函数扩展，词表不改）。
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
    raise NotImplementedError(
        f"M2 判分只覆盖 scout/auditor（S4 范围裁剪），role={role_key!r} 随其生产面"
        "在 M3 补播时沿本函数扩展"
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
