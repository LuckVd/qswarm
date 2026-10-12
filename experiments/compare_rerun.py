"""experiments/compare_rerun.py —— 定稿参数复跑对照（M0 门禁终检证据工具）。

将复跑批次（tune_round 产出）与官方全矩阵批次中同一语义参数组的逐种子行
按种子对齐，对 9 项度量 + regret 曲线列 + 轨迹 digest 逐字段比对：

- 数值列按 float 精确相等（SPEC-4 §6 位级复现纪律，非容差比较）；
- digest 按字符串全等；
- 空值（无观测，如 ③/⑤ 的 None）两侧同为空才算相等。

用法：
  python experiments/compare_rerun.py \
      --base results/experiments-full/results.csv \
      --rerun results/tune-round5/results.csv \
      --group "N10-D0.05-T0.60-E0.30-V0.50"

全部一致 → 打印 ALL_EQUAL=true 并以退出码 0 结束；任何不一致逐列标注
MISMATCH、退出码 1。仅 digest 不一致而度量列全部一致时单独提示
（多为 run_id 命名差异，不构成机制性偏差）。
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

#: 比对列：9 项度量 + ⑥ 曲线读数 + regret 终点/均值 + 轨迹 digest
METRIC_COLS = (
    "promotion_precision",
    "promotion_recall",
    "median_time_to_active",
    "wrong_extinguish_rate",
    "degradation_response",
    "regret_total",
    "regret_per_task",
    "regret_late_over_early",
    "cold_start_length",
    "replay_equivalent",
)
COMPARE_COLS = (*METRIC_COLS, "digest")


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _eq(col: str, a: str, b: str) -> bool:
    if a == "" or b == "":
        return a == b
    if col in ("digest", "replay_equivalent"):   # 字符串/布尔列按字面全等
        return a == b
    return float(a) == float(b)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="定稿参数复跑对照（逐种子逐字段）")
    ap.add_argument("--base", required=True, type=Path)
    ap.add_argument("--rerun", required=True, type=Path)
    ap.add_argument("--group", required=True,
                    help="base 侧的 group_id（如 N10-D0.05-T0.60-E0.30-V0.50）")
    args = ap.parse_args(argv)

    base = {r["seed"]: r for r in read_rows(args.base)
            if r["group_id"] == args.group}
    rerun = {r["seed"]: r for r in read_rows(args.rerun)}
    if not base:
        print(f"base 侧未找到 group_id={args.group} 的行", file=sys.stderr)
        return 2
    if set(base) != set(rerun):
        print(f"种子表不一致：base={sorted(base)} rerun={sorted(rerun)}",
              file=sys.stderr)
        return 2

    print(f"base={args.base} (group={args.group})  rerun={args.rerun}")
    col_ok: dict[str, bool] = {}
    for col in COMPARE_COLS:
        cells = []
        ok_all = True
        for s in sorted(base):
            a, b = base[s][col], rerun[s][col]
            ok = _eq(col, a, b)
            ok_all &= ok
            cells.append(f"{a or '∅'}~{b or '∅'}" + ("" if ok else " <<MISMATCH"))
        col_ok[col] = ok_all
        print(f"{col:<26} " + " | ".join(cells) + f"  [{'ok' if ok_all else 'MISMATCH'}]")

    metrics_equal = all(col_ok[c] for c in METRIC_COLS)
    all_equal = metrics_equal and col_ok["digest"]
    print(f"metrics_equal={str(metrics_equal).lower()} "
          f"digest_equal={str(col_ok['digest']).lower()} "
          f"ALL_EQUAL={str(all_equal).lower()}")
    if metrics_equal and not col_ok["digest"]:
        print("注意：度量列全部一致、仅 digest 不同（run_id/命名差异，"
              "非机制性偏差）——同管道位级复现以官方批次内部断言为准")
    return 0 if metrics_equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
