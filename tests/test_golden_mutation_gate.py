"""突变门禁：人为改动内核任一公式 ⇒ 至少一条黄金用例失败（ac_muz07uljvf4rvnld）。

黄金回归集的价值不在「全绿」，而在「该红必红」。本文件对内核机制层的每个核心
公式族各构造一个人工突变（替换实现，语义等同一次真实的手改代码），在突变生效
下重放黄金用例比对，断言**至少一条失配**——即回归集对该公式有检出灵敏度：

1. decay_factor   指数挥发半衰期减半（§6.4）；
2. CellView.c_star  层级收缩公式替换为不收缩的本层 C_raw（§5.1）；
3. adaptive_tilt  自适应 TILT 优势边际放大 2 倍（§5.2）；
4. wilson_bounds  Wilson 区间 z 1.64→2.5（§7.3 判据②/衰退判据）；
5. _evaluate      晋升硬门槛 n_eff ≥ N 加严为 ≥ 2N（§7.3 判据①）;
6. _decay_below_baseline  衰退熄灭判定恒真（§7.3 转换③）；
7. node_prefixes  影子记账只落类型格、不再同落家族格（§3.3/§7.1）；
8. beta_posterior TS 对照臂 Beta 先验 λ/4→λ/2（route_thompson，SPEC-4 §3 实验臂）；
9. posterior_mean TS 对照臂后验均值 α/(α+β)→α/(2α+β)（点估计位系统偏置）。

补丁打在公式本体（qswarm.kernel.cells / promote_scan / route / route_thompson 的
模块属性与 CellView 类属性）——与「手改内核源码的公式行」等效；monkeypatch 保证
逐用例还原。

覆盖边界（诚实声明）：突变灵敏度以**冻结轨迹的可观测面**为准。seed=7/30 日轨迹
未产生任何 demotion 账本事件（衰退/休眠分支未触发，DORMANT_DAYS=60 > 30 日视野），
故「抑制向」的衰退/休眠突变（如恒 False）在该轨迹面上原理性不可检出；加严向
（恒 True）已由 6 号突变覆盖。test_demotion_surface_guard 保证：一旦未来重冻的
轨迹出现 demotion 事件，promote 夹具必须抽中含 demotion 期望的夜（抽样器已有
保底逻辑，此处是回归防线）。"""

from dataclasses import replace
import importlib

import pytest

import golden_loader as gl
from qswarm.contracts import DepositOrigin

MANIFEST = gl.load_manifest()
PARAMS = gl.manifest_params(MANIFEST)

_SECONDS_PER_DAY = 86400.0


def _kernel_module(name: str):
    """取 kernel 子模块本体。

    注意 ``from qswarm.kernel import promote_scan / route`` 拿到的是包 __init__
    再导出的**函数**（同名遮蔽），打补丁必须用 sys.modules 里的模块对象。
    """
    import qswarm.kernel                                  # noqa: F401 确保包已加载
    return importlib.import_module(f"qswarm.kernel.{name}")


# ── 用例执行序：小切片在前（尽早命中失败，控制门禁耗时）──────────

def _ordered_cases():
    ordered = []
    for kind in gl.ALL_KINDS:
        for p in gl.case_files(kind):
            case = gl.load_case(p)
            slice_id = case.get("slice")
            # M1 合成规格用例无切片（inputs 自含、执行极快）：排最前优先检出。
            n = gl.load_slice(slice_id)["n_events"] if slice_id else 0
            ordered.append((n, case))
    ordered.sort(key=lambda t: t[0])
    return [case for _, case in ordered]


_ORDERED = _ordered_cases()


# ── 突变实现（每条 = 一个被改动的内核公式）───────────────────────

def _mut_decay_half_life(monkeypatch):
    """§6.4 指数挥发：T_half 减半（w_eff = w·2^(−age/(T_half/2))）。"""
    cells_mod = _kernel_module("cells")
    ps_mod = _kernel_module("promote_scan")

    def fast_decay(origin, age_s, *, decay):
        t_half = (
            decay.T_HALF_DAYS_EXAM
            if DepositOrigin(origin) is DepositOrigin.EXAM
            else decay.T_HALF_DAYS
        ) / 2.0
        return 2.0 ** (-age_s / (t_half * _SECONDS_PER_DAY))

    monkeypatch.setattr(cells_mod, "decay_factor", fast_decay)
    monkeypatch.setattr(ps_mod, "decay_factor", fast_decay)


def _mut_shrink_flat(monkeypatch):
    """§5.1 层级收缩：C* 公式被替换为「不收缩，直接取本层 C_raw」。"""
    cells_mod = _kernel_module("cells")

    def c_star_flat(self, agent_id, role, node=None, *, root_prior=cells_mod.ROOT_PRIOR):
        m = self._mass(agent_id, role, node)
        if m.total_w < self.params.decay.EPS_FLOOR:
            return root_prior
        return m.pos_w / m.total_w

    monkeypatch.setattr(cells_mod.CellView, "c_star", c_star_flat)


def _mut_tilt_gain_doubled(monkeypatch):
    """§5.2 自适应 TILT：raw = T_BASE + 2·GAMMA·(top − base)（边际放大 2 倍）。"""
    route_mod = _kernel_module("route")

    def doubled(top, base, *, tilt):
        raw = tilt.T_BASE + 2.0 * tilt.GAMMA * (top - base)
        return raw, min(tilt.T_MAX, max(tilt.T_MIN, raw))

    monkeypatch.setattr(route_mod, "adaptive_tilt", doubled)


def _mut_wilson_z(monkeypatch):
    """§7.3 Wilson 区间：z 由 1.64 改为 2.5（LB/UB 全变）。"""
    ps_mod = _kernel_module("promote_scan")
    orig = ps_mod.wilson_bounds

    def wide(pos_w, neg_w, *, z=ps_mod.WILSON_Z):
        return orig(pos_w, neg_w, z=2.5)

    monkeypatch.setattr(ps_mod, "wilson_bounds", wide)


def _mut_promote_hard_gate(monkeypatch):
    """§7.3 判据①：硬门槛 n_eff ≥ N 加严到当前黄金面不可满足的强度。

    加严基数取 2N；若冻结面的判据快照存在更强的已观测 n_eff（真实聚类接通后
    证据路径转换变稀，面上判据快照的 n_eff 可能 ≥ 2N），则加严到「严格高于面上
    最大已观测 n_eff」——突变检测的判据是「至少一条期望流转被翻转」，固定倍数
    在稀疏判据面上不可观测（乘数随面走，机制公式本身不被修改）。
    """
    ps_mod = _kernel_module("promote_scan")
    max_observed = 0.0
    for p in gl.case_files("promote_scan"):
        for t in gl.load_case(p)["expected"]["transitions"]:
            c = t.get("criteria") or {}
            max_observed = max(max_observed, float(c.get("n_eff") or 0.0))
    threshold = max(2.0 * PARAMS.promote.N, max_observed * (1.0 + 1e-9))
    orig = ps_mod._evaluate

    def strict(view, key, index):
        snap = orig(view, key, index)
        return replace(
            snap,
            hard_ok=snap.hard_ok and snap.n_eff >= threshold,
        )

    monkeypatch.setattr(ps_mod, "_evaluate", strict)


def _mut_decay_demote_always(monkeypatch):
    """§7.3 转换③：连续跌破基线的衰退判定恒真（active 格当夜全部熄灭）。"""
    ps_mod = _kernel_module("promote_scan")
    monkeypatch.setattr(ps_mod, "_decay_below_baseline", lambda key, index, **kw: True)


def _mut_node_prefixes_type_only(monkeypatch):
    """§3.3/§7.1 影子记账：存款只落类型格，不再同落家族格（前缀被砍）。"""
    cells_mod = _kernel_module("cells")
    ps_mod = _kernel_module("promote_scan")

    def type_only(taxonomy_path):
        path = list(taxonomy_path)
        if not path:
            return []
        return ["/".join(path[:2])] if len(path) > 1 else [path[0]]

    monkeypatch.setattr(cells_mod, "node_prefixes", type_only)
    monkeypatch.setattr(ps_mod, "node_prefixes", type_only)


def _mut_ts_beta_prior_doubled(monkeypatch):
    """TS 对照臂 Beta 先验：λ/4 → λ/2（route_thompson.beta_posterior）。"""
    ts_mod = _kernel_module("route_thompson")

    def wider_prior(pos_w, neg_w, *, lam):
        prior = lam / 2.0
        return (pos_w + prior, neg_w + prior)

    monkeypatch.setattr(ts_mod, "beta_posterior", wider_prior)


def _mut_ts_posterior_mean_biased(monkeypatch):
    """TS 对照臂后验均值：α/(α+β) → α/(2α+β)（spec_c_stars 点估计位系统偏置）。"""
    ts_mod = _kernel_module("route_thompson")

    def biased_mean(alpha, beta):
        return alpha / (2.0 * alpha + beta)

    monkeypatch.setattr(ts_mod, "posterior_mean", biased_mean)


def _mut_quota_l2_uncapped(monkeypatch):
    """§2.4 quota_l2：cap 截断被砍（min(cap, ⌊r/c⌋) → ⌊r/c⌋，SPEC-5 §4.3 quota 公式）。"""
    b_mod = _kernel_module("budget")

    def uncapped(remaining_cents, cost_per_l2_cents, *, cap):
        if cost_per_l2_cents <= 0 or remaining_cents <= 0:
            return 0
        return int(remaining_cents) // int(cost_per_l2_cents)

    monkeypatch.setattr(b_mod, "quota_l2", uncapped)


def _mut_quota_s2_ceiling(monkeypatch):
    """§2.4 quota_s2：地板除改天花板除（⌊·⌋ → ⌈·⌉，配额系统性虚高一档）。"""
    b_mod = _kernel_module("budget")

    def ceiling(l2_survivors, remaining_cents, cost_per_s2_cents):
        if cost_per_s2_cents <= 0 or remaining_cents <= 0 or l2_survivors <= 0:
            return 0
        num = int(l2_survivors) * int(remaining_cents)
        return -(-num // int(cost_per_s2_cents))

    monkeypatch.setattr(b_mod, "quota_s2", ceiling)


def _mut_trip_strict_inequality(monkeypatch):
    """§2.4 熔断判据 D16：spent ≥ budget 的等号被砍（恰压线不再触发）。"""
    from dataclasses import replace as _dc_replace

    b_mod = _kernel_module("budget")
    orig_state = b_mod.budget_state

    def strict(spent_cents, policy, *, params=None):
        st = orig_state(spent_cents, policy, params=params)
        if st.degraded and st.spent_cents == int(policy.budget_cents):
            st = _dc_replace(st, degraded=False, tier=str(policy.mode))
        return st

    monkeypatch.setattr(b_mod, "budget_state", strict)


def _mut_finding_transitions_drop_d8_edge(monkeypatch):
    """§2.4/D8：结论状态机迁移表砍 S3 直达边（OPEN→CONFIRMED）——dispatch-004 的
    open→confirmed 流转必红（SPEC-5 §4.3「FINDING_TRANSITIONS 任一边」）。"""
    from qswarm.contracts import FindingState as FS

    d_mod = _kernel_module("dispatch")
    mutated = {
        FS.OPEN: frozenset({FS.VOTED, FS.REJECTED, FS.EXPIRED}),      # D8 直达边被砍
        FS.VOTED: frozenset({FS.VERIFIED, FS.INCONCLUSIVE, FS.CONFIRMED, FS.REJECTED, FS.EXPIRED}),
        FS.INCONCLUSIVE: frozenset({FS.CONFIRMED, FS.REJECTED, FS.EXPIRED}),
        FS.VERIFIED: frozenset({FS.CONFIRMED, FS.REJECTED, FS.EXPIRED}),
        FS.CONFIRMED: frozenset(),
        FS.REJECTED: frozenset(),
        FS.EXPIRED: frozenset(),
    }
    monkeypatch.setattr(d_mod, "FINDING_TRANSITIONS", mutated)


def _mut_re_route_weak_w_bumped(monkeypatch):
    """§3.2/D13：猜测格弱存款幅值 0.05 → 0.06（§6.5 附带逐字「−0.05」失真）——
    re_route-001 的事件载荷与 cells 投影必红（SPEC-5 §4.3「RE_ROUTE_WEAK_W」）。"""
    rr_mod = _kernel_module("reroute")
    monkeypatch.setattr(rr_mod, "RE_ROUTE_WEAK_W", 0.06)


MUTATIONS = [
    ("decay_half_life_halved", _mut_decay_half_life),
    ("shrink_c_star_flattened", _mut_shrink_flat),
    ("tilt_gain_doubled", _mut_tilt_gain_doubled),
    ("wilson_z_1p64_to_2p5", _mut_wilson_z),
    ("promote_hard_gate_n_eff_over_surface", _mut_promote_hard_gate),
    ("decay_demotion_always", _mut_decay_demote_always),
    ("node_prefixes_type_only", _mut_node_prefixes_type_only),
    ("ts_beta_prior_lam_quarter_to_half", _mut_ts_beta_prior_doubled),
    ("ts_posterior_mean_biased", _mut_ts_posterior_mean_biased),
    ("budget_quota_l2_uncapped", _mut_quota_l2_uncapped),
    ("budget_quota_s2_ceiling", _mut_quota_s2_ceiling),
    ("budget_trip_strict_inequality_d16", _mut_trip_strict_inequality),
    ("dispatch_finding_transitions_drop_d8_edge", _mut_finding_transitions_drop_d8_edge),
    ("reroute_weak_w_0p05_to_0p06", _mut_re_route_weak_w_bumped),
]


# ── 门禁本体 ────────────────────────────────────────────────────

@pytest.mark.parametrize("mutation", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_mutation_breaks_at_least_one_golden(monkeypatch, mutation):
    """突变生效下重放黄金集：至少一条用例必须失配（该红的必须红）。"""
    name, apply_mutation = mutation
    apply_mutation(monkeypatch)

    detected = []
    for case in _ORDERED:
        diffs = gl.check_case(case, manifest=MANIFEST, params=PARAMS)
        if diffs:
            detected.append((case["case_id"], diffs[:3]))
            break

    assert detected, (
        f"突变 {name} 未被任何黄金用例检出——回归集对该公式无灵敏度，"
        "请为该公式族补充覆盖（或确认突变点确无可观测数值）。"
    )
    case_id, sample = detected[0]
    print(f"\n[mutation:{name}] 由 {case_id} 检出，例如：{sample[0]}")


def test_mutation_suite_covers_core_formula_families():
    """突变清单覆盖九类核心公式（防清空突变后门禁空转）。"""
    assert len(MUTATIONS) >= 9


def test_demotion_surface_guard():
    """轨迹面守卫：夹具事件日志含 demotion ⇒ promote 期望必须含 demotion 流转。

    衰退/休眠公式的「抑制向」突变只有在期望里出现 demotion 流转时才可检出
    （冻结时抽样器已保底抽 demotion 夜）。若轨迹确无 demotion 事件（seed=7/
    30 日的现状），本守卫放行并记录现状——见模块 docstring 覆盖边界。
    """
    log = gl.load_event_log()
    has_demotion = any(row[0] == "m" for row in log["events"])
    demoted_nights = 0
    for p in gl.case_files("promote_scan"):
        case = gl.load_case(p)
        demoted_nights += sum(
            1 for t in case["expected"]["transitions"] if t["kind"] == "demotion"
        )
    if has_demotion:
        assert demoted_nights >= 1, (
            "事件日志含 demotion 事件，但 promote 夹具期望没有任何 demotion 流转——"
            "衰退/休眠公式的抑制向突变将不可检出，请检查抽样保底逻辑"
        )
    else:
        print(f"\n[guard] 轨迹面无 demotion 事件（DORMANT_DAYS 超出轨迹视野），"
              f"抑制向衰退/休眠突变不在可观测面内；现有 promote 期望流转 "
              f"{demoted_nights} 条均为 promotion。")

# ── cluster 黄金用例突变（M1：kernel.cluster 公式族；tests/golden/cluster/）──
#
# 夹具与突变面均独立于 M0 四类（manifest/参数各自成树，见 golden_loader.
# cluster_case_files）；判定口径与上方门禁一致：突变生效下任一 cluster 夹具
# 失配（或因 literal 簇 id 失配等行为破坏而抛错）即「该红必红」。

_CLUSTER_MANIFEST = gl.load_cluster_manifest()
_CLUSTER_PARAMS = gl.manifest_params(_CLUSTER_MANIFEST)
_CLUSTER_CASES = [gl.load_case(p) for p in gl.cluster_case_files()]


def _mut_cluster_sim_threshold(monkeypatch):
    """精聚类相似度阈值 0.9 → 0.95（§3.4 逐字阈值被改，含等号边界破坏）。"""
    monkeypatch.setattr(_kernel_module("cluster"), "CLUSTER_EMBED_SIM_MIN", 0.95)


def _mut_cluster_overlap_min(monkeypatch):
    """粗聚类行窗重叠下限 0.5 → 0.65（交集/min 口径下 0.6 重叠对被拒并）。"""
    monkeypatch.setattr(_kernel_module("cluster"), "CLUSTER_OVERLAP_MIN", 0.65)


def _mut_cluster_s2_source_weight(monkeypatch):
    """S2 源权重 0.5 → 0.4（§6.1 规格常量被改 → 存款权重与 cells 全变）。"""
    monkeypatch.setattr(_kernel_module("cluster"), "S2_SOURCE_W", 0.4)


def _mut_cluster_expansion_order_reversed(monkeypatch):
    """§6.5 归属展开序（D7：成员→侦察→评审→验证）被倒序。"""
    cluster_mod = _kernel_module("cluster")
    orig = cluster_mod._expansion_rows

    def reversed_rows(*args, **kwargs):
        return list(orig(*args, **kwargs))[::-1]

    monkeypatch.setattr(cluster_mod, "_expansion_rows", reversed_rows)


CLUSTER_MUTATIONS = [
    ("cluster_sim_threshold_0p9_to_0p95", _mut_cluster_sim_threshold),
    ("cluster_overlap_min_0p5_to_0p65", _mut_cluster_overlap_min),
    ("cluster_s2_source_weight_0p5_to_0p4", _mut_cluster_s2_source_weight),
    ("cluster_expansion_order_reversed", _mut_cluster_expansion_order_reversed),
]


@pytest.mark.parametrize(
    "mutation", CLUSTER_MUTATIONS, ids=[m[0] for m in CLUSTER_MUTATIONS]
)
def test_cluster_mutation_breaks_at_least_one_golden(monkeypatch, mutation):
    """cluster 突变生效下重放 cluster 黄金集：至少一条夹具必须失配。"""
    name, apply_mutation = mutation
    apply_mutation(monkeypatch)

    detected = []
    for case in _CLUSTER_CASES:
        try:
            diffs = gl.check_cluster_case(case, params=_CLUSTER_PARAMS)
        except Exception as exc:  # 行为破坏（如 literal 簇 id 失配）同样算检出
            detected.append((case["case_id"], [f"{type(exc).__name__}: {exc}"]))
            break
        if diffs:
            detected.append((case["case_id"], diffs[:3]))
            break

    assert detected, (
        f"突变 {name} 未被任何 cluster 黄金用例检出——回归集对该公式无灵敏度，"
        "请为该公式族补充覆盖。"
    )
    case_id, sample = detected[0]
    print(f"\n[cluster-mutation:{name}] 由 {case_id} 检出，例如：{sample[0]}")


def test_cluster_mutation_suite_covers_cluster_formula_families():
    """cluster 突变清单覆盖判据/权重/展开序四族（防清空后门禁空转）。"""
    assert len(CLUSTER_MUTATIONS) >= 4
