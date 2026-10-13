"""exams.seeding：考试播种总编排（SPEC-6 §4.2/§4.4/§4.5）。

``seed_entry``（§8.4 逐字落法）::

    for agent in 候选编制（注册序 = agents 表插入序）:
        for family in 家族树（taxonomy.yaml 序）∩ 题库实有家族:
            出卷（§3.1）→ 逐题调用/解析/判分（§3.2–3.4）→ exam_results 行（store）
            → 按 (agent, role, node=family) 写一笔考试存款（§4.3）

- 存款形态（M0 逐位同形）：每格恰一笔 ``ExamDepositPayload``——``agent``、
  ``role``、``taxonomy_path=[family]``、``signal_tier=S1``、``direction``/``weight``
  = ``centered_exam_weight(accuracy, w_low=0.2, w_high=0.5)``、``origin=exam``、
  ``exam_id=f"entry:{agent_id}:{family}"``；经 ``KernelLedger.append`` 落账
  （事件序 = 编排序，确定性）；
- **只供血不孵化**（S8）：M2 播种不写 promotion 事件、不动格子 status——直晋
  通道生产化随 M3（promote_scan 夜批接线）；
- 全部格存款落账后调用 ``kernel.cells.agg_cells`` 折叠；考试存款
  ``T_HALF_DAYS_EXAM=15`` 半衰由聚合器按 ``origin=exam`` 处理（既有语义）；
- **双轨制不破**：考试存款只影响路由；S1 档使其天然不进晋升硬门槛
  n_eff(S2+S3)——机制继承，无新增代码；
- 无效格（§3.4 infra_invalid）零存款零行；整场播种无效格占比 > 10% → 报告
  ``degraded=true``（门禁不通过，§8）；
- 供给质检（§4.5/S11）：agent 全家族真题总正确率 < ``entry_exams.<tier>.min_pass``
  → QC 不通过（agents 留 provisioning + 告警）；通过 → active。质检线是入编
  闸门不是播种开关——存款照写；
- 播种报告（§4.4「组织出生证明」）：``out/seeding_report.{json,md}``，JSON 结构
  钉死（黄金形态回归）；报告中无任何 api_key 或网络端点凭据（§11.6-5）。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from qswarm.contracts import PoolClass, load_taxonomy
from qswarm.kernel import KernelLedger, WallClock, agg_cells, iso_utc
from qswarm.prompts import CONTRACT_VERSION
from qswarm.supply import CandidateAdapter, ModelsConfig, UrllibAdapter, load_models
from qswarm.supply.config import ROLE_TIER

from .bank import SeedBank
from .paper import ENTRY_ITEMS
from .roster import Candidate, apply_qc_decision, expand_candidates, register_roster
from .runner import CellOutcome, SnapshotStore, run_entry_cell
from .judge import entry_qc_pass
from .store import upsert_exam_items

__all__ = ["seed_entry", "write_report", "main"]

#: 无效格占比上限（§4.2 逐字：> 10% → degraded，门禁不通过）。
DEGRADED_RATIO = 0.10

#: 播种角色集（S4 范围裁剪：v0 只覆盖 scout/auditor——reviewer 需 staged finding
#: 工件面（M3）、verifier 需沙箱（依赖序第 8 位））。
SEEDING_ROLES: tuple[str, ...] = ("scout", "auditor")


def _cell_record(cand: Candidate, outcome: CellOutcome) -> dict:
    """CellOutcome → 报告逐格记录（JSON 结构钉死面）。"""
    return {
        "family": outcome.family,
        "status": outcome.status,
        "real_n": outcome.real_n,
        "fp_n": outcome.fp_n,
        "real_correct": outcome.real_correct,
        "fp_rejected": outcome.fp_rejected,
        "accuracy": outcome.accuracy,
        "direction": outcome.direction,
        "weight": outcome.weight,
        "deposit_written": outcome.deposit_written,
        "schema_fail": outcome.schema_fail,
        "infra_fail": outcome.infra_fail,
        "canary_hit": outcome.canary_hit,
        "items": [
            {
                "item_id": it.item_id,
                "expected": it.expected,
                "correct": it.correct,
                "schema_fail": it.schema_fail,
                "infra_fail": it.infra_fail,
                "canary_hit": it.canary_hit,
                "attempts": it.attempts,
            }
            for it in outcome.items
        ],
    }


def _qc_record(cand: Candidate, valid_outcomes: list[CellOutcome], min_pass: float) -> dict:
    """agent 全家族真题总正确率 → QC 判定记录（S11 per-agent 总口径）。"""
    real_n = sum(o.real_n for o in valid_outcomes)
    real_correct = sum(o.real_correct for o in valid_outcomes)
    accuracy = (real_correct / real_n) if real_n else None
    passed = accuracy is not None and entry_qc_pass(accuracy, min_pass)
    return {
        "min_pass": min_pass,
        "accuracy": accuracy,
        "passed": passed,
        "status": "active" if passed else "provisioning",
    }


def build_report(
    *,
    bank: SeedBank,
    seed: int,
    roles: Sequence[str],
    candidates: list[Candidate],
    outcomes: list[CellOutcome],
    qc: dict[str, dict],
    generated_at: str,
) -> dict:
    """播种报告 JSON（§4.4；键集/类型黄金回归——tests/test_exam_runner.py 钉）。"""
    cells_total = len(outcomes)
    invalid = [o for o in outcomes if o.infra_invalid]
    deposits = sum(1 for o in outcomes if o.deposit_written)
    rows = sum(
        1
        for o in outcomes
        if not o.infra_invalid
        for it in o.items
        if it.correct is not None
    )
    invalid_ratio = (len(invalid) / cells_total) if cells_total else 0.0
    agents = []
    for cand in candidates:
        mine = [o for o in outcomes if o.agent_id == cand.agent_id]
        agents.append(
            {
                "agent_id": cand.agent_id,
                "model_ref": cand.model_ref,
                "role": cand.role,
                "tier": cand.tier,
                "pool_class": PoolClass.TEMP.value if cand.role == "scout" else PoolClass.FORMAL.value,
                "contract_version": CONTRACT_VERSION,
                "qc": qc[cand.agent_id],
                "cells": [_cell_record(cand, o) for o in mine],
            }
        )
    return {
        "kind": "seeding_report",
        "spec": "SPEC-6 §4.4（组织出生证明）",
        "contract_version": CONTRACT_VERSION,
        "bank_version": bank.bank_version,
        "entry_items": ENTRY_ITEMS,
        "seed": seed,
        "roles": list(roles),
        "generated_at": generated_at,
        "degraded": invalid_ratio > DEGRADED_RATIO,
        "invalid_cell_ratio": invalid_ratio,
        "qc_alerts": sorted(a for a, rec in qc.items() if not rec["passed"]),
        "totals": {
            "cells": cells_total,
            "invalid_cells": len(invalid),
            "deposits": deposits,
            "exam_result_rows": rows,
            "model_calls": sum(
                it.attempts
                for o in outcomes
                if not o.infra_invalid
                for it in o.items
                if it.correct is not None
            ),
            "schema_fail": sum(o.schema_fail for o in outcomes),
            "infra_fail": sum(o.infra_fail for o in outcomes),
            "canary_hit": sum(o.canary_hit for o in outcomes),
        },
        "agents": agents,
    }


def write_report(report: dict, out_dir: str | Path) -> tuple[Path, Path]:
    """报告落盘：out/seeding_report.json + .md（§4.4）。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "seeding_report.json"
    md_path = out / "seeding_report.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# 播种报告（组织出生证明，SPEC-6 §4.4）",
        "",
        f"- bank_version: {report['bank_version']} · entry_items: {report['entry_items']}"
        f" · seed: {report['seed']}",
        f"- generated_at: {report['generated_at']} · contract_version:"
        f" {report['contract_version']}",
        f"- degraded: {report['degraded']}（无效格占比"
        f" {report['invalid_cell_ratio']:.4f}）",
        f"- totals: {json.dumps(report['totals'], ensure_ascii=False)}",
        "",
        "| agent | role | tier | QC min_pass | accuracy | passed | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for agent in report["agents"]:
        qc = agent["qc"]
        acc = "—" if qc["accuracy"] is None else f"{qc['accuracy']:.4f}"
        lines.append(
            f"| {agent['agent_id']} | {agent['role']} | {agent['tier']}"
            f" | {qc['min_pass']} | {acc} | {qc['passed']} | {qc['status']} |"
        )
    lines += ["", "## 逐格判定", ""]
    for agent in report["agents"]:
        for cell in agent["cells"]:
            lines.append(
                f"- {agent['agent_id']} × {cell['family']}: status={cell['status']}"
                f" real {cell['real_correct']}/{cell['real_n']} fp_reject"
                f" {cell['fp_rejected']}/{cell['fp_n']} acc={cell['accuracy']}"
                f" dir={cell['direction']} w={cell['weight']}"
                f" schema_fail={cell['schema_fail']} infra_fail={cell['infra_fail']}"
                f" canary_hit={cell['canary_hit']} deposit={cell['deposit_written']}"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def seed_entry(
    ledger: KernelLedger,
    bank: SeedBank,
    models: ModelsConfig,
    adapter: CandidateAdapter,
    *,
    seed: int,
    roles: Sequence[str] = SEEDING_ROLES,
    cache_dir: str | Path | None = None,
    store_dir: str | Path | None = None,
    resolve_text=None,
    out_dir: str | Path | None = None,
) -> dict:
    """播种总编排（§4.2）：注册 → 逐格考试/存款 → agg_cells 折叠 → QC → 报告。

    返回报告 dict（``out_dir`` 给定时同步落盘 ``seeding_report.{json,md}``）。"""
    candidates = expand_candidates(models, roles=tuple(roles))
    for cand in candidates:
        if cand.tier not in models.entry_exams:
            raise ValueError(
                f"models.yaml entry_exams 缺档位 {cand.tier!r}"
                f"（agent {cand.agent_id!r} 无入编线，§4.5）"
            )
    register_roster(ledger, candidates)
    # exam_items 行在位保障（§4.3 UPSERT-by-version 幂等；exam_results.item_id 外键
    # 前件——bank loader 带 db 装载时已写，此处兜底重放同批行，内容不进不退）。
    upsert_exam_items(ledger, bank)

    # v0.1：只对题库实有家族播种（taxonomy.yaml 序保确定性；§1.2 v0.1 起 deser 无
    # 考试材料——其格冷启动走生产存款，无考试通道，DECISIONS 同日行）。
    bank_families = set(bank.families())
    families = [f for f in load_taxonomy().families if f in bank_families]
    outcomes: list[CellOutcome] = []
    for cand in candidates:
        for family in families:
            outcomes.append(
                run_entry_cell(
                    ledger,
                    bank,
                    agent_id=cand.agent_id,
                    role=cand.role,
                    model_ref=cand.model_ref,
                    family=family,
                    adapter=adapter,
                    seed=seed,
                    cache_dir=cache_dir,
                    store_dir=store_dir,
                    resolve_text=resolve_text,
                )
            )

    as_of = iso_utc(ledger._clock.now())
    agg_cells(ledger, as_of)

    qc: dict[str, dict] = {}
    for cand in candidates:
        mine = [o for o in outcomes if o.agent_id == cand.agent_id and not o.infra_invalid]
        min_pass = models.entry_exams[cand.tier].min_pass
        record = _qc_record(cand, mine, min_pass)
        qc[cand.agent_id] = record
        apply_qc_decision(ledger, cand.agent_id, passed=record["passed"])

    report = build_report(
        bank=bank,
        seed=seed,
        roles=roles,
        candidates=candidates,
        outcomes=outcomes,
        qc=qc,
        generated_at=as_of,
    )
    if out_dir is not None:
        write_report(report, out_dir)
    return report


# ── CLI（SPEC-5 M2 卷 §5.2 执行口径）────────────────────────────


def main(argv: list[str] | None = None) -> int:
    """``python -m qswarm.exams.seeding``：在线播种 / ``--replay`` 缓存重放。

    在线：``--bank seeds/bank.yaml --models configs/models.yaml --db out/m2-seeding/seeds.db``
    重放：``--replay --cache out/m2-seeding/cache/ --db out/m2-seeding/replay.db``
    （replay 不经网络；缓存未命中快速失败 ReplayCacheMiss，S9）。"""
    parser = argparse.ArgumentParser(
        prog="python -m qswarm.exams.seeding",
        description="考试播种（SPEC-6 §4）：入职考 × 全家族 × 候选编制 → 存款 + 报告",
    )
    parser.add_argument("--bank", required=True, help="题库 bank.yaml 路径")
    parser.add_argument("--models", required=True, help="models.yaml 路径")
    parser.add_argument("--db", required=True, help="播种账本 SQLite 路径")
    parser.add_argument("--out", default="out/m2-seeding", help="报告/工件输出目录")
    parser.add_argument("--store-dir", default=None, help="内容寻址工件库根目录（缺省 <out>/store）")
    parser.add_argument("--cache-dir", default=None, help="响应缓存目录（缺省 <out>/cache）")
    parser.add_argument("--seed", type=int, default=0, help="出卷主种子（缺省 0）")
    parser.add_argument(
        "--roles", default=None,
        help="播种角色逗号清单（M3，SPEC-7 §4：如 'reviewer,verifier' 补播；"
             "缺省 scout,auditor 既有行为零扰动）",
    )
    parser.add_argument("--replay", action="store_true", help="缓存重放模式（不经网络，S9）")
    parser.add_argument("--cache", default=None, help="replay 缓存目录（--replay 时必填）")
    args = parser.parse_args(argv)

    roles: tuple[str, ...] | None = None
    if args.roles is not None:
        roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
        bad = [r for r in roles if r not in ROLE_TIER]
        if not roles or bad:
            raise SystemExit(
                f"--roles 越词表：{bad}（合法值：{sorted(ROLE_TIER)}）"
            )

    out_dir = Path(args.out)
    store_dir = Path(args.store_dir) if args.store_dir else out_dir / "store"
    cache_dir = Path(args.cache_dir) if args.cache_dir else out_dir / "cache"

    models = load_models(args.models)
    ledger = KernelLedger(args.db, clock=WallClock())
    try:
        if args.replay:
            if not args.cache:
                parser.error("--replay 需要 --cache（响应缓存目录）")
            adapter = _ReplayOnlyAdapter(models)
            bank = load_bank_cli(args.bank, ledger, store_dir)
            resolve = SnapshotStore(ledger, store_dir, Path(args.bank).parent).resolver()
            report = seed_entry(
                ledger, bank, models, adapter,
                seed=args.seed, roles=roles if roles is not None else SEEDING_ROLES,
                cache_dir=Path(args.cache),
                resolve_text=resolve, out_dir=out_dir,
            )
        else:
            adapter = UrllibAdapter(models)
            bank = load_bank_cli(args.bank, ledger, store_dir)
            resolve = SnapshotStore(ledger, store_dir, Path(args.bank).parent).resolver()
            report = seed_entry(
                ledger, bank, models, adapter,
                seed=args.seed, roles=roles if roles is not None else SEEDING_ROLES,
                cache_dir=cache_dir,
                resolve_text=resolve, out_dir=out_dir,
            )
        totals = report["totals"]
        print(
            f"SEEDING deposits={totals['deposits']}"
            f" rows={totals['exam_result_rows']}"
            f" candidates={len(report['agents'])}"
            + (" REPLAY" if args.replay else "")
            + (" DEGRADED" if report["degraded"] else " OK")
        )
        return 0
    finally:
        ledger.close()


def load_bank_cli(bank_path: str, ledger: KernelLedger, store_dir: Path) -> SeedBank:
    """CLI 装载形态：带工件摄取与 exam_items UPSERT（§1.3/§4.3）。"""
    from .bank import load_bank

    return load_bank(bank_path, db=ledger, store_dir=store_dir)


class _ReplayOnlyAdapter:
    """replay 模式适配器占位：runner 先查缓存（cache_dir 命中即不经 adapter）。"""

    def __init__(self, models: ModelsConfig) -> None:
        self._models = models

    def complete(self, **kwargs):  # pragma: no cover - 缓存全量时不可达
        raise RuntimeError("replay 模式缓存未命中且不允许网络调用（S9 快速失败）")


if __name__ == "__main__":
    sys.exit(main())
