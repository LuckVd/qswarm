"""exams.seeding 端到端测试（SPEC-6 §4.2/§4.4/§4.5；SPEC-5 M2 卷 §1.6 最低用例集，
离线纪律 D27：全部经 FakeAdapter 脚本化响应）。

钉死：存款形态 M0 逐位同形（S1/±1/weight∈[0.2,0.5]/origin=exam/exam_id/
taxonomy_path=[family]）、事件序确定性（注册序 × taxonomy 序 + 重跑同流）、
重跑零新事件（读侧幂等）、QC 入编线（min_pass 流转 + 告警 + 存款照写）、
基建失败整格无效（>2 零存款零行）/ 部分作答重跑收敛、schema 两连败判错计数、
agg_cells 考试份额折叠（T_HALF_DAYS_EXAM=15 路径）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.contracts import AgentStatus, DepositOrigin, LedgerKind, SignalTier
from qswarm.kernel import FixedClock, KernelLedger, agg_cells
from qswarm.supply import AdapterError, load_models
from qswarm.exams.roster import expand_candidates
from qswarm.exams.seeding import seed_entry
from qswarm.supply.config import load_models as _lm

from m2_harness import FAMILIES, FakeAdapter, T0, build_models_yaml, make_bank, make_resolver, scout_answer


@pytest.fixture()
def env(tmp_path):
    bank = make_bank(tmp_path / "seeds")
    models = _lm(build_models_yaml(tmp_path / "models.yaml"))
    resolve = make_resolver(tmp_path / "seeds")
    return tmp_path, bank, models, resolve


def _deposits(led) -> list:
    return [e for e in led.read(kinds=(LedgerKind.DEPOSIT,)) if e.payload.get("origin") == "exam"]


def test_seed_entry_deposit_shape_m0_parity(env):
    """每格恰一笔 ExamDepositPayload：S1 / direction ±1 / weight∈[0.2,0.5] /
    origin=exam / exam_id=f'entry:{agent}:{family}' / taxonomy_path=[family]
    （M0 逐位同形，§4.2）。"""
    tmp, bank, models, resolve = env
    with KernelLedger(tmp / "a.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"),
                            seed=7, resolve_text=resolve)
        deps = _deposits(led)
        agents = expand_candidates(models, roles=("scout", "auditor"))
        assert len(deps) == len(agents) * len(FAMILIES) == report["totals"]["deposits"]
        for ev in deps:
            p = ev.payload
            assert p["signal_tier"] == SignalTier.S1.value
            assert p["direction"] in (1, -1)
            assert 0.2 <= p["weight"] <= 0.5
            assert p["origin"] == DepositOrigin.EXAM.value
            assert p["taxonomy_path"] == [p["taxonomy_path"][0]]
            assert p["finding"] is None and p["cluster"] is None
            assert p["role"] in ("scout", "auditor")           # S4：v0 只播两角色
            # exam_id 形态（载荷经基类校验后不入账本 payload——M0 既定形态；
            # exam_id 语义锚定于格键，见 runner.deposit_exists 注）
            cell_key = (p["agent"], p["role"], p["taxonomy_path"][0])
            assert len({cell_key for cell_key in
                        {(d["agent"], d["role"], d["taxonomy_path"][0]) for d in (e.payload for e in deps)}}) == len(deps)


def test_seed_entry_event_order_deterministic(env):
    """agents 注册序 × taxonomy.yaml 序；两本独立账本（同缓存重放）事件流逐位一致。"""
    tmp, bank, models, resolve = env
    cache = tmp / "cache"
    streams = []
    for name in ("a", "b"):
        adapter = FakeAdapter(tmp / "seeds")
        with KernelLedger(tmp / f"{name}.db", clock=FixedClock(T0)) as led:
            seed_entry(led, bank, models, adapter, seed=7, cache_dir=cache,
                       resolve_text=resolve)
            streams.append(
                json.dumps(
                    [[e.seq, e.ts, str(e.kind), e.run_id, e.payload] for e in led.read()],
                    ensure_ascii=False, sort_keys=True,
                )
            )
    assert streams[0] == streams[1]
    # 事件序 = 注册序 × taxonomy 序（逐对相邻检查）
    events = json.loads(streams[0])
    dep_pairs = [
        (e[4]["agent"], e[4]["taxonomy_path"][0])
        for e in events if e[2] == "deposit" and e[4].get("origin") == "exam"
    ]
    expected_pairs = [
        (c.agent_id, fam)
        for c in expand_candidates(models, roles=("scout", "auditor"))
        for fam in ("injection", "crypto", "authz", "config_dep", "mem_safety")
    ]
    assert dep_pairs == expected_pairs


def test_seed_entry_rerun_zero_new_events(env):
    """已完成播种的账本重跑（同缓存）→ 零新存款零新 exam_results 行（§4.2/B2）。"""
    tmp, bank, models, resolve = env
    cache = tmp / "cache"
    with KernelLedger(tmp / "a.db", clock=FixedClock(T0)) as led:
        seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                   cache_dir=cache, resolve_text=resolve)
        seq0 = led.latest_seq()
        rows0 = led.conn.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0]
        report2 = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                             cache_dir=cache, resolve_text=resolve)
        assert led.latest_seq() == seq0
        assert led.conn.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0] == rows0
        assert report2["totals"]["deposits"] == 0
        assert report2["totals"]["exam_result_rows"] == rows0


def test_seed_entry_qc_gate_min_pass(env):
    """总正确率 < entry_exams.min_pass → status 留 provisioning + 告警记录；≥ →
    active；存款照写（S11：质检线是入编闸门不是播种开关）。"""
    tmp, bank, models, resolve = env
    # scout2（cheap:openai/gpt-4o-mini）全错：real 判错 + fp 误报
    def script(model_ref, item_id, payload):
        if model_ref.startswith("cheap:openai/"):
            if payload["expected"] == "real":
                out = scout_answer(payload, suspicious=False)      # 漏报
            else:
                out = scout_answer(payload, suspicious=True, family="crypto")  # 误报
            return json.dumps({"answers": [{"item_id": item_id, "declined": out is None, "output": out}]},
                              ensure_ascii=False)
        return None

    with KernelLedger(tmp / "qc.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds", script),
                            seed=7, cache_dir=tmp / "cache", resolve_text=resolve)
        by_agent = {a["agent_id"]: a for a in report["agents"]}
        weak = by_agent["cheap:openai/gpt-4o-mini@cv1"]
        strong = by_agent["cheap:deepseek/deepseek-chat@cv1"]
        assert weak["qc"]["passed"] is False and weak["qc"]["status"] == "provisioning"
        assert weak["agent_id"] in report["qc_alerts"]
        assert strong["qc"]["passed"] is True and strong["qc"]["status"] == "active"
        # 存款照写（弱者负向存款在账）
        assert all(c["deposit_written"] for c in weak["cells"])
        weak_deps = [e for e in _deposits(led) if e.payload["agent"] == weak["agent_id"]]
        assert len(weak_deps) == len(FAMILIES) and all(e.payload["direction"] == -1 for e in weak_deps)
        # agents 表流转一致
        rows = dict(led.conn.execute("SELECT agent_id, status FROM agents").fetchall())
        assert rows[weak["agent_id"]] == AgentStatus.PROVISIONING.value
        assert rows[strong["agent_id"]] == AgentStatus.ACTIVE.value


def test_seed_entry_infra_invalid_cell(env):
    """同格基建失败 >2 题 → 零存款零行 + 报告 infra_invalid；≤2 → 部分作答，
    重跑（基建恢复 + 同缓存）收敛补齐（§3.4/S7）。"""
    tmp, bank, models, resolve = env
    from qswarm.contracts import ExamPurpose
    from qswarm.exams.paper import draw_paper

    agent = "cheap:deepseek/deepseek-chat@cv1"
    # 按确定性出卷钉前 3 题为基建失败（重试两次均失败 → infra_fail=3 > 2）
    paper = draw_paper(bank, agent_id=agent, role="scout", family="injection",
                       purpose=ExamPurpose.ENTRY, bank_version=1, seed=7)
    fail_ids = {it.item_id for it in paper[:3]}

    def script(model_ref, item_id, payload):
        if model_ref.startswith("cheap:deepseek/") and item_id in fail_ids:
            raise AdapterError("infra down (test)")
        return None

    with KernelLedger(tmp / "infra.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds", script),
                            seed=7, cache_dir=tmp / "cache", resolve_text=resolve)
        agent = "cheap:deepseek/deepseek-chat@cv1"
        cell = next(c for a in report["agents"] if a["agent_id"] == agent
                    for c in a["cells"] if c["family"] == "injection")
        assert cell["status"] == "infra_invalid"
        assert cell["deposit_written"] is False
        rows = led.conn.execute(
            "SELECT COUNT(*) FROM exam_results WHERE agent_id = ? AND node = 'injection'",
            (agent,),
        ).fetchone()[0]
        assert rows == 0                                   # 整格回滚：零行
        deps = [e for e in _deposits(led)
                if e.payload["agent"] == agent and e.payload["taxonomy_path"] == ["injection"]]
        assert deps == []                                  # 零存款
        assert report["totals"]["invalid_cells"] == 1
        # 重跑（同缓存 + 基建恢复）→ 该格收敛：12 行 + 1 存款
        report2 = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"),
                             seed=7, cache_dir=tmp / "cache", resolve_text=resolve)
        cell2 = next(c for a in report2["agents"] if a["agent_id"] == agent
                     for c in a["cells"] if c["family"] == "injection")
        assert cell2["status"] == "ok" and cell2["deposit_written"] is True
        rows2 = led.conn.execute(
            "SELECT COUNT(*) FROM exam_results WHERE agent_id = ? AND node = 'injection'",
            (agent,),
        ).fetchone()[0]
        assert rows2 == 12


def test_seed_entry_schema_fail_counts_wrong(env):
    """schema 两连败 → correct=0 落行 + schema_fail 计数进报告（S7：契约失能
    是真实能力缺陷信号）。"""
    tmp, bank, models, resolve = env

    def script(model_ref, item_id, payload):
        if model_ref.startswith("cheap:deepseek/") and payload["expected"] == "real" \
                and payload["taxonomy_path"][0] == "crypto":
            return "这不是 JSON"                            # 两连败同响应
        return None

    with KernelLedger(tmp / "sf.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds", script),
                            seed=7, cache_dir=tmp / "cache", resolve_text=resolve)
        agent = "cheap:deepseek/deepseek-chat@cv1"
        cell = next(c for a in report["agents"] if a["agent_id"] == agent
                    for c in a["cells"] if c["family"] == "crypto")
        assert cell["schema_fail"] > 0
        bad_rows = led.conn.execute(
            "SELECT correct FROM exam_results WHERE agent_id = ? AND node = 'crypto'"
            " AND item_id IN (SELECT item_id FROM exam_items WHERE expected='real')"
            " AND correct = 0",
            (agent,),
        ).fetchall()
        assert len(bad_rows) >= cell["schema_fail"]
        assert report["totals"]["schema_fail"] == sum(c["schema_fail"] for c in
                                                      [c for a in report["agents"] for c in a["cells"]])


def test_agg_cells_folds_exam_share(env):
    """播种后 agg_cells：格值含 origin=exam 份额（age=0 时 pos/neg = weight）；
    T_HALF_DAYS_EXAM=15 衰减路径生效（晚时点重折叠按 exam 半衰折算）。"""
    tmp, bank, models, resolve = env
    with KernelLedger(tmp / "agg.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"),
                            seed=7, resolve_text=resolve)
        view = agg_cells(led, T0)
        agents = expand_candidates(models, roles=("scout", "auditor"))
        assert len(view.cells) == len(agents) * len(FAMILIES)
        for ev in _deposits(led):
            p = ev.payload
            cell = view.get(p["agent"], p["role"], p["taxonomy_path"][0])
            assert cell is not None
            assert cell.n_eff_s2s3 == 0.0                     # S1 不进晋升硬门槛
            # age=0：无衰减，pos/neg = weight（方向分侧）
            if p["direction"] == 1:
                assert cell.pos_w == pytest.approx(p["weight"])
            else:
                assert cell.neg_w == pytest.approx(p["weight"])
            assert cell.n_eff == pytest.approx(2 * (cell.pos_w + cell.neg_w))
        # T_HALF_DAYS_EXAM=15：15 天后权重减半（考试半衰路径，非 prod 30 天）
        view15 = agg_cells(led, "2030-01-16T00:00:00Z")
        ev0 = _deposits(led)[0]
        p0 = ev0.payload
        cell15 = view15.get(p0["agent"], p0["role"], p0["taxonomy_path"][0])
        w15 = (cell15.pos_w if p0["direction"] == 1 else cell15.neg_w)
        assert w15 == pytest.approx(p0["weight"] * 0.5, rel=1e-9)
        # 无 promotion 事件（S8：只供血不孵化）
        assert led.read(kinds=(LedgerKind.PROMOTION,)) == []


def test_seed_entry_degraded_flag_on_mass_infra(env):
    """整场播种无效格占比 > 10% → 报告 degraded=true（§4.2；门禁不通过形态）。"""
    tmp, bank, models, resolve = env

    def script(model_ref, item_id, payload):
        raise AdapterError("mass outage (test)")

    with KernelLedger(tmp / "deg.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds", script),
                            seed=7, resolve_text=resolve)
        assert report["degraded"] is True
        assert report["totals"]["invalid_cells"] == report["totals"]["cells"]
        assert report["totals"]["deposits"] == 0
