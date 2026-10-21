"""perception.channels：L0 三通道候选收集（SPEC-7 §1.2–§1.4）。

- **通道 A（结构）**：indexer 索引 × clues 表 → source/sink/dangerous_call/
  serialization 候选；无 grammar 语言缺席（indexer.parse_ok=False 显式承载，
  调用方计数 ``channel_a_absent``）；
- **通道 B（模型直扫）**：分片 → DispatchContract(role=scout, task_id=
  ``l0scan:<shard_id>``)（D26 侦察蜂兼任）→ 供给面调用 → 候选列表解析；
  解析失败重试 1 次仍败 → **该分片通道 B 缺席**（记 ``model_channel_fail`` +
  归属蜂 id，不抛错——候选收集尽力而为）；**零存款**（无对错语义，D26——
  本模块零账本触达，审计锚 = 工件 producer 字段 + 扫描报告计数）；
- **通道 C（规则）**：semgrep（D30 必选件）→ rule_hit 候选；运行期单次超时/
  故障 → 该轮缺席并计 ``rule_channel_skipped``（红线：规则永不卡点，漏斗
  永不因规则通道停摆）；**命中仅提升候选优先级，不是准入条件**。

本模块是 perception 的 IO 边缘（供给面调用 / semgrep 子进程）；归并纯度纪律
不外溢至此。rules_runner / 均可注入（测试离线，同 adapter.opener 形态）。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import DispatchContract, Role
from qswarm.kernel.dispatch import ROLE_TOOL_BOUNDS
from qswarm.prompts import EXAM_TEMPERATURE, SchemaRejected, extract_json

from .candidates import CANDIDATE_KINDS, Candidate
from .indexer import IndexEntry
from .prompts import L0Block, render_l0_scan

__all__ = [
    "L0_CHANNEL_PREFIX",
    "L0_DEADLINE_S",
    "L0_TASK_BOUNDS",
    "build_l0_contract",
    "ChannelBFailure",
    "RulesOutcome",
    "Shard",
    "build_model_shards",
    "channel_a_candidates",
    "channel_b_scan",
    "channel_c_scan",
    "default_rules_runner",
    "parse_channel_b_response",
]

#: 通道 B 任务 id 前缀（D26：与 L1 任务 ``l1:`` 严格区分，防两类工时混账）。
L0_CHANNEL_PREFIX = "l0scan:"

#: 通道 B 调用 deadline（任务头行渲染值；与考试面 EXAM_DEADLINE_S 同量级的
#: 模块常量，不进 params——extra=forbid 纪律）。
L0_DEADLINE_S = 600

#: 通道 B 任务边界（④ 段：直扫语义，与 scout 生产任务边界刻意区分）。
L0_TASK_BOUNDS = "只做结构级扫描与候选收集；不做深挖、不下漏洞结论、不产 finding。"

#: 模型候选 kind 词表（rule_hit 为通道 C 专属；模型产出该 kind 视为越表丢弃）。
_MODEL_KINDS = CANDIDATE_KINDS - {"rule_hit"}


def build_l0_contract(shard: Shard) -> DispatchContract:
    """通道 B 派单契约装配（§1.3 逐字四字段：role=scout、task_id=`l0scan:<shard>`、
    goal=「L0 直扫：结构候选收集」）。

    - **直接构造 DispatchContract** 而非 build_contract：后者的 ② output_schema_ref
      硬编码 ROLE_OUTPUT_SCHEMA[role]="ScoutReport"，而 L0 直扫产出是候选列表
      （工件 kind=l0_candidates 自描述），角色表硬编码不适用（DECISIONS M3-1 修订
      口径）；契约只承载派单语义（渲染头 + 审计锚），不入 tasks 状态机（M3-1）；
    - tool_bounds 沿 scout 权限边界（repo:ro）；task_bounds = L0 直扫专属措辞。"""
    return DispatchContract(
        task_id=f"{L0_CHANNEL_PREFIX}{shard.shard_id}",
        role=Role.SCOUT,
        goal="L0 直扫：结构候选收集",
        output_schema_ref="l0_candidates",
        tool_bounds=list(ROLE_TOOL_BOUNDS["scout"]),
        task_bounds=L0_TASK_BOUNDS,
        context_refs=[b.ref for b in shard.blocks],
        taxonomy_node="",
        hop_budget=0,
        deadline_s=L0_DEADLINE_S,
    )


# ── 通道 A：结构（index × clues）─────────────────────────────────

def channel_a_candidates(entries: list[IndexEntry]) -> list[Candidate]:
    """索引条目 → 通道 A 候选（未归并；纯函数）。

    - 调用点命中：kind 按线索档（sink/dangerous_call/serialization），span =
      调用行单行闭区间，clue = 被调名；
    - 标识符命中：kind ∈ {source, dangerous_call, serialization}（indexer 已滤
      sink 档防同位双计），clue = 标识符名；
    - ``parse_ok=False``（无 grammar/解析失败）→ 零产出（缺席语义，调用方对
      该语言计数 ``channel_a_absent``）；
    - 同一 (family, kind) 同名多点 → 各出一候选（归并层折叠）；taxonomy_hint =
      命中家族（多家族命中取字典序首个——hint 仅供 L1 参考，不约束）。"""
    out: list[Candidate] = []
    for entry in entries:
        if not entry.parse_ok:
            continue
        for name, line, hits in (*entry.calls, *entry.idents):
            for family, kind in hits:
                out.append(
                    Candidate(
                        path=entry.path,
                        span=(line, line),
                        kind=kind,
                        clue=name,
                        channels=frozenset({"A"}),
                        taxonomy_hint=family,
                    )
                )
    return out


# ── 通道 B：模型直扫（D26 侦察蜂兼任）─────────────────────────────

@dataclass(frozen=True)
class Shard:
    """通道 B 单分片（≤ max_file_lines 行；函数边界切分由本模块构造）。"""

    shard_id: str                      # l0scan:<shard_id> 的 <shard_id> 段
    blocks: tuple[L0Block, ...]


@dataclass(frozen=True)
class ChannelBFailure:
    """分片通道 B 缺席记录（重试 1 次仍败；进扫描报告，不抛错）。"""

    shard_id: str
    agent_id: str
    attempts: int
    reason: str                        # SchemaRejected.reason / adapter 错误摘要


@dataclass
class ChannelBOutcome:
    """通道 B 结果：候选（未归并）+ 缺席记录。"""

    candidates: list[Candidate] = field(default_factory=list)
    failures: list[ChannelBFailure] = field(default_factory=list)
    shard_count: int = 0


def _split_span(start: int, end: int, max_len: int) -> list[tuple[int, int]]:
    """[start, end] 按 ≤ max_len 行硬切（1-based 闭区间）。"""
    out: list[tuple[int, int]] = []
    cur = start
    while cur <= end:
        stop = min(cur + max_len - 1, end)
        out.append((cur, stop))
        cur = stop + 1
    return out


def build_model_shards(
    files: list[tuple[str, str]],
    *,
    max_file_lines: int,
    defs_by_path: dict[str, list[tuple[str, int, int]]] | None = None,
) -> list[Shard]:
    """文件集 → 通道 B 分片（§1.3：单文件 ≤ 上限整文件；超长按函数边界切 ≤ 上限片）。

    - 文件序 = 传入序（调用方保证字典序）；片内内容 = 原文行切片（1-based）；
    - 函数边界：defs_by_path 提供该文件 (name, start, end)——函数不跨片贪心装箱
      （单函数超上限 → 自行硬切成 ≤ 上限片）；函数间空隙就近并入（不超上限）；
      无 defs 的超长文件按行硬切（索引不可得时的兜底，语言无关）；
    - shard_id = f"{path}:{start}-{end}"（确定性）。"""
    defs_by_path = defs_by_path or {}
    shards: list[Shard] = []
    for path, text in files:
        lines = text.splitlines()
        total = len(lines)
        if total <= max_file_lines:
            ref = f"{path}:1-{total}"
            shards.append(Shard(shard_id=ref, blocks=(L0Block(ref=ref, content=text),)))
            continue
        segments: list[tuple[int, int]] = []
        prev = 1
        for _name, fstart, fend in sorted(defs_by_path.get(path, ()), key=lambda d: (d[1], d[2])):
            lo, hi = max(1, fstart), min(total, fend)
            if hi < lo:
                continue
            if lo > prev:
                segments.extend(_split_span(prev, lo - 1, max_file_lines))
            segments.extend(_split_span(lo, hi, max_file_lines))
            prev = max(prev, hi + 1)
        if prev <= total:
            segments.extend(_split_span(prev, total, max_file_lines))
        packed: list[tuple[int, int]] = []
        for a, b in segments:
            if packed and b - packed[-1][0] + 1 <= max_file_lines:
                packed[-1] = (packed[-1][0], b)      # 就近并入前片（不超上限）
            else:
                packed.append((a, b))
        for a, b in packed:
            content = "\n".join(lines[a - 1:b])
            ref = f"{path}:{a}-{b}"
            shards.append(Shard(shard_id=ref, blocks=(L0Block(ref=ref, content=content),)))
    return shards


def parse_channel_b_response(raw: str, *, default_path: str | None = None) -> list[dict]:
    """模型响应 → 候选条目 dict 列表（§1.3 输出契约；畸形 → SchemaRejected）。

    - extract_json 两段式提取；容器非对象或缺 candidates 数组 → schema 失败；
    - 条目级畸形（缺字段/kind 越表/rule_hit 专属/行号非法）→ **丢弃该条目**
      （不入候选，容器仍成立——分片级失败只留给结构级畸形）。"""
    text = extract_json(raw)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise SchemaRejected("schema:L0Scan:container")
    out: list[dict] = []
    for it in data["candidates"]:
        if not isinstance(it, dict):
            continue
        path = it.get("path") or default_path
        try:
            start, end = int(it.get("start_line")), int(it.get("end_line"))
        except (TypeError, ValueError):
            continue
        kind = it.get("kind")
        if not path or kind not in _MODEL_KINDS or start < 1 or end < start:
            continue
        reason = str(it.get("reason") or "")[:200]
        hint = it.get("family_hint")
        out.append(
            {"path": path, "start_line": start, "end_line": end, "kind": kind,
             "family_hint": hint if isinstance(hint, str) and hint else None,
             "reason": reason}
        )
    return out


def channel_b_scan(
    shards: list[Shard],
    *,
    adapter,
    model_ref: str,
    agent_id: str,
    temperature: float = EXAM_TEMPERATURE,
) -> ChannelBOutcome:
    """执行通道 B 直扫（D26：侦察蜂身份、零存款、失败重试 1 次后分片缺席）。

    - 每分片装配 DispatchContract（:func:`build_l0_contract`，task_id=
      ``l0scan:<shard_id>``）并随任务头渲染（派单语义可审计）；零账本触达——
      审计锚 = 工件 producer 字段（调用方按 agent 落 l0_candidates 分片工件）
      + 扫描报告计数（含 model_channel_fail 归属蜂 id）；
    - SchemaRejected / AdapterError → 重试 1 次；仍败 → ChannelBFailure（该分片
      通道 B 缺席），不抛错。"""
    outcome = ChannelBOutcome(shard_count=len(shards))
    for shard in shards:
        contract = build_l0_contract(shard)
        messages = render_l0_scan(list(shard.blocks), contract=contract)
        raw: str | None = None
        last_error = ""
        for _attempt in (1, 2):                  # §1.3：重试 1 次
            try:
                raw = adapter.complete(
                    model_ref=model_ref,
                    messages=messages,
                    temperature=temperature,
                )
            except Exception as exc:              # noqa: BLE001 — 供给失败缺席计
                last_error = f"adapter:{type(exc).__name__}"
                raw = None
                continue
            try:
                items = parse_channel_b_response(raw, default_path=_shard_path(shard))
                last_error = ""
                break
            except SchemaRejected as exc:
                parsed_reason = exc.reason
                last_error = parsed_reason if parsed_reason.startswith("schema:") else f"schema:{parsed_reason}"
                raw = None
        if raw is None or last_error:
            outcome.failures.append(
                ChannelBFailure(
                    shard_id=shard.shard_id, agent_id=agent_id,
                    attempts=2, reason=last_error or "adapter",
                )
            )
            continue
        for it in items:
            outcome.candidates.append(
                Candidate(
                    path=it["path"],
                    span=(it["start_line"], it["end_line"]),
                    kind=it["kind"],
                    clue=it["reason"] or it["kind"],
                    channels=frozenset({"B"}),
                    taxonomy_hint=it["family_hint"],
                )
            )
    return outcome


def _shard_path(shard: Shard) -> str | None:
    """分片主路径（单块分片的 path 段；条目缺 path 时的回填兜底）。"""
    if len(shard.blocks) == 1 and ":" in shard.blocks[0].ref:
        return shard.blocks[0].ref.split(":", 1)[0]
    return None


# ── 通道 C：semgrep 规则（D30 必选件，永不卡点）───────────────────

@dataclass(frozen=True)
class RulesOutcome:
    """通道 C 结果：candidates=None 表示该轮缺席（skip_reason 计数）。"""

    candidates: list[Candidate] | None
    skip_reason: str | None = None


def default_rules_runner(timeout_s: int, *, rules_path: str | Path | None = None):
    """生产 semgrep 运行器（subprocess CLI；--json；超时/缺失二进制 → 异常穿透
    由 channel_c_scan 捕获计缺席）。rules_path 缺省 = 仓内 configs/semgrep_rules.yaml。"""

    def _runner(paths: list[str]) -> str:
        rules = Path(rules_path) if rules_path is not None else _default_rules_file()
        proc = subprocess.run(                    # noqa: S603 — 固定 argv，无 shell
            [
                "semgrep", "scan", "--json", "--quiet",
                "--config", str(rules),
                *paths,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return proc.stdout

    return _runner


def _default_rules_file() -> Path:
    return Path(__file__).resolve().parents[2] / "configs" / "semgrep_rules.yaml"


def channel_c_scan(
    paths: list[str],
    *,
    rules_runner,
    repo_root: str | Path | None = None,
) -> RulesOutcome:
    """执行通道 C 规则扫描（命中 → rule_hit 候选；运行期故障 → 缺席计数）。

    - ``rules_runner(paths) -> semgrep --json stdout``（可注入；生产 =
      :func:`default_rules_runner`）；
    - 任何运行期异常（超时/二进制缺失/输出不可解析）→ ``RulesOutcome(None,
      "rule_channel_skipped")``——该轮缺席，漏斗不停摆（§1.4 红线）；
    - 家族映射：规则 ``extra.metadata.family``（仓内规则包自带）→ 反查失败回落
      check_id ``qsw.<family>.<name>`` 前缀 → 仍失败 = taxonomy_hint=None；
    - 命中路径归一（G6 清账，SPEC-9 §8；DECISIONS 2026-10-12 记档项）：semgrep 对
      绝对路径输入回吐绝对路径，Candidate.path 须与其余通道同为仓库相对 posix 形
      （``repo_root`` 前缀剥离 + ``./`` 归一）；
    - clue = check_id（candidate_id 内容寻址输入）。"""
    try:
        stdout = rules_runner(paths)
        data = json.loads(stdout)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ValueError("semgrep 输出 results 非数组")
    except Exception as exc:                      # noqa: BLE001 — 缺席语义（含超时）
        _ = exc
        return RulesOutcome(None, "rule_channel_skipped")
    root_prefix = ""
    if repo_root is not None:
        root_prefix = str(Path(repo_root)).rstrip("/") + "/"
    out: list[Candidate] = []
    for hit in results:
        if not isinstance(hit, dict):
            continue
        check_id = str(hit.get("check_id") or "")
        if not check_id:
            continue
        path = str(hit.get("path") or "")
        if root_prefix and path.startswith(root_prefix):
            path = path[len(root_prefix):]
        while path.startswith("./"):
            path = path[2:]
        start = (hit.get("start") or {}).get("line")
        end = (hit.get("end") or {}).get("line")
        if not path or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start:
            continue
        extra = hit.get("extra") or {}
        family = (extra.get("metadata") or {}).get("family")
        if family not in _FAMILY_VOCAB:
            # 回退：check_id 含 ``qsw.<family>.`` 段（semgrep 会给规则 id 加配置
            # 路径前缀，按段定位 qsw 的后一段）；仍失败 = hint None。
            family = None
            parts = check_id.split(".")
            if "qsw" in parts:
                idx = parts.index("qsw")
                if idx + 1 < len(parts) and parts[idx + 1] in _FAMILY_VOCAB:
                    family = parts[idx + 1]
        out.append(
            Candidate(
                path=path,
                span=(start, end),
                kind="rule_hit",
                clue=check_id,
                channels=frozenset({"C"}),
                taxonomy_hint=family,
            )
        )
    return RulesOutcome(sorted(out, key=Candidate.sort_key))


#: 家族词表（taxonomy.yaml 键集；hints 校验用——不从 contracts 引 Family 枚举：
#: perception 对 contracts 的依赖面只有 params 旋钮，家族词表本地钉住与 taxonomy
#: 校验器同源的六门；扩族时随 taxonomy.yaml 同步扩充）。
_FAMILY_VOCAB = frozenset(
    {"injection", "deser", "crypto", "authz", "config_dep", "mem_safety"}
)
