"""simulator.engine 验收测试（SPEC-4 §0 闭环主循环）。

对照子任务验收标准：
  - ac_muz07uljrplfu9pv：固定种子端到端 run 两次，轨迹（事件序列与度量）逐位一致；
  - ac_muz07ulj5fsczmjq：run 产物含 dispatch/deposit/promotion/demotion 事件与
    9 项度量记录；overrides 生效（改 T_BASE 后路由行为不同）；
  - ac_muz07ulj3tfuw7pu：exam_seeding 开关与 TS 路由替换在引擎层生效（各有断言）。

场景标定说明（机制动力学的真实时标）：孵化门（Wilson 优势 + 暗考）决定 active
专员约在仿真第 10+ 日出现；tilt 专员通道必须有 active 格才会触发。故事件齐备性
与 T_BASE 敏感性场景取 18 日 × λ=40 + 加速孵化覆盖（promote.N/N_S2S3/DELTA/M、
demote.DORMANT_DAYS=1、tilt.GAMMA=0）。GAMMA=0 使 tilt ≡ clamp(T_BASE)——T_BASE
覆盖的效果不被自适应项的夹取吞掉（GAMMA=1 时 top−base>0.2 的格子两臂都夹到
T_MAX，路由逐位相同，无法归因）。8 日 × λ=20 短场景供对照臂/确定性增量断言。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import qswarm

from qswarm.contracts import LedgerKind, load_params
from qswarm.kernel.route_thompson import TS_CHANNEL, route_thompson
from qswarm.simulator import (
    ExamWorldParams,
    RunMetrics,
    TaskStreamParams,
    apply_overrides,
    run,
    run_s2_starvation,
)
from qswarm.simulator.engine import SimClock

# ── 场景常量（见模块 docstring 的标定说明）──────────────────────

STREAM_MAIN = TaskStreamParams(lam=40.0)
OVERRIDES_MAIN = {
    "tilt.GAMMA": 0,            # tilt ≡ T_BASE：T_BASE 覆盖可干净归因到路由分支
    "promote.N": 3,
    "promote.N_S2S3": 0,
    "promote.DELTA": 0.01,
    "promote.M": 1,             # 加速孵化：active 专员早于第 10 日出现
    "demote.DORMANT_DAYS": 1,   # 休眠判据收紧：run 内可产 demotion 事件
    "tilt.T_BASE": 0.7,        # 显式钉基线（params.yaml 定稿坐标不再承担测试语义）
    # 本标定针对引擎 harness 本体（事件齐备/度量齐备/路由归因），按 §7.3/§8.2
    # 字面机制跑；M0 定稿旋钮（DECISIONS 2026-10-09）在此关闭——定稿行为由
    # tests/test_m0_final_knobs.py 与官方全矩阵覆盖。
    "promote.M_PRORATE": False,
    "promote.UNIQUE_PER_FAMILY": False,
    "promote.WINDOW_ADVANTAGE": 0,
    "exam.ENTRY_DIRECT": 0.0,
    "exam.ENTRY_TOP_FRAC": 0.0,
    "exam.TYPE_ENTRY": 0.0,
    "exam.ENTRY_GRACE_DAYS": 0,
    "exam.ENTRY_CORROB_S2S3": 0.0,
    "exam.ENTRY_WINDOW_DAYS": 0,
    "exam.TYPE_ENTRY_COMBINE": 0.0,
    "demote.FC_DAYS": 0,
}
KW_MAIN = dict(
    seed=11, n_days=18, stream=STREAM_MAIN, overrides=OVERRIDES_MAIN, replay_every=6,
)
KW_SHORT = dict(seed=5, n_days=8, stream=TaskStreamParams(lam=20.0), replay_every=4)

NINE_METRIC_KEYS = {
    "promotion_precision",        # ①
    "promotion_recall",           # ②
    "median_time_to_active",      # ③
    "wrong_extinguish_rate",      # ④
    "degradation_response",       # ⑤
    "regret_total",               # ⑥（曲线终点累计；逐点曲线另有 regret_curve）
    "regret_per_task",            # ⑥（终点均值）
    "regret_late_over_early",     # ⑥（曲线后段/前段平均差距之比；sim_report 判定列）
    "cold_start_length",          # ⑦
    "replay_equivalent",          # ⑨（⑧跨运行，由 run_s2_starvation 单测覆盖）
}


@pytest.fixture(scope="module")
def base_run():
    """主场景基线 run（模块级共享，18 日 × λ=40，约 9 s）。"""
    return run(**KW_MAIN)


@pytest.fixture(scope="module")
def tbase_run():
    """主场景 + T_BASE 0.9 的覆盖 run（其余逐项一致）。"""
    return run(
        **{**KW_MAIN, "overrides": {**OVERRIDES_MAIN, "tilt.T_BASE": 0.9}}
    )


# ── ac_muz07uljrplfu9pv：固定种子两次 run 逐位一致 ────────────────


def test_same_seed_two_runs_bit_identical(base_run):
    second = run(**KW_MAIN)

    # 事件序列逐位一致：seq / 内核签发 ts / kind / payload 全量
    sig = lambda r: [
        (e.seq, e.ts, str(e.kind), json.dumps(e.payload, sort_keys=True))
        for e in r.events
    ]
    assert sig(base_run) == sig(second)

    # 规范化轨迹（事件 + 任务流 + 路由决策 + 度量 + 生效参数）与摘要逐位一致
    assert base_run.canonical_trajectory() == second.canonical_trajectory()
    assert base_run.digest == second.digest

    # 九项度量逐位一致（含 None 与浮点全等）
    assert base_run.metrics == second.metrics
    assert base_run.metrics_record() == second.metrics_record()

    # 夜批流转与逐日日志一致
    assert len(base_run.transitions) == len(second.transitions) > 0
    assert base_run.day_log == second.day_log


def test_events_ts_non_decreasing_along_seq(base_run):
    """内核时钟沿 seq 单调不减（metrics.TaskTimeline 的口径要求；replay 的 as_of
    窗把并列 ts 并入同一时点——播种批/夜批事件共享时间戳是设计内行为）。"""
    ts = [e.ts for e in base_run.events]
    assert all(a <= b for a, b in zip(ts, ts[1:]))
    # 跨仿真日严格递增（advance_to 的整日跳变恒大于批内并列）
    days = [e.ts[:10] for e in base_run.events]
    assert days == sorted(days)


# 跨进程子跑片段：与 KW_MAIN 完全同参的端到端 run，回显轨迹摘要（参数值由
# 测试本体从 KW_MAIN 注入，片段内不写死——防两处漂移）。
_CROSS_PROC_SNIPPET = """
import json, sys
sys.path.insert(0, __REPO_ROOT__)
from qswarm.simulator import TaskStreamParams, run

result = run(
    seed=__SEED__,
    n_days=__N_DAYS__,
    stream=TaskStreamParams(lam=__LAM__),
    replay_every=__REPLAY_EVERY__,
    overrides=__OVERRIDES__,
)
print(json.dumps({
    "seed": result.seed,
    "n_days": result.n_days,
    "t_base": result.params.tilt.T_BASE,
    "gamma": result.params.tilt.GAMMA,
    "n_events": len(result.events),
    "digest": result.digest,
}))
"""


def test_same_seed_bit_identical_across_processes(base_run):
    """确定性不依赖进程与哈希序：子进程用不同 PYTHONHASHSEED 重跑同 (seed, 配置)，
    规范化轨迹摘要与进程内 run 逐位相同（排除同进程缓存/哈希序偶然一致）。"""
    repo_root = str(Path(qswarm.__file__).resolve().parent.parent)
    snippet = (
        _CROSS_PROC_SNIPPET
        .replace("__REPO_ROOT__", repr(repo_root))
        .replace("__SEED__", repr(KW_MAIN["seed"]))
        .replace("__N_DAYS__", repr(KW_MAIN["n_days"]))
        .replace("__LAM__", repr(KW_MAIN["stream"].lam))
        .replace("__REPLAY_EVERY__", repr(KW_MAIN["replay_every"]))
        .replace("__OVERRIDES__", repr(KW_MAIN["overrides"]))
    )
    proc = subprocess.run(
        [sys.executable, "-c", snippet],
        capture_output=True, text=True, timeout=900,
        env={**os.environ, "PYTHONHASHSEED": "424242"},  # 与父进程哈希序刻意不同
    )
    assert proc.returncode == 0, f"子进程失败：\n{proc.stderr}"
    echo = json.loads(proc.stdout)

    # 生效配置与父进程场景逐项一致（片段与 KW_MAIN 无漂移）
    assert echo["seed"] == base_run.seed
    assert echo["n_days"] == base_run.n_days
    assert echo["t_base"] == base_run.params.tilt.T_BASE
    assert echo["gamma"] == base_run.params.tilt.GAMMA
    assert echo["n_events"] == len(base_run.events)

    # 跨进程逐位一致（事件序列 + 路由 + 任务流 + 度量的规范化 JSON 摘要）
    assert echo["digest"] == base_run.digest


def test_different_seed_produces_different_trajectory():
    a = run(seed=5, n_days=8, stream=TaskStreamParams(lam=20), replay_every=0)
    b = run(seed=6, n_days=8, stream=TaskStreamParams(lam=20), replay_every=0)
    assert a.digest != b.digest
    assert a.metrics != b.metrics or a.events != b.events


# ── ac_muz07ulj5fsczmjq：事件齐备 + 9 项度量 + overrides 生效 ────


def test_run_produces_all_event_kinds_and_nine_metrics(base_run):
    kinds = base_run.event_kinds()
    for kind in ("dispatch", "deposit", "promotion", "demotion"):
        assert kinds[kind] > 0, f"run 产物缺 {kind} 事件：{dict(kinds)}"
    # 考试播种与暗考事件也在闭环内产生（§8.4/§8.2）
    assert kinds["exam_result"] > 0
    exam_deposits = [
        e for e in base_run.events
        if e.kind is LedgerKind.DEPOSIT and e.payload.get("origin") == "exam"
    ]
    fam_exam = [e for e in exam_deposits if len(e.payload["taxonomy_path"]) == 1]
    assert len(fam_exam) == 18 * 6           # roster 18 只 × 6 家族，每格恰一笔
    # v2 类型席位入格推动存款（§7.1 惰性建格的兑现）：恰 = 类型提名数
    type_exam = [e for e in exam_deposits if len(e.payload["taxonomy_path"]) == 2]
    type_noms = [
        e for e in base_run.events
        if e.kind is LedgerKind.PROMOTION
        and str(e.payload.get("exam_id", "")).startswith("entry-fast:")
        and "/" in str(e.payload["node"])
    ]
    assert len(type_exam) == len(type_noms)

    # 9 项度量记录：键集与条数逐项对应 SPEC-4 §2 表（⑥ 另带曲线读数列）
    record = base_run.metrics_record()
    assert set(record) == NINE_METRIC_KEYS
    assert len(record) == 10
    assert RunMetrics.__dataclass_fields__.keys() == NINE_METRIC_KEYS

    # ⑨ 重放等价：环内可配时点校验 + 收尾全账本校验全部通过（回归项 100%）
    assert base_run.replay_report.passed
    assert len(base_run.replay_day_reports) > 0
    assert all(rep.passed for rep in base_run.replay_day_reports)

    # 闭环健康度：全程无未派单任务（显式探索池永不死锁）
    assert all(r.assignee is not None for r in base_run.routing)
    assert len(base_run.routing) == len(base_run.tasks) > 0


def test_apply_overrides_point_path_semantics():
    p = apply_overrides(load_params(), {"tilt.T_BASE": 0.9, "promote.N": 12})
    assert p.tilt.T_BASE == 0.9
    assert p.promote.N == 12
    assert p.tilt.GAMMA == 1.0                    # 未覆盖字段原样保留
    assert p.deposits.REVIEWER_X == 0.5

    import pydantic

    with pytest.raises(ValueError):               # 未知节
        apply_overrides(load_params(), {"nope.N": 1})
    with pytest.raises(ValueError):               # 未知字段
        apply_overrides(load_params(), {"tilt.NOPE": 1})
    with pytest.raises(ValueError):               # 非 '节.字段' 点路径
        apply_overrides(load_params(), {"tilt": 1})
    with pytest.raises(pydantic.ValidationError):  # 越界值（T_BASE ∉ [0,1]）
        apply_overrides(load_params(), {"tilt.T_BASE": 1.5})
    with pytest.raises(pydantic.ValidationError):  # 交叉约束（T_MIN > T_MAX）
        apply_overrides(load_params(), {"tilt.T_MIN": 0.95, "tilt.T_MAX": 0.1})
    # 原对象不被修改（configs/params.yaml 的定稿坐标 = 0.6，显式比对而非幻数）
    assert load_params().tilt.T_BASE == 0.6


def test_override_t_base_changes_routing_behavior(base_run, tbase_run):
    # 覆盖确实生效（其余参数逐项一致；基线被 OVERRIDES_MAIN 显式钉 0.7）
    assert base_run.params.tilt.T_BASE == 0.7
    assert tbase_run.params.tilt.T_BASE == 0.9
    assert tbase_run.params.tilt.GAMMA == base_run.params.tilt.GAMMA == 0.0
    assert tbase_run.seed == base_run.seed

    # 路由行为不同：同一批任务的派单结果出现分歧（tilt 概率 0.7 → 0.9）
    diff = [
        (x, y) for x, y in zip(base_run.routing, tbase_run.routing)
        if x.assignee != y.assignee
    ]
    assert len(diff) > 0, "改 T_BASE 后路由行为必须不同"
    # 专员通道使用率随之不同（tilt 命中率上移）
    spec_a = sum(1 for r in base_run.routing if r.channel == "specialist")
    spec_b = sum(1 for r in tbase_run.routing if r.channel == "specialist")
    assert spec_b > spec_a >= 0

    # 轨迹与度量随之不同
    assert base_run.digest != tbase_run.digest
    assert base_run.metrics != tbase_run.metrics


# ── ac_muz07ulj3tfuw7pu：exam_seeding 开关与 TS 路由替换 ─────────


def _exam_deposit_count(result) -> int:
    """§8.4 家族级入学存款计数（一级路径；v2 类型席位入格存款另计）。"""
    return sum(
        1 for e in result.events
        if e.kind is LedgerKind.DEPOSIT and e.payload.get("origin") == "exam"
        and len(e.payload["taxonomy_path"]) == 1
    )


def test_exam_seeding_switch_at_engine_level():
    on = run(**KW_SHORT)
    off = run(**KW_SHORT, exam=ExamWorldParams(seeding=False))

    # 开=每 (agent, family) 恰一笔 origin=exam 存款；关=零考试事件（§1.5 对照组）
    assert on.exam.seeding is True
    assert off.exam.seeding is False
    assert _exam_deposit_count(on) == 18 * 6
    assert _exam_deposit_count(off) == 0

    # 对照组整条轨迹不同（播种影响路由 C* → 派单序列）
    assert on.digest != off.digest

    # 无播种不冷死：显式探索池（ROOT_PRIOR 权重）保证闭环照常运转
    assert len(off.routing) == len(off.tasks) > 0
    assert all(r.assignee is not None for r in off.routing)
    assert off.event_kinds()["dispatch"] > 0
    assert off.replay_report.passed


def test_ts_router_replacement_at_engine_level():
    base = run(**KW_SHORT)
    ts = run(**KW_SHORT, router=route_thompson)

    # 引擎层替换生效：全部派单走 TS 通道；路由器名回显
    assert ts.router_name == "route_thompson"
    assert len(ts.routing) == len(ts.tasks) > 0
    assert {r.channel for r in ts.routing} == {TS_CHANNEL}

    # 对照结论的对照面：同参数默认臂不是 TS（双通道词表）
    assert base.router_name == "kernel.route"
    assert {r.channel for r in base.routing} <= {"specialist", "explore"}
    assert any(r.channel == "explore" for r in base.routing)

    # 同种子同参数，仅换路由器 ⇒ 轨迹不同；内核侧等价性不受路由臂影响
    assert base.digest != ts.digest
    assert base.replay_report.passed and ts.replay_report.passed


# ── ⑧ S2 饥饿传导驱动（跨运行度量走引擎）─────────────────────────


def test_s2_starvation_driver_runs_three_arms():
    out = run_s2_starvation(
        seed=3, n_days=3, stream=TaskStreamParams(lam=12.0), replay_every=0
    )
    assert out.points == (0.2, 0.35, 0.5)
    assert set(out.metrics_by_point) == {0.2, 0.35, 0.5}
    for rec in out.metrics_by_point.values():
        assert set(rec) == NINE_METRIC_KEYS
    assert out.report.reference == 0.2 and out.report.limit == 2.0
    assert isinstance(out.report.passed, bool)


# ── 引擎时钟与资源纪律单元断言 ──────────────────────────────────


def test_sim_clock_strictly_monotonic():
    from datetime import timedelta

    c = SimClock()
    seen = [c.now()]
    for day in (0.0, 0.0, 0.5, 1.0, 1.0, 2.5):
        c.advance_to(day)
        seen.append(c.now())
    assert all(b - a >= timedelta(seconds=1) for a, b in zip(seen, seen[1:]))
    assert seen[0] == seen[1] - timedelta(seconds=1)   # 同日推进 = 逐秒前移


def test_temp_ledger_cleaned_up_on_engine_failure(tmp_path, monkeypatch):
    """run 中途抛异常也不泄漏临时账本目录（owned_db 的清理走 finally 兜底）。

    隔离口径：把 tempfile.gettempdir() 钉到本测试私有的 tmp_path——共享 /tmp 下
    存在并发 pytest 会话（taskflow 并发子任务）各自创建/清理 qswarm-engine-*，
    直接扫描 /tmp 会把他会话窗口内消失的目录误判成本测试泄漏（2026-10-10 实测
    误报）。mkdtemp(dir=None) 内部走 gettempdir()，引擎临时目录随之进入私有区。
    """
    import glob
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    def boom(*_a, **_k):
        raise RuntimeError("路由器爆炸")

    marker = str(tmp_path / "qswarm-engine-*")
    before = set(glob.glob(marker))
    with pytest.raises(RuntimeError, match="路由器爆炸"):
        run(seed=1, n_days=2, stream=TaskStreamParams(lam=5.0), router=boom)
    assert set(glob.glob(marker)) == before   # 无新增残留目录
