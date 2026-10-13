"""exams M3 补播面测试（SPEC-7 §4；M3 卷 A 组 §1.6 + E 组链路判据）。

钉死：seed_entry roles 参数化（reviewer/verifier 全链：出卷含预置块、判分、
存款、QC）、CLI --roles（缺省零扰动 + 越词表拒绝）、reviewer/verifier 考试
DepositPayload 形态（origin=exam 与 auditor 同形）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import AgentStatus, DepositOrigin, LedgerKind, Role
from qswarm.kernel import FixedClock, KernelLedger, agg_cells
from qswarm.exams.judge import judge_answer
from qswarm.exams.roster import expand_candidates
from qswarm.exams.runner import run_entry_cell
from qswarm.exams.seeding import SEEDING_ROLES, seed_entry
from qswarm.supply.config import load_models

import m3_harness
import m2_harness
from m2_harness import FakeAdapter, build_models_yaml, make_bank, make_resolver


@pytest.fixture()
def env(tmp_path):
    bank = make_bank(tmp_path / "seeds")
    models = load_models(build_models_yaml(tmp_path / "models.yaml"))
    resolve = make_resolver(tmp_path / "seeds")
    return tmp_path, bank, models, resolve


def _exam_deposits(led):
    return [
        e for e in led.read(kinds=(LedgerKind.DEPOSIT,))
        if e.payload.get("origin") == DepositOrigin.EXAM.value
    ]


def test_seeding_roles_flag_default_unchanged(env):
    """缺省 roles = (scout, auditor)（S4 既有行为零扰动；模块常量钉住）。"""
    assert SEEDING_ROLES == ("scout", "auditor")


def test_seed_entry_reviewer_verifier_e2e(env):
    """M3 卷 E 组链路（FakeAdapter 离线）：reviewer/verifier 补播全链——
    2 蜂 × 5 家族 = 10 格存款、QC 达线转 active、DepositPayload 与 auditor 同形。"""
    tmp, bank, models, resolve = env
    with KernelLedger(tmp / "a.db", clock=FixedClock(m2_harness.T0)) as led:
        report = seed_entry(
            led, bank, models, FakeAdapter(tmp / "seeds"),
            seed=7, roles=("reviewer", "verifier"), resolve_text=resolve,
        )
        assert report["roles"] == ["reviewer", "verifier"]
        agents = expand_candidates(models, roles=("reviewer", "verifier"))
        assert [a.role for a in agents] == ["reviewer", "verifier"]
        assert len(agents) == 2
        assert report["totals"]["deposits"] == 10          # 2 蜂 × 5 家族
        assert report["qc_alerts"] == []
        for agent in report["agents"]:
            assert agent["qc"]["accuracy"] == pytest.approx(1.0)   # 满分卷
            assert agent["qc"]["status"] == "active"
        for dep in _exam_deposits(led):
            p = dep.payload
            assert p["role"] in ("reviewer", "verifier")
            assert p["signal_tier"] == "S1"
            assert 0.2 <= p["weight"] <= 0.5
            assert p["origin"] == "exam"
        # agents 表：两蜂 active
        rows = led.conn.execute(
            "SELECT agent_id, status FROM agents ORDER BY agent_id"
        ).fetchall()
        assert len(rows) == 2 and all(s == AgentStatus.ACTIVE.value for _a, s in rows)


def test_reviewer_verifier_exam_blocks_and_contract(env):
    """纸面考卷构造（SPEC-7 §4.1）：reviewer 卷含送审 finding 块、verifier 卷含
    预置簇块；goal 角色分派；契约四件套同形。"""
    tmp, bank, models, resolve = env
    from qswarm.exams.store import upsert_exam_items
    from qswarm.prompts import render_exam

    class _Capturing(FakeAdapter):
        def __init__(self, root):
            super().__init__(root)
            self.seen = []

        def complete(self, *, model_ref, messages, temperature, timeout_s=120.0):
            self.seen.append((model_ref, list(messages)))
            return super().complete(model_ref=model_ref, messages=messages,
                                    temperature=temperature, timeout_s=timeout_s)

    for role in ("reviewer", "verifier"):
        cap = _Capturing(tmp / "seeds")
        with KernelLedger(tmp / f"{role}.db", clock=FixedClock(m2_harness.T0)) as led:
            upsert_exam_items(led, bank)               # exam_results.item_id 外键前件
            run_entry_cell(
                led, bank, agent_id=f"{role}:x/y@cv1", role=role,
                model_ref=f"{role}:x/y@cv1", family="injection",
                adapter=cap, seed=7, resolve_text=resolve,
            )
        (model_ref, messages) = cap.seen[0]
        contract_goal = messages[1].content.splitlines()[0]
        assert contract_goal.startswith("goal: 入职考：")
        user = "\n".join(m.content for m in messages)
        if role == "reviewer":
            assert "送审 finding" in user and "声称家族: injection" in user
        else:
            assert "簇构成: C-EXAM" in user and "三分类复现结论" in user
        assert "artifact://exam/" in user                  # 预置块引用在位


def test_cli_roles_flag(tmp_path, env, monkeypatch):
    """CLI --roles：合法清单生效、越词表 SystemExit；离线纪律——供给面替换为
    FakeAdapter（CLI 构造 UrllibAdapter 会触网，测试层禁真网）。"""
    from qswarm.exams import seeding as seeding_mod

    tmp, bank, models, resolve = env
    monkeypatch.setattr(
        seeding_mod, "UrllibAdapter",
        lambda models: FakeAdapter(tmp / "seeds"),
    )
    db = tmp / "cli.db"
    rc = seeding_mod.main(
        [
            "--bank", str(tmp / "seeds" / "bank.yaml"),
            "--models", str(tmp / "models.yaml"),
            "--db", str(db),
            "--out", str(tmp / "out"),
            "--roles", "reviewer,verifier",
        ]
    )
    assert rc == 0
    with KernelLedger(db, clock=FixedClock(m2_harness.T0)) as led:
        roles = {r for (r,) in led.conn.execute("SELECT DISTINCT role FROM exam_results")}
        assert roles == {"reviewer", "verifier"}
    with pytest.raises(SystemExit, match="--roles 越词表"):
        seeding_mod.main(
            [
                "--bank", str(tmp / "seeds" / "bank.yaml"),
                "--models", str(tmp / "models.yaml"),
                "--db", str(tmp / "bad.db"),
                "--roles", "scout,queen",
            ]
        )


def test_seed_entry_extra_hats(env):
    """D31 帽兼任（SPEC-8 §7）：已在编 active 蜂加戴 reviewer 帽并纳入考试——
    帽注册走 attach_hat 唯一写口；考试格按 ROLE_TIER 档位展开；QC 失败不降编
    （agents.status 属蜂本体）。"""
    tmp_path, bank, models, resolve = env
    db = tmp_path / "hats.db"
    # 第一轮：常规 scout/auditor 播种（产出在编蜂）
    with KernelLedger(db, clock=FixedClock(m3_harness.T0)) as led:
        seed_entry(led, bank, models, FakeAdapter(tmp_path / "seeds"),
                   seed=7, resolve_text=resolve)
        agents_before = dict(led.conn.execute(
            "SELECT agent_id, status FROM agents").fetchall())
        auditor_id = next(a for a in agents_before if a.startswith("strong:"))
        # 第二轮：auditor 加戴 reviewer 帽补播
        report = seed_entry(
            led, bank, models, FakeAdapter(tmp_path / "seeds"),
            seed=7, roles=("reviewer",), extra_hats=((auditor_id, "reviewer"),),
            resolve_text=resolve,
        )
        assert (auditor_id, "reviewer") in {
            (r[0], r[1]) for r in led.conn.execute(
                "SELECT agent_id, role FROM hats").fetchall()
        }
        assert auditor_id in {a["agent_id"] for a in report["agents"]}
        # QC 记录在案且不降编：status 保持第一轮值（FakeAdapter 全对 → active）
        assert led.conn.execute(
            "SELECT status FROM agents WHERE agent_id = ?", (auditor_id,)
        ).fetchone()[0] == agents_before[auditor_id]
        # reviewer 帽考试存款落账（第二轮新增的 origin=exam 存款）
        mine = [e for e in _exam_deposits(led)
                if e.payload["agent"] == auditor_id
                and e.payload.get("role") == "reviewer"]
        assert mine
