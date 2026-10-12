"""simulator.exam：入学考试播种 + 晋升暗考（SPEC-4 §1.5、设计 §8.2/§8.4/§7.6 转换②）。

两件机制，一条考试语义：

① 入学播种（§8.4）：生产流量进入前，每 agent × 每家族考 ``entry_items=8`` 题，
   单题判对概率由真实能力 q_f **中心化**映射（锚点 0.5 = 二项题随机猜基线，见
   `exam_item_pass_p`）；实考正确率 a 再中心化映射为考试存款权重——幅值恒落在
   ±[w_low, w_high] = [0.2, 0.5]、高于基线 direction=+1 / 低于 −1（符号存
   ``direction``、幅值存 ``weight``，与 §6.5 verifier 冲突系数「幅值入库、符号由
   机制施加」的惯例一致，见 `centered_exam_weight`）。存款按 (agent, role,
   node=family) 格写入，``origin=exam``、``signal_tier=S1``——S1 档只进 n_eff 总量
   与路由 C*，不计晋升判据的 n_eff(S2+S3) 硬门槛，落实 §8.4「考试存款只影响路由，
   绝不进入淘汰判据」。整场播种受 ``seeding`` 开关控制（SPEC-4 §1.5：对照组整体
   关闭，测冷启动差异）；开关属仿真世界旋钮（`ExamWorldParams`，同 TaskStreamParams
   先例，不进 params.yaml 机制参数）。

② 晋升暗考（§8.2/§7.6 转换②）：promote_scan 把格子推到 candidate 后调用本模块
   `ExamCenter.run_promotion_exam`——``PROMO_ITEMS=5`` 题（``PROMO_REAL=3`` 真 +
   2 雷），单题判对概率同①由格上真实能力 q 映射；及格 = 判对真题数 ≥ PROMO_REAL
   （构造上即真题全对）**且** 拒雷数 ≥ PROMO_MIN_REJECT_FP（「不能漏真洞」的价值
   取向直接编码进规则，见 `promotion_exam_passes`）。过 → to_status=active 并落
   promotion 账本事件；不过 → to_status=shadow 并进入 ``exam.COOLDOWN_DAYS`` 冷却
   （params.yaml exam 节，定稿 7 天：14→7 见 DECISIONS 2026-10-09），
   冷却期内同格拒绝再触发（§7.6「无在途考试」判据；回调内二次防御）。考试结果一律
   落 exam_result 账本事件；暗考表现**不写存款**（§8.2：考试表现不进路由格子）——
   §8.4 播种是双轨制的明示例外（origin=exam 入格子），暗考不是。

随机性：全部从注入主种子派生命名子流（rng.derive_seed，同 world/taskgen 纪律）——
入学考按 ``(seed, "exam", "entry", agent, family)`` 逐格派生（池组成变化不移动已有
格的取值序列）；暗考按 ``(seed, "exam", "promo", agent, role, node, attempt)`` 派生，
attempt 入派生路径使冷却后重考自然换题（§8.2「冷却期后换题重考」）且跨进程可复现。

挥发：本模块**不实现**存款折算——SPEC-1 §4.3「T_half 减半由聚合器处理」；聚合器
（kernel.cells）对 origin=exam 采用 ``params.decay.T_HALF_DAYS_EXAM=15d``（与 prod
T_HALF_DAYS=30d 同一配置源）。落库载荷里的 ``origin="exam"`` 即聚合器选择半衰期的
开关信号；权重折算公式 w_eff = w·2^(−age/T_half) 的黄金用例见 tests/test_sim_exam.py
（与 kernel.cells 用例共用同一公式与参数源）。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

# centered_exam_weight 纯移动上移 kernel/exam_math.py（SPEC-6 §0.2/S5，M2）：
# 真实播种与仿真播种消费同一份权重映射；此处 re-export 保 import 面兼容
# （simulator.exam.centered_exam_weight 与 simulator 包导出面不变）。
from ..kernel.exam_math import centered_exam_weight

from qswarm.contracts import (
    CellStatus,
    DepositOrigin,
    ExamDepositPayload,
    ExamParams,
    ExamPurpose,
    ExamResultPayload,
    LedgerKind,
    ParamsConfig,
    PromotionPayload,
    Role,
    SignalTier,
    load_params,
    load_taxonomy,
)

from .rng import derive_seed
from .world import AgentPool

# ── 配置（仿真世界旋钮，非机制参数）─────────────────────────────


@dataclass(frozen=True)
class ExamWorldParams:
    """考试仿真旋钮（M0 定稿对象；机制面参数一律走 params.yaml 的 exam/decay 节）。

    - ``seeding``        ：入学播种总开关（SPEC-4 §1.5 对照组整体关闭）；
    - ``entry_items``    ：每 agent 每家族入学考题数（§8.4「8–12 题」取下限 8）；
    - ``weight_low/high``：正确率中心化映射的权重幅值域 [0.2, 0.5]；§3 实验矩阵的
      「exam 权重上限 ∈ {0.3, 0.5}」扫 ``weight_high``；
    - ``p_slope``        ：单题判对概率映射斜率（1.0 = p 随 q 等比偏离 0.5 基线；
      <1 表示考试发挥比生产保守，供敏感性实验）；
    - ``p_clip``         ：概率域截断 (lo, hi)，保证任何 q 下单题判对概率在 (0,1)。
    """

    seeding: bool = True
    entry_items: int = 12
    weight_low: float = 0.2
    weight_high: float = 0.5
    p_slope: float = 1.0
    p_clip: tuple[float, float] = (0.05, 0.95)
    # 类型级孵化 quiz 题数（v2 通道；exam.TYPE_ENTRY=0 时不出卷不消耗随机数）。
    # §8.4 入学考只覆盖家族级；类型节点提名需要类型级测量（q_f + ε_t）。
    # 缺省 56：席位选择的保真度随题量上升（二项测量噪声 σ∝1/√n），M0 定稿
    # 网格实测（results/TUNING_LOG.md Round 41-49）56 为 ①② 联合可达的最小档。
    type_quiz_items: int = 56

    def __post_init__(self) -> None:
        if self.entry_items < 1:
            raise ValueError(f"entry_items 必须 ≥ 1：{self.entry_items}")
        if self.type_quiz_items < 1:
            raise ValueError(f"type_quiz_items 必须 ≥ 1：{self.type_quiz_items}")
        if not 0.0 <= self.weight_low <= self.weight_high <= 1.0:
            raise ValueError(
                f"权重幅值域必须 0 ≤ low ≤ high ≤ 1：[{self.weight_low}, {self.weight_high}]"
            )
        if self.p_slope < 0:
            raise ValueError(f"p_slope 必须非负：{self.p_slope}")
        lo, hi = self.p_clip
        if not 0.0 <= lo <= hi <= 1.0:
            raise ValueError(f"p_clip 必须 0 ≤ lo ≤ hi ≤ 1：{self.p_clip}")


# ── 纯映射（机制侧可单测的数学核心）─────────────────────────────


def exam_item_pass_p(q: float, *, slope: float = 1.0, clip: tuple[float, float] = (0.05, 0.95)) -> float:
    """真实能力 q → 单题判对概率（中心化映射）。

    p = clip(0.5 + slope·(q − 0.5), lo, hi)。锚点 0.5 是二项题随机猜基线：q 恰在
    基线的 agent 考试期望正确率就是猜对率；slope=1 时 p=q（截断到概率域）。
    """
    lo, hi = clip
    p = 0.5 + slope * (q - 0.5)
    return min(hi, max(lo, p))


def promotion_exam_passes(real_correct: int, fp_rejected: int, exam: ExamParams) -> bool:
    """晋升暗考及格规则（§8.2 定稿：真题全对 且 雷题至少拒 PROMO_MIN_REJECT_FP 道）。

    - 判对真题数 ≥ PROMO_REAL：考卷构造上真题恰为 PROMO_REAL 道，「≥ PROMO_REAL」
      即「真题全对」——漏任何一道真洞都不过；
    - 拒雷数 ≥ PROMO_MIN_REJECT_FP：至少识破一道埋雷假漏洞。
    """
    return real_correct >= exam.PROMO_REAL and fp_rejected >= exam.PROMO_MIN_REJECT_FP


# ── ① 入学考试播种（§8.4）──────────────────────────────────────


@dataclass(frozen=True)
class EntryExamOutcome:
    """一场入学考的完整记录（度量侧可用：播种权重 vs 真实 q 的相关性等）。"""

    exam_id: str
    agent_id: str
    role: Role
    family: str
    q_f: float                     # 世界真值（机制不可见，仅此处留档供度量）
    p_item: float                  # 单题判对概率
    answers: tuple[bool, ...]      # 逐题判对与否（长度 = entry_items）
    accuracy: float                # 正确率 = 判对数 / entry_items
    direction: int                 # ±1：正确率中心化后的符号
    weight: float                  # 存款幅值 ∈ [weight_low, weight_high]
    seq: int                       # 落库 seq（kind=deposit）


def seat_selection(
    scored: Sequence[tuple[float, str]],
    *,
    top_frac: float,
    bar: float,
    margin: int,
    items: int,
) -> list[str]:
    """席位化提名选择（§8.4 v2 通道的纯函数核心，逐条可单测）。

    - ``scored``：(正确数, agent_id) 序列（选择只看正确数与 id 平手，确定性）；
    - 席位 k = max(1, ⌈top_frac·n⌉)（与 §2 度量 top-20% 口径同式）；
    - 门槛：入选者最低正确数 ≥ bar·items（bar 语义同 ENTRY_DIRECT/TYPE_ENTRY）；
    - 边际：入选最低正确数 − 未入选最高正确数 ≥ margin（0 = 关闭）；
    - 任一条件不满足 → 整席空（宁缺毋滥，不递补）。
    """
    if top_frac <= 0.0 or not scored:
        return []
    ranked = sorted(scored, key=lambda t: (-t[0], t[1]))
    k = max(1, math.ceil(top_frac * len(ranked)))
    sel = ranked[:k]
    rest = ranked[k:]
    if min(c for c, _ in sel) < bar * items:
        return []
    if rest and margin > 0 and (sel[-1][0] - rest[0][0]) < margin:
        return []
    return [a for _, a in sel]


def seed_entry_exams(
    pool: AgentPool,
    ledger,
    *,
    seed: int,
    params: ParamsConfig | None = None,
    config: ExamWorldParams | None = None,
    run_id: str | None = None,
) -> list[EntryExamOutcome]:
    """入学播种（SPEC-4 §1.5、§8.4）：仿真开始前对每 agent 每家族模拟考并写考试存款。

    - ``seeding=False``（对照组）：一个事件都不写、一个随机数都不消耗，返回 []；
    - 每格 (agent, family) 恰一笔 origin=exam 存款：幅值 = 正确率中心化映射到
      [w_low, w_high]，符号存 direction；signal_tier=S1（只影响路由）；
    - **入学直晋（M0 定稿，exam.ENTRY_DIRECT > 0 时启用，DECISIONS 2026-10-09）**：
      正确率 ≥ ENTRY_DIRECT 且 direction=+1 的格，在播种时直转 candidate——落
      promotion 事件（shadow→candidate，exam_id=``entry-fast:…``）；随后的夜批
      promote_scan 对存量 candidate 收官暗考（§8.2 及格线不动）→ active。
      这是 §8.4 播种通道的孵化侧延伸：入学高分即孵化证据，替代在 30 日地平线内
      结构性不可达的生产证据门槛（n_eff/流量/暗考延迟），暗考闸门保持质量兜底；
    - **直晋 v2（M0 定稿，exam.ENTRY_TOP_FRAC > 0 时启用，DECISIONS 2026-10-09
      v2 条）**：提名席位从「K=1 唯一 ace」扩展为每 (role, family) 按正确率排名的
      前 ⌈frac·n⌉ 席（:func:`seat_selection`；门槛/边际不变语义）；exam.TYPE_ENTRY
      > 0 再开类型级 quiz（世界侧 type_quiz_items 题测 q_t），同席位规则提名
      (role, family/type) 节点——§2 度量的 top-20% 格子宇宙从此有孵化通道覆盖。
      candidate→active 的收官（宽限/生产佐证/暗考兜底）在 kernel.promote_scan；
    - 家族遍历序 = agent.family_q 的插入序 = taxonomy.yaml 顺序（world.py 保证），
      事件 seq 顺序确定；随机源逐格派生自主种子（见模块 docstring）；v2 新增
      "entry-type" 子流与既有子流正交（TYPE_ENTRY=0 时零消耗）。
    """
    cfg = config or ExamWorldParams()
    if not cfg.seeding:
        return []
    p = params or load_params()
    entry_direct = float(p.exam.ENTRY_DIRECT)
    top_frac = float(p.exam.ENTRY_TOP_FRAC)
    margin = int(p.exam.ENTRY_MARGIN)
    type_entry = float(p.exam.TYPE_ENTRY)
    type_items = int(cfg.type_quiz_items)

    # 播种读的机制参数是 exam 节的直晋旋钮；题数/权重域属仿真世界旋钮。
    outcomes: list[EntryExamOutcome] = []
    best_ace: dict[tuple[str, str], tuple[float, str]] = {}
    family_scores: dict[tuple[str, str], list[tuple[float, str, int]]] = {}
    for agent in pool.agents:
        for family in agent.family_q:                       # 插入序 = taxonomy 序
            q_f = agent.capability(family, t=0, hidden=pool.hidden_params)
            p_item = exam_item_pass_p(q_f, slope=cfg.p_slope, clip=cfg.p_clip)
            rng = random.Random(derive_seed(seed, "exam", "entry", agent.agent_id, family))
            answers = tuple(rng.random() < p_item for _ in range(cfg.entry_items))
            accuracy = sum(answers) / cfg.entry_items
            direction, magnitude = centered_exam_weight(
                accuracy, w_low=cfg.weight_low, w_high=cfg.weight_high
            )
            exam_id = f"entry:{agent.agent_id}:{family}"
            event = ledger.append(
                LedgerKind.DEPOSIT,
                ExamDepositPayload(
                    finding=None,
                    cluster=None,
                    agent=agent.agent_id,
                    role=agent.role,
                    taxonomy_path=[family],
                    signal_tier=SignalTier.S1,
                    direction=direction,
                    weight=magnitude,
                    origin=DepositOrigin.EXAM,
                    exam_id=exam_id,
                ),
                run_id=run_id,
            )
            if entry_direct > 0.0:
                if top_frac <= 0.0:
                    # ── 旧口径（K=1 唯一 ace，DECISIONS 2026-10-09）：同 (role, family)
                    # 只保留正确率 ≥ 门槛且方向为正的最高者（平手取 agent_id 字典序）。──
                    if direction == 1 and accuracy >= entry_direct:
                        key = (agent.role.value, family)
                        cur = best_ace.get(key)
                        if cur is None or (-accuracy, agent.agent_id) < (-cur[0], cur[1]):
                            best_ace[key] = (accuracy, agent.agent_id)
                else:
                    # ── v2 席位口径：全部正答者入池，门槛/席位由 seat_selection 统一
                    # 施加（正确数口径；方向条件不设——它只会截短候选池、留下无人
                    # 入席的节点，deposit 的 ±符号语义不受影响）。──
                    family_scores.setdefault(
                        (agent.role.value, family), []
                    ).append((accuracy, agent.agent_id, sum(answers)))
            outcomes.append(
                EntryExamOutcome(
                    exam_id=exam_id,
                    agent_id=agent.agent_id,
                    role=agent.role,
                    family=family,
                    q_f=q_f,
                    p_item=p_item,
                    answers=answers,
                    accuracy=accuracy,
                    direction=direction,
                    weight=magnitude,
                    seq=event.seq,
                )
            )

    # ── 类型级孵化 quiz（exam.TYPE_ENTRY > 0 时启用；§8.4 播种通道的类型级延伸）──
    # 提名考试语义：quiz 只产生提名依据，**不落存款**——§8.2「考试表现不进路由
    # 格子」的防污染纪律（36 agent × 18 节点 × ±[0.2,0.5] 的低权重考试存款会淹没
    # C* 与 LOO 基线中的生产信号；§8.4 的家族级入学存款是规格明文，照写不误）。
    type_scores: dict[tuple[str, str], list[tuple[float, str, int]]] = {}
    if type_entry > 0.0:
        tax_types = {
            f: list(w.types) for f, w in load_taxonomy().families.items()
        }
        for agent in pool.agents:
            for family in agent.family_q:                   # 插入序 = taxonomy 序
                for type_ in tax_types.get(family, ()):
                    q_t = agent.capability(
                        family, type_, t=0, hidden=pool.hidden_params
                    )
                    p_item = exam_item_pass_p(q_t, slope=cfg.p_slope, clip=cfg.p_clip)
                    rng = random.Random(
                        derive_seed(
                            seed, "exam", "entry-type", agent.agent_id, family, type_
                        )
                    )
                    answers = tuple(rng.random() < p_item for _ in range(type_items))
                    accuracy = sum(answers) / type_items
                    node = f"{family}/{type_}"
                    type_scores.setdefault(
                        (agent.role.value, node), []
                    ).append((accuracy, agent.agent_id, sum(answers)))

    # 类型席位合成分：家族入学分（q_f 的独立测量）按 w 权重混入类型分，
    # 降低席位选择的测量噪声（exam.TYPE_ENTRY_COMBINE，0 = 纯 quiz 分）。
    w_comb = float(p.exam.TYPE_ENTRY_COMBINE)
    if type_entry > 0.0 and w_comb > 0.0:
        for (role_value, node), scored in list(type_scores.items()):
            fam = node.partition("/")[0]
            fam_acc = {
                aid: acc
                for acc, aid, _c in family_scores.get((role_value, fam), [])
            }
            rescored = []
            for accuracy, agent_id, _c in scored:
                base = fam_acc.get(agent_id)
                if base is None:
                    rescored.append((accuracy, agent_id,
                                     round(accuracy * type_items)))
                    continue
                blended = (1 - w_comb) * accuracy + w_comb * base
                rescored.append((blended, agent_id, round(blended * type_items)))
            type_scores[(role_value, node)] = rescored

    if entry_direct > 0.0 or type_entry > 0.0:
        # 直晋提名：shadow→candidate（暗考/佐证收官见 kernel.promote_scan 转换②）。
        # 事件在全部播种存款之后按 (role, node, agent) 字典序追加——确定性序。
        noms: list[tuple[str, str, str]] = []               # (role, node, agent)
        if entry_direct > 0.0:
            if top_frac <= 0.0:
                noms.extend(
                    (role_value, family, agent_id)
                    for (role_value, family), (_, agent_id) in sorted(best_ace.items())
                )
            else:
                for (role_value, family), scored in sorted(family_scores.items()):
                    for agent_id in seat_selection(
                        [(corr, a) for _acc, a, corr in scored],
                        top_frac=top_frac, bar=entry_direct,
                        margin=margin, items=cfg.entry_items,
                    ):
                        noms.append((role_value, family, agent_id))
        if type_entry > 0.0:
            for (role_value, node), scored in sorted(type_scores.items()):
                for agent_id in seat_selection(
                    [(corr, a) for _acc, a, corr in scored],
                    top_frac=top_frac, bar=type_entry,
                    margin=margin, items=type_items,
                ):
                    noms.append((role_value, node, agent_id))
        for role_value, node, agent_id in sorted(noms):
            agent = pool.get(agent_id)
            if "/" in node:
                # 类型席位的入格推动存款（§8.4「考试存款入格推动入考」；§7.1 格子
                # 以存款键惰性建格——无存款的类型提名格是「影格」，夜批与度量都
                # 看不见）。家族席位已由 §8.4 入学存款覆盖，不重复。存款载荷反映
                # 该格 quiz 实考（方向/幅值同 §8.4 中心化映射，世界侧可直接复算）。
                fam, _, typ = node.partition("/")
                quiz_acc = next(
                    (acc for acc, aid, _c in type_scores.get((role_value, node), [])
                     if aid == agent_id), 0.5
                )
                d_dir, d_mag = centered_exam_weight(
                    quiz_acc, w_low=cfg.weight_low, w_high=cfg.weight_high
                )
                ledger.append(
                    LedgerKind.DEPOSIT,
                    ExamDepositPayload(
                        finding=None,
                        cluster=None,
                        agent=agent_id,
                        role=agent.role,
                        taxonomy_path=[fam, typ],
                        signal_tier=SignalTier.S1,
                        direction=d_dir,
                        weight=d_mag,
                        origin=DepositOrigin.EXAM,
                        exam_id=f"entry-nom:{agent_id}:{node}",
                    ),
                    run_id=run_id,
                )
            ledger.append(
                LedgerKind.PROMOTION,
                PromotionPayload(
                    agent=agent_id,
                    role=agent.role,
                    node=node,
                    exam_id=f"entry-fast:{agent_id}:{node}",
                    from_status=CellStatus.SHADOW,
                    to_status=CellStatus.CANDIDATE,
                ),
                run_id=run_id,
            )
    return outcomes


# ── ② 晋升暗考（§8.2 / §7.6 转换②的考试回调）───────────────────


@dataclass(frozen=True)
class PromotionExamOutcome:
    """一场晋升暗考的完整记录（含状态机迁移建议，格状态写回归 kernel.promote_scan）。"""

    exam_id: str
    agent_id: str
    role: Role
    node: str
    day: int                       # 仿真日（冷却计时坐标）
    attempt: int                   # 该格第几次暗考（1 起；重考换题的依据）
    q: float                       # 格上世界真值（度量留档）
    p_item: float
    real_answers: tuple[bool, ...]     # 真题判对与否（长度 = PROMO_REAL）
    fp_rejections: tuple[bool, ...]    # 雷题识破与否（长度 = PROMO_ITEMS − PROMO_REAL）
    real_correct: int
    fp_rejected: int
    score: float                   # (判对真题 + 拒雷) / PROMO_ITEMS ∈ [0,1]
    passed: bool
    from_status: CellStatus
    to_status: CellStatus          # 过 → active；不过 → shadow（退回孵化）
    seq_exam_result: int
    seq_promotion: int | None      # 仅及格时非 None（promotion 事件 seq）


class ExamCenter:
    """晋升暗考回调（promote_scan 的 schedule_exam 落点）+ 冷却登记。

    promote_scan（§7.6）把格子推到 candidate 后调用 ``run_promotion_exam``；本类
    只负责考试本身与 exam_result/promotion 账本事件，**不写格状态**——cells 的
    状态写口在 kernel（写口纪律），调用方按 ``outcome.to_status`` 落格。
    """

    def __init__(
        self,
        pool: AgentPool,
        ledger,
        *,
        seed: int,
        params: ParamsConfig | None = None,
        config: ExamWorldParams | None = None,
        run_id: str | None = None,
    ) -> None:
        self.pool = pool
        self.ledger = ledger
        self.seed = seed
        self.params = params or load_params()
        self.config = config or ExamWorldParams()
        self.run_id = run_id
        self._cooldown_until: dict[tuple[str, str, str], int] = {}
        self._attempts: dict[tuple[str, str, str], int] = {}

    # -- 冷却（§7.6「无在途考试」/ §8.2「冷却期内不再触发」）──────

    def _key(self, agent_id: str, role: Role | str, node: str) -> tuple[str, str, str]:
        return (agent_id, Role(role).value, node)

    def in_cooldown(self, agent_id: str, role: Role | str, node: str, *, day: int) -> bool:
        """该格在仿真日 ``day`` 是否处于暗考冷却期（不过 → COOLDOWN_DAYS 天）。"""
        until = self._cooldown_until.get(self._key(agent_id, role, node))
        return until is not None and day < until

    def cooldown_until(self, agent_id: str, role: Role | str, node: str) -> int | None:
        """冷却截止仿真日（含）；无冷却记录返回 None。"""
        return self._cooldown_until.get(self._key(agent_id, role, node))

    def attempts(self, agent_id: str, role: Role | str, node: str) -> int:
        """该格已考次数（0 = 从未考过；冷却后重考 attempt 递增 → 换题）。"""
        return self._attempts.get(self._key(agent_id, role, node), 0)

    # -- 考试回调 ──────────────────────────────────────────────

    def run_promotion_exam(
        self,
        agent_id: str,
        role: Role | str,
        node: str,
        *,
        day: int,
        t: int = 0,
        from_status: CellStatus = CellStatus.CANDIDATE,
    ) -> PromotionExamOutcome | None:
        """对 (agent, role, node) 格执行一场晋升暗考；冷却期内返回 None（不触发）。

        - 题卷：PROMO_ITEMS 题 = PROMO_REAL 道真题 + (ITEMS−REAL) 道埋雷，逐题以
          同一判对概率 p 独立作答（真题答对 = 判真；雷题答对 = 识破拒雷）；
        - 及格 → 落 promotion 事件（from_status → active）；
          不过 → 退回 shadow（to_status=shadow）并登记 COOLDOWN_DAYS 冷却；
        - 两种结局都落 exam_result 事件（correct=是否及格，score=得分率）；
          暗考不写任何存款（§8.2：考试表现不进路由格子）。
        - ``t``：能力快照的世界时钟（退化型漂移坐标），默认 0。
        """
        role_enum = Role(role)
        key = self._key(agent_id, role_enum, node)
        if self.in_cooldown(agent_id, role_enum, node, day=day):
            return None                                   # 冷却期内拒绝触发（二次防御）

        exam = self.params.exam
        attempt = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempt

        family, _, type_ = node.partition("/")
        agent = self.pool.get(agent_id)
        q = agent.capability(
            family, type_ or None, t=t, hidden=self.pool.hidden_params
        )
        p_item = exam_item_pass_p(q, slope=self.config.p_slope, clip=self.config.p_clip)

        rng = random.Random(
            derive_seed(self.seed, "exam", "promo", agent_id, role_enum.value, node, attempt)
        )
        real_answers = tuple(self._draw_item(rng, p_item) for _ in range(exam.PROMO_REAL))
        fp_rejections = tuple(
            self._draw_item(rng, p_item) for _ in range(exam.PROMO_ITEMS - exam.PROMO_REAL)
        )
        real_correct = sum(real_answers)
        fp_rejected = sum(fp_rejections)
        passed = promotion_exam_passes(real_correct, fp_rejected, exam)
        score = (real_correct + fp_rejected) / exam.PROMO_ITEMS

        exam_id = f"promo:{agent_id}:{role_enum.value}:{node}:a{attempt}"
        ev = self.ledger.append(
            LedgerKind.EXAM_RESULT,
            ExamResultPayload(
                exam_id=exam_id,
                purpose=ExamPurpose.PROMOTION,
                agent=agent_id,
                node=node,
                correct=passed,
                score=score,
            ),
            run_id=self.run_id,
        )
        seq_promo: int | None = None
        if passed:
            promo = self.ledger.append(
                LedgerKind.PROMOTION,
                PromotionPayload(
                    agent=agent_id,
                    role=role_enum,
                    node=node,
                    exam_id=exam_id,
                    from_status=from_status,
                    to_status=CellStatus.ACTIVE,
                ),
                run_id=self.run_id,
            )
            seq_promo = promo.seq
        else:
            self._cooldown_until[key] = day + exam.COOLDOWN_DAYS

        return PromotionExamOutcome(
            exam_id=exam_id,
            agent_id=agent_id,
            role=role_enum,
            node=node,
            day=day,
            attempt=attempt,
            q=q,
            p_item=p_item,
            real_answers=real_answers,
            fp_rejections=fp_rejections,
            real_correct=real_correct,
            fp_rejected=fp_rejected,
            score=score,
            passed=passed,
            from_status=from_status,
            to_status=CellStatus.ACTIVE if passed else CellStatus.SHADOW,
            seq_exam_result=ev.seq,
            seq_promotion=seq_promo,
        )

    def _draw_item(self, rng: random.Random, p_item: float) -> bool:
        """单题作答原语（判对/拒雷共用同一能力模型；测试以此缝点注入脚本化作答）。"""
        return rng.random() < p_item


# ── ③ 周期复核考（§8.1 淘汰通道；demote.FC_* 启用，M0 定稿 DECISIONS v2）──────


@dataclass(frozen=True)
class ForCauseExamOutcome:
    """一场周期复核考的完整记录（熄灭判定写回归 kernel.promote_scan）。"""

    exam_id: str
    agent_id: str
    role: Role
    node: str
    day: int
    attempt: int
    q: float
    p_item: float
    correct: int
    items: int
    score: float
    baseline: bool                  # True = 立基线场（不参与不达标判定）
    below: bool                     # score < 基线 − FC_MARGIN（趋势不达标）
    consecutive: int                # 当前连续不达标数（含本场；基线场恒 0）
    passed: bool                    # = not below（回调协议兼容：False 时kernel判熄）
    decayed: bool                   # 连续不达标 ≥ FC_CONSEC → 熄灭
    seq_exam_result: int


class ForCauseExamCenter:
    """周期复核考回调（promote_scan 的 for_cause 落点）+ 趋势基线登记。

    §8.1「淘汰仍只看后续周期考」「连续 2 个考核周期低于及格线」的落地：每
    ``demote.FC_DAYS`` 天对全部 active 格传唤一场复核考；逐题判对概率由真实
    能力 q 映射（与暗考同式），**判据为趋势式**——得分低于该格冻结基线
    （前 ``FC_BASE_EXAMS`` 场均值，首场只立基线）− ``FC_MARGIN`` 记一次不达标，
    连续 ``FC_CONSEC`` 场不达标 → ``decayed=True``，promote_scan 落
    demotion(reason=decay)。考试结果只落 exam_result，不写存款（§8.2 防污染）。

    随机性：子流 ``(seed, "exam", "forcause", agent, role, node, attempt)`` 与
    暗考/入学正交；attempt 按 (格, 场次) 递增，跨进程逐位可复现。
    """

    def __init__(
        self,
        pool: AgentPool,
        ledger,
        *,
        seed: int,
        params: ParamsConfig | None = None,
        config: ExamWorldParams | None = None,
        run_id: str | None = None,
    ) -> None:
        self.pool = pool
        self.ledger = ledger
        self.seed = seed
        self.params = params or load_params()
        self.config = config or ExamWorldParams()
        self.run_id = run_id
        self._hist: dict[tuple[str, str, str], list[float]] = {}
        self._consec: dict[tuple[str, str, str], int] = {}
        self._attempts: dict[tuple[str, str, str], int] = {}

    def attempts(self, agent_id: str, role: Role | str, node: str) -> int:
        return self._attempts.get((agent_id, Role(role).value, node), 0)

    def history(self, agent_id: str, role: Role | str, node: str) -> tuple[float, ...]:
        return tuple(self._hist.get((agent_id, Role(role).value, node), ()))

    def run_for_cause_exam(
        self,
        agent_id: str,
        role: Role | str,
        node: str,
        *,
        day: int,
        t: int = 0,
    ) -> ForCauseExamOutcome | None:
        """复核考入口：非复核夜 / 已熄灭格返回 None（不消耗随机数）。

        周期相位全局同步（``day % FC_DAYS == 0`` 的夜批传唤全部 active 格；
        由 kernel 侧只对 active 格调用）。首场为立基线场：只记录得分、不判。
        """
        fc = self.params.demote
        if int(fc.FC_DAYS) <= 0 or day % int(fc.FC_DAYS) != 0:
            return None
        role_enum = Role(role)
        key = (agent_id, role_enum.value, node)
        attempt = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempt

        family, _, type_ = node.partition("/")
        agent = self.pool.get(agent_id)
        q = agent.capability(family, type_ or None, t=t, hidden=self.pool.hidden_params)
        p_item = exam_item_pass_p(q, slope=self.config.p_slope, clip=self.config.p_clip)
        rng = random.Random(
            derive_seed(self.seed, "exam", "forcause", agent_id, role_enum.value, node, attempt)
        )
        correct = sum(rng.random() < p_item for _ in range(fc.FC_ITEMS))
        score = correct / fc.FC_ITEMS

        hist = self._hist.setdefault(key, [])
        baseline = len(hist) < int(fc.FC_BASE_EXAMS)
        if not baseline:
            ref = sum(hist) / len(hist)
            below = score < ref - float(fc.FC_MARGIN)
        else:
            ref = None
            below = False
        hist.append(score)
        consecutive = 0 if (baseline or not below) else self._consec.get(key, 0) + 1
        self._consec[key] = consecutive
        decayed = consecutive >= int(fc.FC_CONSEC)
        if decayed:
            # 熄灭后格转 shadow（kernel 落账）；复核史清零——若未来重激活，
            # 重新立基线（q 可能已变化，旧基线不再适用）。
            self._hist.pop(key, None)
            self._consec.pop(key, None)

        exam_id = f"forcause:{agent_id}:{role_enum.value}:{node}:a{attempt}"
        ev = self.ledger.append(
            LedgerKind.EXAM_RESULT,
            ExamResultPayload(
                exam_id=exam_id,
                purpose=ExamPurpose.PERIODIC,
                agent=agent_id,
                node=node,
                correct=not below,
                score=score,
            ),
            run_id=self.run_id,
        )
        return ForCauseExamOutcome(
            exam_id=exam_id,
            agent_id=agent_id,
            role=role_enum,
            node=node,
            day=day,
            attempt=attempt,
            q=q,
            p_item=p_item,
            correct=correct,
            items=fc.FC_ITEMS,
            score=score,
            baseline=baseline,
            below=below,
            consecutive=consecutive,
            passed=not below,
            decayed=decayed,
            seq_exam_result=ev.seq,
        )


__all__ = [
    "ExamCenter",
    "ExamWorldParams",
    "EntryExamOutcome",
    "ForCauseExamCenter",
    "ForCauseExamOutcome",
    "PromotionExamOutcome",
    "centered_exam_weight",
    "exam_item_pass_p",
    "promotion_exam_passes",
    "seat_selection",
    "seed_entry_exams",
]
