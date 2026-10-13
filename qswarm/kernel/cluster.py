"""kernel.cluster：聚类归并内核（SPEC-2 §1；设计 §3.4 两级归并 × §6.5 归属矩阵）。

机制语义（逐条可溯源，SPEC-2 §1.1）：

- **两级归并**：粗聚类（投票前）仅「同 path + 行窗重叠 ≥50% + CWE 同 + lang 同」
  的保守合并；精聚类（投票后）在粗条件全部成立之外再加入「证据链向量相似度
  ≥ 0.9」。保守优先（§3.4 逐字）：宁可漏并（多花一次验证）不可错并（丢真
  finding）——落成三条实现裁定：行窗重叠 = 交集/min（D1）、all-pairwise 不做
  单链传递闭包（D2）、sim_fn 缺省 None = 精聚类不产生任何合并（D4）。
- **建簇/扩簇/拆分**：apply_merges 是 ``clusters``/``cluster_members`` 表的唯一
  写口（SPEC-2 §6.1），cluster_merge 事件 + 表行同事务（§7.1 ledger.transaction，
  D24 可重入）；split_cluster 人工拆分零存款（§3.4），拆分不回滚已传播存款
  （§6.2 append-only）。
- **簇级结论传播**：propagate_verdict 按 §6.5 归属矩阵全行展开存款（展开序 D7：
  成员→侦察→评审回校→验证，各自字典序），每笔派生存款携带 ``src_seq``（§5.1
  D6）作幂等键 (cluster, tier, src_seq)；冲突补记更强反向存款不回滚（§6.2）；
  verify_error / direction=0 零存款零投影。
- **确定性纪律（§6.5）**：本模块零随机性（不接随机流、不消费任何随机源）；一切集合
  展开按字典序或钉死规范序；时间由注入时钟签发（同 (种子, 输入事件序, params)
  ⇒ 事件流逐位一致）。

重放口径（§6.3 单一实现）：重建只重放事件——cluster_merge.members 即成员真源、
deposit 事件即存款真源；本模块不提供第二套折叠逻辑，重放投影见 kernel.replay
（rebuild_clusters / rebuild_findings）。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from qswarm.contracts import (
    DepositOrigin,
    DepositPayload,
    FindingState,
    HumanVerdictPayload,
    LedgerKind,
    ParamsConfig,
    Role,
    SignalTier,
    load_params,
)
from qswarm.contracts.models import (
    ClusterMergePayload,
    ClusterSplitPayload,
)

from .dispatch import (
    NON_TERMINAL_FINDING_STATES,
    attach_cluster,
    record_panel_consensus,
    transition_finding,
)
from .ledger import KernelLedger, LedgerEvent, iso_utc, transaction

__all__ = [
    "CLUSTER_EMBED_SIM_MIN",
    "CLUSTER_OVERLAP_MIN",
    "S1_SOURCE_W",
    "S2_SOURCE_W",
    "S3_SOURCE_W",
    "ClusterProposal",
    "FindingFacts",
    "MemberFacts",
    "RepairReport",
    "SimFn",
    "VerdictContext",
    "VoteConsensus",
    "VoteFacts",
    "aggregate_votes",
    "apply_human_verdict",
    "apply_merges",
    "attribute_findings",
    "close_panel",
    "cluster_findings",
    "coarse_mergeable",
    "fine_mergeable",
    "line_overlap",
    "propagate_pending",
    "propagate_verdict",
    "split_cluster",
]

# ── 规格常量（模块常量不进 params，extra=forbid 纪律，SPEC-2 §8.2）──────

#: 粗/精两级共用的行窗重叠下限（§3.4 逐字 ">=50%"）。
CLUSTER_OVERLAP_MIN: float = 0.5

#: 精聚类证据链向量相似度阈值（§3.4 逐字 "相似度 ≥0.9"）。
CLUSTER_EMBED_SIM_MIN: float = 0.9

#: S1 共识存款源权重（§6.1 区间 [0.05, 0.1] 取下沿；世界侧 VerdictParams.s1_weight
#: 是仿真实验臂旋钮不进内核，DECISIONS 2026-10-08 先例）。S2/S3 源权重为规格常量
#: 0.5 / 1.0（§6.1；与 simulator.verdict.S2_SOURCE_WEIGHT/S3_SOURCE_WEIGHT 同源同值）。
S1_SOURCE_W: float = 0.05
S2_SOURCE_W: float = 0.5
S3_SOURCE_W: float = 1.0

#: 相似度函数注入点：接收两个 FindingFacts（含 sim_text），返回 [0,1] 相似度。
#: **缺省 None = 精聚类不产生任何合并**（保守漏并；§3.4 宁漏勿错 + §8.3 注入点表）。
SimFn = Callable[["FindingFacts", "FindingFacts"], float]

_LEVELS = ("coarse", "fine")

#: propagate_verdict 的源事件词表（D23：含 FINDING——close_panel 的 S1 收口源事件
#: 即 panel 收口 finding 事件）。
_SOURCE_KINDS = (
    LedgerKind.VERIFY_RESULT,
    LedgerKind.HUMAN_VERDICT,
    LedgerKind.VOTE,
    LedgerKind.FINDING,
)


# ── 聚类判据事实面（§1.2）─────────────────────────────────────────

@dataclass(frozen=True)
class FindingFacts:
    """聚类判据所需的 finding 事实面（调用方从 findings 表行 + evidence_nodes 装配）。"""

    finding_id: str
    path: str
    line_start: int
    line_end: int
    cwe: str | None                # None = 未判 CWE（§3.1 可空）
    lang: str
    family: str
    type: str | None
    sim_text: str = ""             # 标题 + 证据链序列化文本（kind:where:ref 串联），
                                   # 供注入的 embedder 消费；内核不解释其内容


def line_overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """行窗重叠系数：交集行数 / min(|a|, |b|)（D1 口径裁定）。

    选交集/min 而非 Jaccard 的理由：子窗完全含于大窗时系数=1.0——同一根因的
    窄证据窗不应因邻居窗更宽而被拒并；空窗或 start>end → 0.0。
    """
    size_a = a[1] - a[0] + 1
    size_b = b[1] - b[0] + 1
    if size_a <= 0 or size_b <= 0:
        return 0.0
    inter = min(a[1], b[1]) - max(a[0], b[0]) + 1
    if inter <= 0:
        return 0.0
    return inter / min(size_a, size_b)


def coarse_mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
    """粗聚类判据（§3.4 保守合并）：同 path ∧ 同 lang ∧ CWE 相等
    ∧ line_overlap ≥ CLUSTER_OVERLAP_MIN。

    CWE 语义（D3）：双方非 None 须相等；双方 None 退化为 (family, type) 相等
    （家族级 finding 的保守替身）；恰一侧 None → False（保守）。
    """
    if fa.path != fb.path or fa.lang != fb.lang:
        return False
    if fa.cwe is not None and fb.cwe is not None:
        if fa.cwe != fb.cwe:
            return False
    elif fa.cwe is None and fb.cwe is None:
        if (fa.family, fa.type) != (fb.family, fb.type):
            return False
    else:
        return False                       # 恰一侧 None → 不可并（D3）
    return line_overlap(
        (fa.line_start, fa.line_end), (fb.line_start, fb.line_end)
    ) >= CLUSTER_OVERLAP_MIN


def fine_mergeable(fa: FindingFacts, fb: FindingFacts, *, sim_fn: SimFn | None) -> bool:
    """精聚类判据 = coarse_mergeable ∧ sim_fn(fa, fb) ≥ CLUSTER_EMBED_SIM_MIN。

    sim_fn 为 None 时恒 False（缺省不并，D4 注入点纪律）。
    """
    if sim_fn is None:
        return False
    if not coarse_mergeable(fa, fb):
        return False
    return float(sim_fn(fa, fb)) >= CLUSTER_EMBED_SIM_MIN


# ── 归并提案（纯函数；§1.2）───────────────────────────────────────

@dataclass(frozen=True)
class ClusterProposal:
    """一次归并提案（apply_merges 的输入；纯数据，可审计可重放）。"""

    level: str                      # "coarse" | "fine"
    member_ids: tuple[str, ...]     # 锚点（最小 finding_id）在前，其余字典序
    key_path: str
    key_cwe: str | None
    key_lang: str
    line_window: tuple[int, int]    # 成员行窗的并包络 (min_start, max_end)
    sim_min: float | None           # 精聚类：成员对最小相似度；粗聚类 None


def cluster_findings(
    findings: Sequence[FindingFacts],
    *,
    level: str,
    sim_fn: SimFn | None = None,
    assigned: Mapping[str, str] | None = None,      # finding_id → 既有 cluster_id
) -> list[ClusterProposal]:
    """确定性两级归并提案（纯函数，零 IO 零随机源）。

    - 候选序：按 (path, line_start, line_end, finding_id) 字典序扫描（§6.5）；
    - 合并判据：**all-pairwise**——新成员须与簇内**全部**成员两两可合并（D2：
      不做单链传递闭包，A~B~C 而 A≁C 时不并 C，防链式错并）；
    - ``assigned`` 中已归属的 finding 不参与新簇提案（扩簇场景由调用方把既有簇
      成员放进 assigned）；
    - 同一 finding 至多出现在一个提案中（先到先得，按候选序）；
    - 返回值只含 ≥2 成员的提案（单 finding 无归并语义；单例簇由调用方显式构造
      ClusterProposal 经 apply_merges 落簇）。
    """
    if level not in _LEVELS:
        raise ValueError(f"level 必须是 {_LEVELS}，实际 {level!r}")
    skip = set(assigned or {})
    cands = sorted(
        findings, key=lambda f: (f.path, f.line_start, f.line_end, f.finding_id)
    )
    if level == "coarse":
        def mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
            return coarse_mergeable(fa, fb)
    else:
        def mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
            return fine_mergeable(fa, fb, sim_fn=sim_fn)

    used: set[str] = set()
    out: list[ClusterProposal] = []
    for i, anchor in enumerate(cands):
        if anchor.finding_id in used or anchor.finding_id in skip:
            continue
        members = [anchor]
        used.add(anchor.finding_id)
        sims: list[float] = []            # 本簇内已并入成员对的相似度（精聚类用）
        for fb in cands[i + 1:]:
            if fb.finding_id in used or fb.finding_id in skip:
                continue
            pair: list[float] = []
            ok = True
            for m in members:
                if not mergeable(m, fb):
                    ok = False
                    break
                if level == "fine":
                    pair.append(float(sim_fn(m, fb)))
            if ok:
                members.append(fb)
                used.add(fb.finding_id)
                sims.extend(pair)
        if len(members) < 2:
            continue                      # 单 finding：无归并语义
        out.append(
            ClusterProposal(
                level=level,
                member_ids=tuple(sorted(m.finding_id for m in members)),
                key_path=anchor.path,
                key_cwe=anchor.cwe,
                key_lang=anchor.lang,
                line_window=(
                    min(m.line_start for m in members),
                    max(m.line_end for m in members),
                ),
                sim_min=min(sims) if sims else None,
            )
        )
    return out


# ── 建簇 / 扩簇 / 拆分（§1.3；clusters/cluster_members 唯一写口）──────

def _clock_now(ledger: KernelLedger):
    return ledger._clock.now()            # 同包内复用注入时钟（蜂与外部无时间源）


def _next_cluster_id(ledger: KernelLedger, date: str) -> str:
    """C-{date}-{seq:06d}：seq = 同日期前缀在账本库内已有最大序号 + 1（D20：
    自增域 = 账本库而非 run——多 run 同库同日期不撞 clusters.id 主键；单 run
    独库时与「run 内自增」逐位同值）。"""
    prefix = f"C-{date}-"
    rows = ledger.conn.execute(
        "SELECT cluster_id FROM clusters WHERE cluster_id LIKE ?", (prefix + "%",)
    ).fetchall()
    max_seq = 0
    for (cid,) in rows:
        try:
            max_seq = max(max_seq, int(str(cid)[len(prefix):]))
        except ValueError:
            continue
    return f"C-{date}-{max_seq + 1:06d}"


def _cluster_members(ledger: KernelLedger, cluster_id: str) -> list[str]:
    """簇现员（finding_id 字典序）。"""
    return [
        r[0]
        for r in ledger.conn.execute(
            "SELECT finding_id FROM cluster_members WHERE cluster_id = ?"
            " ORDER BY finding_id",
            (cluster_id,),
        )
    ]


def _find_extendable_cluster(
    ledger: KernelLedger, proposal: ClusterProposal, members: set[str]
) -> str | None:
    """定位可扩簇/已存在（幂等命中）的同 level 簇。

    规则（确定性）：在与提案同 level 且 key 三元组相同的簇中，取成员集与提案
    成员集相交者——恰一个且其成员 ⊆ 提案成员 → 返回（相等 = 幂等命中，真子集 =
    扩簇）；其余形态（≥2 个相交、或相交但含有提案外成员）返回特殊值：
    - 无相交 → None（建簇）；
    - 幂等/扩簇命中 → 该 cluster_id；
    - 歧义（多簇相交或部分重叠）→ raises ValueError（归并提案跨既有簇边界，
      须先 split 再议；保守拒绝，见 DECISIONS 2026-10-10-CL1）。
    """
    rows = ledger.conn.execute(
        "SELECT cluster_id, key_path, key_cwe, key_lang FROM clusters WHERE level = ?",
        (proposal.level,),
    ).fetchall()
    cands = [
        cid
        for cid, kp, kc, kl in rows
        if kp == proposal.key_path and kc == proposal.key_cwe and kl == proposal.key_lang
    ]
    touching: list[tuple[str, set[str]]] = []
    for cid in sorted(cands):
        existing = set(_cluster_members(ledger, cid))
        if existing & members:
            touching.append((cid, existing))
    if not touching:
        return None
    if len(touching) > 1:
        raise ValueError(
            f"归并提案成员跨 {len(touching)} 个既有 {proposal.level} 簇"
            f"（{sorted(c for c, _ in touching)}）——先 split 再归并（保守拒绝）"
        )
    cid, existing = touching[0]
    if not members >= existing:
        raise ValueError(
            f"提案成员与既有簇 {cid} 部分重叠（簇内有提案外成员"
            f" {sorted(existing - members)}）——扩簇提案须含该簇全部现员"
        )
    return cid


def _insert_cluster_row(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    key_path: str,
    key_cwe: str | None,
    key_lang: str,
    line_window: tuple[int, int],
    level: str,
    embedding_sim: float | None,
    created_at: str,
) -> None:
    ledger.conn.execute(
        "INSERT INTO clusters (cluster_id, key_path, key_cwe, key_lang, line_window,"
        " embedding_sim, level, verdict_tier, verdict_dir, verdict_at, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
        (
            cluster_id,
            key_path,
            key_cwe,
            key_lang,
            f"{line_window[0]}-{line_window[1]}",
            embedding_sim,
            level,
            created_at,
        ),
    )


def _insert_members(
    ledger: KernelLedger,
    cluster_id: str,
    member_ids: Sequence[str],
    merged_at: str,
) -> None:
    """成员行落库并回写 findings.cluster_id（存款归属衔接：占位簇号由内核侧替换）。"""
    for fid in member_ids:
        ledger.conn.execute(
            "INSERT INTO cluster_members (cluster_id, finding_id, merged_at)"
            " VALUES (?, ?, ?)",
            (cluster_id, fid, merged_at),
        )
        # findings.cluster_id 回写经 kernel.dispatch（§6.1 findings 唯一写口模块）
        attach_cluster(ledger, fid, cluster_id, updated_at=merged_at)


def apply_merges(
    ledger: KernelLedger,
    proposals: Sequence[ClusterProposal],
    *,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """建簇/扩簇 + cluster_merge 事件，**同一事务**（SPEC-1 §2 事务边界）。

    - clusters / cluster_members 表写口在本模块（§6.1）；成员行落库同时回写
      findings.cluster_id（占位簇号的内核侧替换，存款归属衔接）；
    - id 生成：cluster_id = f"C-{date}-{seq:06d}"，date=注入时钟当日 YYYYMMDD，
      seq = 同日期前缀在账本库内已有最大序号 + 1（D20）；
    - 幂等键 = (level, frozenset(members))：已存在同 level 簇恰好包含全部 members
      → 零新事件直接跳过（§6.4）；
    - 扩簇：cluster_merge.members = 本次并入集（D5 增量；建簇 = 全部初始成员），
      clusters.line_window 更新为提案并包络；
    - 返回按落库序的全部事件；无变化 → []。
    """
    events: list[LedgerEvent] = []
    with transaction(ledger):
        for prop in proposals:
            if prop.level not in _LEVELS:
                raise ValueError(f"提案 level 非法：{prop.level!r}")
            if not prop.member_ids:
                raise ValueError("提案成员为空")
            members = sorted(set(prop.member_ids))
            existing = _find_extendable_cluster(ledger, prop, set(members))
            if existing is not None:
                cur = set(_cluster_members(ledger, existing))
                if cur == set(members):
                    continue              # 幂等：同 level 簇恰好包含全部 members
                fresh = [m for m in members if m not in cur]
                # 事件先落（ts 为表行时间锚：created_at/merged_at 的重放真源是
                # cluster_merge 事件 ts，见 DECISIONS 2026-10-10-CL3）
                ev = ledger.append(
                    LedgerKind.CLUSTER_MERGE,
                    ClusterMergePayload(
                        cluster_id=existing, members=fresh, level=prop.level
                    ),
                    run_id=run_id,
                )
                _insert_members(ledger, existing, fresh, ev.ts)
                ledger.conn.execute(
                    "UPDATE clusters SET line_window = ? WHERE cluster_id = ?",
                    (f"{prop.line_window[0]}-{prop.line_window[1]}", existing),
                )
                events.append(ev)
                continue
            date = _clock_now(ledger).strftime("%Y%m%d")
            cluster_id = _next_cluster_id(ledger, date)
            ev = ledger.append(
                LedgerKind.CLUSTER_MERGE,
                ClusterMergePayload(
                    cluster_id=cluster_id, members=members, level=prop.level
                ),
                run_id=run_id,
            )
            _insert_cluster_row(
                ledger,
                cluster_id,
                key_path=prop.key_path,
                key_cwe=prop.key_cwe,
                key_lang=prop.key_lang,
                line_window=tuple(prop.line_window),
                level=prop.level,
                embedding_sim=prop.sim_min,
                created_at=ev.ts,
            )
            _insert_members(ledger, cluster_id, members, ev.ts)
            events.append(ev)
    return events


def _member_window(ledger: KernelLedger, member_ids: Sequence[str]) -> tuple[int, int]:
    """成员行窗并包络（findings 行必在：cluster_members.finding_id 外键兜底）。"""
    rows = ledger.conn.execute(
        f"SELECT MIN(line_start), MAX(line_end) FROM findings WHERE id IN "
        f"({', '.join('?' * len(member_ids))})",
        tuple(member_ids),
    ).fetchone()
    return (int(rows[0]), int(rows[1]))


def split_cluster(
    ledger: KernelLedger,
    cluster_id: str,
    groups: Sequence[Sequence[str]],      # ≥2 组，并集须 = 原成员全集
    *,
    actor: str,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """人工拆分（§3.4「错并可人工拆分」）：cluster_split 事件 + 成员表改写，同事务。

    - **拆分零存款**（§3.4 逐字）：已按簇传播的存款一律不回滚（§6.2 无回滚纪律）；
      拆分后各子簇 verdict 投影清空（新簇 verdict_* 列以 NULL 建行——「置空」由
      建行承载；原簇 verdict_* 保留历史投影，见 DECISIONS 2026-10-10-CL2）；
    - groups < 2 或并集 ≠ 原成员全集 → ValueError；
    - 幂等：簇不存在或已拆分（成员数 < 拆分前，此处 = 现员为空）→ ValueError
      （拆分是人工动作，不做静默跳过，§6.4 写侧非法界线）；
    - 每个子簇按其成员全集落一条 cluster_merge 事件（建簇 = 全部初始成员，D5）
      ——成员真源进账本，重放投影可重建（§6.3 单一实现）；
    - 子簇建簇序 = 组内最小 finding_id 字典序（确定性，与调用方给序无关）。
    """
    events: list[LedgerEvent] = []
    with transaction(ledger):
        row = ledger.conn.execute(
            "SELECT key_path, key_cwe, key_lang, level, line_window FROM clusters"
            " WHERE cluster_id = ?",
            (cluster_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"簇不存在：{cluster_id!r}")
        key_path, key_cwe, key_lang, level, _window = row
        cur = _cluster_members(ledger, cluster_id)
        if not cur:
            raise ValueError(f"簇 {cluster_id} 已拆分（现员为空），拒绝重复拆分")
        norm = [sorted(set(g)) for g in groups]
        if len(norm) < 2:
            raise ValueError(f"拆分至少需要 2 组，实际 {len(norm)}")
        union: list[str] = sorted({m for g in norm for m in g})
        if union != cur:
            raise ValueError(
                f"拆分组并集 ≠ 原成员全集：缺 {sorted(set(cur) - set(union))}，"
                f"多 {sorted(set(union) - set(cur))}"
            )
        date = _clock_now(ledger).strftime("%Y%m%d")
        ordered = sorted(norm, key=lambda g: g[0])
        new_ids: list[str] = []
        for g in ordered:
            cid = _next_cluster_id(ledger, date)
            ev = ledger.append(
                LedgerKind.CLUSTER_MERGE,
                ClusterMergePayload(cluster_id=cid, members=list(g), level=str(level)),
                run_id=run_id,
            )
            _insert_cluster_row(
                ledger,
                cid,
                key_path=key_path,
                key_cwe=key_cwe,
                key_lang=key_lang,
                line_window=_member_window(ledger, g),
                level=str(level),
                embedding_sim=None,
                created_at=ev.ts,
            )
            _insert_members(ledger, cid, g, ev.ts)
            events.append(ev)
            new_ids.append(cid)
        ledger.conn.execute(
            "DELETE FROM cluster_members WHERE cluster_id = ?", (cluster_id,)
        )
        events.append(
            ledger.append(
                LedgerKind.CLUSTER_SPLIT,
                ClusterSplitPayload(cluster_id=cluster_id, new_clusters=new_ids),
                run_id=run_id,
            )
        )
    del actor                           # 审计字段：事件由账本 run_id/时钟承载，actor 留调用方审计
    return events


# ── 归属编排入口（§2.1 流水线「粗聚类」步；仿真接通 §0.4）──────────


def _member_facts(ledger: KernelLedger, member_ids: Sequence[str]) -> dict[str, FindingFacts]:
    """从 findings 表行装配成员判据事实面（簇内 all-pairwise 复核用；sim_text 恒空
    ——证据链未建档的世界侧行不参与精聚类判据，fine 级扩簇须由调用方自带事实）。"""
    rows = ledger.conn.execute(
        f"SELECT id, path, line_start, line_end, cwe, lang, family, type FROM findings"
        f" WHERE id IN ({', '.join('?' * len(member_ids))})",
        tuple(member_ids),
    ).fetchall()
    out: dict[str, FindingFacts] = {}
    for fid, path, ls, le, cwe, lang, family, ftype in rows:
        out[str(fid)] = FindingFacts(
            finding_id=str(fid), path=str(path), line_start=int(ls), line_end=int(le),
            cwe=cwe, lang=str(lang), family=str(family), type=ftype, sim_text="",
        )
    missing = [m for m in member_ids if m not in out]
    if missing:
        raise ValueError(f"成员 finding 行缺失：{missing}（归属前须先建档 open_finding）")
    return out


def attribute_findings(
    ledger: KernelLedger,
    facts: Sequence[FindingFacts],
    *,
    level: str = "coarse",
    sim_fn: SimFn | None = None,
    run_id: str | None = None,
) -> dict[str, str]:
    """把一批**新建档** finding 归属到内核簇，返回 finding_id → cluster_id 全映射。

    §2.1 流水线「粗聚类（投票前）/精聚类（投票后）」步的编排入口——提案、判据、
    落簇全部复用本模块既有件，无第二套归并实现：

    1. 提案：:func:`cluster_findings` 在新 finding 内提案（候选字典序 + D2
       all-pairwise；``assigned`` 命中的已归属 finding 直接回读不重复提案）；
    2. 扩簇：未入提案的 finding 对**既有**同 level 簇做 all-pairwise 复核
       （coarse_mergeable/fine_mergeable 逐成员核验，键三元组同者才候选）——
       恰一簇命中 → 扩簇提案（成员 = 该簇现员 ∪ 新 finding，CL1 前件天然成立）；
       ≥2 簇命中 → 保守放弃扩簇、单例落簇（§3.4 宁漏勿错：跨簇归并须先 split，
       静默跨簇或抛错打断编排都更差）；零命中 → 单例落簇（cluster_findings 只出
       ≥2 成员提案，单例由本入口按其 docstring 显式构造）；
    3. 落簇：全部提案一次性交 :func:`apply_merges`（clusters/cluster_members 唯一
       写口 + findings.cluster_id 回写，同事务、幂等键 (level, frozenset(members))）。

    确定性（§6.5）：零随机性；候选/簇/成员全按字典序展开；同 (输入 facts 序,
    账本态) ⇒ 簇号签发序逐位一致。空 facts → {} 零事件。
    """
    if level not in _LEVELS:
        raise ValueError(f"level 必须是 {_LEVELS}，实际 {level!r}")
    uniq: dict[str, FindingFacts] = {}
    for f in facts:
        if f.finding_id in uniq:
            raise ValueError(f"facts 含重复 finding_id：{f.finding_id!r}")
        uniq[f.finding_id] = f
    if not uniq:
        return {}

    assigned: dict[str, str] = {}
    for cid, fid in ledger.conn.execute(
        "SELECT m.cluster_id, m.finding_id FROM cluster_members m"
        " JOIN clusters c ON c.cluster_id = m.cluster_id WHERE c.level = ?"
        " ORDER BY m.finding_id",
        (level,),
    ):
        assigned[str(fid)] = str(cid)

    fresh = [f for f in uniq.values() if f.finding_id not in assigned]
    result = dict(assigned)                # 已归属命中直接回读（重复归属幂等）
    if not fresh:
        return result

    if level == "coarse":
        def mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
            return coarse_mergeable(fa, fb)
    else:
        def mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
            return fine_mergeable(fa, fb, sim_fn=sim_fn)

    proposals: list[ClusterProposal] = list(
        cluster_findings(fresh, level=level, sim_fn=sim_fn)
    )
    covered = {m for p in proposals for m in p.member_ids}

    # 扩簇候选：既有同 level 簇（cluster_id 字典序），成员事实面从 findings 表装配
    existing: dict[str, dict[str, FindingFacts]] = {}
    for (cid,) in ledger.conn.execute(
        "SELECT cluster_id FROM clusters WHERE level = ? ORDER BY cluster_id", (level,)
    ):
        members = _cluster_members(ledger, str(cid))
        if members:
            existing[str(cid)] = _member_facts(ledger, members)

    extensions: dict[str, list[str]] = {}      # 既有簇 id → 本次并入的新 finding
    unattached: list[FindingFacts] = []        # 零命中/跨簇歧义 → 单例落簇
    for f in sorted(
        (f for f in fresh if f.finding_id not in covered),
        key=lambda f: (f.path, f.line_start, f.line_end, f.finding_id),
    ):
        hits = [
            cid
            for cid, members in existing.items()
            if all(mergeable(f, mf) for mf in members.values())
        ]
        if len(hits) == 1:
            extensions.setdefault(hits[0], []).append(f.finding_id)
        else:
            # len(hits) ≥ 2：跨簇歧义 → 保守单例（§3.4 宁漏勿错，不跨簇归并、
            # 不打断编排）；len(hits) == 0：单例（cluster_findings 只出 ≥2 成员
            # 提案，单例由本入口按其 docstring 显式构造）
            unattached.append(f)

    for cid in sorted(extensions):
        members = existing[cid]
        joined = extensions[cid]
        window = (
            min([f.line_start for f in members.values()]
                + [uniq[j].line_start for j in joined]),
            max([f.line_end for f in members.values()]
                + [uniq[j].line_end for j in joined]),
        )
        anchor = next(iter(members.values()))
        proposals.append(
            ClusterProposal(
                level=level,
                member_ids=tuple(sorted(list(members) + joined)),
                key_path=anchor.path,
                key_cwe=anchor.cwe,
                key_lang=anchor.lang,
                line_window=window,
                sim_min=None,
            )
        )
    for f in unattached:                       # 候选序（与扫描序一致）追加单例提案
        proposals.append(
            ClusterProposal(
                level=level,
                member_ids=(f.finding_id,),
                key_path=f.path,
                key_cwe=f.cwe,
                key_lang=f.lang,
                line_window=(f.line_start, f.line_end),
                sim_min=None,
            )
        )

    apply_merges(ledger, proposals, run_id=run_id)
    for f in uniq.values():
        row = ledger.conn.execute(
            "SELECT cluster_id FROM findings WHERE id = ?", (f.finding_id,)
        ).fetchone()
        if row is None or row[0] is None:
            raise ValueError(
                f"finding {f.finding_id!r} 归属未落库（apply_merges 后 cluster_id 为空）"
            )
        result[f.finding_id] = str(row[0])
    return result


# ── 簇级结论传播（§1.4 × §6.5 归属矩阵）──────────────────────────

@dataclass(frozen=True)
class MemberFacts:
    """簇成员的报告者事实（§6.5 系数按角色取）。"""

    finding_id: str
    reporter: str
    reporter_role: str             # "auditor"（×1.0）| "scout"（×SCOUT_X，fast 档 §16.7-1）


@dataclass(frozen=True)
class VerdictContext:
    """归属谱系（DECISIONS 2026-10-10：候选→侦察→finding 谱系由调用方显式装配，
    内核不改 SPEC-1 DDL、不自建谱系表）。"""

    members: tuple[MemberFacts, ...]
    scouts: tuple[str, ...] = ()               # 额外侦察行（非 reporter 的簇标记者）
    votes: tuple[tuple[str, int], ...] = ()    # (reviewer, direction) 评审回校（§6.5 评审行）
    verifier_id: str | None = None             # §6.5 验证行归属（S3 到达时需要）
    s1_factor: float = 1.0                     # S1 行源权重因子（D32 面板相关性补偿；
                                               #  缺省 1.0 = 既有行为逐位不变）


def _source_weight(tier: SignalTier) -> float:
    return {
        SignalTier.S1: S1_SOURCE_W,
        SignalTier.S2: S2_SOURCE_W,
        SignalTier.S3: S3_SOURCE_W,
    }[tier]


def _expansion_rows(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    tier: SignalTier,
    direction: int,
    src_seq: int,
    ctx: VerdictContext,
    params: ParamsConfig,
) -> list[DepositPayload]:
    """§6.5 归属矩阵全行展开（D7 固定序：成员→侦察→评审回校→验证，各自字典序）。

    - 成员行按 finding_id 字典序；reporter_role=scout 走 ×SCOUT_X（§16.7-1），
      auditor ×1.0，其余角色 ValueError（MemberFacts 词表守卫）；
    - taxonomy_path：成员行取该 finding 自身 (family, type)；侦察/评审/验证行取
      锚点成员（finding_id 最小者）的 (family, type)——与 M0 simulator.deposits
      「全行同一路径」口径对齐（§5.2 派生存款标准形）；
    - 评审行与 verdict 同向 + / 反向 −（×REVIEWER_X）；验证行仅 tier=S3 且簇存在
      先验 S2 verdict 时落（同向 +VERIFIER_CONFIRM / 反向 −VERIFIER_CONFLICT）；
    - 每笔携带 src_seq（D6，幂等键三要素之一）。
    """
    members = sorted(ctx.members, key=lambda m: m.finding_id)
    if not members:
        raise ValueError(f"簇 {cluster_id} 的 VerdictContext.members 为空——簇级结论传播需要成员谱系")
    rows_by_finding = {
        r[0]: (r[1], r[2])
        for r in ledger.conn.execute(
            f"SELECT id, family, type FROM findings WHERE id IN "
            f"({', '.join('?' * len(members))})",
            tuple(m.finding_id for m in members),
        )
    }
    missing = [m.finding_id for m in members if m.finding_id not in rows_by_finding]
    if missing:
        raise ValueError(f"成员 finding 行缺失：{missing}（谱系与 findings 表不一致）")

    def tax_of(fid: str) -> list[str]:
        family, ftype = rows_by_finding[fid]
        return [seg for seg in (str(family), ftype) if seg]

    anchor_tax = tax_of(members[0].finding_id)
    source_w = _source_weight(tier)
    if tier is SignalTier.S1:
        # D32：S1 面板相关性补偿（§16.7-6 降权条款定量化）——同模面板信息量打折；
        # S2/S3 不受影响（真值回校无共模问题）。缺省 1.0 时乘法逐位恒等。
        source_w = source_w * float(ctx.s1_factor)
    scout_x = params.deposits.SCOUT_X
    reviewer_x = params.deposits.REVIEWER_X

    def row(agent: str, role: Role, tax: list[str], dir_: int, weight: float,
            finding: str | None) -> DepositPayload:
        return DepositPayload(
            finding=finding,
            cluster=cluster_id,
            agent=agent,
            role=role,
            taxonomy_path=tax,
            signal_tier=tier,
            direction=dir_,
            weight=weight,
            origin=DepositOrigin.PROD,
            src_seq=src_seq,
        )

    out: list[DepositPayload] = []
    # ① 成员报告者行
    for m in members:
        if m.reporter_role == Role.AUDITOR:
            coef = 1.0
            role = Role.AUDITOR
        elif m.reporter_role == Role.SCOUT:
            coef = scout_x
            role = Role.SCOUT
        else:
            raise ValueError(
                f"成员 {m.finding_id} reporter_role 必须是 auditor/scout，实际 {m.reporter_role!r}"
            )
        out.append(
            row(m.reporter, role, tax_of(m.finding_id), direction, source_w * coef,
                m.finding_id)
        )
    # ② 侦察行（额外标记者，字典序）
    for sid in sorted(ctx.scouts):
        out.append(row(sid, Role.SCOUT, anchor_tax, direction, source_w * scout_x, None))
    # ③ 评审回校行（reviewer 字典序；同向 + / 反向 −）
    for reviewer, v_dir in sorted(ctx.votes, key=lambda v: v[0]):
        sign = 1 if v_dir == direction else -1
        out.append(
            row(reviewer, Role.REVIEWER, anchor_tax, sign, source_w * reviewer_x, None)
        )
    # ④ 验证行（仅 S3 且簇存在先验 S2 verdict；§6.5：S3 确认 S2 → +VERIFIER_CONFIRM、
    # 推翻 S2 → −VERIFIER_CONFLICT——「同向/反向」的基准是先验 S2 verdict 方向，与
    # M0 simulator.deposits 的 s3.direction == s2_direction 判式同源）
    if tier is SignalTier.S3:
        s2_dir = _prior_s2_direction(ledger, cluster_id)
        if s2_dir is not None:
            if ctx.verifier_id is None:
                raise ValueError(
                    f"簇 {cluster_id} 存在先验 S2 verdict 但 ctx.verifier_id 缺失"
                    "——无法归属 §6.5 验证行"
                )
            if direction == s2_dir:
                v_dir, v_w = 1, params.deposits.VERIFIER_CONFIRM
            else:
                v_dir, v_w = -1, params.deposits.VERIFIER_CONFLICT
            out.append(
                row(ctx.verifier_id, Role.VERIFIER, anchor_tax, v_dir, v_w, None)
            )
    return out


def _prior_s2_direction(ledger: KernelLedger, cluster_id: str) -> int | None:
    """簇的先验 S2 verdict 方向（成员报告者行承载；无 S2 先验 → None）。

    从 S2 存款的报告者行（finding 非空）读取而非 clusters.verdict_dir 列：S1/S3
    后续传播会覆盖投影列，而「簇曾被 S2 验证过」的事实以存款为真源（append-only）。
    """
    direction: int | None = None
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        p = ev.payload
        if (
            p.get("cluster") == cluster_id
            and p.get("signal_tier") == SignalTier.S2.value
            and p.get("finding") is not None
        ):
            direction = int(p["direction"])
    return direction


def _deposit_already_paid(
    ledger: KernelLedger, cluster_id: str, tier: SignalTier, src_seq: int
) -> bool:
    """幂等键 (cluster, tier, src_seq) 命中判定（§6.4 读侧幂等 → 零新事件）。"""
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        p = ev.payload
        if (
            p.get("cluster") == cluster_id
            and p.get("signal_tier") == tier.value
            and p.get("src_seq") == src_seq
        ):
            return True
    return False


def propagate_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    tier: SignalTier,                              # S1 | S2 | S3
    direction: int,                                # ±1（0 = 无方向，不落任何存款）
    source_seq: int,                               # 源事件 seq（幂等键）
    source_kind: LedgerKind,                       # VERIFY_RESULT | HUMAN_VERDICT | VOTE | FINDING
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """簇 verdict → clusters.verdict_* 投影更新 + §6.5 归属展开 deposit 事件（同事务）。

    - 存款展开序（D7）：成员报告者行（finding_id 字典序；scout ×SCOUT_X）→ 侦察
      行（字典序 ×SCOUT_X）→ 评审回校行（字典序，同向 + / 反向 − ×REVIEWER_X）→
      验证行（仅 tier=S3 且簇存在先验 S2 verdict；verifier_id 缺失 → ValueError）；
    - 每笔派生存款携带 src_seq = source_seq（D6）；
    - verify_error / direction == 0 → 零存款零投影更新（§6.2/§6.3）；
    - 冲突语义（§6.2 逐字）：S3 与 S2/S1 结论冲突时不回滚旧存款，按 S3 方向补记
      更强反向存款；
    - 源事件一致性（D23）：ledger[source_seq] 不存在或 kind ≠ source_kind →
      ValueError；source_kind 仅作派生审计用途，payload 不承载；
    - 幂等键 = (cluster_id, tier, source_seq)：同键重复调用 → 零新事件（§6.4）；
    - clusters.verdict_at = 本次展开**最后一笔**存款的落账 ts（重放折叠同口径——
      deposit 事件即 verdict 投影真源，见 kernel.replay.rebuild_clusters 与
      DECISIONS 2026-10-10-CL3）。
    """
    if tier not in (SignalTier.S1, SignalTier.S2, SignalTier.S3):
        raise ValueError(f"tier 必须是 S1/S2/S3，实际 {tier!r}")
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction 必须 ∈ {{-1,0,1}}，实际 {direction}")
    try:
        source_kind = LedgerKind(source_kind)
    except ValueError:
        raise ValueError(
            f"source_kind 必须是 {[k.value for k in _SOURCE_KINDS]} 之一，实际 {source_kind!r}"
        ) from None
    if source_kind not in _SOURCE_KINDS:
        raise ValueError(
            f"source_kind 必须是 {[k.value for k in _SOURCE_KINDS]} 之一，实际 {source_kind!r}"
        )
    src = ledger.read(from_seq=source_seq, to_seq=source_seq)
    if not src:
        raise ValueError(f"源事件 seq={source_seq} 不存在——幂等键的 src_seq 不可信")
    if src[0].kind is not source_kind:
        raise ValueError(
            f"源事件 seq={source_seq} kind={src[0].kind.value} ≠ 声明 {source_kind.value}"
            "（D23 一致性校验）"
        )
    if direction == 0:
        return []                        # 无方向：零存款零投影更新
    if _deposit_already_paid(ledger, cluster_id, tier, source_seq):
        return []                        # 幂等命中：零新事件
    p = params or load_params()
    events: list[LedgerEvent] = []
    with transaction(ledger):
        payloads = _expansion_rows(
            ledger, cluster_id, tier=tier, direction=direction, src_seq=source_seq,
            ctx=ctx, params=p,
        )
        verdict_at = None
        for dp in payloads:
            ev = ledger.append(LedgerKind.DEPOSIT, dp, run_id=run_id)
            events.append(ev)
            if dp.finding is not None:
                verdict_at = ev.ts      # 成员报告者行的 ts（verdict 投影时间锚，CL3）
        ledger.conn.execute(
            "UPDATE clusters SET verdict_tier = ?, verdict_dir = ?, verdict_at = ?"
            " WHERE cluster_id = ?",
            (tier.value, direction, verdict_at, cluster_id),
        )
    return events


@dataclass(frozen=True)
class RepairReport:
    """propagate_pending 的修复结果：补齐的派生事件 + 谱系缺失被跳过的簇。

    （SPEC-2 §1.4 签名标注 list[LedgerEvent]、正文要求 skipped 列表随返回值——
    两者合取为结构化报告，见 DECISIONS 2026-10-10-CL4。）
    """

    events: tuple[LedgerEvent, ...]
    skipped: tuple[str, ...]


def _vote_direction_from_ledger(ledger: KernelLedger, finding_id: str) -> int:
    """账本 vote 事件 → 该 finding 的票向符号和符号（0 = 平票；修复口径的确定性
    重 derive——票向是账本事实，重放序与落账序同源）。"""
    total = 0
    for ev in ledger.read(kinds=(LedgerKind.VOTE,)):
        if ev.payload.get("finding_id") == finding_id:
            total += int(ev.payload.get("direction", 0))
    return (total > 0) - (total < 0)


def propagate_pending(
    ledger: KernelLedger,
    *,
    lineage: Callable[[str], VerdictContext | None],   # cluster_id → 谱系（harness 提供）
    from_seq: int = 0,
    upto_seq: int | None = None,
    params: ParamsConfig | None = None,
) -> RepairReport:
    """批处理修复口（§6.3 可重放不变量的运行时配套）：扫描窗口内全部
    verify_result / human_verdict / vote 收口（S1）源事件，凡「应派生而未派生」
    的存款（按 (cluster, tier, src_seq) 幂等键比对）逐个补齐。

    - verify_result：verified_real → (S2, +1)；verified_fp → (S2, −1)；
      verify_error → 无存款，跳过；
    - human_verdict：direction ±1 → (S3, direction)；0 → 跳过；
    - finding（state=voted，S1 收口）：方向 = 该 finding 账本 vote 事件票向符号
      和的符号（0 → 跳过）；
    - lineage 返回 None（谱系缺失，如 M0 世界侧未接簇）→ 跳过该簇并保留在返回值
      的 skipped 列表，不报错。
    """
    events: list[LedgerEvent] = []
    skipped: list[str] = []
    done: set[tuple[str, str, int]] = set()
    for ev in ledger.read(from_seq=from_seq, to_seq=upto_seq):
        p = ev.payload
        plan: tuple[str, SignalTier, int, LedgerKind] | None = None
        if ev.kind is LedgerKind.VERIFY_RESULT:
            outcome = p.get("outcome")
            if outcome == "verified_real":
                plan = (str(p["cluster_id"]), SignalTier.S2, 1, LedgerKind.VERIFY_RESULT)
            elif outcome == "verified_fp":
                plan = (str(p["cluster_id"]), SignalTier.S2, -1, LedgerKind.VERIFY_RESULT)
        elif ev.kind is LedgerKind.HUMAN_VERDICT:
            d = int(p.get("direction", 0))
            if d != 0:
                plan = (str(p["cluster_id"]), SignalTier.S3, d, LedgerKind.HUMAN_VERDICT)
        elif ev.kind is LedgerKind.FINDING and p.get("state") == "voted":
            cid = p.get("cluster")
            if cid:
                d = _vote_direction_from_ledger(ledger, str(p["finding_id"]))
                if d != 0:
                    plan = (str(cid), SignalTier.S1, d, LedgerKind.FINDING)
        if plan is None:
            continue
        cid, tier, direction, kind = plan
        key = (cid, tier.value, ev.seq)
        if key in done or _deposit_already_paid(ledger, cid, tier, ev.seq):
            done.add(key)
            continue
        ctx = lineage(cid)
        if ctx is None:
            skipped.append(cid)
            continue
        events.extend(
            propagate_verdict(
                ledger, cid, tier=tier, direction=direction, source_seq=ev.seq,
                source_kind=kind, ctx=ctx, params=params,
            )
        )
        done.add(key)
    return RepairReport(events=tuple(events), skipped=tuple(sorted(set(skipped))))


def apply_human_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    direction: int,                    # +1 真报 | -1 误报 | 0 忽略
    actor: str,
    source: str = "panel",
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """§12 S3 入口（dashboard 复审面板 → 内核）：human_verdict 事件 + 簇内全员
    finding 状态流转 + S3 存款按簇传播（±1.0），同事务。

    - direction=+1 → 全员 CONFIRMED；−1 → REJECTED；**0 = 忽略：落事件、零存款、
      零状态变更**（§12 逐字「忽略 ≠ 误报，零存款，歧义信号不入账」）；
    - 状态流转复用 §2.4 迁移表（终态 finding 再次收到 verdict → ValueError；
      D21：终态守卫先于 direction 分支——direction=0 对终态簇同样 ValueError，
      单事务回滚零落库）；
    - S3 存款经 propagate_verdict(tier=S3)（验证行随之 §6.5）；状态流转以
      kind=finding 事件承载（D9，payload.state=新状态，重放取最后一条）。
    """
    if direction not in (-1, 0, 1):
        raise ValueError(f"direction 必须 ∈ {{-1,0,1}}，实际 {direction}")
    events: list[LedgerEvent] = []
    with transaction(ledger):
        if ledger.conn.execute(
            "SELECT 1 FROM clusters WHERE cluster_id = ?", (cluster_id,)
        ).fetchone() is None:
            raise ValueError(f"簇不存在：{cluster_id!r}")
        members = _cluster_members(ledger, cluster_id)
        if not members:
            raise ValueError(f"簇 {cluster_id} 无成员（已拆分），拒绝裁决")
        states = dict(
            ledger.conn.execute(
                f"SELECT id, state FROM findings WHERE id IN "
                f"({', '.join('?' * len(members))})",
                tuple(members),
            ).fetchall()
        )
        terminal = [
            fid
            for fid, st in states.items()
            if FindingState(st) not in NON_TERMINAL_FINDING_STATES
        ]
        if terminal:
            raise ValueError(
                f"簇 {cluster_id} 存在终态 finding（{sorted(terminal)}）——重复裁决被拒（D21）"
            )
        events.append(
            ledger.append(
                LedgerKind.HUMAN_VERDICT,
                HumanVerdictPayload(
                    cluster_id=cluster_id, direction=direction, actor=actor,
                    source=source,
                ),
                run_id=run_id,
            )
        )
        if direction != 0:
            # 状态流转复用 kernel.dispatch.transition_finding（§6.1 findings 写口
            # 单一实现；迁移表守卫 + kind=finding 事件同事务，D9）。S3 直达边
            # open/voted/inconclusive/verified → confirmed/rejected 均在迁移表内。
            to_state = FindingState.CONFIRMED if direction == 1 else FindingState.REJECTED
            for fid in members:
                events.append(
                    transition_finding(ledger, fid, to_state, run_id=run_id)
                )
            events.extend(
                propagate_verdict(
                    ledger,
                    cluster_id,
                    tier=SignalTier.S3,
                    direction=direction,
                    source_seq=events[0].seq,
                    source_kind=LedgerKind.HUMAN_VERDICT,
                    ctx=ctx,
                    params=params,
                    run_id=run_id,
                )
            )
    return events


# ── S1 评审聚合（§1.5/§10.3）──────────────────────────────────────

@dataclass(frozen=True)
class VoteFacts:
    """一笔评审票的事实面（votes 表行同构）。"""

    reviewer: str
    direction: int
    dim_completeness: float
    dim_reachability: float
    dim_exploitability: float
    dim_fp_suspicion: float


@dataclass(frozen=True)
class VoteConsensus:
    finding_id: str
    direction: int          # −1|0|+1：票向符号和的符号；0 = 平票
    confidence: float       # 多数方向票的四维均值之平均（区间 [0,1]；无多数票 → 0.5）
    disagreement: bool      # 存在反向票 → True（D22：分歧是资产，显式上报不强制仲裁）
    n_votes: int


def aggregate_votes(finding_id: str, votes: Sequence[VoteFacts]) -> VoteConsensus:
    """§10.3 共识规则（纯函数，确定性）：方向 = 票向符号和的符号；平票 → 0。

    confidence = 多数方向票「四维均值」之平均（无多数票 → 0.5）；存在反向票 →
    disagreement=True（D22）。
    """
    votes = list(votes)
    total = sum(int(v.direction) for v in votes)
    direction = (total > 0) - (total < 0)
    if direction == 0:
        confidence = 0.5
    else:
        majority = [v for v in votes if int(v.direction) == direction]
        if majority:
            per_vote = [
                (
                    v.dim_completeness + v.dim_reachability
                    + v.dim_exploitability + v.dim_fp_suspicion
                ) / 4.0
                for v in majority
            ]
            confidence = sum(per_vote) / len(per_vote)
        else:
            confidence = 0.5
    confidence = min(1.0, max(0.0, confidence))
    disagreement = any(int(v.direction) != direction for v in votes)
    return VoteConsensus(
        finding_id=finding_id,
        direction=direction,
        confidence=confidence,
        disagreement=disagreement,
        n_votes=len(votes),
    )


def close_panel(
    ledger: KernelLedger,
    finding_id: str,
    *,
    ctx: VerdictContext,                 # votes 取自 ctx.votes——纯票向；四维取 findings 同源 votes 行
    expected_votes: int | None = None,   # 缺省 = params.panel.SIZE
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """panel 收口（投票后、精聚类/S2 前的必经步，§2.1 流水线序）。

    - findings.confidence = consensus.confidence、disagreement_json 写入（内核写口）；
    - finding 事件 open→voted（§2.4 迁移表）；
    - consensus.direction ≠ 0 → propagate_verdict(tier=S1, source_seq=本收口
      finding 事件的 seq)（S1 存款 = S1_SOURCE_W × 角色系数，§6.5 全行展开）；
      direction == 0 → 只落状态与分歧，零存款；
    - 幂等：finding 已 voted（state ≠ open）→ 零新事件；票数不足 expected_votes
      → ValueError。

    评审票四维事实：ctx.votes 只承载 (reviewer, direction)（§6.5 回校行所需）；
    consensus 的四维分数由本函数从 votes 表行按 reviewer 字典序同源装配——票行
    缺失（未代落账）→ ValueError。
    """
    p = params or load_params()
    expected = int(expected_votes if expected_votes is not None else p.panel.SIZE)
    row = ledger.conn.execute(
        "SELECT state, reporter, cluster_id FROM findings WHERE id = ?", (finding_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"finding 不存在：{finding_id!r}")
    state, _reporter, cluster_id = row
    if state != FindingState.OPEN.value:
        return []                        # 已 voted/终态：读侧幂等，零新事件（§6.4）
    if len(ctx.votes) < expected:
        raise ValueError(
            f"票数不足：{len(ctx.votes)} < expected_votes={expected}"
        )
    vote_rows = {
        r[0]: r[1:]
        for r in ledger.conn.execute(
            "SELECT reviewer, dim_completeness, dim_reachability, dim_exploitability,"
            " dim_fp_suspicion, direction FROM votes WHERE finding_id = ?"
            " ORDER BY reviewer",
            (finding_id,),
        )
    }
    facts: list[VoteFacts] = []
    for reviewer, v_dir in sorted(ctx.votes, key=lambda v: v[0]):
        dims = vote_rows.get(reviewer)
        if dims is None:
            raise ValueError(
                f"reviewer {reviewer!r} 的 votes 表行缺失——panel 收口需票行与谱系同源"
            )
        row_dir = int(dims[4])
        if row_dir != int(v_dir):
            raise ValueError(
                f"reviewer {reviewer!r} 的票向不一致：votes 行 {row_dir} ≠ 谱系 {v_dir}"
                "（谱系与票行必须同源）"
            )
        facts.append(
            VoteFacts(
                reviewer=reviewer,
                direction=row_dir,
                dim_completeness=float(dims[0]),
                dim_reachability=float(dims[1]),
                dim_exploitability=float(dims[2]),
                dim_fp_suspicion=float(dims[3]),
            )
        )
    consensus = aggregate_votes(finding_id, facts)
    events: list[LedgerEvent] = []
    with transaction(ledger):
        now = iso_utc(_clock_now(ledger))
        # §10.3 共识投影经 kernel.dispatch（§6.1 findings 唯一写口模块）
        record_panel_consensus(
            ledger, finding_id, confidence=consensus.confidence,
            disagreement_json=json.dumps(
                {"disagreement": consensus.disagreement}, sort_keys=True
            ),
            updated_at=now,
        )
        # open→voted 复用 kernel.dispatch.transition_finding（迁移表守卫 + kind=
        # finding 事件同事务；§6.1 findings 写口单一实现）。
        events.append(
            transition_finding(ledger, finding_id, FindingState.VOTED, run_id=run_id)
        )
        if consensus.direction != 0 and cluster_id:
            events.extend(
                propagate_verdict(
                    ledger,
                    str(cluster_id),
                    tier=SignalTier.S1,
                    direction=consensus.direction,
                    source_seq=events[-1].seq,
                    source_kind=LedgerKind.FINDING,
                    ctx=ctx,
                    params=p,
                    run_id=run_id,
                )
            )
    return events
