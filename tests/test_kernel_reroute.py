"""kernel.reroute 单元测试：re_route 事件与猜测格弱存款（SPEC-2 §3、SPEC-5 §1.3）。

钉子（本子任务验收 ac 弱存款行）：
- 标准形：每 scout 一笔 S1 / −1 / 0.05 / taxonomy_path=from_node 路径段 / origin=prod /
  src_seq=re_route 事件 seq（§3.2/D13，SPEC-5 §5.2 表末行）；
- 弱存款落入侦察蜂类型猜测格：cells 投影 (scout, 'scout', 猜测node) 的 neg_w 增量恰
  0.05（零账龄 FixedClock 下逐位 0.05），家族影子格同落（node_prefixes 影子记账），
  n_eff_s2s3 恒不动（S1 不计晋升硬门槛，「弱」的结构化表达，D13）；
- 重放等价：任意前缀 rebuild_cells == 在线投影（§6.3/§6-6）+ 两次独立运行事件流
  逐位一致（§6.5 确定性）。
"""

from __future__ import annotations

import pytest

import m1_harness as h
from qswarm.contracts import DepositOrigin, LedgerKind, ScoutReport, SignalTier
from qswarm.kernel import (
    CellKey,
    KernelLedger,
    RE_ROUTE_WEAK_W,
    agg_cells,
    diff_cells,
    guess_taxonomy,
    is_mismatch,
    re_route_finding,
    rebuild_cells,
)
from qswarm.kernel.ledger import FixedClock
from qswarm.kernel.reroute import _split_node

CLOCK_TS = "2026-01-01T00:00:00Z"


def new_ledger() -> KernelLedger:
    return KernelLedger(":memory:", clock=FixedClock(CLOCK_TS))


def seed_scout_reputation(led: KernelLedger, agent: str = "S-scout-1", weight: float = 0.5):
    """给侦察蜂一点正口碑（S2 正存款于猜测格），供 guess_taxonomy 的 C* 加权读数。"""
    led.append(
        LedgerKind.DEPOSIT,
        {
            "finding": None, "cluster": None, "agent": agent, "role": "scout",
            "taxonomy_path": ["injection", "sqli"], "signal_tier": "S2",
            "direction": 1, "weight": weight, "origin": "prod",
        },
    )


# ── §3.1 触发裁定 ────────────────────────────────────────────────


def test_is_mismatch_rules():
    """SPEC-5 §1.3：家族段不同=错配；同族双类型段不同=错配；家族级→类型级细化=不算
    （§3.1/D12）。"""
    assert is_mismatch("injection/sqli", "crypto") is True            # 家族段不同
    assert is_mismatch("injection/sqli", "injection/xss") is True     # 同族双类型段不同
    assert is_mismatch("crypto", "crypto/aes_ecb") is False           # 细化不是错配
    assert is_mismatch("crypto/aes_ecb", "crypto") is False           # 单侧缺类型段不构成
    assert is_mismatch("crypto/aes_ecb", "crypto/aes_ecb") is False   # 全等
    assert is_mismatch("crypto", "crypto") is False
    assert is_mismatch("crypto", "injection") is True                 # 家族级互异仍算错配
    with pytest.raises(ValueError, match="非空家族"):
        is_mismatch("/sqli", "crypto")
    assert _split_node("injection/sqli") == ("injection", "sqli")
    assert _split_node("crypto") == ("crypto", None)


# ── §3.2 弱存款标准形 ────────────────────────────────────────────


def test_re_route_finding_weak_deposit_standard_form():
    """SPEC-5 §1.3：每 scout 一笔：S1 / −1 / 0.05 / taxonomy_path=from_node 路径段 /
    origin=prod / src_seq=re_route 事件 seq（§3.2/D13）；多 scout 字典序落账。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        h.seed_finding(led, "F-20260101-000001", family="crypto", type_=None)
        events = re_route_finding(
            led, "F-20260101-000001",
            from_node="injection/sqli", to_node="crypto",
            scout_ids=["S-scout-2", "S-scout-1"],
        )
        assert [e.kind for e in events] == [LedgerKind.RE_ROUTE, LedgerKind.DEPOSIT, LedgerKind.DEPOSIT]
        reroute_ev = events[0]
        assert reroute_ev.payload == {
            "finding_id": "F-20260101-000001",
            "from_node": "injection/sqli",
            "to_node": "crypto",
            "reason": "type_mismatch",
        }
        assert [e.payload["agent"] for e in events[1:]] == ["S-scout-1", "S-scout-2"]  # 字典序
        for dep in events[1:]:
            assert dep.payload == {
                "finding": "F-20260101-000001",
                "cluster": None,
                "agent": dep.payload["agent"],
                "role": "scout",
                "taxonomy_path": ["injection", "sqli"],      # 被证伪的路由初值路径段
                "signal_tier": "S1",
                "direction": -1,
                "weight": RE_ROUTE_WEAK_W,
                "origin": "prod",
                "src_seq": reroute_ev.seq,
            }
        assert RE_ROUTE_WEAK_W == 0.05                       # §6.5 附带逐字
        # 空 scout_ids（L0 直出无猜测者）→ 仅 re_route 事件，零存款
        h.seed_finding(led, "F-20260101-000002", family="crypto", type_=None)
        solo = re_route_finding(
            led, "F-20260101-000002", from_node="injection/sqli", to_node="crypto",
        )
        assert [e.kind for e in solo] == [LedgerKind.RE_ROUTE]
    finally:
        led.close()


def test_re_route_finding_at_most_once():
    """SPEC-5 §1.3：同 finding 已有 re_route 事件 → 零新事件（§3.2/§6.4，读侧幂等）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        h.seed_finding(led, "F-20260101-000001")
        first = re_route_finding(led, "F-20260101-000001", from_node="injection/sqli",
                                 to_node="crypto", scout_ids=["S-scout-1"])
        assert len(first) == 2                                # 事件 + 一笔存款
        n = len(led.read())
        assert re_route_finding(led, "F-20260101-000001", from_node="crypto", to_node="injection") == []
        assert len(led.read()) == n                           # 零新事件
        # 幂等优先于触发守卫：非错配入参同样只返回 []，不抛
        assert re_route_finding(led, "F-20260101-000001", from_node="crypto", to_node="crypto/x") == []
    finally:
        led.close()


def test_re_route_refinement_not_mismatch_raises():
    """D12 落位：非错配（含家族级→类型级细化）→ ValueError 零落库——内核确定性比对
    在唯一公共入口强制。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        h.seed_finding(led, "F-20260101-000001")
        n = len(led.read())
        with pytest.raises(ValueError, match="非错配不触发"):
            re_route_finding(led, "F-20260101-000001", from_node="injection", to_node="injection/sqli")
        with pytest.raises(ValueError, match="非错配不触发"):
            re_route_finding(led, "F-20260101-000001", from_node="crypto", to_node="crypto")
        assert len(led.read()) == n                           # 零落库
    finally:
        led.close()


# ── ac 弱存款行：猜测格落点 + n_eff_s2s3 不动 + 重放等价 ─────────


def test_weak_deposit_lands_in_scout_guess_cells():
    """−0.05 落入侦察蜂类型猜测格：(S, scout, 猜测node) 的 neg_w 增量恰 0.05，家族影子格
    同落；n_eff_s2s3 恒不动（S1 不计硬门槛，D13）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        seed_scout_reputation(led, "S-scout-1")               # 既有正口碑 S2 0.5
        h.seed_finding(led, "F-20260101-000001")
        re_route_finding(
            led, "F-20260101-000001",
            from_node="injection/sqli", to_node="crypto", scout_ids=["S-scout-1"],
        )
        view = agg_cells(led, FixedClock(CLOCK_TS).now())     # 零账龄：w_eff 逐位 = w
        guess_type_cell = view.get("S-scout-1", "scout", "injection/sqli")
        guess_family_cell = view.get("S-scout-1", "scout", "injection")
        assert guess_type_cell is not None and guess_family_cell is not None
        assert guess_type_cell.neg_w == 0.05                  # 逐位 0.05（§6.5 附带）
        assert guess_family_cell.neg_w == 0.05                # 影子记账同落（§3.3/§7.1）
        assert guess_type_cell.n_eff == 2.0 * (0.5 + 0.05)    # n_eff ≡ 2×(pos+neg)（§6.1）
        assert guess_type_cell.n_eff_s2s3 == 1.0              # 仅既有 S2 存款；弱存款不进硬门槛
        # 无既有口碑的侦察蜂：猜测格照样建格（lazy allocation）
        h.seed_finding(led, "F-20260101-000002")
        re_route_finding(
            led, "F-20260101-000002",
            from_node="crypto", to_node="injection/sqli", scout_ids=["S-scout-new"],
        )
        view = agg_cells(led, FixedClock(CLOCK_TS).now())
        fresh = view.get("S-scout-new", "scout", "crypto")
        assert fresh is not None and fresh.neg_w == 0.05 and fresh.n_eff_s2s3 == 0.0
    finally:
        led.close()


def test_re_route_replay_equivalence():
    """重放等价（§6.3/§6-6）：任意前缀 rebuild_cells == 「该前缀事件重放的在线折叠」，
    diff 逐位空；收尾全账本重放 == 全量在线投影。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        seed_scout_reputation(led, "S-scout-1")
        seed_scout_reputation(led, "S-scout-2")
        h.seed_finding(led, "F-20260101-000001")
        h.seed_finding(led, "F-20260101-000002")
        re_route_finding(led, "F-20260101-000001", from_node="injection/sqli", to_node="crypto",
                         scout_ids=["S-scout-1", "S-scout-2"])
        re_route_finding(led, "F-20260101-000002", from_node="crypto", to_node="injection/xss",
                         scout_ids=["S-scout-1"])
        online = agg_cells(led, FixedClock(CLOCK_TS).now())
        latest = led.latest_seq()
        all_events = led.read()
        for upto in (0, latest // 2, latest):                 # 任意时点前缀
            # 前缀账本逐事件重放（payload 为只读共享 dict，直接交 append 校验）
            replica = KernelLedger(":memory:", clock=FixedClock(CLOCK_TS))
            try:
                for ev in all_events:
                    if ev.seq > upto:
                        break
                    replica.append(ev.kind, dict(ev.payload), run_id=ev.run_id)
                prefix_online = agg_cells(replica, FixedClock(CLOCK_TS).now())
                assert diff_cells(prefix_online, rebuild_cells(led, upto)) == [], f"upto={upto}"
            finally:
                replica.close()
        # 收尾全账本从空账重放：与在线逐位一致
        assert diff_cells(online, rebuild_cells(led, None)) == []
    finally:
        led.close()


def test_re_route_deterministic_event_stream():
    """§6.5 确定性：同 (输入事件序, 操作序) 两次独立运行 → 事件流逐位一致（剔 ts）。"""
    def run_once() -> list[tuple[str, dict]]:
        led = new_ledger()
        try:
            h.seed_run(led)
            seed_scout_reputation(led, "S-scout-2")
            h.seed_finding(led, "F-20260101-000001")
            re_route_finding(led, "F-20260101-000001", from_node="injection/sqli",
                             to_node="crypto", scout_ids=["S-b", "S-a"])
            return [(e.kind.value, e.payload) for e in led.read()]
        finally:
            led.close()

    assert run_once() == run_once()


# ── §3.2 guess_taxonomy ──────────────────────────────────────────


def test_guess_taxonomy_ranking_deterministic():
    """SPEC-5 §1.3：票数 → 猜测格 C* 之和（无格 ROOT_PRIOR）→ 字典序；无猜测 None；
    纯函数零 rng（§3.2）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        seed_scout_reputation(led, "S-good")                  # injection/sqli 上高口碑
        view = agg_cells(led, FixedClock(CLOCK_TS).now())

        def rep(cid: str, fam: str | None, typ: str | None = None) -> ScoutReport:
            return ScoutReport(
                candidate_id=cid, suspicious=True,
                guess_family=fam, guess_type=typ, reason_ref=f"artifact://{cid}",
            )

        # 票数主序：2 票 sqli 胜 1 票 xss（即便 xss 的猜测蜂口碑更高）
        reports = [rep("c1", "injection", "sqli"), rep("c2", "injection", "sqli"),
                   rep("c3", "injection", "xss")]
        assert guess_taxonomy(reports, view, scout_of={"c3": "S-good"}) == ("injection", "sqli")
        # 票数平手 → 猜测格 C* 和：sqli（S-good 有格，C* > ROOT_PRIOR）胜 xss（无格 ROOT_PRIOR）
        reports_tie = [rep("c1", "injection", "sqli"), rep("c3", "injection", "xss")]
        assert guess_taxonomy(reports_tie, view, scout_of={"c1": "S-good"}) == ("injection", "sqli")
        # C* 和也平手（都无谱系/无格）→ 路径字典序升序
        assert guess_taxonomy(reports_tie, view) == ("injection", "sqli")     # 'injection/sqli' < 'injection/xss'
        tie2 = [rep("c1", "injection", "xss"), rep("c2", "injection", "sqli")]
        assert guess_taxonomy(tie2, view) == ("injection", "sqli")
        # 家族级猜测参与聚合：票数 2 的 family 猜测胜 1 票类型猜测
        fam_vs_type = [rep("c1", "injection"), rep("c2", "injection"), rep("c3", "injection", "xss")]
        assert guess_taxonomy(fam_vs_type, view) == ("injection",)
        # guess_family 缺失的报告不成路径；全无猜测 → None
        assert guess_taxonomy([rep("c9", None, None)], view) is None
        assert guess_taxonomy([], view) is None
        # 同输入两次调用逐位一致（纯函数零 rng）
        assert guess_taxonomy(reports, view, scout_of={"c3": "S-good"}) == guess_taxonomy(
            reports, view, scout_of={"c3": "S-good"}
        )
    finally:
        led.close()


def test_guess_cell_c_star_weighting_uses_reputation():
    """口碑加权的直接读数：同一票面下，猜测格 C* 之和随猜测蜂口碑单调——高口碑蜂的
    猜测胜出（§6.5「路由初值随 guesses 口碑加权」）。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        seed_scout_reputation(led, "S-star", weight=1.0)      # crypto 格高口碑
        view = agg_cells(led, FixedClock(CLOCK_TS).now())
        rep_a = ScoutReport(candidate_id="c1", suspicious=True, guess_family="injection",
                            guess_type="sqli", reason_ref="a://1")
        rep_b = ScoutReport(candidate_id="c2", suspicious=True, guess_family="crypto",
                            guess_type="aes_ecb", reason_ref="a://2")
        tie_votes = [rep_a, rep_b]                            # 1:1 平票
        # 无谱系：双方都 ROOT_PRIOR → 字典序 crypto < injection
        assert guess_taxonomy(tie_votes, view) == ("crypto", "aes_ecb")
        # 高口碑蜂猜 crypto → crypto 胜；高口碑蜂猜 injection → injection 胜
        assert guess_taxonomy(tie_votes, view, scout_of={"c2": "S-star"}) == ("crypto", "aes_ecb")
        assert guess_taxonomy(tie_votes, view, scout_of={"c1": "S-star"}) == ("injection", "sqli")
    finally:
        led.close()


def test_weak_deposit_cell_key_shape():
    """猜测格键形状守卫：(agent, 'scout', 'family[/type]')——与 §6.5 附带猜测格定义逐字。"""
    led = new_ledger()
    try:
        h.seed_run(led)
        h.seed_finding(led, "F-20260101-000001")
        re_route_finding(led, "F-20260101-000001", from_node="injection/sqli",
                         to_node="crypto", scout_ids=["S"])
        view = agg_cells(led, FixedClock(CLOCK_TS).now())
        assert CellKey("S", "scout", "injection/sqli") in view.cells
        assert CellKey("S", "scout", "injection") in view.cells
        assert CellKey("S", "auditor", "injection/sqli") not in view.cells
    finally:
        led.close()
