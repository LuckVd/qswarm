"""exams.bank：种子题库数据面（SPEC-6 §1）。

SeedItem / SeedBank pydantic 契约（列对齐 SPEC-1 §2 exam_items 已冻结 DDL；
``upstream`` / ``mutation`` / ``snapshot_files`` 为元数据字段，不入 DDL——随
item.json / 快照清单工件承载）+ 装载器 + 五条校验器（§1.1，装载时全量执行）+
金丝雀检查（§1.4）+ 工件库摄取（§1.3，kind=seed 内容寻址索引行）。

盘上布局（§1.3）::

    seeds/
      bank.yaml                     # bank_version、生成时间、items[]: {item_id, sha256(题目录)}
      items/<item_id>/item.json     # SeedItem 序列化
      items/<item_id>/snapshot/...  # 快照文件（漏洞文件全文 + 必要邻接文件）
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from qswarm.contracts import ExamItemSource, load_taxonomy

__all__ = [
    "V0_QUOTA",
    "MutationMeta",
    "SeedBank",
    "SeedItem",
    "UpstreamRef",
    "load_bank",
    "manifest_content",
    "manifest_sha256",
    "quota_table",
    "validate_bank",
]

#: v0 题库配额（S1 拍板，100 题；SPEC-6 §1.2 表逐格；v0.1 修订 2026-10-11：deser
#: 退出 v0 题库，六→五家族，名额重排——Juliet Java v1.3 无 CWE502 且 OWASP 无
#: deser 类目，fp 面无源；详见 DECISIONS 同日行）：family → (real, fp)。
V0_QUOTA: dict[str, tuple[int, int]] = {
    "injection": (18, 10),
    "crypto": (12, 8),
    "authz": (12, 8),
    "config_dep": (12, 8),
    "mem_safety": (8, 4),
}

#: 金丝雀串形态（§1.4）：qsw-canary-<item_id>-<8位十六进制>（item_id 自带连字符）。
_CANARY_RE = re.compile(r"^qsw-canary-(SEED-\d{4})-[0-9a-f]{8}$")

#: 题号形态（§1.1）：SEED-<4位序号>。
_ITEM_ID_RE = re.compile(r"^SEED-\d{4}$")

#: 变异四族词表（§2 / §16.7-3 逐字缓解）。
MUTATION_KINDS: frozenset[str] = frozenset(
    {"rename", "reorder", "dead_code", "restructure"}
)

#: 快照规模约束（S2；构建器执行、装载器复核）。
MAX_SNAPSHOT_FILES = 8
MAX_SNAPSHOT_LINES = 500


class UpstreamRef(BaseModel):
    """CVE 回滚题的 upstream 元数据（§1.1-4 原版直通红线；构建器校验日期、
    装载器复核字段在位）。"""

    model_config = ConfigDict(extra="forbid")

    cve: str                       # CVE 编号（CVE-<年>-<序号>）
    parent_sha: str                # fix commit 的 parent sha（漏洞版本快照出处）
    commit_date: str               # fix commit 日期（ISO-8601；> 模型知识截止线由构建器校验）


class MutationMeta(BaseModel):
    """变异元数据（§1.1-4 / §2：变异四族 + 每题独立种子；PatchEval 取材必附）。"""

    model_config = ConfigDict(extra="forbid")

    kinds: list[str]               # ⊆ MUTATION_KINDS（rename|reorder|dead_code|restructure）
    seed: int = Field(ge=0)        # 每题独立变异种子


class SeedItem(BaseModel):
    """一道种子考题（盘上 item.json 的 schema；入库映射到 SPEC-1 §2 exam_items
    列——snapshot_files 只随快照清单工件（repo_snapshot_ref 指向）承载，不入表）。"""

    model_config = ConfigDict(extra="forbid")

    item_id: str                   # "SEED-<4位序号>"，如 SEED-0417；全库唯一
    taxonomy_path: list[str]       # [family] 或 [family, type]；family ∈ taxonomy.yaml
    lang: str                      # 快照主语言（free-form 标签，非路由键）
    repo_snapshot_ref: str         # "artifact://seeds/<item_id>/"
    planted: bool                  # True = 代码经人工植入/变换（变异埋雷、变异真题）
    expected: Literal["real", "fp"]  # 考官答案：real=应报出漏洞；fp=应拒报
    difficulty: Literal["easy", "medium", "hard"]
    version: int = Field(ge=1)     # ≥1；变异轮换时 +1
    canary: str                    # 金丝雀串（§1.4）；全库唯一
    source: ExamItemSource         # cve_rollback|patcheval_mutated|juliet_mutation|owasp_mutation
    snapshot_files: list[str]      # 相对 snapshot/ 的文件名清单（快照清单工件承载）
    upstream: UpstreamRef | None = None     # source=cve_rollback 必附（§1.1-4）
    mutation: MutationMeta | None = None    # source=patcheval_mutated 必附（§1.1-4）

    @property
    def family(self) -> str:
        """家族级判分键（v0 播种只按家族级判分，S4）。"""
        return self.taxonomy_path[0]


class SeedBank(BaseModel):
    """题库（bank.yaml + items/ 的内存形态；bank_version 单调递增，跨版本不可混卷）。"""

    model_config = ConfigDict(extra="forbid")

    bank_version: int = Field(ge=1)
    generated_at: str              # 构建器签发时间（ISO-8601）
    items: list[SeedItem]
    _root: Path | None = PrivateAttr(default=None)   # 盘上根（load_bank 绑定；校验器用）

    def item(self, item_id: str) -> SeedItem:
        """按题号取题（缺失 → KeyError）。"""
        for it in self.items:
            if it.item_id == item_id:
                return it
        raise KeyError(f"题号不存在：{item_id!r}")

    def quota_table(self) -> dict[str, tuple[int, int]]:
        """逐家族 (real, fp) 计数表（E 组交付物判据与 §1.2 配额表比对的入口）。"""
        table: dict[str, tuple[int, int]] = {}
        for it in self.items:
            real, fp = table.get(it.family, (0, 0))
            if it.expected == "real":
                real += 1
            else:
                fp += 1
            table[it.family] = (real, fp)
        return table

    def families(self) -> list[str]:
        """家族清单（taxonomy 序无保证——按首次出现序；出卷/播种侧以 taxonomy.yaml 序为准）。"""
        seen: list[str] = []
        for it in self.items:
            if it.family not in seen:
                seen.append(it.family)
        return seen

    def pools(self, family: str) -> tuple[list[SeedItem], list[SeedItem]]:
        """(真题池, 雷池)（draw_paper 消费；池内保持 items 装载序）。"""
        real = [it for it in self.items if it.family == family and it.expected == "real"]
        fp = [it for it in self.items if it.family == family and it.expected == "fp"]
        return real, fp


def quota_table(bank: SeedBank) -> dict[str, tuple[int, int]]:
    """模块级便捷形态（等价 :meth:`SeedBank.quota_table`）。"""
    return bank.quota_table()


def manifest_content(item: SeedItem, root: Path) -> bytes:
    """题快照清单工件内容（§1.3：repo_snapshot_ref 指向；name→sha256 索引）。

    确定性：files 按 snapshot_files 清单序、逐文件 sha256 由内容寻址——同题同内容
    恒同清单（工件可反查的稳定键）。"""
    files = []
    for name in item.snapshot_files:
        data = (root / "items" / item.item_id / "snapshot" / name).read_bytes()
        files.append({"name": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
    payload = {"item_id": item.item_id, "repo_snapshot_ref": item.repo_snapshot_ref, "files": files}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def manifest_sha256(item: SeedItem, root: Path) -> str:
    """题快照清单工件的内容寻址 hash（repo_snapshot_ref 反查键，§1.3）。"""
    return hashlib.sha256(manifest_content(item, root)).hexdigest()


# ── 校验器（§1.1 五条，装载时全量执行，任一失败 → ValueError 带题号）────────


def _check_snapshot_disk(item: SeedItem, root: Path) -> None:
    """规则 2/3 盘面复核：快照在位、清单一致、规模约束、金丝雀在位。"""
    snap_dir = root / "items" / item.item_id / "snapshot"
    if not snap_dir.is_dir():
        raise ValueError(f"[{item.item_id}] 快照目录缺失：{snap_dir}")
    actual = sorted(
        p.relative_to(snap_dir).as_posix()
        for p in snap_dir.rglob("*")
        if p.is_file()
    )
    if sorted(item.snapshot_files) != actual:
        raise ValueError(
            f"[{item.item_id}] snapshot_files 与目录实际内容不一致："
            f"清单={sorted(item.snapshot_files)} 实际={actual}"
        )
    if not item.snapshot_files:
        raise ValueError(f"[{item.item_id}] 快照文件清单为空")
    if len(item.snapshot_files) > MAX_SNAPSHOT_FILES:
        raise ValueError(
            f"[{item.item_id}] 快照文件数 {len(item.snapshot_files)} > {MAX_SNAPSHOT_FILES}（S2）"
        )
    canary_hits = 0
    for name in item.snapshot_files:
        path = snap_dir / name
        data = path.read_bytes()
        if not data:
            raise ValueError(f"[{item.item_id}] 快照文件为空：{name}")
        text = data.decode("utf-8", errors="replace")
        if text.count("\n") + 1 > MAX_SNAPSHOT_LINES:
            raise ValueError(
                f"[{item.item_id}] 快照文件超 {MAX_SNAPSHOT_LINES} 行：{name}（S2）"
            )
        canary_hits += text.count(item.canary)
    if canary_hits < 1:
        raise ValueError(f"[{item.item_id}] 金丝雀串在快照文本中出现 0 次（§1.4）")


def validate_bank(bank: SeedBank, *, root: Path | None = None, taxonomy=None) -> None:
    """五条校验器全量执行（§1.1；任一失败 → ValueError 带题号）。

    1. item_id/canary 全库唯一 + 形态；taxonomy_path[0] ∈ taxonomy.yaml families；
    2. 快照文件在盘上存在且非空；snapshot_files 与目录实际内容一致（≤8 文件/≤500 行）；
    3. canary 串在快照文本中出现 ≥1 次；
    4. 原版直通红线：cve_rollback 必附 upstream（CVE+parent sha+commit 日期在位）；
       patcheval_mutated 必附变异元数据（原版不入考题）；
    5. 每家族 real/fp 配额满足 §1.2 v0 配额表。
    """
    root = root if root is not None else bank._root
    taxonomy = taxonomy if taxonomy is not None else load_taxonomy()
    seen_ids: set[str] = set()
    seen_canaries: set[str] = set()
    family_names = set(taxonomy.families)
    for it in bank.items:
        if not _ITEM_ID_RE.match(it.item_id):
            raise ValueError(f"[{it.item_id}] item_id 形态非法（期望 SEED-<4位序号>）")
        if it.item_id in seen_ids:
            raise ValueError(f"[{it.item_id}] item_id 重复（全库唯一性）")
        seen_ids.add(it.item_id)
        if not _CANARY_RE.match(it.canary):
            raise ValueError(f"[{it.item_id}] canary 形态非法（期望 qsw-canary-<item_id>-<8位hex>）")
        if it.canary in seen_canaries:
            raise ValueError(f"[{it.item_id}] canary 重复（全库唯一性）")
        seen_canaries.add(it.canary)
        if not it.taxonomy_path or len(it.taxonomy_path) > 2:
            raise ValueError(
                f"[{it.item_id}] taxonomy_path 长度非法（[family] 或 [family, type]）"
            )
        if it.taxonomy_path[0] not in family_names:
            raise ValueError(
                f"[{it.item_id}] 家族不在 taxonomy.yaml：{it.taxonomy_path[0]!r}"
            )
        if it.repo_snapshot_ref != f"artifact://seeds/{it.item_id}/":
            raise ValueError(
                f"[{it.item_id}] repo_snapshot_ref 形态非法（期望 artifact://seeds/<item_id>/）"
            )
        if it.source is ExamItemSource.CVE_ROLLBACK:
            if it.upstream is None:
                raise ValueError(f"[{it.item_id}] cve_rollback 缺 upstream 元数据（原版直通红线）")
            if not it.upstream.cve.startswith("CVE-"):
                raise ValueError(f"[{it.item_id}] upstream.cve 形态非法：{it.upstream.cve!r}")
            if not it.upstream.parent_sha:
                raise ValueError(f"[{it.item_id}] upstream.parent_sha 缺失（原版直通红线）")
            try:
                datetime.fromisoformat(it.upstream.commit_date)
            except ValueError as exc:
                raise ValueError(
                    f"[{it.item_id}] upstream.commit_date 非 ISO-8601：{it.upstream.commit_date!r}"
                ) from exc
        if it.source is ExamItemSource.PATCHEVAL_MUTATED and it.mutation is None:
            raise ValueError(
                f"[{it.item_id}] patcheval 取材题缺变异元数据（原版直通红线：原版不入考题）"
            )
        if it.mutation is not None:
            bad = sorted(set(it.mutation.kinds) - MUTATION_KINDS)
            if bad:
                raise ValueError(
                    f"[{it.item_id}] 变异族越词表：{bad}（合法：{sorted(MUTATION_KINDS)}）"
                )
        if root is not None:
            _check_snapshot_disk(it, root)
    quota = bank.quota_table()
    missing = sorted(set(V0_QUOTA) - set(quota))
    if missing:
        raise ValueError(f"配额缺家族：{missing}（v0 五家族必须齐备）")
    extra = sorted(set(quota) - set(V0_QUOTA))
    if extra:
        raise ValueError(f"配额外家族：{extra}（v0 配额表限定五家族）")
    for family, expect in V0_QUOTA.items():
        got = quota.get(family, (0, 0))
        if got != expect:
            raise ValueError(f"[{family}] real/fp 配额不符：{got} ≠ {expect}（§1.2 v0 表）")


def dir_sha256(item_dir: Path) -> str:
    """题目录内容 hash（bank.yaml 清单；sorted(name, content-digest) 串联哈希）。

    构建器（scripts/build_seeds/）签发、装载器复核——bank.yaml sha256 清单自洽
    （SPEC-6 §8 门禁「bank.yaml sha256 清单自洽」的机器可检形态）。"""
    h = hashlib.sha256()
    for p in sorted(item_dir.rglob("*")):
        if p.is_file():
            rel = p.relative_to(item_dir).as_posix()
            h.update(rel.encode("utf-8"))
            h.update(b"\x00")
            h.update(hashlib.sha256(p.read_bytes()).digest())
    return h.hexdigest()


def load_bank(
    path: str | Path,
    *,
    db=None,
    store_dir: str | Path | None = None,
) -> SeedBank:
    """装载题库（bank.yaml + items/）并全量校验。

    - bank.yaml 逐题 ``sha256``（若在位）按 :func:`dir_sha256` 复核——清单自洽门；
    - ``db`` 给定（KernelLedger）时随装载完成工件库摄取（§1.3：快照文件 + item.json
      入 kind=seed 工件、exam_items UPSERT——``store_dir`` 为内容寻址库根目录）与
      exam_items 行落位（§4.3）；缺省纯装载（E 组判据形态）。
    - 校验任一条红 → ValueError（含题号）。"""
    from .store import ingest_bank_artifacts  # 局部导入避免模块环

    p = Path(path)
    root = p.parent
    meta = json.loads(p.read_text(encoding="utf-8"))
    items: list[SeedItem] = []
    for entry in meta["items"]:
        item_id = entry["item_id"]
        declared = entry.get("sha256")
        if declared is not None:
            actual = dir_sha256(root / "items" / item_id)
            if actual != declared:
                raise ValueError(
                    f"[{item_id}] bank.yaml sha256 清单不自洽：{declared[:12]}… ≠ {actual[:12]}…"
                )
        item_json = root / "items" / item_id / "item.json"
        items.append(SeedItem.model_validate(json.loads(item_json.read_text(encoding="utf-8"))))
    bank = SeedBank.model_validate(
        {"bank_version": meta["bank_version"], "generated_at": meta["generated_at"], "items": items}
    )
    bank._root = root
    validate_bank(bank)
    if db is not None:
        if store_dir is None:
            raise ValueError("load_bank(db=…) 需要同时给 store_dir（内容寻址库根目录）")
        ingest_bank_artifacts(bank, root, db, store_dir=Path(store_dir))
        from .store import upsert_exam_items

        upsert_exam_items(db, bank)
    return bank
