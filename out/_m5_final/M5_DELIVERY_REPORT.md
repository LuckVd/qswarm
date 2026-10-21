# M5 交付报告：S2 沙箱与真验证（依赖序第 8 位，两大重件之二）

> 2026-10-21 单轮闭环（负责人拍板「开始做 M5，直接做完」= D38–D43 按评估文推荐
> 全批，逐条落 `docs/spec/DECISIONS.md` 同日行）。规格：`docs/spec/SPEC-9_m5_sandbox.md`
> v1.0；验收卷：`docs/spec/SPEC-5_m5_acceptance.md`（A–E 组判据逐条机器可检）。

## §0 收口摘要

- **全量 pytest 754 passed**（712 基线零回归 + M5 新增 42 例，2026-10-21 实测）；
  写口审计 `--strict` PASS（零新表、零账本直写）；M0 黄金逐位面零触碰
  （taxonomy 扇出变化仅影响 M0 knob 推导测试期望值，机制参数不动）。
- **G1 沙箱双档**（D38）：container 档（docker）五项红线**真机实测全绿**
  （禁网/非特权 nobody/只读根+tmpfs/资源限额/墙钟收割 + docker.sock 零挂载）；
  process 档（subprocess+rlimits+unshare(2)+快照副本+setuid，缺能力逐项降级
  如实记录）。env 缓存双形态（镜像 tag / sitedeps 标记）。
- **G3 run_verify 独立入口**（D40-b）：D43-a 双档落账口径——process 档零账本写
  （机器可检 C4：事件/任务/VR/cells 全等对照）、container 档生产全链（VR 行 +
  verify_result 事件 + §2.4 映射 + propagate_verdict ×0.5，全走 dispatch 既有
  写口）；R7-① quota_s2 改 verifier 实编（C9 预算截面判据）；R7-② 沙箱非 LLM
  成本入报告披露面。
- **G2 PoC 双源 + 前置离线试跑**（D39）：verifier 蜂一体产（```json``` + 
  ```python``` 双围栏）+ 家族模板兜底（零 LLM）；**真实素材试跑 match 2/2**
  （E 组 smoke 2 voted 簇 → verified_real × confirmed，§3）。
- **G4 最小仲裁件**（D42-a）：run_arbitrate 工件（摘要+草稿，summary_ref 回填）
  + 任务审计锚 + 零存款零 verdict；G5 暗考接线默认关（D35 批准面）；G6 遗留
  六件 + M4 判据缺口四条全部收口（SPEC-9 §8 逐项）。

## §1 交付物清单

| 文件 | 状态 | 内容 |
|---|---|---|
| `qswarm/sandbox/`（新包：config/envcache/pocgen/runner/trial） | ✅ | G1+G2 全部执行面（SPEC-9 §2/§3） |
| `qswarm/kernel/verify.py`（新） | ✅ | run_verify 独立编排 + reconcile + 双档落账门（§4） |
| `qswarm/kernel/arbitrate.py`（新） | ✅ | D34 最小仲裁件（§5） |
| `qswarm/exams/dark.py`（新） | ✅ | 暗考回调工厂（§7） |
| `qswarm/kernel/{loop,consult,dispatch,cluster}.py`（微扩） | ✅ | 暗考接线点 + rules_runner 缺省装配 + monotonic 注入 + ROLE_* advisor 行落位（build_contract 对 advisor 仍显式拒绝） |
| `qswarm/perception/channels.py`（微扩） | ✅ | 通道 C 命中路径归一 |
| `qswarm/exams/{runner,store}.py`（微扩） | ✅ | 存款值版本化刷新（补偿存款）+ ARTIFACT_KINDS 扩 4 类 |
| `qswarm/contracts/config.py` + `configs/params.yaml` | ✅ | sandbox/verify 新节 + exam.DARK_EXAM_ENABLED（全缺省=规格逐字，缺节黄金兼容） |
| `configs/taxonomy.yaml` | ✅ | types 面补齐（command_injection/path_traversal） |
| `tests/test_m5_{sandbox,verify,g6}.py`（新）+ 既有钉子更新 | ✅ | 42 新增判据 |
| `docs/spec/SPEC-9_m5_sandbox.md` + `SPEC-5_m5_acceptance.md` | ✅ | 规格 + 验收卷 |
| `docs/m5_assessment_20261021.md`（追记）+ `docs/spec/README.md` + `DECISIONS.md`（D38–D43 + M5-1…M5-5 六行） | ✅ | 档案面 |

## §2 判据对照（M5 卷逐组，全绿；明细见 SPEC-5_m5_acceptance）

- **A 组沙箱**（11 判据）：红线词表/落档、env 身份、双围栏提取、断言契约、
  家族模板与 unsupported 如实、process 真执行（PASS/FAIL 双向 + 超时分类）、
  env 缓存复用、container docker 缺失 → env_build_fail、档位守卫、
  **真 docker 档实跑**（含超时收割）。
- **B 组配置词表**（6 判据）：sandbox/verify 逐键钉值 + 缺节回落 + 越键拒绝；
  ROLE_* advisor 行 + build_contract 拒绝语义保持；ARTIFACT_KINDS；taxonomy；
  M0 推导值（injection 12→10 / authz 20→15，判据③端到端 10/9）。
- **C 组 run_verify**（11 判据）：写口门、reconciliation 全分支数值钉、选取面
  与谱系回读、process 零账本写、**真沙箱已知答案**、container 全链（映射/存款
  逐角色权重 0.25/0.05/0.125/工件三件套/log 回填/on_cost）、env_build_fail 零
  存款、fp+blocking 反向存款、quota 实编截面、无 verifier fail-closed、幂等。
- **D 组试跑**（2 判据）：见 §3。
- **E 组仲裁/暗考/清账**（12 判据）：仲裁件双态 + 幂等；暗考默认关/双前提/
  回调单元（passed + 存款零重复 + fail-open）；rules_runner 装配（真 semgrep）；
  路径归一；pv 三态；存款刷新收敛 + 幂等；注入显式断言；墙钟注入；位级幂等；
  repomap 缓存零增长。

## §3 G2 前置离线试跑实跑记录（评估文补强③兑现）

```
python -m qswarm.sandbox.trial --db out/m4-seeding-v3/prod.db \
  --store out/m4-smoke/store --repo out/m4-smoke/repo --report-dir out/m5-trial
```

- 素材：E 组 smoke 真实产物（2 voted 簇 = render_tool.py 命令注入
  C-20261020-000001/000002；无 pre_verdict 工件 → 期望回退 s1_projection
  confirmed，**回退面如实标注**——评估文「PreVerdictSlip 现成」与实况不符，
  已按实况实现双轨）；
- 结果：**verified_real ×2，match 2/2**；沙箱墙钟 0.1s 量级（非 LLM，如实披露）；
  工件落 `out/m4-smoke/store/trial/<cluster>/{poc.py,run.log,result.json}`；
- 结论口径：生成面「可用」量级（2 簇样本不外推；R3 供血风险的 LLM 一体产面
  留真蜂联调轮复验）。

## §4 E 组真蜂联调（前置件就绪，待负责人放行执行）

联调不在本轮离线交付内（真蜂调用 + 沙箱真簇全链）。前置件状态：

1. docker 运行时 ✅（D38-a 实测）；2. verifier 入编 = D41 级联（重考
engineering:flash → 不过线自动帽兼任 strong:flash），代码面就绪、考试真蜂调用
待放行；3. 联调素材 ✅（E 组 smoke fixture + out/m5-trial 试跑基线）；4.
全量 pytest ✅。

命令形态（供联调轮）：入编考按 M2/M4 补播面（`qswarm.exams.seeding`）；真沙箱
smoke = `kernel.verify.run_verify(tier="container", adapter=UrllibAdapter(...),
runner=None)` 消费 R-CONSULT 簇（spec `docs/spec/SPEC-9_m5_sandbox.md` §4）。

## §5 已知限制与 caveats（如实）

1. **process 档近似红线**：unshare 禁网不可用时如实降级（`not-isolated`），
   D43-a 下不进生产面故不构成账本风险；container 档 repo 0700 时 staging 副本
   （mitigations 记 `staged-0755-copy`）。
2. **家族模板 v0 只覆盖 python 注入/穿越两族**；他族 template_unsupported
   如实计数（m3 仓 util.c c 族 declined 面、C 族 Juliet 簇真验证依赖 LLM
   一体产面）。
3. **预裁决单身份口径双查**（M5-3）：M4 pv 工件 cluster_id 实为锚 finding id
   （fine_clusters 键形态），M5 消费 C id → 锚 id 双查；生产 pv 面若后续统一
   为 C id，双查天然兼容。
4. **taxonomy 扇出变化传导**：M0 effective_M 推导测试期望值已随 M5 types 面
   更新（机制参数/黄金逐位零触碰）；若未来回滚 types 补齐须同步回滚该测试。
5. **存款版本化刷新为补偿存款形**：cells 净值收敛到新值但 n_eff（=2×(pos+neg)）
   含审计轨迹增幅（append-only 语义的代价，如实呈现）；「净差幅值」按
   round(·,9) 入账。
6. **暗考接线未开启**（默认关）：开启归 D35 批准面；影子期结束阈值仍按放行行
   （首月数据窗后反推）。
