"""qswarm.simulator.experiments 的验收测试（taskflow ac 三条 + 覆盖性/并行不变性）。

- 覆盖性：--full 计划 = §3 全矩阵 162 组 × 3 种子 + 三项附加实验（21 runs）；
- 可复现：同命令同种子两次运行，确定性载荷（results/summary/starvation.csv +
  results.json）逐字节一致（smoke 规模，CLI 全路径）；
- schema：结果文件含全部参数维度列与 9 项度量列；timing.csv 记录每组耗时且
  smoke 级单实验 < 5 分钟（§6 预算）。

重测试（CLI --smoke 两次 ≈ 分钟级）排在文件末尾；其余为轻量 API 级测试。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from qswarm.simulator.experiments import (
    FULL_GRID,
    FULL_SEEDS,
    GROUP_TIMING_COLS,
    METRIC_COLS,
    PARAM_COLS,
    RESULT_COLS,
    RUN_BUDGET_S,
    SMOKE_GRID,
    SMOKE_SEEDS,
    EXP_MATRIX,
    ExperimentUnit,
    build_plan,
    execute_plan,
    group_timing_rows,
    main,
    write_outputs,
)

DETERMINISTIC_FILES = ("results.csv", "summary.csv", "starvation.csv", "results.json")
TELEMETRY_FILES = ("timing.csv", "timing_by_group.csv", "manifest.json")


# ── 计划覆盖性（ac1 的 --full 覆盖面）────────────────────────────


def test_full_plan_covers_spec3_grid_and_additions():
    units, meta = build_plan("full")
    matrix = [u for u in units if u.experiment == EXP_MATRIX]
    extra = [u for u in units if u.experiment != EXP_MATRIX]

    # §3 网格逐维精确相等
    assert set(FULL_GRID) == {
        "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error"
    }
    assert FULL_GRID["N"] == (6, 10, 14)
    assert FULL_GRID["DELTA"] == (0.05, 0.10, 0.15)
    assert FULL_GRID["T_BASE"] == (0.6, 0.7, 0.8)
    assert FULL_GRID["exam_weight_high"] == (0.3, 0.5)
    assert FULL_GRID["verify_error"] == (0.2, 0.35, 0.5)

    # 162 组 × 3 固定种子；组内仅种子不同
    gids = {u.group_id for u in matrix}
    assert len(gids) == 162
    assert len(matrix) == 162 * len(FULL_SEEDS) == 486
    for gid in gids:
        seeds = sorted(u.seed for u in matrix if u.group_id == gid)
        assert seeds == list(FULL_SEEDS)
        assert len({u.n_days for u in matrix if u.group_id == gid}) == 1

    # 机制参数覆盖点路径 + 世界侧旋钮逐单元就位
    assert any("promote.N" in dict(u.overrides) for u in matrix)
    assert any("promote.DELTA" in dict(u.overrides) for u in matrix)
    assert any("tilt.T_BASE" in dict(u.overrides) for u in matrix)

    # 三项附加实验：TILT 固定 vs 自适应（2 臂）/ REVIEWER_X 3 点 / Thompson 对照（2 臂）
    by_exp = {}
    for u in extra:
        by_exp.setdefault(u.experiment, set()).add(u.arm)
    assert by_exp["tilt_fixed_vs_adaptive"] == {"adaptive", "fixed"}
    assert by_exp["reviewer_x"] == {"rx0.25", "rx0.50", "rx1.00"}
    assert by_exp["thompson_control"] == {"kernel.route", "route_thompson"}
    assert len(extra) == (2 + 3 + 2) * len(FULL_SEEDS) == 21
    # Thompson 对照臂确实换路由
    ts = [u for u in extra if u.experiment == "thompson_control"
          and u.arm == "route_thompson"]
    assert all(u.router == "route_thompson" for u in ts)
    # 总量与元数据
    assert meta["n_units"] == 507 and len(units) == 507
    assert meta["seeds"] == list(FULL_SEEDS)


def test_smoke_plan_shape():
    units, meta = build_plan("smoke")
    assert meta["n_units"] == len(units) == 78          # 32 组 × 2 + 14
    assert meta["seeds"] == list(SMOKE_SEEDS)
    assert len({u.group_id for u in units
                if u.experiment == EXP_MATRIX}) == 32   # 2^5 子集网格
    assert set(SMOKE_GRID["N"]) <= set(FULL_GRID["N"])
    assert set(SMOKE_GRID["verify_error"]) <= set(FULL_GRID["verify_error"])


def test_plan_cli_dry_run_writes_nothing(tmp_path, capsys):
    rc = main(["--full", "--plan", "--out", str(tmp_path / "should-not-exist")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "matrix groups: 162" in out and "total runs: 507" in out
    assert not (tmp_path / "should-not-exist").exists()


# ── 单元与进程数无关性（ac2 的机制面）────────────────────────────


def _micro_units(n: int = 8, n_days: int = 3) -> tuple[list[ExperimentUnit], dict]:
    units, meta = build_plan("smoke", seeds=(11,), n_days=n_days)
    return units[:n], {**meta, "n_units": n}


def test_execute_unit_twice_bit_identical():
    units, _ = _micro_units(1)
    row_a = execute_plan(units[:1], workers=1)[0]
    row_b = execute_plan(units[:1], workers=1)[0]
    # 确定性载荷（落盘列集）逐字段全等，含 9 度量与轨迹 digest；
    # elapsed_s 是遥测字段（不在 RESULT_COLS），天然随时钟波动，不参与比对。
    assert {c: row_a[c] for c in RESULT_COLS} == {c: row_b[c] for c in RESULT_COLS}


def test_worker_count_invariance(tmp_path):
    units, meta = _micro_units(8)
    rows_serial = execute_plan(units, workers=1)
    rows_par = execute_plan(units, workers=3)   # 沙箱内走 subprocess 降级，同样并行
    assert [r["unit_id"] for r in rows_serial] == [r["unit_id"] for r in rows_par]
    # 行内容（确定性载荷列）与进程数无关；elapsed_s 为遥测字段不参与
    proj = lambda rows: [{c: r[c] for c in RESULT_COLS} for r in rows]
    assert proj(rows_serial) == proj(rows_par)
    d1 = write_outputs(tmp_path / "serial", meta, rows_serial)
    d2 = write_outputs(tmp_path / "par", meta, rows_par)
    by_name1 = {p.name: p for p in d1.values()}
    by_name2 = {p.name: p for p in d2.values()}
    for name in DETERMINISTIC_FILES:
        assert by_name1[name].read_bytes() == by_name2[name].read_bytes(), name


# ── 逐组耗时聚合（ac3「记录每组耗时」的机制面）──────────────────


def test_group_timing_rows_aggregates_per_group():
    units, meta = _micro_units(4, n_days=2)
    rows = execute_plan(units, workers=1)
    for r in rows:                                          # 人造耗时，验证聚合口径
        r["elapsed_s"] = 10.0 + r["unit_id"]
    grouped = group_timing_rows(rows)
    assert len(grouped) == 4                                # 4 个互异参数组
    g0 = grouped[0]
    assert tuple(g0[c] for c in GROUP_TIMING_COLS[:5]) == (
        units[0].experiment, units[0].arm, units[0].group_id,
        units[0].n_days, 1,
    )
    assert g0["elapsed_s_mean"] == g0["elapsed_s_max"] == g0["elapsed_s_sum"]
    assert g0["within_budget_5min"] is True
    # 同组多种子：mean/max/sum 正确聚合（同组仅种子不同的两个单元）
    units2, _ = build_plan("smoke", seeds=(11, 22), n_days=2)
    rows2 = execute_plan(units2[:2], workers=1)
    assert rows2[0]["group_id"] == rows2[1]["group_id"]
    for r in rows2:
        r["elapsed_s"] = 30.0
    g = group_timing_rows(rows2)[0]
    assert g["n_runs"] == 2
    assert g["elapsed_s_mean"] == g["elapsed_s_max"] == 30.0
    assert g["elapsed_s_sum"] == 60.0


# ── CLI 端到端：产出 + 位级复现 + schema + 耗时预算（ac1/2/3 主测试）──


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_cli_smoke_reproducible_schema_and_budget(tmp_path):
    """同命令同种子两次运行：确定性载荷逐位一致；schema 齐备；smoke 单实验 <5 min。"""
    out1, out2 = tmp_path / "a", tmp_path / "b"
    assert main(["--smoke", "--out", str(out1), "--workers", "4"]) == 0
    assert main(["--smoke", "--out", str(out2), "--workers", "4"]) == 0

    # ac1：一条命令产出全部结果文件
    for name in DETERMINISTIC_FILES + TELEMETRY_FILES:
        assert (out1 / name).is_file() and (out2 / name).is_file(), name

    # ac2：确定性载荷逐字节一致（遥测两件天然非确定，只查形状）
    for name in DETERMINISTIC_FILES:
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes(), name
    rows = _read_csv(out1 / "timing.csv")
    assert len(rows) == 78 and all(float(r["elapsed_s"]) > 0 for r in rows)
    assert max(float(r["elapsed_s"]) for r in rows) < RUN_BUDGET_S  # §6 ≤5 min/实验
    manifest = json.loads((out1 / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["n_rows"] == 78 and manifest["seeds"] == list(SMOKE_SEEDS)

    # ac3（每组耗时）：逐参数组耗时聚合表与逐 run 表同构（39 组），预算内全真
    by_group = _read_csv(out1 / "timing_by_group.csv")
    assert len(by_group) == 39
    assert all(int(r["n_runs"]) == 2 for r in by_group)     # smoke 每组 2 种子
    assert all(r["within_budget_5min"] == "True" for r in by_group)
    assert all(0 < float(r["elapsed_s_max"]) < RUN_BUDGET_S for r in by_group)

    # ac3：参数维度列 + 9 项度量列 + digest 齐备；行序 = 计划规范序
    result_rows = _read_csv(out1 / "results.csv")
    with (out1 / "results.csv").open(newline="", encoding="utf-8") as f:
        assert tuple(csv.reader(f).__next__()) == RESULT_COLS
    assert len(result_rows) == 78
    assert all(r["digest"] and len(r["digest"]) == 64 for r in result_rows)
    matrix_rows = [r for r in result_rows if r["experiment"] == EXP_MATRIX]
    assert len({r["group_id"] for r in matrix_rows}) == 32
    # 附加实验臂齐备
    arms = {(r["experiment"], r["arm"]) for r in result_rows}
    assert ("tilt_fixed_vs_adaptive", "fixed") in arms
    assert ("tilt_fixed_vs_adaptive", "adaptive") in arms
    assert {a for e, a in arms if e == "reviewer_x"} == {"rx0.25", "rx0.50", "rx1.00"}
    assert {a for e, a in arms if e == "thompson_control"} == {
        "kernel.route", "route_thompson"}

    # summary：32 组 + 7 附加臂 = 39 组，mean±std 列齐备
    summaries = _read_csv(out1 / "summary.csv")
    assert len(summaries) == 39
    for m in METRIC_COLS:
        assert f"{m}_mean" in summaries[0]
    # starvation：每个 (N,δ,T_BASE,exam) 元组一行——verify_error 是被折叠的
    # 扫描维（⑧ 的定义），smoke=2^4=16 行，full=3·3·3·2=54 行
    starvation = _read_csv(out1 / "starvation.csv")
    assert len(starvation) == 16

    # results.json 与 results.csv 同构（行数与种子表）
    doc = json.loads((out1 / "results.json").read_text(encoding="utf-8"))
    assert len(doc["rows"]) == 78 and doc["seeds"] == list(SMOKE_SEEDS)
