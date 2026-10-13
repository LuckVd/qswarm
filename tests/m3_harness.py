"""m3_harness：M3 测试共享夹具——感知层测试仓 / 脚本化侦察适配器 / 播种库引导。

三件套：
- ``make_repo``：确定性小仓库（python/c/go + 线索命中点），通道 A/C 判据输入；
- ``ScriptedScoutAdapter``：通道 B（l0scan）与 L1（l1:）双面脚本化适配器——
  按 messages 形态识别场景、按脚本决定产出（零网络，D27 同纪律）；
- ``make_prod_ledger``：经 m2_harness 播种（4 蜂 20 笔考试存款）→
  kernel.bootstrap.init_prod_ledger 产出就绪生产账本（D28 全链）。
"""

from __future__ import annotations

import json
from pathlib import Path

from qswarm.kernel import FixedClock, KernelLedger
from qswarm.kernel.bootstrap import init_prod_ledger

import m2_harness

__all__ = [
    "T0",
    "ScriptedScoutAdapter",
    "make_repo",
    "fake_rules_ok",
    "fake_rules_hit",
    "make_seed_db",
    "make_prod_ledger",
]

T0 = "2030-01-01T00:00:00Z"

REPO_PY = '''import os
import pickle


def run(cmd):
    os.system(cmd)


def load(data):
    return pickle.loads(data)


API_SECRET = "hardcoded-value"
'''

REPO_C = '''#include <string.h>

void copy(char *dst, const char *src) {
    strcpy(dst, src);
}
'''

REPO_GO = '''package main

func main() {
}
'''


def make_repo(root: Path) -> Path:
    """确定性小仓库：src/api.py（injection/deser/config_dep 线索）+
    src/util.c（mem_safety 线索）+ src/main.go（干净文件）。"""
    root = Path(root)
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "api.py").write_text(REPO_PY, encoding="utf-8")
    (root / "src" / "util.c").write_text(REPO_C, encoding="utf-8")
    (root / "src" / "main.go").write_text(REPO_GO, encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    return root


def fake_rules_ok(paths: list[str]) -> str:
    """规则通道空结果运行器（装通但零命中；paths 消费不抛错）。"""
    _ = paths
    return json.dumps({"results": [], "errors": []})


def fake_rules_hit(paths: list[str]) -> str:
    """规则通道命中运行器：api.py:5 一条 injection 规则命中（与 A 通道同位）。"""
    _ = paths
    return json.dumps(
        {
            "results": [
                {
                    "check_id": "qsw.injection.os-system",
                    "path": "src/api.py",
                    "start": {"line": 5},
                    "end": {"line": 5},
                    "extra": {"metadata": {"family": "injection"}},
                }
            ],
            "errors": [],
        }
    )


class ScriptedScoutAdapter:
    """通道 B（l0scan）+ L1（l1:）双面脚本化适配器（零网络）。

    - 场景识别：system 消息含 "L0 直扫" → 通道 B；数据块头行含 "candidate_id:"
      → L1 分诊；
    - ``l0_script(shard_ref, index) -> str|None``：None = 缺省空候选集响应；
    - ``l1_script(candidate_id, path) -> dict|None``：None = 缺省ace 响应
      （真实候选 → suspicious+家族 aligned；干净路径 → 拦截）；
    - ``fail_shards``/``fail_candidates``：注入两连败（通道 B 缺席 / L1 schema_fail）；
    - ``calls`` 记录全部 (场景, 标识) 供断言。"""

    def __init__(
        self,
        *,
        l0_script=None,
        l1_script=None,
        fail_shards: set[str] | None = None,
        fail_candidates: set[str] | None = None,
    ) -> None:
        self._l0_script = l0_script
        self._l1_script = l1_script
        self.fail_shards = set(fail_shards or ())
        self.fail_candidates = set(fail_candidates or ())
        self.calls: list[tuple[str, str]] = []

    @staticmethod
    def _first_line(messages, role: str) -> str:
        for m in messages:
            if m.role == role:
                return m.content.splitlines()[0]
        return ""

    def complete(self, *, model_ref, messages, temperature, timeout_s=None) -> str:
        system_head = self._first_line(messages, "system")
        if "L0 直扫" in system_head:
            ref = messages[1].content.split("<<<UNTRUSTED_DATA ", 1)[1].split(" begin", 1)[0]
            self.calls.append(("l0scan", ref))
            if ref in self.fail_shards:
                return "not-json"          # schema 畸形（两连败 → 分片缺席）
            if self._l0_script is not None:
                scripted = self._l0_script(ref)
                if scripted is not None:
                    return scripted
            return json.dumps({"candidates": []}, ensure_ascii=False)
        # L1 分诊：候选标识/路径在数据块头行（user 消息首行是契约任务头 goal）。
        user = messages[1].content
        candidate_id = user.split("candidate_id: ", 1)[1].split("\n", 1)[0]
        path = user.split("path: ", 1)[1].split("\n", 1)[0]
        self.calls.append(("l1", candidate_id))
        if candidate_id in self.fail_candidates:
            return "not-json"
        if self._l1_script is not None:
            scripted = self._l1_script(candidate_id, path)
            if scripted is not None:
                return json.dumps(scripted, ensure_ascii=False)
        suspicious = "api.py" in path or "util.c" in path
        return json.dumps(
            {
                "candidate_id": candidate_id,
                "suspicious": suspicious,
                "guess_family": "injection" if "api.py" in path else ("mem_safety" if "util.c" in path else None),
                "guess_type": None,
                "reason_ref": "artifact://pending",
                "injection_flag": False,
            },
            ensure_ascii=False,
        )


# ── 播种库 → 生产账本（D28 全链）──────────────────────────────────

def make_seed_db(db_path: Path, bank_root: Path) -> Path:
    """经 m2_harness 合成题库播种出种子账本（4 蜂 active + 20 笔考试存款）。"""
    from qswarm.exams.seeding import seed_entry
    from qswarm.supply.config import load_models

    models = load_models(m2_harness.build_models_yaml(bank_root / "models.yaml"))
    bank = m2_harness.make_bank(bank_root / "seeds")
    resolve = m2_harness.make_resolver(bank_root / "seeds")
    with KernelLedger(db_path, clock=FixedClock(T0)) as led:
        report = seed_entry(
            led, bank, models, m2_harness.FakeAdapter(bank_root / "seeds"),
            seed=7, resolve_text=resolve,
        )
        assert report["totals"]["deposits"] == 20
        assert report["qc_alerts"] == []
    return db_path


def make_prod_ledger(prod_path: Path, seed_db_path: Path) -> KernelLedger:
    """种子账本 → 就绪生产账本（bootstrap 全链；返回已打开句柄，调用方负责 close）。"""
    init_prod_ledger(prod_path, seed_db_path=seed_db_path, clock=FixedClock(T0))
    return KernelLedger(prod_path, clock=FixedClock(T0))
