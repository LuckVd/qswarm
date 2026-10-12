"""端到端冒烟（重验轮）：入学播种 → 晋升暗考失败 → 冷却 → 期满重考（换题）。

真实 AgentPool + KernelLedger + ExamCenter（非桩件），验证 exam 模块在真参数
（params.yaml 定稿：COOLDOWN_DAYS=7、T_HALF_DAYS_EXAM=15）下的全链路行为。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from qswarm.contracts import DepositOrigin, ExamPurpose, LedgerKind, Role, load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.simulator import (
    AgentPool,
    ExamCenter,
    seed_entry_exams,
)

P = load_params()
DB = Path(__file__).parent / "smoke-ledger.db"
DB.unlink(missing_ok=True)

# ── ① 入学播种（真实池：roster 6/5/4/3 = 18 只 × 6 家族）──────────
pool = AgentPool.from_params(P, seed=2027)
with KernelLedger(DB, clock=FixedClock("2030-01-01T00:00:00Z")) as led:
    outcomes = seed_entry_exams(pool, led, seed=2027, params=P, run_id="smoke-r2")
    deps = led.read(kinds=[LedgerKind.DEPOSIT])
    assert len(deps) == len(outcomes) == 108, (len(deps), len(outcomes))
    assert all(e.payload["origin"] == DepositOrigin.EXAM.value for e in deps)
    assert all(0.2 <= o.weight <= 0.5 for o in outcomes)
    acc = sum(o.accuracy for o in outcomes) / len(outcomes)
    print(f"[①] 播种 108/108 格 origin=exam，权重域全合规，平均正确率={acc:.3f}")

    # ── ② 暗考失败 → 冷却 → 期满重考（换题）──────────────────────
    # 选一只中等能力 agent 的家族格：结局随种子波动，逐日推进直至及格（≤6 轮）。
    agent = pool.agents[0]
    family = next(iter(agent.family_q))
    center = ExamCenter(pool, led, seed=2027, params=P, run_id="smoke-r2")
    day, history, passed = 0, [], None
    while day <= 6 * P.exam.COOLDOWN_DAYS:
        out = center.run_promotion_exam(agent.agent_id, agent.role, family, day=day)
        assert out is not None, "非冷却期必须触发考试"
        cd_block = center.in_cooldown(agent.agent_id, agent.role, family, day=day)
        history.append((day, out.attempt, out.real_correct, out.fp_rejected,
                        out.passed, center.cooldown_until(agent.agent_id, agent.role, family)))
        if out.passed:
            passed = out
            break
        # 冷却期内同格拒绝再触发（逐日断言）
        blocked_day = out.day + 1
        assert center.in_cooldown(agent.agent_id, agent.role, family, day=blocked_day)
        assert center.run_promotion_exam(
            agent.agent_id, agent.role, family, day=blocked_day) is None
        day += P.exam.COOLDOWN_DAYS                  # 跳到冷却期满重考
    assert passed is not None, "6 轮重考内必及格（p_item≈q>0.5 的 agent）"
    print(f"[②] {agent.agent_id}/{agent.role.value}/{family} q={passed.q:.2f}：")
    for d, at, rc, fr, ok, cu in history:
        tag = "及格→active" if ok else f"不过→shadow+冷却至 d{cu}"
        print(f"     day={d:>2} attempt={at} 真题对={rc}/3 拒雷={fr}/2 → {tag}")

    # ── ③ 账本事件面：exam_result 逐考一条、promotion 仅及格一条 ──
    exams = led.read(kinds=[LedgerKind.EXAM_RESULT])
    promos = led.read(kinds=[LedgerKind.PROMOTION])
    assert len(exams) == len(history)
    assert all(e.payload["purpose"] == ExamPurpose.PROMOTION.value for e in exams)
    assert [e.payload["correct"] for e in exams] == [h[4] for h in history]
    assert len(promos) == 1
    assert promos[0].payload["to_status"] == "active"
    assert promos[0].payload["exam_id"] == passed.exam_id
    # 暗考不写存款：全程 DEPOSIT 仍只有 108 笔播种
    assert len(led.read(kinds=[LedgerKind.DEPOSIT])) == 108
    print(f"[③] exam_result×{len(exams)}、promotion×{len(promos)}、"
          f"存款恒 108（暗考零存款）—— 账本事件面全合规")

print("SMOKE OK")
