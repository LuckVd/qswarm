"""perception.channels 测试（SPEC-7 §1.2–§1.4；M3 卷 A 组 §1.2 判据）。

钉死：通道 B schema 畸形重试 1 次后分片缺席（ChannelBFailure 计数、不抛错）、
l0scan: 任务前缀与工件 producer 归属语义（D26）、通道 C 必选门禁形态（family
映射 + rule_hit 候选）与运行期超时缺席计数（漏斗不停摆）、分片构造确定性
（函数边界不跨片、超长硬切、行隙就近并入）。
"""

from __future__ import annotations

import json

import pytest

from qswarm.perception.channels import (
    L0_CHANNEL_PREFIX,
    Shard,
    build_l0_contract,
    build_model_shards,
    channel_a_candidates,
    channel_b_scan,
    channel_c_scan,
)
from qswarm.perception.indexer import parse_index
from qswarm.prompts import SchemaRejected


PY_SRC = """import os


def run(cmd):
    os.system(cmd)
"""


class _Scripted:
    """通道 B 脚本适配器：按分片序返回响应或注入畸形。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def complete(self, *, model_ref, messages, temperature, timeout_s=None) -> str:
        self.calls += 1
        return self._responses.pop(0) if self._responses else "{}"


def _one_shard(path: str = "a.py", text: str = PY_SRC) -> list[Shard]:
    return build_model_shards([(path, text)], max_file_lines=800)


def test_channel_a_candidates_from_index():
    """索引 × clues → 通道 A 候选（kind 按档、taxonomy_hint=命中家族、缺席零产出）。"""
    entry = parse_index("a.py", "python", PY_SRC.encode())
    cands = channel_a_candidates([entry])
    kinds = {(c.kind, c.clue, tuple(sorted(c.channels))) for c in cands}
    assert ("sink", "system", ("A",)) in kinds
    assert all(c.taxonomy_hint == "injection" for c in cands if c.clue == "system")
    assert all(c.span[0] == c.span[1] for c in cands)      # 调用行单行区间
    # 无 grammar（parse_ok=False）→ 零产出（缺席语义）
    absent = parse_index("x.rb", "ruby", b"system('x')")
    assert channel_a_candidates([absent]) == []


def test_channel_b_scout_identity_and_success():
    """M3 卷 §1.2：l0scan 前缀任务形态 + 正常路径产出 model 候选（D26 侦察蜂身份）。"""
    shard = _one_shard()[0]
    assert shard.shard_id.startswith("a.py:")
    resp = json.dumps(
        {"candidates": [
            {"path": "a.py", "start_line": 5, "end_line": 6, "kind": "model_flag",
             "family_hint": "injection", "reason": "参数流入系统调用"},
        ]}
    )
    adapter = _Scripted([resp, resp])
    out = channel_b_scan(_one_shard() * 2, adapter=adapter,
                         model_ref="cheap:zhipu/glm-4.5-air@cv1", agent_id="A1@cv1")
    assert len(out.candidates) == 2 and out.failures == []
    c = out.candidates[0]
    assert c.channels == frozenset({"B"}) and c.kind == "model_flag"
    assert c.taxonomy_hint == "injection" and len(c.clue) <= 200
    assert L0_CHANNEL_PREFIX == "l0scan:"            # 与 L1 l1: 前缀严格区分


def test_channel_b_contract_assembly():
    """§1.3 派单契约装配（D26）：task_id=`l0scan:<shard>`、role=scout、goal 逐字、
    任务头行随渲染（task_id/goal 进 user 消息头，派单语义可审计）。"""
    from qswarm.contracts import Role

    shard = _one_shard()[0]
    contract = build_l0_contract(shard)
    assert contract.task_id == f"l0scan:{shard.shard_id}"
    assert contract.role == Role.SCOUT
    assert contract.goal == "L0 直扫：结构候选收集"
    assert contract.task_bounds.startswith("只做结构级扫描与候选收集")
    assert contract.output_schema_ref == "l0_candidates"   # 产出=候选工件（自描述）
    assert contract.context_refs == [shard.blocks[0].ref]
    assert contract.hop_budget == 0

    class _Capture(_Scripted):
        def __init__(self, responses):
            super().__init__(responses)
            self.seen_messages = []

        def complete(self, *, model_ref, messages, temperature, timeout_s=None):
            self.seen_messages.append(list(messages))
            return super().complete(model_ref=model_ref, messages=messages,
                                    temperature=temperature, timeout_s=timeout_s)

    adapter = _Capture([json.dumps({"candidates": []})])
    channel_b_scan(_one_shard(), adapter=adapter,
                   model_ref="cheap:x/y@cv1", agent_id="A1@cv1")
    user_head = adapter.seen_messages[0][1].content.splitlines()[:2]
    assert user_head == [
        f"task_id: l0scan:{shard.shard_id}",
        "goal: L0 直扫：结构候选收集",
    ]


def test_channel_b_schema_fail_retry_once_then_absent():
    """M3 卷 §1.2：schema 畸形重试 1 次 → 仍败 = 该分片通道 B 缺席计数（不抛错）。"""
    adapter = _Scripted(["not-json", "also-bad"])
    out = channel_b_scan(_one_shard(), adapter=adapter,
                         model_ref="cheap:x/y@cv1", agent_id="A1@cv1")
    assert out.candidates == [] and len(out.failures) == 1
    f = out.failures[0]
    assert f.agent_id == "A1@cv1" and f.attempts == 2
    assert f.reason.startswith("schema:")
    # 第一败第二成 → 收敛
    adapter2 = _Scripted(["bad", json.dumps({"candidates": []})])
    out2 = channel_b_scan(_one_shard(), adapter=adapter2,
                          model_ref="cheap:x/y@cv1", agent_id="A1@cv1")
    assert out2.failures == [] and out2.candidates == []


def test_channel_b_entry_level_malformation_dropped():
    """条目级畸形（kind 越表/rule_hit 专属/行号非法）逐条丢弃；缺 path 回填分片
    主路径（单片回填语义）；容器成立（结构级畸形才判分片失败）。"""
    resp = json.dumps(
        {"candidates": [
            {"path": "a.py", "start_line": 1, "end_line": 2, "kind": "rule_hit"},   # C 专属 → 丢
            {"path": "a.py", "start_line": 3, "end_line": 0, "kind": "sink"},       # 行非法 → 丢
            {"start_line": 4, "end_line": 5, "kind": "sink"},                        # 缺 path → 回填
            {"path": "a.py", "start_line": 5, "end_line": 6, "kind": "sink",
             "family_hint": None, "reason": "ok"},
        ]}
    )
    adapter = _Scripted([resp])
    out = channel_b_scan(_one_shard(), adapter=adapter,
                         model_ref="cheap:x/y@cv1", agent_id="A1@cv1")
    assert out.failures == []
    by_span = {c.span: c for c in out.candidates}
    assert set(by_span) == {(4, 5), (5, 6)}
    assert all(c.path == "a.py" for c in out.candidates)


def _sg(check_id, path, line, family=None):
    hit = {"check_id": check_id, "path": path, "start": {"line": line},
           "end": {"line": line}, "extra": {}}
    if family:
        hit["extra"]["metadata"] = {"family": family}
    return hit


def test_channel_c_mandatory_gate_family_mapping():
    """M3 卷 §1.2：semgrep 门禁形态——metadata.family 映射 → rule_hit 候选；
    metadata 缺失回落 check_id qsw.<family>.* 前缀；映射不到 = hint None。"""
    out = channel_c_scan(
        ["a.py"],
        rules_runner=lambda paths: json.dumps(
            {"results": [
                _sg("data.cfg.qsw.injection.os-system", "a.py", 5, "injection"),
                _sg("x.qsw.crypto.weak-hash", "b.py", 9),              # 前缀回退
                _sg("unknown.rule.id", "c.py", 2),                      # 无映射
            ]}
        ),
    )
    assert out.skip_reason is None and out.candidates is not None
    assert len(out.candidates) == 3
    by_path = {c.path: c for c in out.candidates}
    assert by_path["a.py"].kind == "rule_hit"
    assert by_path["a.py"].taxonomy_hint == "injection"
    assert by_path["b.py"].taxonomy_hint == "crypto"
    assert by_path["c.py"].taxonomy_hint is None
    assert by_path["a.py"].priority == 2              # C 专属 +1


def test_channel_c_runtime_timeout_absent():
    """M3 卷 §1.2：运行期超时/故障 → rule_channel_skipped 计数缺席，漏斗不停摆。"""
    def boom(paths):
        raise TimeoutError("semgrep timeout")

    out = channel_c_scan(["a.py"], rules_runner=boom)
    assert out.candidates is None and out.skip_reason == "rule_channel_skipped"
    # 输出不可解析同样缺席
    out2 = channel_c_scan(["a.py"], rules_runner=lambda p: "not-json")
    assert out2.candidates is None


def test_build_model_shards_function_boundaries():
    """分片确定性：≤ 上限整文件；超长按函数边界装箱（函数不跨片）；单函数超限硬切。"""
    # 超长无 defs → 按行硬切
    text = "\n".join(f"line{i}" for i in range(1, 21))          # 20 行
    shards = build_model_shards([("big.py", text)], max_file_lines=8)
    assert [s.blocks[0].ref for s in shards] == ["big.py:1-8", "big.py:9-16", "big.py:17-20"]
    # 函数边界：段序装配 + 就近装箱（全片 ≤ 上限、无缝全覆盖、确定性）。
    long_text = "\n".join(f"l{i}" for i in range(1, 31))        # 30 行
    defs = [("f1", 2, 6), ("f2", 7, 11), ("f3", 12, 16), ("f4", 20, 26)]
    shards2 = build_model_shards([("m.py", long_text)], max_file_lines=8, defs_by_path={"m.py": defs})
    refs = [s.blocks[0].ref for s in shards2]
    # 期望序列：行隙1 + f1(2-6) 同片；f2 独片；f3 + 行隙(17-19) 装箱；f4；尾隙。
    assert refs == ["m.py:1-6", "m.py:7-11", "m.py:12-19", "m.py:20-26", "m.py:27-30"]
    parsed = [tuple(r.split(":")[1].split("-")) for r in refs]
    for a, b in parsed:
        assert int(b) - int(a) + 1 <= 8              # 全片 ≤ 上限
    covered = sorted((int(a), int(b)) for a, b in parsed)
    assert covered[0][0] == 1 and covered[-1][1] == 30
    for (_a1, b1), (a2, _b2) in zip(covered, covered[1:]):
        assert a2 == b1 + 1                          # 无缝全覆盖
    # 单函数超限 → 硬切成 ≤ 上限片
    shards3 = build_model_shards(
        [("h.py", "\n".join(f"x{i}" for i in range(1, 26)))],
        max_file_lines=10, defs_by_path={"h.py": [("huge", 1, 25)]},
    )
    assert all(len(s.blocks[0].content.splitlines()) <= 10 for s in shards3)


def test_build_model_shards_deterministic():
    """同输入两跑分片集逐位一致（§5-5 确定性）。"""
    text = "\n".join(f"l{i}" for i in range(1, 51))
    defs = [("f", 3, 10), ("g", 15, 30)]
    r1 = build_model_shards([("a.py", text)], max_file_lines=12, defs_by_path={"a.py": defs})
    r2 = build_model_shards([("a.py", text)], max_file_lines=12, defs_by_path={"a.py": defs})
    assert [(s.shard_id, s.blocks[0].content) for s in r1] == [
        (s.shard_id, s.blocks[0].content) for s in r2
    ]
