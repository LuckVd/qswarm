"""kernel.funnel：离线漏斗 trace 装配（方案 §5.1；SPEC-10 §8；P1 工作包 C）。

**分阶段保留漏斗损失**（§5.1 层清单逐字）：L0 未覆盖 / L1 丢弃 / L2 未报告 /
评审拒绝 / 预算未执行 / S2 error / 确认上报——每层一条记录：输入/输出计数、
原因分解、逐条可得性（``per_item_available``——存量报告多为总量面，如实标注，
不伪造逐条）、抽样概率（存量 unknown）、工件引用。

**金标准回查**（§5.2 路径 1）：已知漏洞项按漏斗回放定位**首次漏失层**
（``first_loss_layer``）——L0/L1 逐条数据存量不可得 → 如实 ``not_assessable``，
可判层（finding 建立/评审/S2/确认）走账本只读查询。

**写口边界**：只读账本/报告，工件只落实验目录（out/ 下）——零账本写、零生产表
（与 evidence_v2 同纪律）。线上/影子随机审计（§5.2 路径 2）属 E1+ pilot，
P1 不实施（预算 10% 预留随 manifest 冻结）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from qswarm.contracts import FindingState, LedgerKind, VerifyOutcome
from qswarm.kernel.ledger import KernelLedger

__all__ = ["STAGES", "assemble_trace", "first_loss_layer", "write_trace"]

#: 漏斗层（方案 §5.1 逐字；序即损失定位序）。
STAGES = (
    "L0_uncovered",        # 文件/模块未被任何通道提名（L0 之外完全不可见）
    "L1_discarded",        # 候选点被 L1 分诊丢弃（scout 判不疑）
    "L2_unreported",       # 深诊执行但未产 finding（l2_clean/schema/infra）
    "review_rejected",     # 评审拒绝（方向 -1 / finding rejected）
    "budget_not_run",      # 预算/编制未执行（quota 跳过 / 无 verifier / 预算停）
    "s2_error",            # S2 执行失败（env/poc/timeout/flaky）
    "confirmed_reported",  # 终点：确认上报
)

_UNKNOWN = "unknown"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class KnownItem:
    """金标准回查项（已知漏洞任务；§5.2 路径 1）。"""

    item_id: str
    path: str                  # 仓内路径（finding 定位键）
    family: str | None = None


# ── trace 装配（总量面 + 如实可得性）────────────────────────────

def assemble_trace(
    *,
    scan_report: dict | None = None,
    consult_report: dict | None = None,
    verify_report: dict | None = None,
    ledger: KernelLedger | None = None,
    as_of_seq: int | None = None,
) -> list[dict]:
    """既有报告 + 只读账本 → 逐层 trace 记录（零写；报告缺层如实 omitted）。

    as_of_seq 仅约束账本面：finding 计数为截止点内有状态事件的已知记录，
    不声称覆盖无事件的旧投影行。外部报告无 seq 谱系，由调用方选择对应工件。
    """
    trace: list[dict] = []

    def add(stage: str, *, in_count, out_count, reasons: dict, refs: dict | None = None):
        trace.append({
            "stage": stage,
            "in_count": in_count,
            "out_count": out_count,
            "reasons": reasons,
            "per_item_available": False,          # 存量报告为总量面（如实）
            "selection_probability": _UNKNOWN,    # 存量无抽样记录（§5.2 预见）
            "artifact_refs": refs or {},
        })

    if scan_report is not None:
        c = scan_report.get("counts", {})
        files_total = int(c.get("files_total", 0))
        files_scanned = int(c.get("files_scanned", 0))
        add("L0_uncovered",
            in_count=files_total, out_count=files_scanned,
            reasons={
                "skipped_binary": int(c.get("files_skipped_binary", 0)),
                "not_scanned_other": max(0, files_total - files_scanned
                                         - int(c.get("files_skipped_binary", 0))),
                "channel_a_absent_langs": list(
                    scan_report.get("channel_a_absent_langs", [])),
            },
            refs={"l0_candidates": scan_report.get("artifact_ref")})

    if consult_report is not None:
        fu = consult_report.get("funnel", {})
        fa = consult_report.get("failures", {})
        cands = int(fu.get("candidates_total", 0))
        l2 = int(fu.get("l2_dispatched", 0))
        opened = int(fu.get("findings_opened", 0))
        add("L1_discarded",
            in_count=cands, out_count=l2,
            # candidates_total 已是 L1 分诊后的候选；派单缺口不是 scout 决策。
            # 路由失败计数还可能有 fallback，不能与 quota 相加反推 L1 丢弃数。
            reasons={
                "scout_not_suspicious": _UNKNOWN,
                "not_dispatched": max(0, cands - l2),
                **{key: int(fa[key]) for key in (
                    "l2_quota_skipped", "l2_route_empty_pool",
                    "l2_model_ref_missing", "l2_gate_revoked",
                ) if key in fa},
            })
        add("L2_unreported",
            in_count=l2, out_count=opened,
            reasons={
                "l2_clean": int(fu.get("l2_clean", 0)),
                "l2_schema_fail": int(fa.get("l2_schema_fail", 0)),
                "l2_infra_fail": int(fa.get("l2_infra_fail", 0)),
                "l2_route_empty_pool": int(fa.get("l2_route_empty_pool", 0)),
            })

    if ledger is not None:
        # 当前投影仅用于无 cutoff 的查询；历史状态只取截止点内每 finding
        # 最后一条状态事件。无事件的旧投影行不可倒读补入历史。
        if as_of_seq is None:
            states = [row[0] for row in ledger.conn.execute(
                "SELECT state FROM findings"
            )]
        else:
            historical_states = {}
            for ev in ledger.read(kinds=[LedgerKind.FINDING], to_seq=as_of_seq):
                historical_states[str(ev.payload["finding_id"])] = ev.payload.get(
                    "state", _UNKNOWN,
                )
            states = list(historical_states.values())
        rejected = states.count(FindingState.REJECTED.value)
        confirmed = states.count(FindingState.CONFIRMED.value)
        # 评审拒绝层（账本只读：finding state + 反向票）。
        neg_votes = 0
        for ev in ledger.read(
            kinds=[LedgerKind.VOTE], to_seq=as_of_seq if as_of_seq is not None else None,
        ):
            if int(ev.payload.get("direction", 0)) < 0:
                neg_votes += 1
        add("review_rejected", in_count=None, out_count=int(rejected),
            reasons={"findings_rejected": int(rejected), "negative_votes": neg_votes})

        # S2 error 层（账本只读：verify_result 事件 error 面）。
        s2_err = {}
        for ev in ledger.read(
            kinds=[LedgerKind.VERIFY_RESULT],
            to_seq=as_of_seq if as_of_seq is not None else None,
        ):
            if str(ev.payload.get("outcome")) == VerifyOutcome.VERIFY_ERROR.value:
                k = str(ev.payload.get("error_kind") or "unspecified")
                s2_err[k] = s2_err.get(k, 0) + 1
        add("s2_error", in_count=None, out_count=sum(s2_err.values()), reasons=s2_err)

        # 确认上报层（账本只读：human_verdict 正向 / finding confirmed）。
        human_pos = sum(
            1 for ev in ledger.read(
                kinds=[LedgerKind.HUMAN_VERDICT],
                to_seq=as_of_seq if as_of_seq is not None else None,
            )
            if int(ev.payload.get("direction", 0)) > 0
        )
        add("confirmed_reported", in_count=None,
            out_count=max(int(confirmed), human_pos),
            reasons={"findings_confirmed": int(confirmed),
                     "human_verdict_positive": human_pos})

    if verify_report is not None:
        fu = verify_report.get("funnel", {})
        add("budget_not_run",
            in_count=None,
            out_count=int(fu.get("skipped_budget", 0)) + int(fu.get("skipped_no_verifier", 0)),
            reasons={"skipped_budget": int(fu.get("skipped_budget", 0)),
                     "skipped_no_verifier": int(fu.get("skipped_no_verifier", 0)),
                     "gate_revoked_midrun": int(fu.get("gate_revoked_midrun", 0))})

    order = {s: i for i, s in enumerate(STAGES)}
    trace.sort(key=lambda r: order.get(r["stage"], len(STAGES)))
    return trace


# ── 金标准回查（§5.2 路径 1：定位首次漏失层）────────────────────

def first_loss_layer(
    items: list[KnownItem], ledger: KernelLedger, *, as_of_seq: int | None = None,
) -> list[dict]:
    """已知漏洞项 → 逐项首次漏失层定位（只读账本）。

    可判层（数据可得）：L2 之后的 finding 建立 / 评审 / S2 / 确认；
    L0/L1 逐条存量不可得 → ``not_assessable``（如实，不伪造覆盖主张）。
    层全过 → ``passed_all_layers``（确认上报）。
    历史 finding 事件不含 path/family，无法可靠关联金标准项；不得借当前
    findings 表补身份/簇归属，故历史逐项回查如实 ``not_assessable``。
    """
    if as_of_seq is not None:
        return [{
            "item_id": item.item_id, "path": item.path,
            "family": item.family or _UNKNOWN,
            "first_loss_layer": "not_assessable",
            "assessable_layers": {
                "L0_uncovered": "not_assessable",
                "L1_discarded": "not_assessable",
                "L2_unreported": _UNKNOWN,
                "review_rejected": "not_assessable",
                "s2_layer": "not_assessable",
                "confirmed_reported": "not_assessable",
            },
            "evidence_refs": [],
            "note": "历史 finding 事件缺 path/family，无法匹配已知项；"
                    "不以当前 findings 状态或归属补历史，不伪造定位",
        } for item in items]

    findings_by_path: dict[str, list[dict]] = {}
    for row in ledger.conn.execute(
        "SELECT id, path, family, state, cluster_id FROM findings"
    ).fetchall():
        findings_by_path.setdefault(str(row[1]), []).append({
            "finding_id": str(row[0]), "family": str(row[2]),
            "state": str(row[3]), "cluster_id": str(row[4]) if row[4] else None,
        })
    verified_clusters = {
        str(ev.payload.get("cluster_id"))
        for ev in ledger.read(
            kinds=[LedgerKind.VERIFY_RESULT],
            to_seq=as_of_seq if as_of_seq is not None else None,
        )
        if str(ev.payload.get("outcome")) == VerifyOutcome.VERIFIED_REAL.value
    }
    confirmed_clusters = {
        str(ev.payload.get("cluster_id"))
        for ev in ledger.read(
            kinds=[LedgerKind.HUMAN_VERDICT],
            to_seq=as_of_seq if as_of_seq is not None else None,
        )
        if int(ev.payload.get("direction", 0)) > 0
    }

    out: list[dict] = []
    for item in items:
        rows = findings_by_path.get(item.path, [])
        if item.family is not None:
            rows = [r for r in rows if r["family"] == item.family]
        ambiguous_family = item.family is None and len({r["family"] for r in rows}) > 1
        record: dict = {
            "item_id": item.item_id, "path": item.path,
            "family": item.family or _UNKNOWN,
        }
        if ambiguous_family:
            record.update({
                "first_loss_layer": "not_assessable",
                "assessable_layers": {
                    "L0_uncovered": "not_assessable",
                    "L1_discarded": "not_assessable",
                    "L2_unreported": _UNKNOWN,
                },
                "evidence_refs": [],
                "note": "已知项未指定 family，同路径存在多个家族；不跨家族推断",
            })
        elif not rows:
            record.update({
                "first_loss_layer": "not_assessable",
                "assessable_layers": {
                    "L0_uncovered": "not_assessable",
                    "L1_discarded": "not_assessable",
                    "L2_unreported": "lost",       # 无 finding：L2 后必失（或更早）
                },
                "evidence_refs": [],
                "note": "无 finding 行——首次漏失在 L2 或更早（L0/L1 逐条存量"
                        "不可得，不伪造定位）",
            })
        else:
            layers = {
                "L2_unreported": "passed",
                "L0_uncovered": "not_assessable",
                "L1_discarded": "not_assessable",
            }
            refs = [r["finding_id"] for r in rows]
            any_rejected = any(r["state"] == FindingState.REJECTED.value for r in rows)
            clusters = {r["cluster_id"] for r in rows if r["cluster_id"]}
            verified = bool(clusters & verified_clusters)
            confirmed = bool(clusters & confirmed_clusters)
            if any_rejected:
                layers["review_rejected"] = "lost"
                first = "review_rejected"
            elif not verified:
                layers["s2_layer"] = "lost_or_not_run"   # S2 未跑/未过（账本不可分）
                first = "s2_error_or_not_run"
            elif not confirmed:
                layers["confirmed_reported"] = "not_reached"
                first = "not_confirmed"
            else:
                layers["confirmed_reported"] = "passed"
                first = "passed_all_layers"
            record.update({
                "first_loss_layer": first,
                "assessable_layers": layers,
                "evidence_refs": refs,
                "clusters": sorted(clusters),
            })
        out.append(record)
    return out


# ── 落盘（实验工件目录）────────────────────────────────────────

def write_trace(trace: list[dict], replay: list[dict] | None, out_dir: Path) -> str:
    """trace + 金标准回查 → funnel_trace.jsonl（逐行 = 逐层/逐项）。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "funnel_trace.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for rec in trace:
            fh.write(_canon(rec) + "\n")
        for rec in replay or []:
            fh.write(_canon({"kind": "gold_replay", **rec}) + "\n")
    return str(path)
