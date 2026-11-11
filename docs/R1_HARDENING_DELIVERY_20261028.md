# R1 HARDENING 交付报告（正确性 / 安全边界 / 可交付性修复轮）

日期：2026-10-28 · 依据：`docs/HANDOVER_GLM_R1_HARDENING_20261028.md`（负责人指令
「按这个开始做」= §16 启动指令授权）· 语义变更说明：`docs/R1_CHANGE_NOTE.md`
（先行交付，不倒填批准）· 交付工件：`out/r1-hardening/r1-20261028T141250Z/`
（基线指纹 / 红测记录 / 全量日志 / 打包检查）· 线上模型调用 = **0**（零密钥读取、
零预算消耗、budget_ledger 零新行）。

## §0 任务状态总表

| 任务 | 状态 | 交付面（摘要） |
|---|---|---|
| T0 基线/红测 | **PASS** | 基线 `baseline.json`（HEAD 6e32fb3 + 工作区 sha256 指纹 + docker 探针 rc=1）；§4.2 八项反例先红（10 测试全 FAIL，`reproductions/t0_red_run.txt`）后随各任务转绿 |
| T1 执行边界 | **PASS** | 可信根门（fixtures+显式 trusted_roots，realpath 防逃逸）先于 pip/copytree/Popen；deps 拒绝矩阵（URL/VCS/path/选项/换行）；缺省拒运行时宿主 pip 与联网 build/拉取；env 最小 allowlist（假密钥实测不透传）；输出限额**读取时**生效+killpg 进程组；禁网探针失败 fail-closed；清理有界 |
| T2 可信写入门 | **PASS** | `kernel/trust.py`（r1-tv1 授权契约：mint 仅 execution_paired+oc-v2 可铸、mechanical_single 拒铸；摘要重算+verify_auth 工件在库复核）；submit(verifier) real/fp 无授权先于一切写拒；normalize_outcome 无授权→verify_error（outcome_raw/trust_gate/err_no_trust 留档）；propagate_verdict(S2)/propagate_pending 同一 eligibility（旧事件恒不补发，ineligible 如实记）；oracle oc-v2（paired 健康分立+观察器宿主侧边界：临时根词法约束、symlink/越界/过大拒读） |
| T3 身份/缓存/恢复 | **PASS** | `kernel/identity.py`（id-v1：run manifest 冻结〔repo_key=resolved path、snapshot_digest=实际文件内容含 dirty〕同 run 换输入写入前拒；候选三身份 occurrence 入表主键；L1/L2 task 内容寻址含 run——第二 run 不被吞〔实测 4 注册/4 处理 → 3/3 对等〕）；register owner-run 守卫+scout 提交归属/预期双核验；consult 源 scan 快照核验；IndexCache key 扩维+实例重绑（同 bytes 异 path 修复） |
| T4 路由准入 | **PASS** | 显式 pool=完整可派范围（专员/探索全过滤池内，pool=[] 必空，pool=None 旧推导保留）；consult 派单/改派前资格 recheck；model_ref 缺席显式失败分类（verify/consult 双侧，不空串调用） |
| T5 指标纠错 | **PASS** | evidence_v3/capability_v3：population 分立（1 错正向+9 正确拒判 → precision=0 非 0.9，正确拒判另列）；direction=0=忽略（不产生 refuted、不造 disputed）；富化只用 as_of 内事件（未来 finding 不改历史投影哈希，实测钉死） |
| T6 校准材料 v2 | **PASS（机械验收）** | calibration_v2/：25 项同覆盖面去泄题重建（docstring 剥离+生成期泄题词硬门；claim/poc 双问题分立；根因分组 1/7/8/9 同组 19 组）；truth_map_v2 单一来源；compute_agreement_v2 边界健壮（零判题/零交集→not_estimable）；ERRATA_v1.md 六项勘误；**v1 材料冻结不动**；人工判题/裁判选型未执行（D46 红线维持，NOT VERIFIED 如实） |
| T7 打包/干净安装 | **PASS** | pyproject 补 exams/prompts/supply/sandbox(+fixtures)/defaults；默认资源入包（importlib.resources；与 configs/ sha256 哈希同步测试钉死；显式路径不存在即报错）；**干净安装链真机全绿**：源码快照→sdist→由 sdist 构建 wheel（离线，系统 setuptools）→清单检查（无密钥/DB/out/缓存，四子包+资源在包）→非 editable 安装→无关 cwd 清 PYTHONPATH 导入+默认资源+离线最小闭环（账本+候选+FakeRunner+oc-v2）→卸载还原；记录 `package-check/check.log` + 构建产物 |
| T8 总验收 | **PASS** | 全量 pytest **832 passed / 0 failed / 1 skipped**（321.96s；skip=container 真机项 docker daemon 不可达——开发可选项显式 skip，**不记为 container 验收 PASS**）；写口审计 `--strict` **PASS**（违规 0/漂移 0）；E0 runner C1–C5 升 oc-v2/v3 口径真机全过（含新增拒判混池反例判据，out/design-revision/e0-20261028T150748Z/） |

## §1 判据与命令（退出码）

| 命令 | 结果 |
|---|---|
| `.venv/bin/python -m pytest` | 832 passed / 1 skipped / **0 failed**（exit 0） |
| `.venv/bin/python -m qswarm.kernel.write_guard --strict` | **PASS**（违规 0 / 漂移 0；runs 写口归 dispatch 保持） |
| `.venv/bin/python experiments/design_revision/run_e0_checks.py` | C1–C5 全 PASS（exit 0） |
| `bash out/r1-hardening/package_check.sh <run-id>` | 全链 PASS（exit 0；wheel sha256 见 check.log） |
| `docker info --format ok` | rc=1（daemon 不可达）——container 真机项 NOT VERIFIED（环境阻断如实） |

## §2 新增/修改测试与代码

**新文件**：`qswarm/kernel/trust.py`（授权契约）、`qswarm/kernel/identity.py`（三身份+manifest）、
`qswarm/defaults/`（打包 canonical 副本×3）、`tests/test_r1_hardening.py`（19 例：八反例+决策矩阵+验收）、
`tests/test_r1_packaging.py`（7 例）、`experiments/design_revision/calibration_v2/`
（生成器+材料×2+truth_map+agreement_v2+ERRATA）、`docs/R1_CHANGE_NOTE.md`、本报告。

**修改（核心）**：sandbox/{runner,envcache,config,oracle,pocgen,trial}；kernel/{dispatch,cluster,
verify,consult,loop,route,evidence}；perception/{indexer,channels}；contracts/{config,models}；
exams/store.py（kind 词表+verify_auth）；pyproject.toml；scripts/freeze_golden.py（场景 S2 源升授权形态）；
tests/golden/cluster（按官方 regen 命令重冻，存款语义不变）；tests/golden_loader.py（黄金世界播种
verify_auth 工件行，不豁免新门）；60+ 处既有测试按语义变化更新（清单见 R1_CHANGE_NOTE §4）。

## §3 旧行为 → 新行为（关键面）

1. **verified_real 产出**：旧=执行绿+蜂报 real 即 L3+S2 存款；新=须内核铸造授权
   （execution_paired+oc-v2 健康分立），无授权 → verify_error（零 L3 零 S2，
   主张留档 outcome_raw/trust_gate）。mechanical_single 单次支持不自动授予 paired 级结论。
2. **S2 补偿**：旧=propagate_pending 见旧 verified_real 事件即补存款；新=与提交同一
   eligibility（旧事件恒不补发）。
3. **process 执行**：旧=任意 repo/deps 全继承 os.environ 执行；新=可信根内+deps 过矩阵+
   env allowlist+读时限额（假密钥实测不透传）。
4. **同库第二 run**：旧=候选/任务被首轮同名吞掉（实测 3 注册/0 处理）；新=occurrence
   三身份各自注册派单（实测对等处理）。
5. **precision**：旧=1 错正向+9 正确拒判=0.9；新=正向上报 precision=0（拒判另列）。
6. **direction=0**：旧=refuted 真值；新=忽略（零真值零 disputed）。
7. **route(pool=[])**：旧=仍可选中池外专员；新=必空派。
8. **同 bytes 异 path 索引**：旧=返回旧 path 实例；新=重绑当前 path。

## §4 语义变化说明（§2.1 要求件）

见 `docs/R1_CHANGE_NOTE.md`（旧语义/新语义/事件工件版本/兼容策略/受影响测试/迁移边界
六面齐全；SPEC-10 §8「只加报告」承诺与 SPEC-9 §4.5 reconciliation 已随 DECISIONS
2026-10-28 R1 行显式修订，未倒填批准）。

## §5 legacy 兼容与红线

- 旧 append-only 事件/旧工件/M0 黄金（budget/route 等）零触碰；cluster 黄金按官方
  regen 路径重冻且存款期望语义不变（002–004 deposits 15/17/17 保持）。
- 生产库零触碰（全部临时库 dry-run）；零 DDL（VerifyResultPayload additive 字段 +
  artifacts kind 词表值，write_guard --strict PASS）。
- 线上调用 0 / 密钥读取 0 / 未知依赖执行 0 / 真实外部 PoC 0。
- 既有未提交 P1 工作区全部保全（基线指纹比对可复核；本轮未 reset/clean/批量提交）。

## §6 NOT VERIFIED / 未完成项（如实）

1. **container 档真机红线五项**：docker daemon 不可达（rc=1）——NOT VERIFIED
   （开发可选项 skip ≠ 验收 PASS；release gate 需真机时按 BLOCKED 报告）。
   修复项：skip 条件已从「CLI 存在」修为 daemon 探针（退出码+stderr 双查）。
2. **E0 人工校准面**：25 例判定 + 跨族裁判选型未执行（D46 红线：GLM 族与施工 AI
   不任裁判）——校准配套 v2 已就位，待负责人。
3. **对抗性生产 oracle**：process 档观察器为 trusted-fixture instrumentation（同 uid、
   无命名空间分离），不声称具备对抗性生产 oracle；container 独立采集面未建（超本轮范围）。
4. **D44/D47/D48**：未裁决（不在本轮授权面）。

## §7 下一阶段申请事项（不自动执行）

按 HANDOVER §15 顺序：①获批环境复验 container 红线与独立观察；②人工审核 v2 校准材料
+跨族裁判供给+少量调用批准（D48 起算锚追认）；③窄范围真实试点（Python 注入/穿越，
候选+无结论+人工确认口径）；④同预算单强模型对照冻结；⑤2–3 个 execution profile
跨批次专长检验。本报告 + R1_CHANGE_NOTE + out/r1-hardening/ 工件即申请数据面。
