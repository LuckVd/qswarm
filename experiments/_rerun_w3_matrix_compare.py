"""experiments/_rerun_w3_matrix_compare.py —— 全矩阵逐组复跑对照（compare_rerun 同语义的全矩阵版）。

experiments/compare_rerun.py 面向「单组复跑批 vs 官方批」设计（rerun 侧按 seed
取行、不滤组）。本工具把同一比对语义（9 度量 + regret 曲线列 + digest，float
精确相等/字面全等/空值两侧同空）整体应用于「全矩阵 vs 全矩阵」：按
(group_id, seed) 对齐两侧 486 个主矩阵 run，逐列比对并输出：

  - results/_rerun_w3_evidence/matrix_compare.csv   逐 run 逐列差异清单；
  - 逐组汇总：n_diff_cols、diff 列名、该组新批是否仍 9/9 达标（group_targets）；
  - 总汇总：差异 run 数 / 差异列分布 / digest 差异数。
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "results" / "experiments-full" / "results.csv"
OLD = ROOT / "results" / "experiments-full-m0gate-20261009" / "results.csv"
GT = ROOT / "out" / "group_targets.csv"
EVID = ROOT / "results" / "_rerun_w3_evidence"

METRIC_COLS = (
    "promotion_precision", "promotion_recall", "median_time_to_active",
    "wrong_extinguish_rate", "degradation_response", "regret_total",
    "regret_per_task", "regret_late_over_early", "cold_start_length",
    "replay_equivalent",
)
COMPARE_COLS = (*METRIC_COLS, "digest")


def _eq(col: str, a: str, b: str) -> bool:
    if a == "" or b == "":
        return a == b
    if col in ("digest", "replay_equivalent"):
        return a == b
    return float(a) == float(b)


def main() -> int:
    def load(path: Path) -> dict[tuple[str, str], dict[str, str]]:
        with path.open(newline="", encoding="utf-8") as f:
            return {(r["group_id"], r["seed"]): r for r in csv.DictReader(f)
                    if r["experiment"] == "matrix" and r["arm"] == "base"}

    old, new = load(OLD), load(NEW)
    assert set(old) == set(new) and len(old) == 486, \
        f"键集不一致：old={len(old)} new={len(new)}"

    with GT.open(newline="", encoding="utf-8") as f:
        qualifies = {r["group_id"]: r["qualifies"] == "True" and r["n_pass"] == "9"
                     for r in csv.DictReader(f)
                     if r["experiment"] == "matrix" and r["arm"] == "base"}

    EVID.mkdir(parents=True, exist_ok=True)
    diff_rows_path = EVID / "matrix_compare.csv"
    group_path = EVID / "matrix_compare_groups.csv"
    n_run_diff = n_digest_diff = 0
    col_hits: dict[str, int] = {c: 0 for c in COMPARE_COLS}
    groups_diff: dict[str, list[str]] = {}
    with diff_rows_path.open("w", newline="", encoding="utf-8") as df, \
         group_path.open("w", newline="", encoding="utf-8") as gf:
        dw = csv.writer(df, lineterminator="\n")
        dw.writerow(["group_id", "seed", "column", "old", "new"])
        gw = csv.writer(gf, lineterminator="\n")
        gw.writerow(["group_id", "n_diff_runs", "diff_cols", "still_9of9"])
        for gid in sorted({g for g, _ in old}):
            seeds = sorted(s for g, s in old if g == gid)
            g_diff_cols: set[str] = set()
            g_diff_runs = 0
            for s in seeds:
                ro, rn = old[(gid, s)], new[(gid, s)]
                cols = [c for c in COMPARE_COLS if not _eq(c, ro[c], rn[c])]
                for c in cols:
                    col_hits[c] += 1
                    dw.writerow([gid, s, c, ro[c], rn[c]])
                if cols:
                    g_diff_runs += 1
                    g_diff_cols |= set(cols)
                    n_run_diff += 1
                    if "digest" in cols:
                        n_digest_diff += 1
            groups_diff[gid] = sorted(g_diff_cols)
            gw.writerow([gid, g_diff_runs, "|".join(sorted(g_diff_cols)),
                         qualifies.get(gid, False)])
    n_g_diff = sum(1 for v in groups_diff.values() if v)
    n_g_still = sum(1 for g in groups_diff if qualifies.get(g))
    print(f"对齐 run 数：486（162 组 × 3 种子）")
    print(f" metrics 列有差异的 run：{n_run_diff}/486；digest 差异：{n_digest_diff}/486")
    print(f" 差异列分布：{ {c: n for c, n in col_hits.items() if n} }")
    print(f" 有差异的组：{n_g_diff}/162；新批仍 9/9 达标的组：{n_g_still}/162")
    print(f" 逐 run 差异清单：{diff_rows_path}")
    print(f" 逐组汇总：{group_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
