"""kernel.consult：L2 会诊生产编排——route() 接线 + 聚类消费 + 面板投票 + 预裁决单
（SPEC-8 §2/§3；M4，依赖序第 7 位）。

职责（SPEC-8 §0 交付面逐字）：读 L1 存活点（candidates triaged 行）→ repo map 切片
注入（perception.repomap，D37 名称绑定近似）→ **route() 本体**派 auditor（F3 生产
接线：active 格 C* 专员通道 + 自适应 TILT + 探索通道）→ findings + 证据链建档（
dispatch 既有写口）→ 粗聚类（cluster.attribute_findings）→ 面板投票（D31 SIZE=3、
排除报告者、least-loaded、帽兼任池）→ S1 收口存款（§6.5 全行展开 + D32 因子）→
精聚类（词面 Jaccard sim_fn v1）→ verifier 预裁决单（D29 三字段，工件面零存款）→
预算熔断（budget 既有件）→ 最小报告（D34）+ 影子期观察指标（F4）。

硬纪律（承 SPEC-7 逐字）：**只复用不重写**——任务生命周期走 create_task/dispatch/
re_dispatch/submit/close_task_empty，聚类走 cluster_findings/apply_merges/
attribute_findings/close_panel，预算走 budget_for_mode/budget_state/on_cost，路由走
route 本体，会话走 verify_ready/open_run；本模块**零账本直写**（B 组审计判据）。

面板语义（D31，2026-10-13 负责人拍板）：panel.SIZE=3 维持；池 = hats 有 reviewer 帽
∧ agents.active（帽兼任 = §4.1「角色是帽子不是身份」原生机制）；排除簇内全部报告者
后取 least-loaded，平手优先互补真实服务模型；池 <consult.min_panel → 不开面板
（finding 停 open，fail-closed 不硬凑）。簇级投票落锚点 finding（票省语义 §2.1），
成员 finding 状态经 transition_finding 传播（无票行 → 无 S2 面重复存款隐患）。

auditor「干净候选」表达（契约词表零改的实现口径）：契约 goal 指令声明降级信封
``{"declined": true}``——模型分析后确认无可报漏洞时输出该 JSON；编排侧解析识别为
close_task_empty 收口 + 计数 ``l2_clean``（零落库零存款，与 L1 拦截对称）。declined
信封是考试面既有词表形态（runner._parse_envelope_answer 同源），生产面由派单方
goal 定义。预裁决单（pv）不走 tasks 状态机（M3-1 先例：无状态机语义、无
verify_results 行、零存款——verify_results 是 S2 真验证专用面）。
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import (
    AgentStatus,
    FindingState,
    LedgerKind,
    Role,
    RunMode,
    load_params,
)
from qswarm.contracts.models import PreVerdictSlip
from qswarm.kernel.bootstrap import verify_ready
from qswarm.kernel.budget import (
    BudgetPolicy,
    budget_for_mode,
    budget_state,
    on_cost,
)
from qswarm.kernel.cells import view_cells
from qswarm.kernel.cluster import (
    FindingFacts,
    MemberFacts,
    VerdictContext,
    apply_merges,
    attribute_findings,
    close_panel,
    cluster_findings,
    fine_mergeable,
)
from qswarm.kernel.route import RouteDecision
from qswarm.kernel.dispatch import (
    build_contract,
    close_task_empty,
    create_task,
    dispatch,
    open_run,
    re_dispatch,
    submit,
    transition_finding,
)
from qswarm.kernel.ledger import KernelClock, KernelLedger, WallClock
from qswarm.kernel.route import route
from qswarm.exams.store import artifact_put
from qswarm.perception.indexer import IndexCache, index_repo, lang_for_path
from qswarm.perception.repomap import build_name_graph, slice_for, slice_to_json
from qswarm.perception.scan import _walk_repo
from qswarm.prompts import EXAM_TEMPERATURE, ContextBlock, parse_output, render
from qswarm.prompts.parse import extract_json
from qswarm.simulator.rng import derive_seed

__all__ = [
    "ConsultReport",
    "TaskRef",
    "active_auditors",
    "active_reviewers",
    "build_panel",
    "s1_factor_for",
    "jaccard_sim",
    "shadow_report",
    "resolve_real_model",
    "run_consult",
]

#: 任务 id 前缀（l2: 候选深诊 / vote: 面板票；pv 预裁决单不走 tasks 状态机）。
L2_TASK_PREFIX = "l2:"
VOTE_TASK_PREFIX = "vote:"

#: L2 深诊 goal（四件套①；declined 信封口径在此对模型声明，见模块 docstring）。
L2_GOAL = (
    "L2 会诊：对该可疑点×类型做深度分析，产出带完整证据链的 finding；"
    '若分析后确认不存在可报漏洞，输出 {"declined": true}（不构造凑数 finding）。'
)

#: 真实服务模型解析表（D32；DECISIONS 2026-10-12 Coding Plan 探针实测口径：
#: 请求的轻量模型由 glm-5.3-flash 服务、旗舰由 glm-5.3 服务）。表外名字 →
#: "unknown" 哨兵（同哨兵合并计 1 个 distinct——保守降 diversity，多降权不少降）。
SERVED_MODEL: dict[str, str] = {
    "glm-4.5-air": "glm-5.3-flash",
    "glm-4.6": "glm-5.3-flash",
    "glm-5.3-flash": "glm-5.3-flash",
    "glm-5.2": "glm-5.3",
    "glm-5": "glm-5.3",
    "glm-5.3": "glm-5.3",
}

#: route() 的任务鸭子类型（_role_of/_taxonomy_of 契约面；§5.2）。
class TaskRef:
    """路由输入的最小任务面（task_id + role + taxonomy_path；只读）。"""

    def __init__(self, task_id: str, role: str, taxonomy_path: list[str]) -> None:
        self.task_id = task_id
        self.role = role
        self.taxonomy_path = taxonomy_path


def resolve_real_model(model_ref: str) -> str:
    """model_ref（``tier:provider/model@cv``）→ 真实服务模型名（D32 解析表）。"""
    model = model_ref.rsplit("@", 1)[0].rsplit("/", 1)[-1]
    return SERVED_MODEL.get(model, "unknown")


# ── 蜂池读取面（agents×hats join；字典序确定性）────────────────────


def _active_by_role(ledger: KernelLedger, role: str) -> list[tuple[str, str]]:
    rows = ledger.conn.execute(
        "SELECT a.agent_id, a.model_ref FROM agents a JOIN hats h"
        " ON a.agent_id = h.agent_id WHERE h.role = ? AND a.status = ?"
        " ORDER BY a.agent_id",
        (role, AgentStatus.ACTIVE.value),
    ).fetchall()
    return [(str(model_ref), str(agent_id)) for agent_id, model_ref in rows]


def active_auditors(ledger: KernelLedger) -> list[tuple[str, str]]:
    """active 审查蜂池 [(model_ref, agent_id)]（route() 探索池 = 全量 auditor，
    家族过滤由 route 内部按格子节点完成）。"""
    return _active_by_role(ledger, Role.AUDITOR.value)


def active_reviewers(ledger: KernelLedger) -> list[tuple[str, str]]:
    """active 评审蜂池（帽兼任池，D31；qualified = 帽 ∧ active 双条件）。"""
    return _active_by_role(ledger, Role.REVIEWER.value)


def active_verifiers(ledger: KernelLedger) -> list[tuple[str, str]]:
    """active 验证蜂池（预裁决单面；D29）。"""
    return _active_by_role(ledger, Role.VERIFIER.value)


# ── 面板构造器（SPEC-8 §3；纯函数）───────────────────────────────


def build_panel(
    pool: list[tuple[str, str]],
    *,
    exclude: frozenset[str],
    load: dict[str, int],
    panel_size: int,
    min_panel: int,
) -> list[str]:
    """评审团构造（§16.7-6 + D31 裁决）：排除报告者 → least-loaded → 互补真实
    服务模型贪心 → 目标 panel_size 席；池不足 min_panel → 空（fail-closed）。

    确定性：排序键 (load, agent_id)；互补贪心在平手集合内按该序展开。"""
    cands = sorted(
        (agent for _ref, agent in pool if agent not in exclude),
        key=lambda a: (load.get(a, 0), a),
    )
    if len(cands) < min_panel:
        return []
    picked: list[str] = []
    used_models: set[str] = set()
    remaining = list(cands)
    while remaining and len(picked) < panel_size:
        # 互补贪心：真实服务模型未出现在已选集合中的最前者；全见过 → 首位。
        nxt = next(
            (a for a in remaining if resolve_real_model(a) not in used_models),
            remaining[0],
        )
        picked.append(nxt)
        used_models.add(resolve_real_model(nxt))
        remaining.remove(nxt)
    return picked


def s1_factor_for(panel: list[str], *, rho: float) -> float:
    """D32 公式：s1_factor = 1 − ρ·(1 − diversity)；diversity = (distinct 真实
    服务模型数 − 1)/(panel 人数 − 1)，单人面板 diversity=1。数值钉：
    3 人同模型 0.6 / 3 人两模型 0.8 / 全异 1.0 / 2 人同模型 0.6。"""
    if len(panel) <= 1:
        return 1.0
    distinct = len({resolve_real_model(a) for a in panel})
    diversity = (distinct - 1) / (len(panel) - 1)
    return 1.0 - float(rho) * (1.0 - diversity)


# ── 精聚类 sim_fn v1（SPEC-8 §2.5；确定性词面 Jaccard）──────────────


def jaccard_sim(fa: FindingFacts, fb: FindingFacts) -> float:
    """词面 Jaccard：A = {path, type∨family} ∪ 证据 where_path 集（casefold）。

    sim_text 形 = "title|kind:where:ref|…"（_finding_facts 装配），证据词取
    where 段；与 fine_mergeable 的 CLUSTER_EMBED_SIM_MIN=0.9 判据配合。"""

    def bag(f: FindingFacts) -> set[str]:
        words = {f.path.casefold(), (f.type or f.family).casefold()}
        for node in f.sim_text.split("|")[1:]:
            parts = node.split(":", 2)
            if len(parts) >= 2 and parts[1]:
                words.add(parts[1].casefold())
        return words

    a, b = bag(fa), bag(fb)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── 影子期观察指标（SPEC-8 §4；view_cells 只读形态）─────────────────


def shadow_report(
    ledger: KernelLedger,
    *,
    as_of,
    params=None,
    entry_zeroed: bool = True,
) -> dict:
    """组织影子账快照（纯读；cells 零写由 C 组判据钉）。

    ``entry_zeroed`` = M3-7 影子注入覆写是否仍生效（调用方如实传；consult 恒
    True——M4 不动直晋通道，裁决面只交指标）。"""
    view = view_cells(ledger, as_of, params=params)
    by_status: dict[str, int] = {}
    n_effs: list[float] = []
    per_family: dict[str, dict[str, int]] = {}
    for key in sorted(view.cells, key=lambda k: (k.agent_id, k.role, k.node)):
        cell = view.cells[key]
        node = key.node
        status = str(getattr(cell, "status", ""))
        by_status[status] = by_status.get(status, 0) + 1
        n_effs.append(float(getattr(cell, "n_eff", 0.0)))
        family = str(node).partition("/")[0]
        slot = per_family.setdefault(family, {"active": 0, "candidate": 0, "shadow": 0})
        if status in slot:
            slot[status] += 1
    n_effs.sort()

    def _quantile(q: float) -> float:
        if not n_effs:
            return 0.0
        idx = min(len(n_effs) - 1, int(q * len(n_effs)))
        return n_effs[idx]

    return {
        "cells_by_status": dict(sorted(by_status.items())),
        "n_eff": {
            "p50": _quantile(0.5),
            "p90": _quantile(0.9),
            "max": n_effs[-1] if n_effs else 0.0,
        },
        "per_family": {k: dict(sorted(v.items())) for k, v in sorted(per_family.items())},
        "entry_knobs_zeroed": bool(entry_zeroed),
        "exam_callback_ready": True,
    }


# ── 报告（SPEC-8 §5；D34 最小报告）───────────────────────────────


@dataclass
class ConsultReport:
    """一轮会诊的循环报告（漏斗计数 + 逐 finding 摘要 + 预算/路由诊断面）。"""

    run_id: str
    mode: str
    scan_run_id: str = ""
    candidates_total: int = 0
    l2_dispatched: int = 0
    l2_clean: int = 0
    findings_opened: int = 0
    clusters_coarse: int = 0
    clusters_fine: int = 0
    panels_run: int = 0
    findings_voted: int = 0
    preverdicts: int = 0
    l2_schema_fail: int = 0
    l2_infra_fail: int = 0
    l2_quota_skipped: int = 0
    l2_route_empty_pool: int = 0
    panel_starved: int = 0
    pv_skipped: int = 0
    pv_schema_fail: int = 0
    expired_open: int = 0
    deposits_written: int = 0
    spent_cents: int = 0
    wall_clock_exceeded: bool = False
    route_specialist: int = 0
    route_explore: int = 0
    findings_detail: list[dict] = field(default_factory=list)
    preverdict_detail: list[dict] = field(default_factory=list)
    shadow: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "scan_run_id": self.scan_run_id,
            "funnel": {
                "candidates_total": self.candidates_total,
                "l2_dispatched": self.l2_dispatched,
                "l2_clean": self.l2_clean,
                "findings_opened": self.findings_opened,
                "clusters_coarse": self.clusters_coarse,
                "clusters_fine": self.clusters_fine,
                "panels_run": self.panels_run,
                "findings_voted": self.findings_voted,
                "preverdicts": self.preverdicts,
                "expired_open": self.expired_open,
            },
            "failures": {
                "l2_schema_fail": self.l2_schema_fail,
                "l2_infra_fail": self.l2_infra_fail,
                "l2_quota_skipped": self.l2_quota_skipped,
                "l2_route_empty_pool": self.l2_route_empty_pool,
                "panel_starved": self.panel_starved,
                "pv_skipped": self.pv_skipped,
                "pv_schema_fail": self.pv_schema_fail,
            },
            "deposits_written": self.deposits_written,
            "spent_cents": self.spent_cents,
            "wall_clock_exceeded": self.wall_clock_exceeded,
            "route_channels": {"specialist": self.route_specialist, "explore": self.route_explore},
            "findings": list(self.findings_detail),
            "preverdicts": list(self.preverdict_detail),
            "shadow": dict(self.shadow),
        }


def _report_md(rep: ConsultReport) -> str:
    """最小报告 md 形态（字段与 json 一一对应，SPEC-8 §5）。"""
    d = rep.to_dict()
    fu, fa = d["funnel"], d["failures"]
    lines = [
        f"# Consult Report — {rep.run_id}",
        "",
        f"- mode: {rep.mode} · scan_run: {rep.scan_run_id} · "
        f"wall_clock_exceeded: {rep.wall_clock_exceeded}",
        f"- 预算：名义 {rep.spent_cents} cents（智谱 Coding Plan 为订阅制，实际成本按"
        "订阅摊销；配额判定用名义价——D33 双轨口径）",
        "",
        "## 漏斗",
        "",
        f"候选 {fu['candidates_total']} → 深诊 {fu['l2_dispatched']}"
        f"（干净 {fu['l2_clean']}）→ finding {fu['findings_opened']}"
        f" → 粗簇 {fu['clusters_coarse']} / 精簇 {fu['clusters_fine']}"
        f" → 面板 {fu['panels_run']} → voted {fu['findings_voted']}"
        f" → 预裁决单 {fu['preverdicts']}；open 过期 {fu['expired_open']}",
        "",
        "## Finding 摘要",
        "",
        "| finding | 簇 | family/type | severity | confidence | 面板 | 方向 | 分歧 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for f in rep.findings_detail:
        lines.append(
            f"| {f['finding_id']} | {f['cluster_id'] or '-'} | {f['family']}/{f['type'] or '-'}"
            f" | {f['severity']} | {f['confidence']} | {f['panel']} | {f['direction']}"
            f" | {'有' if f['disagreement'] else '无'} |"
        )
    lines += ["", "## 预裁决单", ""]
    if rep.preverdict_detail:
        for p in rep.preverdict_detail:
            lines.append(
                f"- {p['cluster_id']}: {p['expected_verdict']}（{p['poc_shape']}；"
                f"env: {', '.join(p['env_requirements']) or '无'}）"
            )
    else:
        lines.append("-（无）")
    lines += [
        "",
        "## 影子账",
        "",
        f"- 格子状态：{d['shadow'].get('cells_by_status', {})}",
        f"- n_eff p50/p90/max：{d['shadow'].get('n_eff', {})}",
        "",
        "## 失败与缺席（如实计数）",
        "",
        f"- schema_fail {fa['l2_schema_fail']} / infra_fail {fa['l2_infra_fail']}"
        f" / quota_skipped {fa['l2_quota_skipped']} / route_empty_pool"
        f" {fa['l2_route_empty_pool']} / panel_starved {fa['panel_starved']}"
        f" / pv_skipped {fa['pv_skipped']} / pv_schema_fail {fa['pv_schema_fail']}",
        "",
    ]
    return "\n".join(lines)


# ── 内部装配件 ──────────────────────────────────────────────────


def _triaged_candidates(ledger: KernelLedger, scan_run_id: str) -> list[dict]:
    rows = ledger.conn.execute(
        "SELECT candidate_id, path, line_start, line_end, channels, channel_score,"
        " guess_family, guess_type FROM candidates"
        " WHERE run_id = ? AND triaged = 1 ORDER BY candidate_id",
        (scan_run_id,),
    ).fetchall()
    return [
        {
            "candidate_id": str(r[0]), "path": str(r[1]),
            "span": (int(r[2]), int(r[3])), "channels": str(r[4]),
            "channel_score": float(r[5]),
            "guess_family": (str(r[6]) if r[6] else None),
            "guess_type": (str(r[7]) if r[7] else None),
        }
        for r in rows
    ]


def _l1_scout_lineage(ledger: KernelLedger, scan_run_id: str) -> dict[str, list[str]]:
    """candidate_id → L1 侦察蜂谱系（tasks.shard_json 反查；S1 侦察行归属面）。"""
    rows = ledger.conn.execute(
        "SELECT shard_json, assignee FROM tasks WHERE run_id = ? AND task_id LIKE 'l1:%'"
        " ORDER BY task_id",
        (scan_run_id,),
    ).fetchall()
    out: dict[str, list[str]] = {}
    for shard_json, assignee in rows:
        try:
            cid = str(json.loads(str(shard_json)).get("candidate_id") or "")
        except ValueError:
            continue
        if cid and assignee:
            out.setdefault(cid, []).append(str(assignee))
    return {k: sorted(set(v)) for k, v in out.items()}


def _index_repo_tree(root: Path, cache_dir: str | Path | None):
    """仓库索引 + 源文（repomap 输入；IndexCache 内容寻址缓存复用）。"""
    cache = IndexCache(cache_dir) if cache_dir is not None else IndexCache()
    files: list[tuple[str, bytes]] = []
    for rel in _walk_repo(root):
        data = (root / rel).read_bytes()
        files.append((rel.as_posix(), data))
    entries = index_repo(files, cache=cache)
    sources = {p: s.decode("utf-8", "replace") for p, s in files}
    return entries, sources


def _candidate_block(root: Path, cand: dict, ctx_lines: int) -> ContextBlock:
    """候选深诊块：span ± ctx_lines 行窗（L2 深诊档窗宽，SPEC-8 §6）。"""
    source = (root / cand["path"]).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    start = max(1, cand["span"][0] - ctx_lines)
    end = min(len(lines), cand["span"][1] + ctx_lines)
    content = (
        f"candidate_id: {cand['candidate_id']}\n"
        f"path: {cand['path']}\n"
        f"span: {cand['span'][0]}-{cand['span'][1]}\n"
        f"L1 家族猜测: {cand['guess_family'] or '无'}\n"
        + "\n".join(lines[start - 1:end])
    )
    return ContextBlock(ref=f"{cand['path']}:{start}-{end}", content=content)


def _repomap_block(root: Path, slice_) -> ContextBlock:
    """repo map 切片数据块（§1.3：切片载荷行从源文切；ref = 行窗引用串）。"""
    parts: list[str] = ["repo map 切片（候选点邻域上下文）："]
    refs: list[str] = []
    for sf in slice_.files:
        source = (root / sf.path).read_text(encoding="utf-8", errors="replace")
        lines = source.splitlines()
        parts.append(f"--- {sf.path} ({sf.lang}) ---")
        for s, e in sf.spans:
            parts.extend(lines[s - 1:e])
            refs.append(f"{sf.path}:{s}-{e}")
        if sf.truncated:
            parts.append(f"（…{sf.path} 载荷被切片预算截断）")
    return ContextBlock(ref=";".join(refs), content="\n".join(parts))


def _finding_facts(ledger: KernelLedger, finding_id: str) -> FindingFacts:
    """findings + evidence_nodes 行 → FindingFacts（cluster 装配面）。"""
    row = ledger.conn.execute(
        "SELECT path, line_start, line_end, cwe, lang, family, type, title"
        " FROM findings WHERE id = ?",
        (finding_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"finding 不存在：{finding_id!r}")
    nodes = ledger.conn.execute(
        "SELECT kind, where_path, where_line, ref FROM evidence_nodes"
        " WHERE finding_id = ? ORDER BY ord",
        (finding_id,),
    ).fetchall()
    sim = "|".join(f"{k}:{w}:{r}" for k, w, _l, r in nodes)
    return FindingFacts(
        finding_id=finding_id,
        path=str(row[0]), line_start=int(row[1]), line_end=int(row[2]),
        cwe=(str(row[3]) if row[3] else None), lang=str(row[4]),
        family=str(row[5]), type=(str(row[6]) if row[6] else None),
        sim_text=f"{row[7]}|{sim}",
    )


def _member_facts(ledger: KernelLedger, finding_id: str) -> MemberFacts:
    row = ledger.conn.execute(
        "SELECT reporter, reporter_role FROM findings WHERE id = ?", (finding_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"finding 不存在：{finding_id!r}")
    return MemberFacts(
        finding_id=finding_id, reporter=str(row[0]), reporter_role=str(row[1])
    )


def _finding_block(
    ledger: KernelLedger, root: Path, anchor: str, members: list[str], ctx: int
) -> ContextBlock:
    """评审块：锚 finding 全量材料（行 + 证据链 + 代码窗 + 簇成员摘要）。"""
    row = ledger.conn.execute(
        "SELECT path, line_start, line_end, lang, family, type, title, severity,"
        " confidence, cwe FROM findings WHERE id = ?", (anchor,)
    ).fetchone()
    nodes = ledger.conn.execute(
        "SELECT ord, kind, where_path, where_line, found FROM evidence_nodes"
        " WHERE finding_id = ? ORDER BY ord", (anchor,),
    ).fetchall()
    mem_rows = ledger.conn.execute(
        "SELECT id, reporter FROM findings WHERE id IN"
        f" ({', '.join('?' * len(members))}) ORDER BY id", tuple(members),
    ).fetchall()
    source = (root / str(row[0])).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    s, e = max(1, int(row[1]) - ctx), min(len(lines), int(row[2]) + ctx)
    body = [
        f"finding_id: {anchor}",
        f"title: {row[6]} | severity: {row[7]} | confidence: {row[8]} | cwe: {row[9] or '-'}",
        f"位置: {row[0]}:{row[1]}-{row[2]} ({row[3]}) 家族: {row[4]}/{row[5] or '-'}",
        "证据链:",
        *[f"  {n[0]}. [{n[1]}] {n[2]}:{n[3]} found={n[4]}" for n in nodes],
        "簇成员: " + ", ".join(f"{m[0]}(reporter={m[1]})" for m in mem_rows),
        "相关代码:",
        *lines[s - 1:e],
    ]
    return ContextBlock(ref=f"{row[0]}:{s}-{e}", content="\n".join(body))


def _cluster_block(
    ledger: KernelLedger, root: Path, members: list[str], ctx: int
) -> ContextBlock:
    """预裁决块：簇全量成员（行 + 证据链 + 代码窗；§2.6 输入面）。"""
    parts: list[str] = [f"待预判簇成员 {len(members)} 个："]
    for fid in members:
        facts = _finding_facts(ledger, fid)
        row = ledger.conn.execute(
            "SELECT title, severity, confidence FROM findings WHERE id = ?", (fid,)
        ).fetchone()
        parts.append(
            f"- {fid}: {row[0]} [{row[1]}] conf={row[2]} @ {facts.path}:"
            f"{facts.line_start}-{facts.line_end} 证据: {facts.sim_text}"
        )
    anchor = members[0]
    row = ledger.conn.execute(
        "SELECT path, line_start, line_end FROM findings WHERE id = ?", (anchor,)
    ).fetchone()
    source = (root / str(row[0])).read_text(encoding="utf-8", errors="replace")
    lines = source.splitlines()
    s, e = max(1, int(row[1]) - ctx), min(len(lines), int(row[2]) + ctx)
    parts.append("锚点代码:")
    parts.extend(lines[s - 1:e])
    return ContextBlock(ref=f"{row[0]}:{s}-{e}", content="\n".join(parts))


def _by_ref(pool: list[tuple[str, str]], agent_id: str) -> str:
    for model_ref, agent in pool:
        if agent == agent_id:
            return model_ref
    return ""


def _next_by_load(pool: list[tuple[str, str]], load: dict[str, int], exclude: str) -> str:
    cands = sorted((a for _r, a in pool if a != exclude), key=lambda a: (load.get(a, 0), a))
    return cands[0] if cands else exclude


# ── L2 深诊（单候选全生命周期；§2.2）─────────────────────────────


def _dispatch_l2_candidate(
    ledger: KernelLedger,
    *,
    root: Path,
    cand: dict,
    seq: int,
    run_id: str,
    mode: str,
    auditors: list[tuple[str, str]],
    cells,
    load: dict[str, int],
    rng: random.Random,
    adapter,
    params,
    policy: BudgetPolicy,
    report: ConsultReport,
    repomap_block: ContextBlock | None,
    deadline_s: int = 600,
) -> str | None:
    """route() 派 auditor → 深诊 → submit / declined / 失败语义（§2.2/§2.8）。

    返回新建 finding_id（候选→finding 谱系锚，供 S1 侦察行归属）；无建档 → None。"""
    guess = cand["guess_family"]
    node = guess if guess else "unassigned"
    task_id = f"{L2_TASK_PREFIX}{cand['candidate_id'][:12]}:{seq:03d}"
    if ledger.conn.execute(
        "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
    ).fetchone() is not None:
        return None                               # 幂等：同 run 重跑跳过
    task = TaskRef(task_id=task_id, role=Role.AUDITOR.value, taxonomy_path=[str(node)])
    decision = route(task, cells, rng, params, load=load, pool=[a for _r, a in auditors])
    if decision.channel == "specialist":
        report.route_specialist += 1
    else:
        report.route_explore += 1
    model_ref = ""
    agent_id = ""
    if decision.assignee:
        agent_id = decision.assignee
        model_ref = _by_ref(auditors, agent_id)
    else:
        report.l2_route_empty_pool += 1
        if not auditors:
            create_task(                          # D10：空池留 queued，如实不派
                ledger, run_id=run_id, role=Role.AUDITOR.value, node=node,
                shard={"candidate_id": cand["candidate_id"], "path": cand["path"],
                       "line_start": cand["span"][0], "line_end": cand["span"][1]},
                task_id=task_id,
            )
            return None
        agent_id = min((a for _r, a in auditors), key=lambda a: (load.get(a, 0), a))
        model_ref = _by_ref(auditors, agent_id)
        decision = RouteDecision(
            task_id=task_id, role=Role.AUDITOR.value,
            family=str(node).partition("/")[0], node=str(node), assignee=agent_id,
            channel="explore", reason="empty_pool_fallback",
            tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
        )
    load[agent_id] = load.get(agent_id, 0) + 1
    create_task(
        ledger, run_id=run_id, role=Role.AUDITOR.value, node=node,
        shard={"candidate_id": cand["candidate_id"], "path": cand["path"],
               "line_start": cand["span"][0], "line_end": cand["span"][1],
               "lang": lang_for_path(cand["path"]) or "", "family_hint": guess},
        task_id=task_id,
    )
    context_refs: list[str] = []
    if repomap_block is not None:
        context_refs.append(f"artifact://{repomap_block.ref}")
    contract = build_contract(
        None, decision, mode=mode, deadline_s=deadline_s,
        node=node, context_refs=context_refs, goal=L2_GOAL,
    )
    dispatch(ledger, task_id, contract, decision, run_id=run_id)
    report.l2_dispatched += 1

    blocks = [_candidate_block(root, cand, params.consult.l2_context_lines)]
    if repomap_block is not None:
        blocks.append(repomap_block)
    messages = render(Role.AUDITOR, contract=contract, blocks=blocks)
    current = agent_id
    output = None
    declined = False
    last_error = ""
    for _attempt in (1, 2):                       # §16.7-8：初派 1 + 重派 1
        try:
            raw = adapter.complete(
                model_ref=model_ref, messages=messages, temperature=EXAM_TEMPERATURE,
            )
            state = on_cost(ledger, run_id, policy.cost_per_l2_cents, policy=policy)
            report.spent_cents = state.spent_cents
            try:
                envelope = json.loads(raw.strip())
                if isinstance(envelope, dict) and envelope.get("declined") is True:
                    declined = True
                    break
            except ValueError:
                pass
            output = parse_output(Role.AUDITOR, raw)
            break
        except ValueError as exc:                 # SchemaRejected ⊂ ValueError
            last_error = f"schema:{exc}"
        except Exception as exc:                  # noqa: BLE001 — 供给失败族
            last_error = f"adapter:{type(exc).__name__}"
        nxt = _next_by_load(auditors, load, current)
        try:
            re_dispatch(
                ledger, task_id, to_agent=nxt,
                reason="schema_fail" if last_error.startswith("schema") else "timeout",
                run_id=run_id,
            )
            load[current] = max(0, load.get(current, 1) - 1)
            current = nxt
            load[nxt] = load.get(nxt, 0) + 1
            model_ref = _by_ref(auditors, nxt)
        except ValueError:                        # attempts 上限：任务自行置 failed
            if last_error.startswith("schema"):
                report.l2_schema_fail += 1
            else:
                report.l2_infra_fail += 1
            return None
    if declined:
        close_task_empty(ledger, task_id)
        report.l2_clean += 1
        return None
    if output is None:
        report.l2_schema_fail += 1
        return None
    try:
        events = submit(ledger, task_id, output, contract=contract, params=params,
                        run_id=run_id)
    except ValueError:                            # 语义失败（写侧非法）
        report.l2_schema_fail += 1
        return None
    report.findings_opened += 1
    for ev in events:                             # finding 事件面回读建档锚
        if ev.kind is LedgerKind.FINDING and ev.payload.get("finding_id"):
            return str(ev.payload["finding_id"])
    return None


# ── 面板投票（§2.3/§2.4）────────────────────────────────────────


def _run_panel(
    ledger: KernelLedger,
    *,
    root: Path,
    cluster_id: str,
    members: list[str],
    run_id: str,
    reviewers: list[tuple[str, str]],
    load: dict[str, int],
    finding_scouts: dict[str, list[str]],
    adapter,
    params,
    policy: BudgetPolicy,
    report: ConsultReport,
    deadline_s: int = 600,
) -> None:
    """簇级面板（D31）：排除报告者 → 构造 → 派票 → 收口（D32 因子）。"""
    anchor = members[0]
    member_facts = [_member_facts(ledger, m) for m in members]
    exclude = frozenset(m.reporter for m in member_facts)
    panel = build_panel(
        reviewers, exclude=exclude, load=load,
        panel_size=int(params.panel.SIZE), min_panel=int(params.consult.min_panel),
    )
    if not panel:
        report.panel_starved += 1
        return
    factor = s1_factor_for(panel, rho=float(params.consult.s1_rho))
    scout_ids = sorted({s for m in members for s in finding_scouts.get(m, ())})
    lineage = VerdictContext(                     # 纪律 2 守卫谱系（票向收齐后回填）
        members=tuple(member_facts), scouts=tuple(scout_ids), votes=(),
        s1_factor=factor,
    )
    # 编排方显式收口（M0 世界侧同款装配）：submit 面板参数放大阈值压制票中自动
    # 收口——收口需要完整 ctx.votes 谱系，逐票 submit 时无从装配（票向在 votes 表）。
    no_autoclose = params.model_copy(deep=True)
    no_autoclose.panel.SIZE = len(panel) + 99
    panel_params = params.model_copy(deep=True)
    panel_params.panel.SIZE = len(panel)          # 收口阈值随实际人数（D31 可变语义）
    anchor_row = ledger.conn.execute(
        "SELECT family, type FROM findings WHERE id = ?", (anchor,)
    ).fetchone()
    node = str(anchor_row[0])
    for i, reviewer in enumerate(panel):
        task_id = f"{VOTE_TASK_PREFIX}{anchor}:{i:02d}"
        if ledger.conn.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone() is not None:
            continue                              # 幂等
        model_ref = _by_ref(reviewers, reviewer)
        decision = RouteDecision(
            task_id=task_id, role=Role.REVIEWER.value,
            family=node.partition("/")[0], node=node, assignee=reviewer,
            channel="explore", reason="panel_least_loaded",
            tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
        )
        create_task(
            ledger, run_id=run_id, role=Role.REVIEWER.value, node=node,
            shard={"finding_id": anchor, "cluster_id": cluster_id,
                   "members": list(members), "panel_seq": i},
            task_id=task_id,
        )
        contract = build_contract(
            None, decision, mode=RunMode.FAST, deadline_s=deadline_s,
            node=node,
            context_refs=[f"finding://{anchor}", f"cluster://{cluster_id}"],
            goal="S1 评审：按四维独立打票（不复用报告者结论）",
        )
        dispatch(ledger, task_id, contract, decision, run_id=run_id)
        load[reviewer] = load.get(reviewer, 0) + 1
        messages = render(
            Role.REVIEWER, contract=contract,
            blocks=[_finding_block(ledger, root, anchor, members,
                                   params.consult.l2_context_lines)],
        )
        output = None
        last_error = ""
        for _attempt in (1, 2):
            try:
                raw = adapter.complete(
                    model_ref=model_ref, messages=messages,
                    temperature=EXAM_TEMPERATURE,
                )
                state = on_cost(ledger, run_id, policy.cost_per_s2_cents, policy=policy)
                report.spent_cents = state.spent_cents
                output = parse_output(Role.REVIEWER, raw)
                break
            except ValueError as exc:
                last_error = f"schema:{exc}"
            except Exception as exc:              # noqa: BLE001
                last_error = f"adapter:{type(exc).__name__}"
            nxt = _next_by_load(reviewers, load, reviewer)
            try:
                re_dispatch(
                    ledger, task_id, to_agent=nxt,
                    reason="schema_fail" if last_error.startswith("schema") else "timeout",
                    run_id=run_id,
                )
                reviewer = nxt
                model_ref = _by_ref(reviewers, nxt)
            except ValueError:
                break
        if output is None:
            continue                              # 单票失败：票数不足 → 星饿收口径
        try:
            submit(ledger, task_id, output, contract=contract, ctx=lineage,
                   params=no_autoclose, run_id=run_id)
        except ValueError:
            continue
    # 收齐票向后显式收口（§2.4：D32 因子在 ctx 上随收口落存款）。
    vote_rows = ledger.conn.execute(
        "SELECT reviewer, direction FROM votes WHERE finding_id = ? ORDER BY reviewer",
        (anchor,),
    ).fetchall()
    if len(vote_rows) < len(panel):
        return                                    # 票不足（失败票）→ 留 open 收口过期
    state_row = ledger.conn.execute(
        "SELECT state FROM findings WHERE id = ?", (anchor,)
    ).fetchone()
    if str(state_row[0]) == FindingState.OPEN.value:
        ctx = VerdictContext(
            members=tuple(member_facts), scouts=tuple(scout_ids),
            votes=tuple((str(r), int(d)) for r, d in vote_rows),
            s1_factor=factor,
        )
        close_panel(ledger, anchor, ctx=ctx, expected_votes=len(panel),
                    params=panel_params, run_id=run_id)
    report.panels_run += 1
    # 成员状态传播（§3.4；无票行 → propagate S1 幂等键不命中，零重复存款）
    for m in members:
        if m == anchor:
            continue
        row = ledger.conn.execute(
            "SELECT state FROM findings WHERE id = ?", (m,)
        ).fetchone()
        if str(row[0]) == FindingState.OPEN.value:
            transition_finding(ledger, m, FindingState.VOTED, run_id=run_id)
    report.findings_voted += len(members)
    rep = ledger.conn.execute(
        "SELECT confidence, disagreement_json FROM findings WHERE id = ?", (anchor,)
    ).fetchone()
    report.findings_detail.append({
        "finding_id": anchor, "cluster_id": cluster_id,
        "family": str(anchor_row[0]), "type": (str(anchor_row[1]) if anchor_row[1] else None),
        "severity": "", "confidence": (float(rep[0]) if rep[0] is not None else None),
        "panel": len(panel), "direction": 0, "disagreement": bool(rep[1]),
    })


# ── 预裁决单（§2.6；D29）────────────────────────────────────────


def _run_preverdict(
    ledger: KernelLedger,
    *,
    root: Path,
    cluster_id: str,
    members: list[str],
    run_id: str,
    verifiers: list[tuple[str, str]],
    load: dict[str, int],
    adapter,
    params,
    policy: BudgetPolicy,
    report: ConsultReport,
    store_dir: Path,
    deadline_s: int = 600,
) -> None:
    """每精聚类簇一张预裁决单（不走 tasks 状态机；工件面零存款）。"""
    if not verifiers:
        report.pv_skipped += 1
        return
    verifier = min((a for _r, a in verifiers), key=lambda a: (load.get(a, 0), a))
    load[verifier] = load.get(verifier, 0) + 1
    model_ref = _by_ref(verifiers, verifier)
    decision = RouteDecision(
        task_id=f"pv:{cluster_id}", role=Role.VERIFIER.value,
        family=cluster_id.partition("-")[0], node="preverdict", assignee=verifier,
        channel="explore", reason="preverdict_least_loaded",
        tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
    )
    contract = build_contract(
        None, decision, mode=RunMode.FAST, deadline_s=deadline_s,
        node="preverdict", context_refs=[f"cluster://{cluster_id}"],
        goal=(f"S2 预裁决（不执行 PoC）对簇 {cluster_id}：产出三字段预裁决单 "
              "expected_verdict/poc_shape/env_requirements"),
    )
    messages = render(
        Role.VERIFIER, contract=contract,
        blocks=[_cluster_block(ledger, root, members, params.consult.l2_context_lines)],
    )
    try:
        raw = adapter.complete(
            model_ref=model_ref, messages=messages, temperature=EXAM_TEMPERATURE,
        )
        state = on_cost(ledger, run_id, policy.cost_per_s2_cents, policy=policy)
        report.spent_cents = state.spent_cents
        slip = PreVerdictSlip.model_validate_json(extract_json(raw))
    except Exception:                             # noqa: BLE001 — 尽力而为面（D30 同款）
        report.pv_schema_fail += 1
        return
    artifact_put(
        ledger, store_dir,
        content=slip.model_dump_json().encode("utf-8"),
        mime="application/json", kind="pre_verdict", producer=verifier,
    )
    report.preverdicts += 1
    report.preverdict_detail.append({
        "cluster_id": cluster_id,
        "expected_verdict": slip.expected_verdict,
        "poc_shape": slip.poc_shape,
        "env_requirements": list(slip.env_requirements),
    })


# ── 主入口（§2.1/§2.7/§2.8）─────────────────────────────────────


def run_consult(
    ledger: KernelLedger,
    *,
    repo_root: str | Path,
    mode: str,
    run_id: str,
    scan_run_id: str,
    seed: int,
    adapter,
    store_dir: str | Path,
    params=None,
    clock: KernelClock | None = None,
    cache_dir: str | Path | None = None,
    deadline_s: int = 600,
    report_dir: str | Path | None = None,
) -> ConsultReport:
    """一轮 L2 会诊（verify_ready → open_run → 深诊 → 聚类 → 面板 → 预裁决 → 报告）。

    - ``scan_run_id``：L1 扫描 run（candidates triaged 行的归属 run）；
    - 幂等：任务 id 由 (run_id, candidate_id/finding_id) 派生，同参数重跑按
      「已 done 跳过、其余重派」收敛（§2.8）；
    - 预算：budget_for_mode(mode) 名义价；on_cost 逐次入账，budget_state 判熔断，
      尽 → 停新派单 + open finding 收口过期（§2.7）；
    - ``report_dir``：最小报告落盘目录（缺省 ``out/<run_id>``；只读环境静默跳过）。"""
    params = params if params is not None else load_params()
    ready = verify_ready(ledger)
    _ = ready
    clock = clock if clock is not None else WallClock()
    root = Path(repo_root)
    report = ConsultReport(run_id=run_id, mode=str(mode), scan_run_id=str(scan_run_id))
    policy = BudgetPolicy(
        mode=str(mode),
        budget_cents=budget_for_mode(mode, params),
        cost_per_l2_cents=int(params.budget.COST_PER_L2_CENTS),
        cost_per_s2_cents=int(params.budget.COST_PER_S2_CENTS),
        l2_cap=int(params.budget.L2_QUOTA_CAP),
    )
    open_run(ledger, run_id=run_id, mode=str(mode), repo_url=str(root),
             budget_cents=policy.budget_cents)

    t0 = time.monotonic()
    budget_s = int(params.consult.wall_clock_budget_s)

    def _time_left() -> bool:
        if time.monotonic() - t0 < budget_s:
            return True
        report.wall_clock_exceeded = True
        return False

    cands = _triaged_candidates(ledger, scan_run_id)
    report.candidates_total = len(cands)
    scouts = _l1_scout_lineage(ledger, scan_run_id)
    auditors = active_auditors(ledger)
    cells = view_cells(ledger, clock.now(), params=params)
    load: dict[str, int] = {}

    # repo map 地基（§1）：索引（缓存复用）→ 名称绑定图 → 每 anchor 一切片
    entries, sources = _index_repo_tree(root, cache_dir)
    entry_map = {e.path: e for e in entries}
    graph = build_name_graph(entries, sources)
    hop = {"fast": 0, "pr": 1, "full": 2}.get(str(mode), 0)

    finding_scouts: dict[str, list[str]] = {}     # finding_id → L1 侦察蜂谱系
    state = budget_state(report.spent_cents, policy, params=params)
    for seq, cand in enumerate(cands, start=1):
        if state.degraded or state.quota_l2 <= 0 or not _time_left():
            report.l2_quota_skipped += 1
            continue
        anchor_slice = slice_for(
            [(cand["path"], cand["span"][0])], graph, entry_map,
            hop=hop, max_files=int(params.perception.repomap.max_files),
            max_lines=int(params.perception.repomap.max_lines),
            sources=sources,
        )
        repomap_block = None
        if anchor_slice.files:
            artifact_put(
                ledger, store_dir,
                content=slice_to_json(anchor_slice).encode("utf-8"),
                mime="application/json", kind="repo_map",
                producer=f"repomap:{cand['candidate_id'][:12]}",
            )
            repomap_block = _repomap_block(root, anchor_slice)
        fid = _dispatch_l2_candidate(
            ledger, root=root, cand=cand, seq=seq, run_id=run_id, mode=str(mode),
            auditors=auditors, cells=cells, load=load,
            rng=random.Random(derive_seed(seed, "route", run_id, cand["candidate_id"])),
            adapter=adapter, params=params, policy=policy, report=report,
            repomap_block=repomap_block, deadline_s=deadline_s,
        )
        if fid:
            finding_scouts[fid] = scouts.get(cand["candidate_id"], ())
        state = budget_state(report.spent_cents, policy, params=params)

    # 粗聚类（§2.3）：本 run 新 finding 全量归属
    new_ids = [
        str(r[0]) for r in ledger.conn.execute(
            "SELECT id FROM findings WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
    ]
    facts = [_finding_facts(ledger, fid) for fid in new_ids]
    cluster_of: dict[str, str] = attribute_findings(
        ledger, facts, level="coarse", run_id=run_id
    ) if facts else {}
    coarse_clusters: dict[str, list[str]] = {}
    for fid, cid in cluster_of.items():
        coarse_clusters.setdefault(cid, []).append(fid)
    report.clusters_coarse = len(coarse_clusters)

    # 面板（每簇一投票，锚点收口 + 成员传播）
    reviewers = active_reviewers(ledger)
    for cid in sorted(coarse_clusters):
        if not _time_left():
            break
        _run_panel(
            ledger, root=root, cluster_id=cid, members=sorted(coarse_clusters[cid]),
            run_id=run_id, reviewers=reviewers, load=load, finding_scouts=finding_scouts,
            adapter=adapter, params=params, policy=policy, report=report,
            deadline_s=deadline_s,
        )

    # 精聚类（§2.5）：粗簇内 fine 提案落簇（Jaccard v1）
    fine_clusters: dict[str, list[str]] = {}
    for cid, members in coarse_clusters.items():
        mfacts = [_finding_facts(ledger, m) for m in members]
        proposals = cluster_findings(mfacts, level="fine", sim_fn=jaccard_sim)
        if proposals:
            apply_merges(ledger, proposals, run_id=run_id)
            merged: set[str] = set()
            for prop in proposals:
                fine_clusters[prop.member_ids[0]] = list(prop.member_ids)
                merged |= set(prop.member_ids)
            singles = [m for m in members if m not in merged]
            for s in singles:
                fine_clusters[s] = [s]
        else:
            for m in members:
                fine_clusters[m] = [m]
    report.clusters_fine = len(fine_clusters)

    # 预裁决单（§2.6）
    verifiers = active_verifiers(ledger)
    store = Path(store_dir)
    for anchor in sorted(fine_clusters):
        if not _time_left():
            report.pv_skipped += 1
            continue
        _run_preverdict(
            ledger, root=root, cluster_id=anchor, members=fine_clusters[anchor],
            run_id=run_id, verifiers=verifiers, load=load, adapter=adapter,
            params=params, policy=policy, report=report, store_dir=store,
            deadline_s=deadline_s,
        )

    # open 收口过期（§2.7：只 expired 未开面板的 open；voted 不动）
    for fid in new_ids:
        row = ledger.conn.execute(
            "SELECT state FROM findings WHERE id = ?", (fid,)
        ).fetchone()
        if str(row[0]) == FindingState.OPEN.value:
            transition_finding(ledger, fid, FindingState.EXPIRED, run_id=run_id)
            report.expired_open += 1

    # 存款计数（报告面对账）
    report.deposits_written = sum(
        1 for ev in ledger.read(kinds=("deposit",)) if ev.payload.get("origin") == "prod"
        and ev.run_id == run_id
    )
    report.shadow = shadow_report(ledger, as_of=clock.now(), params=params)

    # 最小报告落盘（§5）
    out_dir = Path(report_dir) if report_dir is not None else Path("out") / run_id
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "consult_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (out_dir / "consult_report.md").write_text(_report_md(report), encoding="utf-8")
    except OSError:
        pass                                      # 只读环境不阻塞编排（报告面尽力）
    return report
