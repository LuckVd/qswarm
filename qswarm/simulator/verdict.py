"""仿真世界·三层 verdict 生成（SPEC-4 §1.3；设计 §6.1/§6.2/§6.3/§12/§16.7-1）。

世界侧真值模型（机制不可见，仅 verdict 生成器与度量采集可读）：

- 真实对错：reporter 判对概率 = q(reporter, node)·(1 − 0.3·d)（SPEC-4 §1.3 直译）；
  运气型在早期（t < WorldHiddenParams.t_drift，复用漂移时刻作早期窗口边界）叠加
  +luck_bias（world.GroundTruth.early_positive_bias，§1.1 预留的 verdict 生成入口）。
- S1 投票：panel（params.panel.SIZE，默认 3 票），每票 P(与真同向) = 0.55 + 0.25·reviewer_q；
  同底模相关噪声按 SPEC 直译 z_agent = z_own_w·z_own + z_panel_w·z_panel
  （0.6/0.4；z_own 逐票独立、z_panel 全 panel 共享）。潜变量标准化后
  w = z_agent / sqrt(z_own_w² + z_panel_w²) ~ N(0,1)，票向由 probit 阈值判定
  （w < Φ⁻¹(p) ⇔ 与真同向）——边际 P(与真同向) = p 精确保持；panel 内两票潜变量
  两两相关 = z_panel_w²/(z_own_w² + z_panel_w²) = 0.4²/0.52 = 4/13 ≈ 0.3077
  （SPEC 的 ρ=0.4 以混合权重口径直译，诱导的票间相关由此式给出，测试按此断言）。
- S2：仅 full 档按 30% 触达抽样（价值排序与配额属内核件，M0 直译为档位触达概率）。
  判对分支（finding 真实）：verified_real 概率 = 1 − verify_error（默认 0.2 → 0.8，
  即 SPEC「判对 → 0.8」口径）；判错分支：0.1 错误确认 verified_real（会错标），
  其余按 p_blocking 抽「存在阻断证据」→ verified_fp（§10.4 证据标准），否则
  verify_error。verify_error 零存款（§6.2），且是 S2 饥饿传导扫描旋钮
  {0.2, 0.35, 0.5}（SPEC-4 §3 实验矩阵；旋钮增大 → 判对分支确认率 1−ve 下降）。
- S3：延迟人工裁决。触达率：S2 有结论的 40%、其余（inconclusive：未跑 S2 或
  verify_error）的 80%——设计 §2.3「人只看 S2 后仍不确定或重大的」；方向与真值
  相反的概率 ε3 = 0.05；到达延迟 lag ~ LogNormal(ln 5d, σ=1) 截断 [1d, 30d]
  （拒绝采样），arrival_day = task.day + lag。

随机性：逐 finding 从主种子派生独立子流 substream(seed, "verdict", finding_id)，
与处理顺序、其它 finding 无关；同一 (种子, 输入) 下整条 verdict 流逐位可复现
（SPEC-4 §6：随机种子显式注入，结果可复现）。抽取次序固定：
真值 → z_panel → 逐票 z_own → S2 触发 → S2 结果（分支内）→ S3 触达 → S3 方向 → lag。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from statistics import NormalDist

from qswarm.contracts import ParamsConfig, Role, RunMode, VerifyOutcome, load_params
from qswarm.simulator.rng import substream
from qswarm.simulator.taskgen import WorldTask
from qswarm.simulator.world import AgentPool

_ND = NormalDist()

# ── §6.1 信号源权重：S2/S3 为规格定值；S1 在 [0.05, 0.1] 内由 VerdictParams 定 ──
S2_SOURCE_WEIGHT = 0.5
S3_SOURCE_WEIGHT = 1.0
S1_WEIGHT_MIN = 0.05
S1_WEIGHT_MAX = 0.10

# ── S1 投票参数（SPEC-4 §1.3 直译）──────────────────────────────
VOTE_BASE = 0.55                  # 每票 P(与真同向) = 0.55 + 0.25·reviewer_q
VOTE_Q_GAIN = 0.25
Z_OWN_W = 0.6                     # z_agent = 0.6·z_own + 0.4·z_panel（ρ=0.4 直译）
Z_PANEL_W = 0.4

# ── 真实对错（SPEC-4 §1.3）──────────────────────────────────────
DIFFICULTY_PENALTY = 0.3          # P(判对) = q·(1 − 0.3·d)
P_CORRECT_MIN = 0.01              # 概率域夹取（退化漂移后 q 可为负，防 0/1 退化）
P_CORRECT_MAX = 0.99

# ── S3 到达延迟：LogNormal(ln 5d, σ=1) 截断 [1d, 30d] ───────────
LAG_LN_MU = math.log(5.0)
LAG_LN_SIGMA = 1.0
LAG_MIN_DAYS = 1.0
LAG_MAX_DAYS = 30.0


def vote_agree_probability(reviewer_q: float) -> float:
    """单票与真值同向的设计边际概率（SPEC-4 §1.3：0.55 + 0.25·reviewer_q）。"""
    return VOTE_BASE + VOTE_Q_GAIN * reviewer_q


def latent_correlation(z_own_w: float = Z_OWN_W, z_panel_w: float = Z_PANEL_W) -> float:
    """panel 内两票潜变量（z_agent）的理论两两相关 = z_panel_w²/(z_own_w²+z_panel_w²)。

    默认权重（0.6/0.4）下 = 0.16/0.52 = 4/13 ≈ 0.3077（见模块 docstring 的口径说明）。
    """
    var = z_own_w * z_own_w + z_panel_w * z_panel_w
    if var <= 0:
        raise ValueError("z_own_w 与 z_panel_w 不能同时为 0")
    return z_panel_w * z_panel_w / var


def theoretical_lag_mean() -> float:
    """截断对数正态 lag 的解析均值（测试与 sim_report 对照用，免 scipy）。"""
    z_lo = (math.log(LAG_MIN_DAYS) - LAG_LN_MU) / LAG_LN_SIGMA
    z_hi = (math.log(LAG_MAX_DAYS) - LAG_LN_MU) / LAG_LN_SIGMA
    denom = _ND.cdf(z_hi) - _ND.cdf(z_lo)
    # E[X·1(a≤X≤b)] = exp(μ+σ²/2)·(Φ((ln b−μ−σ²)/σ) − Φ((ln a−μ−σ²)/σ))
    num = math.exp(LAG_LN_MU + 0.5 * LAG_LN_SIGMA**2) * (
        _ND.cdf(z_hi - LAG_LN_SIGMA) - _ND.cdf(z_lo - LAG_LN_SIGMA)
    )
    return num / denom


# ── 世界旋钮（M0 扫描对象；不在 params.yaml，属仿真世界参数）────

@dataclass(frozen=True)
class VerdictParams:
    """verdict 生成旋钮（SPEC-4 §1.3 + §3 扫描维度；缺省 = SPEC 口径值）。"""

    s1_weight: float = S1_WEIGHT_MIN        # S1 源权重，定稿域 [0.05, 0.1]（§6.1）
    z_own_w: float = Z_OWN_W                # 相关噪声：自身项权重
    z_panel_w: float = Z_PANEL_W            # 相关噪声：panel 共享项权重（ρ=0.4）
    s2_reach_full: float = 0.30             # S2 触达率（full 档，§1.3）
    verify_error: float = 0.20              # S2 饥饿旋钮 {0.2, 0.35, 0.5}（§3）
    s2_wrong_confirm: float = 0.10          # 判错分支错误确认 verified_real 概率
    p_blocking: float = 0.50                # 判错且存在阻断证据（verified_fp 前提）
    s3_reach_concluded: float = 0.40        # S3 触达：S2 有结论（§1.3）
    s3_reach_inconclusive: float = 0.80     # S3 触达：inconclusive（未跑或出错）
    s3_error: float = 0.05                  # S3 方向与真值相反的概率（ε3）

    def __post_init__(self) -> None:
        if not (S1_WEIGHT_MIN - 1e-12 <= self.s1_weight <= S1_WEIGHT_MAX + 1e-12):
            raise ValueError(f"s1_weight 必须在 [{S1_WEIGHT_MIN}, {S1_WEIGHT_MAX}]：{self.s1_weight}")
        if self.z_own_w < 0 or self.z_panel_w < 0:
            raise ValueError("z_own_w/z_panel_w 必须非负")
        if self.z_own_w == 0 and self.z_panel_w == 0:
            raise ValueError("z_own_w 与 z_panel_w 不能同时为 0（潜变量无方差）")
        for name in ("s2_reach_full", "s3_reach_concluded", "s3_reach_inconclusive"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} 必须在 [0,1]：{v}")
        if not 0.0 < self.verify_error < 1.0:
            raise ValueError(f"verify_error 必须在 (0,1)：{self.verify_error}")
        for name in ("s2_wrong_confirm", "p_blocking", "s3_error"):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} 必须在 [0,1]：{v}")


# ── 世界侧记录结构（机制不可见字段仅度量可读）───────────────────

@dataclass(frozen=True)
class FindingContext:
    """一个 finding 的世界上下文：谁报告、谁评审、簇里有谁的候选标记。

    - pr/full 档：reporter=审查蜂，panel=评审团（§16.7-6），cluster_scouts=标记
      候选点入簇的侦察蜂，verifier_id=S2 验证蜂（full 档消费）；
    - fast 档：不跑 L2，reporter=侦察蜂、panel 恒空（§16.7-1），verdict 按侦察行
      （×SCOUT_X）归属——fast 档是侦察蜂口碑的主供血通道。
    """

    finding_id: str
    cluster_id: str | None                  # 内核签发簇号（kernel.cluster 聚类归属；
                                            #  None = 尚未归属/世界侧无账本流程——引擎
                                            #  不生成任何占位簇号，SPEC-2 §0.4）
    task: WorldTask
    reporter_id: str
    reporter_role: Role
    panel: tuple[str, ...] = ()
    cluster_scouts: tuple[str, ...] = ()
    verifier_id: str | None = None

    def __post_init__(self) -> None:
        if self.task.mode is RunMode.FAST:
            if self.reporter_role is not Role.SCOUT:
                raise ValueError(f"fast 档 reporter 必须是侦察蜂（§16.7-1），实际 {self.reporter_role}")
            if self.panel:
                raise ValueError("fast 档不跑 L2/评审，panel 必须为空（§16.7-1）")
        elif self.reporter_role is not Role.AUDITOR:
            raise ValueError(f"pr/full 档 reporter 必须是审查蜂，实际 {self.reporter_role}")
        if len(set(self.panel)) != len(self.panel):
            raise ValueError("panel 内评审蜂重复")
        if self.reporter_id in self.panel:
            raise ValueError("评审蜂不得由该 finding 的报告者兼任（纪律 2，§4.4）")
        if self.reporter_id in self.cluster_scouts:
            raise ValueError("reporter 不得重复出现在 cluster_scouts（防双重归属）")


@dataclass(frozen=True)
class VoteRecord:
    """单张 S1 票（世界侧记录；direction 语义 = 与真值同向与否，非对错）。"""

    reviewer_id: str
    direction: int          # +1 与真值同向 / −1 反向（真实票向，即机制看到的票向）
    agree: bool             # 与真值同向（冗余字段，便于统计断言）
    p_agree: float          # 该票设计边际概率 0.55+0.25·q（校准断言用）
    z_own: float            # 自身噪声（潜变量，统计断言用）
    z_panel: float          # panel 共享噪声（潜变量，统计断言用）
    z_agent: float          # 混合潜变量 z_own_w·z_own + z_panel_w·z_panel


@dataclass(frozen=True)
class S2Record:
    """S2 验证结果（§6.3 三分类；verified_fp 必附阻断证据，§10.4）。"""

    outcome: VerifyOutcome
    blocking: bool = False  # 仅 verified_fp 时为 True

    def __post_init__(self) -> None:
        if self.outcome is VerifyOutcome.VERIFIED_FP and not self.blocking:
            raise ValueError("verified_fp 必须附 blocking_evidence（§10.4），否则 runner 强制改判 verify_error")
        if self.blocking and self.outcome is not VerifyOutcome.VERIFIED_FP:
            raise ValueError("blocking_evidence 仅在 verified_fp 时存在")


@dataclass(frozen=True)
class S3Record:
    """S3 人工裁决（延迟到达；direction +1=确认 / −1=否定）。"""

    direction: int
    lag_days: float         # LogNormal(ln 5d, 1) 截断 [1d, 30d]
    arrival_day: float      # task.day + lag_days

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError(f"S3 direction 必须 ±1：{self.direction}")
        if not LAG_MIN_DAYS <= self.lag_days <= LAG_MAX_DAYS:
            raise ValueError(f"S3 lag 越出截断域 [{LAG_MIN_DAYS}, {LAG_MAX_DAYS}]：{self.lag_days}")


@dataclass(frozen=True)
class VerdictResult:
    """一个 finding 的三层 verdict 全记录（喂给 deposits 的归属输入）。"""

    ctx: FindingContext
    truth_real: bool                    # reporter 判对 ⇔ finding 真实（世界真值）
    p_correct: float                    # 判对概率（世界真值，度量校准用）
    votes: tuple[VoteRecord, ...] = ()
    s1_direction: int | None = None     # panel 多数共识（无 panel 为 None）
    s1_weight: float = S1_WEIGHT_MIN    # 本 finding 实际使用的 S1 源权重
    s2_triggered: bool = False
    s2: S2Record | None = None
    s3: S3Record | None = None

    @property
    def truth_sign(self) -> int:
        return 1 if self.truth_real else -1

    @property
    def s2_concluded(self) -> bool:
        """S2 出了确定结论（verified_real/verified_fp）；verify_error ≠ 结论（§6.2）。"""
        return self.s2 is not None and self.s2.outcome is not VerifyOutcome.VERIFY_ERROR

    @property
    def s2_direction(self) -> int | None:
        if not self.s2_concluded:
            return None
        return 1 if self.s2.outcome is VerifyOutcome.VERIFIED_REAL else -1

    @property
    def s3_direction(self) -> int | None:
        return self.s3.direction if self.s3 is not None else None


# ── 生成器 ──────────────────────────────────────────────────────

def generate_finding_verdict(
    ctx: FindingContext,
    pool: AgentPool,
    seed: int,
    vparams: VerdictParams | None = None,
) -> VerdictResult:
    """按 §1.3 生成一个 finding 的三层 verdict（逐 finding 独立子流，逐位可复现）。"""
    vp = vparams or VerdictParams()
    task = ctx.task
    rng = substream(seed, "verdict", ctx.finding_id)
    reporter = pool.get(ctx.reporter_id)

    # 真实对错：P(判对) = q·(1 − 0.3·d)（+ 运气型早期偏差，§1.1/§1.3）
    p_correct = reporter_correct_prob(pool, reporter.agent_id, task)
    truth_real = rng.random() < p_correct
    truth_sign = 1 if truth_real else -1

    # S1 投票（相关噪声 + probit 阈值，边际精确）
    z_panel = rng.gauss(0.0, 1.0)
    latent_sd = math.hypot(vp.z_own_w, vp.z_panel_w)
    votes: list[VoteRecord] = []
    for rid in ctx.panel:
        q = pool.capability(rid, task.family, task.type, t=task.t)
        p_agree = vote_agree_probability(q)
        z_own = rng.gauss(0.0, 1.0)
        z_agent = vp.z_own_w * z_own + vp.z_panel_w * z_panel
        agree = (z_agent / latent_sd) < _ND.inv_cdf(p_agree)
        votes.append(
            VoteRecord(
                reviewer_id=rid,
                direction=truth_sign if agree else -truth_sign,
                agree=agree,
                p_agree=p_agree,
                z_own=z_own,
                z_panel=z_panel,
                z_agent=z_agent,
            )
        )
    s1_direction = _majority([v.direction for v in votes])

    # S2（full 档触达；触发判定恒抽签，保证旋钮变化不移动后续抽样序）
    s2_triggered = False
    s2: S2Record | None = None
    if rng.random() < vp.s2_reach_full and task.mode is RunMode.FULL:
        s2_triggered = True
        if truth_real:
            # 判对分支：verified_real 概率 = 1 − verify_error（默认 0.2 → 0.8，§1.3）
            if rng.random() < 1.0 - vp.verify_error:
                s2 = S2Record(VerifyOutcome.VERIFIED_REAL)
            else:
                s2 = S2Record(VerifyOutcome.VERIFY_ERROR)
        else:
            # 判错分支：0.1 错误确认（会错标）；其余需阻断证据才 verified_fp（§10.4）
            if rng.random() < vp.s2_wrong_confirm:
                s2 = S2Record(VerifyOutcome.VERIFIED_REAL)
            else:
                blocking = rng.random() < vp.p_blocking
                outcome = VerifyOutcome.VERIFIED_FP if blocking else VerifyOutcome.VERIFY_ERROR
                s2 = S2Record(outcome, blocking=blocking)

    # S3（延迟人工裁决：S2 结论 40% / inconclusive 80%；ε3=0.05；截断对数正态 lag）
    concluded = s2 is not None and s2.outcome is not VerifyOutcome.VERIFY_ERROR
    reach = vp.s3_reach_concluded if concluded else vp.s3_reach_inconclusive
    s3: S3Record | None = None
    if rng.random() < reach:
        correct = rng.random() >= vp.s3_error
        direction = truth_sign if correct else -truth_sign
        lag = _sample_lag(rng)
        s3 = S3Record(direction=direction, lag_days=lag, arrival_day=task.day + lag)

    return VerdictResult(
        ctx=ctx,
        truth_real=truth_real,
        p_correct=p_correct,
        votes=tuple(votes),
        s1_direction=s1_direction,
        s1_weight=vp.s1_weight,
        s2_triggered=s2_triggered,
        s2=s2,
        s3=s3,
    )


def reporter_correct_prob(pool: AgentPool, reporter_id: str, task: WorldTask) -> float:
    """P(reporter 判对) = q(reporter, node)·(1 − 0.3·d)，运气型早期叠加 +bias（§1.3）。

    「早期」窗口复用 WorldHiddenParams.t_drift（世界隐藏参数的漂移时刻）作边界：
    t < t_drift 视为早期。概率域夹取到 [0.01, 0.99]（退化漂移后 q 可为负）。
    """
    q = pool.capability(reporter_id, task.family, task.type, t=task.t)
    p = q * (1.0 - DIFFICULTY_PENALTY * task.difficulty)
    reporter = pool.get(reporter_id)
    bias = reporter.ground.early_positive_bias
    if bias > 0.0 and task.t < pool.hidden_params.t_drift:
        p += bias
    return min(P_CORRECT_MAX, max(P_CORRECT_MIN, p))


# ── 任务流 → finding 上下文（簇号由调用方经内核聚类归属后接参，SPEC-2 §0.4）──

def assign_finding_context(
    task: WorldTask, pool: AgentPool, params: ParamsConfig, *, cluster_id: str | None
) -> FindingContext:
    """为一个产 finding 任务指派 reporter/panel/scouts/verifier（确定性、与种子无关）。

    M0 简化：路由未接入（SPEC-2 滚动导出后由 kernel.route 替换），指派按全局任务
    序号轮转——它只是 (t, 池) 的纯函数，可复现性不受影响；cache_hit 任务无 finding，
    不进入本函数。``cluster_id`` 为必接参（无占位缺省）：M1 引擎经
    kernel.cluster.attribute_findings 取内核签发簇号后回填；无账本的世界侧流程传
    None（未归属，存款 payload.cluster=None）。
    """
    auditors = pool.by_role(Role.AUDITOR)
    scouts = pool.by_role(Role.SCOUT)
    reviewers = pool.by_role(Role.REVIEWER)
    verifiers = pool.by_role(Role.VERIFIER)
    if not auditors or not scouts or not reviewers:
        raise ValueError("roster 必须 ≥1 scout/auditor/reviewer 才能产 finding 流")

    finding_id = f"F-SIM-{task.t:06d}"
    if task.mode is RunMode.FAST:
        reporter = scouts[task.t % len(scouts)]
        return FindingContext(
            finding_id=finding_id, cluster_id=cluster_id, task=task,
            reporter_id=reporter.agent_id, reporter_role=Role.SCOUT,
        )
    reporter = auditors[task.t % len(auditors)]
    k = min(params.panel.SIZE, len(reviewers))
    panel = tuple(reviewers[(task.t + i) % len(reviewers)].agent_id for i in range(k))
    marks = tuple(scouts[(task.t + i) % len(scouts)].agent_id for i in range(min(2, len(scouts))))
    verifier = verifiers[task.t % len(verifiers)].agent_id if verifiers else None
    return FindingContext(
        finding_id=finding_id, cluster_id=cluster_id, task=task,
        reporter_id=reporter.agent_id, reporter_role=Role.AUDITOR,
        panel=panel, cluster_scouts=marks, verifier_id=verifier,
    )


def run_findings_flow(
    tasks: list[WorldTask],
    pool: AgentPool,
    seed: int,
    params: ParamsConfig | None = None,
    vparams: VerdictParams | None = None,
    *,
    cluster_of: Mapping[str, str] | Callable[[WorldTask], str | None] | None = None,
) -> list[tuple[FindingContext, VerdictResult]]:
    """任务流 → (ctx, verdict) 列表：cache_hit 任务无 finding，直接跳过（§1.2）。

    簇归属经 ``cluster_of`` 显式接参（finding_id → 内核簇号映射，或 task → 簇号
    回调；None = 世界侧无账本流程，ctx.cluster_id=None 未归属）——簇号一律来自
    内核真实聚类（kernel.cluster），本模块不生成任何占位簇号（SPEC-2 §0.4）。
    """
    p = params or load_params()
    out: list[tuple[FindingContext, VerdictResult]] = []
    for task in tasks:
        if not task.produces_finding:
            continue
        cid: str | None = None
        if cluster_of is not None:
            cid = (
                cluster_of[f"F-SIM-{task.t:06d}"]
                if isinstance(cluster_of, Mapping)
                else cluster_of(task)
            )
        ctx = assign_finding_context(task, pool, p, cluster_id=cid)
        out.append((ctx, generate_finding_verdict(ctx, pool, seed, vparams)))
    return out


# ── 内部抽样 ────────────────────────────────────────────────────

def _majority(directions: list[int]) -> int | None:
    """多数共识（奇数票严格判定；平票不支持——M0 评审团为奇数，§16.7-6）。"""
    if not directions:
        return None
    s = sum(directions)
    if s == 0:
        raise ValueError("S1 平票：M0 评审团尺寸必须为奇数")
    return 1 if s > 0 else -1


def _sample_lag(rng) -> float:
    """lag ~ LogNormal(ln 5d, 1) 截断 [1d, 30d]（拒绝采样；接受率 ≈ 0.91）。"""
    while True:
        x = rng.lognormvariate(LAG_LN_MU, LAG_LN_SIGMA)
        if LAG_MIN_DAYS <= x <= LAG_MAX_DAYS:
            return x


__all__ = [
    "DIFFICULTY_PENALTY",
    "FindingContext",
    "LAG_LN_MU", "LAG_LN_SIGMA", "LAG_MAX_DAYS", "LAG_MIN_DAYS",
    "S1_WEIGHT_MAX", "S1_WEIGHT_MIN",
    "S2Record", "S2_SOURCE_WEIGHT",
    "S3Record", "S3_SOURCE_WEIGHT",
    "VerdictParams", "VerdictResult",
    "VoteRecord", "VOTE_BASE", "VOTE_Q_GAIN",
    "assign_finding_context",
    "generate_finding_verdict",
    "latent_correlation",
    "reporter_correct_prob",
    "run_findings_flow",
    "theoretical_lag_mean",
    "vote_agree_probability",
]
