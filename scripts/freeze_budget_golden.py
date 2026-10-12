#!/usr/bin/env python3
"""freeze_budget_golden：确定性冻结 M1 预算熔断黄金用例（SPEC-5 §1.4/§4.3）。

与 freeze_golden.py 的轨迹抽样不同，budget 黄金是**合成规格用例**：输入 = SPEC-2
§4 逐字口径的字面值（三档配额边界、D16 等号熔断、D15 排序键、D17 首 trip 事件），
expected = 当前内核在同一确定性执行语义下的输出（经 tests/golden_loader 的 budget
执行器），落盘前逐条自检（任何一条不一致即中止，不产出坏夹具）。

三个夹具的钉点（目录 tests/golden/budget/，命名 <kind>-NNN.json）：

- budget-001「三档边界」：budget_for_mode 三档表、quota_l2/quota_s2 全边界
  （0 成本 / 0 余额 / cap 等号 / 地板除）、s2_rank_key 字典序与 scarcity、
  batch_eligible 档位表、budget_state 未熔断区间的配额重算；
- budget-002「等号熔断」：spent == budget 恰压线触发（D16 含等号）——首次 trip
  落唯一 SCALING(pool=f"mode:full", delta=-1, reason="budget_breaker")、
  runs.status=degraded、阶梯 full→pr、配额归零（D17/§4.3）；
- budget-003「幂等与阶梯」：pr 档熔断→fast 到底、重复 trip 零新事件（幂等）、
  超 negative remaining 恒 0、batch_eligible 全档对照。

用法::

    python3 scripts/freeze_budget_golden.py            # 写入 tests/golden/budget/
    python3 scripts/freeze_budget_golden.py --check    # 只重放自检，不落盘

同 (输入字面值, 内核实现) ⇒ 输出逐字节一致；无墙钟时间入场（FixedClock 钉死）。
期望不含 seq/ts（账本记账产物，见 manifest.seq_policy）。
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

GOLDEN_BUDGET_DIR = gl.GOLDEN_DIR / "budget"

CLOCK = "2026-10-10T00:00:00Z"

SPEC_REF = "SPEC-2 §4.2/§4.3（D15/D16/D17）；SPEC-5 §1.4"

# 三档全档 policy 字面（§8.1 成本锚点缺省：cap=45、cost 5/10）。
POL_FULL = {"mode": "full", "budget_cents": 600, "cost_per_l2_cents": 5,
            "cost_per_s2_cents": 10, "l2_cap": 45}
POL_PR = {"mode": "pr", "budget_cents": 100, "cost_per_l2_cents": 5,
          "cost_per_s2_cents": 10, "l2_cap": 45}
POL_FAST = {"mode": "fast", "budget_cents": 2, "cost_per_l2_cents": 5,
            "cost_per_s2_cents": 10, "l2_cap": 45}


def _case_budget_001() -> dict:
    """三档边界：配额公式全边界 + 排序键 + 档位表 + 未熔断区间配额重算。"""
    steps = [
        {"op": "budget_for_mode", "mode": "fast"},
        {"op": "budget_for_mode", "mode": "pr"},
        {"op": "budget_for_mode", "mode": "full"},
        # quota_l2 边界（§4.2 逐字：min(cap, ⌊remaining/cost⌋)）
        {"op": "quota_l2", "remaining": 100, "cost": 5, "cap": 45},    # 20
        {"op": "quota_l2", "remaining": 99, "cost": 5, "cap": 45},     # 19 地板
        {"op": "quota_l2", "remaining": 1000, "cost": 5, "cap": 45},   # 45 cap 截断
        {"op": "quota_l2", "remaining": 225, "cost": 5, "cap": 45},    # 45 恰压 cap
        {"op": "quota_l2", "remaining": 224, "cost": 5, "cap": 45},    # 44 cap 之下
        {"op": "quota_l2", "remaining": 100, "cost": 0, "cap": 45},    # 0 cost==0
        {"op": "quota_l2", "remaining": 100, "cost": -5, "cap": 45},   # 0 cost<0
        {"op": "quota_l2", "remaining": 0, "cost": 5, "cap": 45},      # 0 remaining==0
        {"op": "quota_l2", "remaining": -1, "cost": 5, "cap": 45},     # 0 remaining<0
        {"op": "quota_l2", "remaining": 100, "cost": 5, "cap": 0},     # 0 cap==0
        # quota_s2 边界（⌊存活数×remaining/cost⌋）
        {"op": "quota_s2", "survivors": 3, "remaining": 100, "cost": 10},   # 30
        {"op": "quota_s2", "survivors": 7, "remaining": 99, "cost": 10},    # 69 地板
        {"op": "quota_s2", "survivors": 1, "remaining": 10, "cost": 10},    # 1 恰好
        {"op": "quota_s2", "survivors": 3, "remaining": 100, "cost": 0},    # 0
        {"op": "quota_s2", "survivors": 3, "remaining": 0, "cost": 10},     # 0
        {"op": "quota_s2", "survivors": 3, "remaining": -1, "cost": 10},    # 0
        {"op": "quota_s2", "survivors": 0, "remaining": 100, "cost": 10},   # 0
        # s2_rank_key：字典序（severity 主序 / scarcity / confidence；D15 不取乘积）
        {"op": "s2_rank_key", "severity": "critical", "type_node": None,
         "confidence": 0.1, "counts": {}},
        {"op": "s2_rank_key", "severity": "high", "type_node": "sqli",
         "confidence": 0.7, "counts": {"sqli": 2}},
        {"op": "s2_rank_key", "severity": "high", "type_node": "xss",
         "confidence": 0.7, "counts": {"sqli": 2}},
        {"op": "s2_rank_key", "severity": "high", "type_node": "sqli",
         "confidence": 0.0, "counts": {"sqli": 99}},
        {"op": "s2_rank_key", "severity": "low", "type_node": "xss",
         "confidence": 0.99, "counts": {}},
        {"op": "s2_rank_key", "severity": "medium", "type_node": None,
         "confidence": 0.5, "counts": {"sqli": 0}},
        # batch_eligible：full 独占（§4.1）
        {"op": "batch_eligible", "policy": POL_FULL},
        {"op": "batch_eligible", "policy": POL_PR},
        {"op": "batch_eligible", "policy": POL_FAST},
        # budget_state 未熔断区间：quota 按剩余预算重算（§4.2）
        {"op": "policy_state", "spent": 0, "policy": POL_FULL},
        {"op": "policy_state", "spent": 100, "policy": POL_FULL},
        {"op": "policy_state", "spent": 599, "policy": POL_FULL},
    ]
    return {
        "case_id": "budget-001",
        "kind": "budget",
        "meta": {
            "origin": "spec_literal",
            "spec_ref": SPEC_REF,
            "pins": "三档边界（含等号）：quota_l2/quota_s2 全边界、s2_rank_key 字典序、batch_eligible 档位表、未熔断配额重算",
        },
        "inputs": {
            "setup": {"run_id": "R-GOLDEN-B1", "mode": "full",
                      "budget_cents": 600, "clock": CLOCK},
            "steps": steps,
        },
        "expected": None,
    }


def _case_budget_002() -> dict:
    """等号熔断：spent==budget 恰压线触发首 trip（D16/D17）；阶梯 full→pr。"""
    pol = {"mode": "full", "budget_cents": 10, "cost_per_l2_cents": 5,
           "cost_per_s2_cents": 10, "l2_cap": 45}
    steps = [
        {"op": "policy_state", "spent": 9, "policy": pol},    # 差 1：不熔断
        {"op": "on_cost", "cents": 4, "policy": pol},         # 4 < 10：零事件
        {"op": "policy_state", "spent": 10, "policy": pol},   # 等号判定（纯函数）
        {"op": "on_cost", "cents": 6, "policy": pol},         # 4+6 == 10：首 trip
        {"op": "on_cost", "cents": 1, "policy": pol},         # 重复 trip：零新事件
    ]
    return {
        "case_id": "budget-002",
        "kind": "budget",
        "meta": {
            "origin": "spec_literal",
            "spec_ref": SPEC_REF,
            "pins": "D16 等号熔断：首 trip 唯一 SCALING(pool=mode:full,delta=-1,reason=budget_breaker)+runs.status=degraded 同事务；重复 trip 零新事件；full→pr 阶梯；配额归零",
        },
        "inputs": {
            "setup": {"run_id": "R-GOLDEN-B2", "mode": "full",
                      "budget_cents": 10, "clock": CLOCK},
            "steps": steps,
        },
        "expected": None,
    }


def _case_budget_003() -> dict:
    """幂等与阶梯：pr→fast 到底、超支恒 0、batch_eligible 对照、fast 到底。"""
    pol_pr = {"mode": "pr", "budget_cents": 5, "cost_per_l2_cents": 5,
              "cost_per_s2_cents": 10, "l2_cap": 45}
    pol_fast = {"mode": "fast", "budget_cents": 2, "cost_per_l2_cents": 5,
                "cost_per_s2_cents": 10, "l2_cap": 45}
    steps = [
        {"op": "on_cost", "cents": 5, "policy": pol_pr, "note": "pr-trip"},  # 5==5 trip
        {"op": "on_cost", "cents": 3, "policy": pol_pr},                     # 超支零事件
        {"op": "on_cost", "cents": 3, "policy": pol_pr},                     # 再重复
        {"op": "policy_state", "spent": 8, "policy": pol_pr},   # remaining=-3 恒 0
        {"op": "policy_state", "spent": 2, "policy": pol_fast},  # fast 等号 trip
        {"op": "policy_state", "spent": 3, "policy": pol_fast},  # fast 到底仍 fast
        {"op": "batch_eligible", "policy": pol_pr},
        {"op": "batch_eligible", "policy": pol_fast},
    ]
    return {
        "case_id": "budget-003",
        "kind": "budget",
        "meta": {
            "origin": "spec_literal",
            "spec_ref": SPEC_REF,
            "pins": "pr→fast 阶梯到底；重复 trip 幂等零新事件；负 remaining 配额恒 0；fast 档等号熔断；审计注记 note 落 runs.meta_json（不进 SCALING 词表）",
        },
        "inputs": {
            "setup": {"run_id": "R-GOLDEN-B3", "mode": "pr",
                      "budget_cents": 5, "clock": CLOCK},
            "steps": steps,
        },
        "expected": None,
    }


def build_cases() -> list[dict]:
    return [_case_budget_001(), _case_budget_002(), _case_budget_003()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="只重放自检并与盘上夹具逐字节比对，不落盘")
    args = parser.parse_args(argv)

    manifest = gl.load_manifest()
    params = gl.manifest_params(manifest)
    cases = build_cases()
    ok = True
    for case in cases:
        expected = gl._run_budget(case, manifest, params)
        path = GOLDEN_BUDGET_DIR / f"{case['case_id']}.json"
        if args.check:
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            diffs = gl.diff_doc(case["case_id"], on_disk["expected"], expected)
            if diffs:
                ok = False
                print(f"[CHECK-FAIL] {case['case_id']}:")
                for d in diffs[:10]:
                    print(f"  {d}")
            else:
                print(f"[CHECK-OK] {case['case_id']} 与盘上夹具逐位一致")
        else:
            case["expected"] = expected
            GOLDEN_BUDGET_DIR.mkdir(parents=True, exist_ok=True)
            text = json.dumps(case, ensure_ascii=False, indent=1, sort_keys=True)
            path.write_text(text + "\n", encoding="utf-8")
            # 落盘前自检：重新装载并全量重放比对（不产出坏夹具）。
            reloaded = json.loads(path.read_text(encoding="utf-8"))
            diffs = gl.check_case(reloaded, manifest=manifest, params=params)
            if diffs:
                ok = False
                print(f"[SELF-CHECK-FAIL] {case['case_id']}: {diffs[:5]}")
            else:
                print(f"[FROZEN] {path.relative_to(_REPO_ROOT)}（自检通过）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
