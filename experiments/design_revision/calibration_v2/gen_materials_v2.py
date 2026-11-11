#!/usr/bin/env python3
"""E0 校准材料 v2 生成器（R1-T6；HANDOVER §10.2）。

对 v1（冻结不动，`../calibration/`）的勘误与重建：

- **去泄题**：夹具代码剥离模块 docstring（v1 的「夹具目标①：命令注入**漏洞版**
  ——…」直接给答案）与变体名注释；题面去掉类别号/文件路径标签/「预期判读」；
  blind_id 换盐重排（旧→新映射只在 truth_map，不随盲卷下发）；
- **双问题分立**：原主张真假（claim：real/refuted/inconclusive）与 PoC 是否
  有效（poc：effective/ineffective/not_applicable/not_runnable）拆成两个独立
  判定字段——「代码有漏洞」≠「该 PoC 已证明漏洞」；
- **根因分组**：第 1/7/8/9 类共用同一 vuln 源码（驱动变体不产生新根因）→
  同一 root_cause_group；按组统计 + 逐 item 结果双面报告；
- **真值映射单一来源**：``truth_map_v2.json``（item_id ↔ blind_id ↔ 设计真值
  ↔ root_cause_group ↔ old_item_id ↔ 家族口径）——消除 v1 的 TRA/PAT 标签
  不一致（family 口径以夹具目录/题库 item_ref 为准，映射处唯一裁决）；
- v1 材料零触碰；本生成器确定性（同输入同输出，内容哈希入 manifest）。

用法：``python3 gen_materials_v2.py``（在 calibration_v2/ 内产四件套）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
V1_CAL = HERE.parent / "calibration"
V1_SHEET = V1_CAL / "judging_sheet.jsonl"
V1_MANIFEST = HERE.parent / "manifests" / "e0_calibration_set.jsonl"
FIXTURES = HERE.parents[2] / "qswarm" / "sandbox" / "fixtures"

#: 类别 → 根因组（§10.2-4：1/7/8/9 同 vuln 源码同组；②–⑥ 各自独立样本）。
_KLASS_GROUP = {
    1: "rcg-fixture-vuln", 7: "rcg-fixture-vuln", 8: "rcg-fixture-vuln",
    9: "rcg-fixture-vuln",
    2: "rcg-fixture-fix", 3: "rcg-fixture-echo-safe",
    4: "rcg-fixture-unreachable", 5: "rcg-fixture-missing-dep",
    6: "rcg-fixture-timeout",
}

#: 驱动描述（中性化：不含家族/变体/预期结论词）。
_DRIVER_DESC = (
    "该程序曾以下列方式被驱动执行：以命令行参数形式传入一段包含特殊字符的"
    "输入（payload 原文见下），并观察其行为。"
)

_LEAK_WORDS = ("漏洞版", "修复版", "安全版", "预期判读", "答案键", "夹具目标",
               "vuln", "fix", "echo_safe", "canary", "nonce", "violat")


def _load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()
            if x.strip()]


def _strip_module_docstring(source: str) -> str:
    """剥离模块 docstring（保留语义代码；v1 泄题主载体）。"""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        if len(tree.body) == 1:
            return ""
        lines = source.splitlines(keepends=True)
        end = tree.body[0].end_lineno
        return "".join(lines[end:])
    return source


def _strip_leak_comments(source: str) -> str:
    """剥离含泄题词的整行注释（保守面：宁多剥不漏泄）。"""
    out = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") and any(
            w.casefold() in stripped.casefold() for w in _LEAK_WORDS
        ):
            continue
        out.append(line)
    return "\n".join(out)


def _assert_no_leak(text: str, where: str) -> None:
    """泄题词硬门（生成器自检）：题面出现即断言失败（材料不出厂）。"""
    for w in ("漏洞版", "修复版", "安全版", "预期判读", "答案键", "夹具目标",
              "第 ⑦ 类", "第⑦类", "unconditional", "ineffective",
              "no_target_call", "echo_safe"):
        if w.casefold() in text.casefold():
            raise AssertionError(f"泄题词 {w!r} 出现在 {where}")


def _blind_id_v2(item_id: str) -> str:
    return "J2-" + hashlib.sha256(
        ("e0v2:" + item_id).encode("utf-8")).hexdigest()[:10]


def main() -> int:
    sheet = _load(V1_SHEET)
    manifest = {r["item_id"]: r for r in _load(V1_MANIFEST)}
    rows_sheet: list[dict] = []
    rows_judge: list[dict] = []
    truth_map: dict = {}

    for r in sheet:
        iid = r["item_id"]
        man = manifest.get(iid, {})
        fam = str(r.get("family", man.get("family", "unknown")))
        klass = man.get("klass")
        # ── 材料面（去泄题）─────────────────────────────────────
        materials = dict(r["materials"])
        if "target_source" in materials:
            src = _strip_leak_comments(_strip_module_docstring(
                str(materials["target_source"])))
            _assert_no_leak(src, f"{iid}.target_source")
            materials["target_source"] = src
            materials.pop("driver_attempt", None)
        if "driver_attempt" in materials:            # 题库项残留面同样剥离
            materials.pop("driver_attempt", None)
        # 家族标签不下发裁判盲卷（判定问题不依赖家族提示）；语言保留（判读前提）。
        # ── 双问题分立（claim vs poc）────────────────────────────
        poc_applicable = r.get("source") == "fixture"
        row_sheet = {
            "item_id": iid,
            "v2": True,
            "lang": r.get("lang"),
            "materials": materials,
            "question_claim": (
                "读代码：该程序是否存在题目主张的安全缺陷？"
                "（real=存在 / refuted=不存在 / inconclusive=无法判定）"
            ),
            "question_poc": (
                "另问（独立判定，不影响上一问）：文中描述的驱动输入若执行，"
                "能否有效触发上述缺陷？（effective / ineffective / "
                "not_applicable / not_runnable）"
            ) if poc_applicable else None,
            "owner_claim_judgment": None,
            "owner_poc_judgment": None,
            "owner_note": None,
        }
        # 泄题硬门只扫材料面（question 内的选项枚举词是判读词汇非提示）
        _assert_no_leak(json.dumps(materials, ensure_ascii=False),
                        f"{iid}.materials")
        rows_sheet.append(row_sheet)
        blind = {
            "blind_id": _blind_id_v2(iid),
            "lang": r.get("lang"),
            "materials": materials,
            "question_claim": row_sheet["question_claim"],
            "question_poc": row_sheet["question_poc"],
            "driver_context": _DRIVER_DESC if poc_applicable else None,
        }
        rows_judge.append(blind)
        # ── 真值映射单一来源 ────────────────────────────────────
        truth_map[iid] = {
            "blind_id": blind["blind_id"],
            "source": r.get("source"),
            # 家族口径唯一裁决：夹具项 = 夹具目录族；题库项 = manifest 族
            # （消除 v1 TRA/PAT 标签不一致——以映射处为准）。
            "family": (
                str(man.get("family", fam)) if r.get("source") == "seed_bank"
                else fam
            ),
            "klass": klass,
            "root_cause_group": (
                f"{_KLASS_GROUP[int(klass)]}-{fam}" if klass else
                f"rcg-bank-{iid}"
            ),
            "design_truth_claim": man.get("ground_truth"),
            "old_item_id": iid,
            "item_ref": man.get("item_ref"),
        }

    # ID 唯一性 / 映射完整性硬门
    ids = [r["item_id"] for r in rows_sheet]
    blids = [r["blind_id"] for r in rows_judge]
    assert len(set(ids)) == len(ids) and len(set(blids)) == len(blids)
    assert set(truth_map) == set(ids)
    # 根因组内一致性：同组设计真值必须一致（组口径自洽）
    groups: dict[str, set] = {}
    for iid, t in truth_map.items():
        groups.setdefault(t["root_cause_group"], set()).add(
            t["design_truth_claim"])
    for g, truths in groups.items():
        assert len(truths) == 1, f"根因组 {g} 设计真值不一致：{truths}"

    HERE.mkdir(parents=True, exist_ok=True)
    (HERE / "judging_sheet_v2.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                  for r in rows_sheet) + "\n", encoding="utf-8")
    (HERE / "judge_materials_v2.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                  for r in rows_judge) + "\n", encoding="utf-8")
    (HERE / "truth_map_v2.json").write_text(
        json.dumps({
            "version": "e0-cal-v2",
            "n_items": len(truth_map),
            "root_cause_groups": {g: sorted(t["design_truth_claim"] for t in
                                            truth_map.values()
                                            if t["root_cause_group"] == g)
                                  for g in sorted(groups)},
            "items": truth_map,
        }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    digest = hashlib.sha256(
        ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                   for r in rows_judge)).encode("utf-8")).hexdigest()[:16]
    (HERE / "materials_v2_manifest.json").write_text(
        json.dumps({
            "generator": "gen_materials_v2.py",
            "n_items": len(rows_judge),
            "content_hash": digest,
            "leak_gate": "生成器内嵌泄题词硬门（_assert_no_leak）",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"v2 材料生成：{len(rows_judge)} 项 · 盲卷哈希 {digest} · "
          f"根因组 {len(groups)} 组")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
