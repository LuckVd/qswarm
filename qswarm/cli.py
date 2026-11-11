"""Explicit, bounded command surface; no model calls without opt-in.

This module is an application layer, not a kernel writer. Real model calls
still use supply.UrllibAdapter; all business writes use the existing kernel.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


class LimitedAdapter:
    """A per-command request ceiling, not a claim about billed currency.

    Failed calls consume a slot too. The wrapper never retries on its own;
    kernel retries share the same ceiling. Cost/latency studies must still use
    a frozen manifest and the separate experiment budget ledger.
    """

    def __init__(self, delegate, *, max_calls: int):
        if isinstance(max_calls, bool) or not isinstance(max_calls, int) or max_calls < 1:
            raise ValueError("max_calls must be a positive integer")
        self.delegate = delegate
        self.max_calls = max_calls
        self.calls = 0
        self.exhausted = False

    def complete(self, **kwargs):
        from qswarm.supply import AdapterError

        if self.calls >= self.max_calls:
            self.exhausted = True
            raise AdapterError("qswarm command model call limit reached")
        self.calls += 1
        return self.delegate.complete(**kwargs)


def _probe(command: list[str], *, env=None) -> dict:
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=10, env=env)
        return {"ok": p.returncode == 0, "returncode": p.returncode,
                "detail": (p.stdout.strip() if p.returncode == 0 else p.stderr.strip())[:1000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": type(exc).__name__}


def _doctor(args) -> dict:
    from qswarm.contracts import load_params
    from qswarm.perception.indexer import probe_languages

    params = load_params(args.params) if args.params else load_params()
    report = {
        "schema_version": "doctor-v1", "model_calls": 0,
        "python": sys.version.split()[0],
        "dependencies": {name: importlib.util.find_spec(name) is not None
                         for name in ("pydantic", "yaml")},
        "perception_languages": sorted(probe_languages()),
        "semgrep_available": shutil.which("semgrep") is not None,
        "docker_available": shutil.which("docker") is not None,
        "qualification_enforced": params.qualification.enforce,
        "qualification_roles": list(params.qualification.enforce_roles),
        "cli_qualification_roles": ["scout", "auditor", "reviewer", "verifier"],
        "sandbox_tier": params.sandbox.tier,
        "container_check": "not_requested",
        "note": "doctor does not certify production readiness or model quality",
    }
    if args.check_tools:
        env = {**os.environ, "SEMGREP_SEND_METRICS": "off",
               "SEMGREP_ENABLE_VERSION_CHECK": "0"}
        report["semgrep_check"] = _probe(["semgrep", "--version"], env=env)
        report["container_check"] = _probe(["docker", "info", "--format", "{{.ServerVersion}}"])
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qswarm", description="代码审查内核命令入口（生产与离线试跑分离）")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="离线配置/依赖诊断；不调用模型、不创建账本")
    doctor.add_argument("--params")
    doctor.add_argument("--check-tools", action="store_true", help="运行 Semgrep/Docker 启动探针；可能写工具自身日志")
    sub.add_parser("trial", help="零模型调用/零业务写入；参数同 sandbox.trial")
    for command in ("scan", "consult", "verify"):
        p = sub.add_parser(command, help=f"显式受限生产 {command}；需既有合格编制")
        p.add_argument("--db", required=True, help="既有生产账本；本入口不造假蜂、不自动播种")
        p.add_argument("--repo", required=True)
        p.add_argument("--store", required=True)
        p.add_argument("--run-id", required=True)
        p.add_argument("--models", required=True)
        p.add_argument("--params")
        p.add_argument("--mode", choices=("fast", "pr", "full"), default="pr")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--allow-model-calls", action="store_true")
        p.add_argument("--max-calls", required=True, type=int, help="本次请求次数硬上限（含失败/重试；不是实际货币账单上限）")
        p.add_argument("--report-dir")
        if command == "scan":
            p.add_argument("--changed-path", action="append")
            p.add_argument("--cache-dir")
        elif command == "consult":
            p.add_argument("--scan-run-id", required=True)
            p.add_argument("--cache-dir")
        else:
            p.add_argument("--consult-run-id")
            p.add_argument("--max-clusters", type=int)
    return parser


def _online(args, parser) -> int:
    # Validate opt-in before opening a database, constructing supply, or reading
    # any model/key configuration. This command never bypasses readiness/QC.
    if not args.allow_model_calls:
        parser.error("生产命令需要显式 --allow-model-calls；离线验证请用 trial")
    if args.max_calls < 1:
        parser.error("--max-calls 必须为正整数")
    if not Path(args.db).is_file():
        parser.error("--db 必须是既有账本；先通过正式考试播种/迁移，不自动创建编制")
    if not Path(args.repo).is_dir():
        parser.error("--repo 必须是现有目录")
    from qswarm.contracts import load_params
    from qswarm.kernel import KernelLedger
    from qswarm.supply import UrllibAdapter, load_models

    params = load_params(args.params) if args.params else load_params()
    # The legacy library defaults remain replay-compatible. This new command
    # surface is fail-closed for every model-executing role by construction.
    params.qualification.enforce = True
    params.qualification.enforce_roles = ("scout", "auditor", "reviewer", "verifier")
    adapter = LimitedAdapter(UrllibAdapter(load_models(args.models)), max_calls=args.max_calls)
    common = dict(repo_root=args.repo, run_id=args.run_id, mode=args.mode,
                  store_dir=args.store, params=params, adapter=adapter)
    with KernelLedger(args.db) as ledger:
        if args.command == "scan":
            from qswarm.kernel.loop import run_scan
            report = run_scan(ledger, **common, changed_paths=args.changed_path,
                              cache_dir=args.cache_dir, promote=False)
        elif args.command == "consult":
            from qswarm.kernel.consult import run_consult
            report = run_consult(ledger, **common, scan_run_id=args.scan_run_id,
                                 seed=args.seed, cache_dir=args.cache_dir,
                                 report_dir=args.report_dir)
        else:
            from qswarm.kernel.verify import run_verify
            report = run_verify(ledger, **common, consult_run_id=args.consult_run_id,
                                seed=args.seed, tier="container",
                                max_clusters=args.max_clusters, report_dir=args.report_dir)
    result = {"command": args.command, "report": report.to_dict(),
              "model_calls": adapter.calls, "max_calls": adapter.max_calls,
              "call_limit_hit": adapter.exhausted,
              "qualification_enforced": True,
              "budget_note": "request ceiling only; nominal kernel cost is not actual billed currency"}
    if args.report_dir:
        out = Path(args.report_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / "command_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if adapter.exhausted else 0


def main(argv: list[str] | None = None) -> int:
    values = list(sys.argv[1:] if argv is None else argv)
    if values and values[0] == "trial":
        from qswarm.sandbox.trial import main as trial_main
        return trial_main(values[1:])
    parser = _parser()
    args = parser.parse_args(values)
    try:
        if args.command == "doctor":
            report = _doctor(args)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            checks = [report.get("semgrep_check"), report.get("container_check")]
            return 1 if any(isinstance(c, dict) and not c.get("ok") for c in checks) else 0
        return _online(args, parser)
    except (OSError, ValueError, RuntimeError) as exc:
        # Do not serialize arbitrary supply/config exception values: a local
        # provider might include credentials in its URL or diagnostic text.
        print(f"qswarm {args.command} failed ({type(exc).__name__}); no success claimed", file=sys.stderr)
        return 1
