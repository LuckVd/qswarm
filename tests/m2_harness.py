"""m2_harness：M2 测试共享夹具——合成题库构建 + 脚本化候选适配器（FakeAdapter）。

合成题库（100 题，逐家族配额 ≡ SPEC-6 §1.2）确定性生成：题面正文与真实漏洞无关
（判分只消费 family/expected 元数据与产出对齐），快照文件满足规模约束（≤8 文件/
≤500 行）且金丝雀在位。供 tests/test_exam_*.py 复用；生产题库由
scripts/build_seeds/（外网侧流水线）构建，两者共用同一校验器与同一播种管道。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from qswarm.exams.bank import V0_QUOTA, SeedBank, load_bank, validate_bank

__all__ = [
    "FakeAdapter",
    "T0",
    "build_models_yaml",
    "make_bank",
    "make_resolver",
    "scout_answer",
    "auditor_answer",
]

T0 = "2030-01-01T00:00:00Z"

# v0.1 配额（2026-10-11）：deser 退出 v0 题库，五家族（SPEC-6 §1.2 / DECISIONS 同日行）
FAMILIES = ("injection", "crypto", "authz", "config_dep", "mem_safety")
LANGS = {
    "injection": "python",
    "crypto": "c",
    "authz": "java",
    "config_dep": "go",
    "mem_safety": "c",
}
REAL_SOURCES = ("cve_rollback", "juliet_mutation", "owasp_mutation")


def make_bank(root: Path, *, bank_version: int = 1) -> SeedBank:
    """确定性合成 100 题库（quota ≡ V0_QUOTA；校验器全绿）。幂等：已建则直接装载。"""
    root = Path(root)
    if (root / "bank.yaml").exists():
        bank = load_bank(root / "bank.yaml")
        validate_bank(bank)
        return bank
    (root / "items").mkdir(parents=True, exist_ok=True)
    items = []
    seq = 0
    for fam in FAMILIES:
        real_n, fp_n = V0_QUOTA[fam]
        for kind, n in (("real", real_n), ("fp", fp_n)):
            for k in range(n):
                seq += 1
                item_id = f"SEED-{seq:04d}"
                canary = f"qsw-canary-{item_id}-deadbeef"
                snap = root / "items" / item_id / "snapshot"
                snap.mkdir(parents=True)
                (snap / "main.c").write_text(
                    f"// canary {canary}\n// snapshot body {item_id}\n"
                    "int f(int x) { return x + 1; }\n",
                    encoding="utf-8",
                )
                (snap / "util.c").write_text("int g(void) { return 0; }\n", encoding="utf-8")
                source = (
                    REAL_SOURCES[k % 3] if kind == "real"
                    else ("patcheval_mutated" if k % 2 == 0 else "juliet_mutation")
                )
                payload: dict = {
                    "item_id": item_id,
                    "taxonomy_path": [fam],
                    "lang": LANGS[fam],
                    "repo_snapshot_ref": f"artifact://seeds/{item_id}/",
                    "planted": kind == "fp" or (kind == "real" and k % 2 == 1),
                    "expected": kind,
                    "difficulty": ("easy", "medium", "hard")[k % 3],
                    "version": 1,
                    "canary": canary,
                    "source": source,
                    "snapshot_files": ["main.c", "util.c"],
                }
                if source == "cve_rollback":
                    payload["upstream"] = {
                        "cve": f"CVE-2099-{seq:04d}",
                        "parent_sha": "a" * 40,
                        "commit_date": "2098-06-01",
                    }
                if source == "patcheval_mutated":
                    payload["mutation"] = {"kinds": ["rename"], "seed": seq}
                (root / "items" / item_id / "item.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
                )
                items.append({"item_id": item_id})
    (root / "bank.yaml").write_text(
        json.dumps(
            {
                "bank_version": bank_version,
                "generated_at": "2026-10-11T00:00:00Z",
                "items": items,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    bank = load_bank(root / "bank.yaml")
    validate_bank(bank)
    return bank


def make_resolver(root: Path):
    """盘上直读快照解析器（run_entry_cell resolve_text 形态）。"""
    root = Path(root)

    def resolve(item, name: str) -> str:
        return (root / "items" / item.item_id / "snapshot" / name).read_text(encoding="utf-8")

    return resolve


MODELS_YAML = """
tiers:
  cheap:
    primary: {provider: deepseek, model: deepseek-chat, api_key_env: M2_TEST_KEY_CHEAP}
    fallbacks: [{provider: openai, model: gpt-4o-mini, api_key_env: M2_TEST_KEY_OPENAI}]
  strong:
    primary: {provider: anthropic, model: claude-sonnet-4-5, api_key_env: M2_TEST_KEY_STRONG}
    fallbacks: [{provider: openai, model: gpt-4o, api_key_env: M2_TEST_KEY_OPENAI}]
  mid:
    primary: {provider: deepseek, model: deepseek-reasoner, api_key_env: M2_TEST_KEY_CHEAP}
  engineering:
    primary: {provider: openai, model: gpt-4o, api_key_env: M2_TEST_KEY_OPENAI}
entry_exams:
  cheap: {min_pass: 0.8, families: all}
  strong: {min_pass: 0.8, families: all}
  mid: {min_pass: 0.8, families: all}
  engineering: {min_pass: 0.8, families: all}
runtime: {rpm_limit: 600, production_temp: 0.2}
"""


def build_models_yaml(path: Path, **overrides) -> Path:
    """参考 models.yaml 落盘（可按节覆盖，如 entry_exams.min_pass）。"""
    import yaml

    data = yaml.safe_load(MODELS_YAML)
    for dotted, value in overrides.items():
        node = data
        parts = dotted.split("__")
        for part in parts[:-1]:
            node = node[part]
        node[parts[-1]] = value
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return Path(path)


# ── 脚本化候选适配器（离线纪律：零网络；D27）──────────────────────


def _extract_item_id(messages) -> str:
    for m in messages:
        for line in m.content.splitlines():
            if line.startswith("item_id: "):
                return line.split(": ", 1)[1].strip()
    raise AssertionError("messages 中无题面头（item_id 行）")


def scout_answer(item_payload: dict, *, suspicious: bool | None = None, family: str | None = "auto", decline: bool = False) -> dict | None:
    """scout 产出构造器（family='auto' = 对齐题库家族）。"""
    if decline:
        return None
    fam = item_payload["taxonomy_path"][0] if family == "auto" else family
    return {
        "candidate_id": "CD-1",
        "suspicious": bool(suspicious if suspicious is not None else item_payload["expected"] == "real"),
        "guess_family": fam,
        "guess_type": None,
        "reason_ref": "artifact://pending",
        "injection_flag": False,
    }


def auditor_answer(item_payload: dict, *, family: str | None = "auto", decline: bool = False) -> dict | None:
    """auditor 产出构造器（family='auto' = 对齐题库家族）。"""
    if decline:
        return None
    fam = item_payload["taxonomy_path"][0] if family == "auto" else family
    return {
        "title": "t",
        "severity": "high",
        "cwe": None,
        "family": fam,
        "type": None,
        "evidence_chain": [
            {"kind": "source", "where_path": "a", "where_line": 1, "found": None, "ref": "artifact://pending"},
            {"kind": "propagation", "where_path": "b", "where_line": 2, "found": None, "ref": "artifact://pending"},
            {"kind": "sanitizer", "where_path": "c", "where_line": 3, "found": False, "ref": "artifact://pending"},
            {"kind": "sink", "where_path": "d", "where_line": 4, "found": None, "ref": "artifact://pending"},
        ],
        "confidence": 0.9,
        "disagreement_note": None,
        "artifacts": ["artifact://pending"],
    }


class FakeAdapter:
    """脚本化 CandidateAdapter：按 (model_ref, item_id) → 响应或异常。

    ``script``：callable(model_ref, item_id, item_payload) -> str | None；
    返回 str = 原始响应；None = 默认 ace 响应（按 model_ref 的角色产满分卷）。
    也可抛 AdapterError / ValueError 注入基建失败与畸形输出。记录每次调用的
    model_ref（primary-only 断言用）。"""

    def __init__(self, bank_root: Path, script=None) -> None:
        self._bank_root = Path(bank_root)
        self._script = script
        self.calls: list[tuple[str, str]] = []   # (model_ref, item_id)

    def _payload(self, item_id: str) -> dict:
        return json.loads(
            (self._bank_root / "items" / item_id / "item.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def _role(model_ref: str) -> str:
        return "scout" if model_ref.startswith("cheap:") else (
            "auditor" if model_ref.startswith("strong:") else model_ref.split(":")[0]
        )

    def ace_response(self, model_ref: str, item_id: str, payload: dict) -> str:
        role = self._role(model_ref)
        if role == "scout":
            out = scout_answer(payload)
        else:
            out = auditor_answer(payload)
        answer = {"item_id": item_id, "declined": out is None, "output": out}
        return json.dumps({"answers": [answer]}, ensure_ascii=False)

    def complete(self, *, model_ref: str, messages, temperature: float, timeout_s: float = 120.0) -> str:
        item_id = _extract_item_id(messages)
        self.calls.append((model_ref, item_id))
        payload = self._payload(item_id)
        if self._script is not None:
            scripted = self._script(model_ref, item_id, payload)
            if scripted is not None:
                return scripted
        return self.ace_response(model_ref, item_id, payload)


def fresh_dir(path: Path) -> Path:
    """测试目录准备（清理重建）。"""
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    return path
