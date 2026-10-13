"""kernel.dispatch：委托契约四件套装配与派单/结论双状态机（SPEC-2 §2；设计 §4.2/§4.3/§10.2）。

职责与写口（SPEC-2 §6.1 表：``tasks`` / ``findings`` / ``evidence_nodes`` /
``votes`` / ``verify_results`` / ``candidates`` 的唯一写口均落在本模块）：

- **四件套装配**（§2.2，设计 §4.3 逐字）：每次派单必含 ①目标 ②输出格式（SPEC-1
  §4.2 输出契约名）③工具与信息源边界（§4.1 权限边界的机器可读形态）④任务边界；
  外加 hop_budget（§2.2 档位表逐字 fast=0 / pr=1 / full=2）与 taxonomy_node。
  :func:`build_contract` 纯函数零 IO；工蜂产出一律落盘为工件、只回传引用（§4.3）；
- **任务派单状态机**（§2.3）：``queued ──dispatch()──→ dispatched ──→ done|failed``；
  超时/schema 不合格走 :func:`re_dispatch`（初派 1 + 重试 1，§16.7-8 逐字）；
  任意非终态经 :func:`expire_run`（预算熔断/超时收尾）→ 任务侧 failed /
  finding 侧 expired（零存款）；
- **Finding 结论状态机**（§2.4，§10.2 图逐字 + D8 一条 S3 直达边）：
  :data:`FINDING_TRANSITIONS` 迁移表守卫，非法迁移 / 终态再流转一律 ValueError；
  流转以 kind=finding 事件承载（payload.state=新状态，D9），从不产 deposit
  （§10.2 簧语义：不产生「没验证=负面」偏置，零存款由测试钉死）。

复合写原子性（§7.1/D24）：全部「表行 + 账本事件」操作在单一
:func:`kernel.ledger.transaction`（BEGIN IMMEDIATE，可重入）边界内执行——
内部嵌套调用（expire_run → transition_finding）沿用外层边界，异常整体回滚。

确定性（§6.5）：全部函数零随机性；一切集合展开按字典序（finding 收尾序、
事件落账序）；时间由注入时钟签发。

范围边界（DECISIONS 2026-10-10 dispatch 行 + 同日接线行；2026-10-11 L1 处置行）：
§2.5 蜂产出代落账 ``submit()`` 统一入口四分支已落位（scout 三角化回填 / auditor
建档 + SchemaRejected 退回 + 错配自动 re_route / reviewer 自评守卫 + 票满自动
close_panel / verifier §10.4 改判守卫 + S2 状态映射与存款传播），复用 open_finding
/ transition_finding（本模块）与 close_panel / propagate_verdict（kernel.cluster，
环入径经函数内局部 import——cluster 反向 import 本模块）。仿真 verdict 归属仍按
SPEC-2 §0.4 以 attribute_findings 编排接通（窄口不动，M0 轨迹零影响）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Mapping

from qswarm.contracts import (
    FindingPayload,
    FindingState,
    LedgerKind,
    RunMode,
    SignalTier,
    VerifyErrorKind,
    VerifyOutcome,
    load_params,
)
from qswarm.contracts.models import (
    AuditorFinding,
    DispatchContract,
    DispatchPayload,
    ReDispatchPayload,
    ReviewerVote,
    ScoutReport,
    VerifierReport,
    VerifyResultPayload,
    VotePayload,
)
from .ledger import KernelLedger, LedgerEvent, iso_utc, transaction
from .reroute import is_mismatch, re_route_finding

if TYPE_CHECKING:  # 仅注解用
    from qswarm.contracts import ParamsConfig
    from .cluster import VerdictContext
    from .route import RouteDecision

__all__ = [
    "DISPATCH_MAX_ATTEMPTS",
    "FINDING_TRANSITIONS",
    "HOP_BUDGET_BY_MODE",
    "NON_TERMINAL_FINDING_STATES",
    "ROLE_OUTPUT_SCHEMA",
    "ROLE_TASK_BOUNDS",
    "ROLE_TOOL_BOUNDS",
    "TASK_STATE_TERMINAL",
    "SchemaRejected",
    "attach_cluster",
    "build_contract",
    "create_task",
    "dispatch",
    "expire_run",
    "hop_budget_for",
    "open_finding",
    "open_run",
    "re_dispatch",
    "record_panel_consensus",
    "register_candidates",
    "submit",
    "transition_finding",
]


# ── ②③④ 角色缺省表（SPEC-2 §2.2 逐字；SPEC-3 提示词模板细化时引用同一文案源）──

#: ② 输出格式：role → SPEC-1 §4.2 输出契约名（queen_advisor 不派产 finding 任务，
#: §16.7-9 只产结构化仲裁摘要建议稿，落账仍走内核）。queen_advisor 行为 SPEC-3 §6
#: 契约面登记（DECISIONS 2026-10-11：先行注册防 M3 契约漂移）——三表仅本表含
#: advisor，build_contract 的三表合取校验对 advisor 仍拒绝（顾问永不进派单状态机）。
ROLE_OUTPUT_SCHEMA: Mapping[str, str] = {
    "scout": "ScoutReport",
    "auditor": "AuditorFinding",
    "reviewer": "ReviewerVote",
    "verifier": "VerifierReport",
    "queen_advisor": "ArbitrationDraft",
}

#: ③ 工具与信息源边界：§4.1 权限边界列的机器可读形态（只读仓 / repo map 只读 /
#: finding 与工件只读 / 沙箱可写+仓只读）。verifier 的 sandbox:rw 仅指隔离沙箱，
#: 永不触生产（§4.1）。
ROLE_TOOL_BOUNDS: Mapping[str, tuple[str, ...]] = {
    "scout": ("repo:ro",),
    "auditor": ("repo:ro", "repomap:ro"),
    "reviewer": ("finding:ro", "artifact:ro"),
    "verifier": ("repo:ro", "sandbox:rw"),
}

#: ④ 任务边界缺省模板（一句话；SPEC-3 提示词模板细化时引用同一文案源）。
ROLE_TASK_BOUNDS: Mapping[str, str] = {
    "scout": "只分诊给定候选点并给出类型猜测；不做深挖、不产 finding。",
    "auditor": "只分析派单节点指名的可疑点×类型并产带证据链 finding；不扩面、不改结论分级。",
    "reviewer": "只按四维独立打票；不复用报告者结论、不产新 finding。",
    "verifier": "只对指名簇做沙箱复现并产三分类结构化结果；不投票、不产 finding。",
}

#: 深挖跳数（§2.2 档位表逐字：fast=0 / pr=1 / full=2；§2.1 repo map 按
#: 「候选点 ± N 跳邻域子图」注入 L2 上下文）。fast 档跳数预算天生为 0（耗尽态：
#: 不做任何深挖）；预算熔断降档（§4.3）后新判定按低档值取。
HOP_BUDGET_BY_MODE: Mapping[str, int] = {"fast": 0, "pr": 1, "full": 2}

#: 单任务派单次数上限 = 初派 1 + 重试 1（§16.7-8 逐字「timeout → 重试 1 次 → 改派
#: 他人」；模块常量不进 params，extra=forbid 纪律）。
DISPATCH_MAX_ATTEMPTS: int = 2

#: Finding 结论状态机迁移表（SPEC-2 §2.4 = §10.2 图逐字 + 一条 S3 直达扩展，
#: DECISIONS 2026-10-10-D8：open→confirmed/rejected 由 S3 直答触发——§12 入口语义
#: 「标记一个 finding 真假 10 秒内可完成」允许人对未投票 finding 直答；除该扩展外
#: 不新增任何边）。CONFIRMED / REJECTED / EXPIRED 为终态（出边空集）。
FINDING_TRANSITIONS: Mapping[FindingState, frozenset[FindingState]] = {
    FindingState.OPEN: frozenset(
        {FindingState.VOTED, FindingState.CONFIRMED, FindingState.REJECTED, FindingState.EXPIRED}
    ),
    FindingState.VOTED: frozenset(
        {
            FindingState.VERIFIED,
            FindingState.INCONCLUSIVE,
            FindingState.CONFIRMED,
            FindingState.REJECTED,
            FindingState.EXPIRED,
        }
    ),
    FindingState.INCONCLUSIVE: frozenset(
        {FindingState.CONFIRMED, FindingState.REJECTED, FindingState.EXPIRED}
    ),
    FindingState.VERIFIED: frozenset(
        {FindingState.CONFIRMED, FindingState.REJECTED, FindingState.EXPIRED}
    ),  # S3 推翻/确认（§6.2）
    FindingState.CONFIRMED: frozenset(),  # 终态
    FindingState.REJECTED: frozenset(),   # 终态
    FindingState.EXPIRED: frozenset(),    # 终态
}

#: 非终态集（D19）：迁移表出边非空的状态 = {open, voted, inconclusive, verified}。
#: expire_run 的收尾覆盖集由此派生（读 :data:`FINDING_TRANSITIONS` 运行期值，
#: 与迁移表恒一致）。verdict 非终态的机制依据：VERIFIED→EXPIRED 出边非空。
NON_TERMINAL_FINDING_STATES: frozenset[FindingState] = frozenset(
    s for s, outs in FINDING_TRANSITIONS.items() if outs
)

#: tasks.state 终态词表（§2.3 状态机：done | failed；queued/dispatched 为非终态）。
TASK_STATE_TERMINAL: frozenset[str] = frozenset({"done", "failed"})


# ── 四件套装配（§2.2；纯函数零 IO）───────────────────────────────

def hop_budget_for(mode: RunMode | str) -> int:
    """档位 → 深挖跳数（§2.2 逐字表）；未知档位 → ValueError。"""
    key = str(mode)
    try:
        return HOP_BUDGET_BY_MODE[key]
    except KeyError:
        raise ValueError(
            f"未知档位：{mode!r}（合法值：{sorted(HOP_BUDGET_BY_MODE)}，§2.2 档位表）"
        ) from None


def build_contract(
    task,
    decision: "RouteDecision",
    *,
    mode: RunMode | str,
    deadline_s: int,
    node: str | None = None,            # 缺省 = decision.node
    context_refs=(),                    # artifact:// 或 repo 路径+行窗
    goal: str | None = None,            # 缺省 = f"对 {node} 以 {role} 职责完成本次分片"
    task_bounds: str | None = None,     # 缺省 = ROLE_TASK_BOUNDS[role]
) -> DispatchContract:
    """四件套装配（§4.3；SPEC-1 §4.1 DispatchContract 直出）。纯函数零 IO：

    ①goal ②output_schema_ref=ROLE_OUTPUT_SCHEMA[role] ③tool_bounds=ROLE_TOOL_BOUNDS[role]
    ④task_bounds；hop_budget=hop_budget_for(mode)；taxonomy_node=node。

    ``task`` 为分片载体（鸭子类型，保留 §2.2 签名形态；缺省四件套全部由
    ``decision`` 派生，shard 细节由 context_refs 以 artifact:// 引用携带——工蜂
    产出只回传引用，输入同样只给引用，§4.3）。queen_advisor 与未知角色不在
    角色表内（不派产 finding 任务）→ ValueError。
    """
    role = str(decision.role)
    node = decision.node if node is None else node
    if role not in ROLE_OUTPUT_SCHEMA or role not in ROLE_TOOL_BOUNDS or role not in ROLE_TASK_BOUNDS:
        raise ValueError(
            f"未知派单角色：{role!r}（四件套角色表仅含 {sorted(ROLE_OUTPUT_SCHEMA)}；"
            "queen_advisor 不派产 finding 任务，§4.2/§16.7-9）"
        )
    return DispatchContract(
        task_id=decision.task_id,
        role=role,                                        # pydantic 收敛为 Role
        goal=goal if goal is not None else f"对 {node} 以 {role} 职责完成本次分片",
        output_schema_ref=ROLE_OUTPUT_SCHEMA[role],
        tool_bounds=list(ROLE_TOOL_BOUNDS[role]),
        task_bounds=task_bounds if task_bounds is not None else ROLE_TASK_BOUNDS[role],
        context_refs=list(context_refs),
        taxonomy_node=node,
        hop_budget=hop_budget_for(mode),
        deadline_s=int(deadline_s),
    )


# ── L0 候选点装配口（§6.1：candidates 表写口在本模块；SPEC-7 §2 接线消费）──


def register_candidates(
    ledger: KernelLedger,
    *,
    run_id: str,
    candidates,  # Sequence[perception.candidates.Candidate]（鸭子类型，防 kernel→perception 依赖）
) -> list[str]:
    """L0 候选点入表（candidates 表 INSERT 唯一写口 = 本模块，§6.1）。

    - 输入为 perception Candidate 形态（鸭子类型：``candidate_id/path/span/kind/
      channels/taxonomy_hint/clue`` 属性；:data:`L0Channel` 词表映射 A→struct、
      B→model、C→rule，``channels`` 列逗号串按词表序）；
    - ``candidate_id`` = 'CD-<run>-<L0 序>'？非——perception Candidate 自带内容
      寻址 id（blake2b 16B hex，SPEC-7 §1.1/D27），直接作主键（表 TEXT PK 无
      形态约束；``CD-`` 序号形态归 M0 世界侧仿真候选，两形态并存、内容寻址 id
      自明出处）；
    - 幂等（读侧）：同 candidate_id 且字段全等 → 跳过；字段不等 → ValueError
      （写侧非法，§6.4 界线；同 roster 幂等口径）；
    - channel_score = 候选优先级（多通道命中加权，§2.1；归并层值直落）；
      rule_id = kind=rule_hit 时的规则 id（clue 承载）；guess_family = 
      taxonomy_hint（L0 初始猜测，侦察回填走 submit 的 COALESCE 语义）；
    - 全部行同事务落库（§7.1 复合写原子性；行插入无账本事件——创建即初态，
      与 create_task 同型纪律）。"""
    _channel_word = {"A": "struct", "B": "model", "C": "rule"}
    inserted: list[str] = []
    with transaction(ledger):
        for cand in candidates:
            channels = ",".join(
                _channel_word[c] for c in sorted(getattr(cand, "channels"))
            )
            span = getattr(cand, "span")
            cid = str(getattr(cand, "candidate_id"))
            row = ledger.conn.execute(
                "SELECT path, line_start, line_end, channels FROM candidates"
                " WHERE candidate_id = ?",
                (cid,),
            ).fetchone()
            if row is not None:
                if (
                    str(row[0]) != str(getattr(cand, "path"))
                    or int(row[1]) != int(span[0])
                    or int(row[2]) != int(span[1])
                    or str(row[3]) != channels
                ):
                    raise ValueError(
                        f"候选点身份冲突：candidate_id={cid!r} 已存在且字段不等"
                        "（人工介入，§6.4）"
                    )
                continue
            kind = str(getattr(cand, "kind"))
            hint = getattr(cand, "taxonomy_hint", None)
            ledger.conn.execute(
                "INSERT INTO candidates (candidate_id, run_id, path, line_start,"
                " line_end, channels, channel_score, rule_id, guess_family,"
                " guess_type, triaged, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)",
                (
                    cid, run_id, str(getattr(cand, "path")), int(span[0]), int(span[1]),
                    channels, float(getattr(cand, "priority")),
                    str(getattr(cand, "clue")) if kind == "rule_hit" else None,
                    str(hint) if hint else None,
                    _now_iso(ledger),
                ),
            )
            inserted.append(cid)
    return inserted


# ── run 生命周期入口（§6.1：runs 表写口在本模块；findings.run_id 外键的前件）──


def open_run(
    ledger: KernelLedger,
    *,
    run_id: str,
    mode: str = "full",
    repo_url: str = "sim://world",
    commit_sha: str | None = None,
    diff_ref: str | None = None,
    budget_cents: int = 0,
    status: str = "running",
    meta_json: str = "{}",
) -> str:
    """装配 runs 行（run 生命周期入口；findings/tasks 外键前件，§6.1）。

    - 仅插入行、不落账本事件（与 create_task 同型：进入派单/建档视界的审计锚是
      随后的 DISPATCH/FINDING 事件，创建即初态）；
    - 幂等（读侧）：同 run_id 同身份（mode/budget_cents）重复打开 → 原样返回；
      同 run_id 不同 mode → ValueError（写侧非法，run 身份不可静默改写）；
    - ``budget_cents`` 为建立时快照（§2.4 判定以 policy.budget_cents 为准，D18：
      无 BudgetPolicy 注入 = 无预算约束路径，M0 仿真轨迹逐字兼容）。
    """
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT mode, budget_cents FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is not None:
            if str(row[0]) != str(mode):
                raise ValueError(
                    f"run {run_id!r} 已存在且 mode 不一致：{row[0]!r} ≠ {mode!r}"
                )
            return run_id
        ledger.conn.execute(
            "INSERT INTO runs (run_id, mode, repo_url, commit_sha, diff_ref,"
            " budget_cents, spent_cents, status, started_at, finished_at, meta_json)"
            " VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, ?)",
            (
                run_id, str(mode), repo_url, commit_sha, diff_ref,
                int(budget_cents), str(status), _now_iso(ledger), meta_json,
            ),
        )
    return run_id


# ── 任务行装配口（§6.1：tasks 表唯一写口在本模块）────────────────

def _now_iso(ledger: KernelLedger) -> str:
    """行时间戳取注入时钟（包内协作面：内核一切 ts 由内核签发，蜂无时间源）。"""
    return iso_utc(ledger._clock.now())


def _task_row(ledger: KernelLedger, task_id: str) -> dict | None:
    row = ledger.conn.execute(
        "SELECT task_id, run_id, role, node, state, assignee, attempts FROM tasks"
        " WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ("task_id", "run_id", "role", "node", "state", "assignee", "attempts")
    return dict(zip(keys, row))


def create_task(
    ledger: KernelLedger,
    *,
    run_id: str,
    role: str,
    node: str,
    shard,
    task_id: str | None = None,
) -> str:
    """装配一条 queued 任务行（§2.3 状态机入口前件；tasks 写口在本模块，§6.1）。

    task_id 缺省按 ``T-<run_id>-<seq>`` 签发，seq = 同 run 前缀库内已有最大序号 +1
    （D20 同域自增口径）；``shard`` 为 JSON 可序列化分片规格（键序规范化后落
    shard_json，确定性）。仅插入 queued 行，不落账本事件——任务行进入派单视界的
    审计锚是随后的 DISPATCH 事件（§6.2 状态「变更」指既有行迁移；创建即初态）。
    """
    if task_id is None:
        prefix = f"T-{run_id}-"
        seq = 0
        for r in ledger.conn.execute(
            "SELECT task_id FROM tasks WHERE task_id LIKE ?", (prefix + "%",)
        ):
            tail = str(r[0])[len(prefix):]
            if tail.isdigit():
                seq = max(seq, int(tail))
        task_id = f"{prefix}{seq + 1:03d}"
    in_txn = ledger.conn.in_transaction  # D24：外层事务内不自行提交（边界统一提交）
    ledger.conn.execute(
        "INSERT INTO tasks (task_id, run_id, role, node, shard_json, state, assignee,"
        " attempts, dispatched_at, created_at) VALUES (?, ?, ?, ?, ?, 'queued', NULL, 0, NULL, ?)",
        (task_id, run_id, str(role), node, json.dumps(shard, sort_keys=True, ensure_ascii=False), _now_iso(ledger)),
    )
    if not in_txn:
        ledger.conn.commit()
    return task_id


# ── 任务派单状态机（§2.3）────────────────────────────────────────

def dispatch(
    ledger: KernelLedger,
    task_id: str,
    contract: DispatchContract,
    decision: "RouteDecision",
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """queued → dispatched：tasks 行（assignee / state='dispatched' / dispatched_at /
    attempts=1）+ DISPATCH 事件，同事务。

    - tasks.state ≠ 'queued' → ValueError（重复派单被状态守卫拒绝，§6.4 幂等；
      写侧非法 → ValueError，§6.4 界线）；
    - decision.assignee is None（empty_pool）→ 仍落 DISPATCH 事件（assignee=null，
      审计痕迹），tasks **留 queued**、attempts 不消耗；改派由调用方重入（D10）；
    - contract/decision 与 task_id 不一致 → ValueError（装配面完整性守卫）。
    """
    if decision.task_id != task_id or contract.task_id != task_id:
        raise ValueError(
            f"派单面不一致：task_id={task_id!r}，decision.task_id={decision.task_id!r}，"
            f"contract.task_id={contract.task_id!r}"
        )
    if str(contract.role) != str(decision.role):
        raise ValueError(
            f"派单面不一致：contract.role={contract.role!r} ≠ decision.role={decision.role!r}"
        )
    row = _task_row(ledger, task_id)
    if row is None:
        raise ValueError(f"任务不存在：{task_id!r}")
    if row["state"] != "queued":
        raise ValueError(
            f"任务 {task_id!r} 状态为 {row['state']!r}（≠ queued），重复派单被状态守卫拒绝（§6.4）"
        )
    payload = DispatchPayload(
        task_id=task_id,
        role=str(decision.role),
        node=decision.node,
        assignee=decision.assignee,
        hop_budget=contract.hop_budget,
    )
    with transaction(ledger):
        if decision.assignee is not None:
            ledger.conn.execute(
                "UPDATE tasks SET assignee = ?, state = 'dispatched', dispatched_at = ?,"
                " attempts = 1 WHERE task_id = ?",
                (decision.assignee, _now_iso(ledger), task_id),
            )
        # assignee=None（empty_pool，D10）：tasks 留 queued、attempts 不消耗，
        # 仅落 assignee=null 的 DISPATCH 事件留审计痕迹。
        ev = ledger.append(
            LedgerKind.DISPATCH, payload, run_id=run_id if run_id is not None else row["run_id"]
        )
    return ev


def re_dispatch(
    ledger: KernelLedger,
    task_id: str,
    *,
    to_agent: str,
    reason: str,
    run_id: str | None = None,
) -> LedgerEvent:
    """重派（§16.7-8）：tasks.attempts < DISPATCH_MAX_ATTEMPTS → tasks 重派
    （assignee=to_agent、attempts+1、state='dispatched'）+ RE_DISPATCH 事件，同事务；
    attempts 超限 → tasks.state='failed' 并抛 ValueError（重试通道关闭，零存款）。

    守卫（写侧非法 → ValueError，§6.4 界线）：任务不存在 / reason ∉
    {timeout, schema_fail}（ReDispatchPayload 词表）/ state ≠ 'dispatched'
    （queued 走 :func:`dispatch`，终态不可流转）。

    超限分支的实现语义（DECISIONS 2026-10-10 dispatch 行）：同事务先落
    RE_DISPATCH 审计事件（to_agent=from_agent：未发生改派、通道关闭的可重放锚，
    §6.2「不存在无账本事件的状态变更」）再置 tasks.state='failed'，边界提交后
    抛 ValueError——行不永悬 dispatched、调用方仍收到异常，零存款。
    """
    if reason not in ("timeout", "schema_fail"):
        raise ValueError(
            f"非法重派原因：{reason!r}（ReDispatchPayload.reason 词表：timeout|schema_fail）"
        )
    row = _task_row(ledger, task_id)
    if row is None:
        raise ValueError(f"任务不存在：{task_id!r}")
    if row["state"] != "dispatched":
        raise ValueError(
            f"任务 {task_id!r} 状态为 {row['state']!r}（≠ dispatched），不可重派"
            "（queued 走 dispatch()，终态不可流转，§2.3 状态机）"
        )
    attempts = int(row["attempts"])
    assignee = row["assignee"]
    if attempts >= DISPATCH_MAX_ATTEMPTS:
        with transaction(ledger):
            ledger.append(
                LedgerKind.RE_DISPATCH,
                ReDispatchPayload(
                    task_id=task_id, from_agent=assignee, to_agent=assignee, reason=reason
                ),
                run_id=run_id if run_id is not None else row["run_id"],
            )
            ledger.conn.execute(
                "UPDATE tasks SET state = 'failed' WHERE task_id = ?", (task_id,)
            )
        raise ValueError(
            f"任务 {task_id!r} 重派通道已关闭：attempts={attempts} ≥"
            f" DISPATCH_MAX_ATTEMPTS={DISPATCH_MAX_ATTEMPTS}（§16.7-8；tasks 置 failed，零存款）"
        )
    with transaction(ledger):
        ledger.conn.execute(
            "UPDATE tasks SET assignee = ?, attempts = attempts + 1, state = 'dispatched',"
            " dispatched_at = ? WHERE task_id = ?",
            (to_agent, _now_iso(ledger), task_id),
        )
        ev = ledger.append(
            LedgerKind.RE_DISPATCH,
            ReDispatchPayload(
                task_id=task_id, from_agent=assignee, to_agent=to_agent, reason=reason
            ),
            run_id=run_id if run_id is not None else row["run_id"],
        )
    return ev


def expire_run(
    ledger: KernelLedger,
    run_id: str,
    *,
    reason: str = "budget_breaker",
    params: "ParamsConfig | None" = None,
) -> list[LedgerEvent]:
    """run 收尾过期（§2.4「未跑项标记 expired（零存款）」× §10.2 任意状态→expired）：

    - 该 run 下全部非终态 finding（state ∈ {open, voted, inconclusive, verified}；
      D19：「非终态」= 迁移表出边非空，与 §2.4 迁移表 VERIFIED→EXPIRED 对齐）按
      finding_id 字典序逐个 transition_finding(EXPIRED)——**零存款**（本函数路径
      不产任何 deposit，负断言由测试钉死）；
    - 对应 tasks 行 state ∈ {queued, dispatched} → 'failed'（同事务；任务侧终局
      的审计锚是同事务内的 finding 流转事件与调用方预算侧 SCALING 事件（D17），
      词表冻结、无任务终态专用 kind——DECISIONS 2026-10-10 dispatch 行）；
    - 幂等：全部已终态 → []（读侧幂等：重复触发同一业务事实 → 跳过，§6.4）。

    ``reason`` 为调用方审计语境（预算熔断/超时），不单独落载荷——run 级原因由
    budget.on_cost 的 SCALING 事件承载；``params`` 为规格签名保留位（§2.3），
    收尾语义与参数无关。
    """
    non_terminal = tuple(
        s.value for s in FINDING_TRANSITIONS if FINDING_TRANSITIONS[s]
    )
    events: list[LedgerEvent] = []
    with transaction(ledger):
        rows = ledger.conn.execute(
            "SELECT id, run_id FROM findings WHERE run_id = ? AND state IN"
            f" ({', '.join('?' * len(non_terminal))}) ORDER BY id",
            (run_id, *non_terminal),
        ).fetchall()
        for (finding_id, finding_run) in rows:
            events.append(
                transition_finding(
                    ledger, finding_id, FindingState.EXPIRED,
                    run_id=run_id if run_id is not None else finding_run,
                )
            )
        ledger.conn.execute(
            "UPDATE tasks SET state = 'failed' WHERE run_id = ? AND state IN"
            " ('queued', 'dispatched')",
            (run_id,),
        )
    return events


# ── Finding 结论状态机（§2.4 / §10.2）───────────────────────────

def transition_finding(
    ledger: KernelLedger,
    finding_id: str,
    to_state: FindingState,
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """结论状态机流转：迁移表守卫（非法迁移 / 终态再流转 → ValueError）；
    findings.state 列更新 + kind=finding 事件（payload.state=to_state）同事务。

    - 状态事件载体裁定（DECISIONS 2026-10-10-D3）：流转落 kind=finding 事件，
      payload.state 承载新状态——不新增 LedgerKind（枚举词表被 tests/test_enums.py
      ast 钉死）；重放规则 = 取每 finding 最后一条 finding 事件的 state（§6.3）；
    - **expired 零存款**：本函数从不产 deposit（§10.2 簧语义：不产生「没验证=负面」
      偏置）；零存款由测试断言（§9）；
    - run_id 缺省取 findings 行的 run_id（事件归属与行一致，重放可按 run 裁剪）。
    """
    to_state = FindingState(to_state)
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT state, reporter, cluster_id, run_id FROM findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"finding 不存在：{finding_id!r}")
        cur_state, reporter, cluster_id, row_run = row
        cur = FindingState(cur_state)
        allowed = FINDING_TRANSITIONS.get(cur)
        if allowed is None:
            raise ValueError(f"finding {finding_id!r} 处于未知状态：{cur_state!r}")
        if to_state not in allowed:
            hint = "（终态不可再流转）" if not allowed else ""
            raise ValueError(
                f"非法结论迁移：{finding_id!r} {cur.value} → {to_state.value}{hint}"
                "（§10.2 迁移表守卫 + D8 直达边）"
            )
        ledger.conn.execute(
            "UPDATE findings SET state = ?, updated_at = ? WHERE id = ?",
            (to_state.value, _now_iso(ledger), finding_id),
        )
        ev = ledger.append(
            LedgerKind.FINDING,
            FindingPayload(
                finding_id=finding_id,
                reporter=reporter,
                cluster=cluster_id,
                state=to_state,
            ),
            run_id=run_id if run_id is not None else row_run,
        )
    return ev


# ── findings 投影列的聚类侧写口（SPEC-2 §6.1：findings 唯一写口 = 本模块）──


def attach_cluster(
    ledger: KernelLedger,
    finding_id: str,
    cluster_id: str,
    *,
    updated_at: str | None = None,
) -> None:
    """findings.cluster_id 归属回写（聚类归并的存款归属衔接：占位簇号由内核侧替换）。

    apply_merges（建簇/扩簇）与 split_cluster（成员随迁）的成员表改写配套调用。
    纯投影列更新、不落账本事件——归属变更的账本锚是同事务的 cluster_merge /
    cluster_split 事件（成员真源 = 事件增量，D5），重放按 merge 事件折叠重建该列
    （kernel.replay.rebuild_findings）。``updated_at`` 缺省取注入时钟当下；调用方
    传事件 ts 时与表行时间锚一致（CL3）。
    """
    with transaction(ledger):
        cur = ledger.conn.execute(
            "UPDATE findings SET cluster_id = ?, updated_at = ? WHERE id = ?",
            (cluster_id, updated_at if updated_at is not None else _now_iso(ledger),
             finding_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"finding 不存在：{finding_id!r}")


def open_finding(
    ledger: KernelLedger,
    *,
    finding_id: str,
    run_id: str,
    reporter: str,
    reporter_role: str,
    family: str,
    type: str | None,
    lang: str,
    repo: str,
    path: str,
    line_start: int,
    line_end: int,
    title: str,
    severity: str,
    grade: str = "L1",
    func: str | None = None,
    commit_sha: str = "",
    cwe: str | None = None,
) -> LedgerEvent:
    """finding 建档（§6.1 findings 表唯一写口在本模块；SPEC-2 §2.5 submit(auditor)
    「findings 行落库 + finding 事件(state=open)」的建档切片，SPEC-2 §0.4 仿真接通
    「引擎调用 kernel.dispatch」的入口）：

    - findings 行（state='open'、cluster_id=NULL、confidence=0.5）+ kind=finding
      事件（payload.cluster=None——归属尚未发生；cluster_id 由聚类归并经
      :func:`attach_cluster` 回写，事件真源 = cluster_merge.members，CL3 重放口径）
      同事务（§7.1 不存在半写态）；
    - finding_id 由调用方携带（世界侧 F-SIM-<t> 自成前缀，D20；内核签发的
      ``F-{date}-{seq}`` 归 §2.5 submit(auditor)）；重复建档 → ValueError（写侧
      非法，§6.4 界线；TEXT PRIMARY KEY 库级兜底）；
    - state/grade 初始值即 §10.1/§10.2 入口态：state='open'、grade 缺省 L1
      （结论升级由后续状态机与 S2 映射承载，本函数不做任何结论判断）。
    """
    with transaction(ledger):
        if ledger.conn.execute(
            "SELECT 1 FROM findings WHERE id = ?", (finding_id,)
        ).fetchone() is not None:
            raise ValueError(f"finding 重复建档：{finding_id!r}（findings.id 主键）")
        now = _now_iso(ledger)
        ledger.conn.execute(
            "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
            " type, lang, repo, path, func, line_start, line_end, commit_sha,"
            " cluster_id, title, severity, cwe, confidence, grade, state,"
            " disagreement_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, 0.5, ?,"
            " 'open', NULL, ?, ?)",
            (
                finding_id, run_id, reporter, str(reporter_role), family, type,
                lang, repo, path, func, int(line_start), int(line_end),
                commit_sha, title, severity, cwe, str(grade), now, now,
            ),
        )
        ev = ledger.append(
            LedgerKind.FINDING,
            FindingPayload(finding_id=finding_id, reporter=reporter, cluster=None,
                           state=FindingState.OPEN),
            run_id=run_id,
        )
    return ev


def record_panel_consensus(
    ledger: KernelLedger,
    finding_id: str,
    *,
    confidence: float,
    disagreement_json: str | None,
    updated_at: str | None = None,
) -> None:
    """§10.3 panel 收口的 findings 共识投影写入（confidence / disagreement_json）。

    close_panel（kernel.cluster）在同一事务内调用；状态流转本身走
    :func:`transition_finding`（open→voted 迁移守卫 + kind=finding 事件）。
    """
    with transaction(ledger):
        cur = ledger.conn.execute(
            "UPDATE findings SET confidence = ?, disagreement_json = ?, updated_at = ?"
            " WHERE id = ?",
            (confidence, disagreement_json,
             updated_at if updated_at is not None else _now_iso(ledger),
             finding_id),
        )
        if cur.rowcount != 1:
            raise ValueError(f"finding 不存在：{finding_id!r}")


# ── 蜂产出代落账 submit（§2.5；§4.5 纪律 1：内核是唯一写手）────────


class SchemaRejected(ValueError):
    """② 输出格式不合格（§4.3「schema 校验，不合格直接退回」，SPEC-2 §2.5）。

    零落库（内核侧校验一律先于任何写语句）；调用方据 reason 走
    ``re_dispatch(schema_fail)`` 或 failed。pydantic 模型级校验（字段/枚举/区间）
    在输出模型构造时完成；本异常承载内核侧的跨字段契约校验（证据链构成、
    verify_error 的 error_kind 配套、未知输出类型）。
    """


#: evidence_nodes.kind 词表（SPEC-1 §2 DDL 注记逐字；越表即 ② 不合格）。
_EVIDENCE_KINDS: frozenset[str] = frozenset(
    {"source", "propagation", "sink", "sanitizer"}
)


def _submitted_task(ledger: KernelLedger, task_id: str) -> dict:
    """submit 前件：任务行存在且 state='dispatched'（§2.5「task 非 dispatched 态
    → ValueError」；queued 走 dispatch()，终态不可产出代落账）。"""
    row = ledger.conn.execute(
        "SELECT task_id, run_id, role, state, assignee, shard_json FROM tasks"
        " WHERE task_id = ?",
        (task_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"任务不存在：{task_id!r}")
    keys = ("task_id", "run_id", "role", "state", "assignee", "shard_json")
    task = dict(zip(keys, row))
    if task["state"] != "dispatched":
        raise ValueError(
            f"任务 {task_id!r} 状态为 {task['state']!r}（≠ dispatched），"
            "不可代落账（§2.5/§2.3 状态机：dispatched ──submit() 全量代落账成功──→ done）"
        )
    return task


def _task_shard(task: dict) -> dict:
    """tasks.shard_json 反序列化（create_task 落库时已键序规范化）。"""
    raw = task["shard_json"]
    return json.loads(raw) if raw else {}


def _next_finding_id(ledger: KernelLedger) -> str:
    """F-{date}-{seq:06d}：seq = 同日期前缀在账本库内已有最大序号 + 1（D20 同
    §1.3 口径：自增域 = 账本库而非 run；单 run 独库时与「run 内自增」逐位同值）。
    世界侧 F-SIM-<t> 前缀天然不参与同日期前缀匹配。须在事务内调用。"""
    date = ledger._clock.now().strftime("%Y%m%d")
    prefix = f"F-{date}-"
    max_seq = 0
    for (fid,) in ledger.conn.execute(
        "SELECT id FROM findings WHERE id LIKE ?", (prefix + "%",)
    ):
        try:
            max_seq = max(max_seq, int(str(fid)[len(prefix):]))
        except ValueError:
            continue
    return f"F-{date}-{max_seq + 1:06d}"


def _mark_task_done(ledger: KernelLedger, task_id: str) -> None:
    """§2.3 状态机：submit 全量代落账成功 → tasks.state='done'（同事务投影更新）。

    无任务终态专用 kind（词表冻结，expire_run 同口径 DECISIONS 2026-10-10 dispatch
    行）——终局的审计锚 = 同事务内的分支业务事件（finding/vote/verify_result）；
    scout 分支无账本事件（candidates 投影回填，DECISIONS 2026-10-11 submit 行）。
    """
    ledger.conn.execute(
        "UPDATE tasks SET state = 'done' WHERE task_id = ?", (task_id,)
    )


def _submit_scout(
    ledger: KernelLedger,
    task: dict,
    output: ScoutReport,
    contract: DispatchContract,
    ctx: "VerdictContext | None",
    params: "ParamsConfig | None",
    run: str | None,
) -> list[LedgerEvent]:
    """scout 分支（§2.5）：candidates.triaged=1、guess_family/guess_type 回填
    （candidates 表写口在本模块，§6.1）；injection_flag=True 按 §11.6-3 保持候选态
    并标高优先（不做任何丢弃）。

    - 投影回填无对应账本 kind（LedgerKind 词表被 tests/test_enums.py ast 钉死且
      §5 事件总表未为分诊设 kind）→ 本分支零事件（DECISIONS 2026-10-11 submit 行）；
    - 「标高优先」的机制形态（同上 DECISIONS 行）：candidates 无优先级列，落
      channel_score=1.0（多通道命中加权上界 = 最高优先，L2 排序消费面）；
    - 候选点不存在 → ValueError（写侧非法）。
    """
    with transaction(ledger):
        if ledger.conn.execute(
            "SELECT 1 FROM candidates WHERE candidate_id = ?", (output.candidate_id,)
        ).fetchone() is None:
            raise ValueError(f"候选点不存在：{output.candidate_id!r}")
        guess_family = (
            output.guess_family.value if output.guess_family is not None else None
        )
        # 回填语义：COALESCE 保留 L0 初始猜测列原值，仅以侦察蜂的非空猜测覆盖
        if output.injection_flag:
            ledger.conn.execute(
                "UPDATE candidates SET triaged = 1,"
                " guess_family = COALESCE(?, guess_family),"
                " guess_type = COALESCE(?, guess_type),"
                " channel_score = 1.0 WHERE candidate_id = ?",
                (guess_family, output.guess_type, output.candidate_id),
            )
        else:
            ledger.conn.execute(
                "UPDATE candidates SET triaged = 1,"
                " guess_family = COALESCE(?, guess_family),"
                " guess_type = COALESCE(?, guess_type)"
                " WHERE candidate_id = ?",
                (guess_family, output.guess_type, output.candidate_id),
            )
        _mark_task_done(ledger, task["task_id"])
    return []


def _submit_auditor(
    ledger: KernelLedger,
    task: dict,
    output: AuditorFinding,
    contract: DispatchContract,
    ctx: "VerdictContext | None",
    params: "ParamsConfig | None",
    run: str | None,
) -> list[LedgerEvent]:
    """auditor 分支（§2.5）：findings + evidence_nodes 行落库 + finding 事件
    (state=open)，错配自动 re_route（§3.1，含猜测格弱存款）。

    - 证据链内核侧校验先于一切写：节点数 ≥3 且 kind 含 source/sink 且 sanitizer
      必出现（SPEC-1 §4.2 注记），kind 越词表 → SchemaRejected 零落库；
    - finding_id 内核签发 F-{date}-{seq:06d}（D20 库内同前缀自增）；建档复用
      :func:`open_finding`（唯一实现纪律）；reporter=task.assignee、
      reporter_role=contract.role；行窗/语言/仓库来自 tasks.shard_json
      （path/line_start/line_end/lang 必填，repo/func/commit_sha 可选——repo 缺省
      回落 runs.repo_url；DECISIONS 2026-10-11 submit 行）；
    - 产出 (family[/type]) 与 contract.taxonomy_node 错配 → re_route_finding
      （scout_ids 取 ctx.scouts 谱系，D14；细化不是错配 → 不触发，D12）。
    """
    kinds = [node.kind for node in output.evidence_chain]
    unknown = sorted({k for k in kinds if k not in _EVIDENCE_KINDS})
    if unknown or len(kinds) < 3 or not {"source", "sink", "sanitizer"} <= set(kinds):
        raise SchemaRejected(
            f"证据链不合格：{len(kinds)} 节点（须 ≥3 且 kind 含"
            f" source/sink/sanitizer，词表外={unknown or '无'}；SPEC-1 §4.2 注记）"
        )
    shard = _task_shard(task)
    missing = [k for k in ("path", "line_start", "line_end", "lang") if shard.get(k) is None]
    if missing:
        raise ValueError(
            f"auditor 任务 {task['task_id']!r} 分片缺建档字段：{missing}"
            "（tasks.shard_json，DECISIONS 2026-10-11 submit 行）"
        )
    if task["assignee"] is None:
        raise ValueError(
            f"任务 {task['task_id']!r} 无 assignee（empty_pool 不产出代落账，D10）"
        )
    family = output.family.value
    type_ = output.type
    events: list[LedgerEvent] = []
    with transaction(ledger):
        if shard.get("repo") is None:
            runs_row = ledger.conn.execute(
                "SELECT repo_url FROM runs WHERE run_id = ?", (task["run_id"],)
            ).fetchone()
            repo = str(runs_row[0]) if runs_row is not None else ""
        else:
            repo = str(shard["repo"])
        finding_id = _next_finding_id(ledger)
        events.append(
            open_finding(
                ledger,
                finding_id=finding_id,
                run_id=run if run is not None else task["run_id"],
                reporter=str(task["assignee"]),
                reporter_role=str(contract.role),
                family=family,
                type=type_,
                lang=str(shard["lang"]),
                repo=repo,
                path=str(shard["path"]),
                line_start=int(shard["line_start"]),
                line_end=int(shard["line_end"]),
                title=output.title,
                severity=output.severity.value,
                grade="L1",
                func=shard.get("func"),
                commit_sha=str(shard.get("commit_sha") or ""),
                cwe=output.cwe,
            )
        )
        for ord_, node in enumerate(output.evidence_chain, start=1):
            ledger.conn.execute(
                "INSERT INTO evidence_nodes (id, finding_id, ord, kind, where_path,"
                " where_line, found, ref) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"EV-{finding_id}-{ord_}", finding_id, ord_, node.kind,
                    node.where_path, int(node.where_line), node.found, node.ref,
                ),
            )
        _mark_task_done(ledger, task["task_id"])
        to_node = f"{family}/{type_}" if type_ else family
        if is_mismatch(str(contract.taxonomy_node), to_node):
            events.extend(
                re_route_finding(
                    ledger,
                    finding_id,
                    from_node=str(contract.taxonomy_node),
                    to_node=to_node,
                    scout_ids=tuple(ctx.scouts) if ctx is not None else (),
                    run_id=run if run is not None else task["run_id"],
                )
            )
    return events


def _submit_reviewer(
    ledger: KernelLedger,
    task: dict,
    output: ReviewerVote,
    contract: DispatchContract,
    ctx: "VerdictContext | None",
    params: "ParamsConfig | None",
    run: str | None,
) -> list[LedgerEvent]:
    """reviewer 分支（§2.5）：votes 行 + vote 事件（同事务）；票数达
    params.panel.SIZE → 自动 close_panel（§1.5）。

    - 纪律 2 守卫先于一切写：reviewer（=task.assignee）== 该 finding.reporter
      → ValueError（库级 votes UNIQUE(finding_id, reviewer) 的内核提交守卫）；
    - 评审对象 finding_id 由任务分片携带（tasks.shard_json.finding_id——reviewer
      派单按 finding 1→N 装配，§7.2；DECISIONS 2026-10-11 submit 行）；
    - 收口需谱系 ctx（close_panel 共识与回校行同源，§1.5/D14）→ ctx=None ValueError；
    - vote_id = V-{finding}-{seq:03d}，seq = 该 finding 现有票数 + 1（DDL 注记
      'V-<finding>-<seq>'）。
    """
    if ctx is None:
        raise ValueError(
            "submit(reviewer) 需谱系 ctx（close_panel 共识与评审回校行同源，§1.5/D14）"
        )
    shard = _task_shard(task)
    finding_id = shard.get("finding_id")
    if not finding_id:
        raise ValueError(
            f"reviewer 任务 {task['task_id']!r} 分片缺 finding_id"
            "（tasks.shard_json，DECISIONS 2026-10-11 submit 行）"
        )
    if task["assignee"] is None:
        raise ValueError(
            f"任务 {task['task_id']!r} 无 assignee（empty_pool 不产出代落账，D10）"
        )
    reviewer = str(task["assignee"])
    events: list[LedgerEvent] = []
    with transaction(ledger):
        frow = ledger.conn.execute(
            "SELECT reporter FROM findings WHERE id = ?", (finding_id,)
        ).fetchone()
        if frow is None:
            raise ValueError(f"finding 不存在：{finding_id!r}")
        if reviewer == str(frow[0]):
            raise ValueError(
                f"纪律 2 违规：reviewer {reviewer!r} 即 finding {finding_id!r} 报告者，"
                "拒绝投票（§4.4；votes UNIQUE(finding_id, reviewer) 内核提交守卫）"
            )
        if ledger.conn.execute(
            "SELECT 1 FROM votes WHERE finding_id = ? AND reviewer = ?",
            (finding_id, reviewer),
        ).fetchone() is not None:
            raise ValueError(
                f"重复投票：{reviewer!r} 已对 {finding_id!r} 投票"
                "（votes UNIQUE(finding_id, reviewer)）"
            )
        seq = int(
            ledger.conn.execute(
                "SELECT COUNT(*) FROM votes WHERE finding_id = ?", (finding_id,)
            ).fetchone()[0]
        ) + 1
        ledger.conn.execute(
            "INSERT INTO votes (vote_id, finding_id, reviewer, dim_completeness,"
            " dim_reachability, dim_exploitability, dim_fp_suspicion, direction,"
            " comment_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"V-{finding_id}-{seq:03d}", finding_id, reviewer,
                float(output.dim_completeness), float(output.dim_reachability),
                float(output.dim_exploitability), float(output.dim_fp_suspicion),
                int(output.direction), output.comment_ref, _now_iso(ledger),
            ),
        )
        events.append(
            ledger.append(
                LedgerKind.VOTE,
                VotePayload(finding_id=finding_id, reviewer=reviewer,
                            direction=output.direction),
                run_id=run,
            )
        )
        _mark_task_done(ledger, task["task_id"])
        n_votes = seq
        p = params if params is not None else load_params()
        if n_votes >= int(p.panel.SIZE):
            from .cluster import close_panel  # 环入径：cluster 反向 import 本模块

            events.extend(
                close_panel(ledger, finding_id, ctx=ctx, params=p, run_id=run)
            )
    return events


def _submit_verifier(
    ledger: KernelLedger,
    task: dict,
    output: VerifierReport,
    contract: DispatchContract,
    ctx: "VerdictContext | None",
    params: "ParamsConfig | None",
    run: str | None,
) -> list[LedgerEvent]:
    """verifier 分支（§2.5）：verify_results 行 + verify_result 事件 + §2.4 S2
    状态映射 + propagate_verdict(S2)（§1.4），同事务。

    - §10.4 改判守卫：verified_fp 缺 blocking_evidence → 强制改判
      verify_error(error_kind=poc_fail)（不改蜂产出行，判定以内核映射为准）；
    - verify_error 缺 error_kind → SchemaRejected（§6.3 必填）；blocking 与
      非 verified_fp 并携 → SchemaRejected（与 simulator.verdict.S2Record 同口径）；
    - S2 映射（§2.4 表）：verified_real → 簇内全员 verified + grade→L3（§10.1）；
      verified_fp → rejected；verify_error → inconclusive 且零存款零投影更新
      （§6.2/§6.3）——状态流转经 :func:`transition_finding`（迁移表守卫）；
    - verified_real/verified_fp → propagate_verdict(tier=S2, source_seq=本
      verify_result 事件 seq, source_kind=VERIFY_RESULT, ctx)；需谱系 ctx
      （D14）→ ctx=None ValueError。
    """
    if ctx is None:
        raise ValueError(
            "submit(verifier) 需谱系 ctx（S2 存款归属展开，§1.4/D14）"
        )
    outcome = output.outcome
    error_kind = output.error_kind
    if outcome is VerifyOutcome.VERIFIED_FP and output.blocking is None:
        outcome = VerifyOutcome.VERIFY_ERROR       # §10.4 强制改判
        error_kind = VerifyErrorKind.POC_FAIL
    if outcome is VerifyOutcome.VERIFY_ERROR and error_kind is None:
        raise SchemaRejected("verify_error 缺 error_kind（§6.3 必填）→ 退回")
    if output.blocking is not None and outcome is not VerifyOutcome.VERIFIED_FP:
        raise SchemaRejected("blocking_evidence 仅 verified_fp 可携带（§10.4）→ 退回")
    cluster_id = str(output.cluster_id)
    events: list[LedgerEvent] = []
    with transaction(ledger):
        if ledger.conn.execute(
            "SELECT 1 FROM clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone() is None:
            raise ValueError(f"簇不存在：{cluster_id!r}")
        blocking = output.blocking
        ledger.conn.execute(
            "INSERT INTO verify_results (id, cluster_id, outcome, env_base, deps_ref,"
            " trigger_path_json, assertions_json, blocking_kind, blocking_where,"
            " blocking_ref, error_kind, attempts, duration_s, logs_ref, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"VR-{cluster_id}-{int(output.attempts)}", cluster_id,
                outcome.value, output.env_base, output.deps_ref,
                json.dumps([t for t in output.trigger_path], ensure_ascii=False),
                json.dumps(
                    [a.model_dump() for a in output.assertions], sort_keys=True,
                    ensure_ascii=False,
                ),
                blocking.kind.value if blocking is not None else None,
                blocking.where if blocking is not None else None,
                blocking.ref if blocking is not None else None,
                error_kind.value if error_kind is not None else None,
                int(output.attempts), float(output.duration_s), output.logs_ref,
                _now_iso(ledger),
            ),
        )
        ev = ledger.append(
            LedgerKind.VERIFY_RESULT,
            VerifyResultPayload(cluster_id=cluster_id, outcome=outcome,
                                error_kind=error_kind),
            run_id=run,
        )
        events.append(ev)
        members = [
            r[0] for r in ledger.conn.execute(
                "SELECT finding_id FROM cluster_members WHERE cluster_id = ?"
                " ORDER BY finding_id",
                (cluster_id,),
            )
        ]
        if outcome is VerifyOutcome.VERIFIED_REAL:
            for member in members:
                events.append(
                    transition_finding(ledger, member, FindingState.VERIFIED, run_id=run)
                )
                ledger.conn.execute(
                    "UPDATE findings SET grade = 'L3', updated_at = ? WHERE id = ?",
                    (_now_iso(ledger), member),
                )
        elif outcome is VerifyOutcome.VERIFIED_FP:
            for member in members:
                events.append(
                    transition_finding(ledger, member, FindingState.REJECTED, run_id=run)
                )
        else:  # VERIFY_ERROR：inconclusive，零存款零投影更新（§6.2/§6.3）
            for member in members:
                events.append(
                    transition_finding(
                        ledger, member, FindingState.INCONCLUSIVE, run_id=run
                    )
                )
        if outcome is not VerifyOutcome.VERIFY_ERROR:
            from .cluster import propagate_verdict  # 环入径：cluster 反向 import 本模块

            events.extend(
                propagate_verdict(
                    ledger,
                    cluster_id,
                    tier=SignalTier.S2,
                    direction=1 if outcome is VerifyOutcome.VERIFIED_REAL else -1,
                    source_seq=ev.seq,
                    source_kind=LedgerKind.VERIFY_RESULT,
                    ctx=ctx,
                    params=params,
                    run_id=run,
                )
            )
        _mark_task_done(ledger, task["task_id"])
    return events


def submit(
    ledger: KernelLedger,
    task_id: str,
    output: "ScoutReport | AuditorFinding | ReviewerVote | VerifierReport",
    *,
    contract: DispatchContract,
    ctx: "VerdictContext | None" = None,
    params: "ParamsConfig | None" = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """代落账（§2.5）：pydantic 校验（构造时）→ 表行 + 账本事件 + 状态机流转，
    单事务（§7.1/D24）。按 output 类型分发四分支——scout/auditor/reviewer/verifier
    分见 :func:`_submit_scout` / :func:`_submit_auditor` / :func:`_submit_reviewer`
    / :func:`_submit_verifier`；未知输出类型 → SchemaRejected。

    - 前件守卫先于一切写：任务不存在 / state ≠ 'dispatched' → ValueError（§2.5；
      dispatch() 落派、产出代落账收口的同一状态机）；
    - 全量代落账成功 → tasks.state='done'（§2.3，与分支写同事务）；
    - SchemaRejected（② 不合格）零落库零事件，任务留 dispatched——调用方据 reason
      走 re_dispatch(schema_fail) 或 failed；
    - 返回按落库序的全部事件（scout 分支为 []，无对应账本 kind）。
    """
    task = _submitted_task(ledger, task_id)
    run = run_id if run_id is not None else task["run_id"]
    if isinstance(output, ScoutReport):
        return _submit_scout(ledger, task, output, contract, ctx, params, run)
    if isinstance(output, AuditorFinding):
        return _submit_auditor(ledger, task, output, contract, ctx, params, run)
    if isinstance(output, ReviewerVote):
        return _submit_reviewer(ledger, task, output, contract, ctx, params, run)
    if isinstance(output, VerifierReport):
        return _submit_verifier(ledger, task, output, contract, ctx, params, run)
    raise SchemaRejected(
        f"未知输出类型：{type(output).__name__}（② 输出契约词表："
        "ScoutReport|AuditorFinding|ReviewerVote|VerifierReport，SPEC-1 §4.2）"
    )
