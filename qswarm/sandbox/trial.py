"""sandbox.trial：G2 前置离线试跑 CLI（SPEC-9 §5；已知答案比对，零 LLM 零账本写）。

用法（E 组 smoke 2 voted 簇试跑形态；结论如实记档不外推——样本量只够判
「生成面完全不可用」量级）：

    python -m qswarm.sandbox.trial \
      --db out/m4-seeding-v3/prod.db \
      --store out/m4-seeding-v3/store \
      --repo out/m4-smoke/repo \
      --consult-run R-SCAN-CONSULT \
      --report-dir out/m5-trial

- 消费 voted 簇（+ 预裁决单工件；无单时按簇 S1 方向投影回退并如实标注）；
- PoC 生成 = 家族模板（零 LLM；LLM 一体产面见 kernel.run_verify + adapter）；
- 执行 = process 档真沙箱（近似红线，D38-c 开发/CI 档；D43-a：零账本写）；
- 判读 = 三分类 × expected_verdict 比对（match 列）+ R7-② 沙箱墙钟披露。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from qswarm.kernel import KernelLedger
from qswarm.kernel.verify import run_verify


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m qswarm.sandbox.trial",
        description="S2 前置离线试跑（G2）：voted 簇 → 模板 PoC → process 档真执行"
                    " → 与已知答案比对（零账本写）",
    )
    ap.add_argument("--db", required=True, help="生产账本 SQLite 路径")
    ap.add_argument("--store", required=True, help="工件库目录（试跑工件写 <store>/trial/）")
    ap.add_argument("--repo", required=True, help="被审仓库根（只读快照源）")
    ap.add_argument("--consult-run", default=None, help="消费指定会诊 run 的簇（缺省全账本面）")
    ap.add_argument("--report-dir", default=None, help="报告落盘目录（缺省 <store>/trial-report）")
    ap.add_argument("--max-clusters", type=int, default=None, help="单轮试跑簇数上限")
    ap.add_argument("--tier", choices=("process", "container"), default="process",
                    help="执行档（D38；container 需 docker 可用；两者均零账本写——"
                         "container 档生产全链走 kernel.verify.run_verify 编排入口）")
    args = ap.parse_args(argv)

    report_dir = Path(args.report_dir) if args.report_dir else (
        Path(args.store) / "trial-report"
    )
    with KernelLedger(args.db) as ledger:
        report = run_verify(
            ledger,
            repo_root=args.repo,
            run_id=f"TRIAL-{Path(args.db).stem}",
            consult_run_id=args.consult_run,
            store_dir=args.store,
            tier=args.tier,
            report_dir=report_dir,
            max_clusters=args.max_clusters,
        )
    d = report.to_dict()
    print(json.dumps(d, ensure_ascii=False, indent=2, sort_keys=True))
    matched = [c for c in report.detail if c.get("match") is True]
    mismatched = [c for c in report.detail if c.get("match") is False]
    print(
        f"\n=== 试跑判读（如实，不外推）：簇 {d['funnel']['clusters_selected']}"
        f" · 比对命中 {len(matched)} / 落空 {len(mismatched)}"
        f" · 沙箱墙钟 {d['sandbox_disclosure']['sandbox_wall_s']}s（非 LLM）===",
        file=sys.stderr,
    )
    for c in report.detail:
        print(
            f"- {c['cluster_id']}: expected={c['expected']}"
            f"({c['expected_source']}) observed={c['outcome']}"
            f"/{c.get('error_kind') or '-'} poc={c['poc_source']}"
            f" match={c['match']}",
            file=sys.stderr,
        )
    print(f"报告已落盘：{report_dir}/verify_report.{{json,md}}", file=sys.stderr)
    return 0 if report.detail else 1


if __name__ == "__main__":
    raise SystemExit(main())
