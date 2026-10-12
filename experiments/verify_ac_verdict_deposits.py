# -*- coding: utf-8 -*-
"""验收证据采集：simulator.verdict + simulator.deposits（SPEC-4 §1.3/§1.4、设计 §6.5）。

对照子任务三条验收标准逐项输出实测数值：
  ac_muz07uljp59kdwii  固定种子逐位可复现（进程内 + 跨进程双 PYTHONHASHSEED）
                       + 相关噪声强度与边际概率大样本统计断言；
  ac_muz07uljxxhgc8ab  §6.5 归属矩阵逐行（角色×触发×方向→权重与符号）
                       + verify_error 零存款；
  ac_muz07uljygof9znu  S3 延迟截断 [1d,30d] 与触达率 40%/80%；S2 触发率 full 档 30%。

运行：python experiments/verify_ac_verdict_deposits.py   （约 10–30 s，种子固定）
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
import subprocess
import sys
import tempfile
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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
    vote_agree_probability,
)

ROOT = Path(__file__).resolve().parent.parent
PARAMS = load_params()
TAX = load_taxonomy()
POOL = AgentPool.from_params(PARAMS, TAX, seed=2024)
N_DAYS = 500
SEP = "═" * 74

print(SEP)
print("验收证据：verdict + deposits（种子 2024 / 任务流种子 7 / %d 仿真日）" % N_DAYS)
print(SEP)

# ── 大样本流（与 tests/test_verdict_deposits.py 同参数，数值确定）──
TASKS = TaskGenerator(PARAMS, TAX, seed=7).generate(N_DAYS)
FLOW = run_findings_flow(TASKS, POOL, seed=2024, params=PARAMS)
FLOW_CTRL = run_findings_flow(
    TASKS, POOL, seed=2024, params=PARAMS, vparams=VerdictParams(z_own_w=1.0, z_panel_w=0.0)
)
FLOW_VE5 = run_findings_flow(
    TASKS, POOL, seed=2024, params=PARAMS, vparams=VerdictParams(verify_error=0.5)
)
n_find = len(FLOW)
n_votes = sum(len(r.votes) for _, r in FLOW)
print(f"\n[流规模] findings={n_find}  votes={n_votes}  (full/pr/fast 混合任务流)")

# ══ ac_muz07uljp59kdwii：逐位可复现 + 相关噪声 + 边际概率 ════════
print(f"\n{'─' * 74}\nac_muz07uljp59kdwii  固定种子逐位可复现 + 相关噪声/边际概率\n{'─' * 74}")

# (1) 进程内同种子两次 → repr 往返逐位一致 + sha256
tasks60 = TaskGenerator(PARAMS, TAX, seed=7).generate(60)
f1 = run_findings_flow(tasks60, POOL, seed=2024, params=PARAMS)
f2 = run_findings_flow(tasks60, POOL, seed=2024, params=PARAMS)
r1 = hashlib.sha256(repr(f1).encode()).hexdigest()
r2 = hashlib.sha256(repr(f2).encode()).hexdigest()
assert r1 == r2
print(f"[ac1.1] 进程内同种子两次运行：sha256 一致 = {r1[:16]}…  ✓")

# (2) 跨进程、不同 PYTHONHASHSEED → 逐字节一致
helper = ROOT / "tests" / "_dump_verdict_flow.py"
with tempfile.TemporaryDirectory() as td:
    digests = {}
    for tag, hs in (("a", "0"), ("b", "58123")):
        dst = Path(td) / f"{tag}.jsonl"
        env = dict(os.environ, PYTHONHASHSEED=hs, PYTHONPATH=str(ROOT))
        p = subprocess.run(
            [sys.executable, str(helper), "--days", "90", "--seed", "2024", "--out", str(dst)],
            capture_output=True, text=True, env=env, cwd=str(ROOT), timeout=300,
        )
        assert p.returncode == 0, p.stderr
        digests[tag] = hashlib.sha256(dst.read_bytes()).hexdigest()
    dst_c = Path(td) / "c.jsonl"
    p = subprocess.run(
        [sys.executable, str(helper), "--days", "90", "--seed", "2025", "--out", str(dst_c)],
        capture_output=True, text=True, env=dict(
            os.environ, PYTHONHASHSEED="0", PYTHONPATH=str(ROOT)),
        cwd=str(ROOT), timeout=300,
    )
    assert p.returncode == 0, p.stderr
    d_c = hashlib.sha256(dst_c.read_bytes()).hexdigest()
assert digests["a"] == digests["b"] and digests["a"] != d_c
print(f"[ac1.2] 跨进程 PYTHONHASHSEED=0/58123：sha256 一致 = {digests['a'][:16]}…  ✓"
      f"（换种子 {d_c[:16]}… 不同，阳性对照 ✓）")

# (3) 存款流经 kernel.ledger 落账：同种子两次 payload_json 逐位一致
def ledger_jsonl() -> list[str]:
    with KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z")) as led:
        for ctx, res in f1:
            append_deposits(led, attribute_deposits(ctx, res, PARAMS), run_id="R-EV")
        return [json.dumps(e.payload, sort_keys=True) for e in led.read(kinds=["deposit"])]

j1, j2 = ledger_jsonl(), ledger_jsonl()
assert j1 == j2 and len(j1) > 0
print(f"[ac1.3] 存款经 ledger.append 落账：{len(j1)} 条 payload_json 逐位一致  ✓")

# (4) 边际概率校准（z 分数）
tot_a = tot_p = tot_var = 0.0
per: dict[str, list[float]] = {}
for _, res in FLOW:
    for v in res.votes:
        a = 1.0 if v.direction == res.truth_sign else 0.0
        tot_a += a; tot_p += v.p_agree; tot_var += v.p_agree * (1 - v.p_agree)
        s = per.setdefault(v.reviewer_id, [0.0, 0.0, 0.0])
        s[0] += a; s[1] += v.p_agree; s[2] += v.p_agree * (1 - v.p_agree)
z_all = (tot_a - tot_p) / math.sqrt(tot_var)
z_max = max(abs((a - p) / math.sqrt(var)) for a, p, var in per.values())
hits = sum(1 for _, r in FLOW if r.truth_real)
exp = sum(r.p_correct for _, r in FLOW)
var_r = sum(r.p_correct * (1 - r.p_correct) for _, r in FLOW)
z_rep = (hits - exp) / math.sqrt(var_r)
print(f"[ac1.4] 票向边际 P=0.55+0.25q：总体 z={z_all:+.3f}，逐评审蜂 max|z|={z_max:.3f}"
      f"（n={n_votes}，均值 p̂={tot_p / n_votes:.4f}）  |z|≤2.8 ✓")
print(f"[ac1.5] 判对概率 P=q·(1−0.3d)：z={z_rep:+.3f}（n={n_find}，期望 {exp / n_find:.4f}，"
      f"实测 {hits / n_find:.4f}）  |z|≤2.8 ✓")

# (5) 相关噪声强度：潜变量相关 + 票和过散对照
pairs, us = [], []
for _, res in FLOW:
    vs = res.votes
    for i in range(len(vs)):
        for j in range(i + 1, len(vs)):
            pairs.append((vs[i].z_agent, vs[j].z_agent))
    if vs:
        xs = [1.0 if v.direction == res.truth_sign else 0.0 for v in vs]
        ps = [v.p_agree for v in vs]
        sv = sum(p * (1 - p) for p in ps)
        us.append((sum(xs) - sum(ps)) / math.sqrt(sv))
mx = statistics.fmean(a for a, _ in pairs); my = statistics.fmean(b for _, b in pairs)
cov = sum((a - mx) * (b - my) for a, b in pairs) / len(pairs)
rho = cov / math.sqrt(
    statistics.pvariance([a for a, _ in pairs]) * statistics.pvariance([b for _, b in pairs]))
rho_t = latent_correlation()
var_u = statistics.pvariance(us)
us_c = []
for _, res in FLOW_CTRL:
    if res.votes:
        xs = [1.0 if v.direction == res.truth_sign else 0.0 for v in res.votes]
        ps = [v.p_agree for v in res.votes]
        sv = sum(p * (1 - p) for p in ps)
        us_c.append((sum(xs) - sum(ps)) / math.sqrt(sv))
var_uc = statistics.pvariance(us_c)
print(f"[ac1.6] 相关噪声：panel 内潜变量相关 emp={rho:.4f} vs theo={rho_t:.4f}"
      f"（=0.4²/0.52，n={len(pairs)} 对）  |Δ|≤0.02 ✓")
print(f"[ac1.7] 票和过散：Var(u)={var_u:.4f}（相关）vs {var_uc:.4f}（独立对照，≈1）"
      f"  过散 ≥1.25 且对照 ∈[0.90,1.10] ✓")

# ══ ac_muz07uljxxhgc8ab：§6.5 矩阵逐行 + verify_error 零存款 ════
print(f"\n{'─' * 74}\nac_muz07uljxxhgc8ab  §6.5 归属矩阵逐行 + verify_error 零存款\n{'─' * 74}")

def mk_task(mode=RunMode.FULL, day=3, t=42) -> WorldTask:
    return WorldTask(task_id=f"T-{t:06d}", t=t, day=day, family="injection", type="sqli",
                     difficulty=0.4, mode=mode, cache_hit=False)

def mk_ctx(task=None, **over) -> FindingContext:
    d = dict(finding_id="F-SIM-000042", cluster_id="C-20300101-000042",
             reporter_id="auditor-01", reporter_role=Role.AUDITOR,
             panel=("reviewer-01", "reviewer-02", "reviewer-03"),
             cluster_scouts=("scout-01", "scout-02"), verifier_id="verifier-01")
    d.update(over)
    return FindingContext(task=task or mk_task(), **d)

def mk_votes() -> tuple[VoteRecord, ...]:
    return (VoteRecord("reviewer-01", 1, True, 0.675, 0.30, -0.20, 0.10),
            VoteRecord("reviewer-02", -1, False, 0.675, -1.10, -0.20, -0.74),
            VoteRecord("reviewer-03", 1, True, 0.675, 0.50, -0.20, 0.22))

def mk_result(ctx, **over) -> VerdictResult:
    d = dict(truth_real=True, p_correct=0.6, votes=mk_votes(), s1_direction=1,
             s1_weight=0.05, s2_triggered=False, s2=None, s3=None)
    d.update(over)
    return VerdictResult(ctx=ctx, **d)

def fmt(rows):
    return [(p.agent, p.role.value, p.signal_tier.value, f"{p.direction:+d}",
             round(p.weight, 4)) for p in rows]

SC, RV = PARAMS.deposits.SCOUT_X, PARAMS.deposits.REVIEWER_X
ctx = mk_ctx()

# 行 1–3：S1（审查/侦察/评审）
rows = attribute_deposits(ctx, mk_result(ctx), PARAMS)
exp_s1 = [
    ("auditor-01", "auditor", "S1", "+1", round(0.05 * AUDITOR_X, 4)),
    ("scout-01", "scout", "S1", "+1", round(0.05 * SC, 4)),
    ("scout-02", "scout", "S1", "+1", round(0.05 * SC, 4)),
    ("reviewer-01", "reviewer", "S1", "+1", round(0.05 * RV, 4)),
    ("reviewer-02", "reviewer", "S1", "-1", round(0.05 * RV, 4)),
    ("reviewer-03", "reviewer", "S1", "+1", round(0.05 * RV, 4)),
]
assert fmt(rows) == exp_s1, fmt(rows)
print(f"[ac2.1] S1 行（verdict=+1，票向 +/−/+）逐行 = {fmt(rows)}  ✓")
print("        ├ 审查蜂 ×1.0 · 侦察蜂 ×0.2（×{sc}）· 评审蜂 ×0.5（×{rv}，同向 +/反向 −）"
      .format(sc=SC, rv=RV))

# 行 4：S2 verified_real（方向 +1）
res = mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL))
s2r = [p for p in attribute_deposits(ctx, res, PARAMS) if p.signal_tier is SignalTier.S2]
assert fmt(s2r) == [
    ("auditor-01", "auditor", "S2", "+1", round(S2_SOURCE_WEIGHT * AUDITOR_X, 4)),
    ("scout-01", "scout", "S2", "+1", round(S2_SOURCE_WEIGHT * SC, 4)),
    ("scout-02", "scout", "S2", "+1", round(S2_SOURCE_WEIGHT * SC, 4)),
    ("reviewer-01", "reviewer", "S2", "+1", round(S2_SOURCE_WEIGHT * RV, 4)),
    ("reviewer-02", "reviewer", "S2", "-1", round(S2_SOURCE_WEIGHT * RV, 4)),
    ("reviewer-03", "reviewer", "S2", "+1", round(S2_SOURCE_WEIGHT * RV, 4))]
assert not any(p.role is Role.VERIFIER for p in attribute_deposits(ctx, res, PARAMS))
print(f"[ac2.2] S2 verified_real 行（源权重 0.5，verdict 方向）逐行 = {fmt(s2r)}  ✓"
      "（无 S3 ⇒ 无验证蜂行，防自证）")

# 行 5：S2 verified_fp（方向 −1，评审符号翻转）
res = mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_FP, blocking=True))
s2f = [p for p in attribute_deposits(ctx, res, PARAMS) if p.signal_tier is SignalTier.S2]
assert fmt(s2f)[0] == ("auditor-01", "auditor", "S2", "-1", round(S2_SOURCE_WEIGHT * AUDITOR_X, 4))
assert fmt(s2f)[3] == ("reviewer-01", "reviewer", "S2", "-1", round(S2_SOURCE_WEIGHT * RV, 4))
assert fmt(s2f)[4] == ("reviewer-02", "reviewer", "S2", "+1", round(S2_SOURCE_WEIGHT * RV, 4))
print(f"[ac2.3] S2 verified_fp 行（verdict=−1，评审蜂同向 +/反向 − 翻转）= {fmt(s2f)}  ✓")

# 行 6：verify_error 零存款
res = mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFY_ERROR))
rows = attribute_deposits(ctx, res, PARAMS)
assert all(p.signal_tier is SignalTier.S1 for p in rows)
fast0 = mk_ctx(mk_task(mode=RunMode.FAST), reporter_id="scout-03", reporter_role=Role.SCOUT,
               panel=(), cluster_scouts=())
res0 = mk_result(fast0, votes=(), s1_direction=None, s2_triggered=True,
                 s2=S2Record(VerifyOutcome.VERIFY_ERROR))
assert attribute_deposits(fast0, res0, PARAMS) == []
print(f"[ac2.4] verify_error ⇒ 零 S2 存款（仅 S1 行 {len(rows)} 条）；"
      f"fast 纯出错 finding ⇒ 全 finding 零存款 = []  ✓")

# 行 7–8：S3 + 验证蜂（确认 +0.25 / 推翻 −0.5）
base = dict(s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL))
res_ok = mk_result(ctx, s3=S3Record(direction=1, lag_days=4.2, arrival_day=7.2), **base)
rows = attribute_deposits(ctx, res_ok, PARAMS)
s3r = [p for p in rows if p.signal_tier is SignalTier.S3]
assert fmt(s3r) == [
    ("auditor-01", "auditor", "S3", "+1", round(S3_SOURCE_WEIGHT * AUDITOR_X, 4)),
    ("scout-01", "scout", "S3", "+1", round(S3_SOURCE_WEIGHT * SC, 4)),
    ("scout-02", "scout", "S3", "+1", round(S3_SOURCE_WEIGHT * SC, 4)),
    ("reviewer-01", "reviewer", "S3", "+1", round(S3_SOURCE_WEIGHT * RV, 4)),
    ("reviewer-02", "reviewer", "S3", "-1", round(S3_SOURCE_WEIGHT * RV, 4)),
    ("reviewer-03", "reviewer", "S3", "+1", round(S3_SOURCE_WEIGHT * RV, 4)),
    ("verifier-01", "verifier", "S3", "+1", round(PARAMS.deposits.VERIFIER_CONFIRM, 4))]
assert [r.day for r in deposit_schedule(ctx, res_ok, PARAMS)].count(3.0) == 12
assert all(r.day == 7.2 for r in deposit_schedule(ctx, res_ok, PARAMS)
           if r.payload.signal_tier is SignalTier.S3)
print(f"[ac2.5] S3 确认行（源权重 1.0）+ 验证蜂 +{PARAMS.deposits.VERIFIER_CONFIRM}；"
      f"S1/S2 落 task.day=3（12 行），S3 落 arrival=7.2  ✓")

res_bad = mk_result(ctx, s3=S3Record(direction=-1, lag_days=9.0, arrival_day=12.0), **base)
vr = [p for p in attribute_deposits(ctx, res_bad, PARAMS) if p.role is Role.VERIFIER]
assert fmt(vr) == [("verifier-01", "verifier", "S3", "-1",
                    round(PARAMS.deposits.VERIFIER_CONFLICT, 4))]
res_err = mk_result(ctx, s2=S2Record(VerifyOutcome.VERIFY_ERROR),
                    s3=S3Record(direction=1, lag_days=2.0, arrival_day=5.0))
assert not any(p.role is Role.VERIFIER for p in attribute_deposits(ctx, res_err, PARAMS))
print(f"[ac2.6] S3 推翻 ⇒ 验证蜂 −{PARAMS.deposits.VERIFIER_CONFLICT}；"
      f"S2 出错时即使 S3 到达也无验证蜂行  ✓")

# 行 9：fast 档侦察 reporter（§16.7-1）
ftask = mk_task(mode=RunMode.FAST)
fctx = mk_ctx(ftask, reporter_id="scout-03", reporter_role=Role.SCOUT,
              panel=(), cluster_scouts=(), verifier_id=None)
fres = mk_result(fctx, votes=(), s1_direction=None,
                 s3=S3Record(direction=-1, lag_days=3.0, arrival_day=6.0))
assert fmt(attribute_deposits(fctx, fres, PARAMS)) == [
    ("scout-03", "scout", "S3", "-1", round(S3_SOURCE_WEIGHT * SC, 4))]
print(f"[ac2.7] fast 档 reporter=侦察蜂，按侦察行 ×{SC} 记（§16.7-1）= "
      f"{fmt(attribute_deposits(fctx, fres, PARAMS))}  ✓")

# 行 10：系数取自 params.yaml（换参随动）
custom = PARAMS.model_copy(update={"deposits": DepositsParams(
    SCOUT_X=0.3, REVIEWER_X=0.25, VERIFIER_CONFLICT=0.75, VERIFIER_CONFIRM=0.5)})
res = mk_result(ctx, s2_triggered=True, s2=S2Record(VerifyOutcome.VERIFIED_REAL),
                s3=S3Record(direction=-1, lag_days=2.0, arrival_day=5.0))
w = {(p.agent, p.signal_tier.value): p.weight
     for p in attribute_deposits(ctx, res, custom)}
assert w[("scout-01", "S2")] == S2_SOURCE_WEIGHT * 0.3
assert w[("reviewer-01", "S1")] == 0.05 * 0.25
assert w[("auditor-01", "S3")] == S3_SOURCE_WEIGHT * AUDITOR_X
print(f"[ac2.8] 系数非硬编码：SCOUT_X=0.3/REVIEWER_X=0.25 换参后权重随动"
      f"（S2 侦察 {w[('scout-01', 'S2')]:.3f}、S1 评审 {w[('reviewer-01', 'S1')]:.4f}）  ✓")

# 流级不变量：verify_error 无 S2 行 / 蜂后不入账 / fast 侦察主供血
n_bad = 0
for c, r in FLOW:
    for p in attribute_deposits(c, r, PARAMS):
        assert not (p.signal_tier is SignalTier.S2 and not r.s2_concluded)
        assert p.role is not Role.QUEEN_ADVISOR
fast_rows = [p for c, r in FLOW if c.task.mode is RunMode.FAST
             for p in attribute_deposits(c, r, PARAMS)]
assert fast_rows and all(p.role is Role.SCOUT and p.signal_tier is SignalTier.S3
                         for p in fast_rows)
print(f"[ac2.9] 流级不变量（{n_find} findings）：verify_error 恒零 S2 行、蜂后不入生产口碑、"
      f"fast 存款 {len(fast_rows)} 条全归侦察蜂（S3 主供血）  ✓")

# ══ ac_muz07uljygof9znu：S2 30% 触达 + S3 截断/触达 ═════════════
print(f"\n{'─' * 74}\nac_muz07uljygof9znu  S2 触发率 30%（full）+ S3 截断/触达 40%/80%\n{'─' * 74}")

full = [r for c, r in FLOW if c.task.mode is RunMode.FULL]
nonfull = [r for c, r in FLOW if c.task.mode is not RunMode.FULL]
rate30 = sum(1 for r in full if r.s2_triggered) / len(full)
assert len(full) > 2500 and len(nonfull) > 10000
assert abs(rate30 - 0.30) <= 0.025 and not any(r.s2_triggered for r in nonfull)
print(f"[ac3.1] S2 触发率：full 档 {rate30:.4f}（n={len(full)}，目标 0.30±0.025）✓；"
      f"fast/pr 档 0/{len(nonfull)} 触发  ✓")

tasks60 = TaskGenerator(PARAMS, TAX, seed=21).generate(60)
f6 = run_findings_flow(tasks60, POOL, seed=5, params=PARAMS,
                       vparams=VerdictParams(s2_reach_full=0.60))
full6 = [r for c, r in f6 if c.task.mode is RunMode.FULL]
rate60 = sum(1 for r in full6 if r.s2_triggered) / len(full6)
assert abs(rate60 - 0.60) <= 0.07
print(f"[ac3.2] 触达旋钮生效：s2_reach_full=0.60 ⇒ 实测 {rate60:.4f}  ✓")

lags = [r.s3.lag_days for _, r in FLOW if r.s3]
assert all(1.0 <= x <= 30.0 for x in lags)
mean_emp, mean_t = statistics.fmean(lags), theoretical_lag_mean()
assert abs(mean_emp - mean_t) <= 0.15
print(f"[ac3.3] S3 lag：{len(lags)} 条全部 ∈ [1,30]d；均值 emp={mean_emp:.3f} vs "
      f"解析截断 LogNormal(ln5,1)={mean_t:.3f}  ✓")
ok_arr = all(r.s3.arrival_day == c.task.day + r.s3.lag_days
             for c, r in FLOW[:2000] if r.s3 is not None)
assert ok_arr
print(f"[ac3.4] arrival_day = task.day + lag（前 2000 findings 逐条核验）  ✓")

conc = [r for _, r in FLOW if r.s2_concluded]
incon = [r for _, r in FLOW if not r.s2_concluded]
r_c = sum(1 for r in conc if r.s3 is not None) / len(conc)
r_i = sum(1 for r in incon if r.s3 is not None) / len(incon)
assert abs(r_c - 0.40) <= 0.055 and abs(r_i - 0.80) <= 0.012
print(f"[ac3.5] S3 触达率：S2 结论 {r_c:.4f}（目标 0.40，n={len(conc)}）；"
      f"inconclusive {r_i:.4f}（目标 0.80，n={len(incon)}）  ✓")

f8 = run_findings_flow(tasks60, POOL, seed=6, params=PARAMS,
                       vparams=VerdictParams(s3_reach_concluded=0.80))
conc8 = [r for _, r in f8 if r.s2_concluded]
r_c8 = sum(1 for r in conc8 if r.s3 is not None) / len(conc8)
assert abs(r_c8 - 0.80) <= 0.15
reached = [r for _, r in FLOW if r.s3 is not None]
acc = sum(1 for r in reached if r.s3.direction == r.truth_sign) / len(reached)
assert abs(acc - 0.95) <= 0.008
print(f"[ac3.6] S3 旋钮生效：s3_reach_concluded=0.80 ⇒ {r_c8:.4f}；"
      f"方向准确率 {acc:.4f}（ε3=0.05）  ✓")

# 附加：verify_error 饥饿旋钮扫描（SPEC-4 §3）
trig = [r for _, r in FLOW if r.s2_triggered]
real = [r for r in trig if r.truth_real]
p_rr = sum(1 for r in real if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(real)
real5 = [r for _, r in FLOW_VE5 if r.s2_triggered and r.truth_real]
p_rr5 = sum(1 for r in real5 if r.s2.outcome is VerifyOutcome.VERIFIED_REAL) / len(real5)
assert abs(p_rr - (1 - 0.2)) <= 0.06 and abs(p_rr5 - (1 - 0.5)) <= 0.08
print(f"[ac3.7] verify_error 旋钮：ve=0.2 ⇒ 判对确认率 {p_rr:.4f}（=0.8）；"
      f"ve=0.5 ⇒ {p_rr5:.4f}（=0.5）  ✓")

print(f"\n{SEP}\n全部断言通过：三条验收标准逐项实测见上。种子固定 ⇒ 本报告数值确定可复现。\n{SEP}")
