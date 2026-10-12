"""kernel.write_guard：读写并发纪律护栏与写口审计（SPEC-2 §4.5/§6.1/§7）。

§4.5 并发三纪律的机器化落地，三件套（单一实现：pytest 写口纪律测试与终检脚本
``scripts/audit_write_ports.py`` 共用本模块，不复制第二套正则）：

1. **写口静态审计** :func:`audit_write_ports`——按 SPEC-2 §6.1 全词表（M2 扩展后
   15 张写表：内核 11 表 + 考试域 4 表，:data:`WRITE_TABLES`）以
   :data:`WRITE_STMT_RE` 扫描 qswarm 包全部 ``*.py``：

   - 圣化区域外的写语句命中即**违规**（内核 11 表圣化区域 = ``kernel/``；M2 考试域
     四表 = ``exams/``。判据与 tests 写口纪律测试、SPEC-5 §2.1 判据 2 的独立 grep
     完全同口径：违规清单为空 = 通过）；
   - 圣化区域内的命中列成**写口清单**，对照 :data:`EXPECTED_WRITE_PORTS`
     （§6.1「表 → 唯一写口模块」逐行编码）报告清单漂移（``--strict`` 时计入失败）：
     写语句必须落在该表的圣化写口模块内，防止包内长出第二写口。

2. **读侧运行时护栏** :func:`read_only_guard`——以 SQLite 连接级
   ``PRAGMA query_only`` 把一段会话钉成只读：护栏内对任何表的写（直连 SQL、
   预编译缓存语句、DDL、:meth:`KernelLedger.append`、:func:`kernel.cells.agg_cells`
   整表重写）一律 ``sqlite3.OperationalError("attempt to write a readonly
   database")``；读（SELECT、:func:`kernel.cells.view_cells` 只读重放投影）不受
   影响——「读侧会话物理上不可写、写只能经内核写口会话」由此从约定升级为机制
   （§4.5 纪律 1/2、§7.2）。实施要点：

   - 执行期强制而非仅准备期：预编译语句缓存不构成绕过（实测同 SQL 串护栏内外
     复执行仍被拒）；
   - 可重入：进入时读回 ``query_only`` 现值，仅由把 0 翻成 1 的那层在退出时恢复
     原值（嵌套时内层零操作，语义对齐 ledger.transaction 的 D24 计数式纪律）；
   - 缺省不装护栏 = 行为与 M0 逐位一致；护栏是**进程内纪律护栏**（同连接可自行
     解除），不是跨进程安全边界——跨进程只读请用 SQLite ``mode=ro`` URI；
   - generator 被遗弃未退出时连接保持只读：护栏只朝 fail-closed 方向失效。

3. **CLI** ``python -m qswarm.kernel.write_guard [--root PATH] [--strict] [--json]``：
   一键审计；退出码 0=通过 / 1=存在 kernel/ 外违规 / 2=仅 --strict 清单漂移。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .ledger import KernelLedger

__all__ = [
    "EXPECTED_WRITE_PORTS",
    "KERNEL_PREFIX",
    "WRITE_STMT_RE",
    "WRITE_TABLES",
    "WritePortAudit",
    "WritePortHit",
    "audit_write_ports",
    "main",
    "read_only_guard",
    "scan_write_ports",
]


# ── 写口词表与正则（SPEC-2 §6.1 唯一写口表；SPEC-5 §2.1 判据 2 同一口径）────

#: SPEC-2 §6.1 全部写表（穷尽枚举；M2 扩展：agents/hats/exam_items/exam_results
#: 四表唯一写口 = qswarm/exams，DECISIONS 2026-10-11 写口表扩展行）。
WRITE_TABLES: tuple[str, ...] = (
    "ledger",
    "cells",
    "findings",
    "evidence_nodes",
    "votes",
    "verify_results",
    "clusters",
    "cluster_members",
    "tasks",
    "candidates",
    "runs",
    "agents",
    "hats",
    "exam_items",
    "exam_results",
)

#: kernel/ 包内前缀：内核 11 表的写语句只允许落在该前缀下（蜂与仿真器只能读或经
#: 写口落事件）。
KERNEL_PREFIX = "kernel/"

#: exams/ 包内前缀：agents/hats/exam_items/exam_results 四表（考试与编制子域）的
#: 写语句只允许落在该前缀下（SPEC-6 §0.2 写口纪律扩展）。
EXAMS_PREFIX = "exams/"

#: 表 → 唯一写口模块（SPEC-2 §6.1 表逐行编码；包根相对 posix 路径）。
#: runs 行的「run 生命周期入口」按 §2.3/D19 取 dispatch（expire_run 等收尾口）。
#: M2 四表（SPEC-6 §0.2/S4）：roster.py 注册 agents/hats，store.py 承担
#: exam_items UPSERT-by-version 与 exam_results 行签发。
EXPECTED_WRITE_PORTS: dict[str, tuple[str, ...]] = {
    "ledger": ("kernel/ledger.py",),
    "cells": ("kernel/cells.py",),
    "findings": ("kernel/dispatch.py",),
    "evidence_nodes": ("kernel/dispatch.py",),
    "votes": ("kernel/dispatch.py",),
    "verify_results": ("kernel/dispatch.py",),
    "clusters": ("kernel/cluster.py",),
    "cluster_members": ("kernel/cluster.py",),
    "tasks": ("kernel/dispatch.py",),
    "candidates": ("kernel/dispatch.py",),
    "runs": ("kernel/budget.py", "kernel/dispatch.py"),
    "agents": ("exams/roster.py",),
    "hats": ("exams/roster.py",),
    "exam_items": ("exams/store.py",),
    "exam_results": ("exams/store.py",),
}

#: 表 → 圣化前缀（该表写语句的合法包内区域）：内核 11 表 = kernel/，M2 考试域
#: 四表 = exams/。区域内但非圣化模块 → mismatch（--strict 失败）；区域外 →
#: violation（恒失败）。对内核 11 表与 M1 行为逐位同判。
_SANCTIFIED_PREFIX: dict[str, str] = {
    table: (
        EXAMS_PREFIX
        if table in ("agents", "hats", "exam_items", "exam_results")
        else KERNEL_PREFIX
    )
    for table in WRITE_TABLES
}

#: 写语句判定正则：动词词表与 SPEC-5 §2.1 判据 2 的 grep -E 逐字对齐
#: （INSERT INTO | REPLACE INTO | UPDATE | DELETE FROM，\\s+ 容任意空白，
#: 词表全词匹配带 \\b 边界；IGNORECASE 承接 tests 既有写口纪律测试口径）。
WRITE_STMT_RE = re.compile(
    r"\b(?P<verb>INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)"
    rf"\s+(?P<table>{'|'.join(WRITE_TABLES)})\b",
    re.IGNORECASE,
)


# ── 静态审计 ─────────────────────────────────────────────────

@dataclass(frozen=True, order=True)
class WritePortHit:
    """一处写语句命中（一行内可有多处，逐处展开）。"""

    rel_path: str          # 包根相对 posix 路径，如 kernel/cells.py
    lineno: int            # 1 起行号
    verb: str              # 规范大写动词短语，如 "INSERT INTO"
    table: str             # 小写表名
    line: str              # 原行文本（strip 后，供人工复核）

    @property
    def location(self) -> str:
        return f"{self.rel_path}:{self.lineno}"

    @property
    def stmt(self) -> str:
        return f"{self.verb} {self.table}"


def _iter_py_files(pkg_root: Path) -> Iterator[Path]:
    """包根下全部 .py（跳过 __pycache__；排序保证输出确定）。"""
    for path in sorted(pkg_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def scan_write_ports(pkg_root: Path) -> list[WritePortHit]:
    """扫描包根下全部 .py 的写语句命中，按 (路径, 行号, 语句) 确定序返回。"""
    hits: list[WritePortHit] = []
    for path in _iter_py_files(pkg_root):
        rel = path.relative_to(pkg_root).as_posix()
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for m in WRITE_STMT_RE.finditer(line):
                hits.append(
                    WritePortHit(
                        rel_path=rel,
                        lineno=lineno,
                        verb=" ".join(m.group("verb").split()).upper(),
                        table=m.group("table").lower(),
                        line=line.strip(),
                    )
                )
    hits.sort()
    return hits


@dataclass(frozen=True)
class WritePortAudit:
    """一次写口审计的完整结果（三份清单互不重叠、均为确定序）。"""

    scanned_files: int              # 实际扫描的 .py 文件数
    inventory: list[WritePortHit]   # 圣化写口模块内命中 = 写口清单
    violations: list[WritePortHit]  # 圣化区域外命中 = 越界写口（恒应为空）
    mismatches: list[WritePortHit]  # 圣化区域内但非该表圣化模块（--strict 失败项）

    @property
    def ok(self) -> bool:
        """非 strict 通过判据：圣化区域外零命中（SPEC-5 §2.1 判据 2 的空输出）。"""
        return not self.violations

    def strict_ok(self, *, strict: bool) -> bool:
        """--strict 时额外要求写口清单与 §6.1 圣化模块零漂移。"""
        return not self.mismatches if strict else True

    def exit_code(self, *, strict: bool) -> int:
        """CLI 退出码：0 通过 / 1 越界违规 / 2 仅 strict 清单漂移。"""
        if self.violations:
            return 1
        if strict and self.mismatches:
            return 2
        return 0


def audit_write_ports(pkg_root: Path) -> WritePortAudit:
    """按 §6.1 词表审计包根：违规（圣化区域外）、写口清单（圣化模块内）、漂移。

    分类三段（M2 扩展后按表分区，内核 11 表与 M1 行为逐位同判）：

    - inventory：写语句落在该表圣化写口模块内（唯一写口在位）；
    - mismatches：落在圣化区域（内核 11 表 kernel/ · 考试域四表 exams/）内但
      非该表圣化模块——仅 --strict 计失败（内核/考试域内部不许长出第二写口）；
    - violations：落在圣化区域外——恒失败（含：考试域四表在 kernel/ 内的写、
      内核 11 表在 kernel/ 外的写）。
    """
    hits = scan_write_ports(pkg_root)
    inventory: list[WritePortHit] = []
    mismatches: list[WritePortHit] = []
    violations: list[WritePortHit] = []
    for h in hits:
        if h.rel_path in EXPECTED_WRITE_PORTS.get(h.table, ()):
            inventory.append(h)
        elif h.rel_path.startswith(_SANCTIFIED_PREFIX[h.table]):
            mismatches.append(h)
        else:
            violations.append(h)
    scanned = sum(1 for _ in _iter_py_files(pkg_root))
    return WritePortAudit(
        scanned_files=scanned,
        inventory=inventory,
        violations=violations,
        mismatches=mismatches,
    )


# ── 读侧运行时护栏（PRAGMA query_only；§4.5 纪律 1/2 的机制化）────────


@contextmanager
def read_only_guard(target: KernelLedger | sqlite3.Connection):
    """把一段会话钉成只读：护栏内任何写语句一律 OperationalError，读不受影响。

    - ``target`` 接受 :class:`KernelLedger` 或裸 ``sqlite3.Connection``；
    - 可重入：进入时读回 ``query_only`` 现值，仅由把 0 翻成 1 的那一层在退出时
      恢复原值（嵌套时内层零操作；异常路径同样恢复，fail-closed：护栏未正常
      退出时连接保持只读）；
    - yield 出底层连接，便于在护栏内做只读取证（SELECT / view_cells 投影）。
    """
    conn: sqlite3.Connection = (
        target.conn if isinstance(target, KernelLedger) else target
    )
    row = conn.execute("PRAGMA query_only").fetchone()
    prior = int(row[0]) if row and row[0] is not None else 0
    flipped = False
    if prior == 0:
        conn.execute("PRAGMA query_only = 1")
        flipped = True
    try:
        yield conn
    finally:
        if flipped:
            conn.execute(f"PRAGMA query_only = {prior}")


# ── CLI：一键写口审计（终检复用；scripts/audit_write_ports.py 薄封装）──

def _print_report(audit: WritePortAudit, root: Path, *, strict: bool) -> None:
    verbs = "INSERT INTO / REPLACE INTO / UPDATE / DELETE FROM"
    print("写口审计（SPEC-2 §6.1/§7 · SPEC-5 §2.1 判据 2 同口径）")
    print(f"  包根   : {root}")
    print(
        f"  扫描   : {audit.scanned_files} 个 .py · 词表 {len(WRITE_TABLES)} 表 · 动词 {verbs}"
    )
    print(f"  写口清单（kernel/ 内 {len(audit.inventory)} 条，期望写口见 §6.1 表）:")
    for h in audit.inventory:
        expected = EXPECTED_WRITE_PORTS.get(h.table, ())
        mark = "✓" if h.rel_path in expected else "✗漂移"
        print(
            f"    PORT {h.location}  {h.stmt}"
            f"（期望: {','.join(expected) or '未登记'} {mark}）"
        )
    if audit.violations:
        print(f"  违规（kernel/ 外写口，{len(audit.violations)} 条）:")
        for h in audit.violations:
            print(f"    VIOLATION {h.location}  {h.stmt}  # {h.line}")
    else:
        print("  违规（kernel/ 外写口）: 0 条")
    if strict:
        if audit.mismatches:
            print(f"  [strict] 清单漂移（写语句落在非圣化模块，{len(audit.mismatches)} 条）:")
            for h in audit.mismatches:
                expected = ",".join(EXPECTED_WRITE_PORTS.get(h.table, ()) or ("未登记",))
                print(f"    DRIFT {h.location}  {h.stmt}（期望写口: {expected}）")
        else:
            print("  [strict] 清单漂移: 0 条")
    if audit.exit_code(strict=strict) == 0:
        print("WRITE-PORT AUDIT PASS")
    else:
        print(
            "WRITE-PORT AUDIT FAIL"
            f" violations={len(audit.violations)} mismatches={len(audit.mismatches)}"
        )


def main(argv: list[str] | None = None) -> int:
    """``python -m qswarm.kernel.write_guard``：对 qswarm 包做一键写口审计。"""
    import qswarm

    parser = argparse.ArgumentParser(
        prog="python -m qswarm.kernel.write_guard",
        description="写口审计：kernel/ 外写语句即违规；--strict 另查 §6.1 圣化模块漂移",
    )
    parser.add_argument(
        "--root", default=None,
        help="qswarm 包根目录（缺省=当前导入的 qswarm 包所在目录）",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="把 kernel/ 内写口清单与 §6.1 期望写口模块的漂移也记为失败",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出审计结果")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(qswarm.__file__).parent
    audit = audit_write_ports(root)

    if args.json:

        def hit_json(h: WritePortHit) -> dict:
            return {
                "path": h.rel_path,
                "line": h.lineno,
                "verb": h.verb,
                "table": h.table,
                "stmt": h.stmt,
            }

        print(
            json.dumps(
                {
                    "root": str(root),
                    "scanned_files": audit.scanned_files,
                    "ok": audit.ok,
                    "strict": args.strict,
                    "strict_ok": audit.strict_ok(strict=args.strict),
                    "exit_code": audit.exit_code(strict=args.strict),
                    "violations": [hit_json(h) for h in audit.violations],
                    "inventory": [hit_json(h) for h in audit.inventory],
                    "mismatches": [hit_json(h) for h in audit.mismatches],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print_report(audit, root, strict=args.strict)
    return audit.exit_code(strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
