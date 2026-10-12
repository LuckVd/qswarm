# tests/golden — 内核黄金用例回归集（SPEC-4 §4 输出物 3）

从一段 30 日机制仿真轨迹（`seed=7`，`run_id=sim-s7-d30`）确定性冻结的内核回归门：
**此后任何内核机制公式变更，只要改动任一被覆盖的可观测数值，必先红在这里。**

- 轨迹出处（seed / digest / 世界旋钮 / 再生成命令）：`manifest.json`
- 用例规模：route **9** · route_thompson **9** · promote_scan **8** · agg_cells **8**
  （共 **34** 条 ≥ 20；route / promote_scan / agg_cells 三类各 ≥6 为 SPEC-4 §4-3 下限）
- 冻结工具：`scripts/freeze_golden.py`（落盘前逐条自检；输出无墙钟时间，逐字节可复现）
- 装载器：`tests/golden_loader.py`（freeze 与 pytest 共用同一编解码与重放语义）

## 溯源再生成（ac_muz07uljb100z3l3）

```bash
python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check
#   ⇒ 重新跑同种子轨迹、重放全量路由（与轨迹全部派单逐字段核对）、
#     重建全部夹具并与本目录逐字节比对；输出 [check] OK 即溯源成立。
```

`manifest.json` 的 `trajectory.digest` 是该轨迹的 sha256（`RunResult.digest`）；
每条夹具的 `meta.seed` / `meta.trajectory_digest` 必须与 manifest 一致
（`tests/test_golden_regression.py::test_golden_provenance_seed_and_digest` 强制）。

## 布局

```
manifest.json                 轨迹出处 + 生效参数（params.yaml 经 engine 覆盖链）+ 计数
slices/events_log.json        机制事件全序日志（deposit/promotion/demotion，紧凑编码）
slices/slice-*.json           切片 = 事件日志前 n_events 行 + as_of（SimClock ts 严格
                              递增 ⇒ 任意 as_of 过滤都是前缀；night 切片为严格 <，
                              day/nightend 为 ≤）
route/case-*.json             route 决策黄金用例（默认臂 = 轨迹派单策略）
route_thompson/case-*.json    TS 对照臂黄金用例（同切片同 rng 换路由器）
promote_scan/case-*.json      夜批状态流转黄金用例（含四判据快照数值）
agg_cells/case-*.json         聚合数值黄金用例（全格表 + c*/LOO 探针）
```

## 夹具 schema（四类共用骨架）

```jsonc
{
  "case_id": "route-006", "kind": "route", "slice": "day-16",
  "meta":  { "seed": 7, "trajectory_digest": "sha256:…", "day": 16, "…": "出处与抽样说明" },
  "params": { "…": "生效 ParamsConfig 全量（与 manifest.params 一致）" },
  "inputs": { "…": "kind 专属输入（task/pool、exam_script、或空）" },
  "expected": { "…": "期望输出（与 golden_loader 重放同构）" }
}
```

事件紧凑编码（events_log 的每行）：`[code, day, i, *payload 字段]`，时刻 =
`t0 + day·86400s + i·1s`（SimClock 整秒口径，精确还原）：

- `"d"` deposit：finding, cluster, agent, role, taxonomy_path, signal_tier,
  direction, weight, origin
- `"p"` promotion：agent, role, node, exam_id, from_status, to_status
- `"m"` demotion：agent, role, node, reason

## 重放语义与比对口径

- **片段最小化**：只含 agg_cells / promote_scan._Index 会读取的机制事件；dispatch、
  finding、cluster_merge、exam_result 不入夹具（M1 接通后轨迹另含
  finding/cluster_merge 事件，均不被 agg_cells/_Index 读取）。片段重放按片段序重编
  seq，故期望值**不含任何 seq**
  （`Transition.seq`、`cells.last_event_seq` 不参与比对——账本记账产物，非机制公式）。
- **route**：`random.Random(derive_seed(seed,"engine","route"))` 快进
  `meta.draws_before` 次后调用 `kernel.route`（与 engine 同参同池）；期望 = 完整
  RouteDecision。冻结时回放已与轨迹 RoutingRecord 五字段全量核对（回放=轨迹）。
- **route_thompson**：与配对 route 用例（`meta.paired_case`）同切片/同任务/同候选池/
  同 rng 快进偏移，改调 `kernel.route_thompson`。TS 臂**不是**轨迹的派单策略
  （轨迹由 kernel.route 生成），故期望 = 确定性重放的完整 RouteDecision（冻结时经
  loader 同一语义回填 + 落盘前逐条自检）；价值在把 TS 公式族（Beta 后验
  prior=λ/4、后验均值、逐候选采样序）纳入「该红必红」的回归门。
- **promote_scan**：考试回调按 `inputs.exam_script` 脚本化——`kind=promo` 为
  暗考（该夜轨迹的真实 pass/fail + exam_id）、`kind=fc` 为周期复核熄灭
  （转换⑥，v2 定稿：按轨迹 decay 流转脚本化 decayed=true；未熄灭的复核考不产生
  流转，回调未命中返回 None 与原轨迹一致，无 fc 项的旧夹具保持 for_cause 关闭
  语义）；流转记录剔 seq 全量比对，shadow→candidate 附四判据快照
  （n_eff / wilson_lb / LOO 基线 / 流量簇数 / 各判据布尔）。
- **agg_cells**：全格表（pos_w / neg_w / n_eff / n_eff_s2s3 / status /
  activated_at / updated_at）+ 全格探针（c_raw / c_star / LOO baseline_mean）。
- **浮点**：逐位精确（`==`；JSON repr 往返保真）。跨平台若 libm 浮点有差，按
  `regen_command` 重冻（见 manifest.float_policy）。

## 门禁与突变灵敏度

- `tests/test_golden_regression.py`：34 条参数化黄金断言 + 计数门（三类各 ≥6、
  总 ≥20）+ 溯源元信息检查。
- `tests/test_golden_mutation_gate.py`：对九类核心公式（指数挥发、层级收缩、
  自适应 TILT、Wilson 区间、晋升硬门槛、衰退熄灭、影子记账前缀、TS Beta 先验、
  TS 后验均值）各构造一个人工突变，断言任一突变至少击穿一条黄金用例——
  「该红必红」的回归门自证。
- **覆盖边界**：突变灵敏度以冻结轨迹的可观测面为准。当前 seed=7/30 日轨迹
  （M1 真实聚类接通后）含 demotion（decay，转换⑥周期复核）与判据快照夜，
  抽样器对两者均有保底（`test_demotion_surface_guard` 钥守卫：事件日志含
  demotion ⇒ promote 期望必含 demotion 流转）；证据路径转换在真实聚类语义下
  变稀（判据③簇计数按真簇号收敛），硬门槛突变的加严幅度随面上已观测判据快照
  自适应（`_mut_promote_hard_gate`，阈值取 max(2N, 面上最大 n_eff)），保证
  「该红必红」不依赖固定倍数恰落在可观测带内。
