#!/usr/bin/env python3
"""v2 播种成绩离线重判（修正答案键；不重跑考试）。

输入：out/m2-seeding-v2/{seeds.db,cache/} + results/_answers_key_audit.json
规则（docs/answers_key_audit_20261012.md §1）：
  BROKEN_KEY   （7 道 juliet 空壳）：真值=无漏洞 → decline/suspicious=false 记对，
               产出 finding/suspicious=true 记错；
  BROKEN_FAMILY（4 道解压炸弹/DoS 家族键不可达）：从真题分母剔除；
  其余 18 题：沿用生产判分（exam_results.correct）。
QC（§4.5）：per-agent 全家族真题总正确率 ≥ entry_exams.<tier>.min_pass（0.8）。
输出：results/_v2_corrected_scores.{json,md}
"""
from __future__ import annotations

import glob
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "out/m2-seeding-v2/seeds.db"
CACHE = ROOT / "out/m2-seeding-v2/cache"
AUDIT = json.loads((ROOT / "results/_answers_key_audit.json").read_text())
VERDICTS = AUDIT["items"]
MIN_PASS = {"cheap": 0.8, "strong": 0.8}


def cache_key(agent_id: str, family: str, item_id: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for part in (agent_id, family, item_id):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def load_answer(agent_id: str, family: str, item_id: str) -> dict | None:
    p = CACHE / f"{cache_key(agent_id, family, item_id)}.txt"
    if not p.exists():
        return None
    try:
        sheet = json.loads(p.read_text())
    except json.JSONDecodeError:
        return None
    for a in sheet.get("answers", []):
        if a.get("item_id") == item_id:
            return a
    return None


def rejudge(row: tuple, answer: dict | None) -> tuple[int, int, str]:
    """返回 (v2_correct, corrected_correct, note)。row=(agent,node,item,correct)。"""
    _, node, item, correct = row
    verdict = VERDICTS.get(item, {}).get("verdict")
    if verdict == "BROKEN_FAMILY":
        return correct, 0, "excluded"          # 从分母剔除（corrected 值不参与合计）
    if verdict != "BROKEN_KEY":
        return correct, correct, ""
    # 真值=无漏洞：decline/未起疑 记对；产出 finding/起疑 记错
    if answer is None:
        return correct, correct, "no-cache(维持原判)"
    out = answer.get("output")
    if answer.get("declined") or out is None:
        return correct, 1, "declined→对"
    if "suspicious" in out:                     # scout 信封
        return correct, int(not out["suspicious"]), "scout起疑翻转" if correct != int(not out["suspicious"]) else "scout维持"
    return correct, 0, "finding→错"             # auditor 信封：产出 finding 即错


def main() -> int:
    con = sqlite3.connect(DB)
    cur = con.cursor()
    items = {
        r[0]: {"expected": r[1], "planted": r[2]}
        for r in cur.execute("SELECT item_id, expected, planted FROM exam_items")
    }
    rows = list(cur.execute(
        "SELECT agent_id, node, item_id, correct FROM exam_results ORDER BY agent_id, item_id"
    ))

    per_agent = defaultdict(lambda: {"v2": [0, 0], "corr": [0, 0], "flips": [], "excluded": 0})
    for row in rows:
        agent, node, item, correct = row
        if items[item]["expected"] != "real":
            continue  # QC 只看真题；fp 侧键未受影响（审计 §5）
        answer = load_answer(agent, node, item)
        v2c, cc, note = rejudge(row, answer)
        agg = per_agent[agent]
        agg["v2"][0] += v2c
        agg["v2"][1] += 1
        if note == "excluded":
            agg["excluded"] += 1
            continue
        agg["corr"][0] += cc
        agg["corr"][1] += 1
        if cc != v2c:
            agg["flips"].append({"item": item, "v2": v2c, "corrected": cc, "note": note})

    report = {"source": "out/m2-seeding-v2", "audit": AUDIT["audit_id"], "agents": {}}
    print(f"{'agent':<34} {'v2账面':>10} {'修正':>10} {'QC(0.8)':>8} 翻转明细")
    for agent in sorted(per_agent):
        agg = per_agent[agent]
        v2_acc = agg["v2"][0] / agg["v2"][1]
        c_acc = agg["corr"][0] / agg["corr"][1] if agg["corr"][1] else None
        tier = "strong" if "strong" in agent else "cheap"
        qc_v2 = v2_acc >= MIN_PASS[tier]
        qc_c = c_acc is not None and c_acc >= MIN_PASS[tier]
        report["agents"][agent] = {
            "v2": {"correct": agg["v2"][0], "n": agg["v2"][1], "accuracy": round(v2_acc, 4)},
            "corrected": {"correct": agg["corr"][0], "n": agg["corr"][1],
                          "accuracy": round(c_acc, 4) if c_acc is not None else None,
                          "excluded_n": agg["excluded"]},
            "qc_v2": qc_v2, "qc_corrected": qc_c, "flips": agg["flips"],
        }
        print(f"{agent:<34} {agg['v2'][0]}/{agg['v2'][1]}={v2_acc:.3f} "
              f"{agg['corr'][0]}/{agg['corr'][1]}={c_acc if c_acc is None else round(c_acc, 3)} "
              f"{'✅' if qc_c else '❌':>6}  {json.dumps(agg['flips'], ensure_ascii=False)}")

    out_json = ROOT / "results/_v2_corrected_scores.json"
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"\n→ {out_json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
