"""kernel.ledger：SQLite append-only 账本 + 内核时钟（SPEC-1 §2/§4.5、§6 一致性规则 4）。

职责与写口纪律：
- 本模块是 ``ledger``/``cells`` 的**唯一写口**（SPEC-1 §6-2：只允许 kernel 包写入，
  测试以静态扫描断言）；蜂与仿真器只能经 ``KernelLedger.append`` 落事件。
- ``seq`` 由 SQLite AUTOINCREMENT 严格单调递增签发；``ts`` 由注入的 ``KernelClock``
  统一签发 UTC ISO-8601 字符串——蜂与仿真世界无时间源。
- append 前先经 ``contracts.PAYLOAD_SCHEMA[kind]`` pydantic 校验；校验失败不落库、
  不消耗 seq。库级 append-only 由 schema.sql 的 ``ledger_no_update`` /
  ``ledger_no_delete`` 触发器兜底（RAISE(ABORT)，内核 bug 也改不动账本）；
  打开账本时校验两触发器在位，缺失即从 schema.sql 幂等补建，补建失败显式报错
  （M0 审计技术债②，SPEC-5 §6.2）。

时钟可注入：生产用 ``WallClock``；仿真/测试用 ``FixedClock``（固定）/``SteppedClock``
（步进），保证时间戳可预期、可复现（SPEC-4 §0：仿真复用内核实现）。

M1 事务扩展（SPEC-2 §7.1、DECISIONS 2026-10-10 D24）：:func:`transaction` 提供
BEGIN IMMEDIATE 显式事务上下文（正常退出 COMMIT、异常 ROLLBACK，可重入计数式
嵌套，最外层边界统一提交/回滚）；上下文内 :meth:`KernelLedger.append` 不再逐条
commit，由最外层边界统一提交——保证「表行 + 账本事件」复合操作（§5 事务边界列）
不存在半写态。无上下文时 append 逐条提交，与 M0 行为逐位一致。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from qswarm.contracts import LedgerKind, PAYLOAD_SCHEMA, schema_sql

if TYPE_CHECKING:  # 仅注解用（避免 ledger↔cells 模块环）
    from .cells import CellView

__all__ = [
    "FixedClock",
    "KernelClock",
    "KernelLedger",
    "LedgerEvent",
    "SteppedClock",
    "WallClock",
    "iso_utc",
    "transaction",
]


# ── 内核时钟（SPEC-1 §6-4：一切 ts 由内核签发，蜂无时间源）──────────

class KernelClock(Protocol):
    """内核时钟接口：仿真/测试注入 Fixed/Stepped，生产注入 Wall。"""

    def now(self) -> datetime:
        """返回当前时刻（必须带时区）。"""
        ...


@lru_cache(maxsize=65536)
def _coerce_utc(ts: str | datetime) -> datetime:
    """字符串/datetime → 带时区 UTC datetime；naive 或非 UTC 输入一律拒绝。

    lru_cache 记忆化：账本 ts 是有限的整秒 ISO 字符串，agg_cells/promote_scan
    的逐日重折叠会反复解析同一批 ts（profile 实测 8 日 run 超 12 万次解析）；
    返回值 datetime 不可变、异常不缓存，命中与否语义逐位一致。
    """
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            raise ValueError("内核时钟不接受 naive datetime（必须可判定 UTC 时刻）")
        return ts.astimezone(timezone.utc)
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        raise ValueError(f"时钟字符串必须带 UTC 偏移或 Z 后缀：{ts!r}")
    return dt.astimezone(timezone.utc)


def iso_utc(dt: datetime) -> str:
    """内核统一时间戳格式：UTC ISO-8601，整秒省略小数，非整秒带 6 位微秒。"""
    if dt.tzinfo is None:
        raise ValueError("内核时钟不接受 naive datetime（必须可判定 UTC 时刻）")
    dt = dt.astimezone(timezone.utc)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class WallClock:
    """生产时钟：真实 UTC 系统时间。"""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """仿真/测试时钟：永远返回同一时刻（时间戳完全可预期）。"""

    def __init__(self, ts: str | datetime) -> None:
        self._dt = _coerce_utc(ts)

    def now(self) -> datetime:
        return self._dt


class SteppedClock:
    """仿真/测试时钟：每次 now() 前进固定步长，供序列化时间轴用。"""

    def __init__(self, start: str | datetime, step_s: float = 1.0) -> None:
        self._dt = _coerce_utc(start)
        self._step = timedelta(seconds=step_s)

    def now(self) -> datetime:
        current, self._dt = self._dt, self._dt + self._step
        return current


# ── 账本事件（读取侧的稳定结构）────────────────────────────────

@dataclass(frozen=True)
class LedgerEvent:
    """账本一行：seq/ts/kind/run_id + 已解析的 payload dict（append/read 共用）。"""

    seq: int
    ts: str
    kind: LedgerKind
    run_id: str | None
    payload: dict


# ── append-only 防御（SPEC-1 §2；触发器 DDL 唯一真源是 contracts/schema.sql）──

APPEND_ONLY_TRIGGERS = ("ledger_no_update", "ledger_no_delete")

#: ``PRAGMA synchronous`` 合法模式（SQLite 文档口径）；缺省 None = 不触碰（FULL）。
_SYNCHRONOUS_MODES = ("OFF", "NORMAL", "FULL", "EXTRA")


def _trigger_statements(script: str) -> list[str]:
    """从 DDL 脚本按原文抽取完整 ``CREATE TRIGGER`` 语句（剥 ``--`` 注释行）。

    用 :func:`sqlite3.complete_statement` 切分——它感知 ``BEGIN...END`` 触发器体，
    不会把体内的 ``;`` 误判成语句边界，故补建 DDL 与建库 DDL 永远同源（无副本漂移）。
    """
    statements: list[str] = []
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if not sqlite3.complete_statement(buf):
            continue
        code = "\n".join(
            ln for ln in buf.splitlines() if not ln.lstrip().startswith("--")
        ).strip()
        if code.upper().startswith("CREATE TRIGGER"):
            statements.append(code)
        buf = ""
    return statements


# ── 唯一写口 ──────────────────────────────────────────────────

class KernelLedger:
    """append-only 账本句柄：建库、校验后落行、按 seq/kind 读取、重放入口。

    打开即初始化：连接设 ``PRAGMA journal_mode=WAL`` + ``PRAGMA foreign_keys=ON``。
    ``synchronous`` 可选放宽 fsync 持久化（``"OFF"``/``"NORMAL"/…``，缺省 None =
    SQLite 缺省 FULL）：只影响 OS 崩溃/断电时已提交事务的存活性，不改变任何
    进程内可见数据；仅供「用后即删」的一次性账本（如仿真临时账本）使用，
    生产与用户保留的账本一律走缺省。

    schema 在位性 = ``ledger`` 表与两条 append-only 触发器**齐备**：缺表时按依赖序
    ``executescript(contracts.schema_sql())``（DDL 逐字来自 SPEC-1 §2，无
    IF NOT EXISTS，重复打开同一库幂等）；表在而触发器缺（旧版本/外部预建）时
    只幂等补建所缺触发器（DDL 仍取自 schema.sql，单一真源），补建失败抛
    ``RuntimeError`` 拒绝打开，不让账本在无 append-only 防御下静默可用（SPEC-5 §6.2）。
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock: KernelClock | None = None,
        synchronous: str | None = None,
    ) -> None:
        self._clock: KernelClock = clock if clock is not None else WallClock()
        self.conn: sqlite3.Connection = sqlite3.connect(str(db_path))
        # 两条 PRAGMA 每次连接都要设：WAL 是库级持久属性，foreign_keys 是连接级。
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        if synchronous is not None:
            # 连接级 PRAGMA，显式白名单防拼写错误静默失效（SQLite 对非法值报错，
            # 但报错发生在 execute 而非构造意图处，这里前置校验给出可读信息）。
            if synchronous not in _SYNCHRONOUS_MODES:
                raise ValueError(
                    f"非法 synchronous 模式：{synchronous!r}（合法值：{_SYNCHRONOUS_MODES}）"
                )
            self.conn.execute(f"PRAGMA synchronous = {synchronous}")
        # 已读行的 payload 解析缓存：账本 append-only（库级触发器兜底），已落行
        # 的 payload_json 永不改变，按 seq 缓存解析结果严格正确；agg_cells 每个仿
        # 真日全量重读账本（8 日 run 实测 12.9 万次 json.loads），命中后只解析一次。
        # 缓存随连接存活、close() 释放；返回的 payload dict 为共享对象，调用方
        # 不得原地修改（append-only 账本语义下本就不该改）。
        self._payload_cache: dict[int, dict] = {}
        # transaction() 的计数式嵌套深度（D24）：0 = 无上下文（append 逐条提交）。
        self._txn_depth: int = 0
        if not self._schema_present():
            self._init_schema()
        self.conn.commit()

    def _schema_present(self) -> bool:
        """schema 在位 = ``ledger`` 表与两条 append-only 触发器齐备（缺一即走初始化）。"""
        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger'"
        ).fetchone() is None:
            return False
        return self._present_triggers() == set(APPEND_ONLY_TRIGGERS)

    def _present_triggers(self) -> set[str]:
        """库内实际在位的 append-only 触发器名集合。"""
        return {
            name
            for (name,) in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name IN (?, ?)",
                APPEND_ONLY_TRIGGERS,
            )
        }

    def _init_schema(self) -> None:
        """补齐缺失 schema，两条路径与既有行为兼容（DECISIONS 2026-10-10 技术债②）：

        - 缺 ``ledger`` 表 → 全量建库 ``executescript(schema_sql())``（原路径不动）；
        - 表在而触发器缺 → 只补执行 ``schema_sql()`` 中所缺的 ``CREATE TRIGGER``
          （幂等：重开零改动、单缺只补缺者）；补建失败（库只读、DDL 损坏等）抛
          ``RuntimeError``，绝不静默降级为无 append-only 防御的账本。
        """
        if self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ledger'"
        ).fetchone() is None:
            self.conn.executescript(schema_sql())
            return
        missing = [n for n in APPEND_ONLY_TRIGGERS if n not in self._present_triggers()]
        if not missing:
            return
        ddl = {stmt.split()[2]: stmt for stmt in _trigger_statements(schema_sql())}
        absent = [n for n in missing if n not in ddl]
        if absent:
            raise RuntimeError(
                f"contracts/schema.sql 未定义 append-only 触发器 {absent}"
                "（schema 漂移，拒绝打开账本）"
            )
        for name in missing:
            try:
                self.conn.execute(ddl[name])
            except sqlite3.Error as exc:
                raise RuntimeError(
                    f"append-only 触发器 {name!r} 缺失且补建失败，拒绝在无防御下打开账本：{exc}"
                ) from exc

    # -- 写（唯一写口）─────────────────────────────────────────

    def append(
        self,
        kind: LedgerKind | str,
        payload: dict | BaseModel,
        *,
        run_id: str | None = None,
    ) -> LedgerEvent:
        """校验并追加一条账本事件，返回落库后的 LedgerEvent（含内核签发的 seq/ts）。

        - kind 必须是 contracts.LedgerKind 成员，payload 按 PAYLOAD_SCHEMA[kind]
          pydantic 校验；ValidationError/ValueError 直接上抛，**不落库、不消耗 seq**。
        - ts 取自注入时钟（UTC ISO-8601），seq 由 AUTOINCREMENT 严格单调递增。
        - 事务语义（SPEC-2 §7.1/D24）：进入时连接已在事务中（:func:`transaction`
          上下文内）→ 不逐条 commit，由最外层上下文边界统一提交/回滚；否则逐条
          commit，与 M0 行为逐位一致。
        """
        try:
            kind_enum = LedgerKind(kind)
        except ValueError:
            raise ValueError(f"未知账本事件 kind：{kind!r}（合法值见 contracts.LedgerKind）") from None
        model = PAYLOAD_SCHEMA[kind_enum]
        raw = payload.model_dump() if isinstance(payload, BaseModel) else payload
        validated = model.model_validate(raw)  # 失败即上抛 ValidationError，先于任何写库
        ts = iso_utc(self._clock.now())
        in_txn = self.conn.in_transaction
        cur = self.conn.execute(
            "INSERT INTO ledger (ts, kind, run_id, payload_json) VALUES (?, ?, ?, ?)",
            (ts, kind_enum.value, run_id, validated.model_dump_json()),
        )
        if not in_txn:
            self.conn.commit()
        return LedgerEvent(
            seq=int(cur.lastrowid),
            ts=ts,
            kind=kind_enum,
            run_id=run_id,
            payload=validated.model_dump(),
        )

    # -- 读（任意模块可读，无纪律限制）──────────────────────────

    def read(
        self,
        *,
        kinds: Iterable[LedgerKind | str] | None = None,
        from_seq: int | None = None,
        to_seq: int | None = None,
    ) -> list[LedgerEvent]:
        """按 kind 白名单与 seq 闭区间 [from_seq, to_seq] 过滤，按 seq 升序返回。

        kinds=None 不过滤；kinds 为空集合直接返回空列表（SQL 语义：IN () 不匹配）。
        """
        where: list[str] = []
        args: list[object] = []
        if kinds is not None:
            values = [LedgerKind(k).value for k in kinds]
            if not values:
                return []
            where.append(f"kind IN ({', '.join('?' * len(values))})")
            args.extend(values)
        if from_seq is not None:
            where.append("seq >= ?")
            args.append(from_seq)
        if to_seq is not None:
            where.append("seq <= ?")
            args.append(to_seq)
        sql = "SELECT seq, ts, kind, run_id, payload_json FROM ledger"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq"
        rows = self.conn.execute(sql, args).fetchall()
        out: list[LedgerEvent] = []
        cache = self._payload_cache
        for seq, ts, kind, run_id, payload_json in rows:
            payload = cache.get(seq)
            if payload is None:
                payload = json.loads(payload_json)
                cache[seq] = payload
            out.append(
                LedgerEvent(
                    seq=seq,
                    ts=ts,
                    kind=LedgerKind(kind),
                    run_id=run_id,
                    payload=payload,
                )
            )
        return out

    def latest_seq(self) -> int:
        """当前最大 seq；空账本返回 0（与 `replay --from 0` 的起点语义一致）。"""
        (seq,) = self.conn.execute("SELECT COALESCE(MAX(seq), 0) FROM ledger").fetchone()
        return int(seq)

    # -- 重放入口（实现归 kernel.replay）────────────────────────

    def rebuild(self, *, from_seq: int = 0, upto_seq: int | None = None) -> "CellView":
        """重放重建入口：重放窗口 ``[from_seq, upto_seq]`` 的事件，全量重建 cells
        （SPEC-1 §2 重放重建规则；``--from 0`` 即全量重建）。

        委托 :func:`kernel.replay.rebuild_cells`（复用 ``agg_cells`` 单一聚合实现，
        局部导入避免模块环）；``clusters.verdict_*`` / ``findings.state`` 的重放
        随 SPEC-2 状态机落地后接入同一入口。
        """
        from .replay import rebuild_cells  # 局部导入：ledger↔replay 无模块环

        return rebuild_cells(self, upto_seq, from_seq=from_seq)

    # -- 生命周期 ─────────────────────────────────────────────

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "KernelLedger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ── 多写原子上下文（SPEC-2 §7.1；DECISIONS 2026-10-10 D24）──────────


@contextmanager
def transaction(ledger: KernelLedger) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE 显式事务上下文：正常退出 COMMIT、异常 ROLLBACK。

    - **可重入（计数式嵌套，D24）**：已在事务中时内层不再 BEGIN/COMMIT，仅登记
      上下文（深度 +1），由最外层边界统一 COMMIT/ROLLBACK——内核复合函数
      （submit/apply_merges/propagate_verdict/on_cost 等）内部自开事务，被外层
      批处理上下文包裹时沿用外层边界，嵌套调用零特判；
    - **append 语义修订**：上下文内连接处于事务中，:meth:`KernelLedger.append`
      不再逐条 commit（见其 docstring）——「表行 + 账本事件」复合操作在单一
      BEGIN IMMEDIATE 边界内原子生效，异常时整体 ROLLBACK（§4.5 纪律 1
      「不存在半写态」）；
    - 无上下文时全部写路径行为与 M0 逐位一致（append 逐条提交、
      ``cells._write_cells`` 自带 commit 均不受影响）；
    - yield 出底层连接，便于上下文内做同事务的表行读写。
    """
    depth = ledger._txn_depth
    if depth == 0:
        ledger.conn.execute("BEGIN IMMEDIATE")
    ledger._txn_depth = depth + 1
    try:
        yield ledger.conn
    except BaseException:
        ledger._txn_depth -= 1
        if ledger._txn_depth == 0:
            ledger.conn.rollback()
        raise
    else:
        ledger._txn_depth -= 1
        if ledger._txn_depth == 0:
            ledger.conn.commit()
