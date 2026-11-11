"""kernel.evidence：evidence_v3 只读投影（方案 §4；SPEC-10 §7；R1-T5 纠错版）。

**只读纪律**：本模块零 SQL 写、零新表、零账本事件——纯读取账本事件做文件投影
（``evidence_v3.jsonl`` + ``capability_v3.json``）。旧 cells 与 M0 黄金零触碰。

**R1-T5 对 v2 的勘误**（docs/R1_CHANGE_NOTE.md §1-T5；v2 工件为已勘误历史口径，
不覆盖、只补勘误链接）：

1. **population 分立**（v2 缺陷：正向报告与正确拒判混入同一 TP/FP 池——
   1 错误正向上报 + 9 正确拒判输出 0.9）：v3 先定义统计集合再算数——
   正向上报 precision 的分子分母只出自正向报告集（verified_real 决策），
   正确拒判另列 ``correct_rejection_rate``，verify_error 不进 precision 分母
   也不从覆盖/成本面消失；
2. **direction=0 = 忽略**（v2 缺陷：被映射为 refuted 真值、可与正标签造假
   disputed）：human_verdict direction=0 不产生真值记录、不参与冲突判定；
3. **富化只用事件**（v2 缺陷：findings 表当前态富化，未来 finding/merge 改
   变历史 as_of 投影）：v3 富化源 = as_of 窗口内的 FINDING 事件（reporter/
   run 绑定）；事件面无记录的字段（family/repo_commit）如实 ``unknown``——
   同 as_of 同事件输入 → 同投影哈希（未来事件零影响）。

观察记录字段（方案 §4.2）与纠错规则（§4.3：supersedes / 一次根因一个事实
单位 / 同级冲突 disputed 归 unknown）沿用 v2 语义。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import LedgerKind
from qswarm.kernel.ledger import KernelLedger

__all__ = [
    "EvidenceProjection",
    "project_evidence",
    "effective_view",
    "independent_root_causes",
    "capability_summary",
    "write_projection",
]

_UNKNOWN = "unknown"


def _canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass
class EvidenceProjection:
    """一次投影的完整面（记录 + 元数据 + 确定性哈希）。"""

    as_of_seq: int                       # 投影截止账本 seq（含）
    records: list[dict] = field(default_factory=list)
    truth_events_seen: int = 0
    disputed_clusters: list[str] = field(default_factory=list)

    @property
    def projection_hash(self) -> str:
        return "ev3-" + hashlib.sha256(
            (_canon(self.as_of_seq) + _canon(self.records)).encode("utf-8")
        ).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "policy": "evidence_v3",
            "as_of_seq": self.as_of_seq,
            "projection_hash": self.projection_hash,
            "n_records": len(self.records),
            "truth_events_seen": self.truth_events_seen,
            "disputed_clusters": list(self.disputed_clusters),
            "records": list(self.records),
        }


# ── 投影主入口 ─────────────────────────────────────────────────

def project_evidence(ledger: KernelLedger, *, as_of_seq: int | None = None) -> EvidenceProjection:
    """账本事件 → evidence_v3 记录集（零写；富化只用 as_of 窗口内事件）。

    - 判定观察源 = ``verify_result`` 事件（seq 序，append-only 可重放）；
    - 真值源 = ``human_verdict`` 事件：**direction=0 = 忽略**（不产生真值、
      不入冲突判定——R1-T5）；同簇 ±1 方向冲突 → disputed（truth 归 unknown）；
      ``available_at`` = 事件 ts，晚于 as_of 的真值不采（时间可见性）；
    - 富化 = as_of 窗口内 FINDING 事件（首见 per cluster：reporter/run 绑定）；
      事件面无记录的 family/repo_commit 如实 unknown（不用当前表态读未来）；
    - 存量选择面（eligible_set/selection_probability）无记录 → unknown。"""
    if as_of_seq is None:
        last = ledger.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM ledger").fetchone()
        as_of_seq = int(last[0])

    # 真值：human_verdict 按簇收集（seq 序）；direction=0 跳过；冲突 → disputed。
    truth_by_cluster: dict[str, dict] = {}
    disputed: set[str] = set()
    n_truth = 0
    for ev in ledger.read(kinds=[LedgerKind.HUMAN_VERDICT], to_seq=as_of_seq):
        n_truth += 1                                # to_seq 闭区间：未来真值不采
        cid = str(ev.payload.get("cluster_id"))
        direction = int(ev.payload.get("direction", 0))
        if direction == 0:
            continue                                # R1-T5：忽略 ≠ refuted（零真值）
        label = "real" if direction > 0 else "refuted"
        prev = truth_by_cluster.get(cid)
        if prev is None:
            truth_by_cluster[cid] = {
                "truth_label": label,
                "truth_source": "human_verdict",
                "available_at": ev.ts,
                "adjudication_ref": f"ledger:{ev.seq}",
            }
        elif prev["truth_label"] != label:
            disputed.add(cid)                       # 同级冲突 → disputed 归 U
            truth_by_cluster[cid] = {
                "truth_label": "disputed",
                "truth_source": "human_verdict",
                "available_at": ev.ts,
                "adjudication_ref": f"ledger:{ev.seq}",
            }

    # 富化（只读事件面）：cluster → 首见 FINDING 事件（as_of 内——未来 finding/
    # merge 不改变历史投影；事件无 family/repo_commit → 如实 unknown）。
    enrich: dict[str, dict] = {}
    for ev in ledger.read(kinds=[LedgerKind.FINDING], to_seq=as_of_seq):
        cid = ev.payload.get("cluster")
        if cid is None:
            continue
        key = str(cid)
        if key not in enrich:                       # 首见（确定性；seq 序）
            enrich[key] = {
                "reporter": str(ev.payload.get("reporter", _UNKNOWN)),
                "run_id": str(ev.run_id) if ev.run_id else _UNKNOWN,
                "first_finding_seq": ev.seq,
            }

    # 判定观察：verify_result 事件（seq 序）；同簇后判定 supersedes 前判定。
    records: list[dict] = []
    last_obs_by_cluster: dict[str, str] = {}
    for ev in ledger.read(kinds=[LedgerKind.VERIFY_RESULT], to_seq=as_of_seq):
        cid = str(ev.payload.get("cluster_id"))
        outcome = str(ev.payload.get("outcome"))
        error_kind = ev.payload.get("error_kind")
        truth = truth_by_cluster.get(cid)
        en = enrich.get(cid, {})
        observation_id = f"obs-vr-{ev.seq}"
        records.append({
            # ── 方案 §4.2 字段表逐字（v3 勘误面）──────────────────
            "observation_id": observation_id,
            "logical_evidence_id": f"lec-prov:{cid}",   # provisional（§4.3）
            "supersedes": last_obs_by_cluster.get(cid),
            "root_cause_group_id": cid,
            "repo_commit": en.get("repo_commit", _UNKNOWN),   # 事件面无 → unknown
            "task_id": None,
            "execution_profile_id": _UNKNOWN,           # 存量事件无记录，如实
            "role": "verifier",
            "family": en.get("family", _UNKNOWN),       # 事件面无 → unknown（R1-T5）
            "claim": "vuln_reported",                   # 上游 finding 主张面
            "decision": outcome,
            "truth_label": truth["truth_label"] if truth else _UNKNOWN,
            "truth_source": truth["truth_source"] if truth else None,
            "oracle_version": _UNKNOWN,                 # 事件面未携（工件面另读）
            "adjudication_ref": truth["adjudication_ref"] if truth else None,
            "eligible_set": _UNKNOWN,
            "selection_probability": None,
            "selection_reason": "legacy_event_no_selection_record",
            "occurred_at": ev.ts,
            "available_at": truth["available_at"] if truth else ev.ts,
            "correlation_group": cid,
            "cost": None,
            "latency": None,
            "failure_kind": error_kind,
            # ── 投影溯源（非 §4.2 表面，重建用）──────────────
            "_ledger_seq": ev.seq,
            "_cluster_id": cid,
            "_run_id": en.get("run_id", _UNKNOWN),
            "_reporter": en.get("reporter", _UNKNOWN),
        })
        last_obs_by_cluster[cid] = observation_id

    return EvidenceProjection(
        as_of_seq=as_of_seq, records=records,
        truth_events_seen=n_truth, disputed_clusters=sorted(disputed),
    )


# ── 有效视图与根因去重（§4.3/§4.5 验收）────────────────────────

def effective_view(projection: EvidenceProjection) -> list[dict]:
    """有效视图 = 每逻辑事实只保留当前适用判定（supersede 链收口）。

    被指向 supersedes 的观察不进入有效视图——纠错/重跑不增加事实单位。"""
    superseded = {
        r["supersedes"] for r in projection.records if r["supersedes"]
    }
    return [r for r in projection.records if r["observation_id"] not in superseded]


def independent_root_causes(records: list[dict]) -> int:
    """独立根因数 = distinct logical_evidence_id（重复投票/finding/纠错不增数）。"""
    return len({r["logical_evidence_id"] for r in records})


# ── 能力汇总（§4.4；R1-T5 population 分立版；不参与派单）────────────

def capability_summary(view: list[dict]) -> dict:
    """按 family 汇总（**先定义 population 再算数**——R1-T5 勘误面）。

    集合定义（HANDOVER §9.2 表逐字）：

    - **正向报告集** = decision=verified_real（系统主张漏洞存在的判定）；
      正向上报 precision = TP/(TP+FP)，TP = 正向∧truth=real、FP = 正向∧
      truth=refuted——**正确拒判（verified_fp∧refuted）不进分子分母**；
    - 正向未知界 = 对同一正向报告集 [TP/(TP+FP+U), (TP+U)/(TP+FP+U)]，U =
      正向∧truth∈{unknown, disputed}——界限是未知标签范围，非统计置信区间；
    - **正确拒判率** = 已知负例集合（decision=verified_fp∧truth 已知）中
      truth=refuted 占比——不叫 precision；无已知负例 → not_estimable；
    - verify_error 不进 precision 分母，也不从覆盖面消失（decision_counts
      全量保留；覆盖/成本统计另有其位）；
    - 无完整金标准清单时系统召回 not_estimable（不从已验证正例反推）。"""
    groups: dict[str, dict] = {}
    for r in view:
        fam = str(r.get("family", _UNKNOWN))
        g = groups.setdefault(fam, {
            "n_observations": 0, "n_logical": set(),
            "tp": 0, "fp": 0, "u": 0,                 # 正向报告集三分类
            "neg_labeled": 0, "correct_rejections": 0,  # 负例集（拒判面）
            "neg_misjudged": 0,                       # verified_fp ∧ truth=real
            "verified_real": 0, "verified_fp": 0, "verify_error": 0,
        })
        g["n_observations"] += 1
        g["n_logical"].add(r["logical_evidence_id"])
        decision = r.get("decision")
        if decision in ("verified_real", "verified_fp", "verify_error"):
            g[decision] += 1
        truth = r.get("truth_label")
        if decision == "verified_real":
            if truth == "real":
                g["tp"] += 1
            elif truth == "refuted":
                g["fp"] += 1                          # 错误正向上报（precision 分子外）
            else:                                     # unknown / disputed 归 U
                g["u"] += 1
        elif decision == "verified_fp":
            if truth == "refuted":
                g["neg_labeled"] += 1
                g["correct_rejections"] += 1          # 正确拒判（TN——不进 precision）
            elif truth == "real":
                g["neg_labeled"] += 1
                g["neg_misjudged"] += 1

    out: dict[str, dict] = {}
    for fam, g in sorted(groups.items()):
        tp, fp, u = g["tp"], g["fp"], g["u"]
        pos_denom = tp + fp                            # 正向报告集已知标签面
        pos_all = tp + fp + u
        neg = g["neg_labeled"]
        out[fam] = {
            "n_observations": g["n_observations"],
            "n_logical_root_causes": len(g["n_logical"]),
            "decision_counts": {
                "verified_real": g["verified_real"],
                "verified_fp": g["verified_fp"],
                "verify_error": g["verify_error"],
            },
            # ── 正向报告集（population = verified_real 决策）────────
            "positive_reports": pos_all,
            "tp": tp, "fp": fp, "u": u,
            "positive_report_precision": (
                round(tp / pos_denom, 4) if pos_denom else "not_estimable"
            ),
            "positive_report_unknown_bounds": (
                [round(tp / pos_all, 4), round((tp + u) / pos_all, 4)]
                if pos_all else "not_estimable"
            ),
            # ── 拒判面（population = verified_fp ∧ 已知负例标签）─────
            "correct_rejections": g["correct_rejections"],
            "correct_rejection_rate": (
                round(g["correct_rejections"] / neg, 4) if neg else "not_estimable"
            ),
            "negative_misjudgments": g["neg_misjudged"],
            "note": "population 分立（R1-T5）：precision 只含正向报告集；"
                    "未知界非统计置信区间（§4.4）；不参与派单",
        }
    return out


# ── 文件落盘（实验工件目录；零账本零生产表）────────────────────

def write_projection(projection: EvidenceProjection, out_dir: Path) -> dict:
    """投影落盘 evidence_v3.jsonl + capability_v3.json，返回工件路径集。"""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl = out_dir / "evidence_v3.jsonl"
    with jsonl.open("w", encoding="utf-8") as fh:
        for r in projection.records:
            fh.write(_canon(r) + "\n")
    cap = out_dir / "capability_v3.json"
    view = effective_view(projection)
    cap.write_text(_canon({
        "policy": "capability_v3",
        "as_of_seq": projection.as_of_seq,
        "evidence_hash": projection.projection_hash,
        "disputed_clusters": projection.disputed_clusters,
        "families": capability_summary(view),
    }), encoding="utf-8")
    return {
        "evidence_v3": str(jsonl), "capability_v3": str(cap),
        "projection_hash": projection.projection_hash,
    }
