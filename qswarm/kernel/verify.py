"""kernel.verify：S2 真验证独立编排——run_verify（SPEC-9 §4/§5；M5，D40-b/D43-a）。

D40-b 独立排程入口（消费精聚类簇/预裁决单，与 L2 墙钟解耦）：选取「全员 voted 且
尚无 verify_results 行」的簇 → s2_rank_key 降序（severity 主序 × 类型稀缺 × 置信）
→ 分批补验（max_clusters）。D39 verifier 蜂一体产：单次调用同回 VerifierReport
JSON（```json 围栏）与 PoC 脚本（```python 围栏）；PoC 缺围栏 → 家族模板兜底
（template_unsupported 如实计数，不硬凑）。

**D43-a 执行档落账口径（本模块第一纪律）**：``tier="container"`` 是 S2 生产存款
唯一口径——完整链路（tasks/DISPATCH → 蜂调用 → 沙箱 → verify_results 行 +
verify_result 事件 + §2.4 映射 + propagate_verdict ×0.5 存款，全部走 dispatch
既有写口）；``tier="process"``（开发/CI 档）**零账本写**——不 open_run、不建任务、
不落任何表行与事件、不写 artifacts 索引，只产工件文件与报告（G2 前置离线试跑同
款语义）。写口门 = :func:`assert_production_tier`（机器可检：process 档触碰生产
账本即 ValueError）。

内核 owned reconciliation（SPEC-9 §4.5）：蜂产 outcome 只是主张，沙箱执行是实证——
执行绿且蜂报 real → verified_real；执行失败 → verify_error（env_build_fail/
timeout 直接判，poc_fail vs flaky 由同 PoC 重复执行结果集判）；蜂报 fp 且携带
blocking（§10.4 正面证据）→ 维持 verified_fp（不依赖 PoC 绿，无 blocking 由
submit 的 §10.4 守卫强制改判）；其余失败一律 verify_error（复现失败 ≠ 误报）。
process 档无蜂主张时 outcome 纯由执行面产出（如实记档，不外推）。

预算（R7-①②）：quota_s2 存活数 = **active verifier 实编**（quota_l2 代理在独立
入口下不成立）；沙箱轮非 LLM 成本（env 构建墙钟 + 执行墙钟）入报告披露面
（sandbox_wall_s/env_build_s），与 D33 双轨（LLM 名义价）分列。

硬纪律（承 SPEC-7/8 逐字）：任务生命周期走 create_task/dispatch/submit，结论走
submit(verifier) 内核映射，**本模块零账本直写**；幂等 = 任务 id ``vf:{cluster_id}``
（已验簇天然排除在选取面外）。VerifierReport.attempts 语义 = 同 PoC 沙箱执行次数
（flaky 判据的重执行计入）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import (
    BlockingEvidence,
    FindingState,
    LedgerKind,
    Role,
    RunMode,
    VerifyErrorKind,
    VerifyOutcome,
    load_params,
)
from qswarm.contracts.models import (
    Assertion,
    DispatchContract,
    PreVerdictSlip,
    VerifierReport,
)
from qswarm.exams.store import artifact_get, artifact_put
from qswarm.kernel.bootstrap import verify_ready
from qswarm.kernel.budget import (
    BudgetPolicy,
    budget_state,
    on_cost,
    quota_s2,
    s2_rank_key,
)
from qswarm.kernel.cluster import MemberFacts, VerdictContext
from qswarm.kernel.consult import _active_by_role, _cluster_block, _spent_from_runs
from qswarm.kernel.dispatch import (
    build_contract,
    create_task,
    dispatch,
    open_run,
    submit,
)
from qswarm.kernel.ledger import KernelClock, KernelLedger, WallClock
from qswarm.kernel.route import RouteDecision
from qswarm.prompts import EXAM_TEMPERATURE, ContextBlock, parse_output, render
from qswarm.sandbox import (
    SandboxConfig,
    SandboxResult,
    extract_poc,
    make_runner,
    parse_assertions,
    template_poc,
)

__all__ = [
    "PRODUCTION_TIER",
    "VERIFY_GOAL",
    "VerifyRunReport",
    "assert_production_tier",
    "eligible_clusters",
    "reconcile",
    "run_verify",
]

#: D43-a：S2 生产存款唯一执行档（其余档触碰生产账本 = 写口违规）。
PRODUCTION_TIER = "container"

#: verifier 任务 id 前缀（幂等键：已验簇不再入选取面）。
VERIFY_TASK_PREFIX = "vf:"

#: S2 真验证 goal（四件套①；D39 一体产的双围栏约定在此对模型声明）。
VERIFY_GOAL = (
    "S2 真验证：在沙箱中复现指名簇的漏洞并产出三分类结构化结论。最终回答 = 两个"
    "代码围栏：第一个 ```json 围栏内为 VerifierReport JSON（schema 见系统提示）；"
    "随后一个 ```python 围栏内为可独立运行的 PoC 脚本——读取环境变量 REPO（被审"
    "仓库只读副本）与 WORK（可写目录）；断言逐条以行 \"ASSERT|名称|PASS|详情\""
    "（或 FAIL）打印到 stdout；退出码 0 = 断言全过。复现失败绝不改报 verified_fp"
    "（§6.3）。"
)


def assert_production_tier(tier: str) -> None:
    """D43-a 写口门（机器可检）：非 container 档触碰生产账本即拒绝。"""
    if str(tier) != PRODUCTION_TIER:
        raise ValueError(
            f"执行档 {tier!r} ≠ {PRODUCTION_TIER!r}：process 档不写生产账本"
            "（D43-a：只产工件与报告；S2 生产存款唯一口径 = container 档）"
        )


# ── 报告（SPEC-9 §4.7）──────────────────────────────────────────


@dataclass
class VerifyRunReport:
    """一轮真验证的循环报告（漏斗 + 失败如实计数 + R7 披露面）。"""

    run_id: str
    tier: str
    mode: str = "pr"
    clusters_eligible: int = 0
    clusters_selected: int = 0
    verified_real: int = 0
    verified_fp: int = 0
    verify_error: int = 0
    err_env_build_fail: int = 0
    err_poc_fail: int = 0
    err_timeout: int = 0
    err_flaky: int = 0
    skipped_budget: int = 0
    skipped_no_verifier: int = 0
    template_unsupported: int = 0
    bee_schema_fail: int = 0
    deposits_written: int = 0
    spent_cents: int = 0
    sandbox_wall_s: float = 0.0        # R7-② 披露面：沙箱执行墙钟（非 LLM）
    env_build_s: float = 0.0           # R7-② 披露面：env 构建墙钟（非 LLM）
    sandbox_runs: int = 0
    detail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "tier": self.tier,
            "mode": self.mode,
            "funnel": {
                "clusters_eligible": self.clusters_eligible,
                "clusters_selected": self.clusters_selected,
                "verified_real": self.verified_real,
                "verified_fp": self.verified_fp,
                "verify_error": self.verify_error,
                "skipped_budget": self.skipped_budget,
                "skipped_no_verifier": self.skipped_no_verifier,
            },
            "failures": {
                "err_env_build_fail": self.err_env_build_fail,
                "err_poc_fail": self.err_poc_fail,
                "err_timeout": self.err_timeout,
                "err_flaky": self.err_flaky,
                "template_unsupported": self.template_unsupported,
                "bee_schema_fail": self.bee_schema_fail,
            },
            "deposits_written": self.deposits_written,
            "spent_cents": self.spent_cents,
            "sandbox_disclosure": {                    # R7-②：非 LLM 成本如实分列
                "sandbox_wall_s": round(self.sandbox_wall_s, 3),
                "env_build_s": round(self.env_build_s, 3),
                "sandbox_runs": self.sandbox_runs,
                "note": "沙箱轮成本为非 LLM 资源（env 构建 CPU/墙钟 ×簇数），"
                        "与 D33 双轨 LLM 名义价分列（SPEC-9 §4.6）",
            },
            "detail": list(self.detail),
        }

    def to_md(self) -> str:
        d = self.to_dict()
        fu, fa = d["funnel"], d["failures"]
        lines = [
            f"# Verify Report — {self.run_id}",
            "",
            f"- tier: {self.tier} · mode: {self.mode} · 名义 {self.spent_cents} cents"
            "（D33 双轨；沙箱非 LLM 成本见 sandbox_disclosure）",
            "",
            "## 漏斗",
            "",
            f"可验簇 {fu['clusters_eligible']} → 选取 {fu['clusters_selected']}"
            f" → verified_real {fu['verified_real']} / verified_fp {fu['verified_fp']}"
            f" / verify_error {fu['verify_error']}"
            f"（预算跳过 {fu['skipped_budget']} / 无 verifier {fu['skipped_no_verifier']}）",
            "",
            "## 簇明细",
            "",
            "| 簇 | 结论 | error_kind | attempts | PoC 来源 | 比对 | 耗时 s |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in self.detail:
            lines.append(
                f"| {c['cluster_id']} | {c.get('outcome', '-')} | "
                f"{c.get('error_kind') or '-'} | {c.get('attempts', '-')} | "
                f"{c.get('poc_source', '-')} | {c.get('match', '-')} | "
                f"{round(float(c.get('duration_s', 0.0)), 1)} |"
            )
        lines += [
            "",
            "## 失败与披露（如实计数）",
            "",
            f"- env_build_fail {fa['err_env_build_fail']} / poc_fail {fa['err_poc_fail']}"
            f" / timeout {fa['err_timeout']} / flaky {fa['err_flaky']}"
            f" / template_unsupported {fa['template_unsupported']}"
            f" / bee_schema_fail {fa['bee_schema_fail']}",
            f"- 沙箱墙钟 {self.sandbox_wall_s:.1f}s（{self.sandbox_runs} 次）·"
            f" env 构建 {self.env_build_s:.1f}s（非 LLM 成本披露，R7-②）",
            "",
        ]
        return "\n".join(lines)


# ── 选取与谱系装配（只读）────────────────────────────────────────


def eligible_clusters(
    ledger: KernelLedger, *, consult_run_id: str | None = None
) -> list[dict]:
    """S2 可验簇选取面（只读）：簇内全员 voted ∧ 该簇尚无 verify_results 行。

    ``consult_run_id`` 给定时只取成员 finding 属于该 run 的簇（消费指定会诊轮；
    None = 全账本面）。返回 [{cluster_id, members, anchor}]（members/anchor 按
    finding_id 字典序；anchor = 首成员）。"""
    sql = (
        "SELECT cm.cluster_id, cm.finding_id, f.state FROM cluster_members cm"
        " JOIN findings f ON f.id = cm.finding_id"
    )
    args: list = []
    if consult_run_id is not None:
        sql += " WHERE f.run_id = ?"
        args.append(consult_run_id)
    sql += " ORDER BY cm.cluster_id, cm.finding_id"
    grouped: dict[str, list[tuple[str, str]]] = {}
    for cid, fid, state in ledger.conn.execute(sql, tuple(args)).fetchall():
        grouped.setdefault(str(cid), []).append((str(fid), str(state)))
    verified = {
        str(r[0])
        for r in ledger.conn.execute("SELECT DISTINCT cluster_id FROM verify_results")
    }
    out: list[dict] = []
    for cid in sorted(grouped):
        members = grouped[cid]
        if cid in verified:
            continue
        if any(state != FindingState.VOTED.value for _f, state in members):
            continue
        out.append(
            {
                "cluster_id": cid,
                "members": [fid for fid, _s in members],
                "anchor": members[0][0],
            }
        )
    return out


def _rank_clusters(ledger: KernelLedger, clusters: list[dict]) -> list[dict]:
    """s2_rank_key 降序（§2.4 D15：severity 主序 × 类型稀缺 × 置信；平手
    cluster_id 字典序）。"""
    counts: dict[str, int] = {}
    facts: dict[str, dict] = {}
    for c in clusters:
        row = ledger.conn.execute(
            "SELECT family, type, severity, confidence FROM findings WHERE id = ?",
            (c["anchor"],),
        ).fetchone()
        node = str(row[0]) if row[1] is None else f"{row[0]}/{row[1]}"
        facts[c["cluster_id"]] = {
            "node": node,
            "severity": str(row[2]),
            "confidence": float(row[3]),
        }
        counts[node] = counts.get(node, 0) + 1
    for c in clusters:
        f = facts[c["cluster_id"]]
        c["rank_key"] = s2_rank_key(
            f["severity"],
            f["node"] if "/" in f["node"] else None,
            f["confidence"],
            run_cluster_counts=counts,
        )
        c["node"] = f["node"]
    return sorted(
        clusters,
        key=lambda c: (-c["rank_key"][0], -c["rank_key"][1], -c["rank_key"][2],
                       c["cluster_id"]),
    )


def _latest_slip(
    ledger: KernelLedger, store_dir: Path, cluster_id: str
) -> PreVerdictSlip | None:
    """簇的最新预裁决单（artifacts kind=pre_verdict 内容反查；created_at 序取最新）。"""
    rows = ledger.conn.execute(
        "SELECT hash, created_at FROM artifacts WHERE kind = 'pre_verdict'"
        " ORDER BY created_at, hash",
    ).fetchall()
    found: PreVerdictSlip | None = None
    for h, _ts in rows:
        content = artifact_get(ledger, store_dir, str(h))
        if content is None:
            continue
        try:
            slip = PreVerdictSlip.model_validate_json(content)
        except ValueError:
            continue
        if slip.cluster_id == cluster_id:
            found = slip                       # created_at 升序 → 最后一命中即最新
    return found


def _cluster_scouts(ledger: KernelLedger, cluster_id: str) -> list[str]:
    """簇侦察谱系回读（append-only 真源）：S1 存款事件 role=scout ∧ finding=None
    行的 agent 集（M4 面板侦察行的账本回放形态）。"""
    out: set[str] = set()
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        p = ev.payload
        if (
            p.get("cluster") == cluster_id
            and p.get("signal_tier") == "S1"
            and p.get("role") == Role.SCOUT.value
            and p.get("finding") is None
        ):
            out.add(str(p.get("agent")))
    return sorted(out)


def _cluster_votes(ledger: KernelLedger, members: list[str]) -> list[tuple[str, int]]:
    """簇评审回校谱系（votes 表；同评审多成员取 finding_id 字典序首个方向）。"""
    picked: dict[str, int] = {}
    for fid in members:
        rows = ledger.conn.execute(
            "SELECT reviewer, direction FROM votes WHERE finding_id = ?"
            " ORDER BY reviewer",
            (fid,),
        ).fetchall()
        for reviewer, direction in rows:
            if reviewer not in picked:
                picked[str(reviewer)] = int(direction)
    return sorted(picked.items())


def _expected_from_cluster(ledger: KernelLedger, cluster_id: str) -> str:
    """无预裁决单时的期望回退：clusters.verdict_dir 投影（+1→confirmed /
    −1→refuted / 无→inconclusive；expected_source 如实记 's1_projection'）。"""
    row = ledger.conn.execute(
        "SELECT verdict_dir FROM clusters WHERE cluster_id = ?", (cluster_id,)
    ).fetchone()
    direction = int(row[0]) if row is not None and row[0] is not None else 0
    if direction > 0:
        return "confirmed"
    if direction < 0:
        return "refuted"
    return "inconclusive"


# ── 内核 owned reconciliation（SPEC-9 §4.5；纯函数）──────────────


def reconcile(
    bee_outcome: VerifyOutcome | None,
    bee_error_kind: VerifyErrorKind | None,
    blocking: BlockingEvidence | None,
    exec_result: SandboxResult | None,
    *,
    flaky: bool,
    poc_available: bool,
) -> tuple[VerifyOutcome, VerifyErrorKind | None]:
    """蜂主张 × 沙箱实证 → 内核裁定（§6.3 复现失败≠误报；§10.4 blocking 正面证据）。

    - 无 PoC 可执行：蜂报 verify_error → 如实保留；否则降格 verify_error(poc_fail)
      （不可证实的主张不落 real/fp）；
    - env 构建 / 超时：直接判 verify_error 对应类；
    - 执行绿：蜂报 real（或 bee=None 无主张面——process 档试跑，纯执行语义）
      → verified_real；蜂报 fp → 维持 fp（无 blocking 时由 submit 的 §10.4 守卫
      强制改判，不在此重复）；蜂报 error → 如实保留（保守不升格——蜂不认 real
      就不替它认）；
    - 执行红：蜂报 fp 且带 blocking → verified_fp（正面证据独立于 PoC 绿）；flaky →
      verify_error(flaky)；其余 → verify_error(poc_fail)。"""
    if not poc_available or exec_result is None:
        if bee_outcome is VerifyOutcome.VERIFY_ERROR:
            return VerifyOutcome.VERIFY_ERROR, (
                bee_error_kind or VerifyErrorKind.POC_FAIL
            )
        return VerifyOutcome.VERIFY_ERROR, VerifyErrorKind.POC_FAIL
    if exec_result.env_build_failed:
        return VerifyOutcome.VERIFY_ERROR, VerifyErrorKind.ENV_BUILD_FAIL
    if exec_result.timed_out:
        return VerifyOutcome.VERIFY_ERROR, VerifyErrorKind.TIMEOUT
    if exec_result.ok:
        if bee_outcome is VerifyOutcome.VERIFIED_REAL or bee_outcome is None:
            # bee=None = 无蜂主张面（process 档试跑）：outcome 纯由执行面产出
            # （模块 docstring 逐字「不外推」——绿执行即复现成立，如实记档）。
            return VerifyOutcome.VERIFIED_REAL, None
        if bee_outcome is VerifyOutcome.VERIFY_ERROR:
            return VerifyOutcome.VERIFY_ERROR, (
                bee_error_kind or VerifyErrorKind.POC_FAIL
            )
        return VerifyOutcome.VERIFIED_FP, None
    if bee_outcome is VerifyOutcome.VERIFIED_FP and blocking is not None:
        return VerifyOutcome.VERIFIED_FP, None
    if flaky:
        return VerifyOutcome.VERIFY_ERROR, VerifyErrorKind.FLAKY
    return VerifyOutcome.VERIFY_ERROR, VerifyErrorKind.POC_FAIL


# ── 工件面（container=账本索引库；process=纯文件零索引）────────────


def _trial_store_put(store_dir: Path, cluster_id: str, name: str, content: str) -> str:
    """process 档工件落盘（零账本索引；路径 store/trial/<cluster>/<name>）。
    返回引用串 ``trial://<cluster>/<name>``（与 artifact:// 同形语义，档位可辨）。"""
    path = Path(store_dir) / "trial" / cluster_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"trial://{cluster_id}/{name}"


def _match(expected: str, outcome: VerifyOutcome) -> bool | None:
    """已知答案比对（G2 试跑判据；inconclusive 期望不判 → None 如实）。"""
    if expected == "confirmed":
        return outcome is VerifyOutcome.VERIFIED_REAL
    if expected == "refuted":
        return outcome is VerifyOutcome.VERIFIED_FP
    return None


# ── 主入口（§4.1）───────────────────────────────────────────────


def run_verify(
    ledger: KernelLedger,
    *,
    repo_root: str | Path,
    run_id: str,
    consult_run_id: str | None = None,
    mode: str = "pr",
    seed: int = 0,
    adapter=None,
    store_dir: str | Path,
    params=None,
    clock: KernelClock | None = None,
    tier: str | None = None,
    runner=None,
    report_dir: str | Path | None = None,
    max_clusters: int | None = None,
    monotonic=time.monotonic,
) -> VerifyRunReport:
    """一轮 S2 真验证（D40-b 独立入口；D43 双档；幂等 = 已验簇不入选取面）。

    - ``tier``：None → params.sandbox.tier；container = 生产全链（需 adapter 与
      active verifier 编制）；process = 开发/CI 档（**零账本写**，G2 离线试跑同款，
      adapter 可注入做生成面演练）；
    - ``runner``：SandboxRunner 注入（测试 FakeRunner；None → 按配置实装）；
    - ``monotonic``：墙钟注入（M4 判据缺口①的机器可检收口）。"""
    params = params if params is not None else load_params()
    cfg = SandboxConfig.from_params(params, tier=tier)
    production = cfg.tier == PRODUCTION_TIER
    if production:
        assert_production_tier(cfg.tier)
    clock = clock if clock is not None else WallClock()
    root = Path(repo_root)
    store = Path(store_dir)
    report = VerifyRunReport(run_id=run_id, tier=cfg.tier, mode=str(mode))
    policy: BudgetPolicy | None = None
    verifiers = _active_by_role(ledger, Role.VERIFIER.value)

    if production:
        verify_ready(ledger)
        if adapter is None:
            raise ValueError("container 档生产全链需要 adapter（真蜂调用面）")
        policy = BudgetPolicy(
            mode=str(mode),
            budget_cents=int(params.verify.budget_cents),
            cost_per_l2_cents=0,
            cost_per_s2_cents=int(params.budget.COST_PER_S2_CENTS),
            l2_cap=int(params.budget.L2_QUOTA_CAP),
        )
        open_run(ledger, run_id=run_id, mode=str(mode), repo_url=str(root),
                 budget_cents=policy.budget_cents)

    if runner is None:
        runner = make_runner(cfg)
    budget_s = int(params.verify.wall_clock_budget_s)
    t0 = monotonic()

    def _time_left() -> bool:
        return monotonic() - t0 < budget_s

    clusters = eligible_clusters(ledger, consult_run_id=consult_run_id)
    report.clusters_eligible = len(clusters)
    if production and not verifiers:
        report.skipped_no_verifier = len(clusters)   # fail-closed：如实缺席不硬凑
        _write_report(report, report_dir)
        return report
    clusters = _rank_clusters(ledger, clusters)
    cap = int(max_clusters if max_clusters is not None else params.verify.max_clusters)
    clusters = clusters[:cap] if cap > 0 else []
    report.clusters_selected = len(clusters)

    spent = _spent_from_runs(ledger, run_id) if production else 0

    for c in clusters:
        if not _time_left():
            report.skipped_budget += 1
            continue
        if policy is not None:
            state = budget_state(spent, policy, params=params)
            if (
                state.degraded
                or quota_s2(
                    len(verifiers), policy.budget_cents - spent,
                    policy.cost_per_s2_cents,
                ) <= 0
            ):
                report.skipped_budget += 1
                continue
        spent = _verify_one(
            ledger,
            cluster=c,
            root=root,
            store=store,
            run_id=run_id,
            mode=str(mode),
            production=production,
            verifiers=verifiers,
            adapter=adapter,
            runner=runner,
            params=params,
            policy=policy,
            report=report,
            seed=seed,
        )

    report.env_build_s = float(getattr(runner, "env_build_s", 0.0) or 0.0)
    if production:
        report.spent_cents = spent
        report.deposits_written = sum(
            1
            for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,))
            if ev.payload.get("origin") == "prod"
            and ev.payload.get("signal_tier") == "S2"
            and ev.run_id == run_id
        )
    _write_report(report, report_dir)
    return report


def _write_report(report: VerifyRunReport, report_dir) -> None:
    if report_dir is None:
        return
    try:
        out = Path(report_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "verify_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (out / "verify_report.md").write_text(report.to_md(), encoding="utf-8")
    except OSError:
        pass                                      # 只读环境不阻塞编排


# ── 单簇全生命周期（§4.2–§4.5）──────────────────────────────────


def _verify_one(
    ledger: KernelLedger,
    *,
    cluster: dict,
    root: Path,
    store: Path,
    run_id: str,
    mode: str,
    production: bool,
    verifiers: list[tuple[str, str]],
    adapter,
    runner,
    params,
    policy: BudgetPolicy | None,
    report: VerifyRunReport,
    seed: int,
) -> int:
    """单簇：材料装配 → 蜂一体产（D39，可选）→ 沙箱执行 ×attempts → reconcile →
    container 档 submit / process 档纯工件。返回更新后 spent。"""
    cid = cluster["cluster_id"]
    members: list[str] = cluster["members"]
    anchor: str = cluster["anchor"]
    arow = ledger.conn.execute(
        "SELECT family, type, path, line_start, line_end, lang"
        " FROM findings WHERE id = ?",
        (anchor,),
    ).fetchone()
    family, ftype = str(arow[0]), (str(arow[1]) if arow[1] else None)
    slip = _latest_slip(ledger, store, cid)
    if slip is None:
        # M4 pv 身份口径 = 精聚类锚 finding id（fine_clusters 以 member_ids[0] 为键，
        # SPEC-8 §2.6 实装形态）——M5 消费双查：C 簇 id → 锚 finding id。
        slip = _latest_slip(ledger, store, anchor)
    expected = (
        slip.expected_verdict if slip is not None
        else _expected_from_cluster(ledger, cid)
    )
    expected_source = "pre_verdict" if slip is not None else "s1_projection"
    deps = list(slip.env_requirements) if slip is not None else []

    # ── 蜂一体产（D39；无 adapter（G2 试跑）直接走模板面）──────────
    bee_report: VerifierReport | None = None
    poc_script: str | None = None
    poc_source = "template"
    task_id: str | None = None
    contract: DispatchContract | None = None
    if adapter is not None and verifiers:
        task_id = f"{VERIFY_TASK_PREFIX}{cid}"
        if production and ledger.conn.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone() is not None:
            return _spent_from_runs(ledger, run_id)     # 幂等：重跑跳过已建任务
        model_ref = _by_ref(verifiers, min(a for _r, a in verifiers))
        decision = RouteDecision(
            task_id=task_id, role=Role.VERIFIER.value,
            family=family, node=f"{family}/{ftype}" if ftype else family,
            assignee=min(a for _r, a in verifiers),
            channel="explore", reason="verify_least_loaded",
            tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
        )
        blocks = [
            _cluster_block(ledger, root, members, params.consult.l2_context_lines)
        ]
        if slip is not None:
            blocks.append(ContextBlock(
                ref=f"preverdict://{cid}",
                content="预裁决单（S2 纸面预判，供参考）：\n"
                        + slip.model_dump_json(indent=2),
            ))
        if production:
            create_task(
                ledger, run_id=run_id, role=Role.VERIFIER.value,
                node=decision.node,
                shard={"cluster_id": cid, "members": list(members),
                       "expected": expected, "deps": deps},
                task_id=task_id,
            )
            contract = build_contract(
                None, decision, mode=RunMode.FAST,
                deadline_s=int(params.verify.wall_clock_budget_s),
                node=decision.node, goal=VERIFY_GOAL,
                context_refs=[f"cluster://{cid}"],
            )
            dispatch(ledger, task_id, contract, decision, run_id=run_id)
        else:
            contract = DispatchContract(
                task_id=task_id, role=Role.VERIFIER.value, goal=VERIFY_GOAL,
                output_schema_ref="VerifierReport",
                tool_bounds=["repo:ro", "sandbox:rw"],
                task_bounds=(
                    "只对指名簇做沙箱复现并产三分类结构化结果；不投票、不产 finding。"
                ),
                context_refs=[f"cluster://{cid}"], taxonomy_node=decision.node,
                hop_budget=0,
                deadline_s=int(params.verify.wall_clock_budget_s),
            )
        messages = render(Role.VERIFIER, contract=contract, blocks=blocks)
        try:
            raw = adapter.complete(
                model_ref=model_ref, messages=messages,
                temperature=EXAM_TEMPERATURE,
            )
            if production:
                state = on_cost(ledger, run_id, policy.cost_per_s2_cents,
                                policy=policy)
            bee_report = parse_output(Role.VERIFIER, raw)
            poc = extract_poc(raw)
            if poc:
                poc_script = poc
                poc_source = "bee"
        except Exception as exc:                  # noqa: BLE001 — SchemaRejected ⊂
            # ValueError + 供给失败族（AdapterError）：蜂面失败 → 模板兜底，不硬凑
            report.bee_schema_fail += 1
            _ = exc

    # ── PoC 来源（D39 一体产 → 模板兜底 → 如实 none）──────────────
    if poc_script is None:
        poc_script = template_poc(
            family, ftype, path=str(arow[2]), line_start=int(arow[3]),
            lang=str(arow[5]), seed=str(seed),
        )
        if poc_script is not None:
            poc_source = "template"
        else:
            poc_source = "none"
            report.template_unsupported += 1

    # ── 沙箱执行（§6.3 四分类；flaky 判据 = 同 PoC 重复执行结果集）──
    exec_result: SandboxResult | None = None
    attempts = 0
    flaky = False
    total_dur = 0.0
    if poc_source != "none":
        max_attempts = max(1, int(params.verify.poc_attempts))
        exec_result = runner.execute(
            poc_script, repo=root, deps=deps, seed=f"{seed}-{1}"
        )
        attempts = 1
        report.sandbox_runs += 1
        report.sandbox_wall_s += float(exec_result.duration_s)
        total_dur += float(exec_result.duration_s)
        if (
            classify_ok(exec_result) and not exec_result.ok
            and attempts < max_attempts
        ):
            # 执行红：同 PoC 重执行一次判 flaky（结果集 >1）vs 稳定 poc_fail。
            probe = runner.execute(
                poc_script, repo=root, deps=deps, seed=f"{seed}-flaky"
            )
            attempts += 1
            report.sandbox_runs += 1
            report.sandbox_wall_s += float(probe.duration_s)
            total_dur += float(probe.duration_s)
            flaky = probe.ok != exec_result.ok
            if not flaky:
                exec_result = probe        # 取末次读数（同型失败）

    outcome, error_kind = reconcile(
        bee_report.outcome if bee_report else None,
        bee_report.error_kind if bee_report else None,
        bee_report.blocking if bee_report else None,
        exec_result, flaky=flaky, poc_available=(poc_source != "none"),
    )
    logs_text = exec_result.log_text if exec_result is not None else (
        f"no executable PoC (source={poc_source}) for cluster {cid}"
    )
    assertions: list[Assertion] = []
    if exec_result is not None:
        parsed = parse_assertions(exec_result.stdout)
        if parsed:
            assertions = [
                Assertion(expect=name, observed=bool(observed), log="artifact://pending")
                for name, observed, _detail in parsed
            ]
        else:
            assertions = [Assertion(
                expect="poc_exit_0", observed=bool(exec_result.ok),
                log="artifact://pending",
            )]

    # ── 落账（container）/ 纯工件（process）──────────────────────
    if production:
        deps_ref = artifact_put(
            ledger, store,
            content=("\n".join(deps) if deps else "# (no deps)").encode("utf-8"),
            mime="text/plain", kind="poc_deps", producer=f"verify:{cid}",
        )
        logs_ref = artifact_put(
            ledger, store, content=logs_text.encode("utf-8"),
            mime="text/plain", kind="poc_log", producer=f"verify:{cid}",
        )
        for a in assertions:
            a.log = f"artifact://{logs_ref}"      # 派单方回填通道（模板 §2.2 逐字）
        artifact_put(
            ledger, store, content=(poc_script or "# (no executable PoC)").encode("utf-8"),
            mime="text/x-python", kind="poc_script", producer=f"verify:{cid}",
        )
        vr = VerifierReport(
            cluster_id=cid,
            outcome=outcome,
            env_base=exec_result.env_base if exec_result is not None else "unknown",
            deps_ref=f"artifact://{deps_ref}",
            trigger_path=(
                list(bee_report.trigger_path)
                if bee_report is not None and bee_report.trigger_path
                else [f"entry: {arow[2]}:{arow[3]}", f"sink: {arow[2]}:{arow[3]}"]
            ),
            assertions=assertions,
            blocking=bee_report.blocking if bee_report is not None else None,
            error_kind=error_kind,
            attempts=attempts,
            duration_s=total_dur,
            logs_ref=f"artifact://{logs_ref}",
        )
        submit(
            ledger,
            task_id if task_id is not None else f"{VERIFY_TASK_PREFIX}{cid}",
            vr,
            contract=contract,
            ctx=VerdictContext(
                members=tuple(_member_facts(ledger, members)),
                scouts=tuple(_cluster_scouts(ledger, cid)),
                votes=tuple(_cluster_votes(ledger, members)),
            ),
            params=params,
            run_id=run_id,
        )
    else:
        _trial_store_put(store, cid, "poc.py", poc_script or "")
        _trial_store_put(store, cid, "run.log", logs_text)
        _trial_store_put(
            store, cid, "result.json",
            json.dumps(
                {
                    "cluster_id": cid, "outcome": outcome.value,
                    "error_kind": (error_kind.value if error_kind else None),
                    "expected": expected, "expected_source": expected_source,
                    "poc_source": poc_source, "attempts": attempts,
                    "duration_s": total_dur,
                    "mitigations": (
                        exec_result.mitigations if exec_result is not None else {}
                    ),
                },
                ensure_ascii=False, indent=2, sort_keys=True,
            ),
        )

    report.detail.append({
        "cluster_id": cid,
        "outcome": outcome.value,
        "error_kind": (error_kind.value if error_kind else None),
        "attempts": attempts,
        "poc_source": poc_source,
        "duration_s": round(total_dur, 3),
        "expected": expected,
        "expected_source": expected_source,
        "match": _match(expected, outcome),
    })
    if outcome is VerifyOutcome.VERIFIED_REAL:
        report.verified_real += 1
    elif outcome is VerifyOutcome.VERIFIED_FP:
        report.verified_fp += 1
    else:
        report.verify_error += 1
        if error_kind is VerifyErrorKind.ENV_BUILD_FAIL:
            report.err_env_build_fail += 1
        elif error_kind is VerifyErrorKind.POC_FAIL:
            report.err_poc_fail += 1
        elif error_kind is VerifyErrorKind.TIMEOUT:
            report.err_timeout += 1
        elif error_kind is VerifyErrorKind.FLAKY:
            report.err_flaky += 1
    return _spent_from_runs(ledger, run_id) if production else 0


def classify_ok(result: SandboxResult) -> bool:
    """runner 侧不可重试类判定：env_build_fail/timeout → False（不再 flaky 重试）。"""
    return result.error_kind is None


# ── 小装配件 ────────────────────────────────────────────────────


def _member_facts(ledger: KernelLedger, members: list[str]) -> list[MemberFacts]:
    out = []
    for fid in members:
        row = ledger.conn.execute(
            "SELECT reporter, reporter_role FROM findings WHERE id = ?", (fid,)
        ).fetchone()
        out.append(
            MemberFacts(finding_id=fid, reporter=str(row[0]),
                        reporter_role=str(row[1]))
        )
    return out


def _by_ref(pool: list[tuple[str, str]], agent_id: str) -> str:
    for model_ref, agent in pool:
        if agent == agent_id:
            return model_ref
    return ""
