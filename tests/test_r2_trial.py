"""R2: offline trial must never enter production or lose explicit trust config."""
from pathlib import Path

import pytest

from qswarm.kernel import KernelLedger
from qswarm.kernel.verify import run_verify
from qswarm.sandbox import FakeRunner
from qswarm.sandbox.trial import main
from test_m5_verify import consulted  # noqa: F401 -- shared production-shaped fixture


@pytest.mark.parametrize("tier", ["process", "container"])
def test_trial_only_is_zero_business_writes(consulted, tier):
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db") as ledger:
        before = list(ledger.conn.iterdump())
        report = run_verify(
            ledger, repo_root=repo, run_id="R2-TRIAL", store_dir=tmp / "store",
            params=params, tier=tier, runner=FakeRunner(), trial_only=True,
        )
        assert report.clusters_selected > 0
        assert report.tier == tier
        assert report.trial_only is True
        assert report.to_dict()["trial_only"] is True
        assert list(ledger.conn.iterdump()) == before


def test_trial_rejects_adapter_before_any_writes(consulted):
    tmp, repo, params = consulted
    with KernelLedger(tmp / "prod.db") as ledger:
        before = list(ledger.conn.iterdump())
        with pytest.raises(ValueError, match="trial_only.*adapter"):
            run_verify(
                ledger, repo_root=repo, run_id="R2-TRIAL", store_dir=tmp / "store",
                params=params, tier="container", adapter=object(), trial_only=True,
            )
        assert list(ledger.conn.iterdump()) == before


def test_trial_cli_passes_explicit_params_and_offline_flag(tmp_path, monkeypatch):
    import qswarm.sandbox.trial as trial

    calls = []
    class Report:
        detail = []
        def to_dict(self):
            return {"funnel": {"clusters_selected": 0},
                    "sandbox_disclosure": {"sandbox_wall_s": 0}}

    def capture(ledger, **kwargs):
        calls.append(kwargs)
        return Report()

    monkeypatch.setattr(trial, "run_verify", capture)
    assert main([
        "--db", str(tmp_path / "trial.db"), "--store", str(tmp_path / "store"),
        "--repo", str(tmp_path / "repo"), "--trusted-repo", str(tmp_path / "repo"),
        "--tier", "container",
    ]) == 1
    assert calls[0]["trial_only"] is True
    assert str(tmp_path / "repo") in calls[0]["params"].sandbox.trusted_roots
    assert "adapter" not in calls[0]
