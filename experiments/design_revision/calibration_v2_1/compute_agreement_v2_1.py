#!/usr/bin/env python3
"""Strict, stdlib-only scoring of the blinded v2.1 calibration exercise.

Design labels are provisional, not an independent gold standard. This program
reports descriptive agreement, never an automatic calibration pass. Missing
answers are incomplete (including null PoC verdicts for a present PoC task).
Only explicitly absent PoC tasks are excluded from the PoC denominator.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CLAIM_ENUM = frozenset({"real", "refuted", "inconclusive"})
POC_ENUM = frozenset({"effective", "ineffective", "not_applicable", "not_runnable"})
VERSION = "e0-cal-v2.1"


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _enum(value: Any, allowed: frozenset[str], *, nullable: bool = False) -> bool:
    return (nullable and value is None) or (isinstance(value, str) and value in allowed)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    """Reject duplicate JSON keys, including duplicate truth item IDs."""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except ValueError as exc:
            raise ValueError(f"{path.name}:{number}: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{number}: expected JSON object")
        rows.append(row)
    return rows


def _index(rows: Any, key: str, name: str, problems: list[str]) -> dict[str, dict]:
    if not isinstance(rows, list):
        problems.append(f"{name}: expected a list of rows (not a pre-indexed dict)")
        return {}
    indexed = {}
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            problems.append(f"{name} row {number}: expected object")
            continue
        identifier = row.get(key)
        if not _nonempty(identifier):
            problems.append(f"{name} row {number}: missing/nonempty {key} required")
        elif identifier in indexed:
            problems.append(f"{name}: duplicate {key}: {identifier}")
        else:
            indexed[identifier] = row
    return indexed


def _same_ids(actual: set[str], expected: set[str], name: str, problems: list[str]) -> None:
    for identifier in sorted(expected - actual):
        problems.append(f"{name}: missing ID: {identifier}")
    for identifier in sorted(actual - expected):
        problems.append(f"{name}: unknown ID: {identifier}")


def _public_leaks(value: Any, private_ids: set[str], path: str = "materials") -> list[str]:
    """Reject private answer/mapping fields and literal private identifiers."""
    problems = []
    forbidden = {"item_id", "root_cause_group", "design_truth_claim", "design_truth_poc",
                 "claim_evidence", "poc_evidence", "old_blind_id", "owner_claim_judgment",
                 "owner_poc_judgment", "truth", "truth_claim", "truth_poc"}
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                problems.append(f"{path}: private field leaked: {key}")
            problems.extend(_public_leaks(child, private_ids, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            problems.extend(_public_leaks(child, private_ids, f"{path}[{index}]"))
    elif isinstance(value, str):
        for identifier in sorted(private_ids):
            if identifier in value:
                problems.append(f"{path}: private identifier leaked: {identifier}")
    return problems


def validate_mapping(sheet: list[dict], materials: list[dict], truth: dict) -> list[str]:
    """Validate the exact materials ↔ sheet ↔ truth bijection, without scoring."""
    problems: list[str] = []
    sheets = _index(sheet, "item_id", "sheet", problems)
    mats = _index(materials, "blind_id", "materials", problems)
    if not isinstance(truth, dict):
        return problems + ["truth: expected document object"]
    if truth.get("version") != VERSION:
        problems.append(f"truth: version must be {VERSION}")
    if not _nonempty(truth.get("label_status")):
        problems.append("truth: nonempty label_status required")
    items = truth.get("items")
    if not isinstance(items, dict):
        return problems + ["truth: items must be an object"]
    if not sheets or not mats or not items:
        problems.append("empty inputs: materials, sheet and truth items must be nonempty")
    valid_items = {}
    blinds: dict[str, str] = {}
    old_ids = set()
    for iid, item in items.items():
        if not _nonempty(iid) or not isinstance(item, dict):
            problems.append(f"truth: invalid item: {iid!r}")
            continue
        valid_items[iid] = item
        blind = item.get("blind_id")
        if not _nonempty(blind):
            problems.append(f"truth {iid}: nonempty blind_id required")
        elif blind in blinds:
            problems.append(f"truth: duplicate blind_id: {blind} ({blinds[blind]}, {iid})")
        else:
            blinds[blind] = iid
        if not _nonempty(item.get("root_cause_group")):
            problems.append(f"truth {iid}: nonempty root_cause_group required")
        if not _enum(item.get("design_truth_claim"), CLAIM_ENUM):
            problems.append(f"truth {iid}: invalid design_truth_claim enum")
        if "design_truth_poc" not in item or not _enum(item.get("design_truth_poc"), POC_ENUM, nullable=True):
            problems.append(f"truth {iid}: invalid/missing design_truth_poc enum")
        old = item.get("old_blind_id")
        if not _nonempty(old):
            problems.append(f"truth {iid}: nonempty old_blind_id required to check ID reuse")
        else:
            old_ids.add(old)
    _same_ids(set(sheets), set(valid_items), "sheet vs truth", problems)
    _same_ids(set(mats), set(blinds), "materials vs truth", problems)
    for blind in sorted((set(mats) | set(blinds)) & old_ids):
        problems.append(f"old blind ID reused: {blind}")
    sheet_blinds: set[str] = set()
    private_ids = set(valid_items) | old_ids | {
        item["root_cause_group"] for item in valid_items.values()
        if _nonempty(item.get("root_cause_group"))
    }
    for iid, row in sheets.items():
        blind = row.get("blind_id")
        if not _nonempty(blind):
            problems.append(f"sheet {iid}: nonempty blind_id required")
        elif blind in sheet_blinds:
            problems.append(f"sheet: duplicate blind_id: {blind}")
        else:
            sheet_blinds.add(blind)
        if iid in valid_items and blind != valid_items[iid].get("blind_id"):
            problems.append(f"sheet {iid}: blind_id does not match truth")
        for key, allowed in (("owner_claim_judgment", CLAIM_ENUM), ("owner_poc_judgment", POC_ENUM)):
            if key not in row or not _enum(row.get(key), allowed, nullable=True):
                problems.append(f"sheet {iid}: invalid/missing {key} enum")
        if "owner_note" not in row or (row["owner_note"] is not None and not isinstance(row["owner_note"], str)):
            problems.append(f"sheet {iid}: owner_note must be string or null")
    for blind, row in mats.items():
        if not _nonempty(row.get("question_claim")):
            problems.append(f"materials {blind}: nonempty explicit question_claim required")
        if not row.get("materials"):
            problems.append(f"materials {blind}: nonempty materials required")
        if "question_poc" not in row:
            problems.append(f"materials {blind}: explicit question_poc required (null for claim-only)")
        question = row.get("question_poc")
        if question is not None and not _nonempty(question):
            problems.append(f"materials {blind}: question_poc must be nonempty or null")
        item = valid_items.get(blinds.get(blind, ""))
        if item is not None:
            poc = item.get("design_truth_poc")
            if (question is None) != (poc is None):
                problems.append(f"materials {blind}: PoC question/truth null mismatch")
            if question is None:
                if "driver_context" not in row or row.get("driver_context") is not None:
                    problems.append(f"materials {blind}: claim-only driver_context must be explicit null")
                owner = sheets.get(blinds[blind], {})
                if owner.get("owner_poc_judgment") is not None:
                    problems.append(f"sheet {blinds[blind]}: claim-only owner PoC must be null")
            elif not (_nonempty(row.get("driver_context")) or (
                isinstance(row.get("driver_context"), dict) and bool(row["driver_context"])
            )):
                problems.append(f"materials {blind}: PoC task requires nonempty driver_context")
        problems.extend(_public_leaks(row, private_ids, f"materials {blind}"))
    return problems


def validate_judge(judge: list[dict], materials: list[dict]) -> list[str]:
    problems: list[str] = []
    rows = _index(judge, "blind_id", "judge", problems)
    mats = {row["blind_id"]: row for row in materials}
    for blind, row in rows.items():
        if blind not in mats:
            problems.append(f"judge: unknown blind_id: {blind}")
        for key, allowed in (("verdict", CLAIM_ENUM), ("poc_verdict", POC_ENUM)):
            if not _enum(row.get(key), allowed, nullable=True):
                problems.append(f"judge {blind}: invalid {key} enum")
        if blind in mats and mats[blind]["question_poc"] is None and row.get("poc_verdict") is not None:
            problems.append(f"judge {blind}: claim-only poc_verdict must be null")
    return problems


def _rate(hits: int, total: int) -> float | None:
    return hits / total if total else None


def _comparison(expected: list[str], left: dict, right: dict, items: dict) -> dict:
    answered = [iid for iid in expected if left.get(iid) is not None and right.get(iid) is not None]
    hits = {iid for iid in answered if left[iid] == right[iid]}
    groups: dict[str, list[str]] = {}
    for iid in expected:
        groups.setdefault(items[iid]["root_cause_group"], []).append(iid)
    per_group = {}
    for group, members in sorted(groups.items()):
        common = [iid for iid in members if iid in answered]
        agreed = sum(iid in hits for iid in members)
        per_group[group] = {
            "members": members, "expected": len(members), "answered_overlap": len(common),
            "agreed": agreed, "coverage": _rate(len(common), len(members)),
            "accuracy": _rate(agreed, len(common)),
            "full_denominator_accuracy": _rate(agreed, len(members)),
            "all_correct": len(common) == len(members) and agreed == len(members),
        }
    estimable = [g["accuracy"] for g in per_group.values() if g["accuracy"] is not None]
    fractions = [g["full_denominator_accuracy"] for g in per_group.values()]
    all_correct = sum(g["all_correct"] for g in per_group.values())
    return {
        "status": "complete" if len(answered) == len(expected) and expected else "incomplete",
        "estimate_status": "estimable" if answered else "not_estimable",
        "expected": len(expected), "answered_overlap": len(answered),
        "missing_overlap": len(expected) - len(answered), "agreed": len(hits),
        "accuracy": _rate(len(hits), len(answered)),
        "coverage": _rate(len(answered), len(expected)),
        "full_denominator_accuracy": _rate(len(hits), len(expected)),
        "expected_groups": len(groups), "answered_groups": len(estimable),
        "group_all_correct": all_correct,
        "group_all_correct_rate": _rate(all_correct, len(groups)),
        "group_macro_accuracy": sum(estimable) / len(estimable) if estimable else None,
        "group_macro_full_denominator_accuracy": sum(fractions) / len(fractions) if fractions else None,
        "per_group": per_group,
    }


def compute_agreement(sheet: list[dict], judge: list[dict], truth: dict, *, materials: list[dict]) -> dict:
    """Return independent claim/PoC metrics; raise ValueError on invalid input.

    ``truth`` is the complete truth document, not just its ``items`` mapping.
    Judge input must be raw rows so duplicate IDs cannot be silently lost.
    Pairwise accuracy uses answered overlap; coverage and conservative group
    metrics always retain the full expected task/group denominator.
    """
    problems = validate_mapping(sheet, materials, truth)
    if not problems:
        problems.extend(validate_judge(judge, materials))
    if problems:
        raise ValueError("; ".join(problems))
    items = truth["items"]
    sheets = {row["item_id"]: row for row in sheet}
    judges = {row["blind_id"]: row for row in judge}
    result: dict = {
        "version": VERSION, "label_status": truth["label_status"],
        "status": "pending_owner_confirmation",
        "calibration_gate": {
            "passed": False, "status": "pending_owner_confirmation",
            "reasons": ["owner_confirmation_not_verified", "genuinely_independent_judge_not_verified"],
            "automatic_score_threshold": None,
        },
        "counts": {
            "items": len(items),
            "claim_questions": len(items),
            "poc_questions": sum(item["design_truth_poc"] is not None for item in items.values()),
            "claim_only_items": sum(item["design_truth_poc"] is None for item in items.values()),
            "root_cause_groups": len({item["root_cause_group"] for item in items.values()}),
        },
        "interpretation": (
            "Descriptive agreement with provisional design labels, not calibration approval. "
            "Items sharing a root_cause_group are not independent roots. Accuracy uses "
            "answered overlap; group_macro_accuracy equally weights groups with overlap; "
            "group_macro_full_denominator_accuracy and group_all_correct_rate retain all "
            "expected groups and give missing answers no credit."
        ),
        "per_item": [],
    }
    if truth["label_status"] == "draft_requires_owner_confirmation":
        result["calibration_gate"]["reasons"].insert(0, "draft_design_labels")
    complete = True
    for dimension, owner_key, judge_key, truth_key in (
        ("claim", "owner_claim_judgment", "verdict", "design_truth_claim"),
        ("poc", "owner_poc_judgment", "poc_verdict", "design_truth_poc"),
    ):
        expected = [iid for iid, item in items.items() if item[truth_key] is not None]
        owner = {iid: sheets[iid].get(owner_key) for iid in expected}
        independent = {iid: judges.get(items[iid]["blind_id"], {}).get(judge_key) for iid in expected}
        design = {iid: items[iid][truth_key] for iid in expected}
        n_owner = sum(value is not None for value in owner.values())
        n_judge = sum(value is not None for value in independent.values())
        dimension_complete = n_owner == n_judge == len(expected)
        complete = complete and dimension_complete
        result[dimension] = {
            "status": "complete" if dimension_complete else "incomplete",
            "expected": len(expected), "owner_answered": n_owner, "judge_answered": n_judge,
            "owner_coverage": _rate(n_owner, len(expected)),
            "judge_coverage": _rate(n_judge, len(expected)),
            "owner_vs_truth": _comparison(expected, owner, design, items),
            "judge_vs_truth": _comparison(expected, independent, design, items),
            "judge_vs_owner": _comparison(expected, independent, owner, items),
        }
    result["scoring_status"] = "complete" if complete else "incomplete"
    for iid, item in items.items():
        blind = item["blind_id"]
        row = {"item_id": iid, "blind_id": blind, "root_cause_group": item["root_cause_group"]}
        for dimension, owner_key, judge_key, truth_key in (
            ("claim", "owner_claim_judgment", "verdict", "design_truth_claim"),
            ("poc", "owner_poc_judgment", "poc_verdict", "design_truth_poc"),
        ):
            owner = sheets[iid][owner_key]
            other = judges.get(blind, {}).get(judge_key)
            expected = item[truth_key] is not None
            row[dimension] = {
                "expected": expected, "owner": owner, "judge": other, "design_truth": item[truth_key],
                "status": "not_asked" if not expected else (
                    "complete" if owner is not None and other is not None else "incomplete"),
            }
        result["per_item"].append(row)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag, filename in (
        ("materials", "judge_materials_v2_1.jsonl"),
        ("sheet", "judging_sheet_v2_1.jsonl"),
        ("truth", "truth_map_v2_1.json"),
        ("judge", "judge_results_v2_1.jsonl"),
    ):
        parser.add_argument(f"--{flag}", type=Path, default=HERE / filename)
    args = parser.parse_args(argv)
    try:
        materials = load_jsonl(args.materials)
        sheet = load_jsonl(args.sheet)
        truth = load_json(args.truth)
        # An absent optional judge file means unanswered, never a passing score.
        judge = load_jsonl(args.judge) if args.judge.exists() else []
        result = compute_agreement(sheet, judge, truth, materials=materials)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "invalid_inputs", "problems": [str(exc)],
                  "calibration_gate": {"passed": False, "status": "pending_owner_confirmation"}}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
