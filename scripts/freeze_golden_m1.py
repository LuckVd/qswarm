#!/usr/bin/env python3
"""freeze_golden_m1：M1 dispatch/re_route 黄金夹具的确定性冻结（SPEC-5 §4.3 M1 新增黄金类）。

与 ``scripts/freeze_budget_golden.py``（budget 合成类）同一形态：M1 黄金是**合成
规格用例**——输入 = SPEC-2 §2/§3 逐字口径的字面值（契约四件套、状态机操作序、
re_route 触发/弱存款标准形），expected = 当前内核在同一确定性执行语义下的输出
（经 tests/golden_loader 的 dispatch/re_route 执行器，落盘前逐条自检，任何一条
不一致即中止，不产出坏夹具）。

四个 dispatch 夹具的钉点（tests/golden/dispatch/，命名 <kind>-NNN.json）：

- dispatch-001「四件套+派单」：full 档装配（hop_budget=2 直出）+ queued→dispatched
  快乐路径（tasks 行 + DISPATCH 事件同事务，零存款）；
- dispatch-002「hop 耗尽+重派通道关闭」：fast 档 hop_budget=0（§2.2 逐字：跳数预算
  天生耗尽，无深挖）+ timeout 重派一次后 attempts 耗尽 → tasks 置 failed +
  ValueError + RE_DISPATCH 审计事件（to_agent=from_agent，未发生改派），零存款；
- dispatch-003「空池+expire 收尾」：D10 空池派单（assignee=null 事件、tasks 留
  queued、attempts 不消耗）+ expire_run 非终态→expired（零存款，D19 含 verified）+
  未完任务→failed + 幂等 []；
- dispatch-004「结论状态机」：§10.2 迁移表主链 + D8 直达边 + 非法迁移/终态再流转
  ValueError（kind=finding 事件 payload.state 承载，零存款）。

四个 re_route 夹具的钉点（tests/golden/re_route/）：

- re_route-001「标准形弱存款」：错配触发 → 每 scout 一笔 S1/−1/0.05/src_seq
  （字典序），cells 猜测类型格与家族影子格 neg_w 增 0.05、n_eff_s2s3 不动，
  rebuild 重放等价 diff=0；
- re_route-002「幂等与触发门」：同 finding 至多一次（读侧幂等 []）+ 细化/全等
  ValueError（D12 写侧非法）+ L0 直出仅事件零存款；
- re_route-003「guess_taxonomy 全序」：票数 → 猜测格 C* 之和（谱系 scout_of 显式
  接参 D14；无格 ROOT_PRIOR）→ 路径字典序；无猜测 None；
- re_route-004「多 finding+重放等价」：不同路径段弱存款 + 收尾全账本 rebuild
  diff=0 + 表投影/存款计数冻结。

用法::

    python3 scripts/freeze_golden_m1.py            # 写入 tests/golden/{dispatch,re_route}/
                                                   # 并更新 manifest counts/regen_m1_commands
    python3 scripts/freeze_golden_m1.py --check    # 只重放自检，不落盘

同（输入字面值，内核实现）⇒ 输出逐字节一致；FixedClock 钉死无墙钟入场；期望中的
事件**不含信封 seq/ts**（账本记账产物），payload.src_seq 为规格字段、其值由确定性
编号规则（从 1 起、按输入序）唯一决定。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "tests"))

import golden_loader as gl  # noqa: E402

_REGEN = "python3 scripts/freeze_golden_m1.py"
_CLOCK = "2026-01-01T00:00:00Z"


# ── 蓝图公共件 ───────────────────────────────────────────────────

def _decision(task_id: str, *, node: str, role: str = "auditor",
              assignee: str | None = "A-auditor-1", reason: str = "no_specs") -> dict:
    return {
        "task_id": task_id, "role": role, "family": node.split("/")[0], "node": node,
        "assignee": assignee, "channel": "specialist" if reason != "no_specs" else "explore",
        "reason": reason, "tilt": None, "raw_tilt": None, "specs": [], "spec_c_stars": [],
        "uniform_pick": None,
    }


def _contract(task_id: str, decision: dict, *, mode: str, deadline_s: int) -> dict:
    """契约字面（四件套缺省装配口径；冻结时以 build_contract 实测为准）。"""
    from qswarm.kernel import ROLE_OUTPUT_SCHEMA, ROLE_TASK_BOUNDS, ROLE_TOOL_BOUNDS, hop_budget_for

    role = decision["role"]
    return {
        "task_id": task_id, "role": role,
        "goal": f"对 {decision['node']} 以 {role} 职责完成本次分片",
        "output_schema_ref": ROLE_OUTPUT_SCHEMA[role],
        "tool_bounds": list(ROLE_TOOL_BOUNDS[role]),
        "task_bounds": ROLE_TASK_BOUNDS[role],
        "context_refs": [], "taxonomy_node": decision["node"],
        "hop_budget": hop_budget_for(mode), "deadline_s": deadline_s,
    }


def _deposit(agent: str, path: list[str], *, direction: int = 1, weight: float = 0.5,
             tier: str = "S2", finding: str | None = None) -> dict:
    return {
        "kind": "deposit",
        "payload": {
            "finding": finding, "cluster": None, "agent": agent, "role": "scout",
            "taxonomy_path": path, "signal_tier": tier, "direction": direction,
            "weight": weight, "origin": "prod", "src_seq": None,
        },
    }


def _case(case_id: str, kind: str, spec_ref: str, pins: str, desc: str,
          inputs: dict, ops: list[dict]) -> dict:
    inputs = {"clock": _CLOCK, **inputs}
    return {
        "case_id": case_id, "kind": kind,
        "meta": {"origin": "spec_literal", "spec_ref": spec_ref, "pins": pins,
                 "clock": _CLOCK, "generator": _REGEN},
        "desc": desc,
        "inputs": inputs,
        "ops": ops,
        "expected": None,          # 冻结时经 gl.execute_case 实测回填
    }


# ── dispatch 类蓝图 ──────────────────────────────────────────────

def case_dispatch_001() -> dict:
    task_id = "T-R1-001"
    dec = _decision(task_id, node="injection/sqli")
    contract = _contract(task_id, dec, mode="full", deadline_s=600)
    return _case(
        "dispatch-001", "dispatch",
        "SPEC-2 §2.2/§2.3（§4.3 四件套）；SPEC-5 §1.2/§4.3",
        "full 档四件套逐字（①goal ②output_schema_ref ③tool_bounds ④task_bounds +"
        " hop_budget=2 + taxonomy_node）+ queued→dispatched（tasks 行与 DISPATCH 事件"
        "同事务，attempts=1）；零存款。",
        "full 档四件套装配 + 派单快乐路径：契约字段逐字直出；DISPATCH 事件承载"
        " assignee/hop_budget；任务行 dispatched/attempts=1。",
        {"runs": [{"run_id": "R1", "mode": "full"}]},
        [
            {"op": "create_task", "args": {"run_id": "R1", "role": "auditor",
                                           "node": "injection/sqli", "shard": {"paths": ["src/app/db.py"]}}},
            {"op": "build_contract", "args": {"decision": dec, "mode": "full", "deadline_s": 600}},
            {"op": "dispatch", "args": {"task_id": task_id, "contract": contract, "decision": dec}},
        ],
    )


def case_dispatch_002() -> dict:
    task_id = "T-R1-001"
    dec = _decision(task_id, node="crypto/aes_ecb")
    contract = _contract(task_id, dec, mode="fast", deadline_s=30)
    return _case(
        "dispatch-002", "dispatch",
        "SPEC-2 §2.2/§2.3/§16.7-8（D 关闭分支见 DECISIONS 2026-10-10 dispatch 行）；SPEC-5 §1.2/§4.3",
        "fast 档 hop_budget=0（跳数预算天生耗尽）+ 重派通道：attempts<2 重派（attempts+1）"
        "+ RE_DISPATCH 事件；≥2 → tasks 置 failed + ValueError + 关闭审计事件"
        "（to_agent=from_agent）+ 零存款。",
        "fast 档 hop 耗尽态契约 + 重派通道耗尽 → failed：重试上限语义与审计锚逐字。",
        {"runs": [{"run_id": "R1", "mode": "fast"}]},
        [
            {"op": "create_task", "args": {"run_id": "R1", "role": "auditor",
                                           "node": "crypto/aes_ecb", "shard": {"paths": ["src/crypto.py"]}}},
            {"op": "build_contract", "args": {"decision": dec, "mode": "fast", "deadline_s": 30}},
            {"op": "dispatch", "args": {"task_id": task_id, "contract": contract, "decision": dec}},
            {"op": "re_dispatch", "args": {"task_id": task_id, "to_agent": "A-auditor-2", "reason": "timeout"}},
            {"op": "re_dispatch", "args": {"task_id": task_id, "to_agent": "A-auditor-3", "reason": "timeout"}},
        ],
    )


def case_dispatch_003() -> dict:
    dec = _decision("T-R1-001", node="injection/sqli", assignee=None, reason="empty_pool")
    contract = _contract("T-R1-001", dec, mode="pr", deadline_s=60)
    return _case(
        "dispatch-003", "dispatch",
        "SPEC-2 §2.3（D10/D19）；SPEC-5 §1.2/§4.3",
        "空池派单落 assignee=null 的 DISPATCH 事件且 tasks 留 queued、attempts 不消耗；"
        "expire_run 非终态（open/verified，D19）按字典序→expired 零存款、未完任务→failed、"
        "重复收尾幂等 []。",
        "空池派单 + run 收尾过期：零存款负断言与幂等双面。",
        {"runs": [{"run_id": "R1", "mode": "pr"}],
         "findings": [
             {"id": "F-20260101-000001", "run_id": "R1", "state": "open"},
             {"id": "F-20260101-000002", "run_id": "R1", "state": "verified"},
             {"id": "F-20260101-000003", "run_id": "R1", "state": "confirmed"},
         ]},
        [
            {"op": "create_task", "args": {"run_id": "R1", "role": "auditor",
                                           "node": "injection/sqli", "shard": {"paths": ["src/app/db.py"]}}},
            {"op": "build_contract", "args": {"decision": dec, "mode": "pr", "deadline_s": 60}},
            {"op": "dispatch", "args": {"task_id": "T-R1-001", "contract": contract, "decision": dec}},
            {"op": "expire_run", "args": {"run_id": "R1", "reason": "budget_breaker"}},
            {"op": "expire_run", "args": {"run_id": "R1", "reason": "budget_breaker"}},
        ],
    )


def case_dispatch_004() -> dict:
    return _case(
        "dispatch-004", "dispatch",
        "SPEC-2 §2.4/§10.2（D8/D9）；SPEC-5 §1.2/§4.3",
        "§10.2 迁移表逐字 + D8 直达边：open→voted→verified→confirmed 主链、"
        "open→confirmed（S3 直达）、voted→inconclusive；非法迁移/终态再流转/未知 finding"
        "一律 ValueError 零落库；kind=finding 事件 payload.state 承载新状态，零存款。",
        "结论状态机：迁移表合法边全走通 + 非法边负用例（FINDING_TRANSITIONS 突变的检出面）。",
        {"runs": [{"run_id": "R1", "mode": "full"}],
         "findings": [
             {"id": "F-20260101-000001", "run_id": "R1", "state": "open"},
             {"id": "F-20260101-000002", "run_id": "R1", "state": "open"},
             {"id": "F-20260101-000003", "run_id": "R1", "state": "voted"},
             {"id": "F-20260101-000004", "run_id": "R1", "state": "confirmed"},
         ]},
        [
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000001", "to_state": "voted"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000001", "to_state": "verified"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000001", "to_state": "confirmed"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000001", "to_state": "expired"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000002", "to_state": "confirmed"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000002", "to_state": "rejected"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000003", "to_state": "inconclusive"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000003", "to_state": "open"}},
            {"op": "transition_finding", "args": {"finding_id": "F-20260101-000004", "to_state": "expired"}},
            {"op": "transition_finding", "args": {"finding_id": "F-missing", "to_state": "expired"}},
        ],
    )


# ── re_route 类蓝图 ──────────────────────────────────────────────

def case_re_route_001() -> dict:
    return _case(
        "re_route-001", "re_route",
        "SPEC-2 §3.1/§3.2（D12/D13）；SPEC-5 §1.3/§4.3",
        "错配确定性比对触发；弱存款标准形：每 scout 一笔 S1/−1/0.05/taxonomy_path="
        "from_node 路径段/origin=prod/src_seq=re_route 事件 seq（多 scout 字典序）；"
        "cells 猜测类型格与家族影子格 neg_w 增 0.05（零账龄逐位）、n_eff_s2s3 不动；"
        "rebuild 重放等价 diff=0。",
        "标准形弱存款：re_route 事件 + 猜测格落点 + 重放等价（RE_ROUTE_WEAK_W 突变的检出面）。",
        {"runs": [{"run_id": "R1", "mode": "full"}],
         "findings": [{"id": "F-20260101-000001", "run_id": "R1", "family": "crypto", "type": None}],
         "events": [_deposit("S-scout-1", ["injection", "sqli"])]},
        [
            {"op": "agg_view", "args": {}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000001",
                                                "from_node": "injection/sqli", "to_node": "crypto",
                                                "scout_ids": ["S-scout-2", "S-scout-1"]}},
            {"op": "agg_view", "args": {}},
            {"op": "rebuild_check", "args": {}},
        ],
    )


def case_re_route_002() -> dict:
    return _case(
        "re_route-002", "re_route",
        "SPEC-2 §3.1/§3.2/§6.4（D12）；SPEC-5 §1.3/§4.3",
        "同 finding 第二次 re_route → 零新事件（读侧幂等）；家族级→类型级细化与全等"
        "节点 → ValueError 零落库（写侧非法，D12）；L0 直出无猜测者 → 仅 re_route"
        "事件零存款。",
        "幂等至多一次 + 触发门负用例 + L0 直出。",
        {"runs": [{"run_id": "R1", "mode": "pr"}],
         "findings": [
             {"id": "F-20260101-000001", "run_id": "R1"},
             {"id": "F-20260101-000002", "run_id": "R1", "family": "injection"},
         ]},
        [
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000001",
                                                "from_node": "injection/sqli", "to_node": "crypto",
                                                "scout_ids": ["S-b", "S-a"]}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000001",
                                                "from_node": "crypto", "to_node": "injection"}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000002",
                                                "from_node": "injection", "to_node": "injection/sqli"}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000002",
                                                "from_node": "crypto", "to_node": "crypto"}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000002",
                                                "from_node": "crypto", "to_node": "injection"}},
        ],
    )


def case_re_route_003() -> dict:
    def rep(cid: str, fam: str | None, typ: str | None = None) -> dict:
        return {"candidate_id": cid, "suspicious": True, "guess_family": fam,
                "guess_type": typ, "reason_ref": f"artifact://{cid}"}

    tie = [rep("c1", "injection", "sqli"), rep("c3", "injection", "xss")]
    return _case(
        "re_route-003", "re_route",
        "SPEC-2 §3.2/§6.5（D14 谱系接参）；SPEC-5 §1.3/§4.3",
        "猜测聚合全序裁决：票数主序；平手按猜测格 C* 之和（谱系经 scout_of 显式接参；"
        "无谱系/无格按 ROOT_PRIOR）；再平手路径字典序升序；guess_family 缺失不成路径；"
        "全无猜测 → None；纯函数零 rng。",
        "guess_taxonomy 排序确定性（口碑加权读数）。",
        {"runs": [{"run_id": "R1", "mode": "full"}],
         "events": [
             _deposit("S-good", ["injection", "sqli"]),
             _deposit("S-star", ["crypto", "aes_ecb"], weight=1.0),
         ]},
        [
            {"op": "guess_taxonomy", "args": {"reports": [rep("c1", "injection", "sqli"),
                                                          rep("c2", "injection", "sqli"),
                                                          rep("c3", "injection", "xss")],
                                              "scout_of": {"c3": "S-good"}}},
            {"op": "guess_taxonomy", "args": {"reports": tie, "scout_of": {"c1": "S-good"}}},
            {"op": "guess_taxonomy", "args": {"reports": tie}},
            {"op": "guess_taxonomy", "args": {"reports": [rep("c1", "injection"),
                                                          rep("c2", "injection"),
                                                          rep("c3", "injection", "xss")]}},
            {"op": "guess_taxonomy", "args": {"reports": [rep("c9", None, None)]}},
            {"op": "guess_taxonomy", "args": {"reports": tie, "scout_of": {"c1": "S-star"}}},
        ],
    )


def case_re_route_004() -> dict:
    return _case(
        "re_route-004", "re_route",
        "SPEC-2 §3.2/§6.3/§6.5；SPEC-5 §1.3/§4.3",
        "多 finding 各至多一次弱存款（不同 from_node 路径段）；收尾全账本 rebuild 重放"
        "等价 diff=0；tasks/findings 表投影与 deposit 计数冻结。",
        "多 finding 弱存款 + 全账本重放等价 + 表投影。",
        {"runs": [{"run_id": "R1", "mode": "full"}],
         "tasks": [{"task_id": "T-R1-001", "run_id": "R1", "node": "injection/sqli",
                    "state": "dispatched", "assignee": "A-auditor-1", "attempts": 1}],
         "findings": [
             {"id": "F-20260101-000001", "run_id": "R1"},
             {"id": "F-20260101-000002", "run_id": "R1", "family": "crypto", "type": None},
         ],
         "events": [
             _deposit("S-scout-1", ["injection", "sqli"]),
             _deposit("S-scout-2", ["crypto", "aes_ecb"]),
         ]},
        [
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000001",
                                                "from_node": "injection/sqli", "to_node": "crypto",
                                                "scout_ids": ["S-scout-1", "S-scout-2"]}},
            {"op": "re_route_finding", "args": {"finding_id": "F-20260101-000002",
                                                "from_node": "crypto/aes_ecb", "to_node": "injection/xss",
                                                "scout_ids": ["S-scout-1"]}},
            {"op": "rebuild_check", "args": {}},
            {"op": "agg_view", "args": {}},
        ],
    )


CASES = {
    "dispatch": [case_dispatch_001, case_dispatch_002, case_dispatch_003, case_dispatch_004],
    "re_route": [case_re_route_001, case_re_route_002, case_re_route_003, case_re_route_004],
}


def _update_manifest(kinds_written: dict[str, int]) -> None:
    """顶层 manifest 并轨（budget 先例）：counts 增类、total 重算、regen_m1_commands 登记。"""
    path = gl.GOLDEN_DIR / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    counts = manifest.setdefault("counts", {})
    for kind, n in kinds_written.items():
        counts[kind] = n
    known = set(counts) - {"total"}
    counts["total"] = sum(
        len(list((gl.GOLDEN_DIR / k).glob("*.json"))) - (1 if (gl.GOLDEN_DIR / k / "manifest.json").exists() else 0)
        for k in known
    )
    # regen_m1_commands 登记（provenance 测试读该表）
    regen = manifest.setdefault("regen_m1_commands", {})
    for kind in kinds_written:
        regen[kind] = _REGEN
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(f"[freeze-m1] manifest 更新：counts={ {k: counts[k] for k in sorted(known)} }")


def _freeze() -> None:
    written: dict[str, int] = {}
    for kind, builders in CASES.items():
        out_dir = gl.GOLDEN_DIR / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for build in builders:
            case = build()
            case["expected"] = gl.execute_case(case)   # 与回归测试同一执行本体
            payload = {k: v for k, v in case.items() if v is not None}
            (out_dir / f"{case['case_id']}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            n += 1
        written[kind] = n
        print(f"[freeze-m1] {kind}: {n} cases")
    _update_manifest(written)


def _check() -> int:
    ok = True
    for kind in ("dispatch", "re_route"):
        for path in gl.case_files(kind):
            case = gl.load_case(path)
            diffs = gl.check_case(case)
            if diffs:
                ok = False
                print(f"[freeze-m1] FAIL {case['case_id']}（{len(diffs)} 处）:")
                for d in diffs[:10]:
                    print(f"  {d}")
            else:
                print(f"[freeze-m1] PASS {case['case_id']}")
    print("FREEZE_M1 CHECK " + ("ALL OK" if ok else "FAILED"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="只重放自检，不落盘")
    args = ap.parse_args()
    if args.check:
        return _check()
    _freeze()
    print("[freeze-m1] done；自检：", file=sys.stderr)
    return _check()


if __name__ == "__main__":
    raise SystemExit(main())
