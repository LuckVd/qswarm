"""exams.runner：考试执行编排（SPEC-6 §3.2–§3.5）。

调用 / 解析 / 判分 / 缓存 / 落账的单格（agent × family）编排：

- 出卷 = :func:`exams.paper.draw_paper`（ENTRY=12=8 真+4 雷，确定性）；
- 每题一次调用（温度恒 ``prompts.EXAM_TEMPERATURE=0``，只走 primary，S6 不走
  fallback）；原始响应全文写工件库（kind=exam_log）+ 响应缓存（replay 真源，S9）；
- 解析 = 生产同源 ``prompts.parse.parse_output``（信封是运输层，E4）；信封解析
  失败按该题 schema 失败计（S7：契约失能是真实能力缺陷信号，判错）；
- 失败语义（§3.4/S7）：
  - 网络超时/5xx（重试 1 次后仍失败）→ **基建失败**：该题不写 exam_results 行、
    不计入判分；同格基建失败题数 > 2 → 该格考试整体无效（零存款、零行、报告
    ``infra_invalid``）；
  - 输出解析失败（含信封畸形，重试 1 次后仍失败）→ **判错**（correct=0，报告记
    ``schema_fail``）；
  - 输出含金丝雀串 → 判分照常 + ``canary_hit`` 进报告（§1.4 泄漏溯源通道）；
- 写口顺序（§4.3）：单格内 exam_results 行先落、deposit 事件后落，**同一事务**；
  崩溃 → 该格按「部分作答」重跑（出卷确定 ⇒ 重跑覆盖同批行，行级幂等键查重跳过）；
- 位级可复现（§3.5/S9）：确定性边界 = 缓存之后——出卷、解析、判分、落账、折叠
  全链零随机源零时钟漂移（时钟注入三时钟不变）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from qswarm.contracts import (
    DepositOrigin,
    DispatchContract,
    ExamDepositPayload,
    ExamPurpose,
    LedgerKind,
    Role,
    SignalTier,
)
from qswarm.kernel import KernelLedger, centered_exam_weight, transaction
from qswarm.kernel.dispatch import ROLE_OUTPUT_SCHEMA, ROLE_TASK_BOUNDS, ROLE_TOOL_BOUNDS
from qswarm.prompts import (
    EXAM_TEMPERATURE,
    ContextBlock,
    FAMILY_GLOSS,
    SchemaRejected,
    extract_json,
    family_display,
    parse_output,
    render_exam,
)
from qswarm.supply import AdapterError, CandidateAdapter

from .bank import SeedBank, SeedItem
from .judge import judge_answer
from .paper import draw_paper
from .store import artifact_put, write_exam_result

__all__ = [
    "EXAM_DEADLINE_S",
    "CellOutcome",
    "ItemOutcome",
    "ReplayCacheMiss",
    "SnapshotStore",
    "cache_key",
    "cache_get",
    "cache_put",
    "run_entry_cell",
    "deposit_exists",
]

#: 考试调用 deadline（任务头行渲染值；考试面常数，DECISIONS M2 实现期行）。
EXAM_DEADLINE_S = 600

#: 单格基建失败上限（§3.4 逐字：> 2 → 该格整体无效）。
INFRA_FAIL_LIMIT = 2


# ── 响应缓存（S9：考试结果的最终真源；replay 不经网络）────────────


def cache_key(
    agent_id: str,
    family: str,
    item_id: str,
    *,
    role: str = "",
    bank_version: int | str = 0,
) -> str:
    """缓存键（blake2b 内容定址同型——不使用内建 hash，跨 PYTHONHASHSEED 稳定）。

    键含 role 与 bank_version（2026-10-20 修复，DECISIONS 同日行）：不含 role 时
    同蜂跨角色（帽考）命中旧角色答卷且按 S9 纪律不重试；不含 bank_version 时
    题库 regen（保 item_id 换内容）后复用同缓存目录会静默测旧题——canary 按
    item_id+seed 确定性复原，无法暴露陈旧。缺省空参 = 旧键形（外部工具兼容）。"""
    h = hashlib.blake2b(digest_size=16)
    for part in (agent_id, family, str(role), str(bank_version), item_id):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def cache_get(cache_dir: str | Path | None, key: str) -> str | None:
    if cache_dir is None:
        return None
    path = Path(cache_dir) / f"{key}.txt"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def cache_put(cache_dir: str | Path | None, key: str, raw: str) -> None:
    if cache_dir is None:
        return
    path = Path(cache_dir) / f"{key}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


class ReplayCacheMiss(RuntimeError):
    """replay 模式缓存未命中（快速失败：replay ≠ 在线即停，不得静默判基建失败）。

    由编排层（seeding 的 replay 适配器占位）抛出——runner 只捕获 AdapterError，
    本异常穿透单格事务快速上抛，replay ≠ 在线即停。"""


# ── 快照读取（§1.3：考试侧经工件库读取，不直接依赖 seeds/ 盘上路径）──────


class SnapshotStore:
    """快照内容读取器：清单工件（name→sha256）+ 内容寻址库。"""

    def __init__(self, ledger: KernelLedger, store_dir: str | Path, root: str | Path) -> None:
        from .store import artifact_get

        self._artifact_get = lambda h: artifact_get(ledger, Path(store_dir), h)
        self._root = Path(root)
        self._manifests: dict[str, dict[str, str]] = {}

    def index(self, item: SeedItem) -> dict[str, str]:
        """题快照 name→sha256 映射（清单工件反查，缓存一次）。"""
        if item.item_id not in self._manifests:
            from .bank import manifest_sha256

            raw = self._artifact_get(manifest_sha256(item, self._root))
            if raw is None:
                raise ValueError(
                    f"[{item.item_id}] 快照清单工件缺失（先 ingest_bank_artifacts）"
                )
            manifest = json.loads(raw.decode("utf-8"))
            self._manifests[item.item_id] = {f["name"]: f["sha256"] for f in manifest["files"]}
        return self._manifests[item.item_id]

    def text(self, item: SeedItem, name: str) -> str:
        sha = self.index(item)[name]
        raw = self._artifact_get(sha)
        if raw is None:
            raise ValueError(f"[{item.item_id}] 快照文件工件缺失：{name}")
        return raw.decode("utf-8")

    def resolver(self) -> Callable[[SeedItem, str], str]:
        """:data:`SnapshotResolver` 形态（seeding 编排注入 run_entry_cell）。"""
        return self.text


def _disk_snapshot_text(root: str | Path) -> Callable[[SeedItem, str], str]:
    """盘上直读形态（测试/离线工具用；生产播种走 SnapshotStore 工件库读取）。"""
    root_path = Path(root)

    def resolve(item: SeedItem, name: str) -> str:
        return (root_path / "items" / item.item_id / "snapshot" / name).read_text(
            encoding="utf-8"
        )

    return resolve


#: 快照解析器形态：item × 文件名 → 文本。
SnapshotResolver = Callable[[SeedItem, str], str]


# ── 单格编排 ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ItemOutcome:
    """单题结果（播种报告判分明细的载体）。"""

    item_id: str
    expected: str
    correct: bool | None        # None = 基建失败（不写行、不计入判分）
    schema_fail: bool = False
    infra_fail: bool = False
    canary_hit: bool = False
    attempts: int = 0


@dataclass(frozen=True)
class CellOutcome:
    """单格（agent × family）考试结果。"""

    agent_id: str
    role: str
    family: str
    exam_id: str
    items: tuple[ItemOutcome, ...] = ()
    status: str = "ok"                      # ok | infra_invalid
    real_n: int = 0
    fp_n: int = 0
    real_correct: int = 0
    fp_rejected: int = 0
    schema_fail: int = 0
    infra_fail: int = 0
    canary_hit: int = 0
    accuracy: float | None = None
    direction: int | None = None
    weight: float | None = None
    deposit_written: bool = False

    @property
    def infra_invalid(self) -> bool:
        return self.status == "infra_invalid"


class _CellInvalid(Exception):
    """内部信号：单格基建失败超限 → 整格回滚（零存款、零行，§3.4）。"""


def deposit_exists(ledger: KernelLedger, agent_id: str, role: str, family: str) -> bool:
    """账本内该格考试存款是否已在（重跑读侧幂等，§4.2/B2）。

    幂等键 = 格键 ``(agent, role, taxonomy_path=[family], origin=exam)``——
    PAYLOAD_SCHEMA 对 deposit 事件按基类校验（M0 既定形态），载荷中的 exam_id
    不入账本 payload_json，故以格键判定（语义等价：一格恰一笔考试存款）。"""
    for ev in ledger.read(kinds=(LedgerKind.DEPOSIT,)):
        payload = ev.payload
        if (
            payload.get("agent") == agent_id
            and payload.get("role") == role
            and list(payload.get("taxonomy_path") or ()) == [family]
            and payload.get("origin") == str(DepositOrigin.EXAM.value)
        ):
            return True
    return False


def _build_contract(agent_id: str, role: str, family: str) -> DispatchContract:
    """考试 DispatchContract（同 schema 同契约防 prompt 指纹偏差，§8.2）。

    **题面不给家族（S4 逐字，2026-10-20 修复）**：goal 与 taxonomy_node 均不承载
    家族 token——judge 判分要求 ``guess_family == 答案键家族``（judge.py），家族
    进题头等于泄题（auditor 真题家族分抄题头即满）。家族词表在 system 模板的
    输出格式段（SCOUT/AUDITOR_SCHEMA_FIELDS 值域枚举）——模型从代码证据在六家族
    菜单中选对是真实考面。reviewer/verifier 的家族面经送审 claim 块承载（构造
    即对齐口径不变，M3-6）；``taxonomy_node=""`` 与生产 L1 契约同形
    （perception/channels.py）。``family`` 形参保留（task_id 派生与出卷用）。"""
    goals = {
        "scout": "入职考：判断各快照是否存在漏洞（可疑时按输出词表给出家族猜测）",
        "auditor": "入职考：判断各快照是否存在漏洞（存在时按输出词表给出家族）",
        "reviewer": "入职考：独立评审送审 finding（声称家族以送审材料为准，勿默认采信）",
        "verifier": "入职考：对预置簇证据链给出三分类复现结论",
    }
    return DispatchContract(
        task_id=f"entry:{agent_id}:{family}",
        role=Role(role),
        goal=goals.get(role, "入职考：判断各快照是否存在漏洞"),
        output_schema_ref=ROLE_OUTPUT_SCHEMA[role],
        tool_bounds=list(ROLE_TOOL_BOUNDS[role]),
        task_bounds=ROLE_TASK_BOUNDS[role],
        context_refs=[],
        taxonomy_node="",
        hop_budget=0,
        deadline_s=EXAM_DEADLINE_S,
    )


#: 家族注解（v0.4 联调修复，DECISIONS 2026-10-20 行）：裸家族标签与快照具体漏洞
#: 形态在 LLM 评审视角可错位（实证：authz 族=路径穿越件，claim「存在 authz 漏洞」
#: 被三蜂一致拒报——同题注入注解后 direction 由 -1 翻 +1，fp_susp 0.9→0.3）。
#: 注解只展开声称家族的语义面，不改「声称家族 ≡ 答案键」不变量；未知家族回退
#: 裸标签（零行为变化）。**单一源在 prompts.gloss**（2026-10-20 共享化）：考试
#: claim 与生产票面（kernel.consult._finding_block/_candidate_block）同源同形，
#: 消除 eval/production 偏移；本别名供既有测试与 import 面兼容。
_FAMILY_GLOSS = FAMILY_GLOSS


def _exam_claim(item: SeedItem) -> str:
    """纸面考预置 claim（reviewer 送审 finding / verifier 预置簇共用；确定性）。

    - 声称家族/类型 ≡ 题库答案键 taxonomy_path（real 面「family 维与答案键家族
      对齐」由构造侧承载——SPEC-7 §4.2/§4.3 口径，DECISIONS M3 实现期行）；
      v0.4 起家族名附 FAMILY_GLOSS 注解（标签语义面展开，家族 token 不变）；
    - 雷题（expected=fp）同样形态送审（声称存在漏洞而快照无洞）——评审/验证
      的拒报面由判分函数消费（fp：REJECT / verified_fp）。"""
    family = item.taxonomy_path[0]
    type_ = item.taxonomy_path[1] if len(item.taxonomy_path) > 1 else None
    family_disp = family_display(family)
    return (
        f"- 声称家族: {family_disp}\n"
        f"- 声称类型: {type_ if type_ else '（家族级，未细分）'}\n"
        f"- 标题: 送审报告：该快照存在 {family_disp} 漏洞\n"
        "- 摘要: 报告者声称下方快照代码存在上述家族漏洞并提交送审；"
        "请勿默认采信报告者结论，只依据快照代码独立判断。"
    )


def _item_blocks(
    item: SeedItem,
    resolve_text: SnapshotResolver,
    role: str | None = None,
) -> list[ContextBlock]:
    """单题数据块（§3.2）：题面头（item_id、lang、一句话）+ 快照文件按
    snapshot_files 顺序注入；题号标注在数据块首行。M3 补播（SPEC-7 §4.1）：
    reviewer 追加送审 finding 块；verifier 追加预置簇证据链块（纸面考试，
    无沙箱执行——D29）。"""
    heads = {
        "scout": "判断以下快照是否存在漏洞",
        "auditor": "判断以下快照是否存在漏洞",
        "reviewer": "以下为送审 finding 与快照材料，独立评审并投票",
        "verifier": "以下为预置簇证据链与快照材料，给出三分类复现结论",
    }
    head = (
        f"item_id: {item.item_id}\n"
        f"lang: {item.lang}\n"
        f"{heads.get(role or 'scout', heads['scout'])}"
    )
    blocks = [ContextBlock(ref=item.repo_snapshot_ref, content=head)]
    if role in ("reviewer", "verifier"):
        cluster_note = (
            "\n- 簇构成: C-EXAM（单成员 = 上述送审 finding，同快照同家族）"
            if role == "verifier"
            else ""
        )
        blocks.append(
            ContextBlock(
                ref=f"artifact://exam/{item.item_id}/staged-finding",
                content=_exam_claim(item) + cluster_note,
            )
        )
    for name in item.snapshot_files:
        blocks.append(
            ContextBlock(
                ref=f"{item.repo_snapshot_ref}{name}",
                content=resolve_text(item, name),
            )
        )
    return blocks


def _parse_envelope_answer(role: str, raw: str, item_id: str) -> tuple[object | None, bool]:
    """原始响应 → (parsed, declined)。信封畸形/缺本题答案/产出 schema 失败 →
    SchemaRejected（上层按 schema_fail 计）。"""
    text = extract_json(raw)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        raise SchemaRejected("no_json")
    answer = None
    for entry in data["answers"]:
        if isinstance(entry, dict) and entry.get("item_id") == item_id:
            answer = entry
            break
    if answer is None:
        raise SchemaRejected(f"schema:Envelope:answer_missing:{item_id}")
    if answer.get("declined") is True:
        return None, True
    output = answer.get("output")
    if not isinstance(output, dict):
        # declined=false 且 output=null/非对象 = 信封畸形（严格口径，DECISIONS M2 行）。
        raise SchemaRejected(f"schema:Envelope:output_not_object:{item_id}")
    parsed = parse_output(role, json.dumps(output, ensure_ascii=False))
    return parsed, False


def run_entry_cell(
    ledger: KernelLedger,
    bank: SeedBank,
    *,
    agent_id: str,
    role: str,
    model_ref: str,
    family: str,
    adapter: CandidateAdapter,
    seed: int,
    cache_dir: str | Path | None = None,
    store_dir: str | Path | None = None,
    resolve_text: SnapshotResolver | None = None,
) -> CellOutcome:
    """单格入职考执行（出卷 → 逐题调用/解析/判分 → 行 → 存款，§3/§4.2）。

    - 行 + 存款**同一事务**；基建失败超限（>2）→ 整格回滚（零存款、零行），返回
      ``status="infra_invalid"`` 的结果（判分明细置 correct=None，如实呈现）；
    - 缓存命中不经网络（重跑/复现路径，S9）；在线调用成功后写缓存 + exam_log 工件；
    - ``resolve_text`` 缺省 = 盘上直读（工件库形态由 seeding 编排注入
      ``SnapshotStore.resolver()``）。
    """
    if resolve_text is None:
        if store_dir is None:
            raise ValueError("run_entry_cell 需要 resolve_text 或 store_dir（快照读取面）")
        resolve_text = _disk_snapshot_text(Path(store_dir))
    exam_id = f"entry:{agent_id}:{family}"
    paper = draw_paper(
        bank,
        agent_id=agent_id,
        role=role,
        family=family,
        purpose=ExamPurpose.ENTRY,
        bank_version=bank.bank_version,
        seed=seed,
    )
    contract = _build_contract(agent_id, role, family)
    outcomes: list[ItemOutcome] = []
    real_n = fp_n = real_correct = fp_rejected = 0
    schema_fail = infra_fail = canary_hit = 0
    deposit_written = False
    accuracy: float | None = None
    direction: int | None = None
    weight: float | None = None
    status = "ok"

    try:
        with transaction(ledger):
            for item in paper:
                if item.expected == "real":
                    real_n += 1
                else:
                    fp_n += 1
                key = cache_key(
                    agent_id, family, item.item_id,
                    role=role, bank_version=bank.bank_version,
                )
                raw = cache_get(cache_dir, key)
                attempts = 0
                parsed: object | None = None
                declined = False
                infra = False
                schema = False
                hit = False
                if raw is not None:
                    # 缓存命中的 raw = 在线重试后的最终响应（S9）：不再重试。
                    attempts = 1
                    try:
                        parsed, declined = _parse_envelope_answer(role, raw, item.item_id)
                    except SchemaRejected:
                        parsed = None
                        schema = True
                else:
                    for _attempt in (1, 2):     # §3.4：重试 1 次
                        attempts += 1
                        try:
                            raw = adapter.complete(
                                model_ref=model_ref,
                                messages=render_exam(
                                    role,
                                    contract=contract,
                                    blocks=_item_blocks(item, resolve_text, role=role),
                                ),
                                temperature=EXAM_TEMPERATURE,
                            )
                        except AdapterError:
                            infra = True
                            continue
                        # 已获响应：该题失败语义归 schema 面（infra 粘滞复位——
                        # attempt1 网络失败 + attempt2 畸形输出此前被误记 infra_fail，
                        # 违反 S7「解析失败判错」且可诱发整格 infra_invalid）。
                        infra = False
                        cache_put(cache_dir, key, raw)
                        try:
                            parsed, declined = _parse_envelope_answer(role, raw, item.item_id)
                            schema = False
                            break
                        except SchemaRejected:
                            parsed = None
                            schema = True
                hit = raw is not None and item.canary in raw
                if infra:
                    infra_fail += 1
                    outcomes.append(
                        ItemOutcome(
                            item_id=item.item_id, expected=item.expected, correct=None,
                            infra_fail=True, attempts=attempts,
                        )
                    )
                    continue
                if store_dir is not None and raw is not None:
                    artifact_put(
                        ledger, Path(store_dir), raw.encode("utf-8"),
                        mime="text/plain", kind="exam_log", producer=agent_id,
                    )
                if schema:
                    schema_fail += 1
                    correct = False          # S7：契约失能判错（correct=0）
                else:
                    correct = judge_answer(item, role, parsed if not declined else None)
                if hit:
                    canary_hit += 1
                if item.expected == "real":
                    real_correct += int(bool(correct))
                elif correct:
                    fp_rejected += 1
                write_exam_result(
                    ledger,
                    purpose=ExamPurpose.ENTRY,
                    agent_id=agent_id,
                    role=role,
                    node=family,
                    item_id=item.item_id,
                    correct=correct,
                    model_ref=model_ref,
                )
                outcomes.append(
                    ItemOutcome(
                        item_id=item.item_id, expected=item.expected, correct=bool(correct),
                        schema_fail=schema, canary_hit=hit, attempts=attempts,
                    )
                )

            if infra_fail > INFRA_FAIL_LIMIT:
                raise _CellInvalid(
                    f"[{agent_id} × {family}] 基建失败 {infra_fail} 题 >"
                    f" {INFRA_FAIL_LIMIT}（该格考试整体无效，§3.4）"
                )
            accuracy = real_correct / real_n if real_n else None
            if accuracy is not None:
                direction, weight = centered_exam_weight(accuracy)
                if not deposit_exists(ledger, agent_id, role, family):
                    ledger.append(
                        "deposit",
                        ExamDepositPayload(
                            finding=None,
                            cluster=None,
                            agent=agent_id,
                            role=Role(role),
                            taxonomy_path=[family],
                            signal_tier=SignalTier.S1,
                            direction=int(direction),
                            weight=float(weight),
                            origin=DepositOrigin.EXAM,
                            exam_id=exam_id,
                        ),
                    )
                    deposit_written = True
    except _CellInvalid:
        # 整格回滚已由 transaction 上下文完成（零存款、零行）；判分明细如实置空。
        outcomes = [
            replace(o, correct=None) for o in outcomes
        ]
        real_correct = fp_rejected = 0
        accuracy = None
        direction = None
        weight = None
        status = "infra_invalid"

    return CellOutcome(
        agent_id=agent_id,
        role=role,
        family=family,
        exam_id=exam_id,
        items=tuple(outcomes),
        status=status,
        real_n=real_n,
        fp_n=fp_n,
        real_correct=real_correct,
        fp_rejected=fp_rejected,
        schema_fail=schema_fail,
        infra_fail=infra_fail,
        canary_hit=canary_hit,
        accuracy=accuracy,
        direction=direction,
        weight=weight,
        deposit_written=deposit_written,
    )
