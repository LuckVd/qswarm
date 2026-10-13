# M3 交付报告：感知层 L0 v2 + L1 侦察路由 v1（SPEC-7 v1.0 / SPEC-5 M3 卷）

> 交付日期：2026-10-12 · 执行：M3 实现子任务（免审直跑，D26 同款环境判定：自治
> 执行会话、无负责人实时审批回路；门禁不因免审降档）
> 规格真源：`docs/spec/SPEC-7_perception.md` v1.0（D25–D30 六项全部负责人拍板）+
> `docs/spec/SPEC-5_m3_acceptance.md` v1.0
> 判定总则：分组判据机器可检；全程前置既有 611 例零回归。

---

## 0. 门禁结论

| 组 | 内容 | 判定 |
|---|---|---|
| A 功能 | perception 契约/三通道/索引/档位 + kernel.loop/bootstrap + exams 补播 | ✅ 全绿（46 例） |
| B 不变量 | 写口审计、纯度 import 钩子、网络单落点、params 越键 | ✅ 全绿（6 例） |
| C 可复现 | 扫描两跑位级一致、bootstrap 幂等、route 黄金回归 | ✅ 全绿 |
| D 端到端 | fast lane 全链（FakeAdapter 离线）+ 同仓库重跑幂等 | ✅ 全绿（2 例） |
| E 补播 | 判分链路（离线）全绿；**在线入职考待负责人放行**（卷面 §5 口径：不阻塞门禁） | ✅ 离线部分 |

**全量 pytest：672 passed / 0 failed / 0 skipped（611 例零回归 + 61 例 M3 新增）**
**写口审计：`WRITE-PORT AUDIT PASS`（15 表词表零违规零漂移，`--strict` 同）**

---

## 1. 落位清单（模块 ↔ SPEC-7 §0.2）

| 模块 | 状态 | 内容 |
|---|---|---|
| `qswarm/perception/`（新包） | ✅ | `clues.py`（家族线索表，纯数据，v1 词表允许扩充不收缩）、`candidates.py`（Candidate 内容寻址契约 + 归并/优先级，D27）、`indexer.py`（tree-sitter 探测式分级 D25 + 调用点/标识符/函数定义三档索引 + blake2b 内容寻址缓存）、`channels.py`（通道 A 结构 / B 模型直扫 / C semgrep 规则 + 分片器 + 失败语义）、`prompts.py`（L0 直扫模板，防线段复用 prompts.templates 单一源）、`scan.py`（fast/pr/full 三档入口 + l0_candidates 工件） |
| `kernel/loop.py` | ✅ | 生产编排循环：verify_ready 会话校验 → open_run → L0 扫描（候选入工件库+入表）→ 分片 → L1 每候选一任务（create/dispatch/submit 全状态机 + re_dispatch 重试）→ ×0.2 存款 → 影子 promote_scan（M3-7 三通道全零注入） |
| `kernel/bootstrap.py` | ✅ | D28：空 schema 建库 + 编制/考试存款一次性幂等导入（种子库 `mode=ro` 只读、payload 逐字 + `src_seq` 溯源）+ verify_ready 防空账本启动（4 active / ≥20 存款 fail-closed） |
| `exams/judge.py` | ✅ | judge_answer 扩 reviewer（direction=±1 象限，0 判错）/verifier（三值，verify_error 恒判错）；词表不改 |
| `exams/seeding.py` | ✅ | CLI `--roles`（缺省 scout,auditor 零扰动；越词表 SystemExit）；seed_entry roles 参数化（M2 已留口） |
| `exams/runner.py` | ✅ | 纸面考卷：reviewer 送审 finding 块 / verifier 预置簇块（`_exam_claim` 答案键对齐构造）+ goal 角色分派 |
| `exams/store.py` | ✅ | artifact kind 词表扩 `{l0_candidates, l0_index}`（M3-9；审计词表不动） |
| `exams/roster.py` | ✅ | `import_roster_rows`（D28 逐字导入，唯一写口内，M3-5） |
| `kernel/dispatch.py` | ✅ | `register_candidates`（candidates 表 INSERT 面，写口词表零扩，M3-2） |
| `configs/params.yaml` + `contracts/config.py` | ✅ | `perception` 节 7 旋钮回填（extra=forbid；缺节回落规格缺省保黄金兼容） |
| `configs/semgrep_rules.yaml` | ✅ | 通道 C 家族规则包 v1（16 规则/五家族 metadata.family 映射，D30 必选件） |
| `pyproject.toml` | ✅ | `qswarm.perception` 打包 + `perception` optional extra（tree-sitter 六门；核心闭集不破） |

## 2. A–E 组判据对照（逐条）

### A 组：功能验收
| M3 卷判据 | 测试 | 状态 |
|---|---|---|
| test_candidate_merge_overlapping | tests/test_perception_candidates.py | ✅ |
| test_candidate_order_deterministic | 同上 | ✅ |
| test_candidate_id_content_addressed | 同上 | ✅ |
| test_channel_a_tier1_callpoints | tests/test_perception_indexer.py | ✅ |
| test_channel_a_no_grammar_absent | 同上 | ✅ |
| test_channel_b_scout_identity | tests/test_kernel_loop_m3.py（l0scan 调用面 + 零存款断言 + 分片工件 producer=agent_id 归属）+ test_perception_channels.py（contract 装配 + 任务头渲染） | ✅ |
| test_channel_b_schema_fail_retry_once | tests/test_perception_channels.py | ✅ |
| test_channel_c_mandatory_gate（D30） | tests/test_perception_rules_gate.py（**真 semgrep 子进程实跑**） | ✅ |
| test_channel_c_runtime_timeout_absent | tests/test_perception_channels.py | ✅ |
| test_index_content_hash_skip / diff mode | tests/test_perception_indexer.py + test_perception_scan.py（pr 邻域） | ✅ |
| test_scan_fast_topk / pr_neighborhood / full_shards | tests/test_perception_scan.py + loop shards 断言 | ✅ |
| test_loop_l0_to_l1_shard_contract | tests/test_kernel_loop_m3.py（tasks 行 + DISPATCH 事件 + 渲染头三面对账） | ✅ |
| test_loop_scout_least_loaded | 同上（两蜂负载差 ≤1） | ✅ |
| test_loop_deposit_x02 | 同上（S1/0.2/±1/origin=prod 逐字段） | ✅ |
| test_loop_promote_scan_shadow_inert | 同上（扫描痕在位 + 零 PROMOTION 事件 + 编制不变） | ✅ |
| test_seeding_roles_flag | tests/test_exam_seeding_roles.py | ✅ |
| test_judge_reviewer / test_judge_verifier | tests/test_exam_judge.py（四象限 + 三值 + parsed=None） | ✅ |

### B 组：不变量
| 判据 | 测试 | 状态 |
|---|---|---|
| test_write_ports_audit_m3 | tests/test_m3_invariants.py（真包审计零违规零漂移 + perception 全包零账本表写 AST） | ✅ |
| test_purity_perception | 同上（open/random 钩子 import + 调用面；静态零 IO 导入） | ✅ |
| test_network_single_port | 同上（socket 钩子 + AST 零网络库；L0-B 全经注入 adapter） | ✅ |
| test_params_extra_forbid | tests/test_config.py::test_params_perception_extra_forbidden | ✅ |

### C 组：可复现与等价
| 判据 | 测试 | 状态 |
|---|---|---|
| test_scan_replay_bitwise | tests/test_perception_scan.py（双账本双工件库 content hash 相等 + 字节对照） | ✅ |
| test_bootstrap_import | tests/test_kernel_bootstrap.py（payload 逐字 + src_seq + route 池非空） | ✅ |
| test_bootstrap_idempotent | 同上（二次导入零重复行零重复事件） | ✅ |
| test_route_untouched | 全量回归：test_kernel_route + test_golden_regression + 全部黄金 digest 门禁绿（route/route_thompson/promote_scan 源文件零改动） | ✅ |

### D 组：端到端 smoke（FakeAdapter 全离线）
| 判据 | 测试 | 状态 |
|---|---|---|
| test_e2e_fast_lane | tests/test_kernel_loop_m3.py（A/B/C 三通道 → L1 → 存款 → 报告全链） | ✅ |
| test_e2e_idempotent_rerun | 同上（重跑零新候选/零新任务/零重复存款/零新事件） | ✅ |

### E 组：补播执行（在线部分待负责人）
- 离线判分链路全绿（`test_seed_entry_reviewer_verifier_e2e`：2 蜂 × 5 家族 = 10 格
  存款、QC 1.0 转 active、DepositPayload 与 auditor 同形）；
- **在线入职考一轮未执行，且负责人已裁决推迟**（2026-10-12：非 M3 门禁项，绑定为 **M4 生产联调前置件**——评审团/预裁决单需真实 reviewer/verifier 蜂时再跑；名册拍板 glm-5.3/glm-5.3，models.zhipu.yaml mid 档已改，5.1 弃用）。执行命令（届时）：
  ```bash
  QSWARM_KEY_ZHIPU="$(cat .zhipu_key)" python3 -m qswarm.exams.seeding \
    --bank seeds/bank.yaml --models configs/models.zhipu.yaml \
    --db out/m3-seeding-rv/seeds.db --out out/m3-seeding-rv --roles reviewer,verifier
  ```
  （纪律同 M2：新 DB、缓存全新、S9 重放对照；E 组门禁判据 = 链路正确，非必须及格。）

## 3. D25–D30 落位记录

| 编号 | 落位 |
|---|---|
| D25 探测式分级 | `indexer.probe_languages`：发行版名机械反推 + import 探测，不硬编码清单；本环境实测六门 tier-1（python/js/go/java/c/c++）全可用；无 grammar → parse_ok=False 通道 A 缺席计数 |
| D26 侦察蜂兼任 | 通道 B 经 DispatchContract 直接装配（role=scout、task_id=`l0scan:<shard>`、goal 逐字、output_schema_ref=l0_candidates）+ temp 池选蜂 + 任务头渲染；零存款（D 组断言账本零 deposit）；有产出分片以 producer=agent_id 落独立工件 + 报告 channel_b_by_agent 归属计数 |
| D27 候选内容寻址 | Candidate.candidate_id = blake2b(path, span, kind, clue) 16B hex；工件 `l0_candidates`；账本零直写 |
| D28 新建空账本导入 | bootstrap.init_prod_ledger：空 schema + 一次性幂等导入；种子库 mode=ro 只读；src_seq 溯源；verify_ready fail-closed |
| D29 verifier 预裁决单 | M3 纸面阶段 verifier 考试无沙箱执行（纸面卷 + CONFIRMED/REFUTED/INCONCLUSIVE 判分）；生产面 S2 预裁决单随 M4 簇消费 |
| D30 semgrep 必选件 | 缺省 enabled=true；门禁三条实跑判据（规则包映射 + tier-1 样例 rule_hit + scan 全链计数）以真 semgrep 1.180.0 子进程执行；运行期故障缺席计数（漏斗不停摆）语义测试钉住 |

## 4. 新旋钮回填清单（params.yaml `perception` 节）

| 键 | 回填值 | SPEC-7 §6 |
|---|---|---|
| perception.model.enabled | true | 通道 B 开关（D26） |
| perception.rules.enabled | true | 通道 C 必选件（D30） |
| perception.rules.timeout_s | 120 | semgrep 单次超时 |
| perception.l0.max_file_lines | 800 | 通道 B 单片行上限 |
| perception.l1.fast_top_k | 3 | fast 档 Top-K |
| perception.l1.files_per_shard | 20 | L1 分片大小 |
| perception.scan.rescan_sample_frac | 0.05 | 周期重扫抽样率（v1 只落开关） |

逐键钉值：tests/test_config.py::test_m3_perception_knobs_pinned；黄金兼容（缺节回落）：
test_params_perception_section_optional_with_spec_defaults。

## 5. 已知限制与 caveats（如实披露）

1. **语言覆盖实测**：tree-sitter 六门 tier-1 实测可用（python/js/go/java/c/cpp）；
   调用点节点类型按泛化集（call/call_expression/method_invocation/new_expression）
   匹配，tier-2 语言（其余 grammar）按同名泛化——未逐一实测 tier-2 解析质量；
   通道 A 对识别不到调用节点的语言只剩标识符档（缺席语义显式计数，不静默）。
2. **通道 A 词表全等匹配**：v1 clues 为全等匹配（不做子串猜测），漏报面由通道
   B/C 兜底；词表允许扩充不收缩（clues.py 模块头纪律）。
3. **D26 归属口径（提交后复核补全，2026-10-12）**：分片级空产出不落工件（内容寻址库不留空壳），归属以报告 channel_b_by_agent 计数恒记、有产出分片独立工件 producer 署名；通道 B 派单契约经 DispatchContract 直接装配（build_contract 角色表硬编码不适用于直扫产出）并随任务头渲染可审计；
4. **semgrep 可用性**：本环境装通（1.180.0，官方 PyPI；腾讯镜像源无此包——安装
   需 `--index-url https://pypi.org/simple`）。规则包 v1 为模式级 sink 命中，
   误报由 L1 消费（命中仅提升优先级，不是准入条件）。
5. **L1 上下文窗**：候选块注入 span ±20 行（L1_CONTEXT_LINES）——侦察面够定位，
   M4 L2 深挖面（repo map 切片）再扩展。
6. **run 级幂等边界**：同 run 同仓库重跑收敛（任务 id 派生 + 存在性跳过）；crash
   后部分完成 run 的续跑按「已 done 跳过、其余重派」收敛（queued/dispatched 残留
   行的精细恢复未做——v1 无此场景，发生时人工介入）。
7. **E 组在线补播考未执行**（待负责人放行，§2.E 命令就绪）。

## 6. 遗留与下一步（M4 输入面）

- L1 存活点已是候选表 triaged 行 + guess 列（M4 L2 会诊/聚类消费就绪）；
- route()/route_thompson() 生产接线（M4 L2 审查蜂按 (agent×类型) 口碑路由）——
  存款已就位（C 组 route 池非空断言）；
- S2 沙箱（依赖序 8）后 verifier 升级真验证；
- repo map 切片注入（§2.1 v0.3.4 补）复用 indexer 调用点索引地基。
