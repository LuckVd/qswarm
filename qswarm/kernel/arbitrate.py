"""kernel.arbitrate：D34 蜂后顾问仲裁件编排（SPEC-9 §6；M5，D42-a 最小仲裁件）。

人工触发 ``run_arbitrate(ledger, cluster_ids=…)``：簇全量证据工件引用装配（复用
consult._cluster_block）→ queen_advisor 调用（角色是帽子：渲染 ③④ 段随 M5 顾问
接线落位，dispatch ROLE_* 表 advisor 行）→ ArbitrationDraft 解析 → **工件落盘 +
任务行审计锚**（建议权语义，D42-a 逐字）：

- 契约直装（:class:`DispatchContract`）——``build_contract`` 对 advisor 显式拒绝
  （顾问不进派单状态机，§4.2/§16.7-9），建议任务不走四件套装配函数；
- 落账锚 = tasks 行 + DISPATCH 事件（内核代落账，§16.7-9「落账仍走内核」）+
  arbitration 工件（kind=arbitration：摘要全文 + 草稿 JSON 两件；summary_ref 由
  ``artifact://pending`` 回填真实引用——模板 §2.2 逐字「由派单方落盘后回填」）；
- 任务收口走 close_task_empty（零业务行零存款，与 declined 同语义）——**非
  verdict 非存款**，不入生产口碑（§4.2）；
- 全自动触发（consult 流水线内联）归 D42-b，S2 数据积累后另拍——本模块只提供
  人工触发入口。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from qswarm.contracts import Role, load_params
from qswarm.contracts.models import ArbitrationDraft, DispatchContract
from qswarm.exams.store import artifact_put
from qswarm.kernel.consult import _cluster_block
from qswarm.kernel.dispatch import (
    ROLE_TASK_BOUNDS,
    ROLE_TOOL_BOUNDS,
    close_task_empty,
    create_task,
    dispatch,
    open_run,
)
from qswarm.kernel.ledger import KernelClock, KernelLedger, WallClock
from qswarm.kernel.route import RouteDecision
from qswarm.prompts import EXAM_TEMPERATURE, parse_output, render
from qswarm.prompts.parse import SchemaRejected

__all__ = ["ArbitrationReport", "ARBITRATE_TASK_PREFIX", "ARBITRATE_GOAL", "run_arbitrate"]

#: 建议任务 id 前缀（幂等键：同 run 同簇不重复仲裁）。
ARBITRATE_TASK_PREFIX = "arb:"

#: 仲裁 goal（四件套①；建议权语义在此对模型声明，D42-a）。
ARBITRATE_GOAL = (
    "蜂后顾问仲裁：按注入的簇证据材料起草结构化仲裁摘要——概括争议焦点与两派"
    "证据主张，逐簇给出建议动作（merge|split|escalate）与一句话依据。你只有建议权："
    "产出是建议工件，一切落地决定由编排内核执行；不产 finding、不投票、不改变系统状态。"
)


@dataclass
class ArbitrationReport:
    """一次人工触发的仲裁报告（工件引用面；零存款零 verdict）。"""

    run_id: str
    drafts: int = 0
    schema_fail: int = 0
    skipped_unknown_cluster: int = 0
    detail: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "drafts": self.drafts,
            "schema_fail": self.schema_fail,
            "skipped_unknown_cluster": self.skipped_unknown_cluster,
            "detail": list(self.detail),
        }


def run_arbitrate(
    ledger: KernelLedger,
    *,
    cluster_ids: list[str],
    run_id: str,
    repo_root: str | Path,
    adapter,
    store_dir: str | Path,
    params=None,
    clock: KernelClock | None = None,
    deadline_s: int = 600,
) -> ArbitrationReport:
    """D34 最小仲裁件（D42-a 人工触发）：逐簇产 ArbitrationDraft 建议工件。

    - 幂等：任务 id ``arb:{run_id}:{cluster_id}`` 已存在 → 跳过（重跑收敛）；
    - 未知簇 → skipped_unknown_cluster 如实计数（不硬凑）；
    - 解析失败 → schema_fail 计数、任务留 dispatched（§4.3 退回语义，人工复核）。"""
    params = params if params is not None else load_params()
    clock = clock if clock is not None else WallClock()
    root = Path(repo_root)
    store = Path(store_dir)
    report = ArbitrationReport(run_id=run_id)
    open_run(ledger, run_id=run_id, mode="full", repo_url=str(root),
             budget_cents=0)          # 建议件 run 行（tasks 外键前件；零预算面）
    advisor_pool = ledger.conn.execute(
        "SELECT a.agent_id, a.model_ref FROM agents a JOIN hats h"
        " ON a.agent_id = h.agent_id WHERE h.role = 'queen_advisor' AND a.status = 'active'"
        " ORDER BY a.agent_id"
    ).fetchall()
    advisor = str(advisor_pool[0][0]) if advisor_pool else "kernel"
    model_ref = str(advisor_pool[0][1]) if advisor_pool else ""

    for cid in cluster_ids:
        cid = str(cid)
        task_id = f"{ARBITRATE_TASK_PREFIX}{run_id}:{cid}"
        if ledger.conn.execute(
            "SELECT 1 FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone() is not None:
            continue                              # 幂等：同 run 同簇不重复仲裁
        crow = ledger.conn.execute(
            "SELECT key_path FROM clusters WHERE cluster_id = ?", (cid,)
        ).fetchone()
        if crow is None:
            report.skipped_unknown_cluster += 1
            continue
        members = [
            str(r[0])
            for r in ledger.conn.execute(
                "SELECT finding_id FROM cluster_members WHERE cluster_id = ?"
                " ORDER BY finding_id",
                (cid,),
            )
        ]
        decision = RouteDecision(
            task_id=task_id, role=Role.QUEEN_ADVISOR.value,
            family=str(cid).partition("-")[0], node="arbitration",
            assignee=advisor, channel="explore", reason="arbitrate_manual",
            tilt=None, raw_tilt=None, specs=(), spec_c_stars=(),
        )
        create_task(
            ledger, run_id=run_id, role=Role.QUEEN_ADVISOR.value, node="arbitration",
            shard={"cluster_id": cid, "members": members}, task_id=task_id,
        )
        contract = DispatchContract(
            task_id=task_id, role=Role.QUEEN_ADVISOR.value, goal=ARBITRATE_GOAL,
            output_schema_ref="ArbitrationDraft",
            tool_bounds=list(ROLE_TOOL_BOUNDS[Role.QUEEN_ADVISOR.value]),
            task_bounds=ROLE_TASK_BOUNDS[Role.QUEEN_ADVISOR.value],
            context_refs=[f"cluster://{cid}"], taxonomy_node="arbitration",
            hop_budget=0, deadline_s=int(deadline_s),
        )
        dispatch(ledger, task_id, contract, decision, run_id=run_id)
        messages = render(
            Role.QUEEN_ADVISOR, contract=contract,
            blocks=[_cluster_block(ledger, root, members,
                                   params.consult.l2_context_lines)],
        )
        try:
            raw = adapter.complete(
                model_ref=model_ref, messages=messages,
                temperature=EXAM_TEMPERATURE, timeout_s=float(deadline_s),
            )
            draft = parse_output(Role.QUEEN_ADVISOR, raw)
        except SchemaRejected:
            report.schema_fail += 1
            continue                              # 任务留 dispatched（§4.3 退回面）
        except Exception:                         # noqa: BLE001 — 供给失败族
            report.schema_fail += 1
            continue
        # 落盘：摘要全文 + 草稿 JSON（summary_ref 回填真实引用）。
        summary_text = "\n".join(
            f"- {s.cluster_id}: {s.action} — {s.rationale}"
            for s in draft.suggestions
        ) or "（无建议）"
        summary_hash = artifact_put(
            ledger, store,
            content=f"# Arbitration Summary — {run_id}\n\n{summary_text}\n".encode("utf-8"),
            mime="text/markdown", kind="arbitration", producer=advisor,
        )
        draft.summary_ref = f"artifact://{summary_hash}"
        draft_hash = artifact_put(
            ledger, store, content=draft.model_dump_json().encode("utf-8"),
            mime="application/json", kind="arbitration", producer=advisor,
        )
        close_task_empty(ledger, task_id)         # 建议权：零业务行零存款收口
        report.drafts += 1
        report.detail.append({
            "cluster_id": cid, "task_id": task_id,
            "suggestions": [s.model_dump() for s in draft.suggestions],
            "summary_ref": draft.summary_ref, "draft_ref": f"artifact://{draft_hash}",
        })
    return report
