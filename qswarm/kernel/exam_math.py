"""kernel.exam_math：考试侧数学核心（SPEC-6 §0.2/S5：自 simulator.exam 纯移动上移）。

单一实现纪律（SPEC-4 §0「仿真复用内核实现」）的考试侧兑现：真实播种
（qswarm.exams）与仿真播种（simulator.exam）必须消费同一份权重映射。本模块为
**纯移动**——函数体逐字节不变（M0 轨迹零影响，S5 三关验证：全量 pytest /
黄金 digest / 单轮位级复跑）；``simulator.exam`` 改为 re-export 保 import 面兼容。
"""

from __future__ import annotations

__all__ = ["centered_exam_weight"]


def centered_exam_weight(
    accuracy: float, *, w_low: float = 0.2, w_high: float = 0.5
) -> tuple[int, float]:
    """实考正确率 a → (direction, |weight|)：中心化映射到 ±[w_low, w_high]。

    中心 c = a − 0.5（随机猜基线）；幅值 = w_low + (w_high − w_low)·|c|/0.5，
    恒落在 [w_low, w_high]（§8.4：考试存款总权重刻意压低、等效 S2 档封顶）。
    平票 a = 0.5 归正向（确定式 tie 规则，保证可复现）。
    """
    if not 0.0 <= accuracy <= 1.0:
        raise ValueError(f"正确率必须 ∈ [0,1]：{accuracy}")
    c = accuracy - 0.5
    magnitude = w_low + (w_high - w_low) * abs(c) / 0.5
    return (1 if c >= 0 else -1), magnitude
