"""perception.scan 测试（SPEC-7 §1.6；M3 卷 A 组 §1.4 + C 组位级判据）。

钉死：fast 档 priority 降序 Top-K、pr 档 diff ±1 调用邻域、full 档全枚举、
二跑候选集工件逐位一致（同仓库快照同参数）、工件库落位（l0_candidates）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qswarm.contracts import load_params
from qswarm.kernel import FixedClock, KernelLedger
from qswarm.perception import scan_repo
from qswarm.perception.candidates import candidates_to_json

import m3_harness
from m3_harness import fake_rules_hit, fake_rules_ok


@pytest.fixture()
def repo(tmp_path):
    return m3_harness.make_repo(tmp_path / "repo")


def _scan(root, mode, rules_runner=fake_rules_ok, **kw):
    return scan_repo(root, mode=mode, params=load_params(),
                     rules_runner=rules_runner, **kw)


def test_scan_fast_topk(repo):
    """fast 档：归并集按 priority 降序取 Top-3（l1.fast_top_k）；C 命中（priority 2）
    排在单通道结构命中前。"""
    rep = _scan(repo, "fast", rules_runner=fake_rules_hit)
    assert len(rep.candidates) <= load_params().perception.l1.fast_top_k
    top = rep.candidates[0]
    assert top.priority == 2 and "C" in top.channels          # 规则命中居首
    priorities = [c.priority for c in rep.candidates]
    assert priorities == sorted(priorities, reverse=True)
    assert rep.channel_a_count > 0 and rep.channel_c_count == 1


def test_scan_full_enumerates_and_merges(repo):
    """full 档：整仓枚举 + A/C 归并；同位 A+C 异 kind 并存（§1.5 同 kind 族）。"""
    rep = _scan(repo, "full", rules_runner=fake_rules_hit)
    paths = {c.path for c in rep.candidates}
    assert "src/api.py" in paths and "src/util.c" in paths
    kinds = {c.kind for c in rep.candidates}
    assert {"sink", "serialization", "dangerous_call", "rule_hit", "source"} <= kinds
    # 排序确定性：(path, span) 字典序
    keys = [(c.path, c.span) for c in rep.candidates]
    assert keys == sorted(keys)


def test_scan_pr_neighborhood(repo):
    """pr 档：候选 = 变更文件 ± 1 调用邻域（索引跳 1 跳）；缺 changed_paths 拒绝。"""
    with pytest.raises(ValueError, match="changed_paths"):
        _scan(repo, "pr")
    rep = _scan(repo, "pr", changed_paths=["src/api.py"], rules_runner=fake_rules_ok)
    paths = {c.path for c in rep.candidates}
    assert "src/api.py" in paths                    # 变更文件本体在候选面
    assert all(not p.endswith(".md") for p in paths)


def test_scan_channel_b_disabled_without_adapter(repo):
    """无供给面 → 通道 B 缺席（如实、不抛错）；规则关 → 不计缺席。"""
    params = load_params()
    params.perception.rules.enabled = False
    rep = scan_repo(repo, mode="full", params=params, rules_runner=fake_rules_ok)
    assert rep.channel_b_count == 0
    assert rep.rule_channel_skipped is None         # 显式关闭 ≠ 运行期缺席
    assert rep.channel_a_count > 0


def test_scan_replay_bitwise(repo, tmp_path):
    """M3 卷 C 组：同仓库快照同参数两跑（独立账本/工件库）候选集工件逐位一致。"""
    hashes = []
    for name in ("a", "b"):
        with KernelLedger(tmp_path / f"{name}.db", clock=FixedClock(m3_harness.T0)) as led:
            rep = scan_repo(
                repo, mode="full", params=load_params(),
                rules_runner=fake_rules_hit,
                ledger=led, store_dir=tmp_path / f"store-{name}",
            )
            assert rep.artifact_ref
            hashes.append(rep.artifact_ref)
    assert hashes[0] == hashes[1]                   # 内容寻址 hash 即位级一致
    f1 = (tmp_path / "store-a" / hashes[0][:2] / hashes[0]).read_bytes()
    f2 = (tmp_path / "store-b" / hashes[1][:2] / hashes[1]).read_bytes()
    assert f1 == f2


def test_scan_artifact_roundtrip(repo, tmp_path):
    """l0_candidates 工件可反序列化回候选集（S9 读取侧）。"""
    from qswarm.exams.store import ARTIFACT_KINDS

    assert "l0_candidates" in ARTIFACT_KINDS
    with KernelLedger(tmp_path / "l.db", clock=FixedClock(m3_harness.T0)) as led:
        rep = scan_repo(
            repo, mode="full", params=load_params(),
            rules_runner=fake_rules_ok,
            ledger=led, store_dir=tmp_path / "store",
        )
        raw = (Path(tmp_path / "store" / rep.artifact_ref[:2] / rep.artifact_ref)).read_text()
    from qswarm.perception import candidates_from_json

    restored = candidates_from_json(raw)
    assert candidates_to_json(restored) == raw
