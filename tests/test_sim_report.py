"""qswarm.simulator.report 的验收测试（taskflow ac 三条 + 边界值/选择器/渲染）。

- ac_muz07uljuqmecjib：固定实验结果夹具生成报告；达成表 9 行齐全；判定逻辑与
  SPEC-4 §2 目标阈值逐项一致（逐目标构造「恰在阈值 / 恰出阈值」边界用例断言）；
- ac_muz07ulj0skcscna：选择器——多组全达标时取中位晋升时长最短者；无达标组时
  输出差距报告（最接近组 + 逐项差距），绝不静默选组；
- ac_muz07ulj0olvqpcm：报告三节齐全（§1 达成表 / §2 敏感性 / §3 Thompson）、
  含 CSV 产物路径、markdown 表格逐块可渲染（列数一致 + 分隔行）。

夹具直接构造 experiments 六件套的 CSV 形态（summary.csv / starvation.csv /
results.json），不跑仿真——report 是纯聚合层，边界值可精确钉死。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from qswarm.simulator.report import (
    KEY_METRICS,
    SPEC4_TARGETS,
    V_NODATA,
    V_PASS,
    assess_group,
    build_report,
    judge,
    load_experiment_outputs,
    main,
    relative_gap,
    select_group,
    sensitivity_rows,
    starvation_by_tuple,
    thompson_conclusion,
)

# ── 夹具构造（experiments 输出的最小完整形态）────────────────────

_PARAM_COLS = (
    "experiment", "arm", "group_id", "n_days", "n_seeds",
    "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error",
    "reviewer_x", "tilt_gamma", "router",
)
_METRIC_COLS = (
    "promotion_precision", "promotion_recall", "median_time_to_active",
    "wrong_extinguish_rate", "degradation_response", "regret_total",
    "regret_per_task", "regret_late_over_early",
    "cold_start_length", "replay_equivalent",
)
_SUMMARY_HEADER = _PARAM_COLS + tuple(
    c for m in _METRIC_COLS for c in (f"{m}_mean", f"{m}_std")
) + ("regret_late_over_early",)   # ⑥ 的可选曲线列（六件套缺省无，夹具可控）

#: 「全部恰在阈值内」的基准合格组（9/9 达标；⑦/⑥ 用严格比较的达标侧值）。
_QUALIFIED = {
    "promotion_precision": 0.90,          # ≥ 0.90（恰在阈值）
    "promotion_recall": 0.70,             # ≥ 0.70（恰在阈值）
    "median_time_to_active": 200.0,       # ≤ 200（恰在阈值）
    "wrong_extinguish_rate": 0.05,        # ≤ 0.05（恰在阈值）
    "degradation_response": 150.0,        # ≤ 150（恰在阈值）
    "regret_total": 300.0,
    "regret_per_task": 0.20,
    "cold_start_length": 49.999,          # < 50（严格）
    "replay_equivalent": 1.0,             # == 1.0
}


def _gid(n: float, ve: float = 0.2, tag: str = "base") -> str:
    return f"N{int(n):02d}-D0.10-T0.70-E0.50-V{ve:.2f}-{tag}"


def _summary_row(group_id: str, *, values: dict | None = None,
                 ratio: float | None = 0.8, n_seeds: int = 3, **params) -> dict:
    row = {c: "" for c in _SUMMARY_HEADER}
    row.update({
        "experiment": "matrix", "arm": "base", "group_id": group_id,
        "n_days": 30, "n_seeds": n_seeds,
        "N": params.get("N", 10.0), "DELTA": params.get("DELTA", 0.10),
        "T_BASE": params.get("T_BASE", 0.70),
        "exam_weight_high": params.get("exam_weight_high", 0.5),
        "verify_error": params.get("verify_error", 0.2),
        "reviewer_x": params.get("reviewer_x", 0.5),
        "tilt_gamma": params.get("tilt_gamma", 1.0),
        "router": params.get("router", "kernel.route"),
    })
    vals = {**_QUALIFIED, **(values or {})}
    for m in _METRIC_COLS:
        v = vals.get(m)
        row[f"{m}_mean"] = None if v is None else float(v)   # float 直存：写盘由 csv 序列化
        row[f"{m}_std"] = None if v is None else 0.01
    row["regret_late_over_early"] = None if ratio is None else float(ratio)
    return row


def _arm_row(group_id: str, experiment: str, arm: str, **params) -> dict:
    """附加实验臂行（thompson_control / reviewer_x / tilt_fixed_vs_adaptive）。"""
    row = _summary_row(group_id, ratio=None, **params)
    row["experiment"] = experiment
    row["arm"] = arm
    return row


def _star_row(n: float, delta: float, t_base: float, exam: float, *,
              ratios: tuple = (1.0, 1.2, 1.5), passed: bool = True) -> dict:
    row = {"N": n, "DELTA": delta, "T_BASE": t_base, "exam_weight_high": exam,
           "reference": 0.2, "limit": 2.0, "passed": passed}
    for ve, r in zip(("0.20", "0.35", "0.50"), ratios):
        row[f"median_tta_ve{ve}"] = 615.0
        row[f"ratio_ve{ve}"] = r
    return row


def _make_exp_dir(
    tmp_path: Path,
    summaries: list[dict],
    starvation: list[dict] | None = None,
    *,
    meta: dict | None = None,
) -> Path:
    src = tmp_path / "experiments-fixture"
    src.mkdir()
    with (src / "summary.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_SUMMARY_HEADER, lineterminator="\n")
        w.writeheader()
        w.writerows(summaries)
    if starvation is None:
        # 缺省：按 matrix 组的 (N, δ, T_BASE, exam) 元组各生成一行「三档完整且
        # passed=True」——⑧ 元组键不含 verify_error，同元组各档共享该行。
        seen = set()
        starvation = []
        for r in summaries:
            if r["experiment"] != "matrix":
                continue
            key = (r["N"], r["DELTA"], r["T_BASE"], r["exam_weight_high"])
            if key not in seen:
                seen.add(key)
                starvation.append(_star_row(*key))
    star_header = ("N", "DELTA", "T_BASE", "exam_weight_high",
                   "median_tta_ve0.20", "ratio_ve0.20", "median_tta_ve0.35",
                   "ratio_ve0.35", "median_tta_ve0.50", "ratio_ve0.50",
                   "reference", "limit", "passed")
    with (src / "starvation.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=star_header, lineterminator="\n")
        w.writeheader()
        for r in (starvation if starvation is not None else
                  [_star_row(10.0, 0.10, 0.70, 0.5)]):
            w.writerow({k: r.get(k, "") for k in star_header})
    doc = {"schema_version": 1, "mode": "full", "seeds": [11, 22, 33],
           "n_days": 30, **(meta or {})}
    (src / "results.json").write_text(
        json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    return src


def _qualified_group(n: float, tta: float, *, ve: float = 0.2) -> dict:
    """9/9 达标组（中位晋升时长可调，用于选择器排序）。"""
    return _summary_row(
        _gid(n, ve), N=n, verify_error=ve,
        values={**_QUALIFIED, "median_time_to_active": tta},
        ratio=0.8,
    )


def _break_recall(rows: list[dict], value: float) -> list[dict]:
    """把每组晋升查全打落到阈值之下（构造「无达标组」）。"""
    for r in rows:
        if r["experiment"] == "matrix":
            r["promotion_recall_mean"] = float(value)
    return rows


# ── ac1：判定逻辑与 §2 阈值逐项一致（边界值）────────────────────


@pytest.mark.parametrize(
    "key,at,inside,outside",
    [
        # (目标键, 恰在阈值(期望), 阈值内(达标), 阈值外(未达标))
        ("promotion_precision", 0.90, 0.9001, 0.8999),
        ("promotion_recall", 0.70, 0.7001, 0.6999),
        ("median_time_to_active", 200.0, 199.999, 200.0001),
        ("wrong_extinguish_rate", 0.05, 0.0499, 0.0501),
        ("degradation_response", 150.0, 149.9, 150.1),
        ("cold_start_length", 50.0, 49.999, 50.001),   # §2 原文「< 50」严格
        ("s2_starvation", 2.0, 1.9999, 2.0001),        # ≤ 2×（judge 层）
        ("replay_equivalent", 1.0, 1.0, 0.6667),       # == 1.0
    ],
)
def test_judge_boundary_matches_spec_thresholds(key, at, inside, outside):
    target = next(t for t in SPEC4_TARGETS if t.key == key)
    # 恰在阈值：≥/≤/== 含等号判达标；「< 50」严格比较判未达标
    expected_at = at < 50.0 if key == "cold_start_length" else True
    assert judge(target, at) is expected_at, key
    assert judge(target, inside) is True, key
    assert judge(target, outside) is False, key
    assert judge(target, None) is None, key          # 无观测 → 数据不足
    # 相对差距只在未达标时非 None，且方向为正
    if judge(target, outside) is False:
        assert relative_gap(target, outside) > 0, key
    assert relative_gap(target, inside) is None, key


def test_judge_narrowing_strict():
    """⑥ 收窄：后段/前段比值严格 < 1 达标；恰 1.0（未收窄）未达标。"""
    target = next(t for t in SPEC4_TARGETS if t.key == "regret_narrowing")
    assert judge(target, 0.99) is True
    assert judge(target, 1.0) is False               # 边界：持平不算收窄
    assert judge(target, 1.2) is False
    assert judge(target, None) is None               # 无曲线列 → 数据不足
    assert relative_gap(target, 1.2) == pytest.approx(0.2)


def test_regret_ratio_falls_back_to_mean_column(tmp_path):
    """⑥ 列形态演化：裸列缺失时回退到逐 run 度量列（*_mean/*_std）判定。"""
    row = _summary_row(_gid(10.0), ratio=None)
    row["regret_late_over_early_mean"] = 0.95        # 严格 < 1 → 达标
    row["regret_late_over_early_std"] = 0.03
    src = _make_exp_dir(tmp_path, [row])
    outputs = load_experiment_outputs(src)
    star = starvation_by_tuple(outputs.starvation)
    a = assess_group(row, star)
    r6 = a.reading("regret_narrowing")
    assert r6.verdict is True and r6.value == pytest.approx(0.95)
    assert r6.std == pytest.approx(0.03)
    # 反向：两形态都缺失 → 数据不足
    row2 = _summary_row(_gid(6.0), ratio=None)
    b = assess_group(row2, star)
    assert b.reading("regret_narrowing").verdict is None


# ── ac1：达成表 9 行齐全 + 判定与夹具值逐项一致 ─────────────────


def _parse_section1_table(md: str) -> list[list[str]]:
    """取 §1 的达成表（含「SPEC-4 §2 目标」表头的 6 列表）数据行。"""
    blocks = _md_tables(md)
    for block in blocks:
        if "SPEC-4 §2 目标" in block[0]:
            return [
                [c.strip() for c in line.strip("|").split("|")]
                for line in block[2:]
            ]
    raise AssertionError("报告缺少 §1 达成表")


def _md_tables(md: str) -> list[list[str]]:
    """markdown 表格逐块抽取（连续以 | 开头的行）。"""
    blocks: list[list[str]] = []
    cur: list[str] = []
    for line in md.splitlines():
        if line.lstrip().startswith("|"):
            cur.append(line)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def test_achievement_table_nine_rows_all_pass_at_thresholds(tmp_path):
    """恰在阈值的合格组 → 9 行齐全且逐行 ✅；选择器判其全达标。"""
    src = _make_exp_dir(tmp_path, [_qualified_group(10.0, tta=150.0)])
    result = build_report(src, tmp_path / "out")
    rows = _parse_section1_table(result.md)
    assert len(rows) == 9, "达成表必须恰好 9 行（SPEC-4 §2 逐项）"
    assert [r[0] for r in rows] == ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"]
    assert all(V_PASS in r[4] for r in rows), rows
    # 判定与目标阈值文本逐字对应
    for row, target in zip(rows, SPEC4_TARGETS):
        assert target.target_text in row[2]
        assert target.label in row[1]
    assert result.selection.recommended is not None
    assert result.selection.fallback is False
    # 达成表标题是「推荐参数组」而非最接近组
    assert "## 1. 九项度量达成表 —— 推荐参数组" in result.md


def test_achievement_table_reflects_failed_values(tmp_path):
    """构造半数不达标的组：逐行判定与阈值方向一致，未达标行给出差距。"""
    values = {
        "promotion_precision": 0.8999,      # 恰出阈值（边界外）
        "median_time_to_active": 200.0001,  # 恰出阈值
        "cold_start_length": 50.0,          # 「< 50」边界：恰在阈值即未达标
        "degradation_response": None,       # 无观测 → 数据不足
    }
    src = _make_exp_dir(
        tmp_path,
        [_summary_row(_gid(10.0), values=values, ratio=1.0)],
        starvation=[_star_row(10.0, 0.10, 0.70, 0.5,
                              ratios=(1.0, 1.2, 2.5), passed=False)],
    )
    result = build_report(src, tmp_path / "out")
    rows = _parse_section1_table(result.md)
    by_idx = {r[0]: r for r in rows}
    # 逐行断言：未达标 + 差距列
    assert "❌ 未达标" in by_idx["①"][4] and "0.0001" in by_idx["①"][5]
    assert "❌ 未达标" in by_idx["③"][4] and "0.0001" in by_idx["③"][5]
    assert "❌ 未达标" in by_idx["⑦"][4]          # 50.0 不满足严格 < 50
    assert "❌ 未达标" in by_idx["⑧"][4]          # worst 2.5 > 2× 且 passed=False
    assert "0.5" in by_idx["⑧"][5]               # 相对差距 (2.5−2)/2
    assert "❌ 未达标" in by_idx["⑥"][4]          # 比值 1.0 边界：未收窄
    assert V_PASS in by_idx["②"][4] and V_PASS in by_idx["④"][4]
    assert V_PASS in by_idx["⑨"][4]
    # ⑤ 无观测（夹具留空）→ 数据不足而非静默达标
    assert V_NODATA in by_idx["⑤"][4]
    assert result.selection.fallback is True      # 9/9 破 → 无达标组


# ── ac2：选择器 ─────────────────────────────────────────────────


def test_selector_picks_shortest_median_tta_among_qualifying(tmp_path):
    """多组全达标 → 取中位晋升时长最短者（120 < 250 < 300）。"""
    rows = [
        _qualified_group(10.0, tta=190.0),
        _qualified_group(6.0, tta=120.0),
        _qualified_group(14.0, tta=150.0),
    ]
    src = _make_exp_dir(tmp_path, rows)
    result = build_report(src, tmp_path / "out")
    sel = result.selection
    assert sel.fallback is False
    assert len(sel.qualifying) == 3
    assert sel.recommended.group_id == _gid(6.0)
    assert sel.recommended.median_tta == 120.0
    # 平手确定性：同 TTA 时按 group_id 字典序（两组都给完整通过的 ⑧ 元组行）
    star = starvation_by_tuple([
        _star_row(6.0, 0.10, 0.70, 0.5), _star_row(14.0, 0.10, 0.70, 0.5),
    ])
    a = assess_group(_qualified_group(6.0, tta=100.0), star)
    b = assess_group(_qualified_group(14.0, tta=100.0), star)
    winner = select_group([a, b]).recommended
    assert winner is not None and winner.group_id == min(a.group_id, b.group_id)
    assert "推荐参数组" in result.md
    # 全部达标组都进排名：报告给出参与排序的组数
    assert f"{len(sel.qualifying)} 个全达标组" in result.md


def test_selector_no_qualifying_reports_gaps_not_silent_pick(tmp_path):
    """无达标组 → 不静默选组：输出最接近组 + 逐项差距 + 定稿暂缓。"""
    rows = _break_recall(
        [
            _qualified_group(10.0, tta=150.0),
            _qualified_group(6.0, tta=150.0),      # 与上者同 TTA，靠差距和决胜
        ],
        value=0.10,
    )
    rows[1]["promotion_precision_mean"] = 0.85          # 更远离 0.90 → 差距更大
    src = _make_exp_dir(tmp_path, rows)
    result = build_report(src, tmp_path / "out")
    sel = result.selection
    assert sel.fallback is True
    assert sel.recommended is None, "无达标组时绝不能给出推荐组"
    assert sel.closest is not None
    assert sel.closest.group_id == _gid(10.0)      # 查准差距更小者胜出
    # 差距报告而非静默选组：标题、结论、缺口表三处明示
    assert "无满足全部 9 项目标的参数组" in result.md
    assert "最接近组" in result.md
    assert "不定稿、不静默选组" in result.md
    assert "## 4. params.yaml 定稿建议" in result.md
    assert "定稿暂缓" in result.md
    # 支撑数据：group_targets.csv 恰一行 selected=closest，无 recommended
    with result.paths["group_targets_csv"].open(newline="", encoding="utf-8") as f:
        gts = list(csv.DictReader(f))
    assert len(gts) == 2
    assert sorted(r["selected"] for r in gts) == ["", "closest"]
    # §4 缺口表列出未达标目标（晋升查全）与差距
    assert "晋升查全" in result.md


def test_selector_skips_groups_with_missing_observations(tmp_path):
    """有观测缺失（③ 无观测）的组不能达标——无观测不是静默通过。"""
    incomplete = _summary_row(_gid(10.0), values={**_QUALIFIED,
                                                  "median_time_to_active": None},
                              ratio=None)
    complete = _qualified_group(6.0, tta=190.0)
    src = _make_exp_dir(tmp_path, [incomplete, complete])
    result = build_report(src, tmp_path / "out")
    sel = result.selection
    assert sel.fallback is False
    assert sel.recommended.group_id == _gid(6.0)   # 唯一全达标者（尽管 TTA 更长）
    incom = result.assessments[0]
    assert incom.reading("median_time_to_active").verdict is None
    assert not incom.qualifies
    assert incom.reading("regret_narrowing").verdict is None   # 无曲线列


# ── ⑧ S2 饥饿传导的派生判定语义 ────────────────────────────────


def test_s2_starvation_semantics_incomplete_vs_failed(tmp_path):
    """⑧：档位缺失 → 数据不足；三档齐但超限 → 未达标；三档齐且行 passed → 达标。"""
    incomplete = _summary_row(_gid(10.0, ve=0.2), verify_error=0.2)
    failed = _summary_row(_gid(10.0, ve=0.5), verify_error=0.5)
    src = _make_exp_dir(
        tmp_path,
        [incomplete, failed],
        starvation=[
            _star_row(10.0, 0.10, 0.70, 0.5),                      # 完整 + passed
        ],
    )
    # 两个组同元组：共享该元组行——为区分语义，直接用 assess_group 单测
    outputs = load_experiment_outputs(src)
    star = starvation_by_tuple(outputs.starvation)
    a = assess_group(incomplete, star)
    r8 = a.reading("s2_starvation")
    assert r8.verdict is True and r8.value == 1.5 and "最坏档" in r8.note
    # 档位缺失（构造只有基准档有值的元组行；注入键 = 该组自己的元组）
    partial = {"N": 10.0, "DELTA": 0.10, "T_BASE": 0.70, "exam_weight_high": 0.5,
               "ratio_ve0.20": 1.0, "ratio_ve0.35": None, "ratio_ve0.50": None,
               "reference": 0.2, "limit": 2.0, "passed": False}
    b = assess_group(incomplete, {**star, (10.0, 0.10, 0.70, 0.5): partial})
    r8b = b.reading("s2_starvation")
    assert r8b.verdict is None and "缺失" in r8b.note       # 数据不足
    # 三档齐但 passed=False（超限）→ 未达标 + 相对差距
    over = {**partial, "ratio_ve0.35": 1.5, "ratio_ve0.50": 2.5, "passed": False}
    c = assess_group(incomplete, {**star, (10.0, 0.10, 0.70, 0.5): over})
    r8c = c.reading("s2_starvation")
    assert r8c.verdict is False and r8c.value == 2.5
    assert r8c.gap == pytest.approx(0.25)
    # 行内自洽守护：全档增幅 ≤ limit 却 passed=False（数据损坏）→ 数据不足，
    # 不采信任一侧（防「坏行静默放行/静默打杀」）
    corrupt = {**over, "ratio_ve0.50": 1.4, "passed": False}   # 全档 ≤ 2 但 False
    d = assess_group(incomplete, {**star, (10.0, 0.10, 0.70, 0.5): corrupt})
    r8d = d.reading("s2_starvation")
    assert r8d.verdict is None and "矛盾" in r8d.note
    corrupt2 = {**over, "ratio_ve0.50": 2.7, "passed": True}   # 有档 > 2 却 True
    e = assess_group(incomplete, {**star, (10.0, 0.10, 0.70, 0.5): corrupt2})
    r8e = e.reading("s2_starvation")
    assert r8e.verdict is None and "矛盾" in r8e.note


# ── ac3：三节齐全 + CSV 路径 + 表格可渲染 ───────────────────────


def test_report_sections_csv_paths_and_renderable_tables(tmp_path):
    rows = [
        _qualified_group(10.0, tta=150.0),
        _summary_row(_gid(10.0, ve=0.5), verify_error=0.5,
                     values={**_QUALIFIED, "median_time_to_active": None},
                     ratio=None),
        _arm_row("ts-kernel.route", "thompson_control", "kernel.route",
                 router="kernel.route"),
        _arm_row("ts-route_thompson", "thompson_control", "route_thompson",
                 router="route_thompson"),
        _arm_row("rx-0.25", "reviewer_x", "rx0.25", reviewer_x=0.25),
    ]
    src = _make_exp_dir(tmp_path, rows)
    out = tmp_path / "out"
    result = build_report(src, out)

    # 三节齐全（+ §4 定稿建议）
    for head in ("## 1. 九项度量达成表", "## 2. 参数敏感性",
                 "## 3. Thompson 对照结论", "## 4. params.yaml 定稿建议"):
        assert head in result.md, head
    # CSV 产物路径写进报告且文件真实存在
    assert f"{(tmp_path / 'out').as_posix()}/sensitivity.csv" in result.md
    assert f"{(tmp_path / 'out').as_posix()}/group_targets.csv" in result.md
    assert result.paths["sensitivity_csv"].is_file()
    assert result.paths["group_targets_csv"].is_file()
    assert result.paths["report_md"].is_file()

    # markdown 表格逐块可渲染：列数一致 + 第二行是分隔行 + 至少 3 列
    blocks = _md_tables(result.md)
    assert len(blocks) >= 4
    for block in blocks:
        widths = {line.count("|") for line in block}
        assert len(widths) == 1, f"表格列数不一致：{block[0]}"
        sep = block[1]
        assert set(sep) <= {"|", "-", " "} and "---" in sep, sep
        assert min(widths) >= 3 + 1  # 至少 3 列（4 条竖线）


# ── 敏感性 CSV 与 Thompson 结论的数字正确性 ─────────────────────


def test_sensitivity_rows_marginal_aggregation(tmp_path):
    rows = [
        _qualified_group(10.0, tta=150.0),
        _summary_row(_gid(6.0), N=6.0, verify_error=0.5,
                     values={**_QUALIFIED, "promotion_precision": 0.5},
                     ratio=None),
        _arm_row("ts-kernel.route", "thompson_control", "kernel.route",
                 router="kernel.route"),
        _arm_row("ts-route_thompson", "thompson_control", "route_thompson",
                 router="route_thompson"),
        _arm_row("rx-0.25", "reviewer_x", "rx0.25", reviewer_x=0.25),
    ]
    src = _make_exp_dir(tmp_path, rows)
    outputs = load_experiment_outputs(src)
    sens = sensitivity_rows(outputs.summaries)

    dims = {(r["dimension"], r["value"]): r for r in sens}
    # 主矩阵维：N 有两档，各 1 组 3 runs；均值 = 组均值本身
    assert dims[("N", 10.0)]["n_groups"] == 1
    assert dims[("N", 10.0)]["n_runs"] == 3
    assert dims[("N", 10.0)]["promotion_precision_mean"] == pytest.approx(0.90)
    assert dims[("N", 6.0)]["promotion_precision_mean"] == pytest.approx(0.5)
    # verify_error 维只在 matrix 实验内取值（附加臂不混入）
    assert set(v for (d, v) in dims if d == "verify_error") == {0.2, 0.5}
    # 路由臂维：两档；REVIEWER_X 维：一档 1 组
    assert {v for (d, v) in dims if d == "router"} == {"kernel.route",
                                                       "route_thompson"}
    assert dims[("reviewer_x", 0.25)]["n_groups"] == 1
    # 重放通过率：全达标夹具全为 1.0
    assert dims[("N", 10.0)]["replay_pass_rate"] == 1.0
    # 关键指标列齐备
    row = dims[("N", 10.0)]
    for m in KEY_METRICS:
        assert f"{m}_mean" in row and f"{m}_std" in row


def test_thompson_conclusion_numbers_and_direction():
    base = {"experiment": "thompson_control", "arm": "kernel.route",
            "router": "kernel.route", "n_seeds": 3,
            "regret_total_mean": 400.0, "regret_total_std": 10.0,
            "regret_per_task_mean": 0.25,
            "promotion_precision_mean": 0.6, "promotion_recall_mean": 0.0}
    ts = {**base, "arm": "route_thompson", "router": "route_thompson",
          "regret_total_mean": 360.0, "regret_total_std": 12.0,
          "regret_per_task_mean": 0.22}
    out = thompson_conclusion([base, ts])
    assert out["verdict"] == "ts_lower"
    assert out["regret_total_delta"] == pytest.approx(-40.0)
    assert out["regret_total_delta_pct"] == pytest.approx(-10.0)
    assert "低 10.0%" in out["conclusion"] and "对照组而非替代" in out["conclusion"]

    out_hi = thompson_conclusion([{**base, "regret_total_mean": 350.0}, ts])
    assert out_hi["verdict"] == "ts_higher"
    assert "高" in out_hi["conclusion"]

    out_missing = thompson_conclusion([base])
    assert out_missing["verdict"] == "insufficient"
    assert "数据不齐" in out_missing["conclusion"]


# ── CLI 与真实数据集成 ──────────────────────────────────────────


def test_cli_main_writes_all_outputs(tmp_path, capsys):
    src = _make_exp_dir(tmp_path, [
        _qualified_group(10.0, tta=150.0),
        _qualified_group(6.0, tta=90.0),
    ])
    out = tmp_path / "out"
    assert main(["--from", str(src), "--out", str(out)]) == 0
    captured = capsys.readouterr().out
    assert "推荐 " + _gid(6.0) in captured
    for name in ("sim_report.md", "sensitivity.csv", "group_targets.csv"):
        assert (out / name).is_file()


def test_missing_summary_raises(tmp_path):
    src = tmp_path / "empty"
    src.mkdir()
    with pytest.raises(FileNotFoundError):
        load_experiment_outputs(src)


def test_real_full_outputs_if_present(tmp_path):
    """真实全矩阵产物集成：162 组评估、9 行达成表、选择器有结论（不预设达标态）。

    results/experiments-full 是 M0 门禁终检产物、在仓内恒存在（SPEC-5 §6.3：
    该条件分支属死代码）——缺失按显式失败处理（全仓 0 skip 纪律，技术债③
    DECISIONS 2026-10-10），不设静默跳过路径。
    """
    real = Path(__file__).resolve().parents[1] / "results" / "experiments-full"
    if not (real / "summary.csv").is_file():
        pytest.fail("results/experiments-full 不存在：M0 门禁产物应在仓内"
                    "（全仓 0 skip，SPEC-5 §6.3 / DECISIONS 2026-10-10）")
    result = build_report(real, tmp_path / "out")
    assert len(result.assessments) == 162
    rows = _parse_section1_table(result.md)
    assert len(rows) == 9
    assert result.selection.featured is not None
    assert "## 3. Thompson 对照结论" in result.md
    # round-4 起六件套已逐 run 落 ⑥ 曲线列（regret_late_over_early）：
    # ⑥ 必须有判定（不再「数据不足」）；当前全矩阵 162/162 组收窄比 < 1。
    r6 = next(r for r in result.assessments[0].readings
              if r.target.key == "regret_narrowing")
    assert r6.verdict is not None
    assert r6.value is not None and r6.value < 1.0
