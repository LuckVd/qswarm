"""M5 E 组联调 D41-(b)：verifier 帽入职考——仅 strong 蜂帽兼任（级联路径）。

D41 拍板序列：(a) engineering:flash 新口径重考 0.775 < 0.8 未过线 → 自动落
(b) verifier 帽兼任挂 strong:flash（D31 帽兼任先例；仍须过 verifier 帽入职考，
帽 QC 失败只告警不降编——seed_entry 对 extra_hats 跳过 apply_qc_decision）。

不走 CLI 的原因：`--roles verifier` 会把 engineering 蜂再考一遍（60 次调用
无新信息——同卷同缓存同答案 0.775 已如实记档）；此处按 seed_entry 既有签名
以 ``roles=()`` 只展开帽兼任候选，与 CLI main 同构（bank 装载/SnapshotStore/
UrllibAdapter/报告落盘逐件对齐）。

用法：QSWARM_KEY_ZHIPU=... .venv/bin/python out/m5-e2e/exam_strong_hat.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qswarm.kernel import KernelLedger, WallClock  # noqa: E402
from qswarm.supply import UrllibAdapter, load_models  # noqa: E402
from qswarm.exams.seeding import load_bank_cli, seed_entry  # noqa: E402
from qswarm.exams.runner import SnapshotStore  # noqa: E402

PROD_DB = ROOT / "out" / "m4-seeding-v3" / "prod.db"
OUT = ROOT / "out" / "m5-e2e" / "exam-strong-hat"
HAT_AGENT = "strong:zhipu/glm-5.3-flash@cv1"


def main() -> int:
    if not os.environ.get("QSWARM_KEY_ZHIPU"):
        print("QSWARM_KEY_ZHIPU 未设置", file=sys.stderr)
        return 2
    store_dir = OUT / "store"
    cache_dir = OUT / "cache"
    models = load_models(ROOT / "configs" / "models.zhipu-flash.yaml")
    with KernelLedger(PROD_DB, clock=WallClock()) as ledger:
        bank = load_bank_cli(str(ROOT / "seeds" / "bank.yaml"), ledger, store_dir)
        resolve = SnapshotStore(ledger, store_dir, ROOT / "seeds").resolver()
        report = seed_entry(
            ledger, bank, models, UrllibAdapter(models),
            seed=0,
            roles=(),
            extra_hats=((HAT_AGENT, "verifier"),),
            cache_dir=cache_dir,
            store_dir=store_dir,
            resolve_text=resolve,
            out_dir=OUT,
        )
    totals = report["totals"]
    print(
        f"SEEDING deposits={totals['deposits']}"
        f" rows={totals['exam_result_rows']}"
        f" candidates={len(report['agents'])}"
        + (" DEGRADED" if report["degraded"] else " OK")
    )
    for a in report["agents"]:
        print(f"{a['agent_id']} role={a['role']} QC={a['qc']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
