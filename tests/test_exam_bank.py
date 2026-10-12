"""exams.bank 测试（SPEC-6 §1；SPEC-5 M2 卷 §1.3 最低用例集）。

钉死：SeedItem 契约与词表、五条校验器逐条负例、v0 配额表、canary 全库唯一且在
快照文本 ≥1 次、原版直通红线（patcheval 必附变异元数据 / cve_rollback 必附
upstream）、快照工件摄取（kind=seed + artifacts 索引行 + repo_snapshot_ref 反查）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qswarm.kernel import FixedClock, KernelLedger
from qswarm.exams.bank import (
    V0_QUOTA,
    SeedItem,
    load_bank,
    manifest_content,
    manifest_sha256,
    validate_bank,
)
from qswarm.exams.store import artifact_get, upsert_exam_items

from m2_harness import T0, make_bank


@pytest.fixture(scope="module")
def bank_root(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("bank")
    make_bank(root)
    return root


@pytest.fixture(scope="module")
def bank(bank_root):
    return load_bank(bank_root / "bank.yaml")


def test_seed_item_schema_and_vocabulary(bank):
    """SeedItem 字段与 DDL 列映射（snapshot_files/upstream/mutation 为元数据不入
    表）；expected/source/family 词表校验。"""
    item = bank.items[0]
    assert set(SeedItem.model_fields) == {
        "item_id", "taxonomy_path", "lang", "repo_snapshot_ref", "planted",
        "expected", "difficulty", "version", "canary", "source",
        "snapshot_files", "upstream", "mutation",
    }
    assert item.expected in ("real", "fp")
    assert item.difficulty in ("easy", "medium", "hard")
    assert str(item.source) in ("cve_rollback", "patcheval_mutated", "juliet_mutation", "owasp_mutation")
    assert item.family in V0_QUOTA
    # 词表越界 → pydantic 拒绝
    bad = item.model_dump()
    bad["expected"] = "maybe"
    with pytest.raises(Exception):
        SeedItem.model_validate(bad)
    bad2 = item.model_dump()
    bad2["source"] = "cve_original"          # 原版不在枚举（原版直通红线的数据面）
    with pytest.raises(Exception):
        SeedItem.model_validate(bad2)
    bad3 = item.model_dump()
    bad3["taxonomy_path"] = ["nosuchfamily"]
    # 家族词表在 bank 校验器层（§1.1-1），条目级不校验
    SeedItem.model_validate(bad3)
    from qswarm.exams.bank import SeedBank

    lone = SeedBank.model_validate(
        {"bank_version": 1, "generated_at": "2026-10-11T00:00:00Z",
         "items": [SeedItem.model_validate(bad3)]}
    )
    with pytest.raises(ValueError, match="taxonomy"):
        validate_bank(lone)


def test_bank_loader_validates_five_rules(bank_root, bank):
    """五条校验器逐条负例：唯一性 / 快照在位 / canary 在位 / 原版直通红线 / 配额。"""
    # 1. 唯一性：canary 重复 → ValueError（带题号）
    dup = load_bank(bank_root / "bank.yaml")
    dup.items[1] = dup.items[1].model_copy(update={"canary": dup.items[0].canary})
    with pytest.raises(ValueError, match="canary 重复"):
        validate_bank(dup)
    # 1b. item_id 重复
    dup2 = load_bank(bank_root / "bank.yaml")
    dup2.items[1] = dup2.items[1].model_copy(update={"item_id": dup2.items[0].item_id})
    with pytest.raises(ValueError, match="item_id 重复"):
        validate_bank(dup2)
    # 2. 快照清单与目录不一致
    miss = load_bank(bank_root / "bank.yaml")
    miss.items[0] = miss.items[0].model_copy(update={"snapshot_files": ["main.c"]})
    with pytest.raises(ValueError, match="不一致"):
        validate_bank(miss, root=bank_root)
    # 2b. 快照文件被清空
    empty_file = bank_root / "items" / bank.items[0].item_id / "snapshot" / "util.c"
    keep = empty_file.read_text(encoding="utf-8")
    empty_file.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="为空"):
        validate_bank(load_bank(bank_root / "bank.yaml"), root=bank_root)
    empty_file.write_text(keep, encoding="utf-8")
    # 3. canary 从快照文本移除 → ValueError
    main_c = bank_root / "items" / bank.items[0].item_id / "snapshot" / "main.c"
    keep_main = main_c.read_text(encoding="utf-8")
    main_c.write_text(keep_main.replace(bank.items[0].canary + "\n", ""), encoding="utf-8")
    with pytest.raises(ValueError, match="金丝雀"):
        validate_bank(load_bank(bank_root / "bank.yaml"), root=bank_root)
    main_c.write_text(keep_main, encoding="utf-8")
    # 4a. cve_rollback 缺 upstream → ValueError（原版直通红线）
    no_up = load_bank(bank_root / "bank.yaml")
    idx = next(i for i, it in enumerate(no_up.items) if str(it.source) == "cve_rollback")
    no_up.items[idx] = no_up.items[idx].model_copy(update={"upstream": None})
    with pytest.raises(ValueError, match="upstream"):
        validate_bank(no_up)
    # 4b. patcheval 缺变异元数据 → ValueError
    no_mut = load_bank(bank_root / "bank.yaml")
    idx = next(i for i, it in enumerate(no_mut.items) if str(it.source) == "patcheval_mutated")
    no_mut.items[idx] = no_mut.items[idx].model_copy(update={"mutation": None})
    with pytest.raises(ValueError, match="变异元数据"):
        validate_bank(no_mut)
    # 5. 配额不符（抽掉一题）→ ValueError
    short = load_bank(bank_root / "bank.yaml")
    short.items = short.items[:-1]
    with pytest.raises(ValueError, match="配额"):
        validate_bank(short)
    # 全绿基准（对照：以上负例修完后原库仍全绿）
    validate_bank(bank, root=bank_root)


def test_bank_quota_table_v0(bank):
    """100 题；逐家族 real/fp 配额 ≡ SPEC-6 §1.2 v0.1 表（injection 18/10 … mem_safety 8/4，五家族）。"""
    assert len(bank.items) == 100
    expect = {
        "injection": (18, 10), "crypto": (12, 8),
        "authz": (12, 8), "config_dep": (12, 8), "mem_safety": (8, 4),
    }
    assert bank.quota_table() == expect
    assert bank.quota_table() == V0_QUOTA
    assert sum(r for r, _ in expect.values()) == 62
    assert sum(f for _, f in expect.values()) == 38
    assert bank.bank_version == 1


def test_canary_unique_and_present_in_snapshot(bank_root, bank):
    """canary 全库唯一（100/100）且在快照文本 ≥1 次；形态 qsw-canary-<item_id>-<8hex>。"""
    canaries = [it.canary for it in bank.items]
    assert len(set(canaries)) == 100
    for it in bank.items:
        assert it.canary.startswith(f"qsw-canary-{it.item_id}-")
        assert len(it.canary.rsplit("-", 1)[1]) == 8
        snap_dir = bank_root / "items" / it.item_id / "snapshot"
        text = "".join((snap_dir / f).read_text(encoding="utf-8") for f in it.snapshot_files)
        assert it.canary in text


def test_no_unmutated_patcheval_items(bank_root, bank):
    """原版直通 0：patcheval_mutated 必附变异元数据（四族词表 + 种子）；cve_rollback
    必附 upstream（CVE+parent sha+commit 日期）；快照规模约束在位（≤8 文件/≤500 行）。"""
    for it in bank.items:
        if str(it.source) == "patcheval_mutated":
            assert it.mutation is not None
            assert it.mutation.kinds and set(it.mutation.kinds) <= {"rename", "reorder", "dead_code", "restructure"}
            assert it.mutation.seed >= 0
        if str(it.source) == "cve_rollback":
            assert it.upstream is not None
            assert it.upstream.cve.startswith("CVE-")
            assert len(it.upstream.parent_sha) >= 7
            assert it.upstream.commit_date
        assert len(it.snapshot_files) <= 8
        for f in it.snapshot_files:
            text = (bank_root / "items" / it.item_id / "snapshot" / f).read_text(encoding="utf-8")
            assert text.count("\n") + 1 <= 500
    # 变异族越词表 → ValueError
    from qswarm.exams.bank import MutationMeta

    bad = load_bank(bank_root / "bank.yaml")
    idx = next(i for i, it in enumerate(bad.items) if it.mutation is not None)
    bad.items[idx] = bad.items[idx].model_copy(
        update={"mutation": MutationMeta(kinds=["rewrite_everything"], seed=1)}
    )
    with pytest.raises(ValueError, match="变异族越词表"):
        validate_bank(bad)


def test_snapshot_ingest_registers_seed_artifacts(bank_root, bank, tmp_path):
    """快照文件入工件库 kind=seed + artifacts 索引行；repo_snapshot_ref 可反查
    （清单工件 = name→sha256 索引；§1.3 部署形态解耦）。"""
    store = tmp_path / "store"
    with KernelLedger(tmp_path / "ingest.db", clock=FixedClock(T0)) as led:
        loaded = load_bank(bank_root / "bank.yaml", db=led, store_dir=store)
        validate_bank(loaded)
        # 每题：2 快照 + item.json + 清单工件；工件库按内容寻址去重（同内容同 hash）
        import hashlib as _hl

        uniq = set()
        for it in loaded.items:
            d = bank_root / "items" / it.item_id
            for name in (*it.snapshot_files, "../item.json"):
                uniq.add(_hl.sha256((d / "snapshot" / name).resolve().read_bytes()).hexdigest())
            uniq.add(_hl.sha256(manifest_content(it, bank_root)).hexdigest())
        rows = led.conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE kind = 'seed'"
        ).fetchone()[0]
        assert rows == len(uniq) >= 100 * 3      # ≥ 每题 3 个必然唯一的内容物
        # repo_snapshot_ref 反查：清单工件按内容寻址可取回
        it = bank.items[0]
        manifest_hash = manifest_sha256(it, bank_root)
        raw = artifact_get(led, store, manifest_hash)
        assert raw is not None
        manifest = json.loads(raw.decode("utf-8"))
        assert manifest["item_id"] == it.item_id
        assert manifest["repo_snapshot_ref"] == it.repo_snapshot_ref
        assert {f["name"] for f in manifest["files"]} == set(it.snapshot_files)
        # 快照文件本体按清单 sha256 可取回
        for f in manifest["files"]:
            body = artifact_get(led, store, f["sha256"])
            assert body is not None
            assert body == (bank_root / "items" / it.item_id / "snapshot" / f["name"]).read_bytes()
        # exam_items UPSERT 兜底装载（§4.3）：100 行全在位
        assert led.conn.execute("SELECT COUNT(*) FROM exam_items").fetchone()[0] == 100
        upsert_exam_items(led, bank)            # 幂等：同版本重放零写入
        assert led.conn.execute("SELECT COUNT(*) FROM exam_items").fetchone()[0] == 100
