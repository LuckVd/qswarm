#!/usr/bin/env python3
"""Register the owner's blind judgments into the frozen v2.2 judging sheet.

Fails closed: exact blind_id bijection, closed enums, claim-only PoC must stay
null, and an already-annotated sheet requires --force. Before/after snapshots
and hashes are kept next to this script; the frozen sheet's non-owner fields
are re-serialized byte-identically (canonical JSON, same order).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GEN_DIR = ROOT / "experiments" / "design_revision" / "calibration_v2_2"
CLAIM_ENUM = {"real", "refuted", "inconclusive"}
POC_ENUM = {"effective", "ineffective", "not_applicable", "not_runnable"}
OWNER_KEYS = ("owner_claim_judgment", "owner_poc_judgment", "owner_note")


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--answers", type=Path, default=HERE / "owner_answers_v2_2.jsonl")
    parser.add_argument("--force", action="store_true",
                        help="re-apply over an already annotated sheet (snapshots keep history)")
    args = parser.parse_args(argv)

    sheet_path = GEN_DIR / "judging_sheet_v2_2.jsonl"
    materials_path = GEN_DIR / "judge_materials_v2_2.jsonl"
    sheet = load_rows(sheet_path)
    materials = {row["blind_id"]: row for row in load_rows(materials_path)}
    answers = load_rows(args.answers)

    problems: list[str] = []
    sheet_ids = [row["blind_id"] for row in sheet]
    if len(set(sheet_ids)) != len(sheet_ids):
        problems.append("sheet has duplicate blind_id")
    answer_ids = [row["blind_id"] for row in answers]
    if len(set(answer_ids)) != len(answer_ids):
        problems.append("answers have duplicate blind_id")
    if set(answer_ids) != set(sheet_ids):
        problems.append("answers/sheet blind_id mismatch: "
                        f"missing={sorted(set(sheet_ids) - set(answer_ids))} "
                        f"unknown={sorted(set(answer_ids) - set(sheet_ids))}")
    if not args.force and any(any(row[k] is not None for k in OWNER_KEYS) for row in sheet):
        problems.append("sheet already carries owner labels; pass --force only to re-apply")
    claims = pocs = 0
    for row in answers:
        claim = row.get("owner_claim_judgment")
        if claim not in CLAIM_ENUM:
            problems.append(f"{row['blind_id']}: owner_claim_judgment must be one of {sorted(CLAIM_ENUM)}")
        note = row.get("owner_note")
        if note is not None and not isinstance(note, str):
            problems.append(f"{row['blind_id']}: owner_note must be string or null")
        poc = row.get("owner_poc_judgment")
        question = materials.get(row["blind_id"], {}).get("question_poc")
        if question is None:
            if poc is not None:
                problems.append(f"{row['blind_id']}: claim-only item must keep owner_poc_judgment null")
        elif poc not in POC_ENUM:
            problems.append(f"{row['blind_id']}: owner_poc_judgment must be one of {sorted(POC_ENUM)}")
        else:
            pocs += 1
    claims = sum(1 for row in answers if row.get("owner_claim_judgment") in CLAIM_ENUM)
    if problems:
        print("REFUSED — fix the answers file first:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    by_blind = {row["blind_id"]: row for row in answers}
    before = (canonical(sheet) + "\n").encode()
    annotated = []
    for row in sheet:
        answer = by_blind[row["blind_id"]]
        annotated.append({**row,
                          "owner_claim_judgment": answer["owner_claim_judgment"],
                          "owner_poc_judgment": answer["owner_poc_judgment"],
                          "owner_note": answer.get("owner_note")})
    after = (canonical(annotated) + "\n").encode()

    before_path = HERE / "judging_sheet_v2_2.before_owner_blind.jsonl"
    after_path = HERE / "judging_sheet_v2_2.owner_blind_applied.jsonl"
    before_path.write_bytes(before)
    after_path.write_bytes(after)
    with sheet_path.open("wb") as out:
        out.write(after)

    record = {
        "date": "2026-11-10",
        "source": "owner independent blind judgment (paper: blind_paper_v2_2.md)",
        "answers_sha256": digest(args.answers),
        "sheet_before_sha256": hashlib.sha256(before).hexdigest(),
        "sheet_after_sha256": hashlib.sha256(after).hexdigest(),
        "owner_claim_judgment": claims, "owner_poc_judgment": pocs,
        "distribution": {
            "claim": {value: sum(1 for row in answers if row["owner_claim_judgment"] == value)
                      for value in sorted(CLAIM_ENUM)},
            "poc": {value: sum(1 for row in answers if row["owner_poc_judgment"] == value)
                    for value in sorted(POC_ENUM)},
            "poc_null_claim_only": sum(1 for row in answers if row["owner_poc_judgment"] is None),
        },
    }
    (HERE / "owner_blind_application.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"applied: claim {claims}/25, poc {pocs}/18, claim-only null "
          f"{record['distribution']['poc_null_claim_only']}/7")
    print("next: python experiments/design_revision/calibration_v2_2/compute_agreement_v2_2.py "
          "(judge file absent → owner_vs_truth + coverage only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
