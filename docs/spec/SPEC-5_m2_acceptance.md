# SPEC-5 M2 验收测试与门禁清单（机器可检）

> 来源：SPEC-3 §1–§8（唯一机制输入：提示词/版本锚/解析/考试变体）；SPEC-6 §1–§8（唯一机制输入：题库/出卷/判分/播种/供给）；设计文档 §16.6（每步验收机器可检、人只审里程碑）与 §16.7-2/-3/-8；SPEC-1 §2/§6（DDL 与不变量）；SPEC-5 M1 卷（§4/§7 判据形态与基线，本卷不取代它——M1 判据继续作为回归底线）。
> 读者：AI 编码代理与验收执行者。每条验收 = **【判据】可执行命令或 pytest 用例名 + 【期望】精确输出**；判据通过 = 命令退出码 0 且输出匹配期望模式。发现本文件与 SPEC-3/SPEC-6 矛盾，以 SPEC-3/SPEC-6 为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 基线快照（2026-10-11，本卷导出时点，M1 终态）：全量 `python -m pytest` → **535 passed, 0 skipped**（`--collect-only` 535 项）；黄金回归 = M0 四类 34 例 + M1 四新类（cluster 4 / dispatch 4 / budget 3 / re_route 4）+ 元断言，`sha256sum -c results/_golden_m0_baseline.sha256`（66 文件）全 OK；官方矩阵 `results/experiments-full`（workers=3，n_rows=507，162/162 全达标）；params.yaml exam 节 M0 定稿值（ENTRY_DIRECT=0.30 等）。
> 状态：**定稿 v1.0（2026-10-11，SPEC-3/SPEC-6 同日落盘后导出）**——本轮决策 D27–D29（§11 决策记录，已同步 `docs/spec/DECISIONS.md`）。实现期发现的进一步未定细节按滚动协议记 `DECISIONS.md`。

---

## 0. 判定总则

- **命令口径**：pytest 统一 `python -m pytest <路径> -q`（pyproject `addopts=-q`）；工作目录 = 仓根 `/data/Qswarm`；exit 0 = 通过。
- **离线纪律**：全部 pytest 判据在离线环境可复现——LLM 调用一律经注入 adapter（fake opener / FakeAdapter 脚本化响应）；真实网络调用只出现在 §6.2 在线执行证据，不进 CI。
- **位级纪律**：凡标注「逐位/逐字段/逐字节」的判据一律 `==` 全等，无容差（M1 卷 §0 同款）。
- **期望计数**：N 为导出时点基线，实现新增测试使 N 只增不减；「≥ 基线」即为判据。
- **证据留痕**：判据输出摘录进交付报告；播种级证据落盘 `out/seeding_report.{json,md}` 与 `results/`。
- **冻结约束**：SPEC-3、SPEC-6、本文件三份同日（2026-10-11）定稿落盘，冻结解除——实现子任务自本卷落盘起放行（免审分支见 §9.3）。

---

## 1. A 组：M2 功能验收（判据 = 测试文件 + 最低用例名集）

M2 六域 ↔ SPEC 出处：prompts（SPEC-3 §1–§5）、supply（SPEC-6 §5）、bank（SPEC-6 §1）、paper（SPEC-6 §3.1）、judge（SPEC-6 §3.3）、store/roster/seeding/runner（SPEC-6 §3.4–§4）。测试文件为 M2 新增；下列用例名为**门禁最低集**（实现可增不可缺）。

### 1.1 prompts：版本锚 / 装配 / 解析（SPEC-3 §1–§4）

【判据】`python -m pytest tests/test_prompts_render.py tests/test_prompts_parse.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据（SPEC-3 出处） |
|---|---|
| `test_contract_version_and_agent_id_derivation` | CONTRACT_VERSION="cv1"；agent_id = f"{model_ref}@{cv}"；model_ref="{tier}:{provider}/{model}"（§1） |
| `test_template_hash_golden_bump_required` | 全部模板常量 sha256 与冻结值一致；文本变化而 CONTRACT_VERSION 未 bump → 红（§1/§8） |
| `test_schema_fields_cover_model_fields` | 每角色 $schema_fields 字段集 ≡ 对应 pydantic 模型字段集；枚举值域逐字展开（§2.3） |
| `test_render_pure_hashseed_free_zero_io` | 同输入跨 PYTHONHASHSEED 子进程逐位一致；prompts 包无 open/网络/随机源（§3/§8） |
| `test_render_five_segments_and_guard` | system 五段齐备且尾部 = INJECTION_GUARD；③④段文案 ≡ kernel.dispatch 常量逐字（§2.2/E7） |
| `test_render_untrusted_blocks_order_preserved` | 每块按 UNTRUSTED_BLOCK 渲染；blocks 顺序 = 传入顺序（§3/E5） |
| `test_render_forged_delimiter_notice` | 数据含伪造分隔行 → INJECTION_SUSPECT_NOTICE 附加；无命中不附加；原文不转义改写（§3/E6） |
| `test_render_missing_placeholder_raises` | Template.substitute 缺实参 ValueError（非 safe_substitute 静默）（§2.1） |
| `test_render_exam_only_appends_appendix` | render_exam 与 render 差异 = system 尾部追加 EXAM_APPENDIX，其余逐位一致（§5/E4） |
| `test_extract_json_full_then_last_fence` | 全文 JSON 成功→原文；围栏→取最后一个；无 → SchemaRejected("no_json")；不修补（§4/E3） |
| `test_parse_output_role_dispatch_and_ref_guard` | 五角色模型分发；ref 非 ^artifact:// → SchemaRejected(...ref_not_artifact)；"artifact://pending" 放行（§4） |
| `test_parse_output_rejects_out_of_vocabulary` | severity/kind/outcome 越词表 → SchemaRejected 且 reason 可断言（§4） |
| `test_parse_output_deterministic` | 同 raw 恒等（含异常类型与 reason）（§4） |

### 1.2 supply：配置与适配器（SPEC-6 §5）

【判据】`python -m pytest tests/test_supply_config.py tests/test_supply_adapter.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_models_config_loads_and_forbids_extra` | 参考形态三节装载；未知键 extra=forbid 拒绝（§5.1） |
| `test_models_config_domain_validation` | min_pass ∉ [0,1] / 档位键越 {cheap,strong,mid,engineering} / 缺节 → ValueError（§5.1） |
| `test_adapter_lazy_key_lookup` | 构造不读 env；complete() 时才取 api_key_env（§5.1） |
| `test_adapter_openai_compatible_wire` | fake opener 下请求 URL/方法/体结构符合 chat/completions 族；messages 直传；temperature 逐字透传；max_output_tokens=4096（§5.2） |
| `test_adapter_anthropic_wire_system_merge` | 多条 system Message 拼接为单 system 参数；user 直传（§5.2） |
| `test_adapter_error_no_key_leak` | 非 2xx/超时 → AdapterError；异常消息与 __str__ 不含 key 值（§5.2/§11.6-5） |
| `test_adapter_offline_via_injected_opener` | 全部上述断言经注入 opener 离线完成；用例文件无真实网络调用（§5.2/D29） |

### 1.3 bank：题库契约与校验（SPEC-6 §1）

【判据】`python -m pytest tests/test_exam_bank.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_seed_item_schema_and_vocabulary` | SeedItem 字段与 DDL 列映射；expected/source/family 词表校验（§1.1） |
| `test_bank_loader_validates_five_rules` | §1.1 校验器五条逐条负例（唯一性/快照在位/canary 在位/原版直通红线/配额） |
| `test_bank_quota_table_v0` | 100 题；逐家族 real/fp 配额 ≡ SPEC-6 §1.2 表（injection 14/10 … mem_safety 8/4） |
| `test_canary_unique_and_present_in_snapshot` | canary 全库唯一且在快照文本 ≥1 次（§1.4） |
| `test_no_unmutated_patcheval_items` | source=patcheval_mutated 必附变异元数据；cve_rollback 必附 upstream（CVE+parent sha）；原版直通 0（§1.1-4/§16.7-3） |
| `test_snapshot_ingest_registers_seed_artifacts` | 快照文件入工件库 kind=seed + artifacts 索引行；repo_snapshot_ref 可反查（§1.3） |

### 1.4 paper：确定性出卷（SPEC-6 §3.1）

【判据】`python -m pytest tests/test_exam_paper.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_draw_paper_shape_and_positions` | ENTRY=12 题=8 真 4 雷；真雷位次固定交错模式（§3.1/S3） |
| `test_draw_paper_deterministic_same_inputs` | 同 (seed, bank_version, agent_id, family) 重放逐位同卷；跨 PYTHONHASHSEED 一致 |
| `test_draw_paper_varies_across_agents_and_versions` | 不同 agent / 不同 bank_version 卷不同（非全同抽查） |
| `test_draw_paper_no_duplicate_within_paper` | 卷内题不重复；池不足 ValueError（§3.1） |
| `test_draw_paper_rejects_non_entry_purpose` | promotion/periodic → NotImplementedError（M2 范围裁剪，S4/S8） |

### 1.5 judge：判分与及格线（SPEC-6 §3.3）

【判据】`python -m pytest tests/test_exam_judge.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_judge_scout_matrix` | real: suspicious∧guess_family 命中=对；fp: suspicious=False∨guess=None=对；parsed=None 按 real 错/fp 对（§3.3） |
| `test_judge_auditor_matrix` | real: 产出∧family 命中=对；产出但家族错=错；fp: declined=对；injection_flag 不判分（§3.3） |
| `test_judge_rejects_reviewer_verifier` | 两角色 NotImplementedError（S4） |
| `test_pass_line_functions_literal` | promotion_exam_passes（真题全对∧拒雷≥1）/ periodic（≥75%∧真≥2/3∧雷≥1/2）/ entry_qc_pass 边界逐字（§3.3/§8.2） |

### 1.6 store / roster / seeding（SPEC-6 §4）

【判据】`python -m pytest tests/test_exam_store.py tests/test_exam_seeding.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_exam_items_upsert_version_advance` | 同 item_id 高 version → 全列 UPDATE；retired_at 仅移出题库时置位（§4.3） |
| `test_exam_results_seq_and_row_idempotency` | ER-{seq} 按 D20 库内同前缀 max+1；行幂等键 (agent_id, purpose, item_id) 重跑跳过（§4.3） |
| `test_register_roster_identity_pool_class_idempotent` | agent_id=model_ref@cv；hats pool_class scout=temp 余 formal；同身份重注册跳过、字段冲突 ValueError（§4.1/S10） |
| `test_expand_candidates_dedup_by_model` | primary+fallbacks 去重展开 × 角色档位映射；同 (role, model_ref) 唯一；params.roster 不约束（§4.1/S10） |
| `test_seed_entry_deposit_shape_m0_parity` | 每格恰一笔 ExamDepositPayload：S1 / direction±1 / weight∈[0.2,0.5] / origin=exam / exam_id=f"entry:{agent_id}:{family}" / taxonomy_path=[family]（§4.2） |
| `test_seed_entry_event_order_deterministic` | agents 注册序 × taxonomy.yaml 序；重跑（同缓存）事件流逐位一致（§4.2） |
| `test_seed_entry_rerun_zero_new_events` | 已完成播种的账本重跑 → 零新存款零新 exam_results 行（读侧幂等）（§4.2/B2） |
| `test_seed_entry_qc_gate_min_pass` | 总正确率 < entry_exams.min_pass → status 留 provisioning + 告警记录；≥ → active；存款照写（§4.5/S11） |
| `test_seed_entry_infra_invalid_cell` | 同格基建失败 >2 题 → 零存款零行 + 报告 infra_invalid；≤2 → 部分作答重跑收敛（§3.4/S7） |
| `test_seed_entry_schema_fail_counts_wrong` | schema 两连败 → correct=0 落行 + schema_fail 计数进报告（§3.4/S7） |
| `test_agg_cells_folds_exam_share` | 播种后 agg_cells：格值含 origin=exam 份额；T_HALF_DAYS_EXAM=15 衰减路径生效（§4.2） |

### 1.7 runner：缓存与复现（SPEC-6 §3.4/§3.5）

【判据】`python -m pytest tests/test_exam_runner.py -q`
【期望】全 passed；最低用例名：

| 用例名 | 钉住的判据 |
|---|---|
| `test_runner_cache_replay_bit_identical` | 同响应缓存两次 `--replay`：exam_results 行集 + deposit 事件流（seq+payload）逐字节一致（§3.5/S9） |
| `test_runner_replay_cross_pythonhashseed` | replay 跨 PYTHONHASHSEED 逐位一致（§3.5） |
| `test_runner_primary_only_no_fallback` | primary 失败 → 基建失败路径；fallback 条目不参与考试调用（§3.2/S6） |
| `test_runner_logs_response_artifacts` | 每调用写 exam_log 工件（kind=exam_log，producer=agent_id）+ artifacts 行；判分可独立复核（§3.4） |
| `test_runner_canary_hit_recorded` | 输出含 canary → 判分照常 + canary_hit 进报告（§3.4/§1.4） |
| `test_seeding_report_schema_no_secrets` | 报告 JSON 键集/类型黄金回归；全文 grep 无 api_key 值形态（§4.4） |

---

## 2. B 组：不变量验收

### 2.1 写口审计扩展（SPEC-6 §0.2 写口纪律 / M1 卷 §2.1 模式）

【判据 1（测试）】`python -m pytest tests/test_kernel_write_guard.py -q` 全 passed——词表扩展为 M1 全表 ∪ {agents, hats, exam_items, exam_results}，后四表唯一写口模块 = `qswarm/exams`。
【判据 2（独立 grep，交付报告粘贴输出）】

```bash
grep -rnE "\b(INSERT[[:space:]]+INTO|REPLACE[[:space:]]+INTO|UPDATE|DELETE[[:space:]]+FROM)[[:space:]]+(agents|hats|exam_items|exam_results)\b" \
  qswarm/ --include="*.py" | grep -v "^qswarm/exams/"
```
【期望】输出为空（无越界）。kernel 侧旧词表 grep（M1 卷 §2.1 判据 2）继续空输出——M2 不得在 kernel/ 增任何 SQL 写口。

### 2.2 幂等双跑

【判据】`python -m pytest tests/test_exam_store.py tests/test_exam_seeding.py -k "idempotent or rerun or upsert" -q`
【期望】全 passed，收集数 ≥ 3（exam_results 行幂等 / roster 重注册幂等 / seed_entry 重跑零新事件）。

### 2.3 纯度判据（prompts 零 IO 零随机；exams 随机仅派生）

```bash
grep -nE "\brandom\b|\brng\b|\bopen\(|\bsubprocess\b" qswarm/prompts/ --include="*.py"
```
【期望】0 命中（prompts 纯函数域）。`qswarm/exams/paper.py` 的 `random.Random(` 出现处均以 `derive_seed(...)` 派生种子为实参（评审项 + `test_draw_paper_deterministic_same_inputs` 机器钉）。

### 2.4 exams/supply 不触机制层

【判据】`python -m pytest tests/test_sim_engine.py tests/test_kernel_ledger.py -q` 全 passed（M0/M1 机制面既有行为测试不回退——exams/supply 不得 import 机制层可变状态；`grep -rn "from qswarm.exams\|from qswarm.supply" qswarm/kernel/ qswarm/contracts/ qswarm/simulator/ --include="*.py"` 输出为空，机制层零反向依赖）。

---

## 3. C 组：可复现与等价

### 3.1 exam_math 纯移动等价（S5 三关）

- 【判据 1】`python -m pytest tests/test_sim_exam.py -q` 全 passed（`centered_exam_weight` 经 re-export 的全部既有用例绿——import 面兼容证明）；
- 【判据 2】`grep -n "def centered_exam_weight" qswarm/kernel/exam_math.py` 命中且 `qswarm/simulator/exam.py` 含 `from ..kernel.exam_math import centered_exam_weight`；
- 【判据 3】位级保险：`python experiments/tune_round.py --round 60 --dims "N=6" --workers 3` 后 `python experiments/compare_rerun.py --base results/experiments-full/results.csv --rerun results/tune-round60/results.csv --group "N06-D0.05-T0.60-E0.30-V0.20"` → `ALL_EQUAL=true`（M2 未触仿真行为，全矩阵免复跑〔§6.1 触发条件不成立〕，单轮位级复核为移动等价证据）。

### 3.2 响应缓存 replay（SPEC-6 §3.5）

【判据】§1.7 `test_runner_cache_replay_bit_identical` + `test_runner_replay_cross_pythonhashseed` 全 passed——确定性边界 = 缓存之后（S9）：出卷、解析、判分、落账、折叠全链零随机零时钟漂移。

---

## 4. D 组：M0/M1 基线不回归（回归底线，逐条必跑）

- 【判据 1】全量 `python -m pytest` → `N passed`、`0 failed`、`0 skipped`，**N ≥ 535 + M2 新增**；
- 【判据 2】`sha256sum -c results/_golden_m0_baseline.sha256 --quiet && echo SHA256_MANIFEST_ALL_OK` → 打印且退出码 0（M1 卷 §4.1 判据 B 原样沿承）；
- 【判据 3】`python -m pytest tests/test_golden_regression.py tests/test_golden_mutation_gate.py -q` 全 passed（黄金回归 + 突变门不回退）；
- 【判据 4】`python3 scripts/freeze_golden.py --seed 7 --n-days 30 --check` 与 `python3 scripts/freeze_golden.py --cluster --check` 双双退出码 0（M1 终检恢复的 D 组终判据维持）；
- 【判据 5】M1 卷 A 组五文件命令（其 §1.1–§1.5）全绿——M1 最低用例集 47/47 不回退。
- 【跌标处置】D 组任一红 = M2 实现引入行为变更：非 sanctioned 立即停止修复；属 sanctioned（如 DDL 注释级修订）走重冻流程并记 DECISIONS，禁止静默。

---

## 5. E 组：M2 交付物验收（题库建成 + 播种能力）

### 5.1 题库 v0 建成（离线判据，门禁必过）

```bash
python3 - <<'EOF'
import json, sys
sys.path.insert(0, ".")
from qswarm.exams.bank import load_bank, validate_bank
bank = load_bank("seeds/bank.yaml")
validate_bank(bank)
assert bank.bank_version >= 1
q = bank.quota_table()   # {(family): (real, fp)}
expect = {"injection": (14,10), "deser": (10,6), "crypto": (10,6),
          "authz": (10,6), "config_dep": (10,6), "mem_safety": (8,4)}
assert q == expect, q
assert len(bank.items) == 100 and sum(r for r,_ in expect.values()) == 62
assert sum(f for _,f in expect.values()) == 38
canaries = [i.canary for i in bank.items]
assert len(set(canaries)) == 100
print("SEED_BANK_V0 100 items quota OK canary OK")
EOF
```
【期望】末行 `SEED_BANK_V0 100 items quota OK canary OK`，退出码 0。（实现侧允许等价脚本入口，字面行不变。）

### 5.2 播种执行证据（在线判据，D27 二分口径）

- **有 key 环境**（models.yaml 四档 api_key_env 就绪）：
  ```bash
  python -m qswarm.exams.seeding --bank seeds/bank.yaml --models configs/models.yaml \
    --db out/m2-seeding/seeds.db --out out/m2-seeding 2>&1 | tee results/_m2_seeding.log
  python -m qswarm.exams.seeding --replay --cache out/m2-seeding/cache/ \
    --db out/m2-seeding/replay.db --out out/m2-seeding-replay
  ```
  【期望】① 播种报告生成且 `degraded=false`（无效格 ≤10%）；② replay 库与在线库的 `exam_results` 行集与 deposit 事件流逐字节一致（比对脚本输出 `SEEDING_REPLAY ALL_EQUAL`）；③ 下方 SQL 断言全过。
- **无 key 环境**：FakeAdapter 端到端用例（§1.6/§1.7 全家）即为放行下限；在线执行如实记遗留问题（附环境缺口说明），**不得伪造或静默当绿**（D27）。

```bash
python3 - <<'EOF'
import json, sqlite3
db = sqlite3.connect("out/m2-seeding/seeds.db")
n_cands = db.execute(
    "SELECT COUNT(*) FROM agents a JOIN hats h USING(agent_id) WHERE h.role IN ('scout','auditor')"
).fetchone()[0]
dep_rows = db.execute("SELECT payload FROM ledger WHERE kind='deposit'").fetchall()
dep = len(dep_rows)
rows = db.execute("SELECT COUNT(*) FROM exam_results WHERE purpose='entry'").fetchone()[0]
assert dep == n_cands * 6, (dep, n_cands)          # 每 (候选, 家族) 恰一笔（全有效口径）
assert rows == n_cands * 6 * 12, (rows, n_cands)   # 每格 12 题逐题行（全有效口径）
origins = {json.loads(p)["origin"] for (p,) in dep_rows}
assert origins == {"exam"}, origins
roles = {json.loads(p)["role"] for (p,) in dep_rows}
assert roles == {"scout", "auditor"}, roles        # S4：v0 只播 scout/auditor
print(f"SEEDING deposits={dep} rows={rows} candidates={n_cands} OK")
EOF
```
【期望】末行 `SEEDING deposits=24 rows=288 candidates=4 OK`（参考 models.yaml 形态：cheap×2+strong×2；形态变化按公式断言，字面行随 n_cands 变）。

### 5.3 QC 与报告形态

- 【判据】`out/m2-seeding/seeding_report.json` 存在且：逐 agent 逐家族 accuracy/direction/weight 判分明细齐备；QC 判定行与 agents.status 流转一致（min_pass 之下 → provisioning）；schema_fail / infra 失败 / canary_hit 计数在位；`degraded` 标记与无效格占比一致（§4.4/§4.5）。
- 【判据】`test_seeding_report_schema_no_secrets` 黄金回归绿（键集钉死）。

---

## 6. F 组：约束判据

### 6.1 官方矩阵复跑触发条件（不触发声明）

【判据】交付报告含声明：M2 变更面 = prompts/exams/supply 新增 + kernel/exam_math 纯移动 + kernel/dispatch ROLE_OUTPUT_SCHEMA 一行 + contracts 模型追加——仿真行为零变化（§3.1 判据 3 位级证据），官方全矩阵**免复跑**；若实现期引入任何仿真路径变更则升级为必跑（M1 卷 §5 全套判据）。

### 6.2 网络判据（修订口径，DECISIONS 2026-10-11 修订行）

```bash
grep -rnE "\b(socket|ssl|urllib|urllib3|http\.client|httpx|requests|aiohttp|ftplib|smtplib|telnetlib)\b" \
  qswarm/ --include="*.py" | grep -v "^qswarm/supply/adapter.py"
```
【期望】0 命中。附加白名单判据（`tests/test_supply_adapter.py::test_adapter_offline_via_injected_opener` 内或独立用例）：AST 扫描 `qswarm/supply/adapter.py` import 根闭集 ⊆ {urllib.request, urllib.error, urllib.parse, json, os, time, dataclasses, typing}——逐文件钉死，越界即红。

### 6.3 依赖闭集（M1 卷 §7.2 命令修订）

```bash
grep -rhE "^\s*(import|from) " qswarm/ --include="*.py" | grep -vE "^\s*(from|import) \." \
  | sed -E 's/^\s+//; s/^(from|import) ([a-zA-Z_0-9]+).*/\2/' | sort -u \
  | grep -vx -f <(printf '%s\n' __future__ argparse bisect collections concurrent contextlib csv dataclasses datetime enum functools hashlib importlib itertools json math os pathlib pydantic qswarm random re shutil sqlite3 statistics string subprocess sys tempfile time types typing urllib yaml)
```
【期望】输出为空（qswarm/ 第三方根 ⊆ {pydantic, yaml}；新增白名单根 = urllib——stdlib（supply/adapter，DECISIONS 2026-10-11 修订行）与 string——stdlib（prompts/render string.Template）；M2 卷原转抄缺 M1 卷 §7.2 既有 8 根（bisect/concurrent/hashlib/importlib/itertools/shutil/subprocess），已按滚动协议以 M1 卷 §7.2 全集为基修订，修订留痕不回改编号语义）。`pyproject.toml` 依赖零变更（交付报告 diff 留痕）。禁网络 + 依赖白名单继续保证「无 LLM SDK」。

### 6.4 串行执行

【判据】播种与 replay 单进程串行：`grep -nE "ThreadPool|multiprocessing|asyncio|concurrent\.futures" qswarm/exams/ qswarm/supply/ --include="*.py"` → 0 命中；RPM 限速经最小调用间隔实现（§5.1，`test_adapter_offline_via_injected_opener` 覆盖间隔逻辑）。experiments 矩阵命令不受影响（仍 M1 卷 §7.3 口径）。

### 6.5 DECISIONS 同步

【判据】`docs/spec/DECISIONS.md` 含 M2 实现期新增五字段行，交付报告附新增行清单；实现与记录一致（评审项）。

---

## 7. G 组：交付对账（终检子任务输入）

| # | 项 | 判据/出处 |
|---|---|---|
| 1 | 变更清单 | 文件级（新增/修改/删除）+ 一句话目的（区分机制层/考试域/供给边缘/外网侧脚本） |
| 2 | SPEC-实现对账表 | SPEC-3 §0.2 与 SPEC-6 §0.2 每行 → 落位文件 + 测试文件（§1 最低集逐行勾稽） |
| 3 | 写口 grep 审计证据 | §2.1 判据 2 命令与空输出粘贴 + audit_write_ports --strict PASS |
| 4 | DECISIONS 新增行 | §6.5 |
| 5 | 遗留问题 | 逐条（无 key 环境的在线播种缺口如实列首位，若适用） |
| 6 | 免审直跑记录 | §9.3；SPEC-3、SPEC-6、本卷三份全文进交付报告 |
| 7 | 题库与播种证据 | §5.1/§5.2/§5.3 字面行 + 播种报告路径 |

---

## 8. 判据索引（对照 SPEC-6 §8 门禁与里程碑）

| 来源 | 本文件判据节 |
|---|---|
| SPEC-6 §8「题库」 | §1.3（A3）、§5.1 |
| SPEC-6 §8「出卷/判分」 | §1.4（A4）、§1.5（A5） |
| SPEC-6 §8「播种/复现/质检/写口/约束」 | §1.6–§1.7、§2（B）、§3（C）、§5.2–§5.3、§6（F） |
| SPEC-3 §8 门禁 | §1.1（A1） |
| M0/M1 基线底线 | §4（D 组，含 M1 卷 §1 五命令与 digest 双判据） |
| SPEC-6 §7 S1–S11 决策的用例承载 | §1 各表「钉住的判据」列逐行可溯 |

---

## 9. 门禁节（M2 → M3 放行条件）

### 9.1 通过判定（全部勾选方放行）

- [ ] **A 组六域全绿**：§1.1–§1.7 七条 pytest 命令全 passed，最低用例名集齐（合计 ≥ 55 例）；
- [ ] **不变量**：写口扩展双判据（测试 + 独立 grep 空）+ 旧 kernel 词表 grep 仍空；幂等 ≥3 例；prompts 纯度 grep 空；机制层零反向依赖 grep 空；
- [ ] **可复现**：§3.1 exam_math 三关绿（含单轮 `ALL_EQUAL=true`）；§3.2 缓存 replay 位级（含跨 PYTHONHASHSEED）；
- [ ] **基线**：D 组五判据全绿（全量 ≥535+新增 0 skip、`SHA256_MANIFEST_ALL_OK`、黄金回归+突变门、双 `--check` exit 0、M1 A 组五命令绿）；
- [ ] **交付物**：§5.1 题库字面行 `SEED_BANK_V0 ... OK`；§5.2 离线判据全过（在线证据按 D27 二分：有 key 附 `SEEDING ... OK` + `SEEDING_REPLAY ALL_EQUAL`，无 key 遗留问题如实记录）；§5.3 报告形态与 QC 一致；
- [ ] **约束**：网络修订口径 grep 空 + adapter import 白名单、依赖闭集命令空输出、串行执行 grep 空、DECISIONS 同步；
- [ ] **交付对账**：§7 七项齐备。

**人审物**（§16.6）：门禁节勾选状态 + 交付报告 + `out/seeding_report.{json,md}` + results/ 证据文件；人审不重复执行机器判据。

### 9.2 停止判定（跌标即停，任一触发即停）

1. 任一判据命令非零退出 / 期望字面未出现，且按本清单无既定修复路径；
2. D 组任一红且不属 sanctioned 变更（M0/M1 基线被 M2 破坏）；
3. 题库校验器任一条红（尤其原版直通红线：出现未变异 PatchEval 原题 = 数据污染事故，立即停止并复盘取材段）；
4. canary 泄漏实锤（模型输出复现 canary 串且可归因预训练语料）→ 该题下机轮换，全库污染评估后才可继续；
5. 需要「伪造在线证据、放宽 QC 线、跳过判据」才能继续的任何情形。

停止后动作：原样记录触发项与现场输出，如实报告，**不得放行**；等待负责人明示后才可继续。

### 9.3 人工批准 / 免审直跑分支

- **分支裁定（2026-10-11，承 M1 卷 §9.3 执行记录先例）**：本执行环境仍为自治执行会话、无负责人实时审批回路 → 沿用**分支 (ii) 免审直跑**（D28，已记 `DECISIONS.md`）；门禁不因免审降档——§9.1 全项、§9.2 停止判定照常执行。
- **可检判据**：交付报告附 SPEC-3、SPEC-6、本卷三份全文（分支 (ii) 要求）；三份 SPEC 定稿落盘时刻戳与本卷落盘时刻的先后关系以文件时间佐证（§0 冻结约束）。

---

## 10. 决策记录（定稿拍板；五字段；已逐条同步 `docs/spec/DECISIONS.md`）

| 日期 | 模块 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| 2026-10-11 | SPEC-5 | D27：播种执行证据二分——离线判据（FakeAdapter 端到端 + 形态/幂等/QC 判据）为门禁必过项；在线播种证据（真实 LLM 调用）依赖外部资源（models.yaml 四档 api_key_env），有 key 环境附 `SEEDING ... OK` + `SEEDING_REPLAY ALL_EQUAL`，无 key 环境如实记遗留问题不得伪造 | 播种管道的正确性可完全离线机器验证（缓存 replay 位级 = 在线与离线共用同一判分/落账代码路径）；外部资源可用性不是代码验收项，混入门禁会使里程碑被供应商状态绑架 | §5.2 二分口径；§9.1「交付物」勾选措辞；M3 起有 key 环境补在线证据即自动闭合 |
| 2026-10-11 | SPEC-5 | D28：免审直跑沿用——环境判定与 M1 卷 §9.3 执行记录同口径（自治会话、无审批回路），三 SPEC 定稿落盘后直接放行 M2 实现子任务 | M1 卷 D26 同款理由；门禁不降档 | §9.3；SPEC-3/SPEC-6/本卷三份全文进交付报告 |
| 2026-10-11 | SPEC-6 | D29：adapter 传输入口可注入（构造参数 opener，缺省 urllib.request.urlopen）——SPEC-6 §5.2 增一句（修订留痕） | 线格式与错误映射的判据必须离线可执行（§0 离线纪律）；注入点是实现一行、测试全部离线化的最小改动 | SPEC-6 §5.2；§1.2 `test_adapter_offline_via_injected_opener` 判据成立的前提 |

> 滚动协议：本文件为 M2 里程碑的验收快照；实现期发现判据不可执行时按滚动协议记 `DECISIONS.md` 并修订本文件（修订留痕，不回改编号语义）；M1 卷继续作为回归底线，不被本卷取代。
