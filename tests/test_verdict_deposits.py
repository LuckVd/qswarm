"""verdict+deposits 验收测试（SPEC-4 §1.3/§1.4、设计 §6.2/§6.5/§16.7-1）。

对照子任务验收标准：
  - ac_muz07uljp59kdwii：固定种子下 verdict/deposit 流逐位可复现；相关噪声强度
    （panel 共享噪声诱导的潜变量相关 + 票和过散）与边际概率（票向 0.55+0.25q、
    判对概率）大样本统计断言（容差 ≥2.8σ，固定种子下数值确定无抖动）；
  - ac_muz07uljxxhgc8ab：§6.5 归属矩阵逐行断言（角色×触发×方向→权重与符号）；
    verify_error 产生零存款；
  - ac_muz07uljygof9znu：S3 延迟截断 [1d,30d] 与触达率 40%/80% 生效（统计断言）；
    S2 触发率随 full 档 30% 生效。

统计基准来自 500 仿真日大样本（~23k findings，种子 2024/7），容差按实测
σ ≥ 3σ 留裕量；矩阵行为断言为纯确定性单位场景。
"""

import json
import math
import statistics
from pathlib import Path

import pytest

from qswarm.contracts import (
    DepositsParams,
    DepositOrigin,
    Role,
    RunMode,
    SignalTier,
    VerifyOutcome,
    load_params,
    load_taxonomy,
)
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.simulator import (
    AUDITOR_X,
    AgentPool,
    FindingContext,
    S2_SOURCE_WEIGHT,
    S2Record,
    S3_SOURCE_WEIGHT,
    S3Record,
    TaskGenerator,
    VerdictParams,
    VerdictResult,
    VoteRecord,
    WorldTask,
    append_deposits,
    attribute_deposits,
    deposit_schedule,
    latent_correlation,
    run_findings_flow,
    theoretical_lag_mean,
)

PARAMS = load_params()
TAX = load_taxonomy()
POOL = AgentPool.from_params(PARAMS, TAX, seed=2024)
N_DAYS = 500

# 共享大样本流（构建一次，~1s；种子显式注入，数值确定）
TASKS = TaskGenerator(PARAMS, TAX, seed=7).generate(N_DAYS)
FLOW = run_findings_flow(TASKS, POOL, seed=2024, params=PARAMS)
FLOW_CTRL = run_findings_flow(
    TASKS, POOL, seed=2024, params=PARAMS,
    vparams=VerdictParams(z_own_w=1.0, z_panel_w=0.0),      # 对照：无共享噪声
)
FLOW_VE5 = run_findings_flow(
    TASKS, POOL, seed=2024, params=PARAMS,
    vparams=VerdictParams(verify_error=0.5),                # 饥饿旋钮扫描点
)


def _flow_repr(flow) -> list[str]:
    return [repr((ctx, res)) for ctx, res in flow]


# ── ac_muz07uljp59kdwii：逐位可复现 ─────────────────────────────

def test_verdict_flow_bit_identical_same_seed():
    tasks = TaskGenerator(PARAMS, TAX, seed=7).generate(60)
    f1 = run_findings_flow(tasks, POOL, seed=2024, params=PARAMS)
    f2 = run_findings_flow(tasks, POOL, seed=2024, params=PARAMS)
    assert len(f1) == len(f2) > 0
    assert _flow_repr(f1) == _flow_repr(f2)                   # repr 往返：float 逐位
    # 流长度 = 产 finding 任务数（cache_hit 跳过）
    assert len(f1) == sum(1 for t in tasks if t.produces_finding)


def test_verdict_flow_differs_across_seeds():
    tasks = TaskGenerator(PARAMS, TAX, seed=7).generate(60)
    f1 = run_findings_flow(tasks, POOL, seed=2024, params=PARAMS)
    f2 = run_findings_flow(tasks, POOL, seed=4321, params=PARAMS)
    assert _flow_repr(f1) != _flow_repr(f2)


def test_deposit_stream_bit_identical_through_ledger(tmp_path):
    """存款经 kernel.ledger.append 落账：同种子两次运行的 payload_json 逐位一致。"""
    tasks = TaskGenerator(PARAMS, TAX, seed=7).generate(60)

    def run_to_jsonl(tag: str) -> list[str]:
        flow = run_findings_flow(tasks, POOL, seed=2024, params=PARAMS)
        with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
            for ctx, res in flow:
                append_deposits(led, attribute_deposits(ctx, res, PARAMS), run_id=tag)
            events = led.read(kinds=["deposit"])
            assert len(events) > 0
            assert all(e.kind.value == "deposit" for e in events)
            seqs = [e.seq for e in events]
            assert seqs == sorted(seqs) and all(b - a == 1 for a, b in zip(seqs, seqs[1:]))
            return [json.dumps(e.payload, sort_keys=True) for e in events]

    js1 = run_to_jsonl("R-SIM-A")
    js2 = run_to_jsonl("R-SIM-B")
    assert js1 == js2                                          # 逐位一致（含 float 字面量）
    # 换种子必须不同
    flow3 = run_findings_flow(tasks, POOL, seed=999, params=PARAMS)
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        for ctx, res in flow3:
            append_deposits(led, attribute_deposits(ctx, res, PARAMS))
        js3 = [json.dumps(e.payload, sort_keys=True) for e in led.read(kinds=["deposit"])]
    assert js3 != js1


def test_verdict_flow_cross_process_bit_identical(tmp_path):
    """跨进程逐位可复现（最强口径）：不同 PYTHONHASHSEED 的两个子进程输出逐字节一致。

    随机性全部来自 blake2b 派生子流（rng.derive_seed，内容定址），不依赖进程内
    哈希随机化与解释器实例——哈希种子不同仍须逐位一致（SPEC-4 §6）。
    """
    import os
    import subprocess
    import sys

    helper = Path(__file__).resolve().parent / "_dump_verdict_flow.py"
    root = Path(__file__).resolve().parent.parent

    def run_proc(tag: str, seed: int, hashseed: str) -> Path:
        dst = tmp_path / f"flow_{tag}.jsonl"
        env = dict(os.environ, PYTHONHASHSEED=hashseed, PYTHONPATH=str(root))
        proc = subprocess.run(
            [sys.executable, str(helper), "--days", "90", "--seed", str(seed), "--out", str(dst)],
            capture_output=True, text=True, env=env, cwd=str(root), timeout=300,
        )
        assert proc.returncode == 0, proc.stderr
        return dst

    a = run_proc("a", 2024, hashseed="0")
    b = run_proc("b", 2024, hashseed="58123")    # 哈希种子不同 ⇒ dict 迭代序不同
    da, db = a.read_bytes(), b.read_bytes()
    assert len(da) > 100_000
    assert da == db                              # 跨进程逐字节一致
    # 换随机种子必须不同（阳性对照：证明 diff 手段本身有效）
    c = run_proc("c", 2025, hashseed="0")
    assert c.read_bytes() != da


# ── ac_muz07uljp59kdwii：边际概率校准（大样本 z 断言）──────────

def test_vote_marginal_probability_055_plus_025q():
    """每票 P(与真同向) = 0.55 + 0.25·reviewer_q：总体与逐评审蜂 z 分数 ∈ ±2.8。"""
    tot_a = tot_p = tot_var = 0.0
    per: dict[str, list[float]] = {}
    for _, res in FLOW:
        for v in res.votes:
            a = 1.0 if v.direction == res.truth_sign else 0.0
            tot_a += a
            tot_p += v.p_agree
            tot_var += v.p_agree * (1.0 - v.p_agree)
            s = per.setdefault(v.reviewer_id, [0.0, 0.0, 0.0])
            s[0] += a
            s[1] += v.p_agree
            s[2] += v.p_agree * (1.0 - v.p_agree)
    n_votes = sum(len(r.votes) for _, r in FLOW)
    assert n_votes > 20000
    z = (tot_a - tot_p) / math.sqrt(tot_var)
    assert abs(z) <= 2.8, f"总体票向校准失败：z={z:.3f}"
    for rid, (a, p, var) in sorted(per.items()):
        z_r = (a - p) / math.sqrt(var)
        assert abs(z_r) <= 2.8, f"{rid} 票向校准失败：z={z_r:.3f}"
    # 设计边际的量级 sanity：均值 q≈0.5 → 平均票向概率 ≈ 0.675
    assert 0.62 < tot_p / n_votes < 0.73


def test_reporter_correct_probability_calibration():
    """P(判对) = q·(1−0.3d)（含运气型早期偏差）：经验频率 z 分数 ∈ ±2.8。"""
    hits = sum(1 for _, r in FLOW if r.truth_real)
    exp = sum(r.p_correct for _, r in FLOW)
    var = sum(r.p_correct * (1.0 - r.p_correct) for _, r in FLOW)
    z = (hits - exp) / math.sqrt(var)
    assert abs(z) <= 2.8, f"判对概率校准失败：z={z:.3f}"


def test_vote_correlated_noise_strength():
    """相关噪声强度：panel 内潜变量相关 ≈ 4/13，票和过散显著高于独立对照。"""
    rho_theo = latent_correlation()                            # 0.4²/0.52 = 4/13
    pairs = []
    us: list[float] = []
    for _, res in FLOW:
        vs = res.votes
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                pairs.append((vs[i].z_agent, vs[j].z_agent))
        if vs:
            xs = [1.0 if v.direction == res.truth_sign else 0.0 for v in vs]
            ps = [v.p_agree for v in vs]
            sv = sum(p * (1.0 - p) for p in ps)
            us.append((sum(xs) - sum(ps)) / math.sqrt(sv))
    n = len(pairs)
    assert n > 30000
    mx = statistics.fmean(a for a, _ in pairs)
    my = statistics.fmean(b for _, b in pairs)
    cov = sum((a - mx) * (b - my) for a, b in pairs) / n
    vx = statistics.pvariance([a for a, _ in pairs])
    vy = statistics.pvariance([b for _, b in pairs])
    rho_emp = cov / math.sqrt(vx * vy)
    assert abs(rho_emp - rho_theo) <= 0.02, f"潜变量相关偏移：emp={rho_emp:.4f} theo={rho_theo:.4f}"
    # 共享噪声 → 票和过散（标准化票和方差 > 1）；对照（z_panel_w=0）回到 ≈1
    var_u = statistics.pvariance(us)
    assert var_u >= 1.25, f"共享噪声未产生过散：Var(u)={var_u:.4f}"
    us_c = []
    for _, res in FLOW_CTRL:
        if res.votes:
            xs = [1.0 if v.direction == res.truth_sign else 0.0 for v in res.votes]
            ps = [v.p_agree for v in res.votes]
            sv = sum(p * (1.0 - p) for p in ps)
            us_c.append((sum(xs) - sum(ps)) / math.sqrt(sv))
    var_u_c = statistics.pvariance(us_c)
    assert 0.90 <= var_u_c <= 1.10, f"对照（独立票）偏离 1：Var(u)={var_u_c:.4f}"
    assert var_u > var_u_c + 0.15


# ── ac_muz07uljxxhgc8ab：§6.5 归属矩阵逐行断言 ─────────────────

def _mk_task(mode=RunMode.FULL, day=3, t=42) -> WorldTask:
    return WorldTask(
        task_id=f"T-{t:06d}", t=t, day=day, family="injection", type="sqli",
        difficulty=0.4, mode=mode, cache_hit=False,
    )


def _mk_ctx(task=None, **over) -> FindingContext:
    defaults = dict(
        finding_id="F-SIM-000042", cluster_id="C-20300101-000042",
        reporter_id="auditor-01", reporter_role=Role.AUDITOR,
        panel=("reviewer-01", "reviewer-02", "reviewer-03"),
        cluster_scouts=("scout-01", "scout-02"),
        verifier_id="verifier-01",
    )
    defaults.update(over)
    return FindingContext(task=task or _mk_task(), **defaults)


def _mk_votes() -> tuple[VoteRecord, ...]:
    """票向 +/−/+（共识 +1）；z 值仅占位（矩阵断言不消费）。"""
    return (
        VoteRecord("reviewer-01", 1, True, 0.675, 0.30, -0.20, 0.10),
        VoteRecord("reviewer-02", -1, False, 0.675, -1.10, -0.20, -0.74),
        VoteRecord("reviewer-03", 1, True, 0.675, 0.50, -0.20, 0.22),
    )


def _mk_result(ctx, **over) -> VerdictResult:
    defaults = dict(
        truth_real=True, p_correct=0.6, votes=_mk_votes(),
        s1_direction=1, s1_weight=0.05, s2_triggered=False, s2=None, s3=None,
    )
    defaults.update(over)
    return VerdictResult(ctx=ctx, **defaults)


def _keys(rows):
    return [
        (p.agent, p.role.value, p.signal_tier.value, p.direction, pytest.approx(p.weight, abs=1e-12))
        for p in rows
    ]


def _common_fields(rows, ctx):
    for p in rows:
        assert p.finding == ctx.finding_id and p.cluster == ctx.cluster_id
        assert p.taxonomy_path == ["injection", "sqli"]
        assert p.origin is DepositOrigin.PROD
        assert p.direction in (-1, 1)


def test_matrix_s1_rows():
    """S1 行：审查蜂×1.0（verdict 向）/ 侦察蜂×0.2（verdict 向）/ 评审蜂×0.5（同向 + 反向 −）。"""
    ctx = _mk_ctx()
    rows = attribute_deposits(ctx, _mk_result(ctx), PARAMS)
    _common_fields(rows, ctx)
    assert _keys(rows) == [
        ("auditor-01", "auditor", "S1", 1, 0.05 * AUDITOR_X),
        ("scout-01", "scout", "S1", 1, 0.05 * PARAMS.deposits.SCOUT_X),
        ("scout-02", "scout", "S1", 1, 0.05 * PARAMS.deposits.SCOUT_X),
        ("reviewer-01", "reviewer", "S1", 1, 0.05 * PARAMS.deposits.REVIEWER_X),
        ("reviewer-02", "reviewer", "S1", -1, 0.05 * PARAMS.deposits.REVIEWER_X),
        ("reviewer-03", "reviewer", "S1", 1, 0.05 * PARAMS.deposits.REVIEWER_X),
    ]


def test_matrix_s2_verified_real_rows():
    ctx = _mk_ctx()
    res = _mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL))
    rows = attribute_deposits(ctx, res, PARAMS)
    s2_rows = [p for p in rows if p.signal_tier is SignalTier.S2]
    assert _keys(s2_rows) == [
        ("auditor-01", "auditor", "S2", 1, S2_SOURCE_WEIGHT * AUDITOR_X),
        ("scout-01", "scout", "S2", 1, S2_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("scout-02", "scout", "S2", 1, S2_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("reviewer-01", "reviewer", "S2", 1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-02", "reviewer", "S2", -1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-03", "reviewer", "S2", 1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
    ]
    assert not any(p.role is Role.VERIFIER for p in rows)      # 无 S3 不记验证蜂（防自证）


def test_matrix_s2_verified_fp_rows_negative_direction():
    ctx = _mk_ctx()
    res = _mk_result(
        ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_FP, blocking=True)
    )
    rows = attribute_deposits(ctx, res, PARAMS)
    s2_rows = [p for p in rows if p.signal_tier is SignalTier.S2]
    # verdict 方向 = −1；评审蜂票（+/−/+）对 −1 → 符号翻转
    assert _keys(s2_rows) == [
        ("auditor-01", "auditor", "S2", -1, S2_SOURCE_WEIGHT * AUDITOR_X),
        ("scout-01", "scout", "S2", -1, S2_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("scout-02", "scout", "S2", -1, S2_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("reviewer-01", "reviewer", "S2", -1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-02", "reviewer", "S2", 1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-03", "reviewer", "S2", -1, S2_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
    ]


def test_matrix_verify_error_zero_deposits():
    """verify_error 零存款：S2 出错行不存在；纯出错 finding（无 panel/无 S3）零存款。"""
    ctx = _mk_ctx()
    res = _mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFY_ERROR))
    rows = attribute_deposits(ctx, res, PARAMS)
    assert all(p.signal_tier is SignalTier.S1 for p in rows)   # 只有 S1 行，无任何 S2 行
    # 无 panel（fast）且 S2 出错、S3 未触达 → 整个 finding 零存款
    fast = _mk_ctx(_mk_task(mode=RunMode.FAST), reporter_id="scout-03",
                   reporter_role=Role.SCOUT, panel=(), cluster_scouts=())
    res_fast = _mk_result(fast, votes=(), s1_direction=None,
                          s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFY_ERROR))
    assert attribute_deposits(fast, res_fast, PARAMS) == []


def test_matrix_s3_rows_and_verifier_confirm_overturn():
    """S3 行：全角色 ×1.0/×0.2/×0.5 + 验证蜂行（确认 +0.25 / 推翻 −0.5），落账日=arrival。"""
    ctx = _mk_ctx()
    base = dict(s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL))
    # 确认：S3 +1 与 S2 +1 同向 → 验证蜂 (+1, 0.25)
    res = _mk_result(ctx, s3=S3Record(direction=1, lag_days=4.2, arrival_day=7.2), **base)
    rows = attribute_deposits(ctx, res, PARAMS)
    _common_fields(rows, ctx)
    s3_rows = [p for p in rows if p.signal_tier is SignalTier.S3]
    assert _keys(s3_rows) == [
        ("auditor-01", "auditor", "S3", 1, S3_SOURCE_WEIGHT * AUDITOR_X),
        ("scout-01", "scout", "S3", 1, S3_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("scout-02", "scout", "S3", 1, S3_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
        ("reviewer-01", "reviewer", "S3", 1, S3_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-02", "reviewer", "S3", -1, S3_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("reviewer-03", "reviewer", "S3", 1, S3_SOURCE_WEIGHT * PARAMS.deposits.REVIEWER_X),
        ("verifier-01", "verifier", "S3", 1, PARAMS.deposits.VERIFIER_CONFIRM),
    ]
    assert [r.day for r in deposit_schedule(ctx, res, PARAMS)].count(3.0) == 12   # S1+S2 行在 task.day
    assert all(r.day == 7.2 for r in deposit_schedule(ctx, res, PARAMS) if r.payload.signal_tier is SignalTier.S3)
    # 推翻：S3 −1 与 S2 +1 反向 → 验证蜂 (−1, 0.5)
    res_bad = _mk_result(ctx, s3=S3Record(direction=-1, lag_days=9.0, arrival_day=12.0), **base)
    v_row = [p for p in attribute_deposits(ctx, res_bad, PARAMS) if p.role is Role.VERIFIER]
    assert _keys(v_row) == [("verifier-01", "verifier", "S3", -1, PARAMS.deposits.VERIFIER_CONFLICT)]
    # S2 无结论（verify_error）时即使 S3 到达也不记验证蜂（无 S2 结论可确认/推翻）
    res_err = _mk_result(ctx, s2=S2Record(VerifyOutcome.VERIFY_ERROR),
                         s3=S3Record(direction=1, lag_days=2.0, arrival_day=5.0))
    assert not any(p.role is Role.VERIFIER for p in attribute_deposits(ctx, res_err, PARAMS))


def test_matrix_fast_scout_reporter_row():
    """§16.7-1：fast 档 reporter=侦察蜂，verdict 按侦察行 ×SCOUT_X 记（主供血通道）。"""
    fast_task = _mk_task(mode=RunMode.FAST)
    ctx = _mk_ctx(fast_task, reporter_id="scout-03", reporter_role=Role.SCOUT,
                  panel=(), cluster_scouts=(), verifier_id=None)
    res = _mk_result(ctx, votes=(), s1_direction=None,
                     s3=S3Record(direction=-1, lag_days=3.0, arrival_day=6.0))
    rows = attribute_deposits(ctx, res, PARAMS)
    assert _keys(rows) == [
        ("scout-03", "scout", "S3", -1, S3_SOURCE_WEIGHT * PARAMS.deposits.SCOUT_X),
    ]


def test_matrix_coefficients_come_from_params():
    """角色系数取 params.yaml deposits 节（非硬编码）：换参 → 权重随之。"""
    custom = PARAMS.model_copy(update={
        "deposits": DepositsParams(SCOUT_X=0.3, REVIEWER_X=0.25,
                                   VERIFIER_CONFLICT=0.75, VERIFIER_CONFIRM=0.5),
    })
    ctx = _mk_ctx()
    res = _mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL),
                     s3=S3Record(direction=-1, lag_days=2.0, arrival_day=5.0))
    rows = attribute_deposits(ctx, res, custom)
    w = {(p.agent, p.signal_tier.value): p.weight for p in rows}
    assert w[("scout-01", "S2")] == pytest.approx(S2_SOURCE_WEIGHT * 0.3)
    assert w[("reviewer-01", "S1")] == pytest.approx(0.05 * 0.25)
    assert w[("auditor-01", "S3")] == pytest.approx(S3_SOURCE_WEIGHT * AUDITOR_X)
    v = [p for p in rows if p.role is Role.VERIFIER][0]
    assert (v.direction, v.weight) == (-1, 0.75)               # 推翻 → −VERIFIER_CONFLICT
    res_ok = _mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL),
                        s3=S3Record(direction=1, lag_days=2.0, arrival_day=5.0))
    v_ok = [p for p in attribute_deposits(ctx, res_ok, custom) if p.role is Role.VERIFIER][0]
    assert (v_ok.direction, v_ok.weight) == (1, 0.5)           # 确认 → +VERIFIER_CONFIRM


def test_context_validation_rules():
    """契约校验：纪律 2（评审≠报告者）、fast 档形态、重复票、侦察标记不含 reporter。"""
    with pytest.raises(ValueError, match="纪律 2"):
        _mk_ctx(panel=("auditor-01", "reviewer-01", "reviewer-02"))
    with pytest.raises(ValueError, match="fast"):
        _mk_ctx(_mk_task(mode=RunMode.FAST), panel=("reviewer-01",))
    with pytest.raises(ValueError, match="fast"):
        _mk_ctx(_mk_task(mode=RunMode.FAST), reporter_role=Role.AUDITOR)
    with pytest.raises(ValueError, match="审查蜂"):
        _mk_ctx(reporter_role=Role.SCOUT)                      # pr/full 必须 auditor
    with pytest.raises(ValueError, match="重复"):
        _mk_ctx(panel=("reviewer-01", "reviewer-01", "reviewer-02"))
    with pytest.raises(ValueError, match="双重归属"):
        _mk_ctx(cluster_scouts=("auditor-01", "scout-01"))
    with pytest.raises(ValueError, match="blocking_evidence"):
        S2Record(VerifyOutcome.VERIFIED_FP, blocking=False)    # §10.4：无证据强制改判


# ── ac_muz07uljygof9znu：S2 触达率 / S3 截断与触达率（统计断言）──

def test_s2_trigger_rate_full_30pct():
    full = [res for ctx, res in FLOW if ctx.task.mode is RunMode.FULL]
    non_full = [res for ctx, res in FLOW if ctx.task.mode is not RunMode.FULL]
    assert len(full) > 2500 and len(non_full) > 10000
    rate = sum(1 for r in full if r.s2_triggered) / len(full)
    assert abs(rate - 0.30) <= 0.025, f"S2 触达率偏移：{rate:.4f}"
    assert not any(r.s2_triggered for r in non_full)           # fast/pr 档零触达
    assert all(r.s2 is None for r in non_full)
    # 旋钮生效：s2_reach_full=0.6 → 触达率随动
    tasks60 = TaskGenerator(PARAMS, TAX, seed=21).generate(60)
    f6 = run_findings_flow(tasks60, POOL, seed=5, params=PARAMS,
                           vparams=VerdictParams(s2_reach_full=0.60))
    full6 = [r for c, r in f6 if c.task.mode is RunMode.FULL]
    rate6 = sum(1 for r in full6 if r.s2_triggered) / len(full6)
    assert abs(rate6 - 0.60) <= 0.07, f"触达旋钮未生效：{rate6:.4f}"


def test_s2_outcome_branch_rates_and_knob():
    """分支概率：判对→0.8（默认 ve=0.2）、判错→0.1 错误确认 / 0.45 verified_fp；旋钮扫描。"""
    trig = [r for _, r in FLOW if r.s2_triggered]
    real = [r for r in trig if r.truth_real]
    fp = [r for r in trig if not r.truth_real]
    assert len(real) > 300 and len(fp) > 400
    p_rr = sum(1 for r in real if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(real)
    p_wr = sum(1 for r in fp if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(fp)
    p_vfp = sum(1 for r in fp if r.s2.outcome is VerifyOutcome.VERIFIED_FP) / len(fp)
    assert abs(p_rr - 0.80) <= 0.06, f"判对分支确认率偏移：{p_rr:.4f}"
    assert abs(p_wr - 0.10) <= 0.04, f"判错分支错误确认率偏移：{p_wr:.4f}"
    assert abs(p_vfp - 0.45) <= 0.06, f"verified_fp 率偏移：{p_vfp:.4f}"
    assert all(r.s2.blocking for r in trig if r.s2.outcome is VerifyOutcome.VERIFIED_FP)
    assert not any(r.s2.blocking for r in trig if r.s2.outcome is not VerifyOutcome.VERIFIED_FP)
    # 饥饿旋钮：ve=0.5 → 判对分支确认率降至 1−0.5，S2 存款供给随之收缩
    real5 = [r for _, r in FLOW_VE5 if r.s2_triggered and r.truth_real]
    p_rr5 = sum(1 for r in real5 if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(real5)
    assert abs(p_rr5 - 0.50) <= 0.08, f"ve=0.5 确认率偏移：{p_rr5:.4f}"
    # S2 存款供给（结论 finding 数）随旋钮收缩，幅度 = 分支理论值：
    # 结论率 = p_real·(1−ve) + p_fp·(1−0.9·(1−p_blocking))（判错分支错误确认也是结论）
    p_real = len(real) / len(trig)
    p_fp = 1.0 - p_real
    p_bl = VerdictParams().p_blocking

    def concluded_rate(ve: float) -> float:
        return p_real * (1.0 - ve) + p_fp * (1.0 - (1.0 - p_bl) * 0.9)

    ratio_emp = (
        sum(1 for _, r in FLOW_VE5 if r.s2_concluded)
        / sum(1 for _, r in FLOW if r.s2_concluded)
    )
    ratio_theo = concluded_rate(0.5) / concluded_rate(0.2)
    assert abs(ratio_emp - ratio_theo) <= 0.05, (
        f"S2 结论供给收缩偏离分支理论：emp={ratio_emp:.4f} theo={ratio_theo:.4f}"
    )


def test_s3_lag_truncated_lognormal():
    """S3 延迟：截断 [1d,30d] 全体成立；均值 ≈ 解析截断均值（免 scipy erf 推导）。"""
    lags = [res.s3.lag_days for _, res in FLOW if res.s3]
    assert len(lags) > 10000
    assert all(1.0 <= x <= 30.0 for x in lags)
    mean_emp = statistics.fmean(lags)
    mean_theo = theoretical_lag_mean()
    assert abs(mean_emp - mean_theo) <= 0.15, f"lag 均值偏移：emp={mean_emp:.4f} theo={mean_theo:.4f}"
    # arrival_day = task.day + lag（逐条核验）
    for ctx, res in FLOW[:2000]:
        if res.s3 is not None:
            assert res.s3.arrival_day == pytest.approx(ctx.task.day + res.s3.lag_days, abs=1e-9)


def test_s3_reach_rates_40_concluded_80_inconclusive():
    concluded = [res for _, res in FLOW if res.s2_concluded]
    inconclusive = [res for _, res in FLOW if not res.s2_concluded]
    assert len(concluded) > 500 and len(inconclusive) > 15000
    r_c = sum(1 for r in concluded if r.s3 is not None) / len(concluded)
    r_i = sum(1 for r in inconclusive if r.s3 is not None) / len(inconclusive)
    assert abs(r_c - 0.40) <= 0.055, f"S2 结论触达率偏移：{r_c:.4f}"
    assert abs(r_i - 0.80) <= 0.012, f"inconclusive 触达率偏移：{r_i:.4f}"
    # 旋钮生效：s3_reach_concluded=0.8 → 结论触达随动
    tasks60 = TaskGenerator(PARAMS, TAX, seed=21).generate(60)
    f8 = run_findings_flow(tasks60, POOL, seed=6, params=PARAMS,
                           vparams=VerdictParams(s3_reach_concluded=0.80))
    conc8 = [r for _, r in f8 if r.s2_concluded]
    r_c8 = sum(1 for r in conc8 if r.s3 is not None) / len(conc8)
    assert abs(r_c8 - 0.80) <= 0.15, f"S3 触达旋钮未生效：{r_c8:.4f}"


def test_s3_error_rate_5pct():
    reached = [res for _, res in FLOW if res.s3 is not None]
    acc = sum(1 for r in reached if r.s3.direction == r.truth_sign) / len(reached)
    assert abs(acc - 0.95) <= 0.008, f"S3 方向准确率偏移：{acc:.4f}"


# ── 端到端：矩阵在整条流上的全局不变量 ──────────────────────────

def test_flow_invariants_and_scout_fast_supply():
    n_s2 = 0
    for ctx, res in FLOW:
        rows = attribute_deposits(ctx, res, PARAMS)
        # verify_error / 无结论 → 无 S2 行（零存款，§6.2）
        for p in rows:
            assert not (p.signal_tier is SignalTier.S2 and not res.s2_concluded)
            assert p.role is not Role.QUEEN_ADVISOR            # 蜂后不入生产口碑（§6.5）
            assert 0.0 < p.weight <= 1.0
        if res.s2_concluded:
            n_s2 += 1
            assert ctx.task.mode is RunMode.FULL               # S2 只发生在 full 档
            assert ctx.verifier_id is not None
    # fast 档侦察蜂 S3 供血：fast finding 的存款全部归属 scout 且按 ×SCOUT_X
    fast_rows = [
        p for ctx, res in FLOW if ctx.task.mode is RunMode.FAST
        for p in attribute_deposits(ctx, res, PARAMS)
    ]
    assert fast_rows and all(p.role is Role.SCOUT for p in fast_rows)
    assert all(p.signal_tier is SignalTier.S3 for p in fast_rows)
    assert all(p.direction in (-1, 1) for p in fast_rows)
    # S2 结论 finding 数 == S2 行组数（每结论 finding 恰 1+2+3 行）
    s2_group = sum(
        1 for ctx, res in FLOW if res.s2_concluded
        for p in attribute_deposits(ctx, res, PARAMS) if p.signal_tier is SignalTier.S2
        and p.role is Role.AUDITOR
    )
    assert s2_group == n_s2
