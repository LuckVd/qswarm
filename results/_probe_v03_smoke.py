"""v0.3 第三次重考前的小规模探针（2 蜂 × 各 2 题：1 真题 + 1 雷题）。

目的：在全量 240 行重考（≈11h）之前，验证 key 注入 / 思考模式运行点 /
信封解析 / 判分链路四件事仍然正常（v2 之后代码未动，但重考前值得实证一次）。

纪律（对照 HANDOVER §7）：
- 不写账本、不存款（只走在线调用 + 内存判分）；
- 缓存独立目录 out/probe-v03/cache，与正式运行目录隔离（坏响应不污染正式缓存）；
- 调用路径与 runner.run_entry_cell 在线分支逐字同源：
  render_exam(role, contract, _item_blocks(item, resolve)) @ EXAM_TEMPERATURE
  → _parse_envelope_answer → judge_answer；
- 出卷 = draw_paper(seed=0)（seeding CLI 缺省种子）⇒ 探针题即正式卷面真题；
- 快照读取走 _disk_snapshot_text（盘上直读，内容与工件库摄取同源）。

跑法：
  QSWARM_KEY_ZHIPU="$(cat .zhipu_key)" python3 results/_probe_v03_smoke.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from qswarm.contracts import ExamPurpose
from qswarm.exams.bank import load_bank
from qswarm.exams.judge import judge_answer
from qswarm.exams.paper import draw_paper
from qswarm.exams.roster import expand_candidates
from qswarm.exams.runner import (
    _build_contract,
    _disk_snapshot_text,
    _item_blocks,
    _parse_envelope_answer,
    cache_key,
    cache_put,
)
from qswarm.prompts import EXAM_TEMPERATURE, SchemaRejected, render_exam
from qswarm.supply import UrllibAdapter, load_models

BANK = "seeds/bank.yaml"
MODELS = "configs/models.zhipu.yaml"
FAMILY = "injection"          # v0.3 最大家族（18 real + 10 fp）；v0.2 空壳丑闻家族，重考关注点
CACHE = Path("out/probe-v03/cache")
OUT_JSON = Path("results/_probe_v03_smoke.json")


def main() -> int:
    bank = load_bank(BANK)
    models = load_models(MODELS)
    adapter = UrllibAdapter(models)
    resolve = _disk_snapshot_text(Path(BANK).parent)
    cands = expand_candidates(models, roles=("scout", "auditor"))
    # 展开序 = [scout primary, scout fallback, auditor primary, auditor fallback]
    # 探针取两只 primary：scout=glm-4.5-air（flash 服务）、auditor=glm-5.2（旗舰服务）
    picks = [cands[0], cands[2]]
    print(f"bank_version={bank.bank_version} items={len(bank.items)} family={FAMILY}")
    for c in picks:
        print(f"probe agent: role={c.role} model_ref={c.model_ref} agent_id={c.agent_id}")

    rows: list[dict] = []
    for cand in picks:
        contract = _build_contract(cand.agent_id, cand.role, FAMILY)
        paper = draw_paper(
            bank,
            agent_id=cand.agent_id,
            role=cand.role,
            family=FAMILY,
            purpose=ExamPurpose.ENTRY,
            bank_version=bank.bank_version,
            seed=0,  # 与 seeding CLI 缺省一致 ⇒ 同卷
        )
        real = next(it for it in paper if it.expected == "real")
        fp = next(it for it in paper if it.expected == "fp")
        for item, tag in ((real, "real"), (fp, "fp")):
            rec: dict = {
                "role": cand.role,
                "model_ref": cand.model_ref,
                "agent_id": cand.agent_id,
                "item_id": item.item_id,
                "expected": item.expected,
                "family": FAMILY,
            }
            print(f"[{cand.role}] calling {item.item_id} ({tag}) …", flush=True)
            t0 = time.monotonic()
            try:
                raw = adapter.complete(
                    model_ref=cand.model_ref,
                    messages=render_exam(
                        cand.role,
                        contract=contract,
                        blocks=_item_blocks(item, resolve),
                    ),
                    temperature=EXAM_TEMPERATURE,
                )
            except Exception as exc:  # AdapterError 等：基建失败，如实记录
                rec.update(
                    outcome="infra_fail",
                    error=f"{type(exc).__name__}: {exc}"[:300],
                    elapsed_s=round(time.monotonic() - t0, 1),
                )
                rows.append(rec)
                print(f"  -> INFRA FAIL: {rec['error']}", flush=True)
                continue
            dt = round(time.monotonic() - t0, 1)
            cache_put(CACHE, cache_key(cand.agent_id, FAMILY, item.item_id), raw)
            rec.update(elapsed_s=dt, raw_len=len(raw))
            try:
                parsed, declined = _parse_envelope_answer(cand.role, raw, item.item_id)
            except SchemaRejected as exc:
                rec.update(outcome="schema_fail", detail=str(exc)[:300])
                rows.append(rec)
                print(f"  -> SCHEMA FAIL ({dt:.0f}s, len={len(raw)}): {exc}", flush=True)
                continue
            correct = judge_answer(item, cand.role, parsed)
            rec.update(outcome="ok", declined=declined, correct=correct)
            rows.append(rec)
            print(
                f"  -> ok correct={correct} declined={declined} "
                f"({dt:.0f}s, raw_len={len(raw)})",
                flush=True,
            )

    ok = all(r["outcome"] == "ok" for r in rows)
    n_correct = sum(1 for r in rows if r.get("correct") is True)
    summary = {
        "family": FAMILY,
        "probe": "pass" if ok else "fail",
        "calls": len(rows),
        "ok": sum(1 for r in rows if r["outcome"] == "ok"),
        "schema_fail": sum(1 for r in rows if r["outcome"] == "schema_fail"),
        "infra_fail": sum(1 for r in rows if r["outcome"] == "infra_fail"),
        "correct": n_correct,
        "rows": rows,
    }
    OUT_JSON.write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"\nPROBE {summary['probe'].upper()}: ok={summary['ok']}/{len(rows)} "
        f"schema_fail={summary['schema_fail']} infra_fail={summary['infra_fail']} "
        f"correct={n_correct}/{len(rows)}"
    )
    print(f"detail -> {OUT_JSON}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
