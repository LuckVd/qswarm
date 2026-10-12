#!/usr/bin/env python3
"""freeze_golden：从一段仿真轨迹确定性冻结内核黄金用例回归集（SPEC-4 §4 输出物 3）。

流程（同 (seed, n_days) ⇒ 输出逐字节一致，无任何墙钟时间入场）：

1. ``simulator.engine.run(seed, n_days)`` 跑一段中等长度轨迹（默认 30 日）；
2. **全量路由回放**：按 engine 同一口径重建逐日 CellView（同一账本前缀 + 同一
   as_of 过滤）与同一 rng 子流（``derive_seed(seed,"engine","route")`` 顺序消耗），
   逐条调用 ``kernel.route`` 并与轨迹记录的 RoutingRecord 五字段逐一核对——
   回放 = 轨迹，证明夹具的输入切片确实再生产轨迹中的决策；
3. 分层抽样四类用例（前三类各 ≥6）：route 决策（按 通道/原因/均匀保底/角色 分桶）、
   route_thompson 对照臂（与配对 route 用例同切片/同任务/同池/同 rng 快进偏移换
   路由器——TS 臂非轨迹派单策略，expected = 确定性重放，覆盖 Beta 后验公式族）、
   promote_scan 状态流转（按夜分组，覆盖 candidate/exam_pass/exam_fail 多格夜；
   若轨迹存在 demotion 夜则保底抽中一夜）、
   agg_cells 数值（早/中/晚期时点 + 一个夜锚点全格表 + c*/LOO 探针）；
4. 账本片段（deposit/promotion/demotion 紧凑编码 + (day, i) 整秒时刻）按需共享为
   slices/slice-*.json；用例为
   tests/golden/{route,route_thompson,promote_scan,agg_cells}/case-*.json；
5. **落盘前自检**：每条用例先经 tests/golden_loader.check_case 在当前内核上重放
   并精确比对，任何一条不一致即中止（不产出坏夹具）。

用法::

    python3 scripts/freeze_golden.py                        # 默认 seed=7 n_days=30，写入 tests/golden/
    python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check
    # --check：内存中重新生成并与 tests/golden/ 逐字节比对（溯源再生成验证）。

seq 口径：片段重放时账本 seq 按片段顺序重新编号，故期望输出不含任何 seq
（Transition.seq / cells.last_event_seq 不参与比对）；时刻编码 (day, i) 精确还原
SimClock 的 ``T0 + day·86400s + i·1s``。浮点期望值为逐位精确（JSON repr 往返保真）。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))          # 复用夹具装载器（同一编解码，不漂移）

import golden_loader as gl                        # noqa: E402

from qswarm.contracts import (  # noqa: E402
    LedgerKind,
    ParamsConfig,
    Role,
    load_params,
    load_taxonomy,
)
from qswarm.kernel import (  # noqa: E402
    SLIDING_WINDOW_DAYS,
    agg_cells,
    route,
    route_thompson,
)
from qswarm.kernel.ledger import KernelLedger, iso_utc                      # noqa: E402
from qswarm.simulator.engine import _RoutedTask, run                        # noqa: E402
from qswarm.simulator.metrics import SIM_T0                                 # noqa: E402
from qswarm.simulator.rng import derive_seed                                # noqa: E402
from qswarm.simulator.world import AgentPool                                # noqa: E402

MECHANIC_KINDS = (LedgerKind.DEPOSIT, LedgerKind.PROMOTION, LedgerKind.DEMOTION)
#: 轨迹与用例共用的衰退滑动窗窗长：取 kernel 当前定稿值（M0 调参定稿 30→7，
#: DECISIONS 2026-10-09）。此前本常量钉死 30.0（v1 冻结期口径），与现行引擎缺省
#: 脱钩 —— promote 用例重放的窗内质量/基线因此偏离轨迹（--check 基线红的真实
#: 根因之一）；现从内核取值并显式透传 run()，轨迹与重放同窗长。
WINDOW_DAYS = float(SLIDING_WINDOW_DAYS)


# ── 通用工具 ────────────────────────────────────────────────────


class CountingRandom(random.Random):
    """random.Random：同名流逐位一致，仅多记 random() 消耗次数（route 契约：只调 random()）。"""

    def __init__(self, seed: int) -> None:
        super().__init__(seed)
        self.count = 0

    def random(self) -> float:
        self.count += 1
        return super().random()


def _iso_dt(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def _jsonable(x: Any) -> Any:
    """StrEnum/tuple → 纯 str/list（JSON 规范化，sort_keys 下逐字节稳定）。"""
    if is_dataclass(x) and not isinstance(x, type):
        return {k: _jsonable(v) for k, v in asdict(x).items()}
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, str):
        return str(x)                                 # StrEnum → str
    return x


def _dump_bytes(doc: dict) -> bytes:
    return (json.dumps(_jsonable(doc), ensure_ascii=False, sort_keys=True, indent=1)
            + "\n").encode("utf-8")


def _encode_event_row(ev, t0: datetime) -> list:
    """账本事件 → 紧凑行 [code, day, i, *payload 字段]（字段序见 golden_loader）。"""
    p = ev.payload
    day, i = divmod(int((_iso_dt(ev.ts) - t0).total_seconds()), 86400)
    if ev.kind is LedgerKind.DEPOSIT:
        return ["d", day, i, p["finding"], p["cluster"], p["agent"], p["role"],
                list(p["taxonomy_path"]), p["signal_tier"], int(p["direction"]),
                float(p["weight"]), p["origin"]]
    if ev.kind is LedgerKind.PROMOTION:
        return ["p", day, i, p["agent"], p["role"], p["node"], p["exam_id"],
                p["from_status"], p["to_status"]]
    if ev.kind is LedgerKind.DEMOTION:
        return ["m", day, i, p["agent"], p["role"], p["node"], p["reason"]]
    raise ValueError(f"片段不含 kind={ev.kind} 的事件")


# ── 轨迹加载与全量路由回放 ────────────────────────────────────────


def build_trajectory(seed: int, n_days: int) -> dict:
    res = run(seed=seed, n_days=n_days, replay_every=0,
              promote_window_days=WINDOW_DAYS)
    events = res.events
    mech = [e for e in events if e.kind in MECHANIC_KINDS]
    dispatches = [e for e in events if e.kind is LedgerKind.DISPATCH]
    if len(dispatches) != len(res.routing):
        raise AssertionError("dispatch 事件数与 routing 记录数不一致")

    # 逐日边界：day_log 的任务数累计 → dispatch 事件切片
    day_bounds: list[tuple[int, int]] = []
    start = 0
    for rec in res.day_log:
        day_bounds.append((start, start + rec.tasks))
        start += rec.tasks

    def day_as_of(day: int) -> str | None:
        lo, hi = day_bounds[day]
        if hi <= lo:
            return None                               # 空日（λ=50 下实际不会出现）
        return iso_utc(_iso_dt(dispatches[lo].ts) - timedelta(seconds=1))

    # 夜锚点：promotion/demotion 事件的共享 ts（SimClock 夜批不推进 → 同 ts）
    tr_ts = {e.seq: e.ts for e in events}
    nights: dict[str, list] = {}
    for tr in res.transitions:
        nights.setdefault(tr_ts[tr.seq], []).append(tr)
    night_ts_by_day: dict[int, str] = {
        (_iso_dt(ts) - SIM_T0).days: ts for ts in nights
    }

    # 全量路由回放：同一账本前缀 + 同一 rng 子流，逐条核对 RoutingRecord
    pool = AgentPool.generate(
        res.params.roster.model_dump(), load_taxonomy(), seed=seed, hidden=res.hidden
    )
    pools = {role.value: [a.agent_id for a in pool.by_role(role)]
             for role in (Role.SCOUT, Role.AUDITOR)}

    clock = gl.FragmentClock([_iso_dt(e.ts) for e in mech], _iso_dt(mech[-1].ts))
    replay = KernelLedger(":memory:", clock=clock)
    tasks_by_day: dict[int, list] = {}
    for t in res.tasks:
        tasks_by_day.setdefault(t.day, []).append(t)

    decisions: list[dict] = []
    try:
        for ev in mech:
            replay.append(ev.kind.value, ev.payload)
        rng = CountingRandom(derive_seed(seed, "engine", "route"))
        idx = 0
        for day in range(n_days):
            as_of = day_as_of(day)
            if as_of is None:
                continue
            view = agg_cells(replay, _iso_dt(as_of), params=res.params)
            for task in tasks_by_day.get(day, []):
                draws_before = rng.count
                rt = _RoutedTask(task)
                dec = route(rt, view, rng, res.params, pool=pools[rt.role])
                rec = res.routing[idx]
                got = (dec.task_id, dec.role, dec.node, dec.assignee, dec.channel)
                want = (rec.task_id, rec.role, rec.node, rec.assignee, rec.channel)
                if got != want:
                    raise AssertionError(
                        f"路由回放失配 @routing[{idx}] day={day}: 回放{got} != 轨迹{want}")
                decisions.append(
                    {"index": idx, "day": day, "as_of": as_of,
                     "draws_before": draws_before, "decision": dec})
                idx += 1
    finally:
        replay.close()
    if idx != len(res.routing):
        raise AssertionError(f"回放任务数 {idx} != 轨迹 routing 数 {len(res.routing)}")

    return {
        "res": res,
        "events": events,
        "mech": mech,
        "day_bounds": day_bounds,
        "day_as_of": day_as_of,
        "nights": nights,
        "night_ts_by_day": night_ts_by_day,
        "pools": pools,
        "decisions": decisions,
    }


# ── 分层抽样 ────────────────────────────────────────────────────


def sample_route_cases(traj: dict, want: int, digest: str, seed: int,
                       params_doc: dict) -> list[dict]:
    decisions = traj["decisions"]
    pools = traj["pools"]
    task_by_id = {t.task_id: t for t in traj["res"].tasks}

    def bucket_of(d: dict) -> tuple:
        dec = d["decision"]
        return (dec.channel, dec.reason,
                dec.uniform_pick if dec.channel == "explore" else None)

    # 优先级愿望单：专员命中（两角色）、tilt_miss 均匀/加权、no_specs 均匀/加权、
    # 第二个专员命中（异日）、第二个 no_specs 加权（scout）、第二个 tilt_miss 均匀
    wishlist = [
        ("specialist", "tilt_hit", None, "scout"),
        ("specialist", "tilt_hit", None, "auditor"),
        ("explore", "tilt_miss", True, None),
        ("explore", "tilt_miss", False, None),
        ("explore", "no_specs", True, None),
        ("explore", "no_specs", False, None),
        ("specialist", "tilt_hit", None, "auditor"),
        ("explore", "no_specs", False, "scout"),
        ("explore", "tilt_miss", True, None),
    ]
    chosen: list[dict] = []
    used: set[int] = set()
    for wb in wishlist:
        if len(chosen) >= want:
            break
        for d in decisions:
            if d["index"] in used:
                continue
            b = bucket_of(d)
            role_ok = wb[3] is None or d["decision"].role == wb[3]
            if b == wb[:3] and role_ok:
                chosen.append(d)
                used.add(d["index"])
                break
    stride = max(1, len(decisions) // (want * 3))     # 不足则全轨迹铺开补齐
    for k in range(0, len(decisions), stride):
        if len(chosen) >= want:
            break
        if decisions[k]["index"] not in used:
            chosen.append(decisions[k])
            used.add(decisions[k]["index"])

    cases = []
    for n, d in enumerate(sorted(chosen, key=lambda x: x["index"]), 1):
        dec = d["decision"]
        task = task_by_id[dec.task_id]
        cases.append({
            "case_id": f"route-{n:03d}",
            "kind": "route",
            "slice": f"day-{d['day']:02d}",
            "meta": {
                "seed": seed,
                "trajectory_digest": digest,
                "day": d["day"],
                "routing_index": d["index"],
                "task_id": dec.task_id,
                "draws_before": d["draws_before"],
                "route_channel_in_trajectory": dec.channel,
                "route_reason_in_trajectory": dec.reason,
                "cross_checked": f"回放派单与轨迹 RoutingRecord(task_id,role,node,assignee,channel)"
                                 f" 逐字段一致（冻结时全量 {len(traj['decisions'])} 条核对）",
                "note": "expected=route_seed 流快进 draws_before 后调用 kernel.route 的完整决策；"
                        "rng 消耗顺序契约见 kernel.route 模块 docstring",
            },
            "params": params_doc,
            "inputs": {
                "slice_as_of": d["as_of"],
                "task": {
                    "task_id": dec.task_id,
                    "role": dec.role,
                    "family": dec.family,
                    "type": task.type,
                },
                "pool": pools[dec.role],
            },
            "expected": _jsonable(asdict(dec)),
        })
    return cases


def sample_route_thompson_cases(traj: dict, route_cases: list[dict], digest: str,
                                seed: int, params_doc: dict) -> list[dict]:
    """TS 对照臂用例：与每条 route 用例逐条配对（同切片/同任务/同池/同 rng 快进偏移）。

    TS 臂不是轨迹的派单策略（轨迹由 kernel.route 生成），没有可直接核对的
    RoutingRecord；expected 由 assemble 阶段经 golden_loader 的同一重放语义回填
    （与 agg 用例同一自洽口径）。价值：Beta 后验（prior=λ/4）、后验均值、逐候选
    采样序等 TS 公式族的任何改动都会在这里先红（配对信息见 meta.paired_case）。
    """
    task_by_id = {t.task_id: t for t in traj["res"].tasks}
    cases = []
    for n, rc in enumerate(route_cases, 1):
        dec = rc["expected"]
        task = task_by_id[dec["task_id"]]
        cases.append({
            "case_id": f"ts-{n:03d}",
            "kind": "route_thompson",
            "slice": rc["slice"],
            "meta": {
                "seed": seed,
                "trajectory_digest": digest,
                "day": rc["meta"]["day"],
                "task_id": dec["task_id"],
                "draws_before": rc["meta"]["draws_before"],
                "paired_case": rc["case_id"],
                "paired_channel_in_trajectory": dec["channel"],
                "note": "expected=route_seed 流快进 draws_before 后调用 kernel.route_thompson"
                        " 的完整决策（TS 对照臂，非轨迹派单策略；候选集/过载语义与默认臂"
                        "一致，逐候选恰 1 次 Beta 后验采样，rng 消耗顺序=候选字典序）",
            },
            "params": params_doc,
            "inputs": {
                "slice_as_of": rc["inputs"]["slice_as_of"],
                "task": {
                    "task_id": dec["task_id"],
                    "role": dec["role"],
                    "family": dec["family"],
                    "type": task.type,
                },
                "pool": rc["inputs"]["pool"],
            },
            "expected": None,                      # 由 assemble 阶段经 loader 语义回填
        })
    return cases


def sample_promote_cases(traj: dict, want: int, digest: str, seed: int,
                         params_doc: dict) -> list[dict]:
    nights = traj["nights"]
    night_ts_by_day = traj["night_ts_by_day"]

    def score(ts: str) -> tuple:
        trs = nights[ts]
        reasons = {t.reason for t in trs}
        return (
            5 * any(t.kind == "demotion" for t in trs)      # 衰退/休眠夜优先入样
            + 3 * ("exam_fail" in reasons)
            + 2 * ("exam_pass" in reasons)
            + 2 * ("candidate" in reasons)
            + (len(trs) > 1)
            + len(reasons)
        )

    order = sorted(nights, key=lambda ts: (-score(ts), ts))
    picked: list[str] = []
    for ts in order:                                   # 先保多样性，夜间隔 ≥2 天
        if len(picked) >= want:
            break
        d = (_iso_dt(ts) - SIM_T0).days
        if all(abs(d - (_iso_dt(p) - SIM_T0).days) >= 2 for p in picked):
            picked.append(ts)
    demo_ts = [ts for ts in order if any(t.kind == "demotion" for t in nights[ts])]
    if demo_ts and not any(ts in demo_ts for ts in picked):
        # 保底：轨迹存在 demotion 夜则必须抽中一夜——衰退/休眠公式（§7.3 转换③/④/⑥）
        # 的加严与抑制两个突变方向都在轨迹面上可观测（suppressive 方向无 demotion
        # 期望则不可检出）。间隔冲突时让出最晚一夜（确定性置换）。
        ts0 = demo_ts[0]
        d0 = (_iso_dt(ts0) - SIM_T0).days
        if all(abs(d0 - (_iso_dt(p) - SIM_T0).days) >= 2 for p in picked):
            picked.append(ts0)
        elif picked:
            picked.pop()                               # 弃最晚一夜，换 demotion 夜
            picked.append(ts0)
    crit_ts = [
        ts for ts in order
        if any(t.criteria is not None for t in nights[ts])
    ]
    if crit_ts and not any(
        ts in crit_ts
        and any(t.criteria is not None for t in nights[ts])
        for ts in picked
    ):
        # 保底：轨迹存在判据快照夜（四判据 promotion，criteria 非 None）则必须抽中
        # 一夜——硬门槛/Wilson/窗优势公式族的突变灵敏度挂在判据快照上（与 demotion
        # 保底同型；间隔冲突时让出最晚一夜，确定性置换）。
        ts0 = crit_ts[0]
        d0 = (_iso_dt(ts0) - SIM_T0).days
        if all(abs(d0 - (_iso_dt(p) - SIM_T0).days) >= 2 for p in picked):
            picked.append(ts0)
        elif picked:
            picked.pop()
            picked.append(ts0)
    for ts in order:                                   # 放宽间隔补足
        if len(picked) >= want:
            break
        if ts not in picked:
            picked.append(ts)
    picked.sort()                                      # ISO 串字典序 = 时间序

    cases = []
    for n, ts in enumerate(picked, 1):
        day = next(d for d, v in night_ts_by_day.items() if v == ts)
        trs = sorted(nights[ts], key=lambda t: t.seq)
        script = [
            {
                "agent_id": t.agent_id, "role": str(t.role), "node": t.node,
                "kind": "promo",
                "passed": t.reason == "exam_pass",
                "exam_id": t.exam_id,
            }
            for t in trs if t.reason in ("exam_pass", "exam_fail")
        ] + [
            {
                # 转换⑥ 周期复核熄灭（M0 定稿 v2，demote.FC_DAYS>0）：decay 流转
                # 由 for_cause 回调驱动，重放按轨迹结局脚本化（decayed 恒 True；
                # 未熄灭的复核考不产生流转，重放回调返回 None 与「未传唤/未熄灭」
                # 对该夜流转面等价）。
                "agent_id": t.agent_id, "role": str(t.role), "node": t.node,
                "kind": "fc",
                "decayed": True,
                "exam_id": t.exam_id,
            }
            for t in trs if t.kind == "demotion" and t.reason == "decay"
        ]
        cases.append({
            "case_id": f"promote-{n:03d}",
            "kind": "promote_scan",
            "slice": f"night-{day:02d}",
            "meta": {
                "seed": seed,
                "trajectory_digest": digest,
                "day": day,
                "night_as_of": ts,
                "window_days": WINDOW_DAYS,
                "n_transitions": len(trs),
                "reasons": sorted({str(t.reason) for t in trs}),
                "note": "expected=该夜 promote_scan 的全部状态流转（剔 seq；shadow→candidate "
                        "附四判据快照数值）；考试回调按 exam_script 脚本化（kind=promo=暗考 "
                        "passed / kind=fc=周期复核 decayed），未命中格返回 None"
                        "（与原轨迹回调未触发一致）",
            },
            "params": params_doc,
            "inputs": {
                "night_as_of": ts,
                "window_days": WINDOW_DAYS,
                "exam_script": script,
            },
            "expected": {"transitions": [
                {
                    "agent_id": t.agent_id, "role": str(t.role), "node": t.node,
                    "from_status": t.from_status, "to_status": t.to_status,
                    "reason": t.reason, "kind": t.kind, "exam_id": t.exam_id,
                    "criteria": None if t.criteria is None else {
                        "n_eff": t.criteria.n_eff,
                        "n_eff_s2s3": t.criteria.n_eff_s2s3,
                        "hard_ok": t.criteria.hard_ok,
                        "wilson_lb": t.criteria.wilson_lb,
                        "baseline": t.criteria.baseline,
                        "advantage_ok": t.criteria.advantage_ok,
                        "traffic": t.criteria.traffic,
                        "traffic_ok": t.criteria.traffic_ok,
                        "gate_ok": t.criteria.gate_ok,
                    },
                }
                for t in trs
            ]},
        })
    return cases


def sample_agg_cases(traj: dict, digest: str, seed: int, params_doc: dict,
                     days: list[int], night_days: list[int]) -> list[dict]:
    """agg 用例：指定日早晨视图 + 一个夜锚点收尾视图（含该夜流转的 status 重放）。"""
    traj["res"].cells_count                            # 触感检查：轨迹字段在位
    plan: list[tuple[str, str, dict]] = []
    for day in days:
        as_of = traj["day_as_of"](day)
        if as_of is not None:
            plan.append((f"day-{day:02d}", "day", {"day": day, "as_of": as_of}))
    # 夜锚点：取已抽 promote 夜中转折最多的一夜；切片含该夜自身事件（ts <= 锚点）
    if night_days:
        day = max(night_days, key=lambda d: len(traj["nights"].get(
            traj["night_ts_by_day"].get(d, ""), ())))
        ts = traj["night_ts_by_day"].get(day)
        if ts is not None:
            plan.append((f"nightend-{day:02d}", "nightend",
                         {"day": day, "as_of": ts}))

    cases = []
    for n, (slice_id, kind, info) in enumerate(plan, 1):
        cases.append({
            "case_id": f"agg-{n:03d}",
            "kind": "agg_cells",
            "slice": slice_id,
            "meta": {
                "seed": seed,
                "trajectory_digest": digest,
                "day": info["day"],
                "as_of": info["as_of"],
                "anchor": "night" if kind == "nightend" else "day",
                "note": "expected=agg_cells 全格表（pos/neg/n_eff/n_eff_s2s3/status/"
                        "activated_at/updated_at）+ 全格探针 c_raw/c_star/LOO baseline_mean；"
                        "nightend 锚点含该夜流转的 status 重放（同 ts 事件并入）",
            },
            "params": params_doc,
            "inputs": {"slice_as_of": info["as_of"]},
            "expected": None,                      # 由 assemble 阶段经 loader 语义回填
        })
    return cases


# ── 切片构建（前缀设计：全部切片都是同一机制事件序列的前缀）─────────


def build_event_log(traj: dict) -> dict:
    """全部机制事件（deposit/promotion/demotion，seq 序）的单份紧凑日志。

    SimClock 时钟沿 append 序严格递增 ⇒ 任意 as_of 过滤都是该序列的前缀；
    切片文件只存前缀长度 n_events + as_of，事件本体全仓库仅此一份。
    """
    t0 = SIM_T0
    return {
        "t0": iso_utc(t0),
        "n_events": len(traj["mech"]),
        "note": "机制事件全序日志（账本 seq 序，ts 沿 SimClock 严格递增）；"
                "切片 = 本日志前 n_events 行。字段序见 golden_loader（紧凑编码）。",
        "events": [_encode_event_row(e, t0) for e in traj["mech"]],
    }


def build_slices(traj: dict, slice_specs: dict[str, tuple[str, str]]) -> dict[str, dict]:
    """slice_specs: slice_id → ('day'|'night'|'nightend', as_of_ts)。返回切片元数据。"""
    slices: dict[str, dict] = {}
    for slice_id, (kind, as_of_ts) in sorted(slice_specs.items()):
        as_of_dt = _iso_dt(as_of_ts)
        if kind == "night":
            n = sum(1 for e in traj["mech"] if _iso_dt(e.ts) < as_of_dt)
        else:                                          # day / nightend：ts <= as_of
            n = sum(1 for e in traj["mech"] if _iso_dt(e.ts) <= as_of_dt)
        # 前缀性质自证：切片边界之后第一行的 ts 不得早于 as_of
        # （night 切片边界恰为该夜事件自身：同 ts，故用 >=；day/nightend 用 >）
        for e in traj["mech"][n:n + 1]:
            ok = (_iso_dt(e.ts) >= as_of_dt) if kind == "night" else (_iso_dt(e.ts) > as_of_dt)
            if not ok:
                raise AssertionError(f"切片 {slice_id} 前缀性质破坏 @n={n}")
        slices[slice_id] = {
            "slice_id": slice_id,
            "anchor": kind,
            "as_of": as_of_ts,
            "n_events": n,
            "note": "输入账本片段 = events_log.json 前 n_events 行（ts ≤/< as_of："
                    "night 为严格小于，day/nightend 为 ≤）；装载器见 tests/golden_loader.py",
        }
    return slices


# ── 组装 + 自检 ─────────────────────────────────────────────────


def build_fixture_tree(seed: int, n_days: int, *, n_route: int = 9, n_promote: int = 8,
                       agg_days: tuple[int, ...] = (2, 5, 9, 14, 20, 25, 29)
                       ) -> dict[str, bytes]:
    traj = build_trajectory(seed, n_days)
    res = traj["res"]
    digest = "sha256:" + res.digest
    params_doc = _jsonable(res.params.model_dump())

    route_cases = sample_route_cases(traj, n_route, digest, seed, params_doc)
    ts_cases = sample_route_thompson_cases(traj, route_cases, digest, seed, params_doc)
    promote_cases = sample_promote_cases(traj, n_promote, digest, seed, params_doc)
    promote_night_days = [c["meta"]["day"] for c in promote_cases]
    agg_cases = sample_agg_cases(traj, digest, seed, params_doc,
                                 [d for d in agg_days if d < n_days], promote_night_days)

    # 切片需求：route/agg 的日切片 + promote 夜切片（ts < 锚点）+ nightend（ts <= 锚点）
    slice_specs: dict[str, tuple[str, str]] = {}
    for c in route_cases + [a for a in agg_cases if a["meta"]["anchor"] == "day"]:
        slice_specs[c["slice"]] = ("day", c["inputs"]["slice_as_of"])
    for c in promote_cases:
        slice_specs[c["slice"]] = ("night", c["inputs"]["night_as_of"])
    for c in agg_cases:
        if c["meta"]["anchor"] == "night":
            slice_specs[c["slice"]] = ("nightend", c["inputs"]["slice_as_of"])
    log_doc = build_event_log(traj)
    slices = build_slices(traj, slice_specs)

    # 切片展开（内存态，供落盘前自检）：元数据 + 日志前缀
    expanded = {
        sid: {**meta, "t0": log_doc["t0"], "events": log_doc["events"][:meta["n_events"]]}
        for sid, meta in slices.items()
    }

    manifest = {
        "schema_version": 1,
        "spec": "SPEC-4 §4 输出物3：内核黄金用例回归集（route/promote_scan/agg_cells "
                "各≥6 + route_thompson TS 对照臂，从仿真轨迹抽样；此后内核实现变更"
                "以本集为回归门）",
        "generated_by": f"scripts/freeze_golden.py --seed {seed} --n-days {n_days}",
        "regen_command": f"python3 scripts/freeze_golden.py --seed {seed} --n-days {n_days}",
        "verify_command": f"python3 scripts/freeze_golden.py --seed {seed} --n-days {n_days} --check",
        "float_policy": "exact（JSON repr 往返逐位保真；同一平台回归门为逐位比对，"
                        "跨平台若 libm 浮点有差请按 regen_command 重冻）",
        "seq_policy": "片段重放按片段序重编 seq，故期望不含 seq；Transition.seq 与 "
                      "cells.last_event_seq 不参与比对（账本记账产物，非机制公式）",
        "ts_policy": "SimClock 整秒时刻编码为 (day, i)：ts = T0 + day·86400s + i·1s；"
                     "片段耗尽后 FragmentClock 回落 as_of（夜批追加事件=夜锚点，与原一致）",
        "trajectory": {
            "seed": seed,
            "n_days": n_days,
            "run_id": f"sim-s{seed}-d{n_days}",
            "router": "kernel.route",
            "ts_arm": "kernel.route_thompson（对照臂用例与 route 用例同切片同 rng 偏移，"
                      "expected=确定性重放；轨迹派单本身仍由 kernel.route 生成）",
            "replay_every": 0,
            "promote_window_days": WINDOW_DAYS,
            "params_base": "configs/params.yaml",
            "taxonomy": "configs/taxonomy.yaml",
            "overrides": {},
            "stream": _jsonable(asdict(res.stream)),
            "verdict": _jsonable(asdict(res.verdict)),
            "exam": _jsonable(asdict(res.exam)),
            "hidden": _jsonable(asdict(res.hidden)),
            "route_seed": derive_seed(seed, "engine", "route"),
            "route_seed_labels": ["engine", "route"],
            "digest": digest,
            "n_tasks": len(res.tasks),
            "n_events": len(res.events),
            "transitions_total": len(res.transitions),
            "cells_total": res.cells_count,
        },
        "params": params_doc,
        "counts": {
            "route": len(route_cases),
            "route_thompson": len(ts_cases),
            "promote_scan": len(promote_cases),
            "agg_cells": len(agg_cases),
            "total": len(route_cases) + len(ts_cases) + len(promote_cases)
                     + len(agg_cases),
        },
    }

    # ── 期望值回填（冻结 = 自洽）：agg / TS 均经 loader 的同一重放语义计算 ──
    for c in agg_cases:
        p = ParamsConfig.model_validate(c["params"])
        led, as_of, view = gl.build_view(expanded[c["slice"]], params=p)
        try:
            c["expected"] = gl.agg_expected(view)
            c["meta"]["n_cells"] = len(c["expected"]["cells"])
        finally:
            led.close()
    for c in ts_cases:
        p = ParamsConfig.model_validate(c["params"])
        c["expected"] = gl.execute_case(c, manifest=manifest, params=p,
                                        slices=expanded)

    # ── 落盘前自检：每条用例在当前内核上重放并精确比对（内存切片，未写盘）──
    docs: dict[str, bytes] = {"manifest.json": _dump_bytes(manifest)}
    docs["slices/events_log.json"] = _dump_bytes(log_doc)
    for sid, meta in slices.items():
        docs[f"slices/slice-{sid}.json"] = _dump_bytes(meta)
    for c in route_cases + ts_cases + promote_cases + agg_cases:
        diffs = gl.check_case(c, manifest=manifest,
                              params=ParamsConfig.model_validate(c["params"]),
                              slices=expanded)
        if diffs:
            raise AssertionError(
                f"自检失败 {c['case_id']}（不产出坏夹具）：\n" + "\n".join(diffs[:20]))
        docs[f"{c['kind']}/{c['case_id']}.json"] = _dump_bytes(c)
    return docs


# ── 落盘 / 校验 ─────────────────────────────────────────────────


def write_tree(docs: dict[str, bytes], out_dir: Path) -> None:
    (out_dir / "slices").mkdir(parents=True, exist_ok=True)
    for kind in gl.KINDS:
        (out_dir / kind).mkdir(parents=True, exist_ok=True)
    stale: list[Path] = list(out_dir.glob("slices/slice-*.json"))
    for kind in gl.KINDS:
        stale.extend(out_dir.glob(f"{kind}/case-*.json"))
    for p in stale:
        p.unlink()
    for rel, data in docs.items():
        (out_dir / rel).write_bytes(data)


def check_tree(docs: dict[str, bytes], out_dir: Path) -> list[str]:
    problems: list[str] = []
    for rel, data in sorted(docs.items()):
        p = out_dir / rel
        if not p.exists():
            problems.append(f"缺失：{rel}")
        elif p.read_bytes() != data:
            problems.append(f"字节不一致：{rel}")
    # 多余文件只在本工具的属树上扫（manifest + slices + KINDS 用例目录）；
    # M1 合成类（budget/dispatch/re_route/cluster，regen_m1_commands 自有冻结口）
    # 不由本命令再生成，不得当作本树的多余项误报。
    owned: set[str] = {"manifest.json"}
    for p in sorted(out_dir.rglob("*.json")):
        rel = p.relative_to(out_dir).as_posix()
        top = rel.split("/", 1)[0]
        if top == "slices" or top in gl.KINDS:
            owned.add(rel)
    for rel in sorted(owned):
        if rel not in docs:
            problems.append(f"多余文件：{rel}")
    return problems




# ── cluster 黄金用例（M1：kernel.cluster 公式族；--cluster 模式）──
#
# 与 M0 夹具树完全隔离：只写 tests/golden/cluster/{manifest,case-*.json}，
# 不触碰 M0 四类夹具与共享切片（M0 基线 sha256 逐位不变）。
#
# 场景（确定性，全部经 kernel.cluster 复合函数落账，SteppedClock 1s 步进）：
# 检查点 1 建簇/扩簇/幂等 + sim 阈值边界；检查点 2 拆分 + S2/S1 传播与冲突 +
# 忽略态；检查点 3 崩溃窗修复 + 谱系缺失跳过；检查点 4 守卫负用例（终态再裁/
# 票数不足/源 kind 错配/拆分并集）。每检查点一个 case（前缀自含），expected 钉
# 事件流（剔 seq）+ 三投影 + cells 全格表。

_CLUSTER_FINDINGS = [
    # 建簇面：a.py 三条同 CWE 重叠窗（含一条 12-18 边界）+ 一条异 CWE
    {"id": "F1", "path": "a.py", "line_start": 10, "line_end": 20, "reporter": "A1"},
    {"id": "F2", "path": "a.py", "line_start": 12, "line_end": 18, "reporter": "A2"},
    {"id": "F3", "path": "a.py", "line_start": 15, "line_end": 40, "cwe": "CWE-89",
     "reporter": "A3"},
    {"id": "F4", "path": "a.py", "line_start": 100, "line_end": 110, "reporter": "A4"},
    # 家族级（CWE 双 None）可并/不可并对照
    {"id": "F5", "path": "b.py", "line_start": 5, "line_end": 9, "cwe": None,
     "family": "deser", "type": None, "reporter": "A5"},
    {"id": "F6", "path": "b.py", "line_start": 6, "line_end": 8, "cwe": None,
     "family": "deser", "type": None, "reporter": "A6"},
    {"id": "F7", "path": "b.py", "line_start": 7, "line_end": 9, "cwe": None,
     "family": "deser", "type": "variant", "reporter": "A7"},
    # 精聚类 sim 阈值对照：0.9（含等号，并入）/ 0.5（拒绝）
    {"id": "F8", "path": "c.py", "line_start": 1, "line_end": 5, "cwe": "CWE-200",
     "reporter": "A8"},
    {"id": "F9", "path": "c.py", "line_start": 3, "line_end": 9, "cwe": "CWE-200",
     "reporter": "A9"},
    {"id": "FA", "path": "d.py", "line_start": 1, "line_end": 5, "cwe": "CWE-89",
     "reporter": "AA"},
    {"id": "FB", "path": "d.py", "line_start": 3, "line_end": 9, "cwe": "CWE-89",
     "reporter": "AB"},
]


def _cluster_ops_by_checkpoint() -> list[list[dict]]:
    """检查点 → 该段 op 序列（case i 的 inputs.ops = 前缀拼接）。

    建簇 id 序（确定性）：C1={F1,F2}(fine, sim=0.9) / C2={F5,F6}(fine) /
    C3={F8,F9}(fine) / C4={FA,FB}(fine)；随后 C1 扩簇并入 F4；检查点 2 拆分为
    C5={F1,F2} / C6={F4}；检查点 3 崩溃窗修复 + 忽略态。
    """
    ctx_c1 = {"members": [["F1", "A1", "auditor"], ["F2", "A2", "auditor"]],
              "scouts": ["S1"], "votes": [["R1", 1], ["R2", 1], ["R3", -1]],
              "verifier_id": "V9"}
    ctx_c1b = {"members": [["F1", "A1", "auditor"], ["F2", "A2", "auditor"]],
               "verifier_id": "V9"}
    ctx_c2 = {"members": [["F5", "A5", "auditor"], ["F6", "A6", "auditor"]],
              "verifier_id": "V9"}
    ctx_c6 = {"members": [["F4", "A4", "auditor"]]}
    return [
        # ── 检查点 1：两级判据 + sim 阈值边界 + 建簇/扩簇/幂等 + 守卫 ──
        [
            {"op": "find_and_merge", "level": "fine",
             "sim": {"kind": "const", "value": 0.89}},          # 阈值下拒绝（宁漏勿错）
            {"op": "find_and_merge", "level": "fine",
             "sim": {"kind": "const", "value": 0.9}},           # 含等号 → 4 簇
            {"op": "find_and_merge", "level": "coarse",
             "assigned": {"F1": "C-20310304-000001", "F2": "C-20310304-000001",
                          "F5": "C-20310304-000002", "F6": "C-20310304-000002",
                          "F8": "C-20310304-000003", "F9": "C-20310304-000003",
                          "FA": "C-20310304-000004", "FB": "C-20310304-000004"}},
            # 已归属不参与（assigned 工作流：跨级别重扫防重复建簇）；余量全单例 → 零提案
            {"op": "find_and_merge", "level": "fine"},          # sim 缺省 None（D4）→ 零提案
            {"op": "apply_merges", "proposals": [               # 扩簇：F4 并入 C1（同 level）
                {"level": "fine", "member_ids": ["F1", "F2", "F4"],
                 "key_path": "a.py", "key_cwe": "CWE-79", "key_lang": "py",
                 "line_window": [10, 110], "sim_min": None}]},
            {"op": "apply_merges", "proposals": [               # 幂等重放：零新事件
                {"level": "fine", "member_ids": ["F1", "F2", "F4"],
                 "key_path": "a.py", "key_cwe": "CWE-79", "key_lang": "py",
                 "line_window": [10, 110], "sim_min": None}]},
            {"op": "expect_error", "match": "部分重叠", "call": {  # 提案 ⊊ 既有簇 → 保守拒绝
                "op": "apply_merges", "proposals": [
                    {"level": "fine", "member_ids": ["F1"],
                     "key_path": "a.py", "key_cwe": "CWE-79", "key_lang": "py",
                     "line_window": [10, 20], "sim_min": None}]}},
        ],
        # ── 检查点 2：S2 传播 + panel 收口 S1 + S3 冲突补记 + 拆分 ──
        [
            {"op": "seed_votes", "finding_id": "F1", "votes": [
                {"reviewer": "R1", "direction": 1, "dims": [0.8, 0.8, 0.8, 0.8]},
                {"reviewer": "R2", "direction": 1, "dims": [0.6, 0.6, 0.6, 0.6]},
                {"reviewer": "R3", "direction": -1, "dims": [0.4, 0.4, 0.4, 0.4]}]},
            {"op": "propagate", "cluster_id": "C-20310304-000001", "tier": "S2",
             "direction": 1, "source": {"kind": "verify_result",
             "payload": {"cluster_id": "C-20310304-000001", "outcome": "verified_real"}},
             "ctx": ctx_c1},
            {"op": "close_panel", "finding_id": "F1", "expected_votes": 3, "ctx": ctx_c1},
            {"op": "human_verdict", "cluster_id": "C-20310304-000001", "direction": -1,
             "actor": "human", "ctx": ctx_c1b},
            {"op": "split_cluster", "cluster_id": "C-20310304-000001",
             "groups": [["F2", "F1"], ["F4"]], "actor": "human"},
        ],
        # ── 检查点 3：崩溃窗修复 + 谱系缺失跳过 + 忽略态 ──
        [
            {"op": "append", "kind": "verify_result",
             "payload": {"cluster_id": "C-20310304-000002", "outcome": "verified_real"}},
            {"op": "append", "kind": "verify_result",
             "payload": {"cluster_id": "C-PLACEHOLDER", "outcome": "verified_real"}},
            {"op": "propagate_pending", "lineage": {
                "C-20310304-000002": ctx_c2,
                "C-PLACEHOLDER": None}},                         # None → skipped 不报错
            {"op": "human_verdict", "cluster_id": "C-20310304-000004",
             "direction": 0, "actor": "human",
             "ctx": {"members": [["FA", "AA", "auditor"], ["FB", "AB", "auditor"]]}},
            # 忽略（direction=0）：落事件、零存款、零状态变更（C4 成员仍 open）
        ],
        # ── 检查点 4：守卫负用例（终态/票数不足/源 kind 错配/拆分并集）──
        [
            {"op": "expect_error", "match": "终态", "call": {
                "op": "human_verdict", "cluster_id": "C-20310304-000006",
                "direction": 0, "actor": "human",
                "ctx": {"members": [["F4", "A4", "auditor"]]}}},
            {"op": "expect_error", "match": "票数不足", "call": {
                "op": "close_panel", "finding_id": "F5", "expected_votes": 3,
                "ctx": {"votes": [["R1", 1]]}}},
            {"op": "expect_error", "match": "kind", "call": {
                "op": "propagate", "cluster_id": "C-20310304-000002", "tier": "S1",
                "direction": 1, "declared_kind": "vote",
                "source": {"kind": "verify_result",
                "payload": {"cluster_id": "C-20310304-000002", "outcome": "verified_real"}},
                "ctx": ctx_c2}},
            {"op": "expect_error", "match": "并集", "call": {
                "op": "split_cluster", "cluster_id": "C-20310304-000002",
                "groups": [["F5"], ["FA"]], "actor": "human"}},
        ],
    ]


def _cluster_case_inputs(ops_prefix: list[dict]) -> dict:
    return {"t0": "2031-03-04T00:00:00Z", "findings": _CLUSTER_FINDINGS,
            "ops": ops_prefix}


def build_cluster_fixtures() -> dict[str, bytes]:
    """冻结 kernel.cluster 黄金用例（确定性；落盘前逐条自检）。"""
    import golden_loader as gl

    checkpoints = _cluster_ops_by_checkpoint()
    manifest = {
        "schema_version": 1,
        "spec": "SPEC-2 §1 / SPEC-5 §4.3：kernel.cluster 黄金用例（建簇/扩簇/拆分、"
                "S1/S2/S3 归属传播、幂等键、守卫负用例；M1 夹具树独立于 M0 基线）",
        "generated_by": "scripts/freeze_golden.py --cluster",
        "regen_command": "python3 scripts/freeze_golden.py --cluster",
        "verify_command": "python3 scripts/freeze_golden.py --cluster --check",
        "float_policy": "exact（JSON repr 往返逐位保真；同平台逐位比对）",
        "seq_policy": "期望事件剔 seq（账本记账产物）；ts 由 SteppedClock(t0,1s) 确定性签发",
        "ts_policy": "T0=2031-03-04T00:00:00Z，每次 append 消耗 1s；种子行建档同事务"
                     "携带 finding(open) 事件（与 dispatch.submit 同纪律）",
        "params_base": "configs/params.yaml",
        "params": _jsonable(load_params().model_dump()),
        "counts": {"cluster": len(checkpoints), "total": len(checkpoints)},
    }
    docs: dict[str, bytes] = {"cluster/manifest.json": _dump_bytes(manifest)}
    prefix: list[dict] = []
    for i, segment in enumerate(checkpoints, 1):
        prefix = prefix + segment
        case = {
            "case_id": f"cluster-{i:03d}",
            "kind": "cluster",
            "meta": {
                "checkpoint": i,
                "note": "inputs=op 前缀（自含）；expected=事件流（剔 seq）+ clusters/"
                        "findings 投影 + cells 全格表，与重放执行逐位比对",
            },
            "params": _jsonable(load_params().model_dump()),
            "inputs": _cluster_case_inputs(prefix),
            "expected": None,
        }
        actual = gl.execute_cluster_case(case, params=ParamsConfig.model_validate(case["params"]))
        case["expected"] = actual
        diffs = gl.check_cluster_case(case, params=ParamsConfig.model_validate(case["params"]))
        if diffs:
            raise AssertionError(f"cluster-{i:03d} 自检失败（不产出坏夹具）：\n" + "\n".join(diffs[:20]))
        docs[f"cluster/case-{i:03d}.json"] = _dump_bytes(case)
    return docs


def write_cluster_tree(docs: dict[str, bytes], out_dir: Path) -> None:
    """docs 键 = "cluster/<name>"（与 M0 树同构）；落盘根 = tests/golden/cluster。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in sorted(out_dir.glob("case-*.json")):
        p.unlink()
    for rel, data in docs.items():
        (out_dir / rel.split("/", 1)[1]).write_bytes(data)


def check_cluster_tree(docs: dict[str, bytes], out_dir: Path) -> list[str]:
    problems: list[str] = []
    for rel, data in sorted(docs.items()):
        p = out_dir / rel.split("/", 1)[1]
        if not p.exists():
            problems.append(f"缺失：{rel}")
        elif p.read_bytes() != data:
            problems.append(f"字节不一致：{rel}")
    for p in sorted(out_dir.rglob("*.json")):
        rel = "cluster/" + p.relative_to(out_dir).as_posix()
        if rel not in docs:
            problems.append(f"多余文件：{rel}")
    return problems

def _merge_m1_manifest(docs: dict[str, bytes], out_dir: Path) -> None:
    """把盘上 manifest 的 M1 合成类登记并入再生成结果（就地改 docs["manifest.json"]）。

    本工具只产 M0 轨迹树；M1 合成类（budget/dispatch/re_route）由
    freeze_golden_m1.py / freeze_budget_golden.py 冻结并在顶层 manifest 登记
    counts 与 regen_m1_commands（DECISIONS 2026-10-10 M1 黄金并轨行）。重冻 M0
    树不得抹掉这些登记——从盘上旧 manifest 原样并回，total 随之重算；盘上无旧
    manifest（首次冻结）时不动，由 M1 冻结口自行补记。
    """
    m_path = out_dir / "manifest.json"
    if not m_path.exists():
        return
    old = json.loads(m_path.read_text(encoding="utf-8"))
    m = json.loads(docs["manifest.json"])
    m1_counts = {
        k: old.get("counts", {}).get(k)
        for k in gl.M1_KINDS
        if isinstance(old.get("counts", {}).get(k), int)
    }
    if m1_counts:
        m["counts"].update(m1_counts)
        m["counts"]["total"] = sum(
            int(v) for k, v in m["counts"].items() if k != "total"
        )
    if "regen_m1_commands" in old:
        m["regen_m1_commands"] = dict(old["regen_m1_commands"])
    docs["manifest.json"] = _dump_bytes(m)


def main() -> int:
    ap = argparse.ArgumentParser(description="从仿真轨迹冻结内核黄金用例回归集（SPEC-4 §4-3）")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--n-days", type=int, default=30)
    ap.add_argument("--out", type=Path, default=ROOT / "tests" / "golden")
    ap.add_argument("--check", action="store_true",
                    help="重新生成并与盘上夹具逐字节比对（溯源再生成验证）")
    ap.add_argument("--cluster", action="store_true",
                    help="冻结 kernel.cluster 黄金用例（tests/golden/cluster/）；"
                         "不触碰 M0 夹具树")
    args = ap.parse_args()

    if args.cluster:
        out_dir = ROOT / "tests" / "golden" / "cluster"
        docs = build_cluster_fixtures()
        if args.check:
            problems = check_cluster_tree(docs, out_dir)
            for p in problems:
                print(f"[check] {p}")
            if problems:
                print("[check] FAIL：cluster 夹具与再生成结果不一致")
                return 1
            print(f"[check] OK：{len(docs)} 个 cluster 文件逐字节一致")
            return 0
        write_cluster_tree(docs, out_dir)
        print(f"[freeze] 已写入 {out_dir}（{len(docs)} 文件：manifest + "
              f"{len(docs) - 1} case）")
        print("[freeze] 再生成验证：python3 scripts/freeze_golden.py --cluster --check")
        return 0

    print(f"[freeze] engine.run(seed={args.seed}, n_days={args.n_days}) …")
    docs = build_fixture_tree(args.seed, args.n_days)
    _merge_m1_manifest(docs, args.out)
    by_kind = {k: sum(1 for f in docs if f.startswith(k + "/"))
               for k in (*gl.KINDS, "slices")}
    total_kib = sum(len(v) for v in docs.values()) / 1024
    print(f"[freeze] 生成 {len(docs)} 个文件共 {total_kib:.0f} KiB："
          f"route={by_kind['route']} route_thompson={by_kind['route_thompson']} "
          f"promote_scan={by_kind['promote_scan']} "
          f"agg_cells={by_kind['agg_cells']} slices={by_kind['slices']}")

    if args.check:
        problems = check_tree(docs, args.out)
        for p in problems:
            print(f"[check] {p}")
        if problems:
            print("[check] FAIL：盘上夹具与再生成结果不一致")
            return 1
        print(f"[check] OK：{len(docs)} 个文件逐字节一致（seed={args.seed} 可溯源再生成）")
        return 0

    write_tree(docs, args.out)
    print(f"[freeze] 已写入 {args.out}")
    print("[freeze] 再生成验证：python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
