"""内核黄金用例回归集（SPEC-4 §4 输出物 3；§5 门禁「内核黄金用例测试绿」）。

夹具由 ``scripts/freeze_golden.py`` 从一段 30 日仿真轨迹（seed=7）确定性冻结：
route 决策 / promote_scan 状态流转 / agg_cells 数值三类各 ≥6 条，外加 route_thompson
TS 对照臂（与 route 用例同切片/同 rng 快进偏移换路由器，覆盖 Beta 后验公式族），
每条含输入账本片段（切片）、生效参数、种子与期望输出。本文件按夹具参数化重放：

- route：以轨迹 rng 子流快进到冻结偏移后调用 ``kernel.route``，全字段精确比对；
- route_thompson：同切片/同任务/同池/同 rng 偏移改调 ``kernel.route_thompson``，
  全字段精确比对（TS 臂非轨迹派单策略，expected = 确定性重放）；
- promote_scan：脚本化考试回调重放夜批扫描，流转记录（含四判据快照数值）精确比对；
- agg_cells：全格表 + 全格探针（c_raw / c_star / LOO baseline_mean）逐值精确比对。

比对为逐位精确（见 manifest.float_policy）——此后任何内核机制公式变更，只要改动
任一被本集覆盖的可观测数值，必先红在这里。突变灵敏度由
tests/test_golden_mutation_gate.py 逐公式自证。

对应验收：
- ac_muz07uljslqut8ca  tests/ 中 ≥20 条黄金断言（route≥6、promote_scan≥6、
                       agg_cells≥6）且 pytest 全绿；
- ac_muz07uljb100z3l3  夹具含 seed 与轨迹出处元信息，可溯源再生成
                       （manifest.trajectory.seed/digest + regen_command）。
"""

import pytest

import golden_loader as gl

MANIFEST = gl.load_manifest()
PARAMS = gl.manifest_params(MANIFEST)


def _assert_golden(path) -> None:
    case = gl.load_case(path)
    diffs = gl.check_case(case, manifest=MANIFEST, params=PARAMS)
    assert not diffs, (
        f"{case['case_id']} 黄金失配（{len(diffs)} 处）——内核行为偏离冻结轨迹，"
        f"若为有意变更请按 {MANIFEST['regen_command']} 重冻：\n" + "\n".join(diffs[:30])
    )


# ── 四类黄金用例（每条夹具 = 一个参数化断言）─────────────────────

@pytest.mark.parametrize("path", gl.case_files("route"), ids=lambda p: p.stem)
def test_golden_route_decision(path):
    """route 黄金：切片视图 + 轨迹 rng 偏移 → 完整 RouteDecision 逐位比对。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("route_thompson"), ids=lambda p: p.stem)
def test_golden_route_thompson_decision(path):
    """route_thompson 黄金：同切片同 rng 偏移的 TS 对照臂决策逐位比对。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("promote_scan"), ids=lambda p: p.stem)
def test_golden_promote_scan(path):
    """promote_scan 黄金：夜批流转（含四判据快照）逐位比对（剔 seq，见 manifest）。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("agg_cells"), ids=lambda p: p.stem)
def test_golden_agg_cells(path):
    """agg_cells 黄金：全格表 + c*/LOO 探针逐位比对。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("budget"), ids=lambda p: p.stem)
def test_golden_budget_case(path):
    """budget 黄金（M1，SPEC-5 §1.4/§4.3）：三档配额边界（含等号）、D16 等号熔断、
    首 trip SCALING 字面与幂等、降级阶梯——合成规格用例逐位比对。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("dispatch"), ids=lambda p: p.stem)
def test_golden_dispatch_case(path):
    """dispatch 黄金（M1，SPEC-5 §1.2/§4.3）：四件套逐字、hop_budget 档位表（fast=0
    耗尽态）、派单状态机（空池 D10/重派通道关闭/expire 零存款幂等 D19）、结论状态机
    （§10.2 迁移表 + D8 直达边 + 非法迁移 ValueError）——合成规格用例逐位比对。"""
    _assert_golden(path)


@pytest.mark.parametrize("path", gl.case_files("re_route"), ids=lambda p: p.stem)
def test_golden_re_route_case(path):
    """re_route 黄金（M1，SPEC-5 §1.3/§4.3）：弱存款标准形（S1/−1/0.05/src_seq，多
    scout 字典序）、猜测格落点（类型格+家族影子格，n_eff_s2s3 不动）、每 finding 至多
    一次、D12 触发门、guess_taxonomy 全序、rebuild 重放等价——合成规格用例逐位比对。"""
    _assert_golden(path)


# ── 回归集自身的门槛与溯源元信息 ────────────────────────────────

def test_golden_manifest_counts_gate():
    """≥20 条且三类各 ≥6；M1 合成类各 ≥3（SPEC-5 §4.3）；夹具目录与 manifest
    计数一一对应（全类遍历，含 M1_KINDS）。"""
    counts = MANIFEST["counts"]
    assert counts["route"] >= 6, counts
    assert counts["promote_scan"] >= 6, counts
    assert counts["agg_cells"] >= 6, counts
    assert counts["total"] >= 20, counts
    for kind in gl.M1_KINDS:                     # M1 新类各 ≥3（SPEC-5 §4.3）
        assert counts[kind] >= 3, (kind, counts)
    for kind in gl.ALL_KINDS:
        paths = gl.case_files(kind)
        assert counts[kind] == len(paths), (kind, counts[kind], len(paths))
        for p in paths:
            case = gl.load_case(p)
            assert case["kind"] == kind
            assert (p.parent / f"{case['case_id']}.json") == p
    assert counts["total"] == sum(
        len(gl.case_files(kind)) for kind in gl.ALL_KINDS
    ), counts


def test_golden_provenance_seed_and_digest():
    """ac_muz07uljb100z3l3：每条夹具含 seed 与轨迹 digest，且与 manifest 一致。

    M1 合成规格类（SYNTHETIC_KINDS，非轨迹抽样）走 spec_literal 分支：溯源 =
    ``meta.origin == "spec_literal"`` + 规格出处（spec_ref 非空）+ regen 命令入
    manifest（manifest.regen_m1_commands）——见 manifest.m1_fixture_policy。
    """
    traj = MANIFEST["trajectory"]
    assert isinstance(traj["seed"], int)
    assert traj["n_days"] >= 1
    assert traj["digest"].startswith("sha256:")
    assert traj["route_seed_labels"] == ["engine", "route"]
    assert MANIFEST["regen_command"].startswith("python3 scripts/freeze_golden.py")
    assert (gl.GOLDEN_DIR / "slices" / "events_log.json").exists()
    for kind in gl.ALL_KINDS:
        for p in gl.case_files(kind):
            case = gl.load_case(p)
            meta = case["meta"]
            if kind in gl.SYNTHETIC_KINDS:
                assert meta["origin"] == "spec_literal", p
                assert meta.get("spec_ref"), p
                kind_cmd = MANIFEST["regen_m1_commands"][kind]
                assert kind_cmd.startswith("python3 scripts/freeze_"), (p, kind_cmd)
                continue
            assert meta["seed"] == traj["seed"], p
            assert meta["trajectory_digest"] == traj["digest"], p
            assert case["slice"], p
            assert case["params"] == MANIFEST["params"], p   # 夹具自含参数且全局一致
            assert case["expected"] is not None, p


def test_golden_slices_are_event_log_prefixes():
    """切片元数据可解析且为共享事件日志的合法前缀（0 < n ≤ 日志长）。

    M1 合成规格类无切片依赖（inputs 自含），断言其确不引用切片。
    """
    log = gl.load_event_log()
    total = int(log["n_events"])
    assert total == len(log["events"])
    seen = set()
    for kind in gl.ALL_KINDS:
        for p in gl.case_files(kind):
            case = gl.load_case(p)
            if kind in gl.SYNTHETIC_KINDS:
                assert "slice" not in case or case["slice"] is None, (
                    f"{p}: 合成规格用例不得引用轨迹切片"
                )
                continue
            s = gl.load_slice(case["slice"])
            assert 0 < s["n_events"] <= total, (case["slice"], s["n_events"])
            assert s["events"] == log["events"][: s["n_events"]]
            seen.add(case["slice"])
    assert seen, "没有任何切片被引用"
