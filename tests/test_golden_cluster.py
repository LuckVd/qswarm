"""cluster 黄金用例回归（tests/golden/cluster/；scripts/freeze_golden.py --cluster 冻结）。

夹具自含（findings 种子 + 复合操作 op 前缀），expected 钉「事件流（kind/ts/payload，
剔 seq）+ clusters/findings 投影 + cells 全格表」，重放 = 同一 op 序列在
SteppedClock 内存账本上重执行后逐位比对（golden_loader.check_cluster_case）。

回归价值（每类公式改动必先红在这里，突变灵敏度由 test_golden_mutation_gate.py
的 cluster 突变自证）：

- 聚类判据：行窗重叠阈值（含等号）、相似度阈值 0.9（含等号）、CWE 三态、
  all-pairwise、候选序字典序、assigned 工作流；
- id 签发：C-{date}-{seq:06d} 同前缀库内续号（D20）；
- 归属传播：§6.5 展开序与权重（S1/S2/S3 源权重 × 角色系数）、src_seq（D6）、
  冲突补记、验证行 CONFIRM/CONFLICT 基准（先验 S2 方向）；
- 拆分：零存款、子簇 verdict 清空、findings.cluster_id 随迁；
- 幂等键与守卫负用例（终态再裁/票数不足/源 kind 错配/拆分并集/部分重叠）。

对应该验收：cluster 黄金用例入突变门且冻结成功（--check 逐字节一致）。
"""

from __future__ import annotations

import pytest

import golden_loader as gl

MANIFEST = gl.load_cluster_manifest()
PARAMS = gl.manifest_params(MANIFEST)
CASES = [gl.load_case(p) for p in gl.cluster_case_files()]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_golden_cluster_case(case):
    """cluster 黄金：op 前缀重放 → 事件流 + 三投影 + cells 逐位比对。"""
    diffs = gl.check_cluster_case(case, params=PARAMS)
    assert not diffs, (
        f"{case['case_id']} 黄金失配（{len(diffs)} 处）——kernel.cluster 行为偏离冻结"
        f"基线，若为有意变更请按 {MANIFEST['regen_command']} 重冻：\n" + "\n".join(diffs[:30])
    )


def test_golden_cluster_manifest_provenance():
    """夹具目录与 manifest 计数一一对应；溯源命令/策略字段在位。"""
    assert MANIFEST["counts"]["cluster"] == len(CASES) >= 3
    assert MANIFEST["counts"]["total"] == MANIFEST["counts"]["cluster"]
    assert MANIFEST["regen_command"] == "python3 scripts/freeze_golden.py --cluster"
    assert MANIFEST["verify_command"] == "python3 scripts/freeze_golden.py --cluster --check"
    for case in CASES:
        assert case["kind"] == "cluster"
        assert case["meta"]["checkpoint"] >= 1
        assert case["params"] == MANIFEST["params"]
        assert case["expected"] is not None
        # 前缀性质：case i 的 op 序列 = case i-1 的 op 序列 + 增量段
    for prev, cur in zip(CASES, CASES[1:]):
        po, co = prev["inputs"]["ops"], cur["inputs"]["ops"]
        assert co[: len(po)] == po
        assert len(co) > len(po)


def test_golden_cluster_expectations_nontrivial():
    """夹具非平凡性：终态 case 必须含全部三类事件与存款，cells 非空——防「空期望
    恒绿」的退化夹具。"""
    final = CASES[-1]
    kinds = {e["kind"] for e in final["expected"]["events"]}
    assert {"cluster_merge", "cluster_split", "deposit", "finding",
            "human_verdict", "verify_result"} <= kinds
    assert final["expected"]["cells"], "终态 case 的 cells 全格表为空（归属传播未生效）"
    assert any(c["verdict_tier"] is not None for c in final["expected"]["clusters"].values())
    assert any(c["split"] for c in final["expected"]["clusters"].values())
