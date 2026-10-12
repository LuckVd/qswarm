"""experiments/tune_round.py —— M0 调参轮驱动（SPEC-4 §4-1 迭代定稿的执行器）。

按缺口迭代参数的每一「轮」= 一个自定义网格 × 种子表的实验批次：复用
``simulator.experiments`` 的单元执行/六件套落盘/并行降级全部机制，再直接用
``simulator.report`` 的判定器（assess_group/select_group，与 sim_report 同一
语义）输出逐组 9 项目标达成表。正式结论仍以官方 ``--full`` 全矩阵 + report
为准；本驱动只做调参轮的证据产出（results/tune-round<R>/）。

可调维度（点路径覆盖 / 世界旋钮 / 引擎透传）：
  N        promote.N           （覆盖）
  delta    promote.DELTA       （覆盖）
  t_base   tilt.T_BASE         （覆盖）
  tmin/tmax tilt.T_MIN/T_MAX   （覆盖，TILT 区间）
  gamma    tilt.GAMMA          （覆盖，0 = TILT 固定臂）
  lam      shrink.LAMBDA       （覆盖）
  cooldown exam.COOLDOWN_DAYS  （覆盖）
  rx       deposits.REVIEWER_X （覆盖）
  window   衰退滑动窗窗长 promote_window_days（引擎透传，非 params 键）
  exam_wh  ExamWorldParams.weight_high（世界旋钮）
  ve       VerdictParams.verify_error  （世界旋钮）

用法（网格维度逗号分隔取值；未给的维度取基线单点）：
  python experiments/tune_round.py --round 1 --workers 4 \
      --seeds 11,22,33 --dims cooldown=0,2 window=1,7
  python experiments/tune_round.py --round 2 --dims delta=0.05,0.15,0.25 \\
      tmin=0.4 tmax=0.8 --replay-every 0
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel import SLIDING_WINDOW_DAYS
from qswarm.simulator.experiments import (
    EXP_MATRIX,
    FULL_SEEDS,
    S2_LIMIT,
    S2_REFERENCE_VE,
    ExperimentUnit,
    execute_plan,
    group_summaries,
    s2_starvation_rows,
    write_outputs,
    write_telemetry,
)
from qswarm.simulator.report import (
    REGRET_RATIO_COL,
    SPEC4_TARGETS,
    assess_group,
    judge,
    load_experiment_outputs,
    select_group,
    starvation_by_tuple,
)
from qswarm.simulator.verdict import VerdictParams

#: 调参维度 → (点路径覆盖键 | 世界旋钮标记, 默认单点)
_DIMENSIONS: dict[str, dict[str, Any]] = {
    "N":       {"override": "promote.N",           "default": 6},
    "delta":   {"override": "promote.DELTA",       "default": 0.05},
    "t_base":  {"override": "tilt.T_BASE",         "default": 0.7},
    "tmin":    {"override": "tilt.T_MIN",          "default": None},
    "tmax":    {"override": "tilt.T_MAX",          "default": None},
    "gamma":   {"override": "tilt.GAMMA",          "default": None},
    "lam":     {"override": "shrink.LAMBDA",       "default": None},
    "cooldown": {"override": "exam.COOLDOWN_DAYS", "default": None},
    "entry_direct": {"override": "exam.ENTRY_DIRECT", "default": None},
    "entry_frac": {"override": "exam.ENTRY_TOP_FRAC", "default": None},
    "entry_margin": {"override": "exam.ENTRY_MARGIN", "default": None},
    "type_entry": {"override": "exam.TYPE_ENTRY", "default": None},
    "grace": {"override": "exam.ENTRY_GRACE_DAYS", "default": None},
    "corrob": {"override": "exam.ENTRY_CORROB_S2S3", "default": None},
    "fc_days": {"override": "demote.FC_DAYS", "default": None},
    "fc_items": {"override": "demote.FC_ITEMS", "default": None},
    "fc_margin": {"override": "demote.FC_MARGIN", "default": None},
    "fc_consec": {"override": "demote.FC_CONSEC", "default": None},
    "fc_base": {"override": "demote.FC_BASE_EXAMS", "default": None},
    "rscout": {"override": "roster.scout", "default": None},
    "rauditor": {"override": "roster.auditor", "default": None},
    "rreviewer": {"override": "roster.reviewer", "default": None},
    "rverifier": {"override": "roster.verifier", "default": None},
    "m_prorate": {"override": "promote.M_PRORATE", "default": None},
    "unique": {"override": "promote.UNIQUE_PER_FAMILY", "default": None},
    "wadv": {"override": "promote.WINDOW_ADVANTAGE", "default": None},
    "rx":      {"override": "deposits.REVIEWER_X", "default": None},
    "window":  {"override": "@window_days",        "default": float(SLIDING_WINDOW_DAYS)},
    "tdrift":  {"override": "@t_drift",            "default": None},
    "quiz":    {"override": "@type_quiz_items",    "default": None},
    "eitems":  {"override": "@entry_items",         "default": None},
    "ewindow": {"override": "exam.ENTRY_WINDOW_DAYS", "default": None},
    "tcomb":   {"override": "exam.TYPE_ENTRY_COMBINE", "default": None},
    "exam_wh": {"override": "@exam_weight_high",   "default": 0.3},
    "ve":      {"override": "@verify_error",       "default": 0.2},
}

_GID_ORDER = ("rscout", "rauditor", "rreviewer", "rverifier", "window",
              "cooldown", "entry_direct", "entry_frac", "entry_margin",
              "type_entry", "grace", "corrob", "quiz", "eitems", "ewindow", "tcomb",
              "fc_days", "fc_items",
              "fc_margin", "fc_consec", "fc_base", "m_prorate", "unique", "wadv",
              "N", "delta", "t_base", "tmin", "tmax", "gamma", "lam", "rx",
              "tdrift", "exam_wh", "ve")

_BOOL_DIMS = ("m_prorate", "unique")
_INT_DIMS = ("N", "cooldown", "wadv", "entry_margin", "grace", "ewindow", "fc_days",
             "fc_items", "fc_consec", "fc_base", "quiz", "eitems", "rscout", "rauditor",
             "rreviewer", "rverifier")


def _is_float_dim(name: str) -> bool:
    return name not in _BOOL_DIMS and name not in _INT_DIMS


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float) and v == int(v):
        v = int(v)
    return str(v).replace(".", "p").replace("-", "m")


def build_round_units(
    dims: dict[str, list[Any]],
    seeds: list[int],
    n_days: int,
    *,
    replay_every: int = 0,
) -> tuple[list[ExperimentUnit], dict[str, Any]]:
    """调参轮计划：维度全积 × 种子表；group_id 编码全部维度取值（规范序）。"""
    names = [k for k in _GID_ORDER if k in dims]
    combos = list(itertools.product(*(dims[k] for k in names)))
    units: list[ExperimentUnit] = []
    uid = 0
    for combo in combos:
        vals = dict(zip(names, combo))
        overrides = []
        for k in names:
            path = _DIMENSIONS[k]["override"]
            if not path.startswith(("promote.", "tilt.", "shrink.", "exam.",
                                    "deposits.", "demote.", "roster.")) or vals[k] is None:
                continue
            if k in _INT_DIMS:
                overrides.append((path, int(vals[k])))
            elif k in _BOOL_DIMS:
                overrides.append((path, bool(int(vals[k]))))
            else:
                overrides.append((path, float(vals[k])))
        overrides = tuple(overrides)
        gid = "-".join(
            f"{k.upper() if len(k) <= 4 else k}{_fmt(vals[k])}" for k in names
        )
        for seed in seeds:
            units.append(ExperimentUnit(
                unit_id=uid, experiment=EXP_MATRIX, arm="tune", group_id=gid,
                seed=int(seed), n_days=n_days, overrides=overrides,
                exam_weight_high=float(vals.get("exam_wh") or 0.3),
                verify_error=float(vals.get("ve") or 0.2),
                router="kernel.route", replay_every=replay_every,
                promote_window_days=float(vals.get("window") or SLIDING_WINDOW_DAYS),
                t_drift=(int(vals["tdrift"]) if vals.get("tdrift") is not None else None),
                type_quiz_items=(int(vals["quiz"]) if vals.get("quiz") is not None else None),
            ))
            uid += 1
    meta = {
        "mode": f"tune",
        "grid": {
            # write_outputs/starvation 依赖 verify_error 网格键；世界三旋钮恒给全
            "verify_error": [float(v) for v in dims.get("ve", [0.2])],
            **{k: [float(v) for v in dims[k]] for k in names},
        },
        "seeds": [int(s) for s in seeds],
        "n_days": n_days,
        "n_matrix_groups": len(combos),
        "n_additional_runs": 0,
        "n_units": len(units),
        "s2_reference_ve": S2_REFERENCE_VE,
        "s2_limit": S2_LIMIT,
        "replay_every": replay_every,
        "window_days": float(dims.get("window", [SLIDING_WINDOW_DAYS])[0])
        if len(dims.get("window", [])) == 1 else None,
    }
    return units, meta


def evaluate_round(out_dir: Path) -> tuple[list[dict[str, Any]], Any]:
    """读回六件套 → report 同语义逐组 9 目标判定；返回 (逐组判定行, Selection)。"""
    outs = load_experiment_outputs(out_dir)
    star = starvation_by_tuple(outs.starvation)
    rows: list[dict[str, Any]] = []
    assessments = []
    for s in outs.summaries:
        a = assess_group(s, star)
        assessments.append(a)
        rows.append({
            "group_id": a.group_id, "n_seeds": a.n_seeds,
            **{t.key: ("PASS" if r.verdict else
                       ("NODATA" if r.verdict is None else f"FAIL({r.gap:.4f})"
                        if r.gap is not None else "FAIL"))
               for t, r in zip(SPEC4_TARGETS, a.readings)},
            "n_unmet": a.n_unmet, "n_nodata": a.n_nodata,
            "regret_ratio": s.get(REGRET_RATIO_COL),
        })
    sel = select_group(assessments)
    return rows, sel


def _print_table(rows: list[dict[str, Any]], sel) -> None:
    keys = [t.key for t in SPEC4_TARGETS]
    head = f"{'group_id':34s} " + " ".join(f"{k[:9]:>9s}" for k in keys) + \
        "  unmet nodata"
    print(head)
    for r in sorted(rows, key=lambda r: (r["n_unmet"], r["n_nodata"])):
        cells = " ".join(f"{str(r[k])[:9]:>9s}" for k in keys)
        print(f"{r['group_id']:34s} {cells}  {r['n_unmet']:5d} {r['n_nodata']:6d}")
    feat = sel.featured
    if feat is not None:
        print(f"\nselection: fallback={sel.fallback} featured={feat.group_id} "
              f"qualifies={feat.qualifies} unmet={feat.n_unmet} nodata={feat.n_nodata}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seeds", default=",".join(str(s) for s in FULL_SEEDS))
    ap.add_argument("--n-days", type=int, default=30)
    ap.add_argument("--replay-every", type=int, default=0)
    ap.add_argument("--dims", default="",
                    help="维度=值,值;... 例 'cooldown=0,2 window=1,7'")
    ap.add_argument("--plan-only", action="store_true")
    args = ap.parse_args()

    dims: dict[str, list[Any]] = {}
    for part in filter(None, (p.strip() for p in
                              args.dims.replace(";", " ").split())):
        if "=" not in part:
            raise SystemExit(f"--dims 片段必须是 维度=值,值：{part!r}")
        name, _, raw = part.partition("=")
        if name not in _DIMENSIONS:
            raise SystemExit(f"未知维度 {name!r}（合法：{sorted(_DIMENSIONS)}）")
        dims[name] = [
            int(v) if name in _INT_DIMS else float(v)
            for v in raw.split(",") if v.strip()
        ]
    units, meta = build_round_units(dims, [int(s) for s in args.seeds.split(",")],
                                    args.n_days, replay_every=args.replay_every)
    print(f"[tune-round{args.round}] groups={meta['n_matrix_groups']} "
          f"units={len(units)} seeds={meta['seeds']} dims={meta['grid']}")
    if args.plan_only:
        return 0

    out_dir = Path(args.out or f"results/tune-round{args.round}")
    started = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    t0 = time.perf_counter()
    rows = execute_plan(units, workers=args.workers)
    wall = time.perf_counter() - t0
    paths = write_outputs(out_dir, meta, rows)
    paths.update(write_telemetry(out_dir, meta, rows, argv=sys.argv[1:],
                                 workers=args.workers, started_at=started,
                                 wall_s=wall))
    print(f"[tune-round{args.round}] done in {wall:.0f}s → {out_dir} "
          f"(per-run max {max(r['elapsed_s'] for r in rows):.1f}s)")

    target_rows, sel = evaluate_round(out_dir)
    (out_dir / "round_targets.csv").write_text(
        ",".join(target_rows[0].keys()) + "\n" +
        "\n".join(
            ",".join(str(r[k]) if r[k] is not None else "" for k in target_rows[0])
            for r in target_rows
        ) + "\n",
        encoding="utf-8",
    )
    _print_table(target_rows, sel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
