"""kernel.bootstrap：生产账本初始化——空库建账 + 播种库一次性幂等导入（SPEC-7 §3.1/D28）。

口径（D28 负责人拍板：**新建空账本 + bootstrap 导入**，生产/实验物理隔离）：

- 生产账本 = 全新空账本（schema 同源 ``contracts.schema_sql()``），与实验/播种库
  物理隔离——本模块从不写种子库（种子库以 ``mode=ro`` URI 打开，误写即
  ``sqlite3.OperationalError``）；
- 启动导入（一次性、幂等）：①编制行（agents/hats，全字段逐字复制）②20 笔考试
  存款事件（append-only 顺序重放：payload 逐字复制、seq 重新编配、``src_seq``
  记原库 seq 溯源——M1 既有可选字段，聚合器不读、历史语义零扰动）；
- 幂等（读侧）：agents 同 id 同字段跳过/字段不等 ValueError（roster 同口径）；
  存款以 ``(origin=exam, src_seq)`` 查重跳过——二次导入零重复行零重复事件
  （M3 卷 C 组判据）；
- 开工前校验（§3.1 逐字）：4 agent status=active 且考试存款数 ≥ 20，否则
  :func:`verify_ready` 拒绝开工（防空账本启动）；常量模块级承载（spec 常量
  纪律，同 EXAM_DEADLINE_S，不进 params）。

导入器的行为 = SPEC-2 append-only 纪律下的纯重放：除 ``src_seq`` 溯源注记外
不改写任何载荷字段（deposit 载荷经 DepositPayload 基类校验落库——exam_id 不入
payload_json 为 M0 既定形态，DECISIONS M2 实现期行）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import LedgerKind
from qswarm.kernel.ledger import KernelClock, KernelLedger, WallClock, transaction

__all__ = [
    "MIN_ACTIVE_AGENTS",
    "MIN_EXAM_DEPOSITS",
    "BootstrapReport",
    "init_prod_ledger",
    "verify_ready",
]

#: 开工前最小 active 编制（§3.1 逐字：4 agent status=active）。
MIN_ACTIVE_AGENTS = 4

#: 开工前最小考试存款笔数（§3.1 逐字：考试存款数 ≥ 20）。
MIN_EXAM_DEPOSITS = 20


@dataclass
class BootstrapReport:
    """导入报告（幂等对账面；JSON 可序列化）。"""

    seed_db: str
    prod_db: str
    agents_imported: int = 0
    agents_skipped: int = 0
    hats_imported: int = 0
    deposits_imported: int = 0
    deposits_skipped: int = 0
    seed_exam_deposit_seqs: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "seed_db": self.seed_db,
            "prod_db": self.prod_db,
            "agents_imported": self.agents_imported,
            "agents_skipped": self.agents_skipped,
            "hats_imported": self.hats_imported,
            "deposits_imported": self.deposits_imported,
            "deposits_skipped": self.deposits_skipped,
            "seed_exam_deposit_seqs": list(self.seed_exam_deposit_seqs),
        }


def _open_seed_ro(db_path: str | Path) -> sqlite3.Connection:
    """种子库只读连接（mode=ro URI；文件缺失 → ValueError）。

    播种原库只读不触碰（§3.1 逐字）——ro 打开使误写物理失败，而非纪律约定。"""
    path = Path(db_path)
    if not path.exists():
        raise ValueError(f"种子账本不存在：{path}")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = 1")
    return conn


def _import_agents(prod: KernelLedger, seed: sqlite3.Connection, report: BootstrapReport) -> None:
    """agents + hats 全字段导入（写口纪律：行写入经 exams.roster 唯一写口）。"""
    agents = seed.execute(
        "SELECT agent_id, model_ref, contract_version, status, created_at,"
        " last_active_at FROM agents ORDER BY agent_id"
    ).fetchall()
    hats = seed.execute(
        "SELECT agent_id, role, pool_class FROM hats ORDER BY agent_id, role"
    ).fetchall()
    from qswarm.exams.roster import import_roster_rows  # 局部导入：避免 kernel↔exams 环

    report.agents_imported, report.agents_skipped = import_roster_rows(
        prod, agents=[tuple(r) for r in agents], hats=[tuple(r) for r in hats]
    )
    report.hats_imported = len(hats)


def _import_exam_deposits(
    prod: KernelLedger, seed: sqlite3.Connection, report: BootstrapReport
) -> None:
    """考试存款事件顺序重放（payload 逐字 + src_seq 溯源；幂等键 (origin=exam, src_seq)）。"""
    existing_src = {
        int(ev.payload.get("src_seq") or 0)
        for ev in prod.read(kinds=(LedgerKind.DEPOSIT,))
        if ev.payload.get("origin") == "exam"
    }
    rows = seed.execute(
        "SELECT seq, run_id, payload_json FROM ledger WHERE kind = 'deposit' ORDER BY seq"
    ).fetchall()
    for seq, run_id, payload_json in rows:
        payload = json.loads(payload_json)
        if payload.get("origin") != "exam":
            continue                          # 播种库只承载考试存款（防御性过滤）
        report.seed_exam_deposit_seqs.append(int(seq))
        if int(seq) in existing_src:
            report.deposits_skipped += 1
            continue
        prod.append(
            "deposit",
            {**payload, "src_seq": int(seq)},
            run_id=run_id,
        )
        report.deposits_imported += 1
    if report.deposits_imported == 0 and not report.seed_exam_deposit_seqs:
        # 空播种库（零考试存款）如实放行——verify_ready 会在开工前拦截。
        return


def init_prod_ledger(
    prod_db_path: str | Path,
    *,
    seed_db_path: str | Path,
    clock: KernelClock | None = None,
) -> BootstrapReport:
    """生产账本引导（D28）：空 schema 建库 + 编制/考试存款一次性幂等导入。

    - ``prod_db_path`` 已存在且含非空账本 → 幂等续跑（本函数只增不改，二次调用
      零重复）；
    - 返回 :class:`BootstrapReport`（对账面；调用方落盘/进交付报告）。"""
    report = BootstrapReport(seed_db=str(seed_db_path), prod_db=str(prod_db_path))
    seed = _open_seed_ro(seed_db_path)
    prod = KernelLedger(prod_db_path, clock=clock if clock is not None else WallClock())
    try:
        with transaction(prod):
            _import_agents(prod, seed, report)
        with transaction(prod):
            _import_exam_deposits(prod, seed, report)
    finally:
        seed.close()
        prod.close()
    return report


def verify_ready(
    ledger: KernelLedger,
    *,
    min_active: int = MIN_ACTIVE_AGENTS,
    min_deposits: int = MIN_EXAM_DEPOSITS,
) -> dict:
    """开工前校验（§3.1 逐字：4 active + ≥20 考试存款，否则拒绝开工）。

    返回对账 dict（active 编制清单 + 考试存款数）；不达标 → RuntimeError
    （防空账本/欠编启动——门禁语义 fail-closed）。"""
    agents = ledger.conn.execute(
        "SELECT agent_id, status FROM agents ORDER BY agent_id"
    ).fetchall()
    active = [a for a, s in agents if s == "active"]
    n_exam_deposits = sum(
        1
        for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,))
        if ev.payload.get("origin") == "exam"
    )
    if len(active) < min_active or n_exam_deposits < min_deposits:
        raise RuntimeError(
            f"生产账本未就绪：active 编制 {len(active)} < {min_active} 或"
            f" 考试存款 {n_exam_deposits} < {min_deposits}"
            "（先 kernel.bootstrap.init_prod_ledger 导入，§3.1）"
        )
    return {"active": sorted(active), "exam_deposits": n_exam_deposits}
