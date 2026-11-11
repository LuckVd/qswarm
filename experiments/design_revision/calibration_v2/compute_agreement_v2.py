#!/usr/bin/env python3
"""E0 校准一致率计算 v2（R1-T6；HANDOVER §10.2）。

对 v1（`../calibration/compute_agreement.py`，冻结不动）的勘误：

- **边界输入健壮**：零判题 / 空 judge 文件 / 双方无交集 / 零分母 →
  ``status=not_estimable``（不 KeyError / 不除零 / 不默认通过）；
- **映射单一来源**：真值/盲 ID/根因组全部经 ``truth_map_v2.json``
  （``validate_mapping``：每个 sheet ID 都有匹配映射、ID 唯一、枚举合法——
  未知/重复 ID 报错清单，不静默覆盖）；
- **根因组统计**：同组（如共用 vuln 源码的 1/7/8/9 类）不重复计独立根因——
  按组聚合 + 逐 item 结果双面报告；
- **争议项全量报告**：contentious 项单列分层结果，不从分子分母静默剔除；
- 判定字段 v2 双问题：claim（real/refuted/inconclusive）与 poc
  （effective/ineffective/not_applicable/not_runnable）分立计算。

用法：
    python3 compute_agreement_v2.py [--sheet ...] [--judge ...] [--truth ...]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

#: 判定枚举（v2 双问题面）。
CLAIM_ENUM = frozenset({"real", "refuted", "inconclusive"})
POC_ENUM = frozenset({"effective", "ineffective", "not_applicable",
                      "not_runnable"})

#: 预期分歧项（设计面已知解释空间——全量单列报告，不计失败、不剔除）。
CONTENTIOUS = {"E0C-INJ-04", "E0C-TRA-04"}


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def validate_mapping(sheet: list[dict], materials: list[dict],
                     truth: dict | None = None) -> list[str]:
    """映射完整性校验（单一来源面）：返回问题清单（空 = 通过）。

    - sheet ↔ materials ↔ truth 三方 ID 一一对应（经 blind_id 桥接）；
    - 全部 ID 唯一（重复报错，不静默覆盖）；
    - 判定枚举合法；truth 缺项/未知 ID 报错。"""
    problems: list[str] = []
    if truth is None:
        tpath = HERE / "truth_map_v2.json"
        truth = json.loads(tpath.read_text(encoding="utf-8"))["items"] \
            if tpath.exists() else {}
    sheet_ids = [str(r.get("item_id")) for r in sheet]
    mat_blinds = [str(r.get("blind_id")) for r in materials]
    if len(set(sheet_ids)) != len(sheet_ids):
        problems.append(f"sheet item_id 重复：{len(sheet_ids) - len(set(sheet_ids))} 条")
    if len(set(mat_blinds)) != len(mat_blinds):
        problems.append(f"materials blind_id 重复："
                        f"{len(mat_blinds) - len(set(mat_blinds))} 条")
    for iid in sheet_ids:
        if iid not in truth:
            problems.append(f"sheet ID 无 truth 映射：{iid}")
    truth_blinds = {t["blind_id"] for t in truth.values()}
    for b in mat_blinds:
        if b not in truth_blinds:
            problems.append(f"materials blind_id 无 truth 映射：{b}")
    for r in sheet:
        c = r.get("owner_claim_judgment")
        if c is not None and c not in CLAIM_ENUM:
            problems.append(f"claim 判定越枚举：{r.get('item_id')}={c!r}")
        p = r.get("owner_poc_judgment")
        if p is not None and p not in POC_ENUM:
            problems.append(f"poc 判定越枚举：{r.get('item_id')}={p!r}")
    return problems


def _rate(agreed: int, total: int) -> float | str:
    return round(agreed / total, 4) if total else "not_estimable"


def compute_agreement(sheet: list[dict], judge: dict,
                      truth: dict | None = None,
                      *, contentious: frozenset[str] = frozenset()) -> dict:
    """三方一致率（负责人 × 跨族裁判 × 设计真值）。

    边界：零判题 / 空输入 / 无交集 / 零分母 → ``status=not_estimable``
    （结构化原因，不崩溃）；争议项全量报告（分层计数 + 逐 item），不剔除。"""
    if truth is None:
        tpath = HERE / "truth_map_v2.json"
        truth = json.loads(tpath.read_text(encoding="utf-8"))["items"] \
            if tpath.exists() else {}
    judged = [r for r in sheet if r.get("owner_claim_judgment")]
    if not judged:
        return {
            "status": "not_estimable",
            "reason": "zero_owner_judgments",
            "n_judged": 0,
        }
    owner = {str(r["item_id"]): str(r["owner_claim_judgment"]) for r in judged}
    # 裁判侧：blind_id → verdict（无 judge 文件 = 空 dict → 裁判面 not_estimable）
    common = [iid for iid in owner if iid in truth
              and truth[iid]["blind_id"] in judge]
    out: dict = {
        "status": "ok",
        "n_items": len(sheet),
        "n_judged": len(judged),
        "owner_vs_truth": None,
        "judge_vs_owner": None,
        "judge_vs_truth": None,
        "root_cause_groups": {},
        "per_item": [],
        "contentious": {
            "items": sorted(iid for iid in owner if iid in contentious),
            "note": "预期分歧项全量单列（不计失败、不剔除）",
        },
    }
    # ① 负责人 × 设计真值（尺子校准面）
    agreeable = [iid for iid in owner
                 if iid in truth and truth[iid].get("design_truth_claim")
                 in CLAIM_ENUM]
    hits = [iid for iid in agreeable
            if owner[iid] == truth[iid]["design_truth_claim"]]
    out["owner_vs_truth"] = {
        "n": len(agreeable),
        "agreed": len(hits),
        "rate": _rate(len(hits), len(agreeable)),
    }
    # ② 裁判 × 负责人 / ③ 裁判 × 真值（交集为零 → not_estimable，不默认通过）
    if not judge:
        out["judge_vs_owner"] = {"n": 0, "agreed": 0, "rate": "not_estimable",
                                 "reason": "empty_judge_file"}
        out["judge_vs_truth"] = dict(out["judge_vs_owner"])
    else:
        jo_common = [iid for iid in common]
        if not jo_common:
            out["judge_vs_owner"] = {"n": 0, "agreed": 0,
                                     "rate": "not_estimable",
                                     "reason": "zero_overlap"}
        else:
            jo_hits = [i for i in jo_common
                       if judge[truth[i]["blind_id"]] == owner[i]]
            out["judge_vs_owner"] = {"n": len(jo_common),
                                     "agreed": len(jo_hits),
                                     "rate": _rate(len(jo_hits), len(jo_common))}
        jt_common = [i for i in jo_common
                     if truth[i].get("design_truth_claim") in CLAIM_ENUM]
        if not jt_common:
            out["judge_vs_truth"] = {"n": 0, "agreed": 0,
                                     "rate": "not_estimable",
                                     "reason": "zero_overlap"}
        else:
            jt_hits = [i for i in jt_common
                       if judge[truth[i]["blind_id"]]
                       == truth[i]["design_truth_claim"]]
            out["judge_vs_truth"] = {"n": len(jt_common),
                                     "agreed": len(jt_hits),
                                     "rate": _rate(len(jt_hits), len(jt_common))}
    # ④ 根因组聚合（同组不重复计独立根因；组内逐 item 结果保留）
    groups: dict[str, list[str]] = {}
    for iid in owner:
        if iid in truth:
            groups.setdefault(truth[iid]["root_cause_group"], []).append(iid)
    for g, members in sorted(groups.items()):
        g_labeled = [i for i in members if i in agreeable]
        g_hits = [i for i in g_labeled
                  if owner[i] == truth[i]["design_truth_claim"]]
        out["root_cause_groups"][g] = {
            "n_items": len(members),
            "agreed": len(g_hits),
            "rate": _rate(len(g_hits), len(g_labeled)),
            "members": sorted(members),
        }
    # ⑤ 逐 item（争议与非争议全量；judge 缺席如实 None）
    for iid in sorted(owner):
        t = truth.get(iid, {})
        out["per_item"].append({
            "item_id": iid,
            "owner": owner[iid],
            "judge": judge.get(t.get("blind_id", "")),
            "truth": t.get("design_truth_claim"),
            "group": t.get("root_cause_group"),
            "contentious": iid in contentious,
            "poc_judgment": next(
                (r.get("owner_poc_judgment") for r in sheet
                 if str(r.get("item_id")) == iid), None),
        })
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", type=Path, default=HERE / "judging_sheet_v2.jsonl")
    ap.add_argument("--judge", type=Path, default=HERE / "judge_results_v2.jsonl")
    ap.add_argument("--truth", type=Path, default=HERE / "truth_map_v2.json")
    ap.add_argument("--threshold", type=float, default=None)
    args = ap.parse_args(argv)

    sheet = _load(args.sheet) if args.sheet.exists() else []
    judge: dict[str, str] = {}
    if args.judge.exists():
        judge = {str(r["blind_id"]): str(r.get("verdict"))
                 for r in _load(args.judge)}
    truth_doc = json.loads(args.truth.read_text(encoding="utf-8")) \
        if args.truth.exists() else {}
    truth = truth_doc.get("items", {})
    problems = validate_mapping(sheet, _load(HERE / "judge_materials_v2.jsonl")
                                if (HERE / "judge_materials_v2.jsonl").exists()
                                else [], truth)
    if problems:
        print(json.dumps({"status": "invalid_mapping", "problems": problems},
                         ensure_ascii=False, indent=2))
        return 1
    result = compute_agreement(sheet, judge, truth, contentious=CONTENTIOUS)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
