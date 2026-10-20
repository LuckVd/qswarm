"""kernel.loop：生产编排循环——L0→L1 流水线驱动 + promote_scan 影子挂接（SPEC-7 §2/§3）。

职责（§0.2 模块落位逐字）：L0（perception.scan）→ L1（侦察 v1）流水线驱动、
promote_scan 挂接点、账本会话管理（开工前校验 + run 生命周期）。**只复用不重写**
（SPEC-7 硬纪律）：账本会话走 :func:`kernel.bootstrap.verify_ready`（D28）+ 
``open_run``/``create_task``/``dispatch``/``re_dispatch``/``submit``/
``register_candidates``（dispatch 写口既有面）；候选收集走 ``perception.scan``；
存款聚合与孵化扫描走 ``agg_cells``/``promote_scan`` 本体。

L1 语义（SPEC-7 §2）：
- 分片（§2.1）：fast/pr 档文件分片、full 档目录级分片，大小 ``l1.files_per_shard``；
  目录树字典序，确定性；
- 任务化（§2.2）：每候选一任务（ScoutReport 单候选契约的自然落法，DECISIONS
  M3 实现期行）——``task_id = "l1:<shard_id>:<n:03d>"``、role=scout、goal=
  「L1 初筛：可疑点标记 + 初步类型猜测」、taxonomy_node = 分片内候选 taxonomy_hint
  多数族（无线索 = ``unassigned`` 占位，显式不猜）；
- 蜂选择（§2.2）：**temp 池 least-loaded**（active scout × 在途计数，平手 agent_id
  字典序）——侦察蜂不按口碑路由（×0.2 记账语义决定的独立通道）；route() 生产
  接线属 M4 L2，本模块不触；
- 存款（§2.3）：L1 verdict 落 S1 × ``deposits.SCOUT_X``(=0.2) 生产存款——
  suspicious=true（含家族猜测）→ direction=+1、cell=guess_family；明确拦截
  （suspicious=false）→ direction=-1、cell=候选 taxonomy_hint/分片多数族；无家族
  可归属 → 不落存款（cell 键必须有家族语义）、计数如实；
- 失败语义（§2.2/§16.7-8）：schema 畸形（含 candidate_id 回填失败）→
  re_dispatch(schema_fail) 改派重试 1 次；供给失败 → re_dispatch(timeout)；二次
  仍败 → 任务 failed、计数 ``l1_schema_fail``/``l1_infra_fail``、零存款（侦察面
  宽松，不阻塞漏斗）。

promote_scan 影子挂接（§3.2）：每轮循环尾部照常执行，但直晋通道全部置 0
（ENTRY_TOP_FRAC/ENTRY_DIRECT/TYPE_ENTRY）——生产首季无自动晋升，影子期只产
账；暗考回调留接口不实现（exam=None，考试执行器已有、由负责人影子期后开启）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import (
    AgentStatus,
    DepositOrigin,
    Role,
    RunMode,
    SignalTier,
)
from qswarm.kernel.bootstrap import verify_ready
from qswarm.kernel.dispatch import (
    build_contract,
    create_task,
    dispatch,
    open_run,
    re_dispatch,
    register_candidates,
    submit,
)
from qswarm.kernel.ledger import KernelClock, KernelLedger, WallClock, transaction
from qswarm.kernel.promote_scan import promote_scan
from qswarm.kernel.route import RouteDecision
from qswarm.perception import Candidate, ScanReport, scan_repo
from qswarm.prompts import EXAM_TEMPERATURE, ContextBlock, parse_output, render

__all__ = ["ScanLoopReport", "active_scouts", "least_loaded_picker", "run_scan"]

#: L1 任务 id 前缀（D26：与 ``l0scan:`` 严格区分）。
L1_TASK_PREFIX = "l1:"

#: L1 候选上下文窗口（候选 span ± 行数；L1 初筛面足够定位，M4 L2 再深挖）。
L1_CONTEXT_LINES = 20

#: 无线索分片的 taxonomy_node 占位（显式不猜； Deposits 不落此族）。
UNASSIGNED_NODE = "unassigned"


@dataclass
class ScanLoopReport:
    """一轮生产扫描的循环报告（确定性计数面；JSON 可序列化）。"""

    run_id: str
    mode: str
    scan: dict = field(default_factory=dict)          # perception ScanReport.to_dict()
    candidates_registered: int = 0
    shards: list[dict] = field(default_factory=list)  # [{shard_id, node, files, candidates}]
    tasks_dispatched: int = 0
    tasks_done: int = 0
    tasks_failed: int = 0
    l1_schema_fail: int = 0
    l1_infra_fail: int = 0
    l1_no_family: int = 0
    deposits_written: int = 0
    promote_transitions: int = 0

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "scan": self.scan,
            "candidates_registered": self.candidates_registered,
            "shards": list(self.shards),
            "tasks": {
                "dispatched": self.tasks_dispatched,
                "done": self.tasks_done,
                "failed": self.tasks_failed,
                "l1_schema_fail": self.l1_schema_fail,
                "l1_infra_fail": self.l1_infra_fail,
                "l1_no_family": self.l1_no_family,
            },
            "deposits_written": self.deposits_written,
            "promote_transitions": self.promote_transitions,
        }


# ── temp 池（§2.2：active scout × least-loaded，不按口碑路由）─────────

def active_scouts(ledger: KernelLedger) -> list[tuple[str, str]]:
    """active 侦察蜂池 [(model_ref, agent_id)]（agents×hats 读取面；字典序）。"""
    rows = ledger.conn.execute(
        "SELECT a.agent_id, a.model_ref FROM agents a JOIN hats h"
        " ON a.agent_id = h.agent_id WHERE h.role = 'scout' AND a.status = ?"
        " ORDER BY a.agent_id",
        (AgentStatus.ACTIVE.value,),
    ).fetchall()
    return [(str(model_ref), str(agent_id)) for agent_id, model_ref in rows]


def least_loaded_picker(scouts: list[tuple[str, str]]):
    """temp 池 least-loaded 选蜂器（§2.2）：在途计数最小者，平手 agent_id 字典序。"""
    load: dict[str, int] = {agent_id: 0 for _ref, agent_id in scouts}
    by_agent = {agent_id: model_ref for model_ref, agent_id in scouts}

    def _pick(task_id: str, shard_index: int) -> tuple[str, str]:
        _ = task_id, shard_index
        if not by_agent:
            return "", ""
        agent_id = min(by_agent, key=lambda a: (load.get(a, 0), a))
        load[agent_id] = load.get(agent_id, 0) + 1
        return by_agent[agent_id], agent_id

    return _pick


# ── L1 分片（§2.1：fast/pr 文件级、full 目录级；字典序确定性）──────────

def shard_candidates(
    candidates: list[Candidate],
    *,
    mode: str,
    files_per_shard: int,
) -> list[dict]:
    """候选 → L1 分片规格（确定性；返回 [{shard_id, node, files, candidate_ids}]）。

    - fast/pr：**文件分片**——候选涉及文件字典序、按 files_per_shard 装箱；
    - full：**目录/模块级分片**——按候选文件目录分组、组内按文件名字典序装箱；
    - shard_id = f"{mode}:{index:03d}"（两种分法都稳定）。"""
    uniq_files = sorted({c.path for c in candidates})
    if mode == "full":
        groups: dict[str, list[str]] = {}
        for path in uniq_files:
            groups.setdefault(str(Path(path).parent), []).append(path)
        ordered: list[list[str]] = []
        for directory in sorted(groups):
            files = sorted(groups[directory])
            for i in range(0, len(files), files_per_shard):
                ordered.append(files[i:i + files_per_shard])
    else:
        ordered = [
            uniq_files[i:i + files_per_shard]
            for i in range(0, len(uniq_files), files_per_shard)
        ]
    by_file: dict[str, list[Candidate]] = {}
    for cand in candidates:
        by_file.setdefault(cand.path, []).append(cand)
    shards: list[dict] = []
    for index, files in enumerate(ordered):
        members = [c for f in files for c in by_file.get(f, ())]
        hints = [c.taxonomy_hint for c in members if c.taxonomy_hint]
        # 分片内线索多数族（平手字典序首个；无线索 = unassigned 占位）。
        counts: dict[str, int] = {}
        for h in hints:
            counts[h] = counts.get(h, 0) + 1
        node = (
            min((-n, h) for h, n in counts.items())[1]
            if counts
            else UNASSIGNED_NODE
        )
        shards.append(
            {
                "shard_id": f"{mode}:{index:03d}",
                "node": node,
                "files": list(files),
                "candidates": members,
            }
        )
    return shards


# ── L1 单候选任务化（§2.2/§2.3）──────────────────────────────────

def _candidate_block(root: Path, cand: Candidate) -> ContextBlock:
    """候选上下文块：候选 span ± L1_CONTEXT_LINES 行窗 + 候选标识头行。"""
    source = (root / cand.path).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    start = max(1, cand.span[0] - L1_CONTEXT_LINES)
    end = min(len(lines), cand.span[1] + L1_CONTEXT_LINES)
    content = (
        f"candidate_id: {cand.candidate_id}\n"
        f"path: {cand.path}\n"
        f"span: {cand.span[0]}-{cand.span[1]}\n"
        "请对该候选点做 L1 初筛（suspicious + 家族猜测）。\n"
        + "\n".join(lines[start - 1:end])
    )
    return ContextBlock(ref=f"{cand.path}:{start}-{end}", content=content)


def _verdict_family(output, cand: Candidate, shard_node: str) -> str | None:
    """verdict 存款 cell 家族：suspicious → guess_family；拦截 → 候选 hint/分片族。"""
    if output.suspicious and output.guess_family is not None:
        return str(output.guess_family)
    if not output.suspicious:
        return cand.taxonomy_hint or (shard_node if shard_node != UNASSIGNED_NODE else None)
    return None


def _dispatch_one_candidate(
    ledger: KernelLedger,
    *,
    root: Path,
    cand: Candidate,
    shard: dict,
    run_id: str,
    seq: int,
    scouts: list[tuple[str, str]],
    adapter,
    params,
    picker,
    report: ScanLoopReport,
) -> None:
    """单候选 L1 任务全生命周期（create → dispatch → 调用 → submit → 存款 ×0.2）。

    失败语义见模块 docstring；任何二次失败 → 任务 failed、计数、零存款、不抛穿
    （侦察面宽松，单点失败不停漏斗）。"""
    task_id = f"{L1_TASK_PREFIX}{shard['shard_id']}:{seq:03d}"
    # 幂等（D 组判据：同仓库重跑零新候选、账本零重复存款）：任务行已存在 →
    # 该候选已在此 run 处理过（任务 id 由 (run_id, 候选确定性序) 派生），跳过。
    existing = ledger.conn.execute(
        "SELECT state FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone()
    if existing is not None:
        return
    node = shard["node"]
    model_ref, agent_id = picker(task_id, seq)
    if not model_ref:
        # 空池：留 queued（D10 empty_pool 语义），如实计数不派单。
        create_task(
            ledger, run_id=run_id, role="scout", node=node,
            shard={"candidate_id": cand.candidate_id, "path": cand.path,
                   "span": list(cand.span), "family_hint": cand.taxonomy_hint},
            task_id=task_id,
        )
        return
    shard_payload = {
        "candidate_id": cand.candidate_id,
        "path": cand.path,
        "span": list(cand.span),
        "family_hint": cand.taxonomy_hint,
        "shard_node": node,
    }
    create_task(
        ledger, run_id=run_id, role="scout", node=node, shard=shard_payload,
        task_id=task_id,
    )
    decision = RouteDecision(
        task_id=task_id, role="scout", family=str(node).partition("/")[0],
        node=str(node), assignee=agent_id, channel="explore", reason="temp_pool",
        tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
    )
    contract = build_contract(
        None,
        decision,
        mode=RunMode.FAST,                    # scout 不深挖（task_bounds）→ hop=0
        deadline_s=600,
        goal="L1 初筛：可疑点标记 + 初步类型猜测",
    )
    dispatch(ledger, task_id, contract, decision, run_id=run_id)
    report.tasks_dispatched += 1

    messages = render("scout", contract=contract, blocks=[_candidate_block(root, cand)])
    current_agent = agent_id
    last_error = ""
    output = None
    for _attempt in (1, 2):                       # §16.7-8：初派 1 + 重派 1
        try:
            raw = adapter.complete(
                model_ref=by_agent_ref(scouts, current_agent),
                messages=messages,
                temperature=EXAM_TEMPERATURE,     # 生产侦察面与考试同温（temp=0 单一源）
            )
            output = parse_output("scout", raw)
            if output.candidate_id != cand.candidate_id:
                raise ValueError("candidate_id_mismatch")
            break
        except ValueError as exc:                 # SchemaRejected ⊂ ValueError + 回填失败
            last_error = f"schema:{exc}"
            output = None
        except Exception as exc:                  # noqa: BLE001 — 供给失败（AdapterError 族）
            last_error = f"adapter:{type(exc).__name__}"
            output = None
        # 重派（attempts<2）；二次失败 re_dispatch 自行置 failed 并抛出。
        nxt = _next_least_loaded(scouts, exclude=current_agent)
        try:
            re_dispatch(ledger, task_id, to_agent=nxt, reason="schema_fail" if last_error.startswith("schema") else "timeout", run_id=run_id)
            current_agent = nxt
        except ValueError:
            report.tasks_failed += 1
            if last_error.startswith("schema"):
                report.l1_schema_fail += 1
            else:
                report.l1_infra_fail += 1
            return
    if output is None:
        # 理论不可达（二次失败已在上一分支返回）；防御性兜底。
        report.tasks_failed += 1
        report.l1_schema_fail += 1
        return
    family = _verdict_family(output, cand, node)
    direction = 1 if output.suspicious else -1
    # submit（任务置 done）与 L1 存款同一事务（§7.1/D24 可重入）：此前两步间
    # 崩溃则存款永久丢失——重跑时任务行已存在被幂等检查跳过、存款不再补写，
    # 破坏「同仓库重跑账目收敛」（2026-10-20 修复）。
    try:
        with transaction(ledger):
            submit(
                ledger, task_id, output,
                contract=contract, params=params, run_id=run_id,
            )
            if family is None:
                report.l1_no_family += 1
            else:
                ledger.append(
                    "deposit",
                    {
                        "finding": None,
                        "cluster": None,
                        "agent": current_agent,
                        "role": Role.SCOUT.value,
                        "taxonomy_path": [family],
                        "signal_tier": SignalTier.S1.value,
                        "direction": direction,
                        "weight": float(params.deposits.SCOUT_X),
                        "origin": DepositOrigin.PROD.value,
                    },
                    run_id=run_id,
                )
        report.tasks_done += 1
        if family is not None:
            report.deposits_written += 1
    except ValueError:
        # candidate_id 指向不存在行等语义失败：schema_fail 计数 + 任务收尾失败
        # （事务回滚——submit 零半写态）。
        report.l1_schema_fail += 1
        report.tasks_failed += 1
        return


def by_agent_ref(scouts: list[tuple[str, str]], agent_id: str) -> str:
    """agent_id → model_ref（池内缺失 → 空串，adapter 反查报错）。"""
    for model_ref, agent in scouts:
        if agent == agent_id:
            return model_ref
    return ""


def _next_least_loaded(scouts: list[tuple[str, str]], exclude: str) -> str:
    """重派目标：排除当前蜂后的字典序首个（确定性；单蜂池自返由 re_dispatch
    的 attempts 上限兜底）。"""
    for _ref, agent in sorted(scouts, key=lambda t: t[1]):
        if agent != exclude:
            return agent
    return exclude


# ── 主入口 ──────────────────────────────────────────────────────

def run_scan(
    ledger: KernelLedger,
    *,
    repo_root: str | Path,
    mode: str,
    run_id: str,
    params,
    adapter,
    store_dir: str | Path,
    cache_dir: str | Path | None = None,
    changed_paths: list[str] | None = None,
    rules_runner=None,
    clock: KernelClock | None = None,
    promote: bool = True,
) -> ScanLoopReport:
    """一轮生产扫描（L0→L1→影子 promote；会话管理 §3）。

    - 开工前校验（verify_ready：4 active + ≥20 考试存款）→ open_run → L0 扫描
      （候选集入工件库）→ candidates 入表 → L1 分片派单 → 影子 promote_scan；
    - ``promote=False`` 供测试隔离 L0→L1 面（影子挂接由 M3 卷 §1.5 独立判据）；"""
    ready = verify_ready(ledger)
    _ = ready                                     # 对账面：调用方可读（防空账本启动）
    clock = clock if clock is not None else WallClock()
    root = Path(repo_root)
    report = ScanLoopReport(run_id=run_id, mode=str(mode))

    open_run(ledger, run_id=run_id, mode=str(mode), repo_url=str(root))

    scouts = active_scouts(ledger)
    picker = least_loaded_picker(scouts)

    scan_report: ScanReport = scan_repo(
        root,
        mode=mode,
        params=params,
        adapter=adapter,
        scouts=scouts,
        scout_picker=picker,
        cache_dir=cache_dir,
        ledger=ledger,
        store_dir=store_dir,
        changed_paths=changed_paths,
        rules_runner=rules_runner,
    )
    report.scan = scan_report.to_dict()

    registered = register_candidates(ledger, run_id=run_id, candidates=scan_report.candidates)
    report.candidates_registered = len(registered)

    shards = shard_candidates(
        scan_report.candidates,
        mode=str(mode),
        files_per_shard=params.perception.l1.files_per_shard,
    )
    seq = 0
    for shard in shards:
        report.shards.append(
            {"shard_id": shard["shard_id"], "node": shard["node"],
             "files": len(shard["files"]), "candidates": len(shard["candidates"])}
        )
        for cand in shard["candidates"]:
            seq += 1
            _dispatch_one_candidate(
                ledger,
                root=root,
                cand=cand,
                shard=shard,
                run_id=run_id,
                seq=seq,
                scouts=scouts,
                adapter=adapter,
                params=params,
                picker=picker,
                report=report,
            )

    if promote:
        # 影子运行（§3.2）：直晋通道全部置 0——生产首季无自动晋升，只产状态账。
        shadow_params = params.model_copy(deep=True)
        shadow_params.exam.ENTRY_DIRECT = 0.0
        shadow_params.exam.ENTRY_TOP_FRAC = 0.0
        shadow_params.exam.TYPE_ENTRY = 0.0
        transitions = promote_scan(
            ledger, as_of=clock.now(), params=shadow_params, exam=None, run_id=run_id,
        )
        report.promote_transitions = len(transitions)
    return report
