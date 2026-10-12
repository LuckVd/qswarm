"""kernel.replay：重放重建工具与重放等价比对（SPEC-1 §2 重放重建规则 / §6-6 重放等价）。

五个入口：

- :func:`rebuild_cells(ledger, upto_seq)`：从空账重放窗口内事件，全量重建 ``cells``
  （SPEC-1 §2「可随时 DROP 重建」+ ``kernel replay --from 0`` 的实现本体）。
  **聚合唯一实现**是 :func:`kernel.cells.agg_cells`：这里只以 seq 窗口适配器截断
  ``read()`` 的事件集，不复制任何聚合/写表逻辑——重放等价（§6-6）的实现前提是
  单一实现，两套实现逐位一致无从谈起。promotion/demotion 事件按 seq 序重放恢复
  status（``cells._replay_status_into``）——promote_scan 的在线产出恰是这些账本
  事件（同事务落库、cells.status 是事件的重放投影），故「重放事件」与「重跑幂等
  promote_scan」在账本上收敛到同一状态，重放取前者：无时钟、无副作用、确定性。
- :func:`rebuild_clusters` / :func:`rebuild_findings`（M1，SPEC-2 §6.3 三投影）：
  簇投影 ← ``cluster_merge``/``cluster_split`` + deposit（verdict_* 折叠，见
  :func:`rebuild_clusters`）；finding 投影 ← ``finding`` 事件（state 取每 finding
  最后一条，D9）+ ``verify_result``/``human_verdict`` 的簇级状态映射。**单一实现
  纪律**：重建只重放事件，不重跑合并/传播逻辑——cluster_merge.members 即成员
  真源、deposit 事件即存款真源，投影 = 事件折叠，无第二套业务规则。
- :func:`diff_cells` / :func:`diff_clusters` / :func:`diff_findings`：逐字段比对
  工具，浮点与一切字段按 ``==`` 逐位比较（同实现同序计算本应逐位一致，任何差异
  都算重放等价破坏，不做容差）。
- CLI：``python -m qswarm.kernel.replay <ledger.db> [--from N] [--upto N]`` 全量
  重建并输出重建摘要（崩溃恢复与快照回滚 §11 闸2 的操作入口）。

as_of 语义：重放按 **seq** 截断（窗口 [from_seq, upto_seq]，seq 是内核签发的全序），
挥发与 status 重放的观测时点 as_of 取窗口内最后一条事件的 ts——与在线侧"落完第 k
条事件即以该刻为 as_of 聚合"的快照语义严格同构（§6-6 的比对基准）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, replace
from typing import Mapping

from qswarm.contracts import ParamsConfig, load_params
from qswarm.contracts.enums import LedgerKind

from .cells import CellAgg, CellKey, CellView, agg_cells
from .ledger import KernelLedger

__all__ = [
    "CLUSTER_DIFF_FIELDS",
    "DEFAULT_DIFF_FIELDS",
    "FINDING_DIFF_FIELDS",
    "CellDiff",
    "ClusterProj",
    "FindingProj",
    "ProjDiff",
    "cluster_projection",
    "diff_cells",
    "diff_clusters",
    "diff_findings",
    "finding_projection",
    "rebuild_cells",
    "rebuild_clusters",
    "rebuild_findings",
    "rebuild_summary",
    "main",
]

#: 空窗口（from_seq > latest_seq 或空账本）的观测时点：无事件 → 无格子，
#: as_of 取纪元常量即可（不引 WallClock，重放无时间源依赖）。
_EMPTY_WINDOW_AS_OF = "1970-01-01T00:00:00Z"


# ── seq 窗口适配器：截断 read()，写口原样透传 ──────────────────

class _SeqWindow:
    """账本只读窗口：只暴露 ``seq ∈ [from_seq, upto_seq]`` 的事件集。

    :func:`agg_cells` 对账本的全部依赖是 ``read(kinds=…)``（聚合与 status 重放
    输入）和 ``conn``（cells 写口 ``_write_cells``）；窗口把前者截断到窗口内、
    后者原样透传——聚合与写表代码零复制，cells 仍只经 kernel.cells 落表
    （SPEC-1 §6-2 写口纪律）。
    """

    def __init__(self, inner: KernelLedger, *, from_seq: int, upto_seq: int) -> None:
        self._inner = inner
        self._from_seq = from_seq
        self._upto_seq = upto_seq

    @property
    def conn(self):
        """底层 SQLite 连接（agg_cells → _write_cells 的 cells 写口）。"""
        return self._inner.conn

    def read(self, *, kinds=None, from_seq=None, to_seq=None):
        """按 kind 与窗口交集 [max(from_seq, lo), min(to_seq, hi)] 过滤，seq 升序。"""
        lo = self._from_seq if from_seq is None else max(self._from_seq, int(from_seq))
        hi = self._upto_seq if to_seq is None else min(self._upto_seq, int(to_seq))
        return self._inner.read(kinds=kinds, from_seq=lo, to_seq=hi)


# ── 重放重建（SPEC-1 §2 重放重建规则）──────────────────────────

def rebuild_cells(
    ledger: KernelLedger,
    upto_seq: int | None = None,
    *,
    from_seq: int = 0,
    params: ParamsConfig | None = None,
) -> CellView:
    """从空账重放窗口 ``[from_seq, upto_seq]`` 内的事件，全量重建 cells。

    - ``upto_seq=None`` 取当前 ``latest_seq``（即 ``--from 0`` 的全量重建形态）；
    - 复用 :func:`kernel.cells.agg_cells` 聚合（经 :class:`_SeqWindow` 截断事件集），
      promotion/demotion 事件由 ``agg_cells`` 内部的 status 重放按 seq 序恢复；
    - as_of = 窗口内最后一条事件的 ts；窗口为空时用纪元常量（无事件 → 无格子）；
    - 副作用：``cells`` 表被整表重写（先清后插，单事务）；返回重建后的
      :class:`CellView` 供 diff/路由侧读取。
    """
    if from_seq < 0:
        raise ValueError(f"from_seq 必须 ≥ 0，实际 {from_seq}")
    upto = ledger.latest_seq() if upto_seq is None else int(upto_seq)
    if upto < from_seq:
        raise ValueError(f"重放窗口为空：upto_seq ({upto}) < from_seq ({from_seq})")
    window = _SeqWindow(ledger, from_seq=from_seq, upto_seq=upto)
    events = window.read()
    as_of = events[-1].ts if events else _EMPTY_WINDOW_AS_OF
    return agg_cells(window, as_of, params=params)


# ── 逐字段比对（重放等价的判定器）─────────────────────────────

#: 逐字段比对的默认字段集（CellAgg 同名字段；updated_at/last_event_seq 之外，
#: updated_at 由 as_of 派生——重放与在线 as_of 一致时天然相等，不单列）。
DEFAULT_DIFF_FIELDS = (
    "pos_w",
    "neg_w",
    "n_eff",
    "n_eff_s2s3",
    "last_event_seq",
    "status",
    "activated_at",
)


@dataclass(frozen=True)
class CellDiff:
    """一格一字段的差异记录：left/right 为两侧取值；缺格/多格时 field="presence"。"""

    agent_id: str
    role: str
    node: str
    field: str
    left: object
    right: object

    @property
    def key(self) -> CellKey:
        return CellKey(self.agent_id, self.role, self.node)

    def __str__(self) -> str:
        return f"{self.agent_id}/{self.role}/{self.node} · {self.field}: {self.left!r} != {self.right!r}"


def _as_cell_map(x) -> dict[CellKey, CellAgg]:
    """CellView / {CellKey: CellAgg} 映射 → 统一的映射形态。"""
    if isinstance(x, CellView):
        return x.cells
    if isinstance(x, Mapping):
        return dict(x)
    raise TypeError(f"diff_cells 接受 CellView 或 {CellKey.__qualname__}→{CellAgg.__qualname__} 映射，实际 {type(x).__name__}")


def diff_cells(
    a,
    b,
    *,
    fields: tuple[str, ...] = DEFAULT_DIFF_FIELDS,
) -> list[CellDiff]:
    """逐字段比对两份 cells（CellView 或映射），返回差异列表（空列表 = 完全一致）。

    - 键集不同：每个一侧缺失的格子记一条 ``field="presence"`` 差异；
    - 浮点按 ``==`` 逐位比较：重放等价是同实现同序计算（§6-6），出现任何一位
      漂移都判不一致——容差比较会掩盖真 bug（SPEC-4 §3 度量口径 100%）；
    - 结果按 (agent_id, role, node, field) 排序，断言失败信息可读且确定。
    """
    left, right = _as_cell_map(a), _as_cell_map(b)
    unknown = [f for f in fields if f not in CellAgg.__dataclass_fields__]
    if unknown:
        raise ValueError(f"未知比对字段：{unknown}（合法字段见 CellAgg）")
    out: list[CellDiff] = []
    for key in sorted(set(left) | set(right)):
        la, ra = left.get(key), right.get(key)
        if la is None or ra is None:
            out.append(CellDiff(key.agent_id, key.role, key.node, "presence", la, ra))
            continue
        for f in fields:
            lv, rv = getattr(la, f), getattr(ra, f)
            if lv != rv:
                out.append(CellDiff(key.agent_id, key.role, key.node, f, lv, rv))
    return out


# ── 簇投影与 finding 投影（M1 三投影，SPEC-2 §6.3）─────────────

#: 簇投影逐字段比对集（可重放面：cluster_merge/cluster_split + deposit 折叠可
#: 全量重建的字段；key_path/key_cwe/key_lang/line_window/embedding_sim 不在
#: cluster_merge payload 内——SPEC-1 §4.3 载荷冻结不可扩字段，投影面随之限定，
#: 见 DECISIONS 2026-10-10-CL3）。
CLUSTER_DIFF_FIELDS = (
    "level",
    "members",
    "verdict_tier",
    "verdict_dir",
    "verdict_at",
    "created_at",
    "split",
)

#: finding 投影逐字段比对集（state 取最后一条 finding 事件 D9 + cluster 归属；
#: grade/confidence/disagreement 不在任一 payload 内，投影面限定同上）。
FINDING_DIFF_FIELDS = ("state", "cluster_id")


@dataclass(frozen=True)
class ClusterProj:
    """簇投影一行（cluster_members + clusters.verdict_* 的可重放面）。"""

    cluster_id: str
    level: str
    members: tuple[str, ...]
    verdict_tier: str | None
    verdict_dir: int | None
    verdict_at: str | None
    created_at: str
    split: bool = False


@dataclass(frozen=True)
class FindingProj:
    """finding 投影一行（findings.state/cluster_id 的可重放面）。"""

    finding_id: str
    state: str
    cluster_id: str | None


@dataclass(frozen=True)
class ProjDiff:
    """一投影键一字段的差异记录；缺行/多行时 field="presence"。"""

    key: str
    field: str
    left: object
    right: object

    def __str__(self) -> str:
        return f"{self.key} · {self.field}: {self.left!r} != {self.right!r}"


def _fold_cluster_state(events) -> tuple[dict[str, ClusterProj], dict[str, str]]:
    """事件折叠出簇投影（rebuild_clusters 的本体；无 IO 无副作用）。

    返回 (投影, member→cluster 归属)——归属 = 最后一条包含该 member 的
    cluster_merge 事件（split 子簇 merge 事件随之改写归属，§6.3 成员真源）。

    - cluster_merge：建/扩——members 并入 payload.members（D5 增量真源），
      created_at 取首条事件 ts，level 取 payload.level（split 后复merge视为重建）；
    - cluster_split：被拆簇 members 清空、split=True（子簇成员由其自身的
      cluster_merge 事件承载）；
    - deposit：凡 payload.cluster 命中既有投影且 signal_tier ∈ {S1,S2,S3} 且
      direction ≠ 0 的**成员报告者行**，按 seq 序覆盖 verdict_tier/dir/at（at =
      该笔存款 ts；即 verdict 投影真源是 deposit 事件——verify_result/human_verdict
      的源事件与派生存款严格同事务同值，而 S1 收口 finding 事件不承载 direction，
      deposit 折叠是唯一三层级全可重放的口径，DECISIONS 2026-10-10-CL3）。
    """
    out: dict[str, ClusterProj] = {}
    member_cluster: dict[str, str] = {}
    for ev in events:
        if ev.kind is LedgerKind.CLUSTER_MERGE:
            p = ev.payload
            cid = str(p["cluster_id"])
            cur = out.get(cid)
            members = set(cur.members if cur else ()) | set(p["members"])
            out[cid] = ClusterProj(
                cluster_id=cid,
                level=str(p["level"]),
                members=tuple(sorted(members)),
                verdict_tier=cur.verdict_tier if cur else None,
                verdict_dir=cur.verdict_dir if cur else None,
                verdict_at=cur.verdict_at if cur else None,
                created_at=cur.created_at if cur else ev.ts,
                split=False,
            )
            for fid in p["members"]:
                member_cluster[str(fid)] = cid
        elif ev.kind is LedgerKind.CLUSTER_SPLIT:
            cid = str(ev.payload["cluster_id"])
            cur = out.get(cid)
            if cur is not None:
                out[cid] = replace(
                    cur, members=(), split=True
                )
        elif ev.kind is LedgerKind.DEPOSIT:
            p = ev.payload
            cid = p.get("cluster")
            tier = p.get("signal_tier")
            direction = int(p.get("direction", 0))
            # verdict 投影只锚定**成员报告者行**（payload.finding 非空）：§6.5 标准
            # 形下报告者行 direction ≡ verdict 方向，而侦察/评审/验证行的 ± 是回校
            # 语义（同向 +/反向 −），不承载 verdict 方向（DECISIONS 2026-10-10-CL3）。
            if p.get("finding") is None:
                continue
            if not cid or tier not in ("S1", "S2", "S3") or direction == 0:
                continue
            cur = out.get(str(cid))
            if cur is None:
                continue          # 未建簇的占位 cluster 号（M0 世界侧）：不入投影
            out[str(cid)] = replace(
                cur,
                verdict_tier=str(tier),
                verdict_dir=direction,
                verdict_at=ev.ts,
            )
    return out, member_cluster


def _fold_finding_state(
    events,
    clusters: dict[str, ClusterProj],
    member_cluster: Mapping[str, str],
) -> dict[str, FindingProj]:
    """事件折叠出 finding 投影（rebuild_findings 的本体）。

    - state 取每 finding **最后一条** finding 事件 payload.state（D9）——§2.4 的
      verify_result/human_verdict 状态映射以同事务发出的 kind=finding 事件为承载，
      折叠因此只需要 finding 事件（verify/human 事件本身不是状态载体）；
    - cluster 归属 = cluster_merge.members 折叠的成员归属（§6.3 成员真源——
      apply_merges/split 的 findings.cluster_id 回写在事件侧的真源即 merge 事件），
      无归属时回落 finding 事件 payload.cluster。
    """
    out: dict[str, FindingProj] = {}
    for ev in events:
        if ev.kind is LedgerKind.FINDING:
            p = ev.payload
            fid = str(p["finding_id"])
            out[fid] = FindingProj(
                finding_id=fid,
                state=str(p["state"]),
                cluster_id=p.get("cluster"),
            )
    for fid, cid in member_cluster.items():
        cur = out.get(fid)
        if cur is not None:
            out[fid] = replace(cur, cluster_id=cid)
    return out


def rebuild_clusters(
    ledger: KernelLedger,
    upto_seq: int | None = None,
    *,
    from_seq: int = 0,
) -> dict[str, ClusterProj]:
    """窗口 ``[from_seq, upto_seq]`` 事件折叠重建簇投影（纯折叠，不写任何表）。"""
    if from_seq < 0:
        raise ValueError(f"from_seq 必须 ≥ 0，实际 {from_seq}")
    upto = ledger.latest_seq() if upto_seq is None else int(upto_seq)
    if upto < from_seq:
        raise ValueError(f"重放窗口为空：upto_seq ({upto}) < from_seq ({from_seq})")
    return _fold_cluster_state(ledger.read(from_seq=from_seq, to_seq=upto))[0]


def rebuild_findings(
    ledger: KernelLedger,
    upto_seq: int | None = None,
    *,
    from_seq: int = 0,
) -> dict[str, FindingProj]:
    """窗口事件折叠重建 finding 投影（state 取最后一条 finding 事件，D9）。"""
    if from_seq < 0:
        raise ValueError(f"from_seq 必须 ≥ 0，实际 {from_seq}")
    upto = ledger.latest_seq() if upto_seq is None else int(upto_seq)
    if upto < from_seq:
        raise ValueError(f"重放窗口为空：upto_seq ({upto}) < from_seq ({from_seq})")
    events = ledger.read(from_seq=from_seq, to_seq=upto)
    clusters, member_cluster = _fold_cluster_state(events)
    return _fold_finding_state(events, clusters, member_cluster)


def cluster_projection(ledger: KernelLedger) -> dict[str, ClusterProj]:
    """在线簇投影：clusters/cluster_members 物理表 → 投影形态（diff 的在线侧）。"""
    verdict = {
        r[0]: r[1:]
        for r in ledger.conn.execute(
            "SELECT cluster_id, level, verdict_tier, verdict_dir, verdict_at, created_at"
            " FROM clusters"
        )
    }
    members: dict[str, list[str]] = {cid: [] for cid in verdict}
    for cid, fid in ledger.conn.execute(
        "SELECT cluster_id, finding_id FROM cluster_members ORDER BY cluster_id,"
        " finding_id"
    ):
        members.setdefault(str(cid), []).append(str(fid))
    return {
        cid: ClusterProj(
            cluster_id=cid,
            level=v[0],
            members=tuple(members.get(cid, ())),
            verdict_tier=v[1],
            verdict_dir=v[2],
            verdict_at=v[3],
            created_at=v[4],
            split=not members.get(cid),
        )
        for cid, v in verdict.items()
    }


def finding_projection(ledger: KernelLedger) -> dict[str, FindingProj]:
    """在线 finding 投影：findings 表 state/cluster_id 列（diff 的在线侧）。"""
    return {
        str(r[0]): FindingProj(finding_id=str(r[0]), state=str(r[1]), cluster_id=r[2])
        for r in ledger.conn.execute("SELECT id, state, cluster_id FROM findings")
    }


def _diff_projections(
    left: Mapping,
    right: Mapping,
    fields: tuple[str, ...],
) -> list[ProjDiff]:
    out: list[ProjDiff] = []
    for key in sorted(set(left) | set(right), key=str):
        lp, rp = left.get(key), right.get(key)
        if lp is None or rp is None:
            out.append(ProjDiff(str(key), "presence", lp, rp))
            continue
        for f in fields:
            lv, rv = getattr(lp, f), getattr(rp, f)
            if lv != rv:
                out.append(ProjDiff(str(key), f, lv, rv))
    return out


def diff_clusters(a, b, *, fields: tuple[str, ...] = CLUSTER_DIFF_FIELDS) -> list[ProjDiff]:
    """逐字段比对两份簇投影（dict[cid, ClusterProj]），空列表 = 完全一致（逐位）。"""
    unknown = [f for f in fields if f not in ClusterProj.__dataclass_fields__]
    if unknown:
        raise ValueError(f"未知比对字段：{unknown}（合法字段见 ClusterProj）")
    return _diff_projections(dict(a), dict(b), fields)


def diff_findings(a, b, *, fields: tuple[str, ...] = FINDING_DIFF_FIELDS) -> list[ProjDiff]:
    """逐字段比对两份 finding 投影（dict[fid, FindingProj]），空列表 = 完全一致。"""
    unknown = [f for f in fields if f not in FindingProj.__dataclass_fields__]
    if unknown:
        raise ValueError(f"未知比对字段：{unknown}（合法字段见 FindingProj）")
    return _diff_projections(dict(a), dict(b), fields)


# ── 摘要与 CLI ────────────────────────────────────────────────

def rebuild_summary(
    ledger: KernelLedger,
    upto_seq: int | None = None,
    *,
    from_seq: int = 0,
    params: ParamsConfig | None = None,
) -> dict:
    """重放重建并产出机器可读摘要（窗口构成 / as_of / 格子数 / 状态分布 / 三投影）。"""
    upto = ledger.latest_seq() if upto_seq is None else int(upto_seq)
    view = rebuild_cells(ledger, upto, from_seq=from_seq, params=params)
    events = ledger.read(from_seq=from_seq, to_seq=upto)
    clusters = rebuild_clusters(ledger, upto, from_seq=from_seq)
    findings = rebuild_findings(ledger, upto, from_seq=from_seq)
    return {
        "from_seq": from_seq,
        "upto_seq": upto,
        "events": len(events),
        "kinds": dict(sorted(Counter(e.kind.value for e in events).items())),
        "as_of": view.as_of.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        if view.as_of.microsecond
        else view.as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cells": len(view.cells),
        "status_counts": dict(
            sorted(Counter(c.status for c in view.cells.values()).items())
        ),
        "clusters": len(clusters),
        "findings": len(findings),
    }


def main(argv: list[str] | None = None) -> int:
    """``python -m qswarm.kernel.replay``：对任一账本文件重放重建 cells 并打印摘要。"""
    parser = argparse.ArgumentParser(
        prog="python -m qswarm.kernel.replay",
        description="kernel replay：从账本重放重建 cells 并输出重建摘要（SPEC-1 §2）",
    )
    parser.add_argument("ledger", help="SQLite 账本文件路径")
    parser.add_argument("--from", dest="from_seq", type=int, default=0,
                        help="重放窗口起点 seq（含），默认 0=全量重建")
    parser.add_argument("--upto", dest="upto_seq", type=int, default=None,
                        help="重放窗口终点 seq（含），默认=账本最新 seq")
    parser.add_argument("--params", default=None,
                        help="params.yaml 路径（缺省 configs/params.yaml）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出摘要")
    args = parser.parse_args(argv)

    try:
        params = load_params(args.params) if args.params else None
        with KernelLedger(args.ledger) as ledger:
            summary = rebuild_summary(
                ledger, args.upto_seq, from_seq=args.from_seq, params=params
            )
    except Exception as exc:  # CLI 边界：统一出口，退出码 1 + stderr 摘要
        print(f"kernel replay: 失败：{exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ledger": args.ledger, **summary}, ensure_ascii=False, indent=2))
    else:
        kinds = " ".join(f"{k}={v}" for k, v in summary["kinds"].items()) or "-"
        statuses = " ".join(f"{k}={v}" for k, v in summary["status_counts"].items()) or "-"
        print(f"kernel replay: {args.ledger}")
        print(
            f"  window : from_seq={summary['from_seq']} upto_seq={summary['upto_seq']}"
            f" events={summary['events']} ({kinds})"
        )
        print(f"  as_of  : {summary['as_of']}")
        print(f"  cells  : rebuilt={summary['cells']} ({statuses})")
        print(
            f"  proj   : clusters={summary['clusters']} findings={summary['findings']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
