"""exams.dark：暗考回调工厂（SPEC-9 §7/G5；D35 批准面开启，默认关）。

promote_scan 的 ``exam`` 回调接线件（M3-7 预留点的 M5 落位）：candidate 格夜批
传唤暗考——复用 ``run_entry_cell``（出卷/调用/判分/行落库既有面）+ 
``promotion_exam_passes``（§8.2 及格线纯函数），薄适配为 ExamRunner 协议形态。

纪律：
- **回调只考试不写格**（ExamRunner 协议逐字）：candidate→active 的 promotion
  事件由 promote_scan 落（seq_promotion=None）；不过的 candidate→shadow 事件
  同样由 promote_scan 落；
- **考试故障不误杀**：基建/供给故障 → 返回 None（格子保持 candidate，下一夜批
  再传唤）——暗考是闸门不是处刑，fail-open 只对「考不了」，判分不及格照杀；
- 存款面：run_entry_cell 的 deposit 幂等（格键查重）使暗考不重复写考试存款
  （已播种格 deposit_exists 命中跳过）——暗考测量不污染口碑（§8.2 防污染）；
- 开关：params.exam.DARK_EXAM_ENABLED（默认关）∧ run_scan 注入 bank 才生效
  ——「开启归 D35 批准面」的机器化落点（放行行 62b9ed3）。
"""

from __future__ import annotations

from dataclasses import dataclass

from qswarm.contracts import AgentStatus, load_params
from qswarm.exams.judge import promotion_exam_passes
from qswarm.exams.runner import run_entry_cell
from qswarm.kernel.ledger import KernelLedger

__all__ = ["DarkExamOutcome", "make_dark_exam"]


@dataclass(frozen=True)
class DarkExamOutcome:
    """ExamRunner 协议的结果形态（promote_scan 读 .passed/.exam_id/.score）。"""

    passed: bool
    exam_id: str
    score: float | None = None
    seq_promotion: int | None = None       # None → promotion 事件由 promote_scan 落


def _model_ref_of(ledger: KernelLedger, agent_id: str) -> str:
    row = ledger.conn.execute(
        "SELECT model_ref FROM agents WHERE agent_id = ? AND status = ?",
        (agent_id, AgentStatus.ACTIVE.value),
    ).fetchone()
    return str(row[0]) if row is not None else ""


def make_dark_exam(
    ledger: KernelLedger,
    *,
    bank,
    adapter,
    seed: int,
    cache_dir=None,
    store_dir=None,
    params=None,
    resolve_text=None,
):
    """构造 promote_scan(exam=…) 回调（ExamRunner 协议形态）。

    ``node`` 取家族段（paper 出卷按家族，§8.2 暗考面）；考试故障（bank 无该族
    题目/供给失败/基建失败）→ None（格子保持 candidate，如实不判）。"""
    p = params if params is not None else load_params()

    def _exam(*, agent_id: str, role: str, node: str, from_status) -> DarkExamOutcome | None:
        _ = from_status
        family = str(node).partition("/")[0]
        model_ref = _model_ref_of(ledger, agent_id)
        if not model_ref:
            return None
        try:
            outcome = run_entry_cell(
                ledger, bank,
                agent_id=agent_id, role=role, model_ref=model_ref,
                family=family, adapter=adapter, seed=int(seed),
                cache_dir=cache_dir, store_dir=store_dir,
                resolve_text=resolve_text,
            )
        except Exception:                     # noqa: BLE001 — 考试故障不误杀（fail-open）
            return None
        if outcome.infra_invalid:
            return None
        passed = promotion_exam_passes(
            outcome.real_correct, outcome.fp_rejected,
            real_items=int(p.exam.PROMO_REAL),
            min_reject_fp=int(p.exam.PROMO_MIN_REJECT_FP),
        )
        return DarkExamOutcome(
            passed=bool(passed), exam_id=str(outcome.exam_id),
            score=outcome.accuracy,
        )

    return _exam
