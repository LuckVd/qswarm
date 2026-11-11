"""Read-only regression checks for the frozen v2.1 exercise, not calibration evidence.

Never call the generator CLI/main: build_bundle only reads local inputs. The sole
runtime test executes the 18 trusted, generated fixtures in temporary directories;
it does not execute bank snapshots, submissions, or arbitrary repository code.
"""
from __future__ import annotations

import ast
from collections import Counter
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tokenize

import pytest

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "experiments" / "design_revision" / "calibration_v2_1"
V2 = HERE.parent / "calibration_v2"
GENERATOR = HERE / "gen_materials_v2_1.py"
CLAIMS = {"real", "refuted", "inconclusive"}
POCS = {"effective", "ineffective", "not_applicable", "not_runnable"}
BANK_SEEDS = {"0001", "0002", "0003", "0004", "0049", "0050", "0051"}


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def inventory(directory):
    return {str(path.relative_to(directory)): sha256(path.read_bytes())
            for path in sorted(directory.rglob("*")) if path.is_file()}


def jsonl(data):
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def python_tokens(source):
    return list(tokenize.generate_tokens(io.StringIO(source).readline))


def call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return call_name(node.value) + "." + node.attr
    return ""


def operation_calls(tree, family):
    name = "os.system" if family == "INJ" else "open"
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and call_name(node.func) == name]


# Ordinary string/character literals are matched first so comment-looking data
# is not mistaken for a comment. The bank has no C++ raw-string literals.
C_TOKEN = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)''')


@pytest.fixture(scope="module")
def generator():
    spec = importlib.util.spec_from_file_location("calibration_v21_material_tests", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bundle(generator):
    before = inventory(V2)
    built = generator.build_bundle()
    assert inventory(V2) == before
    return built


@pytest.fixture(scope="module")
def data(bundle):
    rows = jsonl(bundle["judge_materials_v2_1.jsonl"])
    truth = json.loads(bundle["truth_map_v2_1.json"])
    by_blind = {row["blind_id"]: row for row in rows}
    return rows, jsonl(bundle["judging_sheet_v2_1.jsonl"]), truth, {
        iid: by_blind[item["blind_id"]] for iid, item in truth["items"].items()
    }


def test_generation_is_deterministic_read_only_and_matches_release(generator, bundle):
    before = inventory(V2)
    release_before = inventory(HERE)
    assert generator.build_bundle() == bundle
    assert inventory(V2) == before
    assert inventory(HERE) == release_before
    for name, content in bundle.items():
        assert (HERE / name).read_bytes() == content, name


def test_frozen_v2_inventory_and_manifest_artifact_hashes(bundle):
    freeze = json.loads((HERE / "v2_frozen_inventory.json").read_text(encoding="utf-8"))
    assert freeze["files_sha256"] == inventory(V2)
    manifest = json.loads(bundle["materials_v2_1_manifest.json"])
    assert manifest["frozen_v2_inventory_sha256"] == sha256(canonical(inventory(V2)).encode())
    artifacts = manifest["artifacts"]
    assert set(artifacts) == set(bundle) - {"materials_v2_1_manifest.json"}
    for name, expected in artifacts.items():
        assert sha256(bundle[name]) == expected
        assert sha256((HERE / name).read_bytes()) == expected
    assert manifest["n_items"] == manifest["claim_questions"] == 25
    assert manifest["poc_questions"] == 18
    assert manifest["claim_only_questions"] == 7
    assert manifest["unique_complete_questions"] == 25
    assert manifest["label_status"] == "draft_requires_owner_confirmation"
    # Historical declared groups are not 19 independently demonstrated roots.
    assert manifest["root_cause_groups"] == 19
    assert any("not 25 independent" in text for text in manifest["limitations"])


def test_all_25_claims_are_explicit_and_complete_bodies_are_unique(data):
    rows, sheet, truth, by_item = data
    expected = {f"E0C-{family}-{number:02d}" for family in ("INJ", "PAT")
                for number in range(1, 10)} | {f"E0C-BANK-SEED-{seed}" for seed in BANK_SEEDS}
    assert len(rows) == len(sheet) == len(truth["items"]) == 25
    assert set(by_item) == expected
    assert len({row["blind_id"] for row in rows}) == 25
    assert len({canonical({key: value for key, value in row.items() if key != "blind_id"})
                for row in rows}) == 25
    for row in rows:
        question = row["question_claim"]
        assert question.startswith("主张：")
        assert "攻击者" in question
        assert "real/refuted/inconclusive" in question
        assert "题目主张的安全缺陷" not in question
        assert len(question) > 120
        assert re.fullmatch(r"J21-[0-9a-f]{12}", row["blind_id"])


def test_18_full_driver_tasks_and_seven_explicit_claim_only_tasks(data):
    rows, _, _, by_item = data
    assert Counter(row["assessment_mode"] for row in rows) == {
        "claim_and_poc": 18, "claim_only": 7,
    }
    for iid, row in by_item.items():
        context = row["driver_context"]
        if "-BANK-" in iid:
            assert row["assessment_mode"] == "claim_only"
            assert row["question_poc"] is context is None
            assert "poc_verdict 必须为 null" in row["question_claim"]
            continue
        assert row["assessment_mode"] == "claim_and_poc"
        assert row["question_poc"] and all(label in row["question_poc"] for label in POCS)
        assert context["kind"] == "python_script"
        assert context["entrypoint"] == "driver.py"
        assert context["argv"] == ["{python}", "-I", "-S", "{case_root}/driver.py"]
        assert context["cwd"] == "{case_root}"
        assert context["inherit_environment"] is False
        assert context["environment"]["CASE_ROOT"] == "{case_root}"
        assert context["environment"]["QSWARM_FEATURE_FLAG"] == "off"
        assert 0 < context["child_timeout_seconds"] < context["timeout_seconds"]
        for field in ("platform", "dependencies", "substitution", "initial_state", "observation"):
            assert context[field]
        assert isinstance(context["target_arguments_in_order"], list)
        assert isinstance(context["file_layout"], dict)
        for name, content in context["file_layout"].items():
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
            assert isinstance(content, str)
        tree = ast.parse(context["driver_source"])
        assert any(isinstance(node, ast.FunctionDef) and node.name == "invoke" for node in tree.body)
        assert any(isinstance(node, ast.Call) and call_name(node.func) == "subprocess.run"
                   for node in ast.walk(tree))


def test_sources_and_drivers_have_no_leaking_comments_or_private_metadata(data):
    rows, _, _, _ = data
    forbidden = ("qsw-canary-", "# sink", "无 shell 拼接", "故意缺失", "runner 墙钟",
                 "POTENTIAL FLAW", "BadSource:", "BadSink", "CVE-", "（截断，全文见快照）")
    for row in rows:
        assert set(row) == {"assessment_mode", "blind_id", "driver_context", "lang",
                            "materials", "question_claim", "question_poc"}
        assert not {"upstream", "driver_attempt", "family"} & row["materials"].keys()
        public = canonical(row)
        assert all(hint not in public for hint in forbidden)
        assert not re.search(r"E0C-|SEED-\d{4}|design_truth|old_blind_id|root_cause_group|owner_", public)
        sources = row["materials"].get("snapshot_files", {})
        for name, source in sources.items():
            if name.endswith(".py"):
                ast.parse(source)
                assert not any(token.type == tokenize.COMMENT for token in python_tokens(source))
            else:
                assert not any(match.group("comment") is not None for match in C_TOKEN.finditer(source))
        if row["driver_context"] is not None:
            for source in (row["materials"]["target_source"], row["driver_context"]["driver_source"]):
                tree = ast.parse(source)
                assert not any(token.type == tokenize.COMMENT for token in python_tokens(source))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert ast.get_docstring(node) is None


def test_python_comment_mask_preserves_string_tokens_and_ast(generator):
    source = ('# explanation\nurl = "https://host/#fragment"  # inline hint\n'
              'literal = "escaped \\\" # not a comment"\n'
              'block = """# literal\n// literal /* data */\n"""\n')
    masked = generator.strip_python_comments(source)
    assert len(masked.splitlines()) == len(source.splitlines())
    assert ast.dump(ast.parse(masked)) == ast.dump(ast.parse(source))
    assert [t.string for t in python_tokens(masked) if t.type == tokenize.STRING] == [
        t.string for t in python_tokens(source) if t.type == tokenize.STRING]
    assert not any(t.type == tokenize.COMMENT for t in python_tokens(masked))


@pytest.mark.xfail(strict=True, reason="Frozen v2.1 rstrip changes trailing whitespace inside multiline strings")
def test_known_frozen_python_mask_multiline_string_whitespace_defect(generator):
    source = 'value = """first   \nsecond\t\n"""  # remove only this comment\n'
    assert ast.dump(ast.parse(generator.strip_python_comments(source))) == ast.dump(ast.parse(source))


def test_c_comment_mask_preserves_quoted_strings_characters_and_lines(generator):
    source = ('const char *url = "https://host/a/*keep*/"; // erase me\n'
              'const char *s = "escaped \\\" // keep"; /* erase\n'
              'also erase */ char slash = \'/\'; char quote = \'\\\'\';\n')
    masked = generator.strip_c_comments(source)
    assert len(masked.splitlines()) == len(source.splitlines())
    assert "erase" not in masked
    assert "https://host/a/*keep*/" in masked
    assert "// keep" in masked
    assert [m.group() for m in C_TOKEN.finditer(source) if m.group("comment") is None] == [
        m.group() for m in C_TOKEN.finditer(masked) if m.group("comment") is None]
    assert not any(m.group("comment") is not None for m in C_TOKEN.finditer(masked))
    with pytest.raises(ValueError, match="unterminated block comment"):
        generator.strip_c_comments("int n; /* never closed")


def test_full_local_imageshow_restoration_has_identical_ast_and_tail_class(data):
    source = data[3]["E0C-BANK-SEED-0002"]["materials"]["snapshot_files"]["ImageShow.py"]
    original = (ROOT / "seeds" / "items" / "SEED-0002" / "snapshot" / "ImageShow.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert ast.dump(tree) == ast.dump(ast.parse(original))
    viewers = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"DisplayViewer", "XVViewer", "IPythonViewer"} <= viewers.keys()
    ipython = viewers["IPythonViewer"]
    assert any(isinstance(node, ast.FunctionDef) and node.name == "show_image" for node in ipython.body)
    assert any(isinstance(node, ast.Try) for node in tree.body if node.lineno > ipython.lineno)
    assert isinstance(tree.body[-1], ast.If)  # The local snapshot's CLI tail is restored too.
    assert len(source.splitlines()) == len(original.splitlines())


def test_current_python_bank_inputs_keep_identical_ast_after_comment_masking(generator, data):
    old_rows = {row["blind_id"]: row for row in generator.load_rows(V2 / "judge_materials_v2.jsonl")}
    checked = 0
    for iid, row in data[3].items():
        if "-BANK-" not in iid:
            continue
        old = old_rows[data[2]["items"][iid]["old_blind_id"]]
        for name, displayed in row["materials"]["snapshot_files"].items():
            if not name.endswith(".py"):
                continue
            original = old["materials"]["snapshot_files"][name]
            if iid == "E0C-BANK-SEED-0002":
                original = (ROOT / "seeds" / "items" / "SEED-0002" / "snapshot" / name).read_text(encoding="utf-8")
            assert ast.dump(ast.parse(displayed)) == ast.dump(ast.parse(original)), (iid, name)
            assert [t.string for t in python_tokens(displayed) if t.type == tokenize.STRING] == [
                t.string for t in python_tokens(original) if t.type == tokenize.STRING], (iid, name)
            checked += 1
    assert checked == 5  # Three Python banks also include two displayed test files.


@pytest.mark.parametrize("family", ["INJ", "PAT"])
def test_05_import_and_06_sleep_retain_real_operations(data, family):
    by_item = data[3]
    fifth = by_item[f"E0C-{family}-05"]
    tree = ast.parse(fifth["materials"]["target_source"])
    dependency = next(node for node in tree.body if isinstance(node, ast.Import)
                      and any(alias.name == "case_dependency" for alias in node.names))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    assert operation_calls(main, family)
    assert dependency.lineno < main.lineno < operation_calls(main, family)[0].lineno
    assert "为空模块" in fifth["question_claim"]
    sixth = by_item[f"E0C-{family}-06"]
    tree = ast.parse(sixth["materials"]["target_source"])
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    sleep = next(node for node in ast.walk(main) if isinstance(node, ast.Call)
                 and call_name(node.func) == "time.sleep")
    assert isinstance(sleep.args[0], ast.Constant) and sleep.args[0].value == 30
    assert operation_calls(main, family)
    assert all(sleep.lineno < node.lineno for node in operation_calls(main, family))
    assert "休眠完成" in sixth["question_claim"]


@pytest.mark.parametrize("family", ["INJ", "PAT"])
def test_01_07_08_09_share_target_but_have_distinct_executable_drivers(data, family):
    rows = [data[3][f"E0C-{family}-{number:02d}"] for number in (1, 7, 8, 9)]
    assert len({row["materials"]["target_source"] for row in rows}) == 1
    assert len({row["driver_context"]["driver_source"] for row in rows}) == 4
    for number, row in zip((1, 7, 8, 9), rows):
        tree = ast.parse(row["driver_context"]["driver_source"])
        calls = [node for statement in tree.body if not isinstance(statement, ast.FunctionDef)
                 for node in ast.walk(statement) if isinstance(node, ast.Call)
                 and call_name(node.func) == "invoke"]
        assert bool(calls) is (number in {1, 9})
        arguments = row["driver_context"]["target_arguments_in_order"]
        assert bool(arguments) is (number in {1, 9})
    assert rows[-1]["driver_context"]["target_arguments_in_order"] == [
        "normal-argument" if family == "INJ" else "allowed.txt"]


def test_owner_fields_null_private_design_labels_valid_and_grouping_is_historical(data):
    rows, sheet, truth, _ = data
    assert {row["blind_id"] for row in rows} == {row["blind_id"] for row in sheet}
    assert [row["blind_id"] for row in rows] == [row["blind_id"] for row in sheet]
    assert len({row["item_id"] for row in sheet}) == 25
    assert {row["item_id"] for row in sheet} == set(truth["items"])
    for row in sheet:
        assert row["blind_id"] == truth["items"][row["item_id"]]["blind_id"]
        assert all(value is None for key, value in row.items() if key.startswith("owner_"))
        assert {"owner_claim_judgment", "owner_poc_judgment", "owner_note"} <= row.keys()
    assert truth["label_status"] == "draft_requires_owner_confirmation"
    groups = {item["root_cause_group"] for item in truth["items"].values()}
    assert len(groups) == truth["n_root_cause_groups"] == 19
    for iid, item in truth["items"].items():
        assert item["design_truth_claim"] in CLAIMS
        assert item["label_provenance"] == "ai_authored_design_draft_not_independent_human_truth"
        assert item["claim_evidence"] and item["poc_evidence"]
        if "-BANK-" in iid:
            assert item["design_truth_poc"] is None
        else:
            number = int(iid.rsplit("-", 1)[1])
            assert item["design_truth_poc"] in POCS
            assert item["design_truth_claim"] == ("refuted" if number in {2, 3} else "real")
            expected = {1: "effective", 3: "not_applicable", 5: "not_runnable"}.get(number, "ineffective")
            assert item["design_truth_poc"] == expected
    # A single fixture lineage spans multiple historical groups, including its
    # fixed variant. This count must never be asserted as independent roots.
    for family in ("INJ", "PAT"):
        assert len({truth["items"][f"E0C-{family}-{number:02d}"]["root_cause_group"]
                    for number in range(1, 10)}) == 6


@pytest.mark.skipif(os.name != "posix" or not Path("/bin/sh").exists()
                    or not any(Path(path).exists() for path in ("/usr/bin/echo", "/bin/echo")),
                    reason="Trusted fixtures require the documented POSIX tools")
def test_all_18_trusted_fixtures_match_design_labels_in_temporary_directories(generator, bundle, data, tmp_path, monkeypatch):
    before = inventory(V2)
    release_before = inventory(HERE)
    temporary_directory = generator.tempfile.TemporaryDirectory

    def isolated_directory(*args, **kwargs):
        return temporary_directory(*args, **kwargs, dir=tmp_path)

    monkeypatch.setattr(generator.tempfile, "TemporaryDirectory", isolated_directory)
    report = generator.verify_runtime(bundle)
    assert inventory(V2) == before
    assert inventory(HERE) == release_before
    assert not list(tmp_path.iterdir())
    assert report["status"] == "pass"
    assert report["n_executed"] == len(report["results"]) == 18
    assert report["problems"] == []
    results = {result["blind_id"]: result for result in report["results"]}
    assert len(results) == 18
    for iid, item in data[2]["items"].items():
        if item["design_truth_poc"] is None:
            assert item["blind_id"] not in results
            continue
        result = results[item["blind_id"]]
        assert result["observed_poc_verdict"] == item["design_truth_poc"]
        assert result["driver_returncode"] == 0 and not result["driver_stderr"]
        number = int(iid.rsplit("-", 1)[1])
        assert bool(result["target_invocations"]) is (number not in {7, 8})
        assert result["effect_observed"] is (number == 1)
        assert result["timed_out"] is (number == 6)
        if iid == "E0C-PAT-01":
            assert result["legal_control_ok"] is True
