# M4 交付报告：L2 会诊 + repo map 切片注入 + route() 生产接线 + 影子期指标
（SPEC-8 v1.0 / SPEC-5 M4 卷）

> 交付日期：2026-10-13 · 执行：M4 实现子任务（免审直跑，D26 同款环境判定；门禁不因
> 免审降档）。决策真源：DECISIONS 2026-10-13 七行（放行 + D31–D34/D37/Q1 + M4-1～
> M4-5 实现期行）。评估真源：`docs/m4_assessment_20261013.md`（自审修订版）。

---

## 0. 门禁结论

| 组 | 内容 | 判定 |
|---|---|---|
| A 功能 | repomap 切片面（7 例）+ consult 编排/面板/因子/影子/报告（13 例）+ config 旋钮（6 例） | ✅ 全绿 |
| B 不变量 | 写口审计 `--strict` PASS（15 表词表零漂移）、params 越键拒绝（consult/repomap 节） | ✅ 全绿 |
| C 可复现 | e2e 同参数重跑账本零新事件、route/cluster 黄金零回归（s1_factor 缺省 1.0 逐位恒等） | ✅ 全绿 |
| D 端到端 | scan→voted 全链（FakeAdapter 离线）：route 专员通道 → finding → 粗聚类 → 面板 → S1 存款含 D32 因子 → 精聚类 → 预裁决单 → 报告落盘；预算降级；面板星饿 fail-closed | ✅ 全绿 |
| E 真蜂联调 | **未执行（前置件绑定，非开发期门禁）**——命令就绪见 §4 | ⏳ 绑定件 |

**全量 pytest：696 passed / 0 failed（673 基线零回归 + 23 例 M4 新增）**
**写口审计：`WRITE-PORT AUDIT PASS`（`--strict` 同；dispatch.close_task_empty 为
tasks 写口同模块公开面，词表零扩）**

## 1. 落位清单（模块 ↔ SPEC-8 §0）

| 模块 | 状态 | 内容 |
|---|---|---|
| `qswarm/perception/repomap.py`（新） | ✅ | 名称绑定图（D37：同文件定义优先→全仓唯一定义成边→歧义丢弃）+ import 正则边（py/js/java/go/c/cpp）+ BFS 邻域切片（hop=hop_budget_for）+ 预算截断 + blake2b digest；纯函数零 IO 零随机 |
| `qswarm/perception/indexer.py`（微扩） | ✅ | IndexEntry 第四档 `all_calls`（全量调用点名；M4-1——原 `calls` 是线索命中稀疏子集）；缓存 JSON 向后兼容 |
| `qswarm/kernel/consult.py`（新） | ✅ | run_consult 全编排（§2）：verify_ready → open_run → repo map 切片注入 → route() 派 auditor（F3）→ submit 建档（findings+evidence_nodes 既有写口）→ attribute_findings 粗聚类 → 面板投票（D31）→ close_panel 显式收口（M4-2）→ S1 存款（D32 因子）→ Jaccard 精聚类 → 预裁决单（M4-4）→ 预算熔断/选择性过期 → 影子指标 + 最小报告 |
| `qswarm/kernel/cluster.py`（微扩） | ✅ | VerdictContext.s1_factor（缺省 1.0 逐位恒等）+ _expansion_rows S1 行源权重乘因子（D32；S2/S3 不乘，单元测试钉） |
| `qswarm/kernel/dispatch.py`（微扩） | ✅ | `close_task_empty`（M4-3 declined 收口公开面） |
| `qswarm/exams/roster.py`（微扩） | ✅ | `attach_hat`（D31 帽兼任注册；active 蜂限定 + 五角色词表 + 幂等） |
| `qswarm/exams/store.py`（微扩） | ✅ | ARTIFACT_KINDS + {repo_map, pre_verdict} |
| `qswarm/contracts/models.py` | ✅ | PreVerdictSlip（D29 三字段 + cluster_id；与 VerifierReport 语义边界注释在案） |
| `qswarm/contracts/config.py` + `configs/params.yaml` | ✅ | perception.repomap（max_files/max_lines）+ consult（wall_clock_budget_s/min_panel/s1_rho/l2_context_lines）逐键钉值 + 缺节回落黄金兼容 |
| `configs/models.zhipu.yaml` | ✅ | mid 档 fallback glm-5.3-flash（D31；reviewer 池 → 2 蜂，expand_candidates 去重展开实测无冲突） |
| `qswarm/kernel/loop.py` | **零改动** | L0→L1 面原样；consult 独立入口消费（SPEC-8 §0 承诺兑现） |

## 2. 判据对照（M4 卷逐条，如实披露）

### 全绿（测试在案）
repomap 七判据（绑定/歧义/import/BFS/截断/确定性/无 grammar 退化）；
consult 编排（route 派单契约、专员通道确定性【T_BASE=T_MAX=1.0 钉法】、declined
clean 面、粗聚/面板/收口、报告者排除【voters ∩ reporters = ∅ 强断言】、面板星饿、
精聚类阈值、预裁决单三字段 + verify_results 零行、预算 quota、空池 queued、
schema 重试一次、影子只读、报告漏斗对账）；因子数值四钉 + 作用域单元（S1 乘 /
S2 不乘）+ 缺省逐位（黄金全量零回归）；config 三判据。

### 如实披露的判据缺口（4 条，非阻塞）
1. **墙钟停机判据未自动化**——`wall_clock_budget_s` 走真实时钟，单测不可确定性
   触发；收口路径与 quota 耗尽共用（budget_degrade 已覆盖），真蜂联调实测。
2. **e2e 幂等以账本事件计数断言**（零新事件/零新存款），未做逐位 payload 对照
   （C 组「rerun_bitwise」降档）——报告计数面（panels_run 等）在重跑时有意的
   幂等跳过不重复计数，位级对照留联调轮。
3. **repomap 缓存复用判据**未单测（indexer 缓存本体 M3 已钉；consult 侧复用
   IndexCache 直通）。
4. **注入防线对切片的显式断言**未加（INJECTION_GUARD 为五模板共享尾部、render
   单一源自动生效——结构上不可能旁路，M5 联调卷补显式断言）。

## 3. 已知限制与 caveats

1. **E 组真蜂联调未执行**（前置件绑定）：离线全链 FakeAdapter 已绿；真蜂面
   （思考模式时延/成本、面板蜂真实投票质量、预裁决单可用性）待 §4 命令。
2. **D32 ρ=0.4 为同族标定值**（M0 z_panel），5.3×5.3-flash 跨档对相关性未实证
   ——校准债记档，真票积累后回测。
3. **SERVED_MODEL 解析表**硬编码于 consult.py（Coding Plan 探针实测口径，
   DECISIONS 2026-10-12）；表外模型名归 "unknown" 哨兵（保守降 diversity）。
   供给面变化时此表需人手同步。
4. **旧感知缓存**（M3 期产出）缺 all_calls 档 → repomap 图边退化为空；生产
   联调使用全新缓存目录即不受影响（M4-1 已注明）。
5. **报告落盘**缺省 `out/<run_id>/`，只读环境静默跳过（编排不阻塞）；`report_dir`
   参数可指定。

## 4. E 组真蜂联调（前置件；负责人放行后执行）

```bash
# ① 探针（4–8 次调用验证请求名 vs 实际服务模型，含新 mid fallback glm-5.3-flash）
# ② E 组补播考（新 DB + 全新缓存 + S9 位级重放；名册含 mid 双蜂 + 帽兼任蜂）
QSWARM_KEY_ZHIPU="$(cat .zhipu_key)" python3 -m qswarm.exams.seeding \
  --bank seeds/bank.yaml --models configs/models.zhipu.yaml \
  --db out/m4-seeding-rv/seeds.db --out out/m4-seeding-rv \
  --roles reviewer,verifier \
  --hat-agents "cheap:zhipu/glm-4.5-air@cv1=reviewer,cheap:zhipu/glm-4.6@cv1=reviewer"
#   帽兼任第 3 席按 D31（帽池实为 3：mid 双蜂 + 帽兼任蜂，排除报告者后面板足额）；
#   资格判据 = reviewer 帽入职考过线；QC 失败只告警不降编（agents.status 属蜂本体）
# ③ smoke 真仓 fast→pr 档全链一次（run_scan → run_consult；名义预算封顶）
```

判据 = 链路正确（事件/工件/报告三面齐全）；时延/成本实测入 M4 联调补录
（D33 双轨记账首次产出）。

## 5. 下一步（M5 输入面）

- **S2 沙箱**（依赖序第 8 位）：verifier 升级真验证（PreVerdictSlip → 沙箱排程
  输入；verify_results 写口/§10.4 verified_fp 证据标准已备）；
- **影子期结束裁决**：shadow_report 指标面已就位（D35 阈值建议随联调补录），
  裁决留负责人；暗考回调 exam 接线点在 loop.run_scan 尾部（M3-7 预留）；
- **仲裁/报告件**（D34 推迟面）随 S2 同交付；
- E 组联调 + M4→M5 放行裁决由负责人按本报告门禁结论明示。
