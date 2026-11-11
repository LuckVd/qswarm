"""The E0 gate must enforce the corrected funnel semantics, not the old guess."""
import importlib.util
from pathlib import Path


def test_e0_funnel_checks_remain_valid():
    path = Path(__file__).resolve().parents[1] / "experiments/design_revision/run_e0_checks.py"
    spec = importlib.util.spec_from_file_location("qswarm_r2_e0_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ok, rows = module.check_funnel_assembly()
    assert ok, rows
    assert all(row["pass"] for row in rows)
