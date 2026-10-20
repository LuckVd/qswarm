"""M4 E 组联调 ③：smoke 真仓 fast→pr 全链（run_scan → run_consult）。

前置：out/m4-seeding-rv/seeds.db 两轮补播考完成（五蜂 active + ≥20 考试存款）。

流程（M4_DELIVERY_REPORT §4-③ 口径；生产形态不加测试钉）：
  1. fixture 真仓构建（seeds 快照真文件 + python sink 件 + 干净件）；
  2. init_prod_ledger（D28 bootstrap：agents/hats/考试存款幂等导入）+ verify_ready；
  3. run_scan(mode=fast)：通道 A/C 用生产缺省（tree-sitter / 真 semgrep 子进程），
     通道 B 真 scout 调用（UrllibAdapter × models.zhipu-flash.yaml）；
  4. 专员格点亮：对本 run 实际出现的 triaged guess_family 逐家族写 promotion 事件
     （auditor 格 shadow→active；e2e 同款确定性前提，生产对应 promote_scan 职责）；
  5. run_consult(mode=pr)：route 专员通道 → repo map 切片注入 → 面板 → 预裁决单；
  6. 三面判据打印：事件漏斗 / tasks 面 / findings 面 / 报告工件。

用法：QSWARM_KEY_ZHIPU=... .venv/bin/python out/m4-smoke/smoke_run.py
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qswarm.contracts import load_params  # noqa: E402
from qswarm.kernel import KernelLedger, WallClock  # noqa: E402
from qswarm.kernel.bootstrap import init_prod_ledger, verify_ready  # noqa: E402
from qswarm.kernel.consult import run_consult  # noqa: E402
from qswarm.kernel.loop import run_scan  # noqa: E402
from qswarm.perception.channels import default_rules_runner  # noqa: E402
from qswarm.supply import UrllibAdapter, load_models  # noqa: E402

SMOKE = ROOT / "out" / "m4-smoke"
#: 就绪生产账本（v3 重考所在地：4 active + 35 存款，verify_ready 已过）；
#: init 幂等（对已导入账本零新增）。
PROD_DB = ROOT / "out" / "m4-seeding-v3" / "prod.db"
SCAN_RUN = "M4SMOKE-SCAN2"
CONSULT_RUN = "M4SMOKE-CONSULT2"

# fixture 素材：真 Juliet 风格 C++ 快照（seeds/items 实件）+ python sink + 干净件
SNAPSHOTS = ["SEED-0050", "SEED-0054", "SEED-0065"]
VULN_PY = '''import os
import sys


def render(path: str) -> None:
    # 用户可控路径直接拼入 shell —— 命令注入 sink（qsw.injection.os-system）
    target = sys.argv[1]
    os.system("cat " + target + path)
'''


def build_repo(repo: Path) -> list[str]:
    if repo.exists():
        shutil.rmtree(repo)
    (repo / "src").mkdir(parents=True)
    for seed_id in SNAPSHOTS:
        src = ROOT / "seeds" / "items" / seed_id / "snapshot" / "source.cpp"
        shutil.copy(src, repo / "src" / f"{seed_id.lower()}.cpp")
    (repo / "src" / "render_tool.py").write_text(VULN_PY, encoding="utf-8")
    (repo / "src" / "clean_util.py").write_text(
        'def add(a: int, b: int) -> int:\n    return a + b\n', encoding="utf-8"
    )
    (repo / "README.md").write_text("M4 联调 smoke 真仓（seeds 快照件构成）\n", encoding="utf-8")
    return [f"src/{seed_id.lower()}.cpp" for seed_id in SNAPSHOTS] + [
        "src/render_tool.py", "src/clean_util.py"]


def main() -> int:
    import os

    if not os.environ.get("QSWARM_KEY_ZHIPU"):
        print("QSWARM_KEY_ZHIPU 未设置", file=sys.stderr)
        return 2
    SMOKE.mkdir(parents=True, exist_ok=True)
    repo = SMOKE / "repo"
    files = build_repo(repo)
    print(f"[1] fixture 真仓就绪：{repo}（{len(files)} 文件）")

    models = load_models(ROOT / "configs" / "models.zhipu-flash.yaml")
    params = load_params()
    adapter = UrllibAdapter(models)
    store = SMOKE / "store"
    cache = SMOKE / "cache"
    clock = WallClock()

    # prod.db 即就绪账本（bootstrap 于重考前完成 + 第三轮重考 QC 已把 mid 转
    # active）——不再对其 init：幂等导入会因「旧种子库 mid 仍 provisioning」触发
    # 身份冲突守卫（设计行为：生产账本编制不可被导入静默改写）。
    with KernelLedger(PROD_DB, clock=clock) as led:
        ready = verify_ready(led)
        print(f"[2] verify_ready：{ready}")

        scan = run_scan(
            led, repo_root=repo, mode="fast", run_id=SCAN_RUN,
            params=params, adapter=adapter, store_dir=store, cache_dir=cache,
            rules_runner=default_rules_runner(timeout_s=90),
            clock=clock,
        )
        print(f"[3] run_scan：candidates={scan.candidates_registered}"
              f" shards={len(scan.shards)} triaged_view={scan.scan}")

        fams = [str(r[0]) for r in led.conn.execute(
            "SELECT DISTINCT guess_family FROM candidates"
            " WHERE run_id = ? AND triaged = 1 AND guess_family IS NOT NULL"
            " ORDER BY guess_family", (SCAN_RUN,)).fetchall()]
        lit = {
            str(r[0]) for r in led.conn.execute(
                "SELECT DISTINCT json_extract(payload_json,'$.node') FROM ledger"
                " WHERE kind='promotion' AND json_extract(payload_json,'$.agent') ="
                " 'strong:zhipu/glm-5.3-flash@cv1'"
            ).fetchall()
        }
        todo = [f for f in fams if f not in lit]
        print(f"[4] triaged 家族：{fams}（已点亮：{sorted(lit)}）→ 本次点亮 {todo}")
        for node in todo:
            led.append(
                "promotion",
                {"agent": "strong:zhipu/glm-5.3-flash@cv1", "role": "auditor",
                 "node": node, "exam_id": f"smoke:promo:{node}",
                 "from_status": "shadow", "to_status": "active"},
            )

        consult = run_consult(
            led, repo_root=repo, mode="pr", run_id=CONSULT_RUN,
            scan_run_id=SCAN_RUN, seed=11, adapter=adapter, store_dir=store,
            params=params, clock=clock, cache_dir=cache,
            report_dir=SMOKE / "report",
        )
        funnel = {k: getattr(consult, k) for k in (
            "candidates_total", "l2_dispatched", "l2_clean", "findings_opened",
            "clusters_coarse", "panels_run", "findings_voted", "preverdicts",
            "spent_cents", "wall_clock_exceeded") if hasattr(consult, k)}
        print(f"[5] run_consult 漏斗：{json.dumps(funnel, ensure_ascii=False)}")

        tasks = led.conn.execute(
            "SELECT role, state, COUNT(*) FROM tasks GROUP BY role, state ORDER BY 1, 2"
        ).fetchall()
        findings = led.conn.execute(
            "SELECT state, COUNT(*) FROM findings GROUP BY state ORDER BY 1"
        ).fetchall()
        events = led.conn.execute(
            "SELECT kind, COUNT(*) FROM ledger GROUP BY kind ORDER BY 1"
        ).fetchall()
        print(f"[6] tasks 面：{tasks}")
        print(f"[6] findings 面：{findings}")
        print(f"[6] events 面：{events}")

    report_file = SMOKE / "report" / f"{CONSULT_RUN}.md"
    print(f"[7] 报告工件：{report_file} exists={report_file.exists()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
