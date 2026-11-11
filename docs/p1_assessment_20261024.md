# P1 开工前评估（2026-10-24）

> **2026-10-24 收口追记**：负责人指令「开工」（P1 四门整体放行）；D47/D48
> 未获答复、按本文 §6 建议值 A/A 保守执行（零调用路径，正式拍板点保留至
> E1 供给批准时追认）。实现与验收见 SPEC-10 v0.2 + SPEC-9 v1.1 §10 与
> `out/_p1_final/P1_DELIVERY_REPORT.md`（全量 pytest 806/806 全绿 =
> 769 基线零回归 + 37 新增；E0 确定性判据真机全过；§5-R1 仪器缺陷已修、
> 新增环境漂移发现 container 档 tmpfs mode 缺省 0755 → 钉死 1777）。
> 以下为开工前评估原文（事实面以追记为准）。

> 评估依据：`docs/DESIGN_REVISION_AND_EXPERIMENT_PLAN_20261022.md`（下称「方案」，
> §4–§7 工作包 B/C/D/E + §8 文件级清单 + §10-P1 阶段门 + §13 批准事项）、
> `docs/spec/SPEC-10_evidence_and_evaluation.md` v0.1（§6–§9 P1 骨架）、
> `docs/spec/DECISIONS.md` 至 6e32fb3（D46 三参数冻结 + P0 收口 + docker 恢复）、
> `out/_p0_final/P0_DELIVERY_REPORT.md` §4、代码基座实测（全量 pytest 769/769，
> 2026-10-23 docker 恢复后；题库 100 题结构实测；pocgen/reconcile/consult 漏斗
> 逐行复核）。本文只评估不拍板；决策点见 §6（D47–D48 候选），供负责人裁决。
> 负责人 2026-10-24 会话指令「开工」= P1 放行（方案 §10-P1 四门整体）；
> 本批实施范围以 §7 建议序为准，D47/D48 答复后落 DECISIONS。

---

## 0. 一句话结论

P1 四门（D oracle 负对照 / B 证据投影 / C 漏斗 trace / E0 仪器检查）**全部可
离线落地**：零线上调用、零生产写、零 DDL（B 是只读文件投影、C 是报告侧 trace、
D 是沙箱夹具与判据模块、E0 是确定性判据 runner），不依赖 D44（无合格 verifier
只阻塞生产 verify 与真沙箱 smoke，不阻塞造尺子）。语义基座已备：VerifyOutcome
三分类 + reconcile §6.3/§10.4 映射、沙箱双档 runner + harness ASSERT 契约、
append-only 账本 16 事件 + as_of 重放、consult/verify 报告漏斗计数。真实工作量
在**仪器精度面**，且 E0 未跑先暴露一个实证级仪器缺陷（§5-R1）：现行 pocgen
模板的 echo-marker 断言可被「仅回显安全程序」满足——九类验收集第 3 类直接
击穿，D 必须重建观察器分离的副作用判据；题库 100 题全部单快照无修复版配对，
成对对照只能自建受控合成夹具。两个决策点（D47 裁判校准供给时机 / D48 预算
起算锚）**不阻塞**本批离线实施。

## 1. 交付面定义（方案 §10-P1 逐字对齐）

- 范围：D 两家族 oracle + B 只读证据投影 + C 离线漏检 trace + E0。
- 交付：成对漏洞/修复测试、负对照、金标准数据清单、旧账本不变的投影报告。
- 退出条件：**E0 通过**；能够区分「脚本失败 / 主张不成立 / 无结论」；重复和
  纠错不增加根因数。
- 红线（D46 + 方案 §8）：不扩公共枚举（§6.1「不急着扩展」）、不动 params.yaml
  生产缺省、M0 黄金与 legacy_v1 cells 零触碰、role_qualifications 唯一写口
  不变、实验面零账本直写。

## 2. 基座盘点（已有落点，全部可复用）

| 面 | 现状 | P1 复用方式 |
|---|---|---|
| D-沙箱执行 | `sandbox/runner.py` 双档（Container 五项红线真机实测 / Process rlimits+unshare / FakeRunner）+ `SandboxResult`（exit_code/stdout/timeout/env_build_failed） | 九类夹具直接以 Process/Fake 档跑（合成件=人工审核受控夹具，方案 §6.3 许可）；container 档真机复验可选 |
| D-PoC 契约 | `pocgen.py` harness ASSERT 行契约 + 两族确定性模板（injection/authz+config_dep python） | 模板判据升级为副作用观察器（H2）；契约面（ASSERT 行、REPO/WORK 环境变量）不变 |
| D-内核裁定 | `verify.reconcile`（蜂主张 × 执行 → 三分类；§6.3 复现失败≠误报；§10.4 blocking 正面证据）；`VerifyErrorKind` 四类；dispatch 侧 §10.4 改判守卫 | reconcile 保持不动；其上叠加 claim_validation/execution_status 双层内部报告与保守映射（H4） |
| B-事件源 | `KernelLedger` append-only 16 LedgerKind + payload schema 校验；`read(kinds, from_seq, to_seq)`；replay 按 as_of 重建不读未来 | 投影读取器直接消费（零写、零新 kind——SPEC-10 §11 决策第 4 行） |
| B-分组 | fine_clusters root_cause 分组、cluster_merge/split 事件、M5 trial 工件（store/trial） | logical_evidence_id 以 root_cause_group_id 为键；supersedes 消费 cluster 事件 |
| C-漏斗计数 | consult 报告 funnel（candidates_total→l2_dispatched→l2_clean→findings_opened）+ 五类失败计数（schema_fail/infra_fail/quota_skipped/route_empty_pool/panel_starved）；scan L0 报告 + l0_candidates 工件；verify 报告漏斗（P0 增 verifiers_pool/qualified/gate_revoked_midrun） | 既有计数是**总量面**；P1 增**逐层逐条 trace 面**（输入/输出/原因/抽样概率/工件引用） |
| E0-锚点 | SPEC-10 §6 四层级 + §9 manifest 冻结面；D46 三参数（15/100、¥500/4 周、分支收缩）；P0 资格判据 15 例 | E0 确定性判据直接引用；manifest schema 落 experiments/design_revision/ |
| 规格 | SPEC-10 v0.1 §6–§9 骨架（字段面冻结、公式随开工细化）；SPEC-9 v1.0 | v0.2 升精度面 + SPEC-9 增补 oracle 边界（H8） |

## 3. 缺口清单（P1 新建面 H1–H8）

**H1（D-判据模块，最大件）** `qswarm/sandbox/oracle.py` 新建：
- `OracleKind`（D46 四层级常量：execution_paired / upstream_truth /
  cross_family_llm / blind_mechanical）+ `oracle_version`（"oc-v1"）；
- `ExecutionStatus`（环境状态/超时/退出码/实际调用目标/断言输出逐条）；
- `ClaimValidation`（supported / refuted / inconclusive）——**模块内类型，
  不进 contracts.enums**（§6.1 不扩公共枚举）；
- 成对判定 `validate_pair`：同一 PoC × {漏洞版, 修复版}——漏洞版触发违规副作用
  ∧ 修复版不触发 → supported；修复版**环境失败不算负对照通过**（→inconclusive）；
- 观察器分离：金丝雀由**目标进程**创建于可区分位置（创建者/权限边界可判），
  PoC 自己建 marker 不算证据；观察器脚本与目标执行分离，目标不得改观察器判定。

**H2（D-模板升级）** `pocgen.py` 两族模板从 echo-marker 升级：injection 面
argv/import 双级保留但断言改「注入命令在目标进程内产生可区分副作用」（受控
观察文件由 payload 创建于观察区，非 stdout 回显可满足）；path_traversal 面
canary 移出允许根目录但留沙箱内 + 增合法输入/拒绝输入对照；不满足观察器前提
的族如实返回 None。

**H3（D-九类验收集）** `qswarm/sandbox/fixtures/` 新建（受控合成夹具，人工
审核、零生产写、零题库改动）：①漏洞版 ②修复版 ③仅回显安全版 ④不可达 sink
⑤缺依赖 ⑥超时 ⑦无条件 PASS ⑧目标未调用 ⑨真实漏洞但 PoC 无效——末类**必须**
inconclusive（不能判 verified_fp）；每族至少覆盖①②③⑦⑨五类硬判据，其余按族
如实（不可达/超时/缺依赖以夹具面支持为准）。Python 两族（injection/
authz·path_traversal）。

**H4（D-内核映射）** `kernel/verify.py` 内部报告增双层结果
（`claim_validation` / `execution_status` / `oracle_kind` / `oracle_version` /
`control_results` / `evidence_ref`，process 档工件 + container 档 verify_result
payload 附加面——**不改既有字段语义，纯增量**）；保守映射规则：supported →
verified_real 仅当独立验证策略满足（成对对照过）；refuted → verified_fp 须
独立 blocking 证据覆盖输入/环境/权限/可达性前提；其余一律 inconclusive →
verify_error 注明具体原因，不静默 verified_fp。

**H5（B-证据投影）** `qswarm/kernel/evidence.py` 新建（纯读取）：
- `project_evidence(ledger, *, as_of) -> evidence_v2.jsonl`：方案 §4.2 字段
  逐字（observation_id / logical_evidence_id / supersedes / root_cause_group_id
  / repo_commit / task_id / execution_profile_id / role / family / claim /
  decision / truth_label / truth_source / oracle_version / adjudication_ref /
  eligible_set / selection_probability / selection_reason / occurred_at /
  available_at / correlation_group / cost / latency / failure_kind）；
  存量事件缺记录的字段如实 `unknown`，不猜测补齐；
- `capability_summary(evidence_v2) -> capability_v2.json`：按 profile/role/
  family 汇总（已知正例召回/已知负例拒判/precision 界
  [TP/(TP+FP+U), (TP+U)/(TP+FP+U)]/unknown 覆盖率），**不参与派单**；
- 纠错规则（§4.3）：同逻辑事实 supersedes 链只保留当前适用判断，一个根因一个
  事实单位；两次同级冲突标 disputed 归 U；
- 验收：同 as_of 同事件输入 → 同投影哈希；未来/未可用真值不进历史投影。

**H6（C-漏斗 trace）** `qswarm/kernel/funnel.py` 新建（离线装配器）：
- 从既有 scan/consult/verify 报告 + 账本事件拼 `funnel_trace.jsonl`：L0 未覆盖
  / L1 丢弃 / L2 未报告 / 评审拒绝 / 预算未执行 / S2 error / 确认上报，逐层记
  输入/输出/原因/抽样概率（存量 unknown 如实）/工件引用；
- 金标准回查 API：已知漏洞任务按漏斗回放，定位首次漏失层（返回逐层判定 +
  首失层 + 证据引用）；
- 写口边界：只读账本 + 写实验工件目录（out/ 下），零账本写、零生产表。

**H7（E0-登记面与确定性判据）** `experiments/design_revision/` 新建：
- `README.md`（协议与执行序）/ `manifests/`（E0 manifest 模板：数据哈希/分组/
  停止规则/预算/审批记录字段，方案 §7.1 冻结面）/ `budget_ledger.jsonl`（¥500
  消耗登记表，每行调用批/目的/金额/余额）；
- `run_e0_checks.py`：确定性判据 runner——九类负对照行为符合预期（③⑦⑧ 不得
  获可信验证、⑨ 必须 inconclusive）、指标分母测试（分母为 0 记不可估）、投影
  哈希确定性、时间可见性（未来真值不进历史投影）、重复/纠错不增根因数；
- E0 报告模板含人工校准面占位（20–30 例抽样协议、跨族裁判一致率表——待 D47）。

**H8（规格升版）** SPEC-10 v0.1→v0.2（§6–§9 骨架升精度面：oracle 判据公式、
证据字段类型、trace 层定义、E0 判据表）；SPEC-9 增补节（oracle/负对照与
outcome 映射边界、观察器分离要求）；`docs/spec/README.md` 索引版本行。

## 4. 前置件

- 无环境阻塞：P1 全部离线（FakeRunner/Process 档 + 合成夹具）；docker 已恢复，
  container 档真机复验九类夹具为可选项（零成本，建议跑一次留痕）。
- semgrep 经 `.venv/bin`（D30 惯例）：全量测试 `PATH="$PWD/.venv/bin:$PATH"`。
- 生产账本 `out/m4-seeding-v3/prod.db` 不触碰：B 投影只跑副本/合成/试跑账本。

## 5. 风险与结构性发现

- **R1（实证仪器缺陷，本批首要修复对象）**：现行 `_py_command_injection` 模板
  断言 = marker 出现在 stdout/stderr——「安全 echo 程序打印 argv」即可满足
  （九类第 ③ 类直接击穿）；`_py_path_traversal` canary 在 WORK 内、无合法输入
  对照。**推论**：M5 试跑 match 2/2 的口径建立在该断言上，模板升级后历史结论
  需重述（历史工件不改，报告加注）——正属 E0「先确认评分器测的是想测的东西」
  的题中之义。
- **R2（成对对照语料缺口）**：题库 100 题全部单快照（67 owasp_mutation java +
  30 juliet c + 3 cve_rollback python），无修复版配对；Python 两族仅 3 题。
  成对对照只能自建受控合成夹具（H3）；上游真值（oracle 层 ②）在 Python 两族
  面覆盖极薄，E0 人工复核抽样按 family 加权并如实记录覆盖限制。
- **R3（真实账本事件贫乏）**：生产账本无生产 run、verify_result 事件仅 M5
  trial 2 例——B 投影在真实账本近乎空投影；E0 判据主要建立在合成/试跑账本上，
  生产面投影价值待 D44 后有真跑数据（如实披露，不硬凑）。
- **R4（selection_probability 存量缺失）**：既有事件无抽样概率记录，投影字段
  只能 unknown——加权总体估计不可做（方案 §5.2 预见，非缺陷；分层结果照报）。
- **R5（人工校准面依赖）**：E0 裁判校准（20–30 例人工判 + 跨族主裁判）需要
  负责人劳动与供给变更批准（D46 红线：GLM 族禁任主裁判）——不阻塞本批离线件，
  时机见 D47。
- **R6（与 D44 解耦）**：P1 全程不需要合格 verifier（无生产派单）；D44 只阻塞
  真沙箱 smoke 与生产 verify。两线可并行，D44 处置不因 P1 推进而默认任何选项。

## 6. 待负责人拍板的决策点（P1 开工前）

**D47：E0 裁判校准面的供给与调用时机**
- 建议 A（分两步）：本批只冻结**协议与题目**（校准集从两族夹具 + 题库分层
  抽样冻结 20–30 例、manifest 字段、一致率表模板），跨族主裁判**选型与实际
  调用**推迟到 E1 开工前单独批。理由：校准题目须先过确定性仪器检查（负对照
  九类 + 分母测试）合格——先冻题再校准裁判，顺序不可倒；供给变更按方案 §13
  「未批准不调用」避免一轮混批；P1 退出判据「E0 通过」按确定性判据面先行裁定。
- 备选 B：现在选型并批准少量校准调用（需负责人指定跨族模型与供给通道）。

**D48：实验硬预算（¥500 / 4 周墙钟，D46 冻结值）起算锚**
- 建议 A：**首次获批线上调用日**起算——离线实现与确定性判据不烧墙钟不烧钱，
  4 周窗覆盖真正的实验消耗期；
- 备选 B：本批准日（2026-10-24）起算。

（范围确认不设决策点：负责人「开工」已按方案 §10-P1 四门整体放行；本批
全部离线零调用，若需收缩到先做 D+E0 可在答复时注明。）

## 7. 建议开工序与量级

序：H1+H2+H3+H4（D 门，最大件，约一半工作量）→ H7（E0 判据 runner 紧跟 D，
负对照即判据）→ H5（B 投影）→ H6（C trace）→ H8（规格升版收口）。

量级预估：新文件 5（oracle.py / evidence.py / funnel.py / fixtures/ /
run_e0_checks.py）+ 修改 2（pocgen.py / verify.py，纯增量）+ 实验 scaffolding
1 套 + 测试 3–4 个新文件（test_s2_oracle_controls.py ~15–20 例 /
test_evidence_projection.py ~10 例 / trace+E0 ~10 例），合计新增约 35–45 例；
判据：全量 pytest 769+新增 全绿；写口审计 --strict PASS（零新表零 DDL，词表
16 表不变）；M0 黄金零触碰；SPEC-10 v0.2 + SPEC-9 增补随交付导出；交付报告
落 `out/_p1_final/`。
