"""simulator.report：SPEC-4 §4 输出物——sim_report.md 与参数敏感性 CSV。

从 :mod:`qswarm.simulator.experiments` 的落盘输出（六件套中的确定性载荷
``summary.csv`` / ``starvation.csv``，可选 ``results.json`` 元数据）聚合生成：

========================  =====================================================
产物                      内容
========================  =====================================================
``out/sim_report.md``     §1 九项度量达成表（实测 / SPEC-4 §2 目标 / 判定 / 差距）
                          + §2 参数敏感性（数据表 CSV 路径 + 边缘均值±std 摘要）
                          + §3 Thompson 对照结论（同参数 TS vs 默认路由 regret）
                          + §4 params.yaml 定稿建议（选择器结论）
``out/sensitivity.csv``   各扫描维度（N/δ/T_BASE/exam 上限/verify_error/REVIEWER_X/
                          TILT 臂/路由臂）对关键指标的均值±std 边缘数据表
``out/group_targets.csv`` 逐参数组 9 目标判定、差距与排名（选择器支撑数据）
========================  =====================================================

判定口径（SPEC-4 §2 逐字阈值 + 三处保守裁定，已记 ``docs/spec/DECISIONS.md``）：

- 阈值边界含等号（≥/≤ 按 §2 原文）；⑦「播种 < 50」与⑥「收窄」为严格比较；
- **无观测（None）→ 未达标**：如中位晋升时长无观测、退化响应无熄灭样本，
  「无证据」不视作达标（不静默放行）；
- ⑥ 探索 regret「随任务数单调收窄」需要 regret 曲线，六件套 schema 只持久化
  终点读数——仅当组级可选列 ``regret_late_over_early``（曲线后段/前段平均差距
  之比）存在时判定（严格 < 1 达标），否则记「数据不足（按未达标计）」；
- ⑧ S2 饥饿传导是元组级派生目标：组按 (N, δ, T_BASE, exam) 元组查
  ``starvation.csv`` 的 ``passed``（各 verify_error 档增幅倍数 ≤ 2×）；
  行内 ``passed`` 与增幅读数互相矛盾时按数据不足（数据损坏不采信任一侧）。

推荐参数组选择器（SPEC-4 §4-1）：**满足全部 9 项目标的参数组**中取中位晋升时长
（跨种子均值）最短者；无达标组时取「最接近组」——排序键（未达标数, 无观测数,
相对差距和, 中位晋升时长, group_id）字典序最小，并在报告中逐项列出与目标的
差距——**绝不静默选组**。

本模块只读 CSV/JSON、纯 stdlib，不 import kernel/engine（聚合层零机制耦合，
fixture 可直接构造实验输出做边界值测试）。
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "SPEC4_TARGETS",
    "KEY_METRICS",
    "SENSITIVITY_DIMENSIONS",
    "GroupAssessment",
    "MetricReading",
    "ReportResult",
    "Selection",
    "Target",
    "assess_group",
    "build_report",
    "judge",
    "load_experiment_outputs",
    "main",
    "relative_gap",
    "render_markdown",
    "sensitivity_rows",
    "select_group",
    "thompson_conclusion",
]


# ── SPEC-4 §2 目标注册表（9 项，顺序 = 报告行序）──────────────────


@dataclass(frozen=True)
class Target:
    """一项 §2 度量的目标定义（阈值边界语义由 :func:`judge` 统一裁定）。"""

    idx: int                 # ①..⑨（报告行序）
    key: str                 # 规范键（summary.csv 列前缀 / 派生目标键）
    label: str               # 中文名（§2 表首列）
    target_text: str         # §2 目标原文（报告逐字引用）
    kind: str                # higher | lower | strict_lower | equal | narrowing | derived
    threshold: float | None  # 数值阈值（narrowing 恒 1.0 严格；derived 为 2.0 上限）
    spec: str                # 口径注（报告「口径」节与 gap 说明引用）


#: §2 九项目标（阈值逐字对照 SPEC-4 §2 表；target_text 原文引用）。
SPEC4_TARGETS: tuple[Target, ...] = (
    Target(1, "promotion_precision", "晋升查准", "≥ 0.90",
           "higher", 0.90, "被晋升为 active 的格子中真 q_t 位于该类型前 20% 的比例"),
    Target(2, "promotion_recall", "晋升查全", "≥ 0.70",
           "higher", 0.70, "前 20% 真能力格子中 500 任务内被晋升的比例"),
    Target(3, "median_time_to_active", "中位晋升时长", "≤ 200",
           "lower", 200.0, "真专家从影子到 active 的任务数中位数"),
    Target(4, "wrong_extinguish_rate", "误熄灭率", "≤ 0.05",
           "lower", 0.05, "非退化型 active 格子被衰退熄灭的比例"),
    Target(5, "degradation_response", "退化响应", "≤ 150",
           "lower", 150.0, "退化型从 q 漂移到熄灭的任务数中位数"),
    Target(6, "regret_narrowing", "探索 regret", "随任务数单调收窄",
           "narrowing", 1.0,
           "后段/前段窗口平均差距之比 regret_late_over_early 严格 < 1"
           "（六件套无曲线列时记数据不足）"),
    Target(7, "cold_start_length", "冷启动长度", "播种 < 50",
           "strict_lower", 50.0, "超均匀基线 +5pp 的任务数（播种 vs 无播种）"),
    Target(8, "s2_starvation", "S2 饥饿传导", "≤ 2×",
           "derived", 2.0,
           "verify_error 扫描下中位晋升时长增幅（元组级派生，查 starvation.csv）"),
    Target(9, "replay_equivalent", "重放等价", "100%",
           "equal", 1.0, "任意时点 replay 重建 cells 与在线一致（回归项）"),
)

_TARGETS_BY_KEY: dict[str, Target] = {t.key: t for t in SPEC4_TARGETS}

#: ⑥ 判定用的可选组级列（曲线后段/前段平均差距之比；六件套缺省无此列）。
REGRET_RATIO_COL = "regret_late_over_early"

#: 敏感性 CSV 的关键指标列（数值型 8 项；⑨ 重放等价以组级通过率单列）。
KEY_METRICS: tuple[str, ...] = (
    "promotion_precision",
    "promotion_recall",
    "median_time_to_active",
    "wrong_extinguish_rate",
    "degradation_response",
    "regret_total",
    "regret_per_task",
    "cold_start_length",
)

#: 敏感性扫描维度：(列名, experiment 过滤, 展示名)——前 5 维为主矩阵（§3），
#: 后 3 维为附加单变量实验的变量。
SENSITIVITY_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    ("N", "matrix", "promote.N"),
    ("DELTA", "matrix", "promote.δ"),
    ("T_BASE", "matrix", "tilt.T_BASE"),
    ("exam_weight_high", "matrix", "exam 权重上限"),
    ("verify_error", "matrix", "verify_error"),
    ("reviewer_x", "reviewer_x", "REVIEWER_X"),
    ("tilt_gamma", "tilt_fixed_vs_adaptive", "TILT 臂"),
    ("router", "thompson_control", "路由臂"),
)

_MATRIX_EXP = "matrix"

#: 达标 / 未达标 / 数据不足 的规范词（报告与 CSV 同词，测试可断言）。
V_PASS = "达标"
V_FAIL = "未达标"
V_NODATA = "数据不足（按未达标计）"


# ── 读取与解析 ───────────────────────────────────────────────────


def _f(s: Any) -> float | None:
    """CSV 单元格 → float（空串/None → None；其余失败即 ValueError 上抛）。"""
    if s is None:
        return None
    s = str(s).strip()
    return float(s) if s else None


def _i(s: Any, default: int = 0) -> int:
    v = _f(s)
    return default if v is None else int(v)


def _tupkey(*vals: float) -> tuple[float, ...]:
    """浮点元组键（6 位舍入，防 CSV 往返的二进制误差）。"""
    return tuple(round(float(v), 6) for v in vals)


@dataclass(frozen=True)
class ExperimentOutputs:
    """experiments 六件套中参与聚合的部分（逐组摘要 + 饥饿表 + 可选元数据）。"""

    summaries: tuple[Mapping[str, Any], ...]     # summary.csv 逐组行（原值字符串→解析值）
    starvation: tuple[Mapping[str, Any], ...]    # starvation.csv 逐元组行
    meta: Mapping[str, Any]                      # results.json 顶层元数据（可选）
    source_dir: Path

    @property
    def matrix_groups(self) -> tuple[Mapping[str, Any], ...]:
        """主矩阵参数组摘要（选择器候选集）。"""
        return tuple(
            r for r in self.summaries
            if r["experiment"] == _MATRIX_EXP and r.get("arm") == "base"
        )


#: 数值型参数维度列（load 时转 float，保证排序/分组/元组键的数值语义）。
_PARAM_NUMERIC: tuple[str, ...] = (
    "N", "DELTA", "T_BASE", "exam_weight_high", "verify_error",
    "reviewer_x", "tilt_gamma",
)


def load_experiment_outputs(src: str | Path) -> ExperimentOutputs:
    """读取 experiments 输出目录：summary.csv + starvation.csv（必需）、results.json（可选）。"""
    src = Path(src)
    summaries: list[dict[str, Any]] = []
    with (src / "summary.csv").open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row: dict[str, Any] = {}
            for k, v in raw.items():
                if k.endswith("_mean") or k.endswith("_std"):
                    row[k] = _f(v)
                elif k in ("n_seeds", "n_days"):
                    row[k] = _i(v)
                elif k in _PARAM_NUMERIC:
                    row[k] = _f(v)
                else:
                    row[k] = v
            summaries.append(row)
    if not summaries:
        raise ValueError(f"{src}/summary.csv 无任何参数组行")

    starvation: list[dict[str, Any]] = []
    star_path = src / "starvation.csv"
    with star_path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = dict(raw)
            row["passed"] = str(raw.get("passed", "")).strip() == "True"
            for k in list(row):
                if k.startswith(("median_tta_ve", "ratio_ve")):
                    row[k] = _f(raw[k])
            starvation.append(row)

    meta_path = src / "results.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta.pop("rows", None)          # 逐 run 明细不进聚合层（summary 已含组级均值）
    return ExperimentOutputs(
        summaries=tuple(summaries), starvation=tuple(starvation),
        meta=meta, source_dir=src,
    )


# ── 判定（阈值边界语义统一在此）──────────────────────────────────


def judge(target: Target, value: float | None) -> bool | None:
    """§2 目标判定：达标 True / 未达标 False / 数据不足 None。

    边界语义（逐字对照 §2）：≥/≤ 含等号；⑦「播种 < 50」与⑥「收窄」严格比较；
    ⑨ 与 1.0 全等（bool 均值 1.0 ⇔ 全部种子通过）。value=None 一律 None。
    """
    if value is None:
        return None
    t = target.threshold
    if target.kind == "higher":
        return value >= float(t)
    if target.kind == "lower":
        return value <= float(t)
    if target.kind == "strict_lower":
        return value < float(t)
    if target.kind == "equal":
        return value == float(t)
    if target.kind == "narrowing":
        return value < 1.0          # 后段/前段严格下降才算「收窄」
    if target.kind == "derived":
        return value <= float(t)    # ⑧ 的最坏档增幅倍数 ≤ 2×
    raise ValueError(f"未知目标类型：{target.kind!r}")


def relative_gap(target: Target, value: float | None) -> float | None:
    """未达标时的相对差距（>0，越大越远；数据不足或已达标 → None）。

    higher：(阈值−值)/阈值；lower/strict_lower：(值−阈值)/阈值；narrowing：
    比值−1；equal：|值−阈值|。用于「最接近组」排序与差距报告。
    """
    v = judge(target, value)
    if v is not False or value is None:
        return None
    t = target.threshold
    if target.kind == "higher":
        return (float(t) - value) / float(t)
    if target.kind in ("lower", "strict_lower", "derived"):
        return (value - float(t)) / float(t)
    if target.kind == "narrowing":
        return value - 1.0
    if target.kind == "equal":
        return abs(value - float(t))
    return None


# ── 逐组评估 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricReading:
    """一组参数在单个目标上的读数与判定。"""

    target: Target
    value: float | None
    std: float | None
    verdict: bool | None               # True/False/None（None=数据不足）
    gap: float | None                  # 未达标相对差距
    note: str = ""                     # 展示辅助（无观测 / 需曲线列 / 元组派生…）

    @property
    def label(self) -> str:
        if self.verdict is True:
            return V_PASS
        if self.verdict is False:
            return V_FAIL
        return V_NODATA


@dataclass(frozen=True)
class GroupAssessment:
    """一个参数组的 9 目标完整评估（与 SPEC4_TARGETS 等长同序）。"""

    group_id: str
    experiment: str
    arm: str
    params: Mapping[str, Any]          # N/DELTA/T_BASE/exam_weight_high/verify_error/…
    n_seeds: int
    readings: tuple[MetricReading, ...]

    @property
    def verdicts(self) -> tuple[bool | None, ...]:
        return tuple(r.verdict for r in self.readings)

    @property
    def n_pass(self) -> int:
        return sum(1 for v in self.verdicts if v is True)

    @property
    def n_unmet(self) -> int:
        return len(self.verdicts) - self.n_pass

    @property
    def n_nodata(self) -> int:
        return sum(1 for v in self.verdicts if v is None)

    @property
    def qualifies(self) -> bool:
        """是否满足全部 9 项目标（数据不足按未达标计，不静默放行）。"""
        return self.n_pass == len(self.readings)

    @property
    def median_tta(self) -> float | None:
        """中位晋升时长读数（③；选择器的主排序键）。"""
        r = self.readings[2]
        return r.value

    @property
    def gap_sum(self) -> float:
        """有限相对差距之和（None 不计入）。"""
        return math.fsum(g for g in (r.gap for r in self.readings) if g is not None)

    def reading(self, key: str) -> MetricReading:
        return next(r for r in self.readings if r.target.key == key)


def _starvation_reading(
    row: Mapping[str, Any] | None,
) -> tuple[float | None, str, bool | None]:
    """⑧ 读数 →（最坏档增幅倍数, 注记, 判定覆盖）。

    - 行缺失 → ``(None, 'starvation.csv 缺该元组', None)``（数据不足）；
    - 任一 verify_error 档增幅缺失（该档中位晋升时长无观测）→ 数据不足：
      增幅不完整的元组不能判达标（与 ``metrics.s2_starvation_propagation``
      的 None-不通过口径一致，但区分「未达标」与「数据不足」）；
    - 三档完整 → 判定取行内 ``passed``（experiments 侧同一次计算的结论，
      单一事实源），读数 = 最坏档倍数；但先做行内自洽守护——
      ``passed`` 应等价于「全档增幅 ≤ limit」，矛盾即数据损坏 → 数据不足。
    """
    if row is None:
        return None, "starvation.csv 缺该元组", None
    ratios = {
        k: v for k, v in row.items() if k.startswith("ratio_ve")
    }
    if not ratios:
        return None, "starvation.csv 无增幅读数", None
    missing = sorted(k.removeprefix("ratio_ve") for k, v in ratios.items() if v is None)
    if missing:
        finite = [v for v in ratios.values() if v is not None]
        worst = max(finite) if finite else None
        return worst, f"增幅档缺失 ve={','.join(missing)}", None
    worst_k = max(ratios, key=lambda k: ratios[k])
    passed = bool(row.get("passed"))
    limit = _f(row.get("limit"))
    if limit is not None:
        # 行内自洽守护：experiments 侧 passed ⟺ 全档增幅 ≤ limit（同一次计算）。
        # 两侧矛盾说明数据损坏，任何一侧都不采信 → 数据不足（不静默放行）。
        if all(v <= limit for v in ratios.values()) != passed:
            return (
                float(ratios[worst_k]),
                "passed 与增幅读数矛盾（数据损坏）",
                None,
            )
    return (
        float(ratios[worst_k]),
        f"最坏档 ve={worst_k.removeprefix('ratio_ve')}",
        passed,
    )


def assess_group(
    summary: Mapping[str, Any],
    starvation_by_tuple: Mapping[tuple[float, ...], Mapping[str, Any]],
) -> GroupAssessment:
    """一个参数组摘要 → 9 目标评估（readings 与 SPEC4_TARGETS 同序）。"""
    readings: list[MetricReading] = []
    for target in SPEC4_TARGETS:
        note = ""
        verdict_override: bool | None = None
        has_override = False
        if target.key == "s2_starvation":                       # ⑧ 元组级派生
            tkey = _tupkey(
                summary["N"], summary["DELTA"],
                summary["T_BASE"], summary["exam_weight_high"],
            )
            value, note, verdict_override = _starvation_reading(
                starvation_by_tuple.get(tkey)
            )
            std = None
            has_override = True
        elif target.key == "regret_narrowing":                  # ⑥ 可选曲线列
            value = _f(summary.get(REGRET_RATIO_COL))
            std = None
            if value is None:
                # 列形态演化回退：同读数的逐 run 度量列（*_mean/*_std）亦可判定
                value = _f(summary.get(f"{REGRET_RATIO_COL}_mean"))
                std = _f(summary.get(f"{REGRET_RATIO_COL}_std"))
            if value is None:
                note = "无 regret 曲线列（六件套 schema）"
        else:                                                   # 直读 mean/std 列
            value = summary.get(f"{target.key}_mean")
            std = summary.get(f"{target.key}_std")
        if has_override:
            verdict = verdict_override
        else:
            verdict = judge(target, value)
        gap = relative_gap(target, value) if verdict is False else None
        readings.append(MetricReading(
            target=target, value=value, std=std, verdict=verdict,
            gap=gap, note=note,
        ))
    return GroupAssessment(
        group_id=str(summary["group_id"]),
        experiment=str(summary["experiment"]),
        arm=str(summary["arm"]),
        params={
            k: summary.get(k)
            for k in ("N", "DELTA", "T_BASE", "exam_weight_high",
                      "verify_error", "reviewer_x", "tilt_gamma", "router")
        },
        n_seeds=int(summary.get("n_seeds", 0) or 0),
        readings=tuple(readings),
    )


def starvation_by_tuple(starvation: Sequence[Mapping[str, Any]]
                        ) -> dict[tuple[float, ...], Mapping[str, Any]]:
    """starvation.csv 行 → (N, δ, T_BASE, exam) 元组键索引。"""
    return {
        _tupkey(r["N"], r["DELTA"], r["T_BASE"], r["exam_weight_high"]): r
        for r in starvation
    }


# ── 推荐参数组选择器（SPEC-4 §4-1）───────────────────────────────


@dataclass(frozen=True)
class Selection:
    """选择器结果：达标组为空时 fallback=True，closest 为最接近组（绝不静默选组）。"""

    qualifying: tuple[GroupAssessment, ...]
    recommended: GroupAssessment | None     # 全达标组中中位晋升时长最短者
    closest: GroupAssessment | None         # 无达标组时的最接近组（= recommended 时同者）
    fallback: bool
    rule: str                               # 排序规则说明（写进报告）

    @property
    def featured(self) -> GroupAssessment | None:
        """报告 §1 展示的组：推荐组优先，否则最接近组。"""
        return self.recommended if self.recommended is not None else self.closest


def _closest_key(a: GroupAssessment) -> tuple:
    """最接近组排序键：(未达标数, 无观测数, 相对差距和, 中位晋升时长, group_id)。"""
    tta = a.median_tta
    return (
        a.n_unmet,
        a.n_nodata,
        round(a.gap_sum, 12),
        math.inf if tta is None else tta,
        a.group_id,
    )


def select_group(assessments: Sequence[GroupAssessment]) -> Selection:
    """§4-1 选择器：全达标组取中位晋升时长最短；无达标组输出最接近组 + 差距。

    - 达标分支排序键：(中位晋升时长（None=+∞）, group_id)——确定性平手；
    - 最接近分支排序键见 :func:`_closest_key`；
    - 空输入 → 全空 Selection（调用方决定如何报错）。
    """
    rule = ("全达标组中取中位晋升时长（跨种子均值）最短者；"
            "无达标组时按（未达标数, 无观测数, 相对差距和, 中位晋升时长, group_id）"
            "取最接近组并逐项报告差距")
    qualifying = tuple(a for a in assessments if a.qualifies)
    if qualifying:
        best = min(
            qualifying,
            key=lambda a: (math.inf if a.median_tta is None else a.median_tta,
                           a.group_id),
        )
        return Selection(qualifying, best, best, False, rule)
    ranked = sorted(assessments, key=_closest_key)
    return Selection((), None, ranked[0] if ranked else None, bool(ranked), rule)


# ── 参数敏感性（边缘均值±std 数据表）─────────────────────────────


def _mean_std_of(vals: Sequence[float | None]) -> tuple[float | None, float | None, int]:
    """跳过 None 后均值 ± 样本标准差（n≥2；与 metrics.mean_std 同口径）。"""
    import statistics
    xs = [float(v) for v in vals if v is not None]
    if not xs:
        return None, None, 0
    return (
        statistics.fmean(xs),
        statistics.stdev(xs) if len(xs) >= 2 else None,
        len(xs),
    )


def sensitivity_rows(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """各扫描维度对关键指标的均值±std 边缘表（每行 = 维度 × 取值）。

    聚合语义：维度 D 取值 v 时，取该 experiment 下 D==v 的全部参数组，对每组
    关键指标的跨种子均值（``*_mean`` 列）再跨组求均值±std——即其它维度边缘化
    后的敏感性读数；``n_groups`` 参与组数、``n_runs`` 合计 run 数。
    """
    rows: list[dict[str, Any]] = []
    for col, experiment, label in SENSITIVITY_DIMENSIONS:
        buckets: dict[Any, list[Mapping[str, Any]]] = {}
        for r in summaries:
            if r["experiment"] != experiment:
                continue
            v = r.get(col)
            if v in (None, ""):
                continue
            key = round(float(v), 6) if not isinstance(v, str) else v
            buckets.setdefault(key, []).append(r)
        for value in sorted(buckets, key=lambda x: (isinstance(x, str), x)):
            members = buckets[value]
            row: dict[str, Any] = {
                "dimension": col, "label": label, "value": value,
                "experiment": experiment,
                "n_groups": len(members),
                "n_runs": sum(int(m.get("n_seeds", 0) or 0) for m in members),
            }
            for m in KEY_METRICS:
                mean, std, _n = _mean_std_of(g[f"{m}_mean"] for g in members)
                row[f"{m}_mean"], row[f"{m}_std"] = mean, std
            ok = sum(
                1 for g in members
                if (x := _f(g.get("replay_equivalent_mean"))) is not None and x == 1.0
            )
            row["replay_pass_rate"] = ok / len(members) if members else None
            rows.append(row)
    return rows


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        for row in rows:
            w.writerow(["" if row.get(c) is None else row.get(c) for c in header])


def write_sensitivity_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """敏感性数据表落盘（列序固定：维度元信息 + 关键指标 mean/std + 重放通过率）。"""
    header = (
        "dimension", "label", "value", "experiment", "n_groups", "n_runs",
    ) + tuple(c for m in KEY_METRICS for c in (f"{m}_mean", f"{m}_std")) + (
        "replay_pass_rate",
    )
    _write_csv(path, header, rows)
    return path


def write_group_targets_csv(
    path: Path, assessments: Sequence[GroupAssessment], selection: Selection
) -> Path:
    """逐组 9 目标判定/差距 + 排名与选择结果落盘（选择器的支撑数据）。"""
    header = (
        "experiment", "arm", "group_id", "N", "DELTA", "T_BASE",
        "exam_weight_high", "verify_error", "n_seeds",
        "median_time_to_active_mean", "n_pass", "n_unmet", "n_nodata",
        "qualifies", "gap_sum",
    ) + tuple(c for t in SPEC4_TARGETS for c in (f"{t.key}_verdict", f"{t.key}_gap")) + (
        "selected",
    )
    featured = selection.featured
    rows: list[dict[str, Any]] = []
    for a in sorted(
        assessments,
        key=lambda x: (_closest_key(x)[:3], x.group_id),
    ):
        row: dict[str, Any] = {
            "experiment": a.experiment, "arm": a.arm, "group_id": a.group_id,
            **{k: a.params.get(k) for k in
               ("N", "DELTA", "T_BASE", "exam_weight_high", "verify_error")},
            "n_seeds": a.n_seeds,
            "median_time_to_active_mean": a.median_tta,
            "n_pass": a.n_pass, "n_unmet": a.n_unmet, "n_nodata": a.n_nodata,
            "qualifies": a.qualifies, "gap_sum": round(a.gap_sum, 6),
        }
        for r in a.readings:
            row[f"{r.target.key}_verdict"] = (
                "pass" if r.verdict is True
                else "fail" if r.verdict is False else "nodata"
            )
            row[f"{r.target.key}_gap"] = (
                None if r.gap is None else round(r.gap, 6)
            )
        row["selected"] = (
            "recommended" if selection.recommended is a
            else "closest" if selection.fallback and featured is a
            else ""
        )
        rows.append(row)
    _write_csv(path, header, rows)
    return path


# ── Thompson 对照（§3 附加实验）──────────────────────────────────

_THOMPSON_BASE = "kernel.route"
_THOMPSON_TS = "route_thompson"


def thompson_conclusion(summaries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """同参数下 TS vs 默认路由的 regret 对比数字与数据驱动结论。

    返回 {base, ts, regret_total_delta, regret_total_delta_pct, verdict,
    conclusion}；缺臂时 verdict='insufficient'，结论句说明缺什么。
    """
    arms = {
        r["arm"]: r for r in summaries if r["experiment"] == "thompson_control"
    }
    base, ts = arms.get(_THOMPSON_BASE), arms.get(_THOMPSON_TS)

    def _brief(row: Mapping[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {"present": False}
        return {
            "present": True,
            "router": row.get("router"),
            "n_seeds": row.get("n_seeds"),
            "regret_total_mean": row.get("regret_total_mean"),
            "regret_total_std": row.get("regret_total_std"),
            "regret_per_task_mean": row.get("regret_per_task_mean"),
            "promotion_precision_mean": row.get("promotion_precision_mean"),
            "promotion_recall_mean": row.get("promotion_recall_mean"),
        }

    out: dict[str, Any] = {
        "base": _brief(base), "ts": _brief(ts),
        "regret_total_delta": None, "regret_total_delta_pct": None,
        "verdict": "insufficient", "conclusion": "",
    }
    if not (out["base"]["present"] and out["ts"]["present"]):
        out["conclusion"] = (
            "thompson_control 两个臂（kernel.route / route_thompson）数据不齐，"
            "无法给出对照结论——请先跑 §3 附加实验（experiments --full 已含）。"
        )
        return out
    b, t = out["base"]["regret_total_mean"], out["ts"]["regret_total_mean"]
    if b is None or t is None or b == 0:
        out["conclusion"] = "两臂 regret_total 读数缺失，无法对比。"
        return out
    delta = t - b
    pct = delta / b * 100.0
    out["regret_total_delta"] = delta
    out["regret_total_delta_pct"] = pct
    bp, tp = out["base"]["regret_per_task_mean"], out["ts"]["regret_per_task_mean"]
    if delta < 0:
        out["verdict"] = "ts_lower"
        head = (f"同参数同种子表下，TS 臂（route_thompson）终点累计 regret 均值较"
                f"默认路由（kernel.route）低 {abs(pct):.1f}%"
                f"（{_fmt(b)} → {_fmt(t)}，各 {out['base']['n_seeds']} 种子）")
    elif delta > 0:
        out["verdict"] = "ts_higher"
        head = (f"同参数同种子表下，TS 臂（route_thompson）终点累计 regret 均值较"
                f"默认路由（kernel.route）高 {pct:.1f}%"
                f"（{_fmt(b)} → {_fmt(t)}，各 {out['base']['n_seeds']} 种子）")
    else:
        out["verdict"] = "tie"
        head = f"两臂 regret_total 均值持平（{_fmt(b)}）"
    tail = (
        "；按 DECISIONS（2026-09-29）Thompson sampling 为对照组而非替代——"
        + ("本次数据支持「后验采样在该任务量级下探索更有效」的观察，"
           "但替换默认路由属机制级变更，留待 M1 以更多种子/更长窗口复核。"
           if delta < 0 else
           "本次数据不支持以 TS 替换默认路由，维持 kernel.route 缺省。")
    )
    if bp is not None and tp is not None:
        tail += f" regret_per_task：{_fmt(bp)} → {_fmt(tp)}。"
    out["conclusion"] = head + tail
    return out


# ── Markdown 渲染 ────────────────────────────────────────────────


def _esc(s: Any) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _fmt(v: float | None, nd: int = 4) -> str:
    """读数格式化：None → 「—」；量级自适应小数位（大数不拖零）。"""
    if v is None:
        return "—"
    av = abs(v)
    if av >= 1000:
        return f"{v:,.0f}"
    if av >= 100:
        return f"{v:.1f}"
    if av >= 10:
        return f"{v:.2f}"
    return f"{v:.{nd}f}"


def _fmt_ms(mean: float | None, std: float | None, n: int | None = None) -> str:
    """mean±std 单元格：std 缺失只显 mean；尾缀样本数。"""
    body = "—" if mean is None else (
        _fmt(mean) if std is None else f"{_fmt(mean)} ± {_fmt(std)}"
    )
    return f"{body}（n={n}）" if n is not None else body


def _md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """GFM 表格（渲染正确性由构造保证；测试另做逐块校验）。"""
    head = "| " + " | ".join(_esc(h) for h in headers) + " |"
    sep = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(_esc(c) for c in row) + " |" for row in rows
    ]
    return "\n".join([head, sep, *body])


def _verdict_cell(r: MetricReading) -> str:
    mark = {"达标": "✅", "未达标": "❌", "数据不足（按未达标计）": "⚠️"}[r.label]
    return mark + " " + r.label


def _reading_cell(r: MetricReading, n: int) -> str:
    """实测单元格：读数 ±std（尾缀样本数）；注记并入同格（无观测时说明原因）。"""
    if r.value is None:
        return f"—（{r.note}）" if r.note else "—"
    body = _fmt_ms(r.value, r.std, n)
    return f"{body}（{r.note}）" if r.note else body


def _gap_cell(r: MetricReading) -> str:
    if r.verdict is True or r.value is None:
        return "—"
    t = r.target
    if t.kind == "higher":
        abs_gap = t.threshold - r.value
        word = "低于目标"
    elif t.kind in ("lower", "strict_lower", "derived"):
        abs_gap = r.value - t.threshold
        word = "超出目标"
    elif t.kind == "narrowing":
        abs_gap = r.value - 1.0
        word = "劣于收窄目标"
    else:
        abs_gap = abs(r.value - float(t.threshold))
        word = "偏离目标"
    rel = r.gap * 100 if r.gap is not None else None
    return f"{_fmt(abs_gap)}（{word} {rel:.1f}%）" if rel is not None else _fmt(abs_gap)


def _param_desc(a: GroupAssessment) -> str:
    p = a.params
    bits = [f"N={_fmt(_f(p.get('N')))}", f"δ={_fmt(_f(p.get('DELTA')))}",
            f"T_BASE={_fmt(_f(p.get('T_BASE')))}",
            f"exam={_fmt(_f(p.get('exam_weight_high')))}",
            f"ve={_fmt(_f(p.get('verify_error')))}"]
    return "，".join(bits)


def render_markdown(
    outputs: ExperimentOutputs,
    assessments: Sequence[GroupAssessment],
    selection: Selection,
    sens: Sequence[Mapping[str, Any]],
    thompson: Mapping[str, Any],
    *,
    out_dir: str | Path,
    now: datetime | None = None,
) -> str:
    """sim_report.md 全文（§1 达成表 §2 敏感性 §3 Thompson §4 定稿建议）。"""
    now = now or datetime.now(timezone.utc)
    meta = outputs.meta
    out_dir_posix = Path(out_dir).as_posix().removesuffix("/")
    sens_path = f"{out_dir_posix}/sensitivity.csv"
    targets_path = f"{out_dir_posix}/group_targets.csv"
    n_groups = len(outputs.matrix_groups)

    lines: list[str] = []
    lines.append("# M0 机制仿真报告（SPEC-4 §4 sim_report）")
    lines.append("")
    lines.append(
        f"- 生成：{now.strftime('%Y-%m-%d %H:%M:%S UTC')}；"
        f"输入：`{outputs.source_dir.as_posix()}`"
        + (f"（mode={meta.get('mode')}，种子={meta.get('seeds')}，"
           f"n_days={meta.get('n_days')}）" if meta else "")
    )
    lines.append(
        f"- 规模：主矩阵参数组 {n_groups} 个，组级摘要共 {len(outputs.summaries)} 条"
        f"（含附加实验臂）；判定对象 = 逐参数组跨种子均值。"
    )
    lines.append("- 判定口径：SPEC-4 §2 阈值逐字（≥/≤ 含等号；⑦「< 50」、⑥「收窄」"
                 "为严格比较）；**无观测（None）按未达标计**；⑥ 需可选曲线列 "
                 f"`{REGRET_RATIO_COL}`（六件套缺省无 → 数据不足）；⑧ 查 "
                 "`starvation.csv` 元组级判定（任一档增幅缺失 → 数据不足）。")
    lines.append("")

    lines.append("## 1. 九项度量达成表 —— 最接近组（**无满足全部 9 项目标的参数组**）" if selection.fallback
                 else "## 1. 九项度量达成表 —— 推荐参数组")
    lines.append("")
    lines.append(f"- 数据产物：参数敏感性 `{sens_path}`；逐组判定明细 `{targets_path}`。")
    lines.append("")

    # ── §1 达成表 ──
    feat = selection.featured
    if feat is None:
        lines.append("> 输入中没有任何主矩阵参数组，无法评估。")
    else:
        tag = "推荐参数组" if not selection.fallback else "最接近组"
        lines.append(
            f"**{tag} `{feat.group_id}`**（{_param_desc(feat)}；"
            f"{feat.n_seeds} 种子；达标 {feat.n_pass}/9 项）"
            + ("" if selection.fallback else
               f"，自 {len(selection.qualifying)} 个全达标组中按"
               "「中位晋升时长最短」选出")
        )
        lines.append("")
        # 目标 × 通过组数（全矩阵扫描的系统性问题一目了然；9 项排两列）
        cov_rows = []
        half = (len(SPEC4_TARGETS) + 1) // 2
        for left, right in zip(SPEC4_TARGETS, SPEC4_TARGETS[half:]):
            row = [
                f"{_circ(left.idx)} {left.label}",
                f"{sum(1 for a in assessments if a.reading(left.key).verdict is True)}"
                f"/{n_groups}",
            ]
            if right is not None:
                row += [
                    f"{_circ(right.idx)} {right.label}",
                    f"{sum(1 for a in assessments if a.reading(right.key).verdict is True)}"
                    f"/{n_groups}",
                ]
            else:
                row += ["", ""]
            cov_rows.append(row)
        cov = _md_table(
            ("目标", "全矩阵通过组数", "目标", "全矩阵通过组数"),
            cov_rows,
        )
        lines.append(cov)
        lines.append("")
        rows = []
        for r in feat.readings:
            t = r.target
            rows.append([
                _circ(t.idx),
                f"{t.label} `{t.key}`",
                t.target_text,
                _reading_cell(r, feat.n_seeds),
                _verdict_cell(r),
                _gap_cell(r),
            ])
        lines.append(_md_table(
            ("#", "度量", "SPEC-4 §2 目标", "实测（跨种子 mean±std）", "判定", "差距"),
            rows,
        ))
        lines.append("")
        if selection.fallback:
            best_pass = max(a.n_pass for a in assessments) if assessments else 0
            lines.append(
                f"**结论：无满足全部 9 项目标的参数组**（全矩阵最好为 {best_pass}/9 项）。"
                f"上表为最接近组 `{feat.group_id}`（选择规则：{selection.rule}）；"
                f"逐项差距见上表「差距」列与全部 {len(assessments)} 组的判定明细 "
                f"`{targets_path}`——**不定稿、不静默选组**。"
            )
        else:
            lines.append(
                f"**结论：`{feat.group_id}` 满足全部 9 项目标**，且为中位晋升时长最短者"
                f"（{len(selection.qualifying)} 个达标组参与排序；规则：{selection.rule}）。"
            )
    lines.append("")

    # ── §2 参数敏感性 ──
    lines.append("## 2. 参数敏感性（边缘均值±std）")
    lines.append("")
    lines.append(
        f"完整数据表：`{sens_path}`（每行 = 扫描维度 × 取值；关键指标为"
        "「其它维度边缘化后，逐组跨种子均值的跨组 mean±std」）。摘要："
    )
    lines.append("")
    sens_rows = []
    for r in sens:
        sens_rows.append([
            f"{r['label']} `{r['dimension']}`",
            _esc(r["value"]),
            str(r["n_groups"]),
            _fmt_ms(r["promotion_precision_mean"], r["promotion_precision_std"]),
            _fmt_ms(r["promotion_recall_mean"], r["promotion_recall_std"]),
            _fmt_ms(r["median_time_to_active_mean"], r["median_time_to_active_std"]),
            _fmt_ms(r["regret_per_task_mean"], r["regret_per_task_std"]),
        ])
    lines.append(_md_table(
        ("维度", "取值", "组数", "晋升查准", "晋升查全", "中位晋升时长", "regret/任务"),
        sens_rows,
    ))
    lines.append("")

    # ── §3 Thompson 对照 ──
    lines.append("## 3. Thompson 对照结论（§3 附加实验）")
    lines.append("")
    b, t = thompson.get("base", {}), thompson.get("ts", {})
    tt_rows = []
    for arm_key, arm in (("默认路由 kernel.route", b), ("TS 对照 route_thompson", t)):
        if not arm.get("present"):
            tt_rows.append([arm_key, "—", "—", "—", "—"])
            continue
        tt_rows.append([
            arm_key,
            _fmt_ms(arm.get("regret_total_mean"), arm.get("regret_total_std")),
            _fmt(arm.get("regret_per_task_mean")),
            _fmt(arm.get("promotion_precision_mean")),
            _fmt(arm.get("promotion_recall_mean")),
        ])
    lines.append(_md_table(
        ("臂", "regret_total（终点累计）", "regret_per_task", "晋升查准", "晋升查全"),
        tt_rows,
    ))
    lines.append("")
    lines.append(f"**结论**：{thompson.get('conclusion', '—')}")
    lines.append("")

    # ── §4 params.yaml 定稿建议 ──
    lines.append("## 4. params.yaml 定稿建议（SPEC-4 §4-1 / §5 门禁）")
    lines.append("")
    if feat is None:
        lines.append("输入无主矩阵参数组，无法给出定稿建议。")
    elif not selection.fallback:
        p = feat.params
        lines.append(
            f"建议按推荐参数组 `{feat.group_id}` 回填 params.yaml："
            f"`promote.N: {_fmt(_f(p.get('N')))}`、"
            f"`promote.DELTA: {_fmt(_f(p.get('DELTA')))}`、"
            f"`tilt.T_BASE: {_fmt(_f(p.get('T_BASE')))}`"
            f"（exam 权重上限={_fmt(_f(p.get('exam_weight_high')))}、"
            f"verify_error={_fmt(_f(p.get('verify_error')))} 为世界侧旋钮，不进契约）。"
            "定稿依据引用本报告 §1 达成表（9/9 项达标）。"
        )
    else:
        lines.append(
            "**定稿暂缓**：当前全矩阵无满足全部 9 项目标的参数组（§1），"
            "params.yaml 不做达标组回填（无达标组可回填）；扫描维度（N/δ/T_BASE/"
            "exam/verify_error）维持设计初值，非扫描维度的 M0 定稿候选参数按调参轮"
            "实证单独回填并记录 DECISIONS（本轮：SLIDING_WINDOW_DAYS=7、"
            "exam.COOLDOWN_DAYS=7，证据见 results/TUNING_LOG.md）；"
            "按 §5 门禁不放行 M1。缺口与建议方向："
        )
        lines.append("")
        gap_rows = [
            [
                _circ(r.target.idx),
                f"{r.target.label}（{r.target.target_text}）",
                _reading_cell(r, feat.n_seeds),
                _gap_cell(r),
            ]
            for r in feat.readings if r.verdict is not True
        ]
        lines.append(_md_table(
            ("#", "未达标目标", f"最接近组实测（`{feat.group_id}`）", "与目标差距"),
            gap_rows,
        ))
        lines.append("")
        lines.append(
            "上述差距即 M0 收尾前必须消除的机制缺口清单（晋升查准受 (role,node) 严格 "
            "top-⌈0.2n⌉ 口径与存款噪声制约、查全受 M=30 流量门槛在 500 任务窗内对 "
            "type 节点/低权重族的结构性排除、中位晋升时长受 S3 延迟与暗考及格率 "
            "p⁴(2−p) 制约、退化响应受 Wilson-UB 双窗检测在格级日样本量下的速度极限"
            "——机制层建议逐项见 docs/spec/DECISIONS.md 与 results/TUNING_LOG.md）。"
        )
    lines.append("")
    return "\n".join(lines)


_CIRC = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤", 6: "⑥", 7: "⑦", 8: "⑧", 9: "⑨"}


def _circ(i: int) -> str:
    return _CIRC.get(i, str(i))


# ── 编排入口与 CLI ───────────────────────────────────────────────


@dataclass(frozen=True)
class ReportResult:
    """build_report 的结构化产物（测试与上游编排直接消费）。"""

    md: str
    paths: Mapping[str, Path]
    selection: Selection
    assessments: tuple[GroupAssessment, ...]
    sensitivity: tuple[Mapping[str, Any], ...]
    thompson: Mapping[str, Any]


def build_report(
    src: str | Path,
    out_dir: str | Path,
    *,
    now: datetime | None = None,
) -> ReportResult:
    """读 experiments 输出 → 评估/选择/聚合 → 写 sim_report.md + 两份 CSV。"""
    outputs = load_experiment_outputs(src)
    star_idx = starvation_by_tuple(outputs.starvation)
    assessments = tuple(
        assess_group(s, star_idx) for s in outputs.matrix_groups
    )
    if not assessments:
        raise ValueError(f"{src} 的 summary.csv 中没有主矩阵参数组（experiment=matrix）")
    selection = select_group(assessments)
    sens = sensitivity_rows(outputs.summaries)
    thompson = thompson_conclusion(outputs.summaries)
    md = render_markdown(
        outputs, assessments, selection, sens, thompson,
        out_dir=out_dir, now=now,
    )

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "report_md": out / "sim_report.md",
        "sensitivity_csv": write_sensitivity_csv(out / "sensitivity.csv", sens),
        "group_targets_csv": write_group_targets_csv(
            out / "group_targets.csv", assessments, selection
        ),
    }
    paths["report_md"].write_text(md, encoding="utf-8")
    return ReportResult(
        md=md, paths=paths, selection=selection, assessments=assessments,
        sensitivity=tuple(sens), thompson=thompson,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """CLI：``python -m qswarm.simulator.report --from results/experiments-full --out out``。"""
    ap = argparse.ArgumentParser(
        prog="python -m qswarm.simulator.report",
        description="SPEC-4 §4 sim_report 生成器（达成表 + 敏感性 CSV + Thompson 结论）",
    )
    ap.add_argument("--from", dest="src", default="results/experiments-full",
                    help="experiments 输出目录（含 summary.csv/starvation.csv）")
    ap.add_argument("--out", default="out", help="报告输出目录（缺省 out/）")
    args = ap.parse_args(list(argv) if argv is not None else None)

    result = build_report(args.src, args.out)
    sel = result.selection
    print(f"[report] 参数组 {len(result.assessments)} 个："
          f"全达标 {len(sel.qualifying)}；"
          + (f"推荐 {sel.recommended.group_id}"
             if sel.recommended is not None else
             f"无达标组 → 最接近 {sel.closest.group_id}"
             if sel.closest is not None else "空输入"))
    for name, path in result.paths.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
