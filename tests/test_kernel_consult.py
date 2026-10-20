"""kernel.consult 测试（SPEC-8 §2/§3/§4；M4 卷 A/C/D 组判据）。

钉死：面板构造（D31 排除报告者/least-loaded/互补模型/fail-closed）、D32 因子数值
（0.6/0.8/1.0）、精聚类 Jaccard 判据、影子指标只读、e2e 全链（L1 存活点 → route()
派 auditor → finding → 粗聚类 → 面板投票 → voted + S1 存款含因子 → 精聚类 →
预裁决单 → 报告落盘）、同参数重跑账本级幂等、panel_starved fail-closed、
declined 干净候选面。
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind, SignalTier, load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger
from qswarm.kernel.consult import (
    build_panel,
    jaccard_sim,
    resolve_real_model,
    run_consult,
    s1_factor_for,
    shadow_report,
)
from qswarm.kernel.cluster import FindingFacts
from qswarm.kernel.loop import run_scan
from qswarm.kernel.route import RouteDecision

import m3_harness
from m3_harness import ScriptedScoutAdapter, fake_rules_ok
from qswarm.exams.roster import attach_hat

T0 = m3_harness.T0

# m2_harness 参考名册展开的四蜂（agent_id = tier:provider/model@cv1）
AUDITOR = "strong:anthropic/claude-sonnet-4-5@cv1"
AUDITOR2 = "strong:openai/gpt-4o@cv1"
SCOUT_A = "cheap:deepseek/deepseek-chat@cv1"
SCOUT_B = "cheap:openai/gpt-4o-mini@cv1"


@pytest.fixture()
def prod(tmp_path):
    """就绪生产账本 + 小仓库 + L1 已跑完的扫描 run。"""
    seed_db = m3_harness.make_seed_db(tmp_path / "seed.db", tmp_path / "bank")
    init_prod_ledger(tmp_path / "prod.db", seed_db_path=seed_db, clock=FixedClock(T0))
    repo = m3_harness.make_repo(tmp_path / "repo")
    params = load_params()
    params.perception.model.enabled = False               # L1 焦点：通道 B 关
    with KernelLedger(tmp_path / "prod.db", clock=FixedClock(T0)) as led:
        run_scan(
            led, repo_root=repo, mode="full", run_id="R-SCAN",
            params=params, adapter=ScriptedScoutAdapter(), store_dir=tmp_path / "store",
            rules_runner=fake_rules_ok, clock=FixedClock(T0), promote=False,
        )
        n_triaged = led.conn.execute(
            "SELECT COUNT(*) FROM candidates WHERE triaged = 1"
        ).fetchone()[0]
    assert n_triaged > 0
    return tmp_path, repo, params, n_triaged


def _add_hats(led):
    """帽兼任扩池（D31）：两侦察蜂 + auditor 蜂加戴 reviewer 帽（报告者排除的
    强断言前提——auditor 兼 reviewer 时不得评审自己的 finding）+ verifier 帽。"""
    attach_hat(led, SCOUT_A, "reviewer")
    attach_hat(led, SCOUT_B, "reviewer")
    attach_hat(led, AUDITOR, "reviewer")
    attach_hat(led, SCOUT_A, "verifier")


# ── 单元：面板 / 因子 / Jaccard ──────────────────────────────────


def test_build_panel_diversity_and_exclusion():
    """D31：排除报告者、least-loaded 平手 agent_id、互补真实服务模型贪心。"""
    pool = [
        ("mid:zhipu/glm-5.3@cv1", "mid:zhipu/glm-5.3@cv1"),
        ("mid:zhipu/glm-5.3-flash@cv1", "mid:zhipu/glm-5.3-flash@cv1"),
        ("cheap:zhipu/glm-4.5-air@cv1", "cheap:zhipu/glm-4.5-air@cv1"),
        ("strong:zhipu/glm-5.2@cv1", "strong:zhipu/glm-5.2@cv1"),
    ]
    panel = build_panel(pool, exclude=frozenset({"strong:zhipu/glm-5.2@cv1"}),
                        load={}, panel_size=3, min_panel=2)
    assert len(panel) == 3
    assert "strong:zhipu/glm-5.2@cv1" not in panel        # 报告者被排除
    # 互补贪心：air(5.3-flash) → mid:5.3(glm-5.3) → 剩余 mid:flash（真实模型两档覆盖）
    assert panel == [
        "cheap:zhipu/glm-4.5-air@cv1",
        "mid:zhipu/glm-5.3@cv1",
        "mid:zhipu/glm-5.3-flash@cv1",
    ]
    assert {resolve_real_model(a) for a in panel} == {"glm-5.3", "glm-5.3-flash"}


def test_build_panel_failclosed():
    """池 <min_panel → 空面板（fail-closed 不硬凑）。"""
    pool = [("cheap:zhipu/glm-4.5-air@cv1", SCOUT_A)]
    assert build_panel(pool, exclude=frozenset(), load={},
                       panel_size=3, min_panel=2) == []
    assert build_panel(pool, exclude=frozenset({SCOUT_A}), load={},
                       panel_size=3, min_panel=2) == []


def test_s1_factor_values_pinned():
    """D32 数值钉：3 人同模型 0.6 / 3 人两模型 0.8 / 全异 1.0 / 2 人同模型 0.6。"""
    same3 = ["a", "b", "c"]                               # 全 unknown → 1 个 distinct
    assert s1_factor_for(same3, rho=0.4) == pytest.approx(0.6)
    mixed3 = ["strong:x/glm-5.2@cv1", "strong:x/glm-5@cv1", "cheap:x/glm-4.5-air@cv1"]
    assert s1_factor_for(mixed3, rho=0.4) == pytest.approx(0.8)   # 5.3/5.3-flash/unknown
    all_diff = ["strong:x/glm-5.2@cv1", "mid:x/glm-5.3@cv1", "cheap:x/glm-4.5-air@cv1"]
    # glm-5.2/glm-5→5.3、air→5.3-flash、glm-5.3→5.3：仍 2 distinct → 0.8
    assert s1_factor_for(all_diff, rho=0.4) == pytest.approx(0.8)
    assert s1_factor_for(["a", "b"], rho=0.4) == pytest.approx(0.6)
    assert s1_factor_for(["a"], rho=0.4) == 1.0


def _facts(fid, path, etype, wheres):
    return FindingFacts(
        finding_id=fid, path=path, line_start=1, line_end=10, cwe=None,
        lang="python", family="injection", type=etype,
        sim_text="t|" + "|".join(f"source:{w}:artifact://p" for w in wheres),
    )


def test_jaccard_fine_merge_threshold():
    """sim_fn v1：同路径同证据词面 ≥0.9 可并；不同路径/证据不并。"""
    a = _facts("F-1", "src/api.py", "sqli", ["src/api.py", "src/db.py"])
    b = _facts("F-2", "src/api.py", "sqli", ["src/api.py", "src/db.py"])
    assert jaccard_sim(a, b) == pytest.approx(1.0)
    c = _facts("F-3", "src/other.py", "sqli", ["src/other.py"])
    assert jaccard_sim(a, c) < 0.9


def test_shadow_report_readonly(prod):
    """影子指标纯读：view_cells 只读形态，cells/账本零物理写。"""
    tmp, repo, params, _n = prod
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        before = led.conn.total_changes
        snap = shadow_report(led, as_of=T0, params=params)
        assert led.conn.total_changes == before
        assert snap["entry_knobs_zeroed"] is True
        assert snap["exam_callback_ready"] is True
        assert snap["cells_by_status"]                    # 考试存款折叠出格子


# ── 脚本化会诊适配器（零网络）────────────────────────────────────


class ScriptedConsultAdapter:
    """auditor/reviewer/verifier 三面脚本化适配器。

    - 场景识别：system 消息含「审查蜂 / 评审蜂 / 验证蜂」；
    - ``audit_script(path) -> dict``：AuditorFinding 形状字典或 {"declined": True}；
    - ``vote_script(finding_ref) -> dict``：ReviewerVote 形状；
    - ``pv_script() -> dict``：PreVerdictSlip 形状；
    - ``calls`` 记录 (场景, 标识)。"""

    def __init__(self, audit_script=None, vote_direction=1, pv_verdict="confirmed"):
        self.audit_script = audit_script or {}
        self.vote_direction = vote_direction
        self.pv_verdict = pv_verdict
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _finding(candidate_id):
        return {
            "title": f"test finding {candidate_id}",
            "severity": "high",
            "cwe": None,
            "family": "injection",
            "type": "sqli",
            "evidence_chain": [
                {"kind": "source", "where_path": "src/api.py", "where_line": 7,
                 "found": True, "ref": "artifact://pending"},
                {"kind": "propagation", "where_path": "src/api.py", "where_line": 8,
                 "found": True, "ref": "artifact://pending"},
                {"kind": "sanitizer", "where_path": "src/api.py", "where_line": 9,
                 "found": False, "ref": "artifact://pending"},
                {"kind": "sink", "where_path": "src/api.py", "where_line": 10,
                 "found": True, "ref": "artifact://pending"},
            ],
            "confidence": 0.8,
            "artifacts": ["artifact://pending"],
        }

    def complete(self, *, model_ref, messages, temperature=0.0, **_kw):
        system = messages[0].content
        user = messages[1].content
        if "审查蜂" in system:
            path = Path(user.split("path: ", 1)[1].split("\n", 1)[0]).name
            self.calls.append(("audit", path))
            scripted = self.audit_script.get(path)
            if scripted is None:
                return json.dumps(self._finding(path))
            return json.dumps(scripted)
        if "评审蜂" in system:
            ref = ""
            for line in user.splitlines():
                if line.startswith("finding_id: "):
                    ref = line.split(": ", 1)[1]
            self.calls.append(("vote", ref))
            return json.dumps({
                "dim_completeness": 0.8, "dim_reachability": 0.8,
                "dim_exploitability": 0.7, "dim_fp_suspicion": 0.2,
                "direction": self.vote_direction,
                "comment_ref": "artifact://pending",
            })
        if "验证蜂" in system:
            self.calls.append(("pv", ""))
            return json.dumps({
                "cluster_id": user.split("簇 ", 1)[1].split("：", 1)[0].split(" ", 1)[0],
                "expected_verdict": self.pv_verdict,
                "poc_shape": "http request with payload",
                "env_requirements": ["python3", "flask"],
            })
        raise AssertionError(f"未知会诊场景：{system[:40]}")


# ── e2e 全链 ─────────────────────────────────────────────────────


def _promote_auditor(led, node):
    """auditor (role, node) 格点亮 active（route 专员通道判据的确定性前提）。"""
    led.append(
        "promotion",
        {"agent": AUDITOR, "role": "auditor", "node": node, "exam_id": "test:promo",
         "from_status": "shadow", "to_status": "active"},
    )


def test_e2e_scan_to_voted(prod, tmp_path, monkeypatch):
    """M4 卷 D 组：L1 存活点 → route 派 auditor → finding → 粗聚类 → 面板 →
    voted + S1 存款（D32 因子）→ 预裁决单 → 报告落盘。"""
    tmp, repo, params, _n = prod
    monkeypatch.chdir(tmp)
    params.tilt.T_BASE = 1.0                              # 专员通道确定性命中
    params.tilt.T_MAX = 1.0
    adapter = ScriptedConsultAdapter(
        audit_script={"util.c": {"declined": True}},      # mem_safety 候选 → 干净
    )
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        _promote_auditor(led, "injection")                # 专员格：injection
        report = run_consult(
            led, repo_root=repo, mode="full", run_id="R-CONSULT", scan_run_id="R-SCAN",
            seed=11, adapter=adapter, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), cache_dir=tmp / "cache", report_dir=tmp / "out-r",
        )
        # 漏斗面
        assert report.candidates_total == _n
        assert report.l2_dispatched == _n
        assert report.l2_clean == 1                       # declined 候选
        assert report.findings_opened == _n - 1
        assert report.clusters_coarse >= 1
        assert report.panels_run >= 1
        assert report.findings_voted >= 1
        assert report.preverdicts >= 1
        # tasks 面：l2/vote 两前缀全 done；assignee ∈ 对应池
        rows = led.conn.execute(
            "SELECT task_id, role, state, assignee FROM tasks ORDER BY task_id"
        ).fetchall()
        for tid, role, state, assignee in rows:
            if tid.startswith("l2:"):
                assert role == "auditor" and state == "done"
                assert assignee in (AUDITOR, AUDITOR2)
            elif tid.startswith("vote:"):
                assert role == "reviewer" and state == "done"
                assert assignee in (SCOUT_A, SCOUT_B)      # 帽兼任池；报告者排除在建池侧
        # finding 状态面： voted；无 verify_results 行（预裁决 ≠ S2）
        states = dict(led.conn.execute(
            "SELECT id, state FROM findings").fetchall())
        assert states and set(states.values()) <= {"voted"}
        votes = led.conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
        assert votes == report.panels_run * 2              # 2 蜂帽池面板
        voters = {r[0] for r in led.conn.execute("SELECT DISTINCT reviewer FROM votes")}
        reporters = {r[0] for r in led.conn.execute("SELECT DISTINCT reporter FROM findings")}
        assert voters and not (voters & reporters)         # 纪律 2：报告者不入面板
        assert led.conn.execute("SELECT COUNT(*) FROM verify_results").fetchone()[0] == 0
        # S1 收口存款（R-CONSULT run）：源权重 0.05 × 角色系数 × s1_factor(0.6)；
        # 成员报告者(auditor ×1.0)=0.03 / 侦察行(×0.2)=0.006 / 评审回校(×0.5)=0.015
        s1 = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
              if e.run_id == "R-CONSULT" and e.payload.get("origin") == "prod"
              and e.payload.get("signal_tier") == "S1"]
        assert s1
        expected = {"auditor": 0.05 * 1.0 * 0.6, "scout": 0.05 * 0.2 * 0.6,
                    "reviewer": 0.05 * 0.5 * 0.6}
        for ev in s1:
            role = ev.payload["role"]
            assert role in expected, role
            assert ev.payload["weight"] == pytest.approx(expected[role], rel=1e-6)
        assert {ev.payload["role"] for ev in s1} == {"auditor", "scout", "reviewer"}
        # 预裁决单工件
        arts = led.conn.execute(
            "SELECT kind, producer FROM artifacts WHERE kind = 'pre_verdict'"
        ).fetchall()
        assert arts and report.preverdicts == len(arts)
        # 报告落盘（D34）
        blob = json.loads((tmp / "out-r" / "consult_report.json").read_text())
        assert blob["funnel"]["findings_voted"] == report.findings_voted
        assert (tmp / "out-r" / "consult_report.md").exists()
        # 报告字段真实化（2026-10-20 修复）：severity/direction/disagreement 读
        # findings 行与票面聚合（修复前恒空串/0/字符串 truthy）
        assert report.findings_detail
        for f in report.findings_detail:
            assert f["severity"] == "high"                # findings 行实值
            assert f["direction"] == 1                    # 票向符号和的符号（全 +1）
            assert f["disagreement"] is False             # disagreement_json 解析值
        # 影子面
        assert report.shadow["entry_knobs_zeroed"] is True


def test_e2e_idempotent_rerun(prod):
    """同参数重跑：账本零新事件（任务存在跳过 / 存款幂等键 / 簇幂等）。"""
    tmp, repo, params, _n = prod
    adapter = ScriptedConsultAdapter(audit_script={"util.c": {"declined": True}})
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        kw = dict(
            repo_root=repo, mode="full", run_id="R-IDEM", scan_run_id="R-SCAN",
            seed=11, adapter=adapter, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), cache_dir=tmp / "cache", report_dir=tmp / "out-i",
        )
        run_consult(led, **kw)
        n_before = len(led.read())
        dep_before = len(led.read(kinds=(LedgerKind.DEPOSIT,)))
        report2 = run_consult(led, **kw)
        assert len(led.read()) == n_before                 # 零新事件
        assert len(led.read(kinds=(LedgerKind.DEPOSIT,))) == dep_before
        assert report2.l2_dispatched == 0                  # 全部任务已存在跳过


def test_consult_panel_starved_failclosed(prod):
    """池 <2（无 reviewer 帽）→ 不开面板：finding 停 open → 收口过期（零存款）。"""
    tmp, repo, params, _n = prod
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        adapter = ScriptedConsultAdapter()
        report = run_consult(
            led, repo_root=repo, mode="full", run_id="R-STARVE", scan_run_id="R-SCAN",
            seed=11, adapter=adapter, store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), cache_dir=tmp / "cache", report_dir=tmp / "out-s",
        )
        assert report.panel_starved == report.clusters_coarse
        assert report.findings_voted == 0
        assert report.expired_open == report.findings_opened
        states = {r[0] for r in led.conn.execute("SELECT state FROM findings").fetchall()}
        assert states == {"expired"}
        s1 = [e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
              if e.payload.get("origin") == "prod" and e.payload.get("role") == "reviewer"]
        assert s1 == []


def test_consult_route_specialist_channel(prod):
    """F3 主判据：active 专员格 + tilt 命中 → specialist 通道、assignee=专员。"""
    tmp, repo, params, _n = prod
    params.tilt.T_BASE = 1.0
    params.tilt.T_MAX = 1.0
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        _promote_auditor(led, "injection")
        run_consult(
            led, repo_root=repo, mode="full", run_id="R-ROUTE", scan_run_id="R-SCAN",
            seed=11, adapter=ScriptedConsultAdapter(), store_dir=tmp / "store",
            params=params, clock=FixedClock(T0), cache_dir=tmp / "cache",
            report_dir=tmp / "out-rt",
        )
        spec_events = [
            e for e in led.read(kinds=(LedgerKind.DISPATCH,))
            if e.run_id == "R-ROUTE" and e.payload.get("role") == "auditor"
        ]
        assert spec_events
        assert all(ev.payload["assignee"] in (AUDITOR, AUDITOR2) for ev in spec_events)
        # 专员格 active 且 guess=injection 的候选 → 恒走专员通道 assignee=AUDITOR
        l2_rows = led.conn.execute(
            "SELECT shard_json, assignee FROM tasks WHERE run_id = 'R-ROUTE'"
            " AND task_id LIKE 'l2:%'"
        ).fetchall()
        injection_assignees = {
            assignee for shard, assignee in l2_rows
            if json.loads(str(shard)).get("family_hint") == "injection"
        }
        assert injection_assignees == {AUDITOR}


def test_consult_budget_degrade(prod, monkeypatch):
    """预算拧到最小 → full 行为退化为部分深诊 + open 过期 + 报告披露（§2.4/§2.7）。"""
    tmp, repo, params, _n = prod
    monkeypatch.chdir(tmp)
    params.budget.PR_CENTS = 5                            # 名义 5 cents ≈ 1 次 L2
    params.budget.FULL_CENTS = 5
    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        adapter = ScriptedConsultAdapter()
        report = run_consult(
            led, repo_root=repo, mode="full", run_id="R-BUDGET", scan_run_id="R-SCAN",
            seed=11, adapter=adapter, store_dir=tmp / "store",
            params=params, clock=FixedClock(T0), cache_dir=tmp / "cache",
            report_dir=tmp / "out-b",
        )
        assert report.l2_quota_skipped >= 1
        # 面板/预裁决预算门控（§2.7「预算尽 → 停新派单」对付费票单同样成立，
        # 2026-10-20 修复——修复前熔断后两阶段仍持续产生付费调用）
        assert report.panel_budget_skipped == report.clusters_coarse
        assert report.pv_budget_skipped == report.clusters_fine
        assert report.panels_run == 0 and report.preverdicts == 0
        assert not [c for c in adapter.calls if c[0] in ("vote", "pv")]
        blob = json.loads((tmp / "out-b" / "consult_report.json").read_text())
        assert blob["failures"]["l2_quota_skipped"] >= 1
        assert blob["failures"]["panel_budget_skipped"] == report.panel_budget_skipped
        assert blob["failures"]["pv_budget_skipped"] == report.pv_budget_skipped


def test_production_blocks_carry_family_gloss(prod):
    """FAMILY_GLOSS 生产注入（prompts.gloss 单一源，2026-10-20）：L2 深诊块的
    「L1 家族猜测」行与 reviewer 票面的「家族」行都带注解——与考试 claim 同形，
    消除 eval/production 偏移（修复前 gloss 只在考试面注入）。"""
    from qswarm.prompts import family_display

    tmp, repo, params, _n = prod
    seen: list[str] = []

    class Capturing(ScriptedConsultAdapter):
        def complete(self, *, model_ref, messages, temperature=0.0, **_kw):
            seen.append(messages[1].content)
            return super().complete(model_ref=model_ref, messages=messages,
                                    temperature=temperature, **_kw)

    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        run_consult(
            led, repo_root=repo, mode="full", run_id="R-GLOSS", scan_run_id="R-SCAN",
            seed=11, adapter=Capturing(), store_dir=tmp / "store", params=params,
            clock=FixedClock(T0), cache_dir=tmp / "cache", report_dir=tmp / "out-g",
        )
    audits = [u for u in seen if "candidate_id: " in u]
    votes = [u for u in seen if "\nfinding_id: " in u]
    assert audits and votes
    for u in audits:                                      # 猜测行：注解形或「无」
        for line in u.splitlines():
            if line.startswith("L1 家族猜测: ") and line != "L1 家族猜测: 无":
                fam = line.split(": ", 1)[1].split("（", 1)[0]
                assert line == f"L1 家族猜测: {family_display(fam)}", line
    for u in votes:                                       # 票面家族行带注解
        assert "家族: injection（注入类" in u


def test_consult_empty_auditor_pool_queued(tmp_path):
    """auditor 空池 → D10 empty_pool 语义：任务 queued 留待 + 缺席计数
    （单元面直呼编排件——verify_ready 4-active 门禁挡全 dormant 路径）。"""
    from qswarm.kernel.consult import ConsultReport, _dispatch_l2_candidate
    from qswarm.kernel.ledger import KernelLedger
    from qswarm.kernel.dispatch import open_run
    from qswarm.kernel.cells import view_cells

    with KernelLedger(tmp_path / "l.db", clock=FixedClock(T0)) as led:
        open_run(led, run_id="R-EP", mode="full", repo_url=str(tmp_path))
        empty_cells = view_cells(led, T0)
        report = ConsultReport(run_id="R-EP", mode="full")
        from qswarm.kernel.budget import BudgetPolicy, budget_for_mode
        params = load_params()
        policy = BudgetPolicy(mode="full", budget_cents=budget_for_mode("full", params),
                              cost_per_l2_cents=5, cost_per_s2_cents=10, l2_cap=45)
        cand = {"candidate_id": "cd-x", "path": "src/api.py", "span": (4, 4),
                "guess_family": None}
        _dispatch_l2_candidate(
            led, root=tmp_path, cand=cand, seq=1, run_id="R-EP", mode="full",
            auditors=[], cells=empty_cells, load={}, rng=random.Random(1),
            adapter=ScriptedConsultAdapter(), params=params, policy=policy,
            report=report, repomap_block=None,
        )
        assert report.l2_route_empty_pool == 1
        assert report.l2_dispatched == 0
        row = led.conn.execute(
            "SELECT state, assignee FROM tasks WHERE task_id LIKE 'l2:%'"
        ).fetchone()
        assert row is not None and row[0] == "queued" and row[1] is None


def test_consult_schema_fail_retry_once(prod):
    """schema 畸形 → 改派重试 1 次后成功收口（§16.7-8）；零永久失败零凑数。"""
    tmp, repo, params, _n = prod

    class FlakyAudit(ScriptedConsultAdapter):
        def __init__(self):
            super().__init__()
            self.bad_paths: set[str] = set()      # 每候选路径首调坏 JSON，其后恒好

        def complete(self, *, model_ref, messages, temperature=0.0, **_kw):
            if "审查蜂" in messages[0].content:
                user = messages[1].content
                path = Path(user.split("path: ", 1)[1].split("\n", 1)[0]).name
                if path not in self.bad_paths:
                    self.bad_paths.add(path)
                    return "not-json-at-all"
            return super().complete(model_ref=model_ref, messages=messages,
                                    temperature=temperature, **_kw)

    with KernelLedger(tmp / "prod.db", clock=FixedClock(T0)) as led:
        _add_hats(led)
        report = run_consult(
            led, repo_root=repo, mode="full", run_id="R-FLAKY", scan_run_id="R-SCAN",
            seed=11, adapter=FlakyAudit(), store_dir=tmp / "store",
            params=params, clock=FixedClock(T0), cache_dir=tmp / "cache",
            report_dir=tmp / "out-f",
        )
        assert report.l2_schema_fail == 0
        assert report.findings_opened + report.l2_clean == report.candidates_total
        states = {r[0] for r in led.conn.execute(
            "SELECT state FROM tasks WHERE task_id LIKE 'l2:%'").fetchall()}
        assert states <= {"done", "failed"}


def test_s1_factor_scoped_to_s1_rows():
    """D32 作用域：s1_factor 只乘 S1 行源权重；S2/S3 行不受影响（单元面）。"""
    import sqlite3

    from qswarm.kernel.cluster import _expansion_rows, VerdictContext as VC, MemberFacts as MF

    raw = sqlite3.connect(":memory:")
    raw.execute("CREATE TABLE findings (id TEXT PRIMARY KEY, family TEXT, type TEXT)")
    raw.execute("INSERT INTO findings VALUES ('F-1', 'injection', 'sqli')")

    class _Led:                                  # _expansion_rows 只读 conn 面
        conn = raw

    conn = _Led()
    ctx = VC(members=(MF(finding_id="F-1", reporter="auditor-a", reporter_role="auditor"),),
             s1_factor=0.6)
    from qswarm.contracts import load_params as _lp
    params = _lp()
    rows_s1 = _expansion_rows(conn, "C-x", tier=SignalTier.S1, direction=1,
                              src_seq=1, ctx=ctx, params=params)
    rows_s2 = _expansion_rows(conn, "C-x", tier=SignalTier.S2, direction=1,
                              src_seq=2, ctx=ctx, params=params)
    assert rows_s1[0].weight == pytest.approx(0.05 * 0.6)
    assert rows_s2[0].weight == pytest.approx(0.5)     # S2 不乘因子（S2_SOURCE_W=0.5）
