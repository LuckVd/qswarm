"""golden_loader：黄金用例夹具的装载与重放（SPEC-4 §4-3 内核回归门的执行本体）。

夹具布局（由 scripts/freeze_golden.py 从仿真轨迹确定性冻结，见 tests/golden/README.md）::

    tests/golden/
      manifest.json                 轨迹出处（seed/n_days/digest/参数/再生成命令）
      slices/slice-*.json           共享账本片段（deposit/promotion/demotion 紧凑编码）
      route/case-*.json             route 决策黄金用例（默认臂，轨迹派单策略）
      route_thompson/case-*.json    TS 对照臂黄金用例（同切片同 rng 换路由器）
      promote_scan/case-*.json      promote_scan 状态流转黄金用例
      agg_cells/case-*.json         agg_cells 数值黄金用例
      budget/budget-*.json          M1 预算熔断合成规格用例（SPEC-2 §4 逐字输入）
      dispatch/dispatch-*.json      M1 委托契约/派单与结论状态机合成规格用例（SPEC-2 §2）
      re_route/re_route-*.json      M1 re_route/猜测格弱存款合成规格用例（SPEC-2 §3）
                                    （budget/dispatch/re_route 皆合成规格类：
                                    非轨迹抽样，origin=spec_literal）

重放语义（与 engine/promote_scan 的调用口径逐位一致）：

- 账本片段只含机制相关事件（deposit / promotion / demotion）；dispatch、exam_result
  等不被 agg_cells/_Index 读取的 kind 不入夹具。seq 在重放账本中按片段顺序重新编号，
  因此期望输出**不含任何 seq**（cells.last_event_seq、Transition.seq 不参与比对——
  这两处是账本记账产物，机制公式变更不会只体现在它们上，见 tests/golden/README.md）。
- 时间戳：SimClock 的时间形如 ``T0 + day·86400s + i·1s``（整秒），夹具存 ``(day, i)``
  整数对；装载器用 :class:`FragmentClock` 按事件顺序回放这些时刻，片段耗尽后回落到
  切片 as_of——夜批 promote_scan 自己追加的事件恰取夜锚点时刻，与原轨迹一致。
- route 用例：``rng = random.Random(route_seed)`` 先快进 ``draws_before`` 次
  ``rng.random()``（Mersenne Twister 流与顺序消耗逐位一致），再以与 engine 完全
  相同的实参调用 ``kernel.route``；期望输出 = 轨迹回放出的完整 RouteDecision
  （冻结时已对全量真实派单逐条核对了 RoutingRecord 五字段）。
- route_thompson 用例：与配对 route 用例同切片/同任务/同候选池/同 rng 快进偏移，
  改调 ``kernel.route_thompson``（TS 对照臂，非轨迹派单策略）；expected = 该确定性
  重放的完整决策——覆盖 Beta 后验（β_prior=λ/4）与后验均值公式（冻结时由 loader
  同一语义回填，见 scripts/freeze_golden.py）。
- promote_scan 用例：考试回调脚本化——夹具按该夜轨迹记录每格暗考结局
  （pass/fail + exam_id）；脚本未命中的格子回调返回 None（= 原轨迹该回调未触发）。
  序列化比对剔除 seq，其余字段（含四判据快照数值）全量精确比对。
- agg_cells 用例：全格表（pos/neg/n_eff/n_eff_s2s3/status/activated_at/updated_at）
  + 全格探针（c_raw / c_star / LOO baseline_mean）逐值精确比对。

浮点比对口径：**逐位精确（==）**。同一平台同一代码路径下重放是逐位确定的；任何
公式改动（衰减、收缩、Wilson、TILT、门槛）都会至少破坏一个夹具——这正是
tests/test_golden_mutation_gate.py 的门禁。跨平台/跨 Python 版本若出现 libm 级
浮点差，按 tests/golden/README.md 的再生成流程重冻夹具。
"""

from __future__ import annotations

import json
import random

import pytest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from qswarm.contracts import ParamsConfig
from qswarm.kernel import KernelLedger, agg_cells, promote_scan, route, route_thompson
from qswarm.kernel.ledger import FixedClock, iso_utc

__all__ = [
    "ALL_KINDS",
    "GOLDEN_DIR",
    "KINDS",
    "M1_KINDS",
    "SYNTHETIC_KINDS",
    "FragmentClock",
    "agg_expected",
    "build_ledger",
    "build_view",
    "case_files",
    "check_case",
    "diff_doc",
    "execute_case",
    "iter_case_paths",
    "load_case",
    "load_manifest",
    "load_slice",
    "manifest_params",
]

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

#: 四类黄金用例（目录名 = kind 标识）。route_thompson 为 TS 对照臂：与 route 共用
#: 同一轨迹切片与 route rng 子流（非轨迹派单策略，expected = 确定性重放，见 README）。
KINDS = ("route", "route_thompson", "promote_scan", "agg_cells")

#: M1 新增黄金类（SPEC-5 §4.3）：**合成规格用例**（非轨迹抽样）——输入为规格字面
#: 值（SPEC-2 §2/§3/§4 逐字），expected = 当前内核在同一确定性执行语义下的输出，由
#: freeze 脚本落盘前自检。溯源记 ``meta.origin="spec_literal"``（轨迹 seed/digest
#: 断言不适用，见 test_golden_regression 的分类处理）；后续 cluster 等合成类滚动
#: 追加进本元组即可。
M1_KINDS = ("budget", "dispatch", "re_route")

#: 全量黄金类（回归与突变门的遍历口径）。
ALL_KINDS = KINDS + M1_KINDS

#: 非轨迹类（无切片依赖；provenance 断言走 spec_literal 分支）。
SYNTHETIC_KINDS = M1_KINDS

# 片段事件紧凑编码：[kind_code, day, i, *payload 字段…]；字段序固定如下。
_DEPOSIT_FIELDS = (
    "finding", "cluster", "agent", "role", "taxonomy_path",
    "signal_tier", "direction", "weight", "origin",
)
_PROMOTION_FIELDS = ("agent", "role", "node", "exam_id", "from_status", "to_status")
_DEMOTION_FIELDS = ("agent", "role", "node", "reason")
_FIELDS_BY_CODE = {"d": _DEPOSIT_FIELDS, "p": _PROMOTION_FIELDS, "m": _DEMOTION_FIELDS}
_KIND_BY_CODE = {"d": "deposit", "p": "promotion", "m": "demotion"}


# ── 夹具读取 ────────────────────────────────────────────────────

_LOG_CACHE: dict | None = None


def load_manifest() -> dict:
    return json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))


def load_event_log() -> dict:
    """共享机制事件日志（全部切片都是它的前缀；模块级缓存一份）。"""
    global _LOG_CACHE
    if _LOG_CACHE is None:
        _LOG_CACHE = json.loads(
            (GOLDEN_DIR / "slices" / "events_log.json").read_text(encoding="utf-8")
        )
    return _LOG_CACHE


def manifest_params(manifest: dict | None = None) -> ParamsConfig:
    m = manifest or load_manifest()
    return ParamsConfig.model_validate(m["params"])


def iter_case_paths(kind: str | None = None) -> list[Path]:
    kinds = ALL_KINDS if kind is None else (kind,)
    paths: list[Path] = []
    for k in kinds:
        paths.extend(sorted((GOLDEN_DIR / k).glob("*.json")))
    return paths


def case_files(kind: str) -> list[Path]:
    """兼容别名：按类取夹具路径（排序稳定，pytest id 稳定）。"""
    return iter_case_paths(kind)


def load_case(path: Path | str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_slice(slice_id: str) -> dict:
    """切片 = 共享事件日志的前 n_events 行 + as_of（前缀性质见 README/slice 元数据）。"""
    meta = json.loads(
        (GOLDEN_DIR / "slices" / f"slice-{slice_id}.json").read_text(encoding="utf-8")
    )
    log = load_event_log()
    events = log["events"][: int(meta["n_events"])]
    if len(events) != int(meta["n_events"]):
        raise ValueError(f"切片 {slice_id} 超出事件日志长度：{meta['n_events']}")
    return {
        "slice_id": slice_id,
        "anchor": meta.get("anchor"),
        "t0": log["t0"],
        "as_of": meta["as_of"],
        "n_events": int(meta["n_events"]),
        "events": events,
    }


# ── 时间戳编解码（SimClock：T0 + day·86400s + i·1s，整秒）────────

def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)


def _split_ts(t0: datetime, ts: str) -> tuple[int, int]:
    total = int((_parse_iso(ts) - t0).total_seconds())
    return divmod(total, 86400)


class FragmentClock:
    """按预设时刻序列回放的内核时钟；耗尽后恒返 ``tail``（夜批追加事件=夜锚点）。"""

    def __init__(self, stamps: list[datetime], tail: datetime) -> None:
        self._stamps = list(stamps)
        self._tail = tail
        self._i = 0

    def now(self) -> datetime:
        if self._i < len(self._stamps):
            ts = self._stamps[self._i]
            self._i += 1
            return ts
        return self._tail


# ── 片段 → 内存账本 ─────────────────────────────────────────────

def decode_events(slice_doc: dict) -> list[tuple[str, dict, datetime]]:
    """紧凑编码 → [(kind, payload, ts)，原 seq 序]。"""
    t0 = _parse_iso(slice_doc["t0"])
    out: list[tuple[str, dict, datetime]] = []
    for row in slice_doc["events"]:
        code, day, i, *vals = row
        fields = _FIELDS_BY_CODE[code]
        if len(vals) != len(fields):
            raise ValueError(f"切片事件字段数不符：{row[:3]}… 期望 {len(fields)} 列")
        payload = dict(zip(fields, vals))
        out.append((_KIND_BY_CODE[code], payload, t0 + timedelta(days=day, seconds=i)))
    return out


def build_ledger(
    slice_doc: dict, *, params: ParamsConfig
) -> tuple[KernelLedger, datetime]:
    """片段 → 内存账本（事件按原序落账、时钟逐事件回放）；返回 (ledger, as_of)。"""
    events = decode_events(slice_doc)
    as_of = _parse_iso(slice_doc["as_of"])
    clock = FragmentClock([ts for _, _, ts in events], as_of)
    led = KernelLedger(":memory:", clock=clock)
    try:
        for kind, payload, _ in events:      # 校验后落账（与内核写口同一入口）
            led.append(kind, payload)
    except Exception:
        led.close()
        raise
    return led, as_of


def build_view(slice_doc: dict, *, params: ParamsConfig):
    """片段 → (ledger, as_of, CellView)。调用方负责 close ledger。"""
    led, as_of = build_ledger(slice_doc, params=params)
    try:
        view = agg_cells(led, as_of, params=params)
    except Exception:
        led.close()
        raise
    return led, as_of, view


# ── 四类用例的执行（返回与 expected 同构的 dict）─────────────────

def _route_rng(manifest: dict, case: dict) -> random.Random:
    rng = random.Random(manifest["trajectory"]["route_seed"])
    for _ in range(int(case["meta"]["draws_before"])):
        rng.random()                          # 快进：与顺序消耗逐位等价
    return rng


def _jsonify(x):
    """tuple→list / StrEnum→str 递归规范化（与 JSON 往返后的期望结构同构）。"""
    if isinstance(x, dict):
        return {k: _jsonify(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonify(v) for v in x]
    if isinstance(x, str):
        return str(x)                                 # StrEnum → str
    return x


def _run_route(case: dict, manifest: dict, view, params: ParamsConfig) -> dict:
    task = SimpleNamespace(
        task_id=case["inputs"]["task"]["task_id"],
        role=case["inputs"]["task"]["role"],
        family=case["inputs"]["task"]["family"],
        type=case["inputs"]["task"]["type"],
    )
    decision = route(
        task, view, _route_rng(manifest, case), params,
        pool=list(case["inputs"]["pool"]),
    )
    return _jsonify(asdict(decision))


def _run_route_thompson(case: dict, manifest: dict, view, params: ParamsConfig) -> dict:
    """TS 对照臂：与配对 route 用例同切片/同任务/同池/同 rng 快进偏移，换路由器。"""
    task = SimpleNamespace(
        task_id=case["inputs"]["task"]["task_id"],
        role=case["inputs"]["task"]["role"],
        family=case["inputs"]["task"]["family"],
        type=case["inputs"]["task"]["type"],
    )
    decision = route_thompson(
        task, view, _route_rng(manifest, case), params,
        pool=list(case["inputs"]["pool"]),
    )
    return _jsonify(asdict(decision))


def _transition_doc(t) -> dict:
    snap = t.criteria
    return {
        "agent_id": t.agent_id,
        "role": t.role,
        "node": t.node,
        "from_status": t.from_status,
        "to_status": t.to_status,
        "reason": t.reason,
        "kind": t.kind,
        "exam_id": t.exam_id,
        "criteria": None if snap is None else {
            "n_eff": snap.n_eff,
            "n_eff_s2s3": snap.n_eff_s2s3,
            "hard_ok": snap.hard_ok,
            "wilson_lb": snap.wilson_lb,
            "baseline": snap.baseline,
            "advantage_ok": snap.advantage_ok,
            "traffic": snap.traffic,
            "traffic_ok": snap.traffic_ok,
            "gate_ok": snap.gate_ok,
        },
    }


def _run_promote(case: dict, led: KernelLedger, as_of: datetime, params: ParamsConfig) -> dict:
    entries = case["inputs"]["exam_script"]
    promo = {
        (e["agent_id"], e["role"], e["node"]): e
        for e in entries if e.get("kind", "promo") == "promo"
    }
    fc = {
        (e["agent_id"], e["role"], e["node"]): e
        for e in entries if e.get("kind") == "fc"
    }

    def exam(*, agent_id: str, role, node: str, from_status):
        entry = promo.get((agent_id, str(role), node))
        if entry is None:                     # 原轨迹该回调未触发（保持 candidate）
            return None
        return SimpleNamespace(passed=bool(entry["passed"]), exam_id=entry["exam_id"])

    def for_cause(**kwargs):
        entry = fc.get((kwargs["agent_id"], str(kwargs["role"]), kwargs["node"]))
        if entry is None:                     # 未传唤/未熄灭（该夜无 decay 流转）
            return None
        return SimpleNamespace(decayed=bool(entry["decayed"]), exam_id=entry["exam_id"])

    transitions = promote_scan(
        led, as_of, params=params, exam=exam,
        window_days=float(case["meta"]["window_days"]),
        for_cause=for_cause if fc else None,  # 旧夹具（无 fc 项）保持关闭语义
    )
    return {"transitions": [_transition_doc(t) for t in transitions]}


def _run_agg(view) -> dict:
    """CellView → {cells, probes} 期望结构（freeze 与测试共用同一聚合导出语义）。"""
    cells: dict[str, dict] = {}
    probes: dict[str, dict] = {}
    for key in sorted(view.cells):
        c = view.cells[key]
        kstr = f"{key.agent_id}|{key.role}|{key.node}"
        cells[kstr] = {
            "pos_w": c.pos_w,
            "neg_w": c.neg_w,
            "n_eff": c.n_eff,
            "n_eff_s2s3": c.n_eff_s2s3,
            "status": c.status,
            "activated_at": c.activated_at,
            "updated_at": c.updated_at,
        }
        probes[kstr] = {
            "c_raw": view.c_raw(key.agent_id, key.role, key.node),
            "c_star": view.c_star(key.agent_id, key.role, key.node),
            "baseline_mean": view.baseline_mean(
                key.role, key.node, exclude_agent=key.agent_id
            ),
        }
    return {"cells": cells, "probes": probes}


#: 公开别名：freeze 脚本复用同一导出语义生成 agg 期望值。
agg_expected = _run_agg


def _run_budget(case: dict, manifest: dict, params: ParamsConfig) -> dict:
    """budget 黄金用例执行（SPEC-2 §4 合成规格用例；M1_KINDS）。

    输入自含（inputs.setup + inputs.steps）：先建内存账本与 runs 行，再按步骤
    逐一调用 kernel.budget（**经模块属性动态解析**——突变门 monkeypatch 即生效），
    输出与期望同构：逐步输出 + 全部 SCALING 事件（不含 seq/ts，与 M0 seq 口径
    一致：账本记账产物不参与比对）+ runs 行终态。
    """
    import qswarm.kernel.budget as budget_mod

    from qswarm.contracts import LedgerKind
    from qswarm.kernel import BudgetPolicy

    setup = case["inputs"]["setup"]
    led = KernelLedger(":memory:", clock=FixedClock(_parse_iso(setup["clock"])))
    try:
        led.conn.execute(
            "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
            " status, started_at) VALUES (?, ?, 'golden://budget', ?, 0, 'running', ?)",
            (setup["run_id"], setup["mode"], setup["budget_cents"], setup["clock"]),
        )
        led.conn.commit()
        out_steps: list[dict] = []
        for st in case["inputs"]["steps"]:
            op = st["op"]
            if op == "on_cost":
                state = budget_mod.on_cost(
                    led, setup["run_id"], st["cents"],
                    policy=BudgetPolicy(**st["policy"]),
                    note=st.get("note", ""),
                )
                out_steps.append({"state": _jsonify(state.as_dict())})
            elif op == "policy_state":
                state = budget_mod.budget_state(
                    st["spent"], BudgetPolicy(**st["policy"]), params=params
                )
                out_steps.append({"state": _jsonify(state.as_dict())})
            elif op == "quota_l2":
                value = budget_mod.quota_l2(st["remaining"], st["cost"], cap=st["cap"])
                out_steps.append({"value": value})
            elif op == "quota_s2":
                value = budget_mod.quota_s2(st["survivors"], st["remaining"], st["cost"])
                out_steps.append({"value": value})
            elif op == "budget_for_mode":
                value = budget_mod.budget_for_mode(st["mode"], params)
                out_steps.append({"value": value})
            elif op == "s2_rank_key":
                key = budget_mod.s2_rank_key(
                    st["severity"], st.get("type_node"), st["confidence"],
                    run_cluster_counts=st.get("counts", {}),
                )
                out_steps.append({"value": _jsonify(list(key))})
            elif op == "batch_eligible":
                value = BudgetPolicy(**st["policy"]).batch_eligible
                out_steps.append({"value": value})
            else:
                raise ValueError(f"未知 budget 用例 op：{op!r}")
        scaling = [
            {"kind": str(ev.kind), "run_id": ev.run_id, "payload": ev.payload}
            for ev in led.read(kinds=(LedgerKind.SCALING,))
        ]
        row = led.conn.execute(
            "SELECT spent_cents, status FROM runs WHERE run_id = ?",
            (setup["run_id"],),
        ).fetchone()
        return {
            "steps": out_steps,
            "scaling_events": scaling,
            "run": {"spent_cents": row[0], "status": row[1]},
        }
    finally:
        led.close()


def _run_m1_ledger(case: dict) -> KernelLedger:
    """M1 合成用例建库：行播种（harness 装配面，写口审计只扫 qswarm/ 包）+ 前置事件。

    时钟取 ``inputs.clock``（缺省整秒 FixedClock，全部 ts 相等 → 弱存款零账龄，
    w_eff 逐位 = weight）；事件按输入序 append（seq 从 1 确定性编号）。
    """
    led = KernelLedger(":memory:", clock=FixedClock(_parse_iso(case["inputs"].get("clock", "2026-01-01T00:00:00Z"))))
    conn = led.conn
    for r in case["inputs"].get("runs", ()):
        conn.execute(
            "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents, status,"
            " started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                r["run_id"], r.get("mode", "full"),
                r.get("repo_url", "https://example.invalid/repo"),
                r.get("budget_cents", 600), r.get("spent_cents", 0),
                r.get("status", "running"), r.get("started_at", "2026-01-01T00:00:00Z"),
            ),
        )
    for t in case["inputs"].get("tasks", ()):
        conn.execute(
            "INSERT INTO tasks (task_id, run_id, role, node, shard_json, state, assignee,"
            " attempts, dispatched_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                t["task_id"], t["run_id"], t.get("role", "auditor"), t["node"],
                json.dumps(t.get("shard", {}), sort_keys=True, ensure_ascii=False),
                t.get("state", "queued"), t.get("assignee"), t.get("attempts", 0),
                t.get("dispatched_at"), t.get("created_at", "2026-01-01T00:00:00Z"),
            ),
        )
    for f in case["inputs"].get("findings", ()):
        conn.execute(
            "INSERT INTO findings (id, run_id, reporter, reporter_role, family, type, lang,"
            " repo, path, func, line_start, line_end, commit_sha, cluster_id, title, severity,"
            " cwe, confidence, grade, state, disagreement_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 0.5, ?, ?, NULL, ?, ?)",
            (
                f["id"], f["run_id"], f.get("reporter", "A-auditor-1"),
                f.get("reporter_role", "auditor"), f.get("family", "injection"),
                f.get("type"), f.get("lang", "py"), f.get("repo", "https://example.invalid/repo"),
                f.get("path", "src/app/db.py"), f.get("line_start", 10), f.get("line_end", 20),
                f.get("commit_sha", "0c0ffee"), f.get("cluster_id"), f.get("title", "golden finding"),
                f.get("severity", "high"), f.get("cwe", "CWE-89"), f.get("grade", "L1"),
                f.get("state", "open"), f.get("created_at", "2026-01-01T00:00:00Z"),
                f.get("updated_at", "2026-01-01T00:00:00Z"),
            ),
        )
    conn.commit()
    for ev in case["inputs"].get("events", ()):
        led.append(ev["kind"], ev["payload"], run_id=ev.get("run_id"))
    return led


def _m1_decision_of(d: dict):
    """夹具 decision 字面 → RouteDecision（dataclass 元组字段由 list 还原）。"""
    from qswarm.kernel.route import RouteDecision

    return RouteDecision(
        task_id=d["task_id"], role=d["role"], family=d["family"], node=d["node"],
        assignee=d.get("assignee"), channel=d.get("channel", "explore"),
        reason=d.get("reason", "no_specs"), tilt=d.get("tilt"), raw_tilt=d.get("raw_tilt"),
        specs=tuple(d.get("specs", ())), spec_c_stars=tuple(d.get("spec_c_stars", ())),
        uniform_pick=d.get("uniform_pick"),
    )


def _m1_run_op(led: KernelLedger, op: dict):
    """M1 合成用例单条 op（dispatch/re_route 共享词表）。

    **经模块属性动态解析**调用 kernel.dispatch / kernel.reroute——突变门对
    FINDING_TRANSITIONS / RE_ROUTE_WEAK_W 的 monkeypatch 即生效。ValueError 上抛由
    执行器捕获成错误面（守卫负用例的期望承载，SPEC-2 §6.4 写侧非法 → ValueError）。
    """
    import importlib

    # 包 __init__ 把同名函数再导出（dispatch 遮蔽子模块），必须走 sys.modules 取模块
    # 本体——突变门 monkeypatch 才能经模块属性生效（同 _kernel_module 纪律）。
    dispatch_mod = importlib.import_module("qswarm.kernel.dispatch")
    reroute_mod = importlib.import_module("qswarm.kernel.reroute")

    from qswarm.contracts import DispatchContract, ScoutReport

    name, args = op["op"], op.get("args", {})
    if name == "create_task":
        return dispatch_mod.create_task(
            led, run_id=args["run_id"], role=args.get("role", "auditor"),
            node=args["node"], shard=args.get("shard", {}),
        )
    if name == "build_contract":
        contract = dispatch_mod.build_contract(
            SimpleNamespace(**args.get("task", {})), _m1_decision_of(args["decision"]),
            mode=args["mode"], deadline_s=args["deadline_s"],
            node=args.get("node"), goal=args.get("goal"),
            task_bounds=args.get("task_bounds"), context_refs=tuple(args.get("context_refs", ())),
        )
        return _jsonify(contract.model_dump())
    if name == "dispatch":
        ev = dispatch_mod.dispatch(
            led, args["task_id"], DispatchContract(**args["contract"]),
            _m1_decision_of(args["decision"]),
        )
        return {"kind": str(ev.kind), "payload": _jsonify(ev.payload)}
    if name == "re_dispatch":
        ev = dispatch_mod.re_dispatch(
            led, args["task_id"], to_agent=args["to_agent"], reason=args["reason"]
        )
        return {"kind": str(ev.kind), "payload": _jsonify(ev.payload)}
    if name == "transition_finding":
        ev = dispatch_mod.transition_finding(led, args["finding_id"], args["to_state"])
        return {"kind": str(ev.kind), "payload": _jsonify(ev.payload)}
    if name == "expire_run":
        events = dispatch_mod.expire_run(
            led, args["run_id"], reason=args.get("reason", "budget_breaker")
        )
        return [{"kind": str(e.kind), "payload": _jsonify(e.payload)} for e in events]
    if name == "re_route_finding":
        events = reroute_mod.re_route_finding(
            led, args["finding_id"], from_node=args["from_node"], to_node=args["to_node"],
            scout_ids=tuple(args.get("scout_ids", ())), reason=args.get("reason", "type_mismatch"),
        )
        return [{"kind": str(e.kind), "payload": _jsonify(e.payload)} for e in events]
    if name == "guess_taxonomy":
        reports = [ScoutReport(**r) for r in args["reports"]]
        view = agg_cells(led, led._clock.now())
        path = reroute_mod.guess_taxonomy(
            reports, view, scout_of=args.get("scout_of")
        )
        return list(path) if path is not None else None
    if name == "agg_view":
        view = agg_cells(led, led._clock.now())
        cells = {}
        for key in sorted(view.cells):
            c = view.cells[key]
            cells[f"{key.agent_id}|{key.role}|{key.node}"] = {
                "pos_w": c.pos_w, "neg_w": c.neg_w, "n_eff": c.n_eff,
                "n_eff_s2s3": c.n_eff_s2s3, "status": c.status, "activated_at": c.activated_at,
            }
        return {"cells": cells}
    if name == "rebuild_check":
        view = agg_cells(led, led._clock.now())
        from qswarm.kernel import diff_cells, rebuild_cells

        diffs = diff_cells(view, rebuild_cells(led, None))
        return {"n_diff": len(diffs), "n_cells": len(view.cells)}
    raise ValueError(f"未知 M1 用例 op：{name!r}")


def _m1_tables_doc(led: KernelLedger) -> dict:
    """收尾表投影：tasks 状态三列 / findings 状态（字典序，确定性）。"""
    tasks = {
        tid: {"state": state, "assignee": assignee, "attempts": attempts}
        for tid, state, assignee, attempts in led.conn.execute(
            "SELECT task_id, state, assignee, attempts FROM tasks ORDER BY task_id"
        )
    }
    findings = {
        fid: state
        for fid, state in led.conn.execute("SELECT id, state FROM findings ORDER BY id")
    }
    return {"tasks": tasks, "findings": findings}


def _run_m1_case(case: dict, manifest: dict, params: ParamsConfig) -> dict:
    """M1 dispatch/re_route 合成用例执行（SPEC-2 §2/§3；M1_KINDS）。

    输入自含（inputs 建库 + ops 操作序）；输出与期望同构：逐操作结果（成功值或
    守卫错误面）+ 收尾表投影 + 全账本 deposit 计数（零存款负断言的黄金形态）。
    """
    led = _run_m1_ledger(case)
    try:
        ops_doc = []
        for op in case["ops"]:
            try:
                ops_doc.append({"ok": True, "value": _m1_run_op(led, op)})
            except ValueError as exc:
                ops_doc.append(
                    {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
                )
        return {
            "ops": ops_doc,
            "tables": _m1_tables_doc(led),
            "deposit_count": len(led.read(kinds=("deposit",))),
        }
    finally:
        led.close()


def execute_case(
    case: dict,
    *,
    manifest: dict | None = None,
    params: ParamsConfig | None = None,
    slices: dict[str, dict] | None = None,
):
    """按夹具重放一条黄金用例，返回与 expected 同构的实际输出 dict。

    ``slices`` 给定时优先取内存切片（freeze 落盘前自检用）；否则从盘上读。
    """
    m = manifest or load_manifest()
    p = params or manifest_params(m)
    kind = case["kind"]
    if kind == "budget":
        return _run_budget(case, m, p)
    if kind in ("dispatch", "re_route"):
        return _run_m1_case(case, m, p)
    slice_doc = (slices or {}).get(case["slice"]) or load_slice(case["slice"])
    led, as_of, view = build_view(slice_doc, params=p)
    try:
        if kind == "route":
            return _run_route(case, m, view, p)
        if kind == "route_thompson":
            return _run_route_thompson(case, m, view, p)
        if kind == "promote_scan":
            return _run_promote(case, led, as_of, p)
        if kind == "agg_cells":
            return _run_agg(view)
        raise ValueError(f"未知用例类型：{kind!r}")
    finally:
        led.close()


# ── 精确比对（逐位；返回差异清单，空 = 通过）─────────────────────

def _is_bool(x) -> bool:
    return isinstance(x, bool)


def diff_doc(path: str, expected, actual, out: list[str] | None = None) -> list[str]:
    """递归精确比对；差异以 ``路径: expected … got …`` 行式列出。"""
    out = [] if out is None else out
    if isinstance(expected, dict) and isinstance(actual, dict):
        for k in sorted(set(expected) | set(actual)):
            if k not in expected:
                out.append(f"{path}.{k}: 多出的键（实际输出含、期望无）")
            elif k not in actual:
                out.append(f"{path}.{k}: 缺失的键（期望含、实际无）")
            else:
                diff_doc(f"{path}.{k}", expected[k], actual[k], out)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            out.append(f"{path}: 列表长度 expected={len(expected)} got={len(actual)}")
        else:
            for idx, (e, a) in enumerate(zip(expected, actual)):
                diff_doc(f"{path}[{idx}]", e, a, out)
    else:
        if _is_bool(expected) != _is_bool(actual):
            out.append(f"{path}: 布尔型不符 expected={expected!r} got={actual!r}")
        elif expected != actual:
            out.append(f"{path}: expected={expected!r} got={actual!r}")
    return out


def check_case(
    case: dict,
    *,
    manifest: dict | None = None,
    params: ParamsConfig | None = None,
    slices: dict[str, dict] | None = None,
) -> list[str]:
    """重放一条用例并与期望精确比对；返回差异行（空列表 = 黄金通过）。"""
    actual = execute_case(case, manifest=manifest, params=params, slices=slices)
    return diff_doc(case.get("case_id", case["kind"]), case["expected"], actual)


# ── cluster 黄金用例（M1：kernel.cluster 公式族回归，SPEC-5 §4.3）──
#
# 夹具形态：inputs 自含（findings 种子行 + 复合操作 op 序列的前缀），expected 钉
# 「事件流（kind/ts/payload，剔 seq）+ 三投影（clusters/findings）+ cells 全格表」。
# 重放 = 同一 op 序列在 SteppedClock 内存账本上重执行 → 与 expected 逐位比对。
# 价值：聚类判据（重叠/相似度阈值/all-pairwise）、归属展开（权重/次序/src_seq）、
# 幂等键、id 签发、拆分语义的任何公式改动都会在这里先红（突变门复用同一批夹具）。

CLUSTER_DIR = GOLDEN_DIR / "cluster"
CLUSTER_T0 = "2031-03-04T00:00:00Z"
CLUSTER_RUN_ID = "R-GOLDEN"


def cluster_case_files() -> list[Path]:
    """cluster 夹具路径（case-*.json；manifest.json 不入用例集）。"""
    return sorted(CLUSTER_DIR.glob("case-*.json"))


def load_cluster_manifest() -> dict:
    return json.loads((CLUSTER_DIR / "manifest.json").read_text(encoding="utf-8"))


def _cluster_sim(doc: dict | None):
    """夹具 sim 注入桩：{"kind": "const", "value": x} → 常函数；None → 无注入。"""
    if doc is None:
        return None
    if doc.get("kind") == "const":
        value = float(doc["value"])
        return lambda fa, fb: value
    raise ValueError(f"未知 sim 注入桩：{doc!r}")


def _cluster_ctx(doc: dict | None):
    from qswarm.kernel import MemberFacts, VerdictContext

    doc = doc or {}
    return VerdictContext(
        members=tuple(MemberFacts(m[0], m[1], m[2]) for m in doc.get("members", ())),
        scouts=tuple(doc.get("scouts", ())),
        votes=tuple(tuple(v) for v in doc.get("votes", ())),
        verifier_id=doc.get("verifier_id"),
    )


def _seed_cluster_world(led, findings: list[dict]) -> None:
    """runs/findings 种子行 + 同事务纪律的 finding(open) 事件（行创建即有账本事件）。"""
    led.conn.execute(
        "INSERT INTO runs (run_id, mode, repo_url, budget_cents, spent_cents,"
        " status, started_at, meta_json) VALUES (?, 'full', 'repo', 600, 0,"
        " 'running', '2031-03-04T00:00:00Z', '{}')",
        (CLUSTER_RUN_ID,),
    )
    for f in findings:
        led.conn.execute(
            "INSERT INTO findings (id, run_id, reporter, reporter_role, family,"
            " type, lang, repo, path, func, line_start, line_end, commit_sha,"
            " cluster_id, title, severity, cwe, confidence, grade, state,"
            " disagreement_json, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f["id"], CLUSTER_RUN_ID, f.get("reporter", "A1"),
                f.get("reporter_role", "auditor"), f.get("family", "injection"),
                f.get("type", "xss"), f.get("lang", "py"), "repo", f["path"],
                None, f["line_start"], f["line_end"], "c", None, "t", "high",
                f.get("cwe", "CWE-79"), 0.5, "L1", f.get("state", "open"), None,
                "2031-03-04T00:00:00Z", "2031-03-04T00:00:00Z",
            ),
        )
        led.append(
            "finding",
            {"finding_id": f["id"], "reporter": f.get("reporter", "A1"),
             "cluster": None, "state": "open"},
            run_id=CLUSTER_RUN_ID,
        )
    for v in []:  # votes 行按 op 就地播种（见 _op_seed_votes）
        pass


def _seed_cluster_votes(led, finding_id: str, votes: list[dict]) -> None:
    for v in votes:
        led.conn.execute(
            "INSERT INTO votes (vote_id, finding_id, reviewer, dim_completeness,"
            " dim_reachability, dim_exploitability, dim_fp_suspicion, direction,"
            " comment_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"V-{finding_id}-{v['reviewer']}", finding_id, v["reviewer"],
             v["dims"][0], v["dims"][1], v["dims"][2], v["dims"][3],
             v["direction"], None, "2031-03-04T00:00:00Z"),
        )


def _run_cluster_op(led, op: dict, params, world_findings=()) -> None:
    """执行一个复合操作（op 词表见 freeze_golden._cluster_scenario）。"""
    from qswarm.kernel import (
        ClusterProposal,
        FindingFacts,
        apply_human_verdict,
        apply_merges,
        close_panel,
        cluster_findings,
        propagate_pending,
        propagate_verdict,
        split_cluster,
    )
    from qswarm.contracts import LedgerKind, SignalTier

    kind = op["op"]
    if kind == "seed_votes":
        _seed_cluster_votes(led, op["finding_id"], op["votes"])
        led.conn.commit()
        return
    if kind == "find_and_merge":
        props = cluster_findings(
            [
                FindingFacts(
                    f["id"], f["path"], f["line_start"], f["line_end"],
                    f.get("cwe", "CWE-79"), f.get("lang", "py"),
                    f.get("family", "injection"), f.get("type", "xss"),
                )
                for f in op.get("findings", world_findings)
            ],
            level=op["level"],
            sim_fn=_cluster_sim(op.get("sim")),
            assigned=op.get("assigned"),
        )
        apply_merges(led, props, run_id=CLUSTER_RUN_ID)
        return
    if kind == "apply_merges":
        apply_merges(
            led,
            [
                ClusterProposal(
                    level=p["level"], member_ids=tuple(p["member_ids"]),
                    key_path=p["key_path"], key_cwe=p.get("key_cwe"),
                    key_lang=p["key_lang"], line_window=tuple(p["line_window"]),
                    sim_min=p.get("sim_min"),
                )
                for p in op["proposals"]
            ],
            run_id=CLUSTER_RUN_ID,
        )
        return
    if kind == "split_cluster":
        split_cluster(led, op["cluster_id"], op["groups"], actor=op.get("actor", "human"),
                      run_id=CLUSTER_RUN_ID)
        return
    if kind == "append":
        led.append(op["kind"], op["payload"], run_id=CLUSTER_RUN_ID)
        return
    if kind == "propagate":
        src = led.append(op["source"]["kind"], op["source"]["payload"],
                         run_id=CLUSTER_RUN_ID)
        propagate_verdict(
            led, op["cluster_id"], tier=SignalTier(op["tier"]),
            direction=op["direction"], source_seq=src.seq,
            source_kind=LedgerKind(op.get("declared_kind", op["source"]["kind"])),
            ctx=_cluster_ctx(op.get("ctx")), params=params, run_id=CLUSTER_RUN_ID,
        )
        return
    if kind == "close_panel":
        close_panel(led, op["finding_id"], ctx=_cluster_ctx(op.get("ctx")),
                    expected_votes=op.get("expected_votes"), params=params,
                    run_id=CLUSTER_RUN_ID)
        return
    if kind == "human_verdict":
        apply_human_verdict(led, op["cluster_id"], direction=op["direction"],
                            actor=op.get("actor", "human"),
                            source=op.get("source_name", "panel"),
                            ctx=_cluster_ctx(op.get("ctx")), params=params,
                            run_id=CLUSTER_RUN_ID)
        return
    if kind == "propagate_pending":
        lineage_doc = op.get("lineage", {})
        propagate_pending(
            led, lineage=lambda c: (
                _cluster_ctx(lineage_doc[c]) if lineage_doc.get(c) is not None else None
            ),
            params=params,
        )
        return
    raise ValueError(f"未知 cluster 黄金 op：{kind!r}")


def execute_cluster_case(case: dict, *, params: ParamsConfig | None = None) -> dict:
    """按夹具重放一条 cluster 黄金用例，返回与 expected 同构的实际输出 dict。"""
    from qswarm.kernel import KernelLedger, SteppedClock
    from qswarm.kernel.cells import view_cells
    from qswarm.kernel.replay import cluster_projection, finding_projection

    p = params or manifest_params()
    led = KernelLedger(":memory:", clock=SteppedClock(CLUSTER_T0, 1.0))
    try:
        _seed_cluster_world(led, case["inputs"]["findings"])
        led.conn.commit()
        for op in case["inputs"]["ops"]:
            if op["op"] == "expect_error":
                before = led.latest_seq()
                with pytest.raises(ValueError, match=op["match"]):
                    _run_cluster_op(led, op["call"], p, case["inputs"]["findings"])
                # propagate op 的源事件 append 属 harness 侧（先于被守卫的
                # propagate_verdict）；守卫本身必须零新增事件。
                allowed = before + (1 if op["call"]["op"] == "propagate" else 0)
                assert led.latest_seq() == allowed, (
                    f"{op['call']['op']}: 守卫失败必须零落库")
            else:
                _run_cluster_op(led, op, p, case["inputs"]["findings"])
        events = [
            {"kind": ev.kind.value, "ts": ev.ts, "payload": ev.payload}
            for ev in led.read()
        ]
        clusters = {
            cid: {
                "level": c.level, "members": list(c.members),
                "verdict_tier": c.verdict_tier, "verdict_dir": c.verdict_dir,
                "verdict_at": c.verdict_at, "created_at": c.created_at,
                "split": c.split,
            }
            for cid, c in sorted(cluster_projection(led).items())
        }
        findings = {
            fid: {"state": f.state, "cluster_id": f.cluster_id}
            for fid, f in sorted(finding_projection(led).items())
        }
        evs = led.read()
        as_of = evs[-1].ts if evs else CLUSTER_T0
        view = view_cells(led, as_of, params=p)
        cells = {}
        for key in sorted(view.cells):
            c = view.cells[key]
            cells[f"{key.agent_id}|{key.role}|{key.node}"] = {
                "pos_w": c.pos_w, "neg_w": c.neg_w, "n_eff": c.n_eff,
                "n_eff_s2s3": c.n_eff_s2s3, "status": c.status,
                "activated_at": c.activated_at, "updated_at": c.updated_at,
            }
        return {"events": events, "clusters": clusters, "findings": findings,
                "cells": cells}
    finally:
        led.close()


def check_cluster_case(case: dict, *, params: ParamsConfig | None = None) -> list[str]:
    """重放一条 cluster 用例并与期望精确比对；返回差异行（空列表 = 黄金通过）。"""
    return diff_doc(case.get("case_id", "cluster"),
                    case["expected"],
                    execute_cluster_case(case, params=params))
