"""R2 command surface: explicit opt-in and hard model-call ceiling."""
import json
from types import SimpleNamespace

import pytest


def test_doctor_does_not_construct_adapter_or_create_db(tmp_path, monkeypatch, capsys):
    from qswarm.cli import main
    import qswarm.supply

    def forbidden(*args, **kwargs):
        raise AssertionError("doctor must not construct a model adapter")
    monkeypatch.setattr(qswarm.supply, "UrllibAdapter", forbidden)
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["model_calls"] == 0
    assert report["qualification_enforced"] is False
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["scan", "consult", "verify"])
def test_online_commands_require_explicit_optin_before_db_access(tmp_path, command):
    from qswarm.cli import main

    db = tmp_path / "must-not-be-created.db"
    options = [command, "--db", str(db), "--repo", str(tmp_path),
               "--store", str(tmp_path / "store"), "--run-id", "R2",
               "--models", str(tmp_path / "missing.yaml"), "--max-calls", "1"]
    if command == "consult":
        options += ["--scan-run-id", "SCAN-SOURCE"]
    with pytest.raises(SystemExit) as exc:
        main(options)
    assert exc.value.code == 2
    assert not db.exists()


def test_call_ceiling_rejects_before_network():
    from qswarm.cli import LimitedAdapter
    from qswarm.supply import AdapterError

    calls = []
    delegate = SimpleNamespace(complete=lambda **kwargs: calls.append(kwargs) or "ok")
    adapter = LimitedAdapter(delegate, max_calls=1)
    assert adapter.complete(model_ref="fixture:model", messages=[], temperature=0) == "ok"
    with pytest.raises(AdapterError, match="call limit"):
        adapter.complete(model_ref="fixture:model", messages=[], temperature=0)
    assert len(calls) == 1
    assert adapter.calls == 1
    assert adapter.exhausted is True


@pytest.mark.parametrize("limit", [0, -1])
def test_call_ceiling_positive(limit):
    from qswarm.cli import LimitedAdapter
    with pytest.raises(ValueError):
        LimitedAdapter(object(), max_calls=limit)


def test_trial_command_forwards_args_without_adapter(monkeypatch):
    from qswarm.cli import main
    import qswarm.sandbox.trial
    calls = []
    monkeypatch.setattr(qswarm.sandbox.trial, "main", lambda args: calls.append(args) or 0)
    assert main(["trial", "--db", "fixture.db", "--tier", "container"]) == 0
    assert calls == [["--db", "fixture.db", "--tier", "container"]]
