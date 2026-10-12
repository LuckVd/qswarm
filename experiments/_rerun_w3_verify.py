"""experiments/_rerun_w3_verify.py —— M0 门禁复跑（并发 3）验收证据生成器。

对复跑批（results/experiments-full，--full --workers 3）产出四类证据：

  E1 运行元数据   —— manifest.json：workers=3、507 rows、wall、per-run max；
  E2 达标表       —— out/group_targets.csv：matrix 162 组全部 n_pass=9 且 qualifies；
                     results.csv：507 runs replay_equivalent 全真；
  E3 位级列比对   —— 复跑批 vs M0 归档批（results/experiments-full-m0gate-20261009）：
                     results.csv 剔除 digest 列后逐字节 sha256 比对（参数列+9 度量列）；
  E4 逐组复跑对照 —— 复用 experiments/compare_rerun.py 逐组（162 组）比对 9 度量 +
                     regret 曲线列 + 轨迹 digest，落逐组 ALL_EQUAL 表与汇总。

退出码 0 = E1/E2/E3 全过（E4 的 metrics_equal 汇总单列报告，不并入退出码）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / "results" / "experiments-full"
OLD = ROOT / "results" / "experiments-full-m0gate-20261009"
EVID = ROOT / "results" / "_rerun_w3_evidence"
COMPARE = ROOT / "experiments" / "compare_rerun.py"

ok = True


def check(name: str, cond: bool, detail: str = "") -> bool:
    global ok
    ok &= bool(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    return bool(cond)


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    EVID.mkdir(parents=True, exist_ok=True)

    # ── E1 运行元数据 ──────────────────────────────────────────
    man = json.loads((NEW / "manifest.json").read_text(encoding="utf-8"))
    check("E1 workers==3", man["workers"] == 3, f"argv={man['argv']}")
    check("E1 n_rows==507", man["n_rows"] == 507, f"n_units={man['n_units']}")
    check("E1 wall ≤ 2h", man["wall_s"] <= 7200, f"wall={man['wall_s']:.0f}s")
    check("E1 per-run max < 300s", (man["per_run_elapsed_s"]["max"] or 0) < 300,
          f"max={man['per_run_elapsed_s']['max']:.1f}s")

    # ── E2 达标表 ──────────────────────────────────────────────
    with (ROOT / "out" / "group_targets.csv").open(newline="", encoding="utf-8") as f:
        gt = list(csv.DictReader(f))
    matrix = [r for r in gt if r["experiment"] == "matrix" and r["arm"] == "base"]
    check("E2 matrix 组数==162", len(matrix) == 162, f"n={len(matrix)}")
    check("E2 162 组全 9/9 达标",
          all(r["n_pass"] == "9" and r["n_unmet"] == "0" and r["n_nodata"] == "0"
              and r["qualifies"] == "True" for r in matrix))
    rec = [r for r in matrix if r["selected"] == "recommended"]
    check("E2 推荐组==N06-D0.05-T0.60-E0.30-V0.20",
          len(rec) == 1 and rec[0]["group_id"] == "N06-D0.05-T0.60-E0.30-V0.20",
          rec[0]["group_id"] if rec else "无")
    with (NEW / "results.csv").open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    n_replay = sum(1 for r in rows if r["replay_equivalent"] == "True")
    check("E2 replay_equivalent 507/507", len(rows) == 507 and n_replay == 507,
          f"{n_replay}/{len(rows)}")

    # ── E3 位级列比对（剔除 digest 列后逐字节）────────────────
    def strip_digest(src: Path, dst: Path) -> None:
        lines = src.read_text(encoding="utf-8").splitlines(keepends=True)
        out = []
        for ln in lines:
            body = ln.rstrip("\n")
            out.append(body[: body.rfind(",")] + "\n")   # 末列 = digest
        dst.write_text("".join(out), encoding="utf-8")

    s_new, s_old = EVID / "results_minus_digest_new.csv", EVID / "results_minus_digest_old.csv"
    strip_digest(NEW / "results.csv", s_new)
    strip_digest(OLD / "results.csv", s_old)
    h_new, h_old = sha_file(s_new), sha_file(s_old)
    check("E3 results.csv 剔除 digest 列后逐位一致", h_new == h_old,
          f"new={h_new[:16]}… old={h_old[:16]}…")

    # ── E4 逐组复跑对照（复用 compare_rerun.py，按其设计喂单组 rerun 批）──
    gids = sorted({r["group_id"] for r in matrix})
    table_path = EVID / "compare_all_groups.csv"
    log_path = EVID / "compare_all_groups.log"
    n_me = n_de = n_ae = 0
    with table_path.open("w", newline="", encoding="utf-8") as tf, \
         log_path.open("w", encoding="utf-8") as lf:
        w = csv.writer(tf, lineterminator="\n")
        w.writerow(["group_id", "metrics_equal", "digest_equal", "ALL_EQUAL"])
        # compare_rerun.py 的 rerun 侧按 seed 取行、不滤组（单组批设计）——
        # 先把新批按组切成单组 CSV 再喂入，避免跨组行覆盖。
        header = (NEW / "results.csv").read_text(encoding="utf-8").splitlines()[0]
        by_group: dict[str, list[str]] = {}
        for line in (NEW / "results.csv").read_text(encoding="utf-8").splitlines()[1:]:
            by_group.setdefault(line.split(",")[2], []).append(line)
        for gid in gids:
            single = EVID / "_single_group.csv"
            single.write_text("\n".join([header, *by_group[gid]]) + "\n",
                              encoding="utf-8")
            p = subprocess.run(
                [sys.executable, str(COMPARE), "--base", str(OLD / "results.csv"),
                 "--rerun", str(single), "--group", gid],
                capture_output=True, text=True)
            lf.write(p.stdout + p.stderr)
            me = "metrics_equal=true" in p.stdout
            de = "digest_equal=true" in p.stdout
            ae = "ALL_EQUAL=true" in p.stdout
            n_me += me; n_de += de; n_ae += ae
            w.writerow([gid, me, de, ae])
        single.unlink(missing_ok=True)
    print(f"E4 compare_rerun.py 逐组对照（162 组）：metrics_equal={n_me}/162 "
          f"digest_equal={n_de}/162 ALL_EQUAL={n_ae}/162")
    check("E4 逐组 metrics_equal 全真", n_me == 162)
    # digest/ALL_EQUAL 的判定如实单列（不并入 ok）：M1 接线新增 cluster_merge 事件
    # 属预期形态差异，是否全真都按工具口径原样落盘，不调参、不掩盖。
    print(f"E4 信息位：digest_equal={n_de}/162（逐字段落盘于 compare_all_groups.csv，"
          f"差异根因另证）")

    print("VERIFY_RESULT =", "ALL_PASS" if ok else "HAS_FAILURES")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
