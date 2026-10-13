"""M3 B 组不变量测试（SPEC-7 §5；M3 卷 B 组判据）。

钉死：写口纪律（perception 零账本表写、真包审计零违规零漂移）、纯度（clues/
candidates/prompts 零 IO 零随机——import 钩子断言）、网络单落点（L0-B 全经
注入 adapter——socket 钩子 + AST 双面）、params perception 节越键拒绝
（判据本体在 test_config.py，此处做词表面交叉断言）。
"""

from __future__ import annotations

import ast
import builtins
import io
from pathlib import Path

import pytest

import qswarm
from qswarm.kernel.write_guard import WRITE_TABLES, audit_write_ports

REPO_ROOT = Path(qswarm.__file__).parent
PERCEPTION = REPO_ROOT / "perception"

#: 纯度模块（§5-3：纯函数零 IO 零随机；channels/scan 是 IO 编排边缘、indexer
#: 的 IndexCache 承载盘上缓存 IO——均不在列；parse_index 纯度由其无 IO 依赖面保证）。
PURE_MODULES = ("clues.py", "candidates.py", "prompts.py")


# ══ 写口纪律（§5-2：perception 只写工件库/缓存，零账本直写）═════════

def _write_stmts(tree: ast.Module) -> list[str]:
    """AST 抽取字符串常量中形如 `<VERB> INTO|FROM|UPDATE <table>` 的写语句头。"""
    verbs = ("INSERT", "UPDATE", "DELETE", "REPLACE")
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip().upper()
            for verb in verbs:
                if text.startswith(verb):
                    out.append(node.value)
                    break
    return out


def test_write_ports_audit_m3():
    """M3 卷 B 组：真包审计零违规零漂移（写口词表 15 表不变）；perception 全包
    零账本表写语句（只经 exams.store 形态写工件库 + 盘上缓存）。"""
    audit = audit_write_ports(REPO_ROOT)
    assert audit.ok and audit.violations == [] and audit.mismatches == []
    for py in sorted(PERCEPTION.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for stmt in _write_stmts(tree):
            head = " ".join(stmt.strip().upper().split()[:3])
            for table in WRITE_TABLES:
                assert table not in head, f"{py.name} 触写口表 {table}: {stmt!r}"


def test_perception_artifacts_only_via_store_shape():
    """perception 对工件库的写入仅经 exams.store.artifact_put（kind 词表内）；
    l0_index 索引工件形态在词表（本次由 scan 落 l0_candidates，l0_index 留给
    索引聚合工件——kind 词表两级都在位）。"""
    from qswarm.exams.store import ARTIFACT_KINDS

    assert {"l0_candidates", "l0_index"} <= ARTIFACT_KINDS
    scan_src = (PERCEPTION / "scan.py").read_text(encoding="utf-8")
    assert "artifact_put" in scan_src
    # channels 不落工件（工件事由 scan 编排层统一写）
    channels_src = (PERCEPTION / "channels.py").read_text(encoding="utf-8")
    assert "artifact_put" not in channels_src


# ══ 纯度（§5-3：纯函数零 IO 零随机；import 钩子断言）══════════════

def test_purity_perception_import_hook(monkeypatch):
    """钩子内 import 纯度模块并调用其纯函数：任何 open/socket/random 触碰即红。"""
    import random as _random

    def _forbidden(*args, **kwargs):  # pragma: no cover — 触碰即被测试捕获
        raise AssertionError(f"纯度违规：{args[:2]}")

    monkeypatch.setattr(builtins, "open", _forbidden)
    monkeypatch.setattr(_random.Random, "random", _forbidden)
    import importlib

    for mod in PURE_MODULES:
        module = importlib.import_module(f"qswarm.perception.{mod[:-3]}")
        importlib.reload(module)
    # 纯函数调用面（reload 后再跑一遍）
    from qswarm.perception.candidates import Candidate, merge_candidates
    from qswarm.perception.clues import match_name
    from qswarm.perception.prompts import L0Block, render_l0_scan

    assert match_name("system") == (("injection", "sink"),)
    merged = merge_candidates(
        [
            Candidate(path="a.py", span=(1, 2), kind="sink", clue="system",
                      channels=frozenset({"A"})),
            Candidate(path="a.py", span=(3, 4), kind="sink", clue="system",
                      channels=frozenset({"B"})),
        ]
    )
    assert len(merged) == 1 and merged[0].priority == 2
    msgs = render_l0_scan([L0Block(ref="a.py:1-2", content="os.system(x)")])
    assert msgs[0].role == "system" and "L0 直扫" in msgs[0].content


def test_purity_static_no_io_imports():
    """静态面：纯度模块零 open/socket/subprocess/os.system/urllib 导入。"""
    banned = {"socket", "subprocess", "urllib", "http", "requests", "shutil", "pathlib"}
    for mod in PURE_MODULES:
        tree = ast.parse((PERCEPTION / mod).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                assert name not in banned, f"{mod} 导入禁用面 {name}"


# ══ 网络单落点（§5-4：L0-B 全经注入 adapter；socket 钩子）══════════

def test_network_single_port(monkeypatch, tmp_path):
    """M3 卷 B 组：socket 钩子下通道 B 全链（分片渲染 → adapter 注入 → 解析）
    零真网调用；perception AST 零网络库导入。"""
    import socket

    def _no_socket(*args, **kwargs):  # pragma: no cover — 触网即被捕获
        raise AssertionError("网络单落点违规：perception 直触 socket")

    monkeypatch.setattr(socket, "socket", _no_socket)
    monkeypatch.setattr(socket, "create_connection", _no_socket)

    from qswarm.perception.channels import build_model_shards, channel_b_scan

    shards = build_model_shards([("a.py", "import os\nos.system(x)\n")], max_file_lines=800)

    class _Local:
        def __init__(self):
            self.calls = 0

        def complete(self, *, model_ref, messages, temperature, timeout_s=None):
            self.calls += 1
            import json as _json

            return _json.dumps({"candidates": []})

    adapter = _Local()
    out = channel_b_scan(shards, adapter=adapter, model_ref="cheap:x/y@cv1", agent_id="A@cv1")
    assert adapter.calls == 1 and out.failures == []

    for py in sorted(PERCEPTION.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            for name in names:
                if name in ("socket", "urllib", "http", "requests"):
                    # 唯一例外：无——supply.adapter 是全仓唯一网络落点
                    raise AssertionError(f"{py.name} 导入网络库 {name}")
