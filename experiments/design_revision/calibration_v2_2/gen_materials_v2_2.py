#!/usr/bin/env python3
"""Build the v2.2 revision: neutralize category-bearing identifiers in the four
bank display copies; never touch the frozen v2 / v2.1 directories.

Fixture items are carried over byte-for-byte from v2.1 (same target sources,
drivers, claim/PoC questions); only blind IDs, ordering and this revision's
artifacts are new. Bank display copies receive a fixed, documented token
substitution map that removes vulnerability-family hints (CWE-typed
namespaces/classes, Juliet build guards, "cmdi" URL/message fragments) while
keeping upstream provenance naming. Claims, PoC tasks and design labels are
unchanged, so this is instrument repair before any v2.1 judging spend — not
result-driven item re-drawing.

Only the trusted, locally constructed fixture programs below may be executed
by --verify-runtime. This is not a sandbox for judge submissions or repositories.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tokenize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V21 = HERE.parent / "calibration_v2_1"
VERSION = "e0-cal-v2.2"
CLAIM_ENUM = {"real", "refuted", "inconclusive"}
POC_ENUM = {"effective", "ineffective", "not_applicable", "not_runnable"}

# Longest tokens first: the 0051 class token contains the namespace token.
NEUTRALIZE: tuple[tuple[str, str], ...] = (
    ("CWE23_Relative_Path_Traversal__char_environment_ofstream_81_bad", "v2c7e913"),
    ("CWE23_Relative_Path_Traversal__char_environment_ofstream_81", "v8d1b6a4_81"),
    ("CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81", "v3f9c2e7_81"),
    ("OMITBAD", "OMITGUARD"),
    ("/cmdi-00/", "/"),
    (" executing cmdi - ", " executing task - "),
)
# Post-transform negative gate: none of these may survive in any material.
CATEGORY_TOKENS = ("CWE23_", "cmdi", "OMITBAD", "Relative_Path_Traversal")
# Upstream API naming kept on purpose (provenance-level, verdict-neutral).
EXEMPTED_NAMING = {
    "E0C-BANK-SEED-0004": [
        "org.owasp.benchmark.helpers.Utils.getInsecureOSCommandString：上游 API 命名，"
        "属溯源面不指判定；真值 inconclusive 不依赖该名称。"
    ],
}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def v21_inventory() -> dict[str, str]:
    return {str(p.relative_to(V21)): digest(p.read_bytes())
            for p in sorted(V21.rglob("*")) if p.is_file()}


def apply_neutralize(text: str) -> tuple[str, list[dict]]:
    """Apply the fixed substitution map; report per-token replacement counts."""
    counts: list[dict] = []
    for old, new in NEUTRALIZE:
        count = text.count(old)
        if count:
            text = text.replace(old, new)
            counts.append({"old": old, "new": new, "count": count})
    return text, counts


def question_body(row: dict) -> str:
    return canonical({k: v for k, v in row.items() if k != "blind_id"})


def build_bundle() -> dict[str, bytes]:
    v21_rows = load_rows(V21 / "judge_materials_v2_1.jsonl")
    v21_truth_doc = json.loads((V21 / "truth_map_v2_1.json").read_text(encoding="utf-8"))
    v21_truth = v21_truth_doc["items"]
    reverse = {item["blind_id"]: iid for iid, item in v21_truth.items()}
    public, sheet, truths, changes, provenance, neutralized = [], [], {}, [], {}, {}
    for old in v21_rows:
        iid = reverse[old["blind_id"]]
        blind = "J22-" + digest((VERSION + ":" + iid).encode())[:12]
        row = dict(old)
        row["blind_id"] = blind
        old_body = question_body(old)
        item = v21_truth[iid]
        if iid.startswith("E0C-BANK-"):
            files = {}
            for name, content in old["materials"]["snapshot_files"].items():
                new_content, counts = apply_neutralize(content)
                files[name] = new_content
                if counts:
                    neutralized.setdefault(iid, []).extend(
                        {"file": name, **entry} for entry in counts)
            row["materials"] = {"snapshot_files": files}
            if iid in neutralized:
                change = "v2.2 族别提示中性化（仅展示本标识符，主张/PoC/真值不变）：" + \
                    "；".join(f"`{e['old'][:48]}…`→`{e['new']}`×{e['count']}"
                              for e in neutralized[iid])
            else:
                change = "展示本与 v2.1 逐字节相同，仅换盲 ID 与乱序。"
        else:
            change = "内容与 v2.1 逐字节相同（同目标源码、驱动、题面），仅换盲 ID 与乱序；root_cause_group 不变。"
        new_body = question_body(row)
        new_digest = digest(new_body.encode())
        changes.append({
            "item_id": iid, "old_blind_id": old["blind_id"], "blind_id": blind,
            "old_question_sha256": digest(old_body.encode()),
            "new_question_sha256": new_digest, "change": change,
        })
        if not iid.startswith("E0C-BANK-"):
            # Fixture items must be provably content-identical to v2.1.
            assert new_digest == digest(old_body.encode()), f"fixture drifted: {iid}"
        public.append(row)
        sheet.append({"item_id": iid, "blind_id": blind, "owner_claim_judgment": None,
                      "owner_poc_judgment": None, "owner_note": None})
        truths[iid] = {"blind_id": blind, "old_blind_id": old["blind_id"],
                       "root_cause_group": item["root_cause_group"],
                       "design_truth_claim": item["design_truth_claim"],
                       "design_truth_poc": item["design_truth_poc"],
                       "claim_evidence": item["claim_evidence"],
                       "poc_evidence": item["poc_evidence"],
                       "label_provenance": item["label_provenance"]}
    for seed_iid, entries in neutralized.items():
        provenance[seed_iid] = {
            "carry_over_from": "experiments/design_revision/calibration_v2_1/provenance_v2_1.json",
            "display_neutralization_v2_2": entries,
            "exempted_upstream_naming": EXEMPTED_NAMING.get(seed_iid, []),
        }
    validate_bundle(public, sheet, truths)
    truth_doc = {"version": VERSION, "label_status": "draft_requires_owner_confirmation",
                 "n_items": len(public),
                 "n_root_cause_groups": len({t["root_cause_group"] for t in truths.values()}),
                 "label_provenance": v21_truth_doc["label_provenance"],
                 "items": truths}

    def pretty(value: object) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()

    def jsonl(rows: list[dict]) -> bytes:
        return ("\n".join(canonical(row) for row in rows) + "\n").encode()

    outputs = {
        "judge_materials_v2_2.jsonl": jsonl(public), "judging_sheet_v2_2.jsonl": jsonl(sheet),
        "truth_map_v2_2.json": pretty(truth_doc), "changes_v2_2.json": pretty({"version": VERSION, "items": changes}),
        "provenance_v2_2.json": pretty({"neutralized_items": provenance}), "hint_audit_v2_1.json": pretty(hint_audit_v2_1()),
    }
    outputs["materials_v2_2_manifest.json"] = pretty({
        "version": VERSION, "generator": "gen_materials_v2_2.py", "n_items": 25, "claim_questions": 25,
        "poc_questions": 18, "claim_only_questions": 7, "root_cause_groups": 19,
        "unique_complete_questions": 25, "label_status": "draft_requires_owner_confirmation",
        "base_chain": [
            "e0-cal-v2（calibration_v2，冻结清单见 calibration_v2_1/v2_frozen_inventory.json）",
            "e0-cal-v2.1（calibration_v2_1，冻结清单见本目录 v21_frozen_inventory.json）",
        ],
        "scope_note": "v2.2 仅做 bank 展示本族别标识符中性化与换盲 ID/乱序；主张、PoC 任务、驱动与设计真值逐字不变；"
                      "v2.1 从未用于任何裁判轮（零裁判调用零结果），本修订属仪器修复，不属「未达标重抽题」。",
        "generator_sha256": digest(Path(__file__).read_bytes()),
        "frozen_v21_inventory_sha256": digest(canonical(v21_inventory()).encode()),
        "artifacts": {name: digest(data) for name, data in outputs.items()},
        "checks": ["exact complete-question deduplication ignoring blind_id",
                   "fixture byte-identity with v2.1 enforced by assertion",
                   "Python AST syntax and token comment gate",
                   "category-token negative gate: " + ", ".join(CATEGORY_TOKENS),
                   "private metadata/source identity hint gate",
                   "all owner labels empty", "explicit18drivers7claim_only",
                   "valid claim/poc design enums"],
        "limitations": [
            "25 items are 19 declared groups, not 25 independent vulnerability roots",
            "7 snapshots remain static-only; no fabricated runtime labels",
            "upstream provenance naming is retained on purpose (aiohttp/Pillow/werkzeug/OWASP "
            "benchmark identifiers, getInsecureOSCommandString); category-bearing identifiers "
            "(CWE-typed namespaces/classes, OMITBAD guard, cmdi URL/message fragments) are "
            "neutralized in v2.2 display copies",
            "draft design labels and fixture runs are not independent calibration evidence",
        ],
    })
    return outputs


def hint_audit_v2_1() -> dict:
    """Quantify the residual category hints in frozen v2.1 that v2.2 removes."""
    tokens = sorted({old for old, _ in NEUTRALIZE} | set(CATEGORY_TOKENS))
    items: dict[str, dict] = {}
    for row in load_rows(V21 / "judge_materials_v2_1.jsonl"):
        text = json.dumps(row["materials"], ensure_ascii=False)
        counts = {token: text.count(token) for token in tokens if text.count(token)}
        if counts:
            items[row["blind_id"]] = counts
    return {
        "audited_artifact": "judge_materials_v2_1.jsonl",
        "note": "v2.1 展示本中的族别提示残留量化（v2.2 的中性化依据）；"
                "getInsecureOSCommandString 属上游 API 命名按溯源豁免，不计入族别提示。",
        "tokens": tokens, "items": items,
    }


def validate_bundle(rows: list[dict], sheet: list[dict], truths: dict) -> None:
    if len(rows) != 25 or len(truths) != 25 or len(sheet) != 25:
        raise ValueError("expected exactly 25 cases")
    seen = set()
    for row in rows:
        body = question_body(row)
        if body in seen:
            raise ValueError("duplicate complete question")
        seen.add(body)
        text = canonical(row["materials"])
        for hint in ("qsw-canary-", "# sink", "无 shell 拼接", "故意缺失", "runner 墙钟",
                     "POTENTIAL FLAW", "BadSource:", "BadSink", "CVE-", "（截断，全文见快照）",
                     *CATEGORY_TOKENS):
            if hint in text:
                raise ValueError(f"leaking or truncated materials: {hint}")
        if any(key in row["materials"] for key in ("upstream", "driver_attempt", "family")):
            raise ValueError("private metadata in public materials")
        if row["driver_context"] is not None:
            ast.parse(row["materials"]["target_source"])
            ast.parse(row["driver_context"]["driver_source"])
            if any(tok.type == tokenize.COMMENT for tok in tokenize.generate_tokens(
                    io.StringIO(row["materials"]["target_source"]).readline)):
                raise ValueError("fixture comment survived")
        for name, content in row["materials"].get("snapshot_files", {}).items():
            if name.endswith(".py"):
                ast.parse(content)
    if len({row["blind_id"] for row in rows}) != 25:
        raise ValueError("duplicate blind ID")
    if any(row[k] is not None for row in sheet for k in
           ("owner_claim_judgment", "owner_poc_judgment", "owner_note")):
        raise ValueError("owner labels must be empty")
    for item in truths.values():
        if item["design_truth_claim"] not in CLAIM_ENUM or \
                item["design_truth_poc"] not in POC_ENUM | {None}:
            raise ValueError("invalid design label")


AUDIT_WRAPPER = '''import json, runpy, sys
from pathlib import Path
trace, driver = Path(sys.argv[1]), sys.argv[2]
def hook(event, args):
    if event == "subprocess.Popen":
        with trace.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"event": event, "argv": args[1]}) + "\\n")
sys.addaudithook(hook)
runpy.run_path(driver, run_name="__main__")
'''


def run_fixture(row: dict) -> dict:
    """Execute a freshly built, trusted fixture with no inherited secrets/network use."""
    context = row["driver_context"]
    with tempfile.TemporaryDirectory(prefix="qsw-cal-v22-") as tmp:
        root = Path(tmp) / "case with space"
        root.mkdir()
        (root / "target.py").write_text(row["materials"]["target_source"], encoding="utf-8")
        (root / "driver.py").write_text(context["driver_source"], encoding="utf-8")
        for relative, data in context["file_layout"].items():
            dest = root / relative
            if not dest.resolve().is_relative_to(root):
                raise ValueError("invalid fixture layout")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(data, encoding="utf-8")
        env = {key: value.replace("{case_root}", str(root)) for key, value in context["environment"].items()}
        trace = Path(tmp) / "parent-observer.jsonl"
        p = subprocess.run([sys.executable, "-I", "-S", "-c", AUDIT_WRAPPER, str(trace), str(root / "driver.py")],
                           env=env, cwd=root, capture_output=True, text=True, timeout=context["timeout_seconds"])
        events = load_rows(trace) if trace.exists() else []
        records = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        calls = [record for record in records if "argument" in record]
        target_invocations = sum(str(root / "target.py") in event.get("argv", []) for event in events)
        source = row["materials"]["target_source"]
        is_file_case = "SERVE_ROOT" in context["environment"]
        has_operation = bool(re.search(r"\bopen\(", source)) if is_file_case else ("os.system(" in source or "subprocess.run(" in source)
        token = "OBSERVATION_73ab901c"
        observed = any(record["argument"] == "../secret.txt" and record["stdout"].strip() == token
                       and record["returncode"] == 0 for record in calls) if is_file_case else \
            (root / "observation.txt").exists() and (root / "observation.txt").read_text() == token
        startup_failed = bool(calls) and all("ModuleNotFoundError" in record["stderr"] for record in calls)
        label = "not_applicable" if not has_operation else "not_runnable" if startup_failed else \
            "effective" if target_invocations and observed else "ineffective"
        # Paths in the evidence are normalized for reproducible review, never shell-expanded.
        clean = lambda text: text.replace(str(root), "{case_root}")
        return {"blind_id": row["blind_id"], "observed_poc_verdict": label,
                "driver_returncode": p.returncode, "target_invocations": target_invocations,
                "effect_observed": bool(observed),
                "timed_out": any(r["timed_out"] for r in calls),
                "legal_control_ok": any(r["argument"] == "allowed.txt" and r["stdout"].strip() == "PUBLIC_CONTENT_612a"
                                        and r["returncode"] == 0 for r in calls) if is_file_case else None,
                "records": [{key: clean(value) if isinstance(value, str) else value
                             for key, value in record.items()} for record in records],
                "driver_stderr": clean(p.stderr)}


def verify_runtime(bundle: dict[str, bytes]) -> dict:
    if os.name != "posix" or not Path("/bin/sh").exists() or \
            not any(Path(p).exists() for p in ("/usr/bin/echo", "/bin/echo")):
        raise RuntimeError("fixture verification requires documented POSIX tools")
    rows = [json.loads(line) for line in bundle["judge_materials_v2_2.jsonl"].decode().splitlines()]
    truth = json.loads(bundle["truth_map_v2_2.json"])["items"]
    expected = {t["blind_id"]: t["design_truth_poc"] for t in truth.values()}
    results = [run_fixture(row) for row in rows if row["driver_context"] is not None]
    problems = [r["blind_id"] for r in results if r["observed_poc_verdict"] != expected[r["blind_id"]]
                or r["driver_returncode"] != 0 or r["driver_stderr"]]
    return {"version": VERSION, "status": "pass" if not problems else "fail",
            "n_executed": len(results), "problems": problems,
            "interpretation": "trusted local fixture consistency only; not independent human truth or arbitrary-code sandbox evidence",
            "python_version": sys.version, "results": results}


def publish(path: Path, data: bytes, *, check: bool = False) -> None:
    """Fail closed on different existing bytes; never silently overwrite a release."""
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"existing artifact differs; create a new revision, do not overwrite: {path}")
    elif check:
        raise ValueError(f"missing artifact: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare deterministic artifacts without writing")
    parser.add_argument("--verify-runtime", action="store_true", help="run only the 18 generated trusted fixtures")
    args = parser.parse_args(argv)
    before = v21_inventory()
    freeze_path = HERE / "v21_frozen_inventory.json"
    frozen = {"directory": "../calibration_v2_1", "files_sha256": before,
              "policy": "byte-for-byte frozen; do not regenerate v2.1 or reuse its score as v2.2"}
    publish(freeze_path, (json.dumps(frozen, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
            check=args.check)
    bundle = build_bundle()
    for name, data in bundle.items():
        publish(HERE / name, data, check=args.check)
    if args.verify_runtime:
        report = verify_runtime(bundle)
        # Runtime records contain diagnostic interpreter paths/timings, not release truth.
        out = HERE / "runtime_checks_v2_2.json"
        data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if args.check:
            if report["status"] != "pass":
                raise RuntimeError("runtime verification failed: " + canonical(report["problems"]))
        else:
            publish(out, data)
        if report["status"] != "pass":
            raise RuntimeError("runtime verification failed: " + canonical(report["problems"]))
    if v21_inventory() != before:
        raise RuntimeError("frozen v2.1 changed during generation")
    print("v2.2: bank display copies category-neutralized; fixtures byte-identical to v2.1; "
          "claims/truth unchanged; v2.1 unchanged; labels pending owner blind judgment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
