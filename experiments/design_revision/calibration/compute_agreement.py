#!/usr/bin/env python3
"""E0 校准一致率计算（D46 校准门；三方：负责人人工判 × 跨族裁判 × 设计真值）。

用法：
    python3 compute_agreement.py [--sheet ...] [--judge ...] [--threshold 0.85]

输入：
- judging_sheet.jsonl —— 负责人判定（owner_judgment 已填；real/refuted/inconclusive）
- judge_results.jsonl —— 跨族裁判判定（blind_id/verdict；供给批准后产生，可缺省）
- 设计真值 —— 夹具项内嵌于本脚本（NINE_CLASSES 设计面）；题库项按答案键主张
  对照（owner 判 real=同意键 / refuted=否定键）。

输出：三方一致率 + 分族明细 + 未判项清单；--threshold 给出达标判定
（阈值正式值 pilot 前由负责人冻结——D46；本工具只计算不裁决）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# 夹具设计真值（与生成 judging_sheet 的 KLASS_MAP 单一源同形；变更须同步）
FIXTURE_TRUTH = {
    **{f"E0C-INJ-{k:02d}": t for k, t in {
        1: "real", 2: "refuted", 3: "refuted", 4: "real", 5: "refuted",
        6: "refuted", 7: "real", 8: "real", 9: "real"}.items()},
    **{f"E0C-TRA-{k:02d}": t for k, t in {
        1: "real", 2: "refuted", 3: "refuted", 4: "real", 5: "refuted",
        6: "refuted", 7: "real", 8: "real", 9: "real"}.items()},
}
#: 预期分歧项（设计面已知存在解释空间——一致率统计单列，不计失败）
CONTENTIOUS = {"E0C-INJ-04", "E0C-TRA-04"}


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _blind_id(item_id: str) -> str:
    return "J-" + hashlib.sha256(item_id.encode()).hexdigest()[:10]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", type=Path, default=HERE / "judging_sheet.jsonl")
    ap.add_argument("--judge", type=Path, default=HERE / "judge_results.jsonl")
    ap.add_argument("--threshold", type=float, default=None,
                    help="达标阈值（正式值 pilot 前负责人冻结；只计算不裁决）")
    args = ap.parse_args(argv)

    sheet = _load(args.sheet)
    judge: dict[str, str] = {}
    if args.judge.exists():
        judge = {r["blind_id"]: str(r["verdict"]) for r in _load(args.judge)}

    judged = [r for r in sheet if r.get("owner_judgment")]
    n_total, n_judged = len(sheet), len(judged)
    agree_truth = agree_truth_core = n_truth = 0
    agree_owner = n_both = 0
    per_family: dict[str, dict] = {}
    for r in judged:
        oid, ans = r["item_id"], str(r["owner_judgment"]).lower()
        fam = str(r.get("family", "unknown"))
        f = per_family.setdefault(fam, {"n": 0, "agree_truth": 0})
        f["n"] += 1
        if r.get("source") == "fixture":
            truth = FIXTURE_TRUTH[oid]
        else:                      # 题库项：答案键主张 real → owner real=同意
            truth = "real"
        n_truth += 1
        hit = ans == truth
        agree_truth += hit
        f["agree_truth"] += hit
        if oid not in CONTENTIOUS:
            agree_truth_core += hit
        jv = judge.get(_blind_id(oid))
        if jv is not None:
            n_both += 1
            agree_owner += (jv == ans)

    print(f"判定进度：{n_judged}/{n_total}")
    if not n_judged:
        print("（尚无负责人判定——填 judging_sheet.jsonl 的 owner_judgment 后重跑）")
        return 0
    core_n = n_truth - len([r for r in judged if r["item_id"] in CONTENTIOUS])
    print(f"负责人 × 设计真值 一致率：{agree_truth}/{n_truth}"
          f" = {agree_truth / n_truth:.3f}")
    print(f"  剔除预期分歧项（类别 4 不可达题）：{agree_truth_core}/{core_n}"
          f" = {agree_truth_core / core_n:.3f}")
    for fam, f in sorted(per_family.items()):
        print(f"  - {fam}: {f['agree_truth']}/{f['n']}")
    if judge:
        print(f"裁判 × 负责人 一致率（校准门主指标）：{agree_owner}/{n_both}"
              f" = {agree_owner / n_both:.3f}")
        if args.threshold is not None:
            ok = agree_owner / n_both >= args.threshold
            print(f"  阈值 {args.threshold}：{'达标' if ok else '未达标'}"
                  "（未过换裁判或加人工——D46；本输出不构成裁决）")
    else:
        print("（judge_results.jsonl 缺省——跨族裁判供给批准后产生，D47）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
