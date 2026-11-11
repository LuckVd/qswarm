"""Dedicated synthetic tests: never read or modify the frozen calibration outputs."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCORER_PATH = (Path(__file__).resolve().parents[1] / "experiments" / "design_revision"
               / "calibration_v2_2" / "compute_agreement_v2_2.py")
_spec = importlib.util.spec_from_file_location("calibration_v2_2_scoring", SCORER_PATH)
assert _spec is not None and _spec.loader is not None
scorer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scorer)


def exercise(groups=("ROOT-ALPHA", "ROOT-ALPHA", "ROOT-BETA"), *, static_last=True):
    materials, sheet, judge, items = [], [], [], {}
    for index, group in enumerate(groups):
        iid, blind = f"ITEM-{index:03}", f"NEW-{index:03}"
        static = static_last and index == len(groups) - 1
        poc = None if static else "effective"
        materials.append({
            "blind_id": blind, "materials": {"target_source": "print(input())"},
            "question_claim": "Claim: attacker input reaches the output without escaping. Is it true?",
            "question_poc": None if static else "Does this exact driver demonstrate the claim?",
            "driver_context": None if static else {
                "driver_source": "print('input')", "argv": ["input"], "env": {}, "files": {},
            },
        })
        sheet.append({"item_id": iid, "blind_id": blind, "owner_claim_judgment": "real",
                      "owner_poc_judgment": poc, "owner_note": None})
        judge.append({"blind_id": blind, "verdict": "real", "poc_verdict": poc})
        items[iid] = {"blind_id": blind, "root_cause_group": group,
                      "design_truth_claim": "real", "design_truth_poc": poc,
                      "claim_evidence": "source", "poc_evidence": None if static else "driver",
                      "old_blind_id": f"OLD-{index:03}"}
    truth = {"version": "e0-cal-v2.2", "label_status": "draft_requires_owner_confirmation",
             "items": items}
    return materials, sheet, truth, judge


def score(data):
    materials, sheet, truth, judge = data
    return scorer.compute_agreement(sheet, judge, truth, materials=materials)


def test_exact_agreement_is_descriptive_and_draft_gate_never_passes():
    result = score(exercise())
    assert result["scoring_status"] == "complete"
    assert result["status"] == "pending_owner_confirmation"
    assert result["counts"] == {"items": 3, "claim_questions": 3, "poc_questions": 2,
                                "claim_only_items": 1, "root_cause_groups": 2}
    for dimension in ("claim", "poc"):
        for pair in ("owner_vs_truth", "judge_vs_truth", "judge_vs_owner"):
            metric = result[dimension][pair]
            assert metric["accuracy"] == metric["coverage"] == 1
            assert metric["group_all_correct_rate"] == 1
    assert result["calibration_gate"]["passed"] is False
    assert "draft_design_labels" in result["calibration_gate"]["reasons"]
    assert result["calibration_gate"]["automatic_score_threshold"] is None


def test_full_25_synthetic_answers_do_not_trigger_23_of_25_pass():
    data = exercise(tuple(f"ROOT-{index:03}" for index in range(25)), static_last=False)
    result = score(data)
    assert result["claim"]["judge_vs_truth"]["agreed"] == 25
    assert not result["calibration_gate"]["passed"]
    data[2]["label_status"] = "owner_confirmed"
    # Even a caller-supplied status is not independent judge/owner verification.
    assert not score(data)["calibration_gate"]["passed"]


@pytest.mark.parametrize("side", ["materials", "sheet", "judge"])
def test_duplicate_rows_rejected_without_silent_overwrite(side):
    data = exercise()
    rows = data[{"materials": 0, "sheet": 1, "judge": 3}[side]]
    rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValueError, match="duplicate"):
        score(data)


def test_duplicate_truth_blinds_rejected():
    data = exercise()
    data[2]["items"]["ITEM-001"]["blind_id"] = "NEW-000"
    with pytest.raises(ValueError, match="truth: duplicate blind_id"):
        score(data)


def test_duplicate_sheet_blinds_rejected():
    data = exercise()
    data[1][1]["blind_id"] = "NEW-000"
    with pytest.raises(ValueError, match="sheet: duplicate blind_id"):
        score(data)


@pytest.mark.parametrize("side", ["materials", "sheet", "truth"])
def test_missing_mapping_on_each_side_rejected(side):
    data = exercise()
    if side == "truth":
        del data[2]["items"]["ITEM-000"]
    else:
        data[0 if side == "materials" else 1].pop(0)
    with pytest.raises(ValueError, match="missing ID|unknown ID"):
        score(data)


def test_mismatched_sheet_bridge_rejected():
    data = exercise()
    data[1][0]["blind_id"] = "UNKNOWN-BLIND"
    with pytest.raises(ValueError, match="does not match truth"):
        score(data)


def test_unknown_judge_id_rejected():
    data = exercise()
    data[3].append({"blind_id": "UNKNOWN-BLIND", "verdict": "real", "poc_verdict": None})
    with pytest.raises(ValueError, match="judge: unknown blind_id"):
        score(data)


def test_preindexed_judge_mapping_rejected_to_preserve_duplicate_detection():
    data = list(exercise())
    data[3] = {row["blind_id"]: row for row in data[3]}
    with pytest.raises(ValueError, match="not a pre-indexed dict"):
        score(data)


@pytest.mark.parametrize("location,key", [
    ("owner", "owner_claim_judgment"), ("owner", "owner_poc_judgment"),
    ("judge", "verdict"), ("judge", "poc_verdict"),
    ("truth", "design_truth_claim"), ("truth", "design_truth_poc"),
])
@pytest.mark.parametrize("value", ["unknown", "", 1, ["real"]])
def test_invalid_enums_rejected(location, key, value):
    data = exercise()
    target = (data[1][0] if location == "owner" else data[3][0] if location == "judge"
              else data[2]["items"]["ITEM-000"])
    target[key] = value
    with pytest.raises(ValueError, match="enum"):
        score(data)


def test_partial_claim_and_poc_answers_are_independent():
    data = exercise()
    data[1][0]["owner_claim_judgment"] = None
    data[3][1]["verdict"] = None
    data[3][0]["poc_verdict"] = None
    result = score(data)
    assert result["scoring_status"] == "incomplete"
    claim = result["claim"]
    assert claim["owner_vs_truth"]["answered_overlap"] == 2
    assert claim["judge_vs_truth"]["answered_overlap"] == 2
    assert claim["judge_vs_owner"]["answered_overlap"] == 1
    assert claim["judge_vs_owner"]["expected"] == 3
    assert claim["judge_vs_owner"]["coverage"] == pytest.approx(1 / 3)
    assert claim["judge_vs_owner"]["accuracy"] == 1
    poc = result["poc"]
    assert poc["owner_vs_truth"]["answered_overlap"] == 2
    assert poc["judge_vs_truth"]["answered_overlap"] == 1
    assert poc["judge_vs_truth"]["coverage"] == 0.5
    assert result["per_item"][0]["poc"]["status"] == "incomplete"
    assert result["per_item"][0]["poc"]["judge"] is None
    assert len(result["per_item"]) == 3
    assert result["calibration_gate"]["passed"] is False


def test_poc_scores_when_claim_has_no_answers():
    data = exercise()
    for row in data[1]:
        row["owner_claim_judgment"] = None
    for row in data[3]:
        row["verdict"] = None
    result = score(data)
    assert result["claim"]["owner_vs_truth"]["accuracy"] is None
    assert result["claim"]["owner_vs_truth"]["estimate_status"] == "not_estimable"
    assert result["poc"]["judge_vs_owner"]["accuracy"] == 1


def test_empty_judge_and_empty_owner_are_incomplete_not_passed():
    data = exercise()
    data[3].clear()
    for row in data[1]:
        row["owner_claim_judgment"] = None
        row["owner_poc_judgment"] = None
    result = score(data)
    for dimension in ("claim", "poc"):
        metric = result[dimension]["judge_vs_owner"]
        assert metric["answered_overlap"] == metric["agreed"] == 0
        assert metric["accuracy"] is None
        assert metric["coverage"] == metric["group_all_correct_rate"] == 0
        assert metric["group_macro_full_denominator_accuracy"] == 0
        assert metric["group_macro_accuracy"] is None
        assert metric["status"] == "incomplete"
        assert metric["estimate_status"] == "not_estimable"
    assert len(result["per_item"]) == 3
    assert result["scoring_status"] == "incomplete"


def test_empty_corpus_rejected():
    truth = {"version": scorer.VERSION, "label_status": "draft_requires_owner_confirmation", "items": {}}
    with pytest.raises(ValueError, match="empty inputs"):
        scorer.compute_agreement([], [], truth, materials=[])


def test_static_only_null_is_excluded_but_present_task_null_is_incomplete():
    data = exercise()
    result = score(data)
    assert result["poc"]["expected"] == 2
    assert result["per_item"][-1]["poc"]["status"] == "not_asked"
    data[3][0]["poc_verdict"] = None
    result = score(data)
    assert result["poc"]["expected"] == 2
    assert result["poc"]["judge_vs_truth"]["answered_overlap"] == 1
    assert result["poc"]["judge_vs_truth"]["agreed"] == 1
    assert result["poc"]["status"] == "incomplete"


@pytest.mark.parametrize("side", ["owner", "judge"])
def test_static_only_cannot_have_invented_poc_verdict(side):
    data = exercise()
    data[1 if side == "owner" else 3][-1][
        "owner_poc_judgment" if side == "owner" else "poc_verdict"] = "not_runnable"
    with pytest.raises(ValueError, match="claim-only"):
        score(data)


@pytest.mark.parametrize("mutation", ["question_null", "truth_null", "driver_missing", "invent_driver"])
def test_poc_task_consistency(mutation):
    data = exercise()
    if mutation == "question_null":
        data[0][0]["question_poc"] = None
    elif mutation == "truth_null":
        data[2]["items"]["ITEM-000"]["design_truth_poc"] = None
    elif mutation == "driver_missing":
        data[0][0]["driver_context"] = {}
    else:
        data[0][-1]["driver_context"] = "Invented execution for an unavailable snapshot"
    with pytest.raises(ValueError, match="null mismatch|driver_context"):
        score(data)


@pytest.mark.parametrize("field", ["question_claim", "question_poc"])
def test_blank_questions_rejected(field):
    data = exercise()
    data[0][0][field] = "  "
    with pytest.raises(ValueError, match=field):
        score(data)


def test_new_blinds_cannot_reuse_any_old_item_blind():
    data = exercise()
    data[2]["items"]["ITEM-001"]["old_blind_id"] = "NEW-000"
    with pytest.raises(ValueError, match="old blind ID reused"):
        score(data)


@pytest.mark.parametrize("leak", ["item_id", "root_cause_group", "design_truth_claim", "literal_id"])
def test_public_materials_cannot_leak_truth_or_private_ids(leak):
    data = exercise()
    if leak == "literal_id":
        data[0][0]["materials"]["note"] = "Hidden solution: ROOT-ALPHA"
    else:
        data[0][0]["materials"][leak] = "real"
    with pytest.raises(ValueError, match="private field leaked|private identifier leaked"):
        score(data)


def test_group_macro_equal_weights_and_all_correct_rate_retains_all_items():
    data = exercise(("ROOT-ALPHA", "ROOT-ALPHA", "ROOT-ALPHA", "ROOT-BETA"), static_last=False)
    data[1][-1]["owner_claim_judgment"] = "refuted"
    result = score(data)
    metric = result["claim"]["owner_vs_truth"]
    assert metric["accuracy"] == 0.75
    assert metric["group_macro_accuracy"] == 0.5
    assert metric["group_macro_full_denominator_accuracy"] == 0.5
    assert metric["group_all_correct_rate"] == 0.5
    assert metric["expected_groups"] == 2
    assert len(metric["per_group"]["ROOT-ALPHA"]["members"]) == 3
    assert len(result["per_item"]) == 4
    # Incomplete groups are not counted all-correct, even if observed answers agree.
    data[1][0]["owner_claim_judgment"] = None
    metric = score(data)["claim"]["owner_vs_truth"]
    assert metric["group_all_correct_rate"] == 0
    assert metric["group_macro_accuracy"] == 0.5
    assert metric["group_macro_full_denominator_accuracy"] == pytest.approx(1 / 3)


def test_group_without_overlap_remains_in_conservative_denominators():
    data = exercise()
    data[1][-1]["owner_claim_judgment"] = None
    metric = score(data)["claim"]["owner_vs_truth"]
    assert metric["expected_groups"] == 2
    assert metric["answered_groups"] == 1
    assert metric["group_macro_accuracy"] == 1
    assert metric["group_macro_full_denominator_accuracy"] == 0.5
    assert metric["group_all_correct_rate"] == 0.5
    assert metric["per_group"]["ROOT-BETA"]["answered_overlap"] == 0


def test_only_claim_questions_handles_zero_poc_denominator():
    result = score(exercise(("ROOT-ALPHA",)))
    assert result["poc"]["expected"] == 0
    assert result["poc"]["judge_vs_truth"]["accuracy"] is None
    assert result["poc"]["judge_vs_truth"]["group_all_correct_rate"] is None
    assert not result["calibration_gate"]["passed"]


def write_inputs(tmp_path, data, *, include_judge=True):
    materials, sheet, truth, judge = data
    paths = {}
    for key, value in (("materials", materials), ("sheet", sheet), ("truth", truth), ("judge", judge)):
        path = tmp_path / (key + (".json" if key == "truth" else ".jsonl"))
        paths[key] = path
        if key == "judge" and not include_judge:
            continue
        text = json.dumps(value) if key == "truth" else "\n".join(json.dumps(row) for row in value)
        path.write_text(text, encoding="utf-8")
    return paths


def cli_args(paths):
    return [argument for key, path in paths.items() for argument in (f"--{key}", str(path))]


def test_cli_uses_all_explicit_paths_and_missing_judge_means_incomplete(tmp_path, capsys):
    paths = write_inputs(tmp_path, exercise(), include_judge=False)
    assert scorer.main(cli_args(paths)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scoring_status"] == "incomplete"
    assert result["claim"]["judge_vs_owner"]["accuracy"] is None
    assert not result["calibration_gate"]["passed"]


def test_cli_defaults_to_new_directory_only(tmp_path, monkeypatch, capsys):
    data = exercise()
    names = {"materials": "judge_materials_v2_2.jsonl", "sheet": "judging_sheet_v2_2.jsonl",
             "truth": "truth_map_v2_2.json", "judge": "judge_results_v2_2.jsonl"}
    paths = write_inputs(tmp_path, data)
    for key, path in paths.items():
        (tmp_path / names[key]).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(scorer, "HERE", tmp_path)
    assert scorer.main([]) == 0
    assert json.loads(capsys.readouterr().out)["counts"]["items"] == 3


def test_cli_rejects_duplicate_truth_json_keys(tmp_path, capsys):
    paths = write_inputs(tmp_path, exercise())
    item = json.dumps(exercise()[2]["items"]["ITEM-000"])
    paths["truth"].write_text('{"version":"e0-cal-v2.2","label_status":"draft_requires_owner_confirmation",'
                              '"items":{"ITEM-000":' + item + ',"ITEM-000":' + item + '}}', encoding="utf-8")
    assert scorer.main(cli_args(paths)) == 1
    result = json.loads(capsys.readouterr().out)
    assert "duplicate JSON key" in result["problems"][0]
    assert not result["calibration_gate"]["passed"]


@pytest.mark.parametrize("failure", ["invalid_json", "nonobject", "duplicate_key", "missing_materials", "empty_files"])
def test_cli_malformed_or_missing_inputs_fail_closed(tmp_path, capsys, failure):
    paths = write_inputs(tmp_path, exercise())
    if failure == "missing_materials":
        paths["materials"] = tmp_path / "not-created.jsonl"
    elif failure == "empty_files":
        paths["materials"].write_text("", encoding="utf-8")
        paths["sheet"].write_text("", encoding="utf-8")
    else:
        paths["judge"].write_text({"invalid_json": "{oops", "nonobject": "[]",
                                    "duplicate_key": '{"blind_id":"NEW-000","blind_id":"NEW-001"}'}[failure],
                                   encoding="utf-8")
    assert scorer.main(cli_args(paths)) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "invalid_inputs"
    assert result["problems"]
    assert not result["calibration_gate"]["passed"]


# --- v2.2 bundle construction tests: strictly read-only against frozen v2/v2.1 dirs ---

GEN_PATH = (Path(__file__).resolve().parents[1] / "experiments" / "design_revision"
            / "calibration_v2_2" / "gen_materials_v2_2.py")
GEN_DIR = GEN_PATH.parent
V21_DIR = GEN_DIR.parent / "calibration_v2_1"
NEUTRALIZE = (
    ("CWE23_Relative_Path_Traversal__char_environment_ofstream_81_bad", "v2c7e913"),
    ("CWE23_Relative_Path_Traversal__char_environment_ofstream_81", "v8d1b6a4_81"),
    ("CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81", "v3f9c2e7_81"),
    ("OMITBAD", "OMITGUARD"),
    ("/cmdi-00/", "/"),
    (" executing cmdi - ", " executing task - "),
)


def _gen():
    spec = importlib.util.spec_from_file_location("calibration_v2_2_gen", GEN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _blind_to_iid(truth_path):
    items = json.loads(truth_path.read_text(encoding="utf-8"))["items"]
    return {item["blind_id"]: iid for iid, item in items.items()}


def _body(row):
    return json.dumps({k: v for k, v in row.items() if k != "blind_id"},
                      sort_keys=True, ensure_ascii=False)


def test_bundle_builds_and_validates_25_items():
    bundle = _gen().build_bundle()
    rows = [json.loads(line) for line in bundle["judge_materials_v2_2.jsonl"].decode().splitlines()]
    sheet = [json.loads(line) for line in bundle["judging_sheet_v2_2.jsonl"].decode().splitlines()]
    truth = json.loads(bundle["truth_map_v2_2.json"])
    assert len(rows) == len(sheet) == len(truth["items"]) == 25
    assert truth["n_root_cause_groups"] == 19
    assert truth["version"] == scorer.VERSION
    assert all(row[k] is None for row in sheet
               for k in ("owner_claim_judgment", "owner_poc_judgment", "owner_note"))


def test_category_tokens_absent_from_all_materials():
    bundle = _gen().build_bundle()
    text = bundle["judge_materials_v2_2.jsonl"].decode()
    for token in ("CWE23_", "cmdi", "OMITBAD", "Relative_Path_Traversal",
                  "CVE-", "SEED-", "J21-", "qsw-canary"):
        assert token not in text


def test_fixtures_byte_identical_and_bank_neutralization_exact():
    bundle = _gen().build_bundle()
    new_rows = [json.loads(line) for line in bundle["judge_materials_v2_2.jsonl"].decode().splitlines()]
    old_rev = _blind_to_iid(V21_DIR / "truth_map_v2_1.json")
    old_by_iid = {old_rev[row["blind_id"]]: row for row in _rows(V21_DIR / "judge_materials_v2_1.jsonl")}
    new_rev = _blind_to_iid(GEN_DIR / "truth_map_v2_2.json")
    for row in new_rows:
        iid = new_rev[row["blind_id"]]
        old = old_by_iid[iid]
        if iid.startswith("E0C-BANK-"):
            for name, content in row["materials"]["snapshot_files"].items():
                expected = old["materials"]["snapshot_files"][name]
                for token, replacement in NEUTRALIZE:
                    expected = expected.replace(token, replacement)
                assert content == expected, f"bank drift: {iid}:{name}"
        else:
            assert _body(row) == _body(old), f"fixture drift: {iid}"


def test_truth_labels_carry_over_from_v2_1():
    bundle = _gen().build_bundle()
    new_items = json.loads(bundle["truth_map_v2_2.json"])["items"]
    old_items = json.loads((V21_DIR / "truth_map_v2_1.json").read_text(encoding="utf-8"))["items"]
    assert set(new_items) == set(old_items)
    for iid, item in new_items.items():
        old = old_items[iid]
        for key in ("root_cause_group", "design_truth_claim", "design_truth_poc",
                    "claim_evidence", "poc_evidence"):
            assert item[key] == old[key], f"{iid}.{key}"
        assert item["old_blind_id"] == old["blind_id"]


def test_changes_record_exactly_four_neutralized_bank_items():
    bundle = _gen().build_bundle()
    changes = json.loads(bundle["changes_v2_2.json"])["items"]
    changed = [c["item_id"] for c in changes if c["old_question_sha256"] != c["new_question_sha256"]]
    assert sorted(changed) == ["E0C-BANK-SEED-0003", "E0C-BANK-SEED-0004",
                               "E0C-BANK-SEED-0050", "E0C-BANK-SEED-0051"]
    for entry in changes:
        if entry["item_id"] not in changed:
            assert entry["old_question_sha256"] == entry["new_question_sha256"]
