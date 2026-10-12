"""simulator ↔ kernel.cluster 接线回归（SPEC-2 §0.4 仿真接通；SPEC-5 §9-7）。

钉住的判据：

- **占位簇号归零**：全仓（qswarm/experiments/scripts/tests 源码）不出现任何
  占位簇号（SIM 前缀）字面/产生点——verdict 归属只消费内核签发簇号；
- **归属走真实内核聚类**：引擎产 finding 经 kernel.dispatch.open_finding 建档、
  kernel.cluster.attribute_findings（cluster_findings 提案 + all-pairwise 扩簇
  复核 + apply_merges 落簇）归属，簇号形如 C-{date}-{seq:06d}（D20），findings
  表 cluster_id 与存款 payload.cluster 同源；
- **归并语义**：同 (family,type) 同槽位证据窗的 finding 共簇（真归并发生）、
  异槽位单例（保守拒并发生）；簇内成员按内核判据两两可并；
- **确定性**：同种子两次 run 簇归属映射与轨迹逐位一致（含跨 PYTHONHASHSEED）；
- **世界侧无账本流程**：run_findings_flow 不接 cluster_of → cluster_id=None
  （未归属），不生成任何占位簇号。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind, RunMode, load_params, load_taxonomy
from qswarm.kernel import FindingFacts, KernelLedger, coarse_mergeable
from qswarm.simulator.engine import (
    WORLD_LANG,
    WORLD_LINE_SLOTS,
    WORLD_WINDOW_LEN,
    finding_facts,
    run,
)
from qswarm.simulator.metrics import SIM_T0
from qswarm.simulator.taskgen import TaskGenerator, WorldTask
from qswarm.simulator.verdict import run_findings_flow
from qswarm.simulator.world import AgentPool

REPO_ROOT = Path(__file__).resolve().parents[1]
CLUSTER_ID_RE = re.compile(r"^C-\d{8}-\d{6}$")
FINDING_ID_RE = re.compile(r"^F-SIM-\d{6}$")

#: 接线回归主场景（比 test_sim_engine 的 18 日基线轻；并/拒两态在 6 日内齐备）。
KW = dict(seed=11, n_days=6, replay_every=3)


@pytest.fixture(scope="module")
def wired_run():
    return run(**KW)


def _mk_task(t: int, family: str = "injection", type_: str = "sqli") -> WorldTask:
    return WorldTask(
        task_id=f"T-{t:06d}", t=t, day=t // 30, family=family, type=type_,
        difficulty=0.4, mode=RunMode.PR, cache_hit=False,
    )


# ── 占位簇号全仓归零（静态 + 永久钉）──────────────────────────────


def test_repo_has_no_placeholder_cluster_id_literals():
    """全仓源码扫描归零：占位簇号（SIM 前缀）产生点/字面值不再存在；
    本测试以拼接构造 needle，自身零命中。"""
    needle = "C-" + "SIM"
    hits: list[str] = []
    for rel in ("qswarm", "experiments", "scripts", "tests"):
        for p in sorted((REPO_ROOT / rel).rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            for lineno, line in enumerate(
                p.read_text(encoding="utf-8").splitlines(), 1
            ):
                if needle in line:
                    hits.append(f"{p.relative_to(REPO_ROOT)}:{lineno}")
    assert hits == [], f"占位簇号字面残留：{hits}"


# ── 归属走内核真实聚类（结构判据）────────────────────────────────


def test_engine_attribution_is_kernel_issued(wired_run):
    """簇号全部为内核签发形态（C-{YYYYMMDD}-{seq}，date=SimClock T0 当日）；
    finding_id 保持世界侧 F-SIM 前缀（D20 明文）。"""
    res = wired_run
    assert res.cluster_of, "接通后 run 必须产出 finding 归属"
    assert all(FINDING_ID_RE.fullmatch(f) for f in res.cluster_of)
    assert all(CLUSTER_ID_RE.fullmatch(c) for c in res.cluster_of.values())
    date_lo = int(SIM_T0.strftime("%Y%m%d"))
    date_hi = int((SIM_T0 + timedelta(days=res.n_days)).strftime("%Y%m%d"))
    for c in res.cluster_of.values():
        d = int(c.split("-")[1])
        assert date_lo <= d <= date_hi, c       # date=建簇仿真日（随日推进）
    seqs = [int(c.rsplit("-", 1)[1]) for c in res.cluster_of.values()]
    assert min(seqs) >= 1 and max(seqs) <= len(res.cluster_of)


def test_engine_findings_rows_and_deposits_share_cluster_ids():
    """账本级同源判据：findings 行 cluster_id = cluster_of = 存款 payload.cluster
    （按 finding 关联逐条核对）；cluster_members 与归属逆映射恰一致；存款 cluster
    不存在任何占位形态。"""
    res = run(**{**KW, "seed": 21, "keep_db": True})
    try:
        led = KernelLedger(res.db_path)
        rows = dict(
            led.conn.execute("SELECT id, cluster_id FROM findings").fetchall()
        )
        members: dict[str, set] = {}
        for cid, fid in led.conn.execute(
            "SELECT cluster_id, finding_id FROM cluster_members"
        ):
            members.setdefault(cid, set()).add(fid)
        deposits = [
            e.payload for e in led.read(kinds=(LedgerKind.DEPOSIT,))
            if e.payload.get("origin", "prod") == "prod"
        ]
        led.close()

        assert rows == res.cluster_of
        inv: dict[str, set] = {}
        for fid, cid in res.cluster_of.items():
            inv.setdefault(cid, set()).add(fid)
        assert members == inv          # 全 finding 建档且恰属一簇（成员表=逆映射）
        checked = 0
        for p in deposits:
            if p.get("finding") is not None:
                assert p["cluster"] == res.cluster_of[p["finding"]]
                checked += 1
        assert checked > 0
        assert all(
            p.get("cluster") is None or CLUSTER_ID_RE.fullmatch(p["cluster"])
            for p in deposits
        )
    finally:
        shutil.rmtree(Path(res.db_path).parent, ignore_errors=True)


def test_engine_clustering_merges_and_refuses():
    """真实归并两面齐备：存在多成员簇（真归并发生）与单例簇（保守拒并发生）；
    且每个簇的成员按内核判据（coarse_mergeable，事实面=表行同源）两两可并。"""
    res = run(**{**KW, "seed": 21, "keep_db": True})
    try:
        led = KernelLedger(res.db_path)
        facts = {
            str(fid): FindingFacts(
                finding_id=str(fid), path=str(path), line_start=int(ls),
                line_end=int(le), cwe=cwe, lang=str(lang), family=str(fam),
                type=ftype,
            )
            for fid, path, ls, le, cwe, lang, fam, ftype in led.conn.execute(
                "SELECT id, path, line_start, line_end, cwe, lang, family, type"
                " FROM findings"
            )
        }
        led.close()
    finally:
        shutil.rmtree(Path(res.db_path).parent, ignore_errors=True)

    inv: dict[str, list] = {}
    for fid, cid in res.cluster_of.items():
        inv.setdefault(cid, []).append(fid)
    sizes = Counter(len(v) for v in inv.values())
    assert sizes[1] > 0, "异槽位/异族 finding 应保守拒并（单例簇）"
    assert max(sizes) >= 2, "同族同槽位 finding 应被真实归并（多成员簇）"
    for cid, fids in inv.items():
        for i in range(len(fids)):
            for j in range(i + 1, len(fids)):
                assert coarse_mergeable(facts[fids[i]], facts[fids[j]]), (
                    f"簇 {cid} 内不可并对 {fids[i]}/{fids[j]}"
                )


def test_world_finding_facts_surface_model():
    """世界证据面模型：path=taxonomy 节点、lang 恒 sim、cwe=None、行窗槽位轮转
    （t mod 槽数 × 窗长）；纯 t 派生（同 t 同事实，与种子无关）。"""
    f0, f1, f10 = (
        finding_facts(_mk_task(0)), finding_facts(_mk_task(1)),
        finding_facts(_mk_task(10)),
    )
    assert f0.finding_id == "F-SIM-000000"
    assert f0.path == "injection/sqli" and f0.lang == WORLD_LANG and f0.cwe is None
    assert (f0.line_start, f0.line_end) == (1, WORLD_WINDOW_LEN)
    slot = (10 % WORLD_LINE_SLOTS) * WORLD_WINDOW_LEN
    assert (f10.line_start, f10.line_end) == (slot + 1, slot + WORLD_WINDOW_LEN)
    assert f0.line_start == f10.line_start            # 同槽位 → 同窗（可并）
    assert f0.line_start != f1.line_start             # 异槽位 → 异窗（拒并）
    assert finding_facts(_mk_task(5)) == finding_facts(_mk_task(5))


# ── 确定性（同种子逐位 + 跨 PYTHONHASHSEED）──────────────────────


def test_same_seed_attribution_bit_identical(wired_run):
    second = run(**KW)
    assert second.cluster_of == wired_run.cluster_of
    assert second.digest == wired_run.digest
    assert second.metrics == wired_run.metrics


def test_attribution_identical_across_pythonhashseed():
    """跨 PYTHONHASHSEED 两子进程：cluster_of 与轨迹 digest 逐位一致
    （对标 tests/test_sim_engine.py 跨进程模式；归属面进同一断言）。"""
    script = (
        "import json;from qswarm.simulator.engine import run;"
        f"r=run(**{json.dumps(KW)});"
        "print(json.dumps({'cluster_of': r.cluster_of, 'digest': r.digest}))"
    )
    outs = []
    for hs in ("0", "12345"):
        proc = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            cwd=REPO_ROOT, env={**os.environ, "PYTHONHASHSEED": hs},
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(json.loads(proc.stdout.strip().splitlines()[-1]))
    assert outs[0] == outs[1]


# ── 世界侧无账本流程：不接归属 → 未归属（None），无占位簇号 ────────


def test_unattributed_world_flow_has_no_cluster_ids():
    seed = 2024
    tasks = TaskGenerator(seed=seed).generate(2)
    pool = AgentPool.generate(
        load_params().roster.model_dump(), load_taxonomy(), seed=seed
    )
    flow = run_findings_flow(tasks, pool, seed=seed)
    assert flow
    assert all(ctx.cluster_id is None for ctx, _ in flow)

    # 显式映射接参（引擎同型的内核簇号）→ 原样进入 ctx（归属由调用方经内核获得）
    mapping = {
        ctx.finding_id: f"C-20300101-{i + 1:06d}"
        for i, (ctx, _) in enumerate(flow)
    }
    flow2 = run_findings_flow(tasks, pool, seed=seed, cluster_of=mapping)
    assert all(ctx.cluster_id == mapping[ctx.finding_id] for ctx, _ in flow2)
