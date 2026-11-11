"""kernel.identity：R1-T3 运行身份契约（三身份分立 + 输入 manifest 冻结）。

HANDOVER §7.2/§7.3 落地（docs/R1_CHANGE_NOTE.md §1-T3）：

- **run 输入 manifest**（``identity_version``/stage/repo_key/snapshot_digest/
  scope/参数指纹）：``repo_key`` = resolved checkout path（本地仓库身份——不猜
  两个 remote/fork/checkout 语义等价）；``snapshot_digest`` = 实际纳入扫描的
  文件内容 digest（含 dirty/untracked——只记 git HEAD 不够）。同 run 同
  manifest 恢复；同 run 换仓/改源码/改 scope → 新写入前拒绝，要求新 run
  （:func:`ensure_run_manifest`）。
- **三身份分立**：
  - ``local_locator_key`` = 既有纯感知候选 hash（perception.candidates.
    candidate_id——同仓同位定位，跨仓/跨 run 不唯一）；
  - ``candidate_occurrence_id = H(identity_version, scan_run_id, locator)``
    ——入 candidates 表主键（一次观察的单位，归属本 run）；
  - ``exact_snapshot_tracking = (repo_key, snapshot_digest, locator,
    file_blob_hash)``——跨 run 去重观察面（同快照同位=同一观察；跨 commit/
    rename 的语义同源根因合并 R1 不做，occurrence 不当根因 ID）。
- **任务身份**：``l1:{run}:{occurrence}`` / ``l2:{consult_run}:{scan_run}:
  {occurrence}``——内容寻址（候选重排/新增无关候选不改变既有任务身份），
  不用候选排序序号；跨 run 各自存在（第二 run 不被首轮同名任务吞掉）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from qswarm.kernel.ledger import KernelLedger

__all__ = [
    "IDENTITY_VERSION",
    "candidate_occurrence_id",
    "ensure_run_manifest",
    "l1_task_id",
    "l2_task_id",
    "load_run_manifest",
    "repo_key",
    "run_manifest",
    "manifest_digest",
    "snapshot_digest",
]

#: 身份契约版本（身份公式变更须升版；occurrence/manifest 均入哈希）。
IDENTITY_VERSION = "id-v1"

#: 快照面跳过的目录（与 ProcessRunner copytree ignore 同集 + VCS/缓存）。
_SNAPSHOT_IGNORE = {".git", "__pycache__", ".venv", ".pytest_cache", "node_modules"}


def _h(*parts: str) -> str:
    h = hashlib.blake2b(digest_size=16)
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def repo_key(root: str | Path) -> str:
    """本地仓库身份 = resolved checkout path（``repo:<path>``——不猜语义等价）。"""
    return f"repo:{Path(root).resolve()}"


def snapshot_digest(root: str | Path, scope: list[str] | None = None) -> str:
    """实际纳入扫描的文件内容 digest：blake2b over sorted(relpath, file-hash)。

    ``scope`` 给定时只纳入列出的相对路径（pr/changed 档）；缺失文件如实跳过
    （scope 与实际文件集的差集不伪造）。"""
    root_p = Path(root)
    files: list[tuple[str, str]] = []
    if scope is not None:
        for rel in sorted(set(scope)):
            p = root_p / rel
            if p.is_file():
                files.append((rel, _h(p.read_bytes())))
    else:
        for p in sorted(root_p.rglob("*")):
            if not p.is_file():
                continue
            if any(part in _SNAPSHOT_IGNORE for part in p.parts):
                continue
            files.append((str(p.relative_to(root_p)), _h(p.read_bytes())))
    return _h(*(f"{rel}:{fh}" for rel, fh in files)) if files else _h("empty")


def params_fingerprint(params) -> str:
    """参数面指纹（规则/解析器/prompt 关键旋钮的规范化序列化 hash）。"""
    payload = {
        "perception": {
            "rules_enabled": bool(params.perception.rules.enabled),
            "model_enabled": bool(params.perception.model.enabled),
            "l1_files_per_shard": int(params.perception.l1.files_per_shard),
        },
        "mode_face": {
            "l2_context_lines": int(params.consult.l2_context_lines),
            "panel_size": int(params.panel.SIZE),
        },
    }
    return _h(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def run_manifest(
    root: str | Path,
    *,
    stage: str,
    mode: str,
    changed_paths: list[str] | None = None,
    params=None,
) -> dict:
    """版本化输入 manifest（不可变输入的规范记录面）。"""
    scope = sorted(changed_paths) if changed_paths is not None else None
    return {
        "identity_version": IDENTITY_VERSION,
        "stage": str(stage),
        "mode": str(mode),
        "repo_key": repo_key(root),
        "snapshot_digest": snapshot_digest(root, scope),
        "scope": scope,
        "params_fingerprint": (
            params_fingerprint(params) if params is not None else "none"
        ),
    }


def manifest_digest(manifest: dict) -> str:
    return "mf:" + _h(json.dumps(manifest, sort_keys=True, ensure_ascii=False))


def ensure_run_manifest(
    ledger: KernelLedger, *, run_id: str, manifest: dict
) -> None:
    """run 行携带/校验 manifest（同 run 换输入 → ValueError，要求新 run）。

    - run 不存在 → 不建行（open_run 先行；本函数只读校验 + meta 回填）；
    - run 存在且 meta 无 manifest（legacy）→ 写入本次 manifest（首次绑定）；
    - run 存在且已有 manifest → 必须逐字段一致，否则 ValueError（写侧非法：
      同 run 输入不可变；换仓/改源码/改 scope/改参数 = 新 run）。"""
    row = ledger.conn.execute(
        "SELECT meta_json FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return                                    # open_run 尚未建行（调用序兜底）
    try:
        meta = json.loads(str(row[0]) or "{}")
    except ValueError:
        meta = {}
    existing = meta.get("run_manifest")
    if existing is None:
        from qswarm.kernel.dispatch import set_run_manifest   # runs 写口纪律（§6.1）

        meta["run_manifest"] = manifest
        set_run_manifest(
            ledger, run_id,
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
        )
        return
    if existing != manifest:
        raise ValueError(
            f"run {run_id!r} 输入 manifest 冲突：同 run 换仓/改源码/改 scope/"
            "改关键参数不被允许（R1-T3：不可变输入——要求新 run）。"
            f"既有 {manifest_digest(existing)} vs 本次 {manifest_digest(manifest)}"
        )


def load_run_manifest(ledger: KernelLedger, run_id: str) -> dict | None:
    """读 run 的输入 manifest（legacy run 无记录 → None，如实不猜）。"""
    row = ledger.conn.execute(
        "SELECT meta_json FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        meta = json.loads(str(row[0]) or "{}")
    except ValueError:
        return None
    m = meta.get("run_manifest")
    return dict(m) if isinstance(m, dict) else None


def candidate_occurrence_id(scan_run_id: str, locator_key: str) -> str:
    """观察单位身份：H(identity_version, scan_run_id, local_locator_key)。

    表主键形态（内容寻址 hex）——同仓同位每 run 各自一行（不串档不吞任务）；
    跨 run 去重走 exact_snapshot_tracking 观察面，不以 run ID 当根因 ID。"""
    return _h(IDENTITY_VERSION, scan_run_id, locator_key)


def l1_task_id(run_id: str, occurrence_id: str) -> str:
    """L1 任务身份（内容寻址：候选重排/新增无关候选不改变既有任务 id）。"""
    return f"l1:{run_id}:{occurrence_id}"


def l2_task_id(consult_run_id: str, source_scan_run_id: str, occurrence_id: str) -> str:
    """L2 任务身份（消费谱系入键：consult run × 源 scan run × occurrence）。"""
    return f"l2:{consult_run_id}:{source_scan_run_id}:{occurrence_id}"
