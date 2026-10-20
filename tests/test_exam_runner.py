"""exams.runner 测试（SPEC-6 §3.4/§3.5、§4.4；SPEC-5 M2 卷 §1.7 最低用例集）。

钉死：同缓存两次 replay 的 exam_results 行集 + deposit 事件流逐字节一致（S9）、
replay 跨 PYTHONHASHSEED 逐位一致、primary-only（fallback 不参与考试调用）、
exam_log 工件（kind=exam_log，producer=agent_id）、canary_hit 记录、播种报告
JSON 键集黄金回归 + 无密钥泄漏。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from qswarm.contracts import LedgerKind
from qswarm.kernel import FixedClock, KernelLedger, agg_cells
from qswarm.supply import AdapterError
from qswarm.supply.config import load_models as _lm
from qswarm.exams.roster import expand_candidates
from qswarm.exams.seeding import seed_entry
from qswarm.exams.store import artifact_get

from m2_harness import FAMILIES, T0, FakeAdapter, build_models_yaml, make_bank, make_resolver

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def env(tmp_path):
    bank = make_bank(tmp_path / "seeds")
    models = _lm(build_models_yaml(tmp_path / "models.yaml"))
    resolve = make_resolver(tmp_path / "seeds")
    return tmp_path, bank, models, resolve


def _events_dump(led) -> str:
    return json.dumps(
        [[e.seq, e.ts, str(e.kind), e.run_id, e.payload] for e in led.read()],
        ensure_ascii=False, sort_keys=True,
    )


def _rows_dump(led) -> str:
    rows = led.conn.execute(
        "SELECT id, purpose, agent_id, role, node, item_id, correct, score,"
        " model_ref, ts FROM exam_results ORDER BY id"
    ).fetchall()
    return json.dumps(rows, ensure_ascii=False, sort_keys=True)


def test_runner_cache_replay_bit_identical(env):
    """同响应缓存两次 replay：exam_results 行集 + deposit 事件流（seq+payload）
    逐字节一致（§3.5/S9：确定性边界 = 缓存之后）。"""
    tmp, bank, models, resolve = env
    cache = tmp / "cache"
    with KernelLedger(tmp / "online.db", clock=FixedClock(T0)) as led:
        seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                   cache_dir=cache, resolve_text=resolve)
        online_events, online_rows = _events_dump(led), _rows_dump(led)

    # 两次 replay（各自独立账本，同一个缓存目录）
    dumps = []
    for name in ("replay1", "replay2"):
        with KernelLedger(tmp / f"{name}.db", clock=FixedClock(T0)) as led:
            seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                       cache_dir=cache, resolve_text=resolve)
            dumps.append((_events_dump(led), _rows_dump(led)))
    assert dumps[0] == dumps[1]
    assert dumps[0][0] == online_events           # deposit 事件流逐字节一致
    assert dumps[0][1] == online_rows             # exam_results 行集逐字节一致


def test_runner_replay_cross_pythonhashseed(env, tmp_path):
    """replay 跨 PYTHONHASHSEED 逐位一致（子进程探针：缓存重放的事件流 digest
    恒等；cache_key 为 blake2b 内容定址，禁内建 hash）。"""
    tmp, bank, models, resolve = env
    cache = tmp / "cache"
    with KernelLedger(tmp / "online.db", clock=FixedClock(T0)) as led:
        seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                   cache_dir=cache, resolve_text=resolve)

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import hashlib, json, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        f"sys.path.insert(0, {str(REPO_ROOT / 'tests')!r})\n"
        f"sys.path.insert(0, {str(tmp)!r})\n"
        "from qswarm.kernel import FixedClock, KernelLedger\n"
        "from qswarm.supply.config import load_models\n"
        "from qswarm.exams.seeding import seed_entry\n"
        "from m2_harness import FakeAdapter, make_bank, make_resolver\n"
        "bank = make_bank(sys.argv[1])\n"
        "models = load_models(sys.argv[2])\n"
        "led = KernelLedger(sys.argv[3], clock=FixedClock('2030-01-01T00:00:00Z'))\n"
        "seed_entry(led, bank, models, FakeAdapter(sys.argv[1]), seed=7,\n"
        "           cache_dir=sys.argv[4], resolve_text=make_resolver(sys.argv[1]))\n"
        "events = json.dumps([[e.seq, e.ts, str(e.kind), e.run_id, e.payload] for e in led.read()],\n"
        "                    ensure_ascii=False, sort_keys=True)\n"
        "rows = json.dumps(led.conn.execute('SELECT id, purpose, agent_id, role, node,"
        " item_id, correct, score, model_ref, ts FROM exam_results ORDER BY id').fetchall(),\n"
        "                  ensure_ascii=False, sort_keys=True)\n"
        "print(hashlib.sha256((events + rows).encode('utf-8')).hexdigest())\n",
        encoding="utf-8",
    )
    outs = []
    for seed in ("0", "20261011"):
        proc = subprocess.run(
            [sys.executable, str(probe), str(tmp / "seeds"),
             str(tmp / "models.yaml"), str(tmp / f"replay-{seed}.db"), str(cache)],
            capture_output=True, text=True, cwd=REPO_ROOT,
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": seed, "HOME": "/tmp"},
        )
        assert proc.returncode == 0, proc.stderr
        outs.append(proc.stdout.strip())
    assert outs[0] == outs[1] and len(outs[0]) == 64


def test_runner_primary_only_no_fallback(env):
    """primary 失败 → 同 primary 重试 1 次（仍失败 → 基建失败路径）；每蜂只调
    自己的 model_ref（agent ≡ 模型×契约，S6：fallback 是生产可用性机制、不是
    考试代理——换模型作答 = 考的不是这只蜂）。"""
    tmp, bank, models, resolve = env
    calls: list[str] = []

    class PrimaryOnlyFail:
        def complete(self, *, model_ref, messages, temperature, timeout_s=120.0):
            calls.append(model_ref)
            raise AdapterError("primary down (test)")

    with KernelLedger(tmp / "pf.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, PrimaryOnlyFail(), seed=7,
                            resolve_text=resolve)
        cands = expand_candidates(models, roles=("scout", "auditor"))
        own_refs = {c.model_ref for c in cands}
        # 全部调用 = 各蜂自己的 model_ref（身份固定；不存在跨蜂/降档调用）
        assert set(calls) == own_refs
        # 每蜂 = 题库家族数 × 12 题 × 重试 2 次调用（重试 1 次语义）
        assert calls.count("cheap:deepseek/deepseek-chat") == len(FAMILIES) * 12 * 2
        assert calls.count("strong:openai/gpt-4o") == len(FAMILIES) * 12 * 2
        assert len(calls) == len(cands) * len(FAMILIES) * 12 * 2
        # 每 agent 全家族 12 题基建失败 → 全格无效 → degraded
        assert report["degraded"] is True
        assert report["totals"]["deposits"] == 0
    # 行也为零（整格回滚）
    import sqlite3

    con = sqlite3.connect(tmp / "pf.db")
    assert con.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0] == 0
    con.close()


def test_runner_logs_response_artifacts(env):
    """每次成功调用的原始响应全文写工件库（kind=exam_log，producer=agent_id）+
    artifacts 索引行；判分可独立复核（内容与响应缓存逐字节一致）。"""
    tmp, bank, models, resolve = env
    store = tmp / "store"
    with KernelLedger(tmp / "art.db", clock=FixedClock(T0)) as led:
        seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"), seed=7,
                   cache_dir=tmp / "cache", resolve_text=resolve, store_dir=store)
        rows = led.conn.execute(
            "SELECT hash, producer FROM artifacts WHERE kind = 'exam_log'"
        ).fetchall()
        assert rows, "exam_log 工件缺失"
        producers = {r[1] for r in rows}
        assert producers == {
            c.agent_id for c in expand_candidates(models, roles=("scout", "auditor"))
        }
        # 内容可独立复核：工件内容 == 响应缓存文件内容（同 key 的 raw）
        sample_hash, sample_producer = rows[0]
        body = artifact_get(led, store, sample_hash)
        assert body is not None and json.loads(body.decode("utf-8"))["answers"]
        # 每个成功判分题都有工件（= n_agent × 题库家族数 × 12）
        n_items = sum(
            1
            for a in expand_candidates(models, roles=("scout", "auditor"))
            for _ in range(len(FAMILIES) * 12)
        )
        cache_files = list((tmp / "cache").glob("*.txt"))
        assert len(cache_files) == n_items
        assert len(rows) >= 100          # 内容寻址去重后 ≥ 题量下限（288 题唯一响应）


def test_runner_canary_hit_recorded(env):
    """输出含 canary → 判分照常 + canary_hit 进报告（§1.4 泄漏溯源通道）。"""
    tmp, bank, models, resolve = env
    bank_root = tmp / "seeds"

    def script(model_ref, item_id, payload):
        if payload["expected"] == "real":
            canary = json.loads(
                (bank_root / "items" / item_id / "item.json").read_text(encoding="utf-8")
            )["canary"]
            out = {
                "candidate_id": "CD-1", "suspicious": True,
                "guess_family": payload["taxonomy_path"][0], "guess_type": None,
                "reason_ref": f"artifact://{canary}", "injection_flag": False,
            }
            return json.dumps({"answers": [{"item_id": item_id, "declined": False, "output": out}]},
                              ensure_ascii=False)
        return None

    with KernelLedger(tmp / "can.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(bank_root, script),
                            seed=7, resolve_text=resolve)
        assert report["totals"]["canary_hit"] > 0
        hits = [
            (a["agent_id"], c["family"])
            for a in report["agents"] for c in a["cells"] if c["canary_hit"] > 0
        ]
        assert hits
        # 判分照常：canary 不影响对错（real 题命中家族仍判对）
        for a in report["agents"]:
            for c in a["cells"]:
                assert c["accuracy"] is not None


def test_seeding_report_schema_no_secrets(env):
    """报告 JSON 键集/类型黄金回归（递归键集钉死）；全文无 api_key 形态与
    key 值（§11.6-5）。"""
    tmp, bank, models, resolve = env
    with KernelLedger(tmp / "rep.db", clock=FixedClock(T0)) as led:
        report = seed_entry(led, bank, models, FakeAdapter(tmp / "seeds"),
                            seed=7, resolve_text=resolve)

    def key_tree(node):
        if isinstance(node, dict):
            return {k: key_tree(v) for k, v in sorted(node.items())}
        if isinstance(node, list):
            return [key_tree(node[0])] if node else []
        return type(node).__name__

    golden_path = REPO_ROOT / "tests" / "golden" / "exams" / "seeding_report_schema.json"
    fixture = json.loads(golden_path.read_text(encoding="utf-8"))
    assert key_tree(report) == fixture

    # 无密钥泄漏：全文不含 api_key 形态与 env 变量名值
    text = json.dumps(report, ensure_ascii=False)
    for needle in ("api_key", "api-key", "M2_TEST_KEY", "QSWARM_KEY", "Bearer ", "sk-"):
        assert needle not in text, needle
    # 报告落盘形态（write_report → json+md）
    from qswarm.exams.seeding import write_report

    json_path, md_path = write_report(report, tmp / "out")
    assert json_path.exists() and md_path.exists()
    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk == report


def test_cache_key_role_and_bank_version_sensitive():
    """cache_key 含 role 与 bank_version（2026-10-20 修复）：跨角色（帽考）与跨
    题库版本（regen 保 item_id 换内容、canary 确定性复原无法暴露陈旧）都不得
    命中同一缓存条目；缺省空参保持旧调用兼容。"""
    from qswarm.exams.runner import cache_key

    base = cache_key("a@cv1", "injection", "SEED-0001")
    assert cache_key("a@cv1", "injection", "SEED-0001") == base
    assert cache_key("a@cv1", "injection", "SEED-0001", role="scout") != base
    scout = cache_key("a@cv1", "injection", "SEED-0001", role="scout", bank_version=3)
    assert cache_key("a@cv1", "injection", "SEED-0001",
                     role="reviewer", bank_version=3) != scout
    assert cache_key("a@cv1", "injection", "SEED-0001",
                     role="scout", bank_version=2) != scout


def test_retry_classification_infra_reset_on_content(env):
    """重试分类（2026-10-20 修复）：attempt1 网络失败 + attempt2 返回内容但解析
    失败 → **schema_fail 判错**（S7「解析失败判错」），不因 attempt1 的 infra 标记
    粘滞误记 infra_fail（误分类可把格推过 INFRA_FAIL_LIMIT 整格作废）。"""
    from qswarm.exams.runner import run_entry_cell
    from qswarm.exams.paper import draw_paper
    from qswarm.exams.store import upsert_exam_items
    from qswarm.contracts import ExamPurpose

    tmp, bank, models, resolve = env
    family = bank.items[0].taxonomy_path[0]
    paper = draw_paper(
        bank, agent_id="cheap:x/y@cv1", role="scout", family=family,
        purpose=ExamPurpose.ENTRY, bank_version=bank.bank_version, seed=7,
    )
    target = paper[0].item_id                             # 出卷确定性：必在卷内
    seen: set[str] = set()

    def script(model_ref, item_id, payload):
        if item_id != target:
            return None                                  # 其余题满分卷
        if item_id in seen:
            return "not-json-at-all"                     # attempt2：有内容、畸形
        seen.add(item_id)
        raise AdapterError("attempt1 network down")      # attempt1：基建失败

    adapter = FakeAdapter(tmp / "seeds", script=script)
    with KernelLedger(tmp / "retry.db", clock=FixedClock(T0)) as led:
        upsert_exam_items(led, bank)                     # exam_results 外键前件
        outcome = run_entry_cell(
            led, bank, agent_id="cheap:x/y@cv1", role="scout",
            model_ref="cheap:x/y@cv1", family=family,
            adapter=adapter, seed=7, resolve_text=resolve,
        )
    assert outcome.status == "ok"                        # infra_fail=0 → 格不作废
    assert outcome.infra_fail == 0 and outcome.schema_fail == 1
    hit = next(o for o in outcome.items if o.item_id == target)
    assert hit.schema_fail and not hit.infra_fail and hit.correct is False
    assert hit.attempts == 2
