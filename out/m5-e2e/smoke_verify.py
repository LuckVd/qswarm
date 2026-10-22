"""M5 E 组联调：真沙箱 smoke（container 档生产全链一次）。

前置：D41 verifier 入编考完成（engineering:flash active 或级联帽兼任过线），
      prod.db verify_ready 已过（M4 smoke 先例）。

流程（M5_DELIVERY_REPORT §4 口径；生产形态不加测试钉）：
  1. 消费 R-CONSULT voted 簇（eligible_clusters：全员 voted ∧ 无 verify_results 行）；
  2. run_verify(tier="container", runner=None)：真蜂调用（PoC 一体产，D39）→
     真沙箱 container 档（D38-a 五项红线）→ reconciliation（§4.4）→
     submit() 生产写口（VR 行 + verify_result 事件 + §2.4 映射 + ×0.5 存款）；
  3. 三面判据打印：漏斗 / verify_results 行 / 存款与事件 / 报告工件。

用法：QSWARM_KEY_ZHIPU=... .venv/bin/python out/m5-e2e/smoke_verify.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qswarm.kernel import KernelLedger, WallClock  # noqa: E402
from qswarm.kernel.verify import run_verify  # noqa: E402
from qswarm.supply import UrllibAdapter, load_models  # noqa: E402

PROD_DB = ROOT / "out" / "m4-seeding-v3" / "prod.db"
REPO = ROOT / "out" / "m4-smoke" / "repo"
STORE = ROOT / "out" / "m4-smoke" / "store"
RUN_ID = "M5SMOKE-VERIFY1"


def main() -> int:
    if not os.environ.get("QSWARM_KEY_ZHIPU"):
        print("QSWARM_KEY_ZHIPU 未设置", file=sys.stderr)
        return 2
    with KernelLedger(PROD_DB, clock=WallClock()) as led:
        verifiers = led.conn.execute(
            "SELECT a.agent_id FROM agents a JOIN hats h ON a.agent_id = h.agent_id"
            " WHERE h.role = 'verifier' AND a.status = 'active'"
        ).fetchall()
        print(f"[1] active verifier：{[r[0] for r in verifiers]}")

        report = run_verify(
            led,
            repo_root=REPO,
            run_id=RUN_ID,
            mode="pr",
            seed=0,
            adapter=UrllibAdapter(load_models(ROOT / "configs" / "models.zhipu-flash.yaml")),
            store_dir=STORE,
            tier="container",
            runner=None,
            report_dir=ROOT / "out" / "m5-e2e",
        )

        d = report.to_dict()
        print(f"[2] run_verify 漏斗：{json.dumps(d['funnel'], ensure_ascii=False)}")
        print(f"[3] R7-② 披露：{json.dumps(d['sandbox_disclosure'], ensure_ascii=False)}")

        vr = led.conn.execute(
            "SELECT cluster_id, outcome, error_kind FROM verify_results"
            " WHERE run_id = ?", (RUN_ID,),
        ).fetchall()
        print(f"[4] verify_results：{[tuple(r) for r in vr]}")
        ev = led.conn.execute(
            "SELECT COUNT(*) FROM ledger WHERE kind = 'verify_result'"
            " AND json_extract(payload_json, '$.run_id') = ?", (RUN_ID,),
        ).fetchone()[0]
        dep = led.conn.execute(
            "SELECT COUNT(*) FROM ledger WHERE kind = 'deposit'"
            " AND json_extract(payload_json, '$.origin') = 'verify'"
            " AND json_extract(payload_json, '$.run_id') = ?", (RUN_ID,),
        ).fetchone()[0]
        print(f"[5] verify_result 事件 {ev} 笔 · verify 存款 {dep} 笔")
        cells = led.conn.execute(
            "SELECT agent, role, taxonomy_path, status, pos, neg FROM cells"
            " WHERE role = 'verifier' ORDER BY agent", 
        ).fetchall()
        print(f"[6] verifier 格：{[tuple(r) for r in cells]}")

    print(f"报告已落盘：{ROOT / 'out' / 'm5-e2e'}/verify_report.{{json,md}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
