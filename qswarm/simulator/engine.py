"""simulator.engine：M0 机制仿真器主循环（SPEC-4 §0 闭环架构图的本体实现）。

闭环逐仿真日步进（§0 架构图，一行不多一行不少）：

    sim_world ──(tasks)──→ kernel.route ──→ sim_verdict_gen ──(deposits)──→ kernel.ledger/cells
                                  ↑                                              │
                                  └────────── promote_scan（夜间批）←─────────────┘

职责边界（§0 关键工程决策「仿真复用内核实现」的逐条落实）：

- 引擎**只生成事件流**：任务（taskgen）、verdict（verdict）、存款调度（deposits）、
  考试（exam）；机制本体一律调用真实内核——派单 ``kernel.route``（或注入的 TS
  对照路由 ``kernel.route_thompson``）、落账 ``KernelLedger.append``（唯一写口）、
  聚合 ``agg_cells``、夜间孵化 ``promote_scan``（挂 ExamCenter 暗考回调）、重放
  ``kernel.replay``；不重写任何内核机制，也无 LLM/网络（§6 实现约束）。
- **真实聚类归属（M1 接通，SPEC-2 §0.4/§2.1）**：产 finding 任务经
  ``kernel.dispatch.open_finding`` 建档（findings 行 + finding 事件，内核唯一
  写口），昼末由 ``kernel.cluster.attribute_findings`` 做真实粗聚类归属（提案
  cluster_findings / 扩簇 all-pairwise 复核 / 落簇 apply_merges，簇号 C-{date}-{seq}
  由内核签发）——引擎不再生成任何占位簇号，存款归属消费内核真号；世界侧只合成
  finding 证据面事实（:func:`finding_facts`：taxonomy 节点 path + 槽位行窗）。
- 度量从内核输出侧采集（``simulator.metrics``）：①–⑦ + ⑨ 在每次 run 收尾经
  ``collect_run_metrics`` 一次采齐；⑧ S2 饥饿传导是跨运行对比，由
  :func:`run_s2_starvation` 驱动同一世界在 verify_error 三档下重跑后聚合。
- 重放等价校验（⑨）按**可配时点**在环内触发：``replay_every`` 个仿真日一次
  （对当时账本做 rebuild vs 在线逐字段比对），收尾再做全账本默认检查点检查。

参数覆盖接口（§3 实验矩阵的扫描入口）：:func:`run` 的 ``overrides`` 接受
``{"tilt.T_BASE": 0.9, "promote.N": 12}`` 形态的点路径覆盖——机制参数仍走
params.yaml 的 pydantic 校验（越界/交叉约束违反/未知键一律拒绝）；世界侧旋钮
（λ、verify_error、exam 播种开关等）走 ``stream`` / ``verdict`` / ``exam`` /
``hidden`` 显式注入。对照组实验臂：``exam=ExamWorldParams(seeding=False)`` 关
播种；``router=route_thompson`` 换 TS 路由（同参数比 regret，§3 附加实验）。

确定性纪律（§6：种子显式注入、可复现）：
- 全部随机源从主种子派生命名子流（rng.derive_seed）：任务流/verdict 逐 finding/
  考试逐格/engine 路由流互不串扰；内核时钟为引擎自有的 :class:`SimClock`
  （沿 append 序单调不减的确定性仿真时钟，批内并列、跨日严格递增），
  账本 seq 由 SQLite 单调签发。
- 同 ``(seed, n_days, 全部配置)`` ⇒ 事件序列（seq/ts/kind/payload）与度量逐位
  一致（ac：固定种子端到端 run 两次逐位一致）；任何配置差 ⇒ 轨迹不同。确定性
  不依赖进程内状态与哈希序：随机源全部显式注入、候选集统一字典序，跨进程
  （不同 PYTHONHASHSEED）重跑同轨迹（测试有跨进程断言钉死）。
- 时长预算（§6 单实验 ≤ 5 分钟）：夜批 promote_scan 与逐日 agg_cells 都是
  O(账本) 重扫，30 日 × λ=50 量级（约万级事件）在秒级完成。

存款入账时点口径：S1/S2 于 task.day 落账；S3 于 ``task.day + lag`` 起在逐日
flush 中入账（``rec.day <= day`` 即到期）。lag 的日内小数部分被「整日 flush」
归整到次日末——任务坐标换算（metrics.TaskTimeline 按 floor(day) 取整日累计）
对此不敏感，且该归整是确定性的，可复现性不受影响。
"""

from __future__ import annotations

import hashlib
import json
import random
import shutil
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from qswarm.contracts import (
    DispatchPayload,
    LedgerKind,
    ParamsConfig,
    Role,
    RunMode,
    TaxonomyConfig,
    load_params,
    load_taxonomy,
)
from qswarm.kernel import (
    SLIDING_WINDOW_DAYS,
    FindingFacts,
    KernelLedger,
    RouteDecision,
    Transition,
    agg_cells,
    attribute_findings,
    open_finding,
    open_run,
    promote_scan,
    route,
)
from qswarm.kernel.ledger import LedgerEvent

from .deposits import deposit_schedule
from .exam import ExamCenter, ExamWorldParams, ForCauseExamCenter, seed_entry_exams
from .metrics import (
    ReplayEquivalenceReport,
    RunMetrics,
    RoutingRecord,
    SIM_T0,
    S2StarvationReport,
    TaskTimeline,
    WorldTruth,
    collect_run_metrics,
    replay_equivalence,
    snapshot_world_truth,
    s2_starvation_propagation,
)
from .rng import derive_seed
from .taskgen import TaskGenerator, TaskStreamParams
from .verdict import FindingContext, VerdictParams, generate_finding_verdict
from .world import AgentPool, WorldHiddenParams

__all__ = [
    "NIGHT_FRACTION",
    "ROUTED_ROLES",
    "WORLD_LANG",
    "WORLD_LINE_SLOTS",
    "WORLD_WINDOW_LEN",
    "DayRecord",
    "Router",
    "S2StarvationRun",
    "SimClock",
    "RunResult",
    "apply_overrides",
    "finding_facts",
    "run",
    "run_s2_starvation",
]

#: 夜间批（promote_scan）的日内锚点：as_of = day + 0.5（日内中点）。取中点而非
#: 0.999，使 SimClock 的秒级漂移（沿 append 序累计，见 SimClock docstring）不把
#: 夜批时点推过日界；promote_scan 是当日全部任务之后的批，floor(day) 坐标不变。
NIGHT_FRACTION = 0.5

#: 引擎路由的角色集（§16.7-1 口径：fast 档 reporter=侦察蜂，pr/full 档=审查蜂；
#: panel/侦察标记/验证蜂由 verdict 侧确定性轮转指派，不走路由——见
#: `_finding_context`）。route 的显式探索池 = 该角色全员编制（§5.1 永不死锁：
#: 池中无格子 agent 的权重取 ROOT_PRIOR，冷启动/无播种对照都能起跑）。
ROUTED_ROLES = (Role.SCOUT, Role.AUDITOR)

Router = Callable[..., RouteDecision]

# ── 世界 finding 证据面模型（SPEC-2 §0.4「世界侧生成 finding 流」的落地面）──

#: 世界 finding 的语言维度：仿真世界无多语言建模，恒 "sim"（coarse_mergeable 的
#: 同 lang 判据恒真，不阻断归并——语言维度留给真实仓世界）。
WORLD_LANG = "sim"

#: 证据行窗槽位数：world finding 的行窗 = 槽 base + [1, WORLD_WINDOW_LEN]，
#: 槽号 = task.t mod WORLD_LINE_SLOTS。同 (family,type) 同槽位的证据窗完全重叠
#: （line_overlap=1.0 ≥ 0.5，内核判可并）、异槽位零重叠（拒并）——真实粗聚类在
#: 轨迹中同时吃到「并/拒」两类输入。纯 t 派生：不新增随机子流、跨进程逐位一致
#: （§6 随机纪律）；该模型只决定世界侧 finding 事实面，归并判定本体在内核
#: （cluster_findings/attribute_findings/apply_merges，仿真零第二实现）。
WORLD_LINE_SLOTS = 10
WORLD_WINDOW_LEN = 10

#: 世界 finding 的建档静态字段（findings 表 NOT NULL 列的世界口径：无真实仓、
#: 无 commit、无 severity 评审——确定性常量；机制侧不读这些字段）。
WORLD_REPO = "sim://world"
WORLD_COMMIT = ""
WORLD_SEVERITY = "medium"
WORLD_GRADE = "L1"


def finding_facts(task) -> FindingFacts:
    """WorldTask → 聚类判据事实面（世界侧确定性合成；SPEC-2 §1.2 调用方装配口）。

    - finding_id = ``F-SIM-{t:06d}``（世界侧自成前缀，D20 明文保留）；
    - path = taxonomy 节点 ``{family}/{type}``（同族同类型的 finding 共享 path）；
    - cwe = None（世界不判 CWE；D3：双 None 退 (family,type) 相等）；
    - 行窗按槽位轮转（见 WORLD_LINE_SLOTS），纯 t 派生零随机源。
    """
    base = (task.t % WORLD_LINE_SLOTS) * WORLD_WINDOW_LEN
    return FindingFacts(
        finding_id=f"F-SIM-{task.t:06d}",
        path=f"{task.family}/{task.type}",
        line_start=base + 1,
        line_end=base + WORLD_WINDOW_LEN,
        cwe=None,
        lang=WORLD_LANG,
        family=task.family,
        type=task.type,
        sim_text="",
    )


# ── 确定性仿真时钟（kernel.KernelClock 协议实现）────────────────


class SimClock:
    """沿 append 序单调不减的仿真时钟：``ts = T0 + day 天 + 全局计数秒``。

    计数器**全局**递增（不按日清零）：同日多次 advance 逐秒前移，跨日 advance
    的整日跳变远大于累计秒数 ⇒ 任意两次 now() 不减、跨日严格递增，ts 序与 seq
    序的并列结构确定可复现——批内事件（入学播种 / 夜批流转）共享时间戳是设计内
    行为，metrics.replay_equivalence 的 as_of 重放窗按「并列 ts 并入同窗」处理
    （§6-6）。秒级漂移量 = 累计落账事件数（约万级事件 ≈ 0.1 天），远小于夜批锚点
    NIGHT_FRACTION=0.5 到下一日界的余量。
    """

    def __init__(self, t0: datetime = SIM_T0) -> None:
        self.t0 = t0
        self.dt: datetime = t0
        self._i = 0

    def advance_to(self, day: float) -> None:
        self._i += 1
        self.dt = self.t0 + timedelta(days=float(day), seconds=self._i)

    def now(self) -> datetime:
        return self.dt


# ── 路由适配与 finding 上下文 ───────────────────────────────────


class _RoutedTask:
    """WorldTask 的路由适配：补 route() 所需 role（fast→scout，其余→auditor）。

    route() 是鸭子类型读取（task_id/family[/type]/role）；WorldTask 自带前两者。
    """

    __slots__ = ("task_id", "family", "type", "role")

    def __init__(self, task) -> None:
        self.task_id = task.task_id
        self.family = task.family
        self.type = task.type
        self.role = (
            Role.SCOUT.value if task.mode is RunMode.FAST else Role.AUDITOR.value
        )


def _finding_context(
    task, reporter_id: str, pool: AgentPool, params: ParamsConfig,
    *, finding_id: str,
) -> FindingContext:
    """任务 + 路由 assignee → FindingContext（verdict/deposits 的归属输入）。

    - reporter = kernel.route 的派单结果（引擎闭环与 verdict.assign_finding_context
      的轮转占位在此分野：M0 正式路线是 route 派 reporter，SPEC-2 滚动后不变）；
    - panel / cluster_scouts / verifier 为 (t, 池) 的确定性轮转（评审与验证蜂不占
      派单预算；轮转跳开 reporter，满足 FindingContext 的防双重归属校验）；
    - cluster_id 由调用方在内核聚类归属后回填（建簇号一律 kernel 签发：
      attribute_findings → apply_merges → attach_cluster，引擎不生成任何簇号）。
    """
    fid = finding_id
    if task.mode is RunMode.FAST:
        return FindingContext(
            finding_id=fid, cluster_id=None, task=task,
            reporter_id=reporter_id, reporter_role=Role.SCOUT,
        )
    reviewers = [a.agent_id for a in pool.by_role(Role.REVIEWER)]
    scouts = [a.agent_id for a in pool.by_role(Role.SCOUT)]
    verifiers = [a.agent_id for a in pool.by_role(Role.VERIFIER)]
    if not reviewers or not scouts:
        raise ValueError("roster 必须 ≥1 scout/reviewer 才能产 pr/full finding 流")
    k = min(params.panel.SIZE, len(reviewers))
    rotated_rev = reviewers[task.t % len(reviewers):] + reviewers[: task.t % len(reviewers)]
    panel = tuple(a for a in rotated_rev if a != reporter_id)[:k]
    rotated_scout = scouts[task.t % len(scouts):] + scouts[: task.t % len(scouts)]
    marks = tuple(a for a in rotated_scout if a != reporter_id)[: min(2, len(scouts))]
    verifier = verifiers[task.t % len(verifiers)] if verifiers else None
    return FindingContext(
        finding_id=fid, cluster_id=None, task=task,
        reporter_id=reporter_id, reporter_role=Role.AUDITOR,
        panel=panel, cluster_scouts=marks, verifier_id=verifier,
    )


# ── 参数覆盖（§3 实验矩阵的机制参数扫描入口）────────────────────


def apply_overrides(
    params: ParamsConfig, overrides: Mapping[str, Any] | None
) -> ParamsConfig:
    """点路径覆盖机制参数并**重新过全量 pydantic 校验**。

    - 键形如 ``"节.字段"``（如 ``"tilt.T_BASE"``、``"promote.N"``）；
    - 未知节/未知字段/非点路径 → ValueError；越界值或交叉约束违反
      （如 T_MIN > T_MAX）→ pydantic ValidationError（== 未覆盖前的 params.yaml
      严格度，实验矩阵扫描不出无效参数组）；
    - 未覆盖字段原样保留（深拷贝语义：不修改传入对象）。
    """
    if not overrides:
        return params
    data = params.model_dump()
    for key, value in overrides.items():
        parts = key.split(".")
        if len(parts) != 2:
            raise ValueError(
                f"overrides 键必须是 '节.字段' 点路径：{key!r}（例 'tilt.T_BASE'）"
            )
        section, name = parts
        if section not in data:
            raise ValueError(f"overrides 未知参数节：{section!r}（键 {key!r}）")
        if name not in data[section]:
            raise ValueError(
                f"overrides 未知字段：{key!r}（节 {section} 合法字段：{sorted(data[section])}）"
            )
        data[section][name] = value
    return ParamsConfig.model_validate(data)


# ── 运行产物 ────────────────────────────────────────────────────


@dataclass(frozen=True)
class DayRecord:
    """单日运行摘要（day_log 一行；逐日进度与闭环健康度的取证口径）。"""

    day: int
    tasks: int                    # 当日到达任务数
    dispatched: int               # 当日落 dispatch 事件数
    deposits: int                 # 当日入账存款笔数（含 S3 到期）
    promotions: int               # 当日夜批 promotion 流转数
    demotions: int                # 当日夜批 demotion 流转数
    replay_ok: bool | None        # 当日触发重放等价校验的结果（未触发 None）


@dataclass
class RunResult:
    """一次 ``run()`` 的完整产物：轨迹 + 9 项度量 + 配置回显。"""

    seed: int
    n_days: int
    params: ParamsConfig                       # 生效参数（覆盖后）
    stream: TaskStreamParams
    verdict: VerdictParams
    exam: ExamWorldParams
    hidden: WorldHiddenParams
    router_name: str                           # 'kernel.route' | 'route_thompson' | 自定义
    tasks: list                                # list[WorldTask]
    routing: list[RoutingRecord]
    transitions: list[Transition]
    events: list[LedgerEvent]
    day_log: list[DayRecord]
    metrics: RunMetrics                        # §2 九项（⑧为跨运行，见 run_s2_starvation）
    replay_report: ReplayEquivalenceReport     # 收尾全账本重放等价报告
    replay_day_reports: tuple                   # 环内可配时点校验报告序列
    truth: WorldTruth
    timeline: TaskTimeline
    status_counts: dict[str, int]              # 收尾 cells 状态分布
    cells_count: int
    cluster_of: dict[str, str]                 # finding_id → 内核签发簇号（真实聚类归属）
    db_path: str | None                        # keep_db=True 时保留的账本路径
    elapsed_s: float

    # -- 轨迹比对（ac：固定种子两次 run 逐位一致）──

    def canonical_trajectory(self) -> str:
        """事件序列 + 路由决策 + 任务流 + 度量的规范化 JSON（sort_keys，逐位比对用）。

        float 经 json 的 repr 往返精确保真；事件含内核签发的 seq/ts/payload 全量。
        """
        doc = {
            "seed": self.seed,
            "n_days": self.n_days,
            "params": self.params.model_dump(),
            "tasks": [t.to_record() for t in self.tasks],
            "events": [
                {"seq": e.seq, "ts": e.ts, "kind": str(e.kind), "payload": e.payload}
                for e in self.events
            ],
            "routing": [asdict(r) for r in self.routing],
            "metrics": self.metrics.to_dict(),
        }
        return json.dumps(doc, sort_keys=True, ensure_ascii=False)

    @property
    def digest(self) -> str:
        """轨迹摘要（sha256，快速不相等断言用）。"""
        return hashlib.sha256(self.canonical_trajectory().encode("utf-8")).hexdigest()

    def event_kinds(self) -> Counter:
        """账本事件 kind 计数（dispatch/deposit/promotion/demotion 齐备性断言用）。"""
        return Counter(str(e.kind) for e in self.events)

    def metrics_record(self) -> dict[str, float | bool | None]:
        """九项度量记录（RunMetrics 的 9 字段平铺；⑨ = replay_equivalent 布尔）。"""
        return self.metrics.to_dict()


# ── 主入口 ──────────────────────────────────────────────────────


def run(
    params: ParamsConfig | str | Path | None = None,
    *,
    seed: int = 0,
    n_days: int = 30,
    overrides: Mapping[str, Any] | None = None,
    stream: TaskStreamParams | None = None,
    verdict: VerdictParams | None = None,
    exam: ExamWorldParams | None = None,
    hidden: WorldHiddenParams | None = None,
    taxonomy: TaxonomyConfig | None = None,
    router: Router | None = None,
    promote_window_days: float = SLIDING_WINDOW_DAYS,
    replay_every: int = 10,
    replay_sample: int = 12,
    run_id: str | None = None,
    db_path: str | Path | None = None,
    keep_db: bool = False,
) -> RunResult:
    """按仿真日步进跑通 §0 闭环，返回完整轨迹与 §2 九项度量。

    参数：
    - ``params``：ParamsConfig / params.yaml 路径 / None（configs/params.yaml）；
    - ``seed``   ：主种子（世界/任务/verdict/考试/路由全部子流的派生根）；
    - ``n_days`` ：仿真日数；
    - ``overrides``：机制参数点路径覆盖（见 :func:`apply_overrides`；§3 矩阵扫描口）；
    - ``stream`` / ``verdict`` / ``exam`` / ``hidden``：世界侧旋钮（λ、verify_error、
      exam_seeding 开关、隐藏设定）——不在 params.yaml，属仿真世界（各 dataclass 缺省
      = SPEC 口径值）；
    - ``router``：路由器注入点（§3 TS 对照臂）。None = ``kernel.route``；传
      ``route_thompson`` 即同参数换 TS；签名约定
      ``router(task, cells, rng, params, *, pool)``（与 kernel.route 一致）；
    - ``promote_window_days``：夜批衰退滑动窗窗长（透传 kernel.promote_scan）；
    - ``replay_every``：每 N 个仿真日在环内触发一次重放等价校验（0 = 关闭）；
      ``replay_sample``：收尾全账本检查的检查点采样数；
    - ``run_id``：全事件落的 run 标签（缺省按 seed/n_days 生成）；
    - ``db_path`` / ``keep_db``：账本落点；缺省临时目录用后即删（异常路径也不
      泄漏），``db_path`` 给定或 ``keep_db=True`` 时保留（返回值 ``db_path`` 字段
      回传路径）。``db_path`` 指向已有账本时为追加写：``RunResult.events`` 只含
      本次 run 追加的事件段，度量与重放等价按账本全量口径。
    """
    t_start = time.perf_counter()

    # ── 配置固化 ──
    if isinstance(params, ParamsConfig):
        base_params = params
    elif params is None:
        base_params = load_params()
    else:
        base_params = load_params(params)
    p = apply_overrides(base_params, overrides)
    tax = taxonomy or load_taxonomy()
    sp = stream or TaskStreamParams()
    vp = verdict or VerdictParams()
    ep = exam or ExamWorldParams()
    hp = hidden or WorldHiddenParams()
    router_fn = router or route
    router_name = getattr(router_fn, "__name__", str(router_fn)) if router is not None else "kernel.route"
    rid = run_id or f"sim-s{seed}-d{n_days}"
    if promote_window_days <= 0:
        raise ValueError(f"promote_window_days 必须 > 0：{promote_window_days}")

    # ── 世界与账本 ──
    pool = AgentPool.generate(p.roster.model_dump(), tax, seed=seed, hidden=hp)
    truth = snapshot_world_truth(pool)
    clock = SimClock(SIM_T0)

    owned_db = db_path is None
    if owned_db:
        workdir = tempfile.mkdtemp(prefix="qswarm-engine-")
        db = Path(workdir) / "ledger.db"
    else:
        db = Path(db_path)
        db.parent.mkdir(parents=True, exist_ok=True)
    # 一次性临时账本（用后即删且未被 keep_db 留存）放宽 fsync：synchronous=OFF
    # 只影响 OS 崩溃/断电时已提交事务的存活性，进程内数据与读取语义逐位不变
    # （DECISIONS 2026-10-10）；每事件一次 commit 的写口纪律不动，省下的是
    # 纯 I/O 等待（profile 实测占单 run 墙钟 ~40%）。用户指定 db_path 或
    # keep_db=True 的账本可能被留存复用，保持缺省 FULL 持久化。
    ledger = KernelLedger(
        db, clock=clock, synchronous="OFF" if (owned_db and not keep_db) else None
    )
    # 本次 run 的事件段起点：复用 db_path（追加写）时，RunResult.events 只圈定
    # 本次 run 追加的 seq 区间，不混入此前 run 的事件（度量/重放仍按账本全量口径）。
    seq_start = ledger.latest_seq() + 1
    keep_db_dir = (not owned_db) or keep_db

    try:
        gen = TaskGenerator(p, tax, seed=seed, stream=sp)
        center = ExamCenter(pool, ledger, seed=seed, params=p, config=ep, run_id=rid)
        fc_center = ForCauseExamCenter(
            pool, ledger, seed=seed, params=p, config=ep, run_id=rid
        )
        rng_route = random.Random(derive_seed(seed, "engine", "route"))
        # 显式探索池 = 该角色全员编制（route 缺省池只含已有格子者，冷启动/对照组会死锁）
        pools = {
            role.value: [a.agent_id for a in pool.by_role(role)] for role in ROUTED_ROLES
        }

        # §8.4 入学播种（对照组 ep.seeding=False：零事件零随机数）
        seed_entry_exams(pool, ledger, seed=seed, params=p, config=ep, run_id=rid)

        # run 生命周期入口（findings.run_id 外键前件；无 BudgetPolicy 注入 =
        # 无预算约束路径，budget_cents=0 快照，D18/D20 同域口径）
        open_run(ledger, run_id=rid)

        tasks_all: list = []
        routing: list[RoutingRecord] = []
        transitions: list[Transition] = []
        day_log: list[DayRecord] = []
        day_reports: list = []
        pending: list[tuple[float, int, Any]] = []   # (arrival_day, order, payload)
        cluster_of: dict[str, str] = {}              # finding_id → 内核簇号（真聚类归属）
        order = 0

        for day, batch in enumerate(gen.iter_days(n_days)):
            # ── 晨：视图刷新（当日派单读同一份 as_of 快照，日内确定性）──
            clock.advance_to(float(day))
            view = agg_cells(ledger, clock.dt, params=p)

            # ── 昼：任务流 → kernel.route 派单 → verdict 生成（归属待昼末）──
            day_findings: list = []                  # [(ctx, VerdictResult, FindingFacts)]
            tasks_all.extend(batch)
            for task in batch:
                rt = _RoutedTask(task)
                decision = router_fn(rt, view, rng_route, p, pool=pools[rt.role])
                clock.advance_to(float(day))
                ledger.append(
                    LedgerKind.DISPATCH,
                    DispatchPayload(**decision.to_dispatch_payload()),
                    run_id=rid,
                )
                routing.append(
                    RoutingRecord(
                        task_id=task.task_id, role=decision.role, node=decision.node,
                        assignee=decision.assignee, channel=decision.channel,
                    )
                )
                if task.produces_finding and decision.assignee is not None:
                    facts = finding_facts(task)
                    clock.advance_to(float(day))
                    open_finding(
                        ledger,
                        finding_id=facts.finding_id, run_id=rid,
                        reporter=decision.assignee,
                        reporter_role=rt.role,
                        family=task.family, type=task.type,
                        lang=WORLD_LANG, repo=WORLD_REPO, path=facts.path,
                        line_start=facts.line_start, line_end=facts.line_end,
                        title=f"{task.family}/{task.type} finding",
                        severity=WORLD_SEVERITY, grade=WORLD_GRADE,
                        commit_sha=WORLD_COMMIT, cwe=facts.cwe,
                    )
                    ctx = _finding_context(
                        task, decision.assignee, pool, p, finding_id=facts.finding_id
                    )
                    day_findings.append((ctx, generate_finding_verdict(ctx, pool, seed, vp), facts))

            # ── 昼末（存款入账前）：内核真实聚类归属（§2.1 流水线「粗聚类」步）──
            # 簇号一律 kernel 签发（C-{date}-{seq}，D20）；存款归属消费该真号。
            if day_findings:
                clock.advance_to(float(day))
                assignment = attribute_findings(
                    ledger, [f for _, _, f in day_findings], run_id=rid
                )
                cluster_of.update(assignment)
                for ctx, res, facts in day_findings:
                    ctx = replace(ctx, cluster_id=assignment[facts.finding_id])
                    for rec in deposit_schedule(ctx, res, p):
                        pending.append((rec.day, order, rec.payload))
                        order += 1

            # ── 昼末：到期存款入账（S1/S2=task.day；S3=task.day+lag 起逐日 flush）──
            clock.advance_to(float(day))
            due = sorted((x for x in pending if x[0] <= day), key=lambda x: (x[0], x[1]))
            pending = [x for x in pending if x[0] > day]
            for _, _, payload in due:
                clock.advance_to(float(day))
                ledger.append(LedgerKind.DEPOSIT, payload, run_id=rid)
            n_dep_day = len(due)

            # ── 夜：promote_scan（孵化状态机 + ExamCenter 暗考回调）──
            t_now = len(routing)                        # 世界时钟坐标（退化漂移/考试读）
            clock.advance_to(float(day) + NIGHT_FRACTION)
            night_as_of = clock.dt

            def _exam_cb(*, agent_id: str, role: str, node: str, from_status):
                return center.run_promotion_exam(
                    agent_id, role, node, day=day, t=t_now, from_status=from_status
                )

            def _for_cause_cb(**kwargs):
                # 裁量复核考回调（§8.1 淘汰通道）：触发判定在 kernel（FC_NET），
                # 考试本体在此；冷却/传唤次数由 fc_center 登记确定演化。
                return fc_center.run_for_cause_exam(
                    kwargs["agent_id"],
                    kwargs["role"],
                    kwargs["node"],
                    day=day,
                    t=t_now,
                )

            day_trans = promote_scan(
                ledger, night_as_of, params=p, exam=_exam_cb,
                window_days=promote_window_days, run_id=rid,
                for_cause=_for_cause_cb,
            )
            transitions.extend(day_trans)

            # ── 可配时点：环内重放等价校验（⑨ 回归项）──
            replay_ok: bool | None = None
            if replay_every and replay_every > 0 and day > 0 and day % replay_every == 0:
                rep = replay_equivalence(
                    ledger, checkpoints=[ledger.latest_seq()], params=p,
                    check_table=False, sample=replay_sample,
                )
                day_reports.append(rep)
                replay_ok = rep.passed

            day_log.append(
                DayRecord(
                    day=day, tasks=len(batch), dispatched=len(batch),
                    deposits=n_dep_day,
                    promotions=sum(1 for t in day_trans if t.kind == "promotion"),
                    demotions=sum(1 for t in day_trans if t.kind == "demotion"),
                    replay_ok=replay_ok,
                )
            )

        # ── 收尾：度量采集（内核输出侧）+ 全账本重放等价 + cells 状态分布 ──
        timeline = TaskTimeline.from_tasks(tasks_all, t0=SIM_T0)
        final_report = replay_equivalence(ledger, params=p, sample=replay_sample)
        overall_ok = bool(final_report.passed) and all(r.passed for r in day_reports)
        metrics = collect_run_metrics(
            ledger, truth, timeline, tasks_all, routing, replay_equivalent=overall_ok
        )
        final_view = agg_cells(ledger, clock.dt, params=p)
        status_counts = dict(sorted(Counter(c.status for c in final_view.cells.values()).items()))
        events = ledger.read(from_seq=seq_start)
    finally:
        ledger.close()
        # 临时账本用后即删——异常路径同样不泄漏（finally 兜底）
        if not keep_db_dir:
            shutil.rmtree(db.parent, ignore_errors=True)

    kept_path = str(db) if keep_db_dir else None

    return RunResult(
        seed=seed,
        n_days=n_days,
        params=p,
        stream=sp,
        verdict=vp,
        exam=ep,
        hidden=hp,
        router_name=router_name,
        tasks=tasks_all,
        routing=routing,
        transitions=transitions,
        events=events,
        day_log=day_log,
        metrics=metrics,
        replay_report=final_report,
        replay_day_reports=tuple(day_reports),
        truth=truth,
        timeline=timeline,
        status_counts=status_counts,
        cells_count=len(final_view.cells),
        cluster_of=dict(sorted(cluster_of.items())),
        db_path=kept_path,
        elapsed_s=time.perf_counter() - t_start,
    )


# ── ⑧ S2 饥饿传导驱动（跨运行度量；§2/§3 扫描臂）────────────────


@dataclass(frozen=True)
class S2StarvationRun:
    """verify_error 扫描的驱动产物：⑧ 报告 + 各档位的运行度量记录。"""

    points: tuple[float, ...]
    report: S2StarvationReport
    metrics_by_point: dict[float, dict]         # point → RunMetrics.to_dict()
    reference: float
    limit: float

    @property
    def passed(self) -> bool:
        return self.report.passed


def run_s2_starvation(
    params: ParamsConfig | str | Path | None = None,
    *,
    seed: int = 0,
    n_days: int = 30,
    verify_error_points: Sequence[float] = (0.20, 0.35, 0.50),
    reference: float = 0.20,
    limit: float = 2.0,
    overrides: Mapping[str, Any] | None = None,
    stream: TaskStreamParams | None = None,
    exam: ExamWorldParams | None = None,
    hidden: WorldHiddenParams | None = None,
    taxonomy: TaxonomyConfig | None = None,
    replay_every: int = 0,
) -> S2StarvationRun:
    """⑧ S2 饥饿传导：同一世界在 verify_error 各档下重跑，报告中位晋升时长增幅。

    除 ``verdict`` 外的全部配置逐档一致（同种子同参数，唯一变量是 S2 出错率）；
    判定 = 真专家口径中位晋升时长的增幅 ≤ limit（§2 目标 ≤ 2×）。缺省关闭环内
    重放校验（该驱动只采 ⑧；等价性由单次 run 的 ⑨ 覆盖）。
    """
    metrics_by_point: dict[float, dict] = {}
    for ve in verify_error_points:
        res = run(
            params,
            seed=seed,
            n_days=n_days,
            overrides=overrides,
            stream=stream,
            verdict=VerdictParams(verify_error=float(ve)),
            exam=exam,
            hidden=hidden,
            taxonomy=taxonomy,
            replay_every=replay_every,
        )
        metrics_by_point[float(ve)] = res.metrics_record()
    report = s2_starvation_propagation(
        {
            ve: [metrics_by_point[float(ve)]["median_time_to_active"]]
            for ve in verify_error_points
        },
        reference=reference,
        limit=limit,
    )
    return S2StarvationRun(
        points=tuple(float(v) for v in verify_error_points),
        report=report,
        metrics_by_point=metrics_by_point,
        reference=float(reference),
        limit=float(limit),
    )
