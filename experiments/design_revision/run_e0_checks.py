#!/usr/bin/env python3
"""E0 确定性判据 runner（方案 §7.2；P1 工作包 E0；零线上调用）。

五组判据（P1 退出条件「E0 通过」的确定性面）：

- **C1 九类负对照**：两族 × 九类夹具真机执行（process 档），行为必须符合
  期望表——③仅回显/⑦无条件 PASS/⑧目标未调用 不得获可信验证；⑨真实漏洞但
  PoC 无效必须 inconclusive；①+② 成对才 supported（方案 §6.3）；
- **C2 模板判据回归**：模板 v2 对漏洞版判 PASS、修复版/仅回显版判 FAIL
  （R1 仪器缺陷的回归钉：旧 echo-marker 面会被仅回显击穿）；
- **C3 指标分母**：全 unknown/零分母 → not_estimable；有标签 precision 界
  公式手算对拍（§4.4 逐字）；
- **C4 投影确定性与纠错**：同 as_of 同事件 → 同投影哈希；同簇二次判定
  （supersedes）后独立根因数不变、有效视图单条；晚于 as_of 的真值不进
  历史投影（时间可见性）；
- **C5 漏斗装配**：合成报告 → 七层齐备带原因分解；金标准回查对无 finding
  项如实 not_assessable（L0/L1 逐条存量不可得不伪造）；
- **C6 container 档模板判据复验**（``--tier container`` 才跑）：C2 判据面 +
  injection 夹具目标①–⑤在 container 档（五项红线施加下）行为一致。C1 观察器
  面不随档复验——宿主判读需观察目录文件可见性，container 跨 ns 属生产面另行
  设计（P1 仅 process，oracle 模块记档）；traversal 夹具走 SERVE_ROOT 驱动
  接口、模板面（cwd=REPO）不接口，⑥超时/⑦⑧⑨为驱动变体类——均不属模板面，
  如实不虚跑。

人工校准面（20–30 例负责人判 + 跨族裁判一致率）不在本 runner：题目已冻结于
manifests/e0_calibration_set.jsonl，供给与调用待批（D47-A）。

用法：``python3 experiments/design_revision/run_e0_checks.py [--out DIR]
[--tier process|container]``（container 档需 docker 桥接环境，DECISIONS
2026-10-23 配方；判据不过 exit 1 如实落盘）
全过 exit 0；任一判据不过 exit 1（报告如实落盘）。报告默认落
``out/design-revision/e0-<UTC 时间戳>/``。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from qswarm.sandbox.config import SandboxConfig          # noqa: E402
from qswarm.sandbox.fixtures import FIXTURES_ROOT, NINE_CLASSES  # noqa: E402
from qswarm.sandbox.oracle import (                      # noqa: E402
    ClaimValidation,
    run_control,
    validate_pair,
)
from qswarm.sandbox.pocgen import parse_assertions, template_poc  # noqa: E402
from qswarm.sandbox.runner import ProcessRunner, make_runner  # noqa: E402
from qswarm.kernel.evidence import (                     # noqa: E402
    capability_summary,
    effective_view,
    independent_root_causes,
    project_evidence,
)
from qswarm.kernel.funnel import (                     # noqa: E402
    STAGES, KnownItem, assemble_trace, first_loss_layer,
)
from qswarm.kernel.ledger import FixedClock, KernelLedger  # noqa: E402
from qswarm.contracts import LedgerKind                  # noqa: E402


def _mk_runner(tmp: Path) -> ProcessRunner:
    """R1-T1：E0 合成仓（kernel-shape 等）经 trusted_roots 显式核验（开发输入
    面——受控合成夹具，非不可信仓库；真实仓库面不经此径）。"""
    cfg = SandboxConfig(
        tier="process", timeout_s=8,
        env_cache_dir=str(tmp / "env"),
        trusted_roots=(str(tmp),),
    )
    return ProcessRunner(cfg)


def _mk_container_runner(tmp: Path):
    """container 档（C6）：五项红线施加下的模板判据复验（D38 配方，timeout
    对齐 pytest container 判据 120s——镜像启动+两级驱动墙钟余量）。"""
    cfg = SandboxConfig(
        tier="container", timeout_s=120,
        env_cache_dir=str(tmp / "env-container"),
    )
    return make_runner(cfg)


# ── C1 九类负对照 ───────────────────────────────────────────────

def check_nine_classes(runner: ProcessRunner, tmp: Path) -> tuple[bool, list[dict]]:
    rows: list[dict] = []
    ok_all = True
    pair_inputs: dict[str, dict] = {}
    for spec in NINE_CLASSES:
        ctrl = run_control(
            runner, FIXTURES_ROOT, family=spec.family, variant=spec.variant,
            driver_kind=spec.driver_kind,
        )
        viol_ok = ctrl.violation_observed == spec.violation
        if spec.klass == 1:
            pair_inputs[spec.family] = {"vuln": ctrl}      # 单独运行不判 supported
        elif spec.klass == 2:
            pair_inputs[spec.family]["fix"] = ctrl
        row = {
            "item": spec.key, "family": spec.family,
            "expected_violation": spec.violation,
            "observed_violation": ctrl.violation_observed,
            "evidence": ctrl.violation_evidence,
            "timed_out": ctrl.execution.timed_out,
            "target_exit_code": ctrl.execution.target_exit_code,
            "pass": viol_ok,
        }
        rows.append(row)
        ok_all &= viol_ok
    # ① + ② 成对判据（supported 只出自成对）
    for family, pair in pair_inputs.items():
        verdict = validate_pair(pair["vuln"], pair["fix"])
        ok = verdict.claim_validation is ClaimValidation.SUPPORTED
        rows.append({
            "item": f"1:{family} paired(validate_pair)", "family": family,
            "expected_violation": None,
            "claim_validation": verdict.claim_validation.value,
            "reason": verdict.reason,
            "pass": ok,
        })
        ok_all &= ok
    # ⑨ 的「绝不 verified_fp/refuted」由 viol=False 期望断言承载（无违规 →
    # supported 不可能；refuted 不出自执行判据——oracle 模块结构性保证）。
    return ok_all, rows


# ── C2 模板判据回归（R1 钉）────────────────────────────────────

def _kernel_shape_repo(tmp: Path) -> Path:
    repo = tmp / "kernel-shape"
    (repo / "src").mkdir(parents=True, exist_ok=True)   # 幂等（C2/C6 同 tmp 复用）
    (repo / "src" / "pub.txt").write_text("pub-content", encoding="utf-8")
    (repo / "src" / "vuln.py").write_text(
        "import sys\n"
        "with open(sys.argv[1], encoding='utf-8') as fh:\n"
        "    print(fh.read())\n",
        encoding="utf-8",
    )
    (repo / "src" / "fix.py").write_text(
        "import os, sys\n"
        "p = os.path.realpath(sys.argv[1])\n"
        "root = os.path.realpath('.')\n"
        "if not (p == root or p.startswith(root + os.sep)):\n"
        "    print('rejected'); raise SystemExit(2)\n"
        "with open(p, encoding='utf-8') as fh:\n"
        "    print(fh.read())\n",
        encoding="utf-8",
    )
    (repo / "src" / "echo.py").write_text(
        "import sys\nprint('requested path:', sys.argv[1])\n",
        encoding="utf-8",
    )
    (repo / "src" / "inj_vuln.py").write_text(
        "import os, sys\nos.system('echo hello ' + (sys.argv[1] if len(sys.argv) > 1 else ''))\n",
        encoding="utf-8",
    )
    (repo / "src" / "inj_echo.py").write_text(
        "import sys\nprint('requested:', sys.argv[1:])\n",
        encoding="utf-8",
    )
    (repo / "src" / "inj_fix.py").write_text(
        "import subprocess, sys\nsubprocess.run(['echo', 'hello'] + (sys.argv[1:] or ['']), check=False)\n",
        encoding="utf-8",
    )
    return repo


def check_template_regression(runner: ProcessRunner, tmp: Path) -> tuple[bool, list[dict]]:
    repo = _kernel_shape_repo(tmp)
    cases = [
        ("injection", "src/inj_vuln.py", "injection_payload_executed", True),
        ("injection", "src/inj_fix.py", "injection_payload_executed", False),
        ("injection", "src/inj_echo.py", "injection_payload_executed", False),   # R1
        ("authz", "src/vuln.py", "path_traversal_read", True),
        ("authz", "src/fix.py", "path_traversal_read", False),
        ("authz", "src/echo.py", "path_traversal_read", False),                 # R1
    ]
    rows, ok_all = [], True
    for fam, path, assert_name, expect in cases:
        poc = template_poc(fam, None, path=path, line_start=1, lang="python", seed="e0c2")
        res = runner.execute(poc, repo=repo, deps=[], seed=f"e0c2-{path}")
        got = dict(
            (n, ok) for n, ok, _d in parse_assertions(res.stdout)
        ).get(assert_name)
        ok = got is expect
        rows.append({
            "case": f"{fam}:{path}", "assertion": assert_name,
            "expected": expect, "observed": got, "pass": ok,
        })
        ok_all &= ok
    return ok_all, rows


# ── C6 container 档模板判据复验（--tier container）───────────────

def check_container_template(runner, tmp: Path) -> tuple[bool, list[dict]]:
    """container 档（五项红线施加）下 C2 判据面 + injection 夹具①–⑤行为一致。

    夹具根目录经 staging 副本入 container：bind mount 源须 daemon 可见
    （DECISIONS 2026-10-23 桥接面），repo 原件零触碰、只读消费。

    不虚跑面（如实）：traversal 夹具走 SERVE_ROOT 驱动接口、模板面 cwd=REPO
    不接口；⑥超时/⑦⑧⑨为 oracle 驱动变体类；C1 观察器判读需宿主文件可见性
    （container 跨 ns 属生产面另行设计）。docker 不可达 → 行如实 FAIL。"""
    rows, ok_all = [], True
    cases: list[tuple[str, str, str, bool, Path]] = []
    repo = _kernel_shape_repo(tmp)
    fixtures_staged = tmp / "fixtures-staged"
    shutil.copytree(
        FIXTURES_ROOT, fixtures_staged,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for fam, path, assert_name, expect in [
        ("injection", "src/inj_vuln.py", "injection_payload_executed", True),
        ("injection", "src/inj_fix.py", "injection_payload_executed", False),
        ("injection", "src/inj_echo.py", "injection_payload_executed", False),  # R1
        ("authz", "src/vuln.py", "path_traversal_read", True),
        ("authz", "src/fix.py", "path_traversal_read", False),
        ("authz", "src/echo.py", "path_traversal_read", False),                # R1
    ]:
        cases.append((fam, path, assert_name, expect, repo))
    for variant, expect in [
        ("vuln", True), ("fix", False), ("echo_safe", False),
        ("unreachable", False), ("missing_dep", False),
    ]:
        cases.append(("injection", f"injection/{variant}/target.py",
                      "injection_payload_executed", expect, fixtures_staged))
    for fam, path, assert_name, expect, repo_root in cases:
        poc = template_poc(fam, None, path=path, line_start=1, lang="python",
                           seed="e0c6")
        res = runner.execute(poc, repo=repo_root, deps=[], seed=f"e0c6-{path}")
        got = dict(
            (n, ok) for n, ok, _d in parse_assertions(res.stdout)
        ).get(assert_name)
        ok = got is expect
        rows.append({
            "case": f"{fam}:{path}", "tier": res.tier,
            "assertion": assert_name, "expected": expect,
            "observed": got, "pass": ok,
        })
        ok_all &= ok
    return ok_all, rows


# ── C3 指标分母（§4.4 公式对拍）────────────────────────────────

def check_denominators(runner=None, tmp=None) -> tuple[bool, list[dict]]:
    def rec(decision, truth):
        return {"family": "injection", "logical_evidence_id": f"lec:{decision}:{truth}",
                "decision": decision, "truth_label": truth}

    # R1-T5 population 分立：正向集（verified_real）与拒判集（verified_fp）
    # 各自统计——1 错正向 + 9 正确拒判 → precision=0（不是 0.9）。
    cases = {
        "all_unknown": {
            "view": [rec("verified_real", "unknown"), rec("verified_fp", "unknown")],
        },
        "empty": {"view": []},
        "mixed": {
            "view": [rec("verified_real", "real"), rec("verified_real", "refuted"),
                     rec("verified_real", "unknown")],
        },
        "mixed_with_rejections": {
            "view": [rec("verified_real", "refuted")]
            + [rec("verified_fp", "refuted") for _ in range(9)],
        },
    }
    rows, ok_all = [], True
    fam_summary = capability_summary(cases["mixed"]["view"])
    got = fam_summary.get("injection", {})
    ok = (got.get("positive_report_precision") == 0.5
          and got.get("positive_report_unknown_bounds") == [0.3333, 0.6667])
    rows.append({"case": "mixed_formula", "summary": got, "pass": ok})
    ok_all &= ok

    rej = capability_summary(cases["mixed_with_rejections"]["view"])["injection"]
    ok = (rej.get("positive_report_precision") == 0.0          # 0/1（拒判不进分母）
          and rej.get("correct_rejections") == 9
          and rej.get("correct_rejection_rate") == 1.0)
    rows.append({"case": "rejections_not_in_precision", "summary": rej, "pass": ok})
    ok_all &= ok

    empty = capability_summary([])
    ok = empty == {}
    rows.append({"case": "empty_not_estimable", "summary": empty, "pass": ok})
    ok_all &= ok

    unknown_only = capability_summary(cases["all_unknown"]["view"])["injection"]
    ok = (unknown_only["positive_report_precision"] == "not_estimable"
          and unknown_only["positive_report_unknown_bounds"] == [0.0, 1.0])
    rows.append({"case": "all_unknown_bounds_full_range",
                 "summary": unknown_only, "pass": ok})
    ok_all &= ok
    return ok_all, rows


# ── C4 投影确定性与纠错（合成账本，:memory:）───────────────────

def _build_synthetic_ledger() -> KernelLedger:
    led = KernelLedger(":memory:", clock=FixedClock("2030-01-01T00:00:00Z"), synchronous="OFF")
    led.append(LedgerKind.VERIFY_RESULT,
               {"cluster_id": "C-1", "outcome": "verified_real"}, run_id="R1")
    led.append(LedgerKind.VERIFY_RESULT,
               {"cluster_id": "C-2", "outcome": "verify_error",
                "error_kind": "poc_fail"}, run_id="R1")
    return led


def check_projection_determinism(runner=None, tmp=None) -> tuple[bool, list[dict]]:
    rows, ok_all = [], True
    with _build_synthetic_ledger() as led:
        p1 = project_evidence(led)
        p2 = project_evidence(led, as_of_seq=p1.as_of_seq)
        ok = p1.projection_hash == p2.projection_hash and len(p1.records) == 2
        rows.append({"case": "same_as_of_same_hash",
                     "hash": p1.projection_hash, "n": len(p1.records), "pass": ok})
        ok_all &= ok
        n0 = independent_root_causes(p1.records)
        # 纠错：同簇二次判定（supersedes）→ 根因数不变、有效视图单条
        led.append(LedgerKind.VERIFY_RESULT,
                   {"cluster_id": "C-1", "outcome": "verified_fp"}, run_id="R1")
        p3 = project_evidence(led)
        ok = (independent_root_causes(p3.records) == n0
              and len(effective_view(p3)) == 2
              and p3.projection_hash != p1.projection_hash)
        rows.append({"case": "correction_no_root_cause_inflation",
                     "n_records": len(p3.records),
                     "n_effective": len(effective_view(p3)),
                     "pass": ok})
        ok_all &= ok
        # 时间可见性：晚于 as_of 的真值不进历史投影
        led.append(LedgerKind.HUMAN_VERDICT,
                   {"cluster_id": "C-1", "direction": 1, "actor": "owner"},
                   run_id="R1")
        p_hist = project_evidence(led, as_of_seq=p1.as_of_seq)   # 截在真值前
        hist_truths = {r["truth_label"] for r in p_hist.records}
        ok = hist_truths == {"unknown"}
        p_full = project_evidence(led)
        full_truths = {r["truth_label"] for r in p_full.records}
        ok = ok and "real" in full_truths
        rows.append({"case": "future_truth_not_in_history",
                     "history_truths": sorted(hist_truths),
                     "full_truths": sorted(full_truths), "pass": ok})
        ok_all &= ok
        # disputed：同级冲突 → truth 归 disputed（U 面）
        led.append(LedgerKind.HUMAN_VERDICT,
                   {"cluster_id": "C-1", "direction": -1, "actor": "owner2"},
                   run_id="R1")
        p_disp = project_evidence(led)
        ok = p_disp.disputed_clusters == ["C-1"]
        rows.append({"case": "conflicting_verdicts_disputed",
                     "disputed": p_disp.disputed_clusters, "pass": ok})
        ok_all &= ok
    return ok_all, rows


# ── C5 漏斗装配 ─────────────────────────────────────────────────

def check_funnel_assembly(runner=None, tmp=None) -> tuple[bool, list[dict]]:
    scan = {"counts": {"files_total": 100, "files_scanned": 90,
                       "files_skipped_binary": 5},
            "channel_a_absent_langs": ["kotlin"], "artifact_ref": "art://l0"}
    consult = {"funnel": {"candidates_total": 40, "l2_dispatched": 12,
                          "l2_clean": 4, "findings_opened": 8},
               "failures": {"l2_schema_fail": 1, "l2_infra_fail": 0,
                            "l2_route_empty_pool": 0}}
    verify = {"funnel": {"skipped_budget": 2, "skipped_no_verifier": 1,
                         "gate_revoked_midrun": 0}}
    with _build_synthetic_ledger() as led:
        trace = assemble_trace(scan_report=scan, consult_report=consult,
                               verify_report=verify, ledger=led)
    stages = [r["stage"] for r in trace]
    ok = tuple(stages) == STAGES
    l0 = next(r for r in trace if r["stage"] == "L0_uncovered")
    ok = ok and l0["in_count"] == 100 and l0["out_count"] == 90
    l1 = next(r for r in trace if r["stage"] == "L1_discarded")
    # R2: candidates_total is already the triaged pool; its dispatch gap is
    # not evidence that a scout rejected those candidates. Preserve unknown.
    ok = (ok and l1["reasons"]["scout_not_suspicious"] == "unknown"
          and l1["reasons"]["not_dispatched"] == 28)
    rows = [{"case": "seven_stages_ordered", "stages": stages, "pass": ok}]
    ok_all = ok
    with _build_synthetic_ledger() as led:
        replay = first_loss_layer(
            [KnownItem("K-1", "src/missing.py", "injection")], led)
    ok = (replay[0]["first_loss_layer"] == "not_assessable"
          and replay[0]["assessable_layers"]["L2_unreported"] == "lost")
    rows.append({"case": "gold_replay_no_finding_honest",
                 "replay": replay[0], "pass": ok})
    ok_all &= ok
    return ok_all, rows


# ── 主入口 ─────────────────────────────────────────────────────

CHECKS = [
    ("C1_nine_class_controls", check_nine_classes),
    ("C2_template_regression", check_template_regression),
    ("C3_metric_denominators", check_denominators),
    ("C4_projection_determinism", check_projection_determinism),
    ("C5_funnel_assembly", check_funnel_assembly),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E0 确定性判据 runner（零线上调用）")
    ap.add_argument("--out", type=Path, default=None,
                    help="报告目录（缺省 out/design-revision/e0-<ts>/）")
    ap.add_argument("--tier", choices=("process", "container"),
                    default="process",
                    help="process=缺省五组判据；container=加跑 C6 模板判据"
                         " container 档复验（需 docker 桥接环境）")
    args = ap.parse_args(argv)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or (REPO_ROOT / "out" / "design-revision" / f"e0-{ts}")
    tmp = Path(tempfile.mkdtemp(prefix="e0-checks-"))
    runner = _mk_runner(tmp)

    checks = list(CHECKS)
    if args.tier == "container":
        checks.append(("C6_container_template", check_container_template))

    results: dict[str, dict] = {}
    all_ok = True
    for name, fn in checks:
        r = runner
        if name == "C6_container_template":
            r = _mk_container_runner(tmp)
        ok, rows = fn(r, tmp)
        results[name] = {"pass": ok, "rows": rows}
        all_ok &= ok
        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

    report = {
        "experiment": "E0-deterministic",
        "ran_at": ts,
        "tier": args.tier,
        "oracle_version": "oc-v2",
        "all_pass": all_ok,
        "checks": results,
        "notes": [
            "人工校准面（20–30 例）不在本 runner：题目冻结于 manifests/"
            "e0_calibration_set.jsonl，供给与调用待批（D47-A）",
            "C1 九类为 process 档观察器面（宿主判读需观察目录文件可见性，"
            "container 跨 ns 属生产面另行设计，P1 仅 process）",
        ],
    }
    if args.tier == "container":
        report["notes"].append(
            "C6 = container 档模板判据复验（五项红线施加）：C2 判据面 + "
            "injection 夹具①–⑤；traversal 夹具（SERVE_ROOT 驱动接口）与"
            "⑥⑦⑧⑨驱动变体类不属模板面，如实不虚跑"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    lines = [
        "# E0 确定性判据报告", "",
        f"- 时间：{ts} · oracle oc-v2 · 总判定：**{'PASS' if all_ok else 'FAIL'}**", "",
    ]
    for name, r in results.items():
        lines.append(f"## {name} —— {'PASS' if r['pass'] else 'FAIL'}")
        lines.append("")
        lines.append(f"（{len(r['rows'])} 项判据明细见 report.json）")
        lines.append("")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"report -> {out_dir}/report.json")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
