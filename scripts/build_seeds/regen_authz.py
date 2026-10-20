#!/usr/bin/env python3
"""regen_authz：authz 族 11 题（SEED-0050..0060）v0.4 引擎外科重生成 + bank v3 签发。

背景（DECISIONS 2026-10-20 行）：v0.3 rename 变异打坏关键字/宏/include，快照不可编译，
评审蜂合理拒报 → authz QC 崩盘。变异引擎修复（_nomut_mask + _unrenameable +
syntax_gate + taint_sink_live）后，本脚本以**原 mutation.seed** 重生成受影响题：

- 素材源：/tmp/juliet-c（arichardson 镜像稀疏克隆，CWE23/CWE36 两目录）+ SEED-0060
  的 OWASP BenchmarkTest00001.java（/tmp/owasp-bench/）；
- 原始文件定位：现快照头部 `Filename:` 注释反查（mutation 不可逆，故取镜像同名原件）；
- 门禁全过才落盘：expected_recheck + syntax_gate + taint_sink_live（+ 取材面
  taint_param_live 经 taint_sink_live 复用）；
- item.json version 1→2；canary/难度/seed 全部按原 seq 确定性复现（_write_item）；
- bank.yaml：11 题 sha256 重算、bank_version→3（BANK_VERSION 常量同步）；
- 任一门禁弃题 → 计数如实报告（不静默缩编：exit 1 由负责人裁决）。

用法（仓库根）：.venv/bin/python scripts/build_seeds/regen_authz.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import build  # noqa: E402  (scripts/build_seeds/build.py)

ROOT = _HERE.parent.parent
ITEMS = ROOT / "seeds" / "items"
BACKUP = ROOT / "out" / "m4-seeding-rv" / "bank-v2-backup"
JULIET = Path("/tmp/juliet-c/testcases")
OWASP_FILE = Path("/tmp/owasp-bench/BenchmarkTest00001.java")

#: 重生成范围（authz 变异件；SEED-0049 cve_rollback 经对账 KEY-OK，不动）。
TARGETS = list(range(50, 61))


def find_juliet_source(fname: str) -> Path | None:
    hits = list(JULIET.glob(f"*/**/{fname}"))
    return hits[0] if hits else None


def snapshot_origin_filename(item_dir: Path) -> str:
    snap = next((item_dir / "snapshot").iterdir())
    head = snap.read_text(encoding="utf-8", errors="replace")[:800]
    m = re.search(r"Filename:\s*(\S+)", head)
    if not m:
        raise SystemExit(f"[fatal] {item_dir.name} 快照头部无 Filename 注释，无法反查原件")
    return m.group(1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只跑门禁与报告，不落盘")
    args = ap.parse_args()

    if not JULIET.exists():
        raise SystemExit("[fatal] /tmp/juliet-c 不存在（先稀疏克隆镜像）")

    rebuilt: list[dict] = []
    dropped: list[str] = []
    for seq in TARGETS:
        item_id = f"SEED-{seq:04d}"
        item_dir = ITEMS / item_id
        old = json.loads((item_dir / "item.json").read_text(encoding="utf-8"))
        lang, source = old["lang"], old["source"]
        if lang == "java":
            origin_path, fname = OWASP_FILE, "BenchmarkTest00001.java"
        else:
            fname = snapshot_origin_filename(item_dir)
            origin_path = find_juliet_source(fname)
        if origin_path is None or not origin_path.exists():
            dropped.append(f"{item_id}: 原件缺失 {fname}")
            continue
        text = origin_path.read_text(encoding="utf-8", errors="replace")

        mutated = build.mutate_code(text, family="authz", seed=seq, lang=lang)
        reasons = []
        if not build.expected_recheck(mutated, "authz"):
            reasons.append("expected_recheck")
        if not build.syntax_gate(text, mutated, lang):
            reasons.append("syntax_gate")
        if not build.taint_sink_live(mutated, lang):
            reasons.append("taint_sink_live")
        if reasons:
            dropped.append(f"{item_id}: 门禁未过 {'/'.join(reasons)}")
            continue

        # 基线对照（证据面）：新变异的语法错误计数必须 ≤ 原件（syntax_gate 已保证），
        # 此处另报计数供补录引用。
        rebuilt.append({
            "item_id": item_id, "lang": lang, "source": source,
            "origin": str(origin_path.relative_to(origin_path.anchor)) if False else fname,
            "seed": seq,
            "ts_errors_original": build._ts_errors(text, lang),
            "ts_errors_mutated": build._ts_errors(mutated, lang),
        })

        if not args.dry_run:
            if not BACKUP.exists():
                BACKUP.mkdir(parents=True)
                shutil.copy2(ROOT / "seeds" / "bank.yaml", BACKUP / "bank.yaml")
            dst = BACKUP / item_dir.name
            dst.mkdir(parents=True, exist_ok=True)
            for p in item_dir.iterdir():
                if p.is_dir():
                    shutil.rmtree(dst / p.name, ignore_errors=True)
                    shutil.copytree(p, dst / p.name)
                else:
                    shutil.copy2(p, dst / p.name)
            build._write_item(
                ITEMS, seq, family="authz", expected="real", planted=True,
                lang=lang, source=source,
                files={"source.cpp": mutated} if lang != "java" else {"source.java": mutated},
                seed=seq, version=2,
                mutation={"kinds": ["rename", "reorder", "dead_code", "restructure"],
                          "seed": seq},
            )

    print(f"[regen] 门禁通过 {len(rebuilt)}/11：")
    for r in rebuilt:
        print(f"  {r['item_id']}  orig_err={r['ts_errors_original']} "
              f"mut_err={r['ts_errors_mutated']}  seed={r['seed']}  {r['origin']}")
    if dropped:
        print(f"[regen] 弃题 {len(dropped)}：")
        for d in dropped:
            print("  ", d)
    if not args.dry_run and rebuilt:
        meta = json.loads((ROOT / "seeds" / "bank.yaml").read_text(encoding="utf-8"))
        for r in rebuilt:
            for it in meta["items"]:
                if it["item_id"] == r["item_id"]:
                    it["sha256"] = build.dir_sha256(ITEMS / r["item_id"])
        meta["bank_version"] = build.BANK_VERSION
        meta["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (ROOT / "seeds" / "bank.yaml").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        bank = build.load_bank(ROOT / "seeds" / "bank.yaml")
        build.validate_bank(bank)
        print(f"[bank] bank_version={meta['bank_version']} items={len(bank.items)} 校验全绿")
    if dropped:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
