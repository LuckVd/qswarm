# SPEC-2 编排内核接口（M1 实现级规格）

> 来源：设计文档 v0.3.4 §2.1/§2.2/§2.4/§3.2/§3.4/§4.1–4.5/§5.2/§6.1/§6.2/§6.5/§10.2/§10.3/§10.4/§11/§12/§16.6/§16.7；SPEC-1 §2/§4/§5/§6；SPEC-4 §0；DECISIONS.md 全部既有决策（尤其：缺省关闭=规格逐字、写口纪律、promotion 事件承载考试流转）。
> 读者：AI 编码代理。函数签名 / 字段名 / 语义不得偏离本文件；发现矛盾以本文件为准并记 `DECISIONS.md`（滚动协议 2/3）。
> 硬纪律：**只复用不重写**——M0 已落地的 `kernel.ledger` / `kernel.cells` / `kernel.route` / `kernel.route_thompson` / `kernel.promote_scan` / `kernel.replay` 及 `contracts` 一律原样复用（SPEC-4 §0「仿真复用内核实现」的同一纪律向 M1 延伸）；本 SPEC 只定义 M1 新增面与必要的最小增量修订。
> 本文件只写规格；实现落位与 `params.yaml` 回填由 M1 实现子任务按本文件执行。
> 状态：**定稿 v1.0（2026-10-10，规格门歧义拍板）**——本轮新增决策 D19–D24（五字段定稿表见 §10.1，已逐条同步 `docs/spec/DECISIONS.md`），并对 §1.3/§1.4/§1.5/§2.3/§2.5/§7.1 做定稿微修（逐处标注 D 编号）。实现期发现的进一步未定细节按滚动协议记 `DECISIONS.md`，不回改本文件编号语义。门禁分支裁定见 SPEC-5 §9.3 执行记录。

---

## 0. 范围、模块落位与来源映射

### 0.1 五块机制 ↔ 设计章节 ↔ 本文件章节（验收对映表）

| 机制块 | 设计文档出处 | 本文件 |
|---|---|---|
| 聚类归并（粗/精两级、split/merge 事件、簇级结论传播） | §3.4、§2.1、§3.2、§5.3、§16.5 | §1 |
| 委托契约四件套与派单状态机（含 Finding 结论状态机） | §4.2、§4.3、§4.4、§10.2、§10.3、§10.4、§16.7-8 | §2 |
| re_route 与侦察蜂猜测格弱存款 | §6.5 附带、§2.1、§3.2 | §3 |
| 配额与预算熔断（三档） | §2.4、§2.2、§12、§16.7-1 | §4 |
| 并发纪律（单写者/事务/三种读/时间解耦） | §4.5、SPEC-1 §6 | §7 |

其余章：§5 账本事件与 payload 契约总表；§6 四条不变量 + 确定性；§8 新旋钮清单；§9 M1 门禁要点；§10 决策记录索引。

### 0.2 M1 新增模块落位

| 模块 | 状态 | 内容 |
|---|---|---|
| `kernel/cluster.py` | **新增** | 两级聚类判据、建簇/扩簇/拆分、簇级结论传播（§1） |
| `kernel/dispatch.py` | **新增** | 委托契约装配、任务派单状态机、蜂产出代落账、Finding 结论状态机（§2） |
| `kernel/reroute.py` | **新增** | re_route 触发、猜测格弱存款、猜测聚合（§3） |
| `kernel/budget.py` | **新增** | L2/S2 配额、预算熔断与降级、过期收尾（§4） |
| `kernel/replay.py` | **扩展** | 三投影重建 `rebuild_clusters` / `rebuild_findings` 与逐位 diff（§6.3） |
| `kernel/ledger.py` | **最小扩展** | `transaction()` 事务上下文；`rebuild()` 升级为三投影（§7.1/§6.3） |
| `contracts/models.py` | **最小扩展** | `DepositPayload` 增可选字段 `src_seq`（§5.2） |
| `contracts/config.py` + `configs/params.yaml` | **扩展** | `budget` 节 3 个新键（§8；由实现子任务回填） |

### 0.3 M0 复用清单（禁止重写的既有面）

`KernelLedger.append/read/latest_seq`、三时钟（`Wall/Fixed/Stepped`）、`agg_cells`/`CellView`（C*/LOO/地板）、`route`/`route_thompson`/`RouteDecision`、`promote_scan`（含 v2 通道）、`rebuild_cells`/`diff_cells`、`PAYLOAD_SCHEMA` 校验、`schema.sql` append-only 触发器。M1 全部新函数只**调用**这些面，不复制其逻辑。

### 0.4 仿真接通原则（对 M1 总目标「接通仿真真实聚类」）

SPEC-4 §0 的复用纪律延伸到 M1：仿真器接通真实聚类/预算时，直接调用 `kernel.cluster` / `kernel.budget` / `kernel.dispatch`，不写第二份实现；世界侧只负责生成 finding 流、相似度函数（确定性文本相似度注入 `sim_fn`，见 §1.2）与谱系装配（`VerdictContext`，见 §1.4）。M0 九项度量与既有 golden 轨迹**不得回归**（§9）。

---

## 1. 聚类归并（`kernel/cluster.py`；设计 §3.4）

### 1.1 机制语义（逐条可溯源）

- **两级归并**（§3.4）：
  - **粗聚类**（投票前）：仅「同 `path` + 行窗重叠 ≥50% + CWE 同 + `lang` 同」的保守合并，省投票；
  - **精聚类**（投票后）：粗条件**全部成立**之外再加入「证据链向量相似度 ≥ 0.9」，省沙箱；
- **保守优先**（§3.4 逐字）：宁可漏并（多花一次验证）不可错并（丢真 finding）；
- **簇级验证**（§3.4/§5.3）：S2 按簇跑（一簇一沙箱），成员共享结论；
- **结论传播**（§3.4/§6.5）：簇收到 S2/S3 verdict → 按 verdict 方向给每个成员的报告者记存款（§6.5 归属矩阵全行展开）；S3 标记任一成员 → 簇内传播；
- **错并可人工拆分**（§3.4）：`cluster_split` 事件，**拆分本身零存款**；
- **为什么必须有**（§3.4/§2.1）：防重复存款虚增 n_eff、防 S3 标记失效、防 L2/S2 预算翻倍；归并决策由内核执行（确定性规则），事件落账本可审计。

### 1.2 常量、数据结构与函数签名

```python
#: 粗/精两级共用的行窗重叠下限（§3.4 逐字 ">=50%"；模块常量不进 params，
#: extra=forbid 纪律同 route.SPEC_CAP 先例，DECISIONS 2026-10-08）。
CLUSTER_OVERLAP_MIN: float = 0.5

#: 精聚类证据链向量相似度阈值（§3.4 逐字 "相似度 ≥0.9"）。
CLUSTER_EMBED_SIM_MIN: float = 0.9

#: S1 共识存款源权重（§6.1 区间 [0.05, 0.1] 取下沿；世界侧 VerdictParams.s1_weight
#: 是仿真实验臂旋钮不进内核，DECISIONS 2026-10-08 先例）。S2/S3 源权重为规格常量
#: 0.5 / 1.0（§6.1；与 simulator.verdict.S2_SOURCE_WEIGHT/S3_SOURCE_WEIGHT 同源同值）。
S1_SOURCE_W: float = 0.05
S2_SOURCE_W: float = 0.5
S3_SOURCE_W: float = 1.0

#: 相似度函数注入点：接收两个 FindingFacts（含 sim_text），返回 [0,1] 相似度。
#: **缺省 None = 精聚类不产生任何合并**（保守漏并；§3.4 宁漏勿错 + §8 旋钮表）。
SimFn = Callable[["FindingFacts", "FindingFacts"], float]


@dataclass(frozen=True)
class FindingFacts:
    """聚类判据所需的 finding 事实面（调用方从 findings 表行 + evidence_nodes 装配）。"""

    finding_id: str
    path: str
    line_start: int
    line_end: int
    cwe: str | None                # None = 未判 CWE（§3.1 可空）
    lang: str
    family: str
    type: str | None
    sim_text: str = ""             # 标题 + 证据链序列化文本（kind:where:ref 串联），
                                   # 供注入的 embedder 消费；内核不解释其内容


def line_overlap(a: tuple[int, int], b: tuple[int, int]) -> float:
    """行窗重叠系数：交集行数 / min(|a|, |b|)（DECISIONS 2026-10-10，口径裁定）。

    选交集/min 而非 Jaccard 的理由：子窗完全含于大窗时系数=1.0——同一根因的
    窄证据窗不应因邻居窗更宽而被拒并；空窗或 start>end → 0.0。"""


def coarse_mergeable(fa: FindingFacts, fb: FindingFacts) -> bool:
    """粗聚类判据（§3.4 保守合并）：同 path ∧ 同 lang ∧ CWE 相等
    ∧ line_overlap ≥ CLUSTER_OVERLAP_MIN。
    CWE 语义（DECISIONS 2026-10-10）：双方非 None 须相等；双方 None 退化为
    (family, type) 相等（家族级 finding 的保守替身）；恰一侧 None → False（保守）。"""


def fine_mergeable(fa: FindingFacts, fb: FindingFacts, *, sim_fn: SimFn | None) -> bool:
    """精聚类判据 = coarse_mergeable ∧ sim_fn(fa, fb) ≥ CLUSTER_EMBED_SIM_MIN。
    sim_fn 为 None 时恒 False（缺省不并，§1.2 注入点纪律）。"""


@dataclass(frozen=True)
class ClusterProposal:
    """一次归并提案（apply_merges 的输入；纯数据，可审计可重放）。"""

    level: str                      # "coarse" | "fine"
    member_ids: tuple[str, ...]     # 锚点（最小 finding_id）在前，其余字典序
    key_path: str
    key_cwe: str | None
    key_lang: str
    line_window: tuple[int, int]    # 成员行窗的并包络 (min_start, max_end)
    sim_min: float | None           # 精聚类：成员对最小相似度；粗聚类 None


def cluster_findings(
    findings: Sequence[FindingFacts],
    *,
    level: str,                                     # "coarse" | "fine"
    sim_fn: SimFn | None = None,
    assigned: Mapping[str, str] | None = None,      # finding_id → 既有 cluster_id
) -> list[ClusterProposal]:
    """确定性两级归并提案（纯函数，零 IO 零 rng）。

    - 候选序：按 (path, line_start, line_end, finding_id) 字典序扫描（§6.5 确定性纪律）；
    - 合并判据：**all-pairwise**——新成员须与簇内**全部**成员两两可合并（DECISIONS
      2026-10-10：不做单链传递闭包，A~B~C 而 A≁C 时不并 C，防链式错并，§3.4 保守优先）；
    - `assigned` 中已归属的 finding 不参与新簇提案（扩簇场景由调用方把既有簇成员
      放进 assigned）；
    - 同一 finding 至多出现在一个提案中（先到先得，按候选序）。"""
```

### 1.3 建簇 / 扩簇 / 拆分（事件与写口）

```python
def apply_merges(
    ledger: KernelLedger,
    proposals: Sequence[ClusterProposal],
    *,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """建簇/扩簇 + cluster_merge 事件，**同一事务**（SPEC-1 §2 事务边界：
    clusters 合并与 cluster_merge 事件同事务）。

    - clusters / cluster_members 表写口在本模块（§6.1）；
    - id 生成：cluster_id = f"C-{date}-{seq:06d}"，date=注入时钟当日 YYYYMMDD，
      seq = 同日期前缀在账本库内已有最大序号 + 1（D20：自增域=账本库而非 run——
      引擎支持复用 db_path 追加写，多 run 同库同日期时按 run 内计数会撞 clusters.id
      主键；单 run 独库时与「run 内自增」逐位同值，M0 轨迹零影响。内核单写者串行
      保证唯一，SPEC-1 §6-1）；
    - 幂等键 = (level, frozenset(members))：已存在同 level 簇恰好包含全部 members
      → 零新事件直接跳过（§6.4）；
    - 返回按落库序的全部事件；无变化 → []。"""


def split_cluster(
    ledger: KernelLedger,
    cluster_id: str,
    groups: Sequence[Sequence[str]],      # ≥2 组，并集须 = 原成员全集
    *,
    actor: str,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """人工拆分（§3.4「错并可人工拆分」）：cluster_split 事件 + 成员表改写，同事务。

    - **拆分零存款**（§3.4 逐字）：已按簇传播的存款一律不回滚（§6.2 无回滚纪律，
      账本只追加）；拆分后各子簇 verdict 投影清空（verdict_* 置空列更新，
      属 clusters.verdict_* 内核写口）；
    - groups < 2 或并集 ≠ 原成员全集 → ValueError；
    - 幂等：簇不存在或已拆分（成员数 < 拆分前）→ ValueError（拆分是人工动作，
      不做静默跳过）。"""
```

### 1.4 簇级结论传播（§3.4 × §6.5 归属矩阵）

```python
@dataclass(frozen=True)
class MemberFacts:
    """簇成员的报告者事实（§6.5 系数按角色取）。"""

    finding_id: str
    reporter: str
    reporter_role: str             # "auditor"（×1.0）| "scout"（×SCOUT_X，fast 档 §16.7-1）


@dataclass(frozen=True)
class VerdictContext:
    """归属谱系（DECISIONS 2026-10-10：候选→侦察→finding 谱系由调用方显式装配，
    内核不改 SPEC-1 DDL、不自建谱系表）。"""

    members: tuple[MemberFacts, ...]
    scouts: tuple[str, ...] = ()               # 额外侦察行（非 reporter 的簇标记者）
    votes: tuple[tuple[str, int], ...] = ()    # (reviewer, direction) 评审回校（§6.5 评审行）
    verifier_id: str | None = None             # §6.5 验证行归属（S3 到达时需要）


def propagate_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    tier: SignalTier,                              # S1 | S2 | S3
    direction: int,                                # ±1（0 = 无方向，不落任何存款）
    source_seq: int,                               # 源事件 seq（幂等键，见下）
    source_kind: LedgerKind,                       # VERIFY_RESULT | HUMAN_VERDICT | VOTE | FINDING
                                                   #   （D23：词表增 FINDING——close_panel 的 S1 收口
                                                   #   源事件即 panel 收口 finding 事件；实现须校验
                                                   #   ledger[source_seq] 存在且 kind == source_kind）
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """簇 verdict → clusters.verdict_* 投影更新 + §6.5 归属展开 deposit 事件（同事务）。

    - 存款展开序（确定性，DECISIONS 2026-10-10；对齐 M0 simulator.deposits 行序）：
      成员报告者行（按 finding_id 字典序；reporter_role=scout 走 ×SCOUT_X，§16.7-1）
      → 侦察行（scouts 字典序，×SCOUT_X）→ 评审回校行（votes 字典序，与 verdict 同向
      + / 反向 −，×REVIEWER_X）→ 验证行（仅 tier=S3 且簇存在先验 S2 verdict 时：
      S3 同向 +VERIFIER_CONFIRM / 反向 −VERIFIER_CONFLICT；ctx.verifier_id 为 None
      且先验存在 → ValueError）；
    - 每笔派生存款 payload 携带 `src_seq = source_seq`（§5.2 新字段；崩溃修复与
      重放幂等的唯一判据，§6.3）；
    - verify_error / direction == 0 → 零存款零投影更新（§6.2/§6.3：不产生任何进化压力）；
    - **冲突语义**（§6.2 逐字）：S3 与 S2/S1 结论冲突时不回滚旧存款，按 S3 方向补记
      更强反向存款（本函数对同一簇可被 S1/S2/S3 先后调用各一次，净额自然收敛）；
    - 源事件一致性（D23）：ledger[source_seq] 不存在或 kind ≠ source_kind → ValueError
      （审计校验；source_kind 仅作派生审计用途，payload 不承载、不新增 LedgerKind）；
    - 幂等键 = (cluster_id, tier, source_seq)：同键重复调用 → 零新事件（§6.4）。"""


def propagate_pending(
    ledger: KernelLedger,
    *,
    lineage: Callable[[str], VerdictContext | None],   # cluster_id → 谱系（harness 提供）
    from_seq: int = 0,
    upto_seq: int | None = None,
    params: ParamsConfig | None = None,
) -> list[LedgerEvent]:
    """批处理修复口（§6.3 可重放不变量的运行时配套）：扫描窗口内全部
    verify_result / human_verdict / vote（S1 收口）事件，凡「应派生而未派生」的
    存款（按 (cluster, tier, src_seq) 幂等键比对）逐个补齐。崩溃窗口=源事件已落、
    派生存款未落——本函数把窗口收敛到零。lineage 返回 None（谱系缺失，如 M0 世界
    侧未接簇）→ 跳过该簇并保留在返回值的 skipped 列表，不报错。"""


def apply_human_verdict(
    ledger: KernelLedger,
    cluster_id: str,
    *,
    direction: int,                    # +1 真报 | -1 误报 | 0 忽略
    actor: str,
    source: str = "panel",
    ctx: VerdictContext,
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """§12 S3 入口（dashboard 复审面板 → 内核）：human_verdict 事件 + 簇内全员
    finding 状态流转 + S3 存款按簇传播（±1.0），同事务。

    - direction=+1 → 全员 transition_finding(CONFIRMED)；−1 → REJECTED；
      **0 = 忽略：落事件、零存款、零状态变更**（§12 逐字「忽略 ≠ 误报，零存款，
      歧义信号不入账」——事件落账仅为审计轨迹）；
    - 状态流转复用 §2.3 迁移表（终态 finding 再次收到 verdict → ValueError，
      防重复裁决；D21：终态守卫先于 direction 分支——direction=0 对终态簇同样
      ValueError，单事务回滚零落库，不存在「忽略态半落事件」）；
    - S3 存款经 propagate_verdict(tier=S3)（验证行随之 §6.5）。"""
```

### 1.5 S1 评审聚合（§10.3）

```python
@dataclass(frozen=True)
class VoteFacts:
    """一笔评审票的事实面（votes 表行同构）。"""

    reviewer: str
    direction: int
    dim_completeness: float
    dim_reachability: float
    dim_exploitability: float
    dim_fp_suspicion: float


@dataclass(frozen=True)
class VoteConsensus:
    finding_id: str
    direction: int          # −1|0|+1：票向符号和的符号；0 = 平票
    confidence: float       # 多数方向票的四维均值之平均（区间 [0,1]；无多数票 → 0.5）
    disagreement: bool      # 存在反向票 → True（DECISIONS 2026-10-10-D22，见 §1.6）
    n_votes: int


def aggregate_votes(finding_id: str, votes: Sequence[VoteFacts]) -> VoteConsensus:
    """§10.3 共识规则（纯函数，确定性）：方向 = 多数；平票（符号和为 0）→ direction=0。
    分歧是资产（§10.3）：存在反向票即记 disagreement 并显式上报，不强制仲裁。"""


def close_panel(
    ledger: KernelLedger,
    finding_id: str,
    *,
    ctx: VerdictContext,                 # votes 取自 ctx.votes
    expected_votes: int | None = None,   # 缺省 = params.panel.SIZE
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """panel 收口（投票后、精聚类/S2 前的必经步，§2.1 流水线序）：

    - findings.confidence = consensus.confidence、disagreement_json 写入（内核写口）；
    - finding 事件 open→voted（§2.3 迁移表）；
    - consensus.direction ≠ 0 → propagate_verdict(tier=S1, source_seq=本收口 finding
      事件的 seq)，S1 存款 = 源权重 S1_SOURCE_W × 角色系数（§6.5 全行展开）；
      direction == 0 → 只落状态与分歧，零存款；
    - 幂等：finding 已 voted → 零新事件；票数不足 expected_votes → ValueError。"""
```

### 1.6 本节决策记录（滚动协议 1；已同步 DECISIONS.md，见 §10）

| # | 决策 | 理由 |
|---|---|---|
| D1 | 重叠口径 = 交集行数 / min(两窗行数) | 子窗含于大窗应判可并；Jaccard 会拒并窄证据窗 |
| D2 | all-pairwise 保守合并，不做单链传递闭包 | §3.4「宁可漏并不可错并」对链式错并的直接落地 |
| D3 | CWE 双 None → 退 (family,type) 相等；单 None → 不可并 | 家族级 finding 的保守替身；半边缺失并簇风险不可控 |
| D4 | sim_fn 注入缺省 None = 精聚类不并 | M1 内核零模型依赖；缺省行为=保守漏并（§3.4 优先序） |
| D5 | cluster_merge.members = 本次并入集（建簇=全部初始成员；扩簇=新增成员） | 全量成员以 cluster_members 表为真源，事件记增量可审计 |
| D6 | 派生存款增 src_seq 字段做幂等键 | 可重放不变量要求崩溃窗口可修复（§6.3） |
| D7 | 结论传播展开序固定（成员→侦察→评审→验证，各自字典序） | 确定性纪律 §6.5；跨运行逐位可比 |
| D20 | finding_id/cluster_id 自增域 = 同日期前缀账本库内 max+1（§1.3/§2.5 措辞同步修订） | 引擎支持复用 db_path 追加写（多 run 同库同日期）；id 是 TEXT PRIMARY KEY，run 内计数会撞主键；单 run 独库时逐位同值 |
| D21 | apply_human_verdict 终态守卫先于 direction 分支（0=忽略对终态簇同样 ValueError、事务回滚零落库） | 守卫无条件优先才防重复裁决；否则终态簇可被忽略态反复刷审计事件 |
| D22 | aggregate_votes 分歧语义定稿补记：反向票 → disagreement=True 显式上报不强制仲裁；修正 §1.5 错引「见 §10-8」 | §10.3「分歧是资产」逐字；该决策此前声称已同步 DECISIONS.md 但无对应行，本次补齐 |
| D23 | propagate_verdict source_kind 词表增 FINDING + 源事件 kind 一致性校验（ledger[source_seq].kind ≠ source_kind → ValueError） | close_panel 的 S1 源事件客观上是 finding 事件，原词表与之错位；校验让幂等键的 src_seq 可信 |

---

## 2. 委托契约与派单状态机（`kernel/dispatch.py`；设计 §4.2/§4.3/§10.2）

### 2.1 机制语义

- **蜂后 = 确定性内核 + LLM 顾问**（§4.2）：一切写态动作在内核执行；LLM 产出一律是建议工件，落地必须过内核 schema 校验，**LLM 对全局状态零写权**；
- **委托契约四件套**（§4.3）：每次派单必含 ①目标；②输出格式（schema 校验，不合格直接退回）；③工具与信息源边界；④任务边界。工蜂产出**一律落盘为工件、只回传引用**；
- **三条硬纪律**（§4.4）：写态收归内核串行；评审蜂不得为报告者本人（纪律 2，库级表达 = votes UNIQUE(finding_id, reviewer) + 内核提交守卫）；单蜂不产生确定性结论（L3 必经 S2）。

### 2.2 委托契约装配

```python
#: ② 输出格式：role → SPEC-1 §4.2 输出契约名（queen_advisor 不派产 finding 任务，
#: §16.7-9 只产结构化仲裁摘要建议稿，落账仍走内核）。
ROLE_OUTPUT_SCHEMA: Mapping[str, str] = {
    "scout": "ScoutReport",
    "auditor": "AuditorFinding",
    "reviewer": "ReviewerVote",
    "verifier": "VerifierReport",
}

#: ③ 工具与信息源边界：§4.1 权限边界列的机器可读形态（只读仓 / repo map 只读 /
#: finding 与工件只读 / 沙箱可写+仓只读）。verifier 的 sandbox:rw 仅指隔离沙箱，
#: 永不触生产（§4.1）。
ROLE_TOOL_BOUNDS: Mapping[str, tuple[str, ...]] = {
    "scout": ("repo:ro",),
    "auditor": ("repo:ro", "repomap:ro"),
    "reviewer": ("finding:ro", "artifact:ro"),
    "verifier": ("repo:ro", "sandbox:rw"),
}

#: ④ 任务边界缺省模板（一句话；SPEC-3 提示词模板细化时引用同一文案源）。
ROLE_TASK_BOUNDS: Mapping[str, str] = {
    "scout": "只分诊给定候选点并给出类型猜测；不做深挖、不产 finding。",
    "auditor": "只分析派单节点指名的可疑点×类型并产带证据链 finding；不扩面、不改结论分级。",
    "reviewer": "只按四维独立打票；不复用报告者结论、不产新 finding。",
    "verifier": "只对指名簇做沙箱复现并产三分类结构化结果；不投票、不产 finding。",
}

#: 深挖跳数（§2.2 档位表逐字：fast=0 / pr=1 / full=2；§2.1 repo map 按
#: 「候选点 ± N 跳邻域子图」注入 L2 上下文）。
HOP_BUDGET_BY_MODE: Mapping[str, int] = {"fast": 0, "pr": 1, "full": 2}


def hop_budget_for(mode: RunMode | str) -> int:
    """档位 → 深挖跳数（§2.2 逐字表）；未知档位 → ValueError。"""


def build_contract(
    task,
    decision: RouteDecision,
    *,
    mode: RunMode | str,
    deadline_s: int,
    node: str | None = None,            # 缺省 = decision.node
    context_refs: Sequence[str] = (),   # artifact:// 或 repo 路径+行窗
    goal: str | None = None,            # 缺省 = f"对 {node} 以 {role} 职责完成本次分片"
    task_bounds: str | None = None,     # 缺省 = ROLE_TASK_BOUNDS[role]
) -> DispatchContract:
    """四件套装配（§4.3；SPEC-1 §4.1 DispatchContract 直出）。纯函数零 IO：
    ①goal ②output_schema_ref=ROLE_OUTPUT_SCHEMA[role] ③tool_bounds=ROLE_TOOL_BOUNDS[role]
    ④task_bounds；hop_budget=hop_budget_for(mode)；taxonomy_node=node。"""
```

### 2.3 任务派单状态机（tasks.state：queued → dispatched → done|failed）

```
queued ──dispatch()──→ dispatched ──submit() 全量代落账成功──→ done
                          │           │
                          │           └─超时/schema 不合格─→ re_dispatch(attempts<2) ──→ 重派
                          │                         └─attempts≥2─→ failed
任意非终态 ──expire_run()（预算熔断/超时）──→ failed（任务侧；finding 侧=expired）
```

```python
#: 单任务派单次数上限 = 初派 1 + 重试 1（§16.7-8 逐字「timeout → 重试 1 次 → 改派
#: 他人」；模块常量不进 params，extra=forbid 纪律）。
DISPATCH_MAX_ATTEMPTS: int = 2


def dispatch(
    ledger: KernelLedger,
    task_id: str,
    contract: DispatchContract,
    decision: RouteDecision,
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """queued → dispatched：tasks 行（assignee / state='dispatched' / dispatched_at /
    attempts=1）+ DISPATCH 事件，同事务。

    - tasks.state ≠ 'queued' → ValueError（重复派单被状态守卫拒绝，§6.4 幂等）；
    - decision.assignee is None（empty_pool）→ 仍落 DISPATCH 事件（assignee=null，
      审计痕迹），tasks **留 queued**、attempts 不消耗；改派由调用方重入；"""


def re_dispatch(
    ledger: KernelLedger,
    task_id: str,
    *,
    to_agent: str,
    reason: Literal["timeout", "schema_fail"],     # = ReDispatchPayload.reason 词表
    run_id: str | None = None,
) -> LedgerEvent:
    """重派（§16.7-8）：tasks.attempts < DISPATCH_MAX_ATTEMPTS → tasks 重派
    （assignee=to_agent、attempts+1、state='dispatched'）+ RE_DISPATCH 事件，同事务；
    attempts 超限 → tasks.state='failed' 并抛 ValueError（重试通道关闭，零存款）。"""


def expire_run(
    ledger: KernelLedger,
    run_id: str,
    *,
    reason: str = "budget_breaker",
    params: ParamsConfig | None = None,
) -> list[LedgerEvent]:
    """run 收尾过期（§2.4「未跑项标记 expired（零存款）」× §10.2 任意状态→expired）：

    - 该 run 下全部非终态 finding（state ∈ {open, voted, inconclusive, verified}；
      D19：「非终态」= 迁移表出边非空，与 §2.4 迁移表 VERIFIED→EXPIRED 对齐）逐个
      transition_finding(EXPIRED)——**零存款**（本函数路径不产任何 deposit）；
    - 对应 tasks 行 state ∈ {queued, dispatched} → 'failed'；
    - 幂等：全部已终态 → []。"""
```

### 2.4 Finding 结论状态机（§10.2）

```python
#: 迁移表（§10.2 图逐字 + 一条 S3 直达扩展，DECISIONS 2026-10-10-D8：
#: open→confirmed/rejected 由 S3 直答触发——§12 入口语义「标记一个 finding 真假
#: 10 秒内可完成」允许人对未投票 finding 直答；除该扩展外不新增任何边）。
FINDING_TRANSITIONS: Mapping[FindingState, frozenset[FindingState]] = {
    OPEN:         frozenset({VOTED, CONFIRMED, REJECTED, EXPIRED}),
    VOTED:        frozenset({VERIFIED, INCONCLUSIVE, CONFIRMED, REJECTED, EXPIRED}),
    INCONCLUSIVE: frozenset({CONFIRMED, REJECTED, EXPIRED}),
    VERIFIED:     frozenset({CONFIRMED, REJECTED, EXPIRED}),   # S3 推翻/确认（§6.2）
    CONFIRMED:    frozenset(),                                  # 终态
    REJECTED:    frozenset(),                                  # 终态
    EXPIRED:     frozenset(),                                  # 终态
}


def transition_finding(
    ledger: KernelLedger,
    finding_id: str,
    to_state: FindingState,
    *,
    run_id: str | None = None,
) -> LedgerEvent:
    """结论状态机流转：迁移表守卫（非法迁移 / 终态再流转 → ValueError）；
    findings.state 列更新 + kind=finding 事件（payload.state=to_state）同事务。

    - 状态事件载体裁定（DECISIONS 2026-10-10-D3）：流转落 kind=finding 事件，
      payload.state 承载新状态——不新增 LedgerKind（枚举词表被 tests/test_enums.py
      ast 钉死）；重放规则 = 取每 finding 最后一条 finding 事件的 state（§6.3）；
    - **expired 零存款**：本函数从不产 deposit（§10.2 簧语义：不产生「没验证=负面」
      偏置）；零存款由测试断言（§9）。"""
```

**S2 结果 → 状态/结论映射**（submit(verifier) 内部执行，§6.3/§10.4/§10.2）：

| VerifyOutcome | 簇内全员 finding 状态 | clusters.verdict_* 投影 | 存款 |
|---|---|---|---|
| `verified_real` | `verified`（grade→L3，§10.1） | tier=S2, dir=+1 | §6.5 全行展开（S2 源权重 0.5） |
| `verified_fp` | `rejected` | tier=S2, dir=−1 | 同上；**缺 blocking_evidence 先强制改判 verify_error**（§10.4） |
| `verify_error` | `inconclusive`（§6.2「复现失败 ≠ 非漏洞，只降级」） | 无投影更新 | **零存款**；error_kind 必填（§6.3） |

### 2.5 蜂产出代落账 submit（§4.5 纪律 1：内核是唯一写手）

```python
class SchemaRejected(ValueError):
    """② 输出格式不合格（§4.3「schema 校验，不合格直接退回」）：零落库，
    调用方据 reason 走 re_dispatch(schema_fail) 或 failed。"""


def submit(
    ledger: KernelLedger,
    task_id: str,
    output: ScoutReport | AuditorFinding | ReviewerVote | VerifierReport,
    *,
    contract: DispatchContract,
    ctx: VerdictContext | None = None,       # auditor/verifier/reviewer 收口所需谱系
    params: ParamsConfig | None = None,
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """代落账：pydantic 校验 → 表行 + 账本事件 + 状态机流转，**单事务**（§7.1）。
    按 output 类型分发：

    - **ScoutReport**：candidates.triaged=1、guess_family/guess_type 回填（candidates
      表写口在本模块）；injection_flag=True 的候选点按 §11.6-3 保持候选态并标高优先
      （不做任何丢弃）；
    - **AuditorFinding**：findings + evidence_nodes 行落库（finding_id = 内核签发
      f"F-{date}-{seq:06d}"，seq = 同前缀库内已有最大序号 + 1，D20 同 §1.3）+
      finding 事件(state=open)；证据链校验
      （≥3 节点且含 source/sink、sanitizer 必出现，SPEC-1 §4.2 注记）不合格 →
      SchemaRejected；产出 (family,type) 与 contract.taxonomy_node 错配 → 自动
      re_route（§3.1，含猜测格弱存款）；
    - **ReviewerVote**：纪律 2 守卫（reviewer == 该 finding.reporter → ValueError）；
      votes 行 + vote 事件；票数达 params.panel.SIZE → 自动 close_panel（§1.5）；
    - **VerifierReport**：§10.4 改判守卫（verified_fp 缺 blocking → 强制改判
      verify_error，error_kind=poc_fail）→ verify_results 行 + verify_result 事件 +
      S2 状态映射（§2.4 表）+ propagate_verdict(S2)（§1.4，同事务）。

    task 非 dispatched 态 → ValueError。返回按落库序的全部事件。"""
```

### 2.6 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D8 | 迁移表增 open→confirmed/rejected（S3 直达）边 | §12 入口语义；除此之外迁移表逐字照 §10.2 图 |
| D9 | 状态流转以 kind=finding 事件承载（不新增 LedgerKind） | §3.2 信封+按 kind 分载荷；枚举词表被测试钉死 |
| D10 | 空池派单落 assignee=null 的 dispatch 事件且 tasks 留 queued | 派单失败需可审计；attempts 只计真派发 |
| D11 | verified_fp → rejected（而非 verified 态变体） | §6.2 verified_fp 是确定性负结论；与 S3 否定同状态出口 |
| D19 | expire_run 非终态覆盖集 = {open, voted, inconclusive, verified}（修正 §2.3 原枚举漏 verified） | 迁移表 VERIFIED→EXPIRED 出边非空；漏收尾会留下永悬 verified 行，破坏 run 收尾无非终态 |

---

## 3. re_route 与侦察蜂猜测格弱存款（`kernel/reroute.py`；设计 §6.5 附带）

### 3.1 机制语义与触发规则

- **侦察蜂的初步类型猜测**作为 L2 路由的 taxonomy 初值（§2.1 L1 初筛）；
- **路由初值随 guesses 口碑加权**（§6.5 附带）：猜测格 = `(scout, role=scout, node=猜测路径)`，其口碑（C*）参与猜测聚合；
- **re_route 事件**（审查蜂声明类型错配）→ 对猜测格记 **−0.05 弱存款**（§6.5 附带逐字）；
- **触发裁定**（DECISIONS 2026-10-10-D12）：内核**确定性比对**——产出 finding 的
  `(family, type)` 与派单契约 `taxonomy_node` 不一致即触发，无需蜂显式声明字段：
  - 错配 = 家族段不同，**或**（家族相同 ∧ 双方都含类型段 ∧ 类型不同）；
  - 家族级契约 + 更细类型产出 = **细化不是错配**，不触发；
  - 每个 finding 至多触发一次（首错配生效；后续产出与已改路由节点一致即不再触发）。

### 3.2 函数签名与弱存款契约

```python
#: 猜测错配弱存款幅值（§6.5 附带逐字 -0.05；模块常量不进 params）。
RE_ROUTE_WEAK_W: float = 0.05


def is_mismatch(from_node: str, to_node: str) -> bool:
    """§3.1 触发裁定的纯函数实现（家族段不同，或同族双类型段且类型不同）。"""


def re_route_finding(
    ledger: KernelLedger,
    finding_id: str,
    *,
    from_node: str,                        # 派单契约 taxonomy_node
    to_node: str,                          # 产出 finding 的 (family[/type])
    scout_ids: Sequence[str] = (),         # 猜测来源侦察蜂（弱存款归属；空 = L0 直出无猜测者）
    reason: str = "type_mismatch",
    run_id: str | None = None,
) -> list[LedgerEvent]:
    """re_route 事件 + 猜测格弱存款，同事务：

    - 事件：kind=re_route，payload {finding_id, from_node, to_node, reason}
      （ReRoutePayload 逐字，SPEC-1 §4.3）；
    - 弱存款：每个 scout_id 一笔 DepositPayload ——
      `agent=scout_id, role=scout, taxonomy_path=from_node 路径段（猜测即被证伪的
      路由初值）, signal_tier=S1, direction=-1, weight=0.05, origin=prod,
      src_seq=<re_route 事件 seq>`；
      记 S1 档的理由（DECISIONS 2026-10-10-D13）：弱存款是生产面最低档信号，且 S1
      不计晋升硬门槛 n_eff_s2s3（§6.1/§7.3）——「弱」字的结构化表达；
    - 幂等：同 finding 已有 re_route 事件 → 零新事件（§6.4）。"""


def guess_taxonomy(
    reports: Sequence[ScoutReport],
    cells: CellView,
) -> tuple[str, ...] | None:
    """L1 猜测聚合 → L2 路由 taxonomy 初值（§2.1/§6.5「路由初值随 guesses 口碑加权」）。

    - 候选 = reports 中 (guess_family, guess_type) 非空的 distinct 猜测路径集合；
    - 得分序（字典序降序取首）：票数 → 猜测格 C* 之和（Σ cells.c_star(scout,
      "scout", 猜测node)，无格子按 ROOT_PRIOR 计）→ 猜测路径字典序升序；
    - 无任何猜测 → None（调用方退家族级路由）。纯函数零 rng。"""
```

### 3.3 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D12 | 错配由内核确定性比对触发；细化（家族级→类型级）不算错配 | §6.5 附带「审查蜂声明类型错配」的机制化；细化是正常流程 |
| D13 | 弱存款记 signal_tier=S1 / weight=0.05 / direction=−1 / 猜测路径 | 「弱」= 低档 + 不进 n_eff_s2s3 硬门槛；幅值逐字 §6.5 |
| D14 | 谱系（candidate→scout→finding）由调用方显式接参（scout_ids / VerdictContext），不改 SPEC-1 DDL | findings/candidates 表无谱系列；M1 不为内核便利扩散 DDL 变更，harness 可从 dispatch 事件流装配 |

---

## 4. 配额与预算熔断（`kernel/budget.py`；设计 §2.4/§2.2）

### 4.1 机制语义（§2.4 逐条）

- **L2 配额（full 档）**：`quota_L2 = min(45, budget_remaining / cost_per_L2)`；
- **S2 配额**：`quota_S2 = quota_L2 存活数 × budget_remaining / cost_per_S2`，按（severity 初判 × 类型稀缺度 × 置信边际）降序取；
- **预算熔断器**：单 run 预算耗尽 → 档位自动降级（full 行为退化为 pr 语义），未跑项标记 `expired`（零存款，不产生「没验证=负面」的偏置）；
- **Batch 开关**：full 档默认走 Batch API（50% 折扣）；fast/pr 延迟敏感禁用——供给层职责（models.yaml `runtime.batch_mode_for_full`，SPEC-1 §5.1），内核只在 `BudgetPolicy.batch_eligible` 暴露档位判定；
- **三档预算**：run 建立时按入口档取 `budget_for_mode`（§2.2 成本锚点 → §8 新键）。

### 4.2 数据结构与函数签名

```python
SEVERITY_RANK: Mapping[str, int] = {"critical": 4, "high": 3, "medium": 2, "low": 1}


@dataclass(frozen=True)
class BudgetPolicy:
    """一次 run 的预算约束载体（harness 装配；注入None = 无预算约束路径，
    M0 仿真轨迹逐字兼容——见 §8 旋钮表「缺省时行为」列）。"""

    mode: str                          # 入口档 "fast"|"pr"|"full"
    budget_cents: int                  # run 总预算（= runs.budget_cents）
    cost_per_l2_cents: int             # L2 单点成本估计（§8 新键）
    cost_per_s2_cents: int             # S2 单次成本估计（§8 新键）
    l2_cap: int                        # = params.budget.L2_QUOTA_CAP（§2.4 的 45）

    @property
    def batch_eligible(self) -> bool:
        """§2.4 Batch 开关的档位判定（full 独占；实际 Batch 调用归供给层）。"""


@dataclass(frozen=True)
class BudgetState:
    mode: str
    spent_cents: int
    remaining_cents: int
    quota_l2: int
    quota_s2: int
    degraded: bool                     # True = 熔断已触发
    tier: str                          # 降级后的生效语义档（"full"→"pr"→"fast"）


def budget_for_mode(mode: RunMode | str, params: ParamsConfig) -> int:
    """入口档 → run 预算（美分）：fast→budget.FAST_CENTS / pr→PR_CENTS / full→FULL_CENTS。"""


def quota_l2(remaining_cents: int, cost_per_l2_cents: int, *, cap: int) -> int:
    """§2.4 逐字：min(cap, ⌊remaining / cost⌋)；cost ≤ 0 或 remaining ≤ 0 → 0
    （未配置成本模型不派单，保守）。"""


def quota_s2(l2_survivors: int, remaining_cents: int, cost_per_s2_cents: int) -> int:
    """§2.4 逐字：⌊存活数 × remaining / cost⌋；cost ≤ 0 → 0。"""


def s2_rank_key(
    severity: str,
    type_node: str | None,
    confidence: float,
    *,
    run_cluster_counts: Mapping[str, int],   # 本 run 已出现的类型节点 → 簇数
) -> tuple[int, float, float]:
    """§2.4 S2 降序取的排序键（DECISIONS 2026-10-10-D15）：
    (SEVERITY_RANK[severity], scarcity, confidence) 三元组字典序降序。
    scarcity = 1 / (1 + run_cluster_counts.get(type_node, 0))——本 run 内越稀缺的
    类型越优先；不取乘积（零置信会把乘积归零吞掉 severity 主序）。
    type_node 为 None（家族级）按空串计（scarcity 最高，与「类型稀缺度」的稀疏优先一致）。"""


def budget_state(spent_cents: int, policy: BudgetPolicy, *, params: ParamsConfig | None = None) -> BudgetState:
    """熔断判定（纯函数）：spent ≥ budget → degraded=True（含等号，保守闭包，
    DECISIONS 2026-10-10-D16），tier 沿 full→pr→fast 阶梯落一档；quota 按剩余预算
    重算。未触发 → tier=policy.mode。"""


def on_cost(
    ledger: KernelLedger,
    run_id: str,
    cents: int,
    *,
    policy: BudgetPolicy,
    note: str = "",
) -> BudgetState:
    """成本入账（runs.spent_cents += cents，runs 表写口在本模块，§6.1）并返回
    trip 后 BudgetState。**首次** trip：SCALING 事件（pool=f"mode:{policy.mode}",
    delta=-1, reason="budget_breaker"）+ runs.status='degraded'，与 spent 更新同事务
    （DECISIONS 2026-10-10-D17：不新增 LedgerKind，降级以既有 scaling 事件承载可审计
    轨迹，§11-3「一切伸缩可追溯」）；重复 trip 零新事件（幂等）。"""
```

### 4.3 熔断触达后的行为契约（降级语义的精确化）

| 侧 | 触发 spent ≥ budget 后的行为 | 出处 |
|---|---|---|
| 新派单 | quota 公式在 remaining ≤ 0 下恒 0 → 不再派任何付费任务（L2/S2） | §2.4 公式直译 |
| 在途任务 | 不撤回，走 §2.3 状态机自然完成或失败 | 内核无取消语义（保守） |
| 未跑项 | `expire_run(run_id)`：未终态 finding → expired（零存款）、未完任务 → failed | §2.4/§10.2 |
| 语义档 | full→pr（pr→fast，fast 到底）：新判定按低档参数（hop_budget 取低档值） | §2.4「档位自动降级」 |
| 可观测 | SCALING 事件 + runs.status='degraded'（RunStatus.DEGRADED） | §11-3 / SPEC-1 §1 |

### 4.4 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D15 | S2 排序键 = (severity, scarcity, confidence) 字典序降序；scarcity = 1/(1+本 run 类型簇数) | §2.4 乘积表述在零值处失真；字典序保留 severity 主序且确定 |
| D16 | 熔断判据 spent ≥ budget（含等号） | 保守闭包；> 会放行恰好压线的第 N+1 次付费调用 |
| D17 | 降级落 SCALING 事件 + runs.status=degraded，不新增 LedgerKind | §11-3 可追溯；枚举词表冻结（test_enums ast 钉 SPEC-1） |
| D18 | BudgetPolicy 注入 None = 无预算约束路径 | M0 仿真轨迹逐字兼容（缺省关闭=规格逐字纪律的预算侧形态；§2.4 熔断本身无开关，生产 harness 必配 policy） |

---

## 5. 账本事件与 payload 契约总表（LedgerKind ×15）

payload 字段与 pydantic 模型以 **SPEC-1 §4.3 为唯一真源**（PAYLOAD_SCHEMA 按 kind 注册，append 前强制校验）；本表补充 M1 视角：产出函数 / 触发时机 / 事务边界 / 幂等键。M0 已有 kind 标注「M0」。

| kind | 产出函数（M1） | 触发时机 | 事务边界 | 幂等键 |
|---|---|---|---|---|
| `dispatch` | dispatch() | queued→dispatched | tasks 行+事件同事务 | tasks.state 守卫（非 queued 拒绝） |
| `finding` | submit(auditor) / transition_finding / close_panel | 建档 / 结论流转 | 表行+事件同事务 | §2.4 迁移表守卫 |
| `vote` | submit(reviewer) | 评审票代落账 | votes 行+事件同事务 | votes UNIQUE(finding_id, reviewer)（库级，SPEC-1 §2） |
| `verify_result` | submit(verifier) | S2 代落账 | verify_results 行+事件+状态映射+S2 存款同事务 | §10.4 改判守卫 + propagate 幂等键 |
| `human_verdict` | apply_human_verdict | S3 面板直答 | 事件+状态流转+S3 存款同事务 | 终态守卫 + propagate 幂等键 |
| `exam_result` | 考试 runner（M0） | 入学/暗考/周期考 | 单事件 | exam_id（M0 现状） |
| `promotion` | promote_scan（M0） | 孵化流转（**承载考试通道全部流转**，to_status 如实记录） | 事件与 cells 重放投影 | 转换前件守卫（M0 幂等性） |
| `demotion` | promote_scan（M0） | 机制性熄灭 decay/dormant/eliminate | 同上 | 同上 |
| `scaling` | budget.on_cost（M1 复用）/ 扩缩容触发器 | 预算降级 / 编制伸缩 | spent/status+事件同事务 | trip 位（首次才落） |
| `rollback` | 快照回滚（M2+，§11 闸2） | 口碑快照指针切换 | 单事件 | snap id |
| `cluster_merge` | apply_merges | 建簇/扩簇 | clusters/cluster_members+事件同事务 | (level, frozenset(members)) |
| `cluster_split` | split_cluster | 人工拆分（零存款） | 成员表+verdict 清空+事件同事务 | 拆分前件守卫 |
| `re_route` | re_route_finding / submit 自动错配 | 类型错配 | 事件+弱存款同事务 | 每 finding 至多一次 |
| `re_dispatch` | re_dispatch | timeout/schema_fail 重派 | tasks 行+事件同事务 | attempts 守卫 |
| `deposit` | propagate_verdict / re_route_finding / M0 世界侧 | 存款（唯一驱动进化的 kind） | 与源状态变更同事务（§4.5 纪律 1） | src_seq 派生键（M1 派生行；M0 世界侧行维持现状） |

### 5.1 payload 字段契约增量

除下述一条外，全部 payload 模型**逐字沿用 SPEC-1 §4.3**，不新增不删改：

```python
class DepositPayload(BaseModel):               # kind=deposit（§3.2/§6.5 归属矩阵）
    finding: str | None
    cluster: str | None
    agent: str
    role: Role
    taxonomy_path: list[str]
    signal_tier: SignalTier
    direction: int = Field(ge=-1, le=1)
    weight: float
    origin: DepositOrigin = DepositOrigin.PROD
    src_seq: int | None = None                 # ← M1 新增可选字段（DECISIONS 2026-10-10-D6）：
                                               #   派生存款的源事件 seq（§5 表幂等键）。
                                               #   M0 历史事件无此字段（=None）；聚合器
                                               #   （kernel.cells._fold）不读该字段，
                                               #   M0 轨迹与重放逐位不受影响。
```

`ExamDepositPayload(DepositPayload)` 继承即自动携带 `src_seq`；`PAYLOAD_SCHEMA` 注册表不变。

### 5.2 派生存款的标准形（§6.5 归属矩阵的内核展开）

| 行 | agent | role | signal_tier | direction | weight |
|---|---|---|---|---|---|
| 报告者（auditor） | member.reporter | auditor | verdict tier | verdict 方向 | 源权重 × 1.0 |
| 报告者（scout，fast 档 §16.7-1） | member.reporter | scout | verdict tier | verdict 方向 | 源权重 × SCOUT_X |
| 侦察行（§6.5） | ctx.scouts | scout | verdict tier | verdict 方向 | 源权重 × SCOUT_X |
| 评审回校行（§6.5） | ctx.votes reviewer | reviewer | verdict tier | 与 verdict 同向 + / 反向 − | 源权重 × REVIEWER_X |
| 验证行（§6.5，仅 S3） | ctx.verifier_id | verifier | S3 | 同向 +1 / 反向 −1 | VERIFIER_CONFIRM(0.25) / VERIFIER_CONFLICT(0.5) |
| 猜测弱存款（§3） | scout_id | scout | S1 | −1 | 0.05（RE_ROUTE_WEAK_W） |

---

## 6. 不变量（编码 AI 必须遵守；SPEC-1 §6 的 M1 扩展）

### 6.1 写口唯一（SPEC-1 §6-2 扩展）

**全局状态只有内核一个写者**（§4.5 纪律 1）。表 → 唯一写口模块：

| 表/对象 | 唯一写口 | 状态 |
|---|---|---|
| `ledger` | `kernel.ledger.KernelLedger.append` | M0 |
| `cells` | `kernel.cells._write_cells`（agg_cells 内部） | M0 |
| `findings` / `evidence_nodes` / `findings.state` | `kernel.dispatch`（submit / transition_finding / close_panel / expire_run） | **M1** |
| `votes` | `kernel.dispatch.submit`（reviewer 分支） | **M1** |
| `verify_results` | `kernel.dispatch.submit`（verifier 分支） | **M1** |
| `clusters` / `cluster_members` / `clusters.verdict_*` | `kernel.cluster`（apply_merges / split_cluster / propagate_verdict） | **M1** |
| `tasks` | `kernel.dispatch`（dispatch / re_dispatch / expire_run） | **M1** |
| `candidates` | `kernel.dispatch.submit`（scout 分支：triaged/guess 回填） | **M1** |
| `runs.spent_cents` / `runs.status` | `kernel.budget.on_cost` / run 生命周期入口 | **M1** |
| `exam_*` / `snapshots` / `artifacts` 索引 | 考试 runner / 快照入口（非本 SPEC 范围） | M0/后置 |

- 蜂与仿真器：只读，或经 `KernelLedger.append` 落事件（蜂**从不**直接写任何表）；
- **静态扫描扩展**：tests/test_kernel_ledger.py 的写语句正则从 `(ledger|cells)` 扩展到上表全部内核写表（exact 词表见 SPEC-5 滚动导出）；越界即红。

### 6.2 append-only

- `ledger` 由 schema.sql 触发器（`ledger_no_update`/`ledger_no_delete`，RAISE(ABORT)）强制 append-only——**内核 bug 也改不动账本**（DECISIONS 2026-09-29）；
- 一切校正都是新事件：S3 与 S2/S1 冲突补记反向存款不回滚（§6.2 设计逐字）；拆分不回滚已传播存款（§1.3）；降级是新增 SCALING 事件而非改写历史；
- `runs.status` / `tasks.state` / `findings.state` / `clusters.verdict_*` 是**当前态投影**（非 append-only 表），但其每次变更必有同事务账本事件（SPEC-1 §2「不存在无账本事件的状态变更」），投影可随时由账本重建（§6.3）。

### 6.3 可重放

- **三投影全量重建**（SPEC-1 §2 重放重建规则 + §6-6 重放等价）：
  - `cells` ← `deposit` + `promotion`/`demotion`（M0 `agg_cells`，不动）；
  - `clusters`/`cluster_members`/`verdict_*` ← `cluster_merge`/`cluster_split` + `verify_result`/`human_verdict`（M1 新增）；
  - `findings.state`/`grade`/`confidence`/`disagreement` ← `finding`/`vote`/`verify_result`/`human_verdict`（M1 新增；state 取每 finding **最后一条** finding 事件的 payload.state）；
- **单一实现纪律**（承 M0 replay「聚合唯一实现」）：重建只重放事件，不重跑合并/传播逻辑——`cluster_merge.members` 即成员真源、deposit 事件即存款真源，投影 = 事件折叠，无第二套业务规则；
- **入口**：`ledger.rebuild()` M1 起重建三投影（返回含三投影摘要的 dict；`rebuild_cells` 保留为 cells 单投影入口）；`kernel.replay` 新增 `rebuild_clusters(ledger, upto_seq=None, *, from_seq=0)` / `rebuild_findings(...)` 与逐位 diff `diff_clusters` / `diff_findings`（字段 `==` 全等比较，无容差——M0 diff_cells 同一纪律）；CLI `python -m qswarm.kernel.replay` 接口不变、摘要增投影列；
- **崩溃修复**：`propagate_pending`（§1.4）以 src_seq 幂等键把「源事件已落、派生存款未落」的窗口收敛到零；重放取事件折叠（无时钟、无副作用、确定性）。

### 6.4 幂等

同一账本状态重复执行任何内核操作 → 零新事件、零表变更：

| 操作 | 幂等来源 |
|---|---|
| apply_merges | (level, frozenset(members)) 已存在即跳过 |
| split_cluster / dispatch / re_dispatch | 状态守卫（前件不满足即 ValueError，非静默） |
| propagate_verdict | (cluster, tier, src_seq) 派生键已存在即跳过 |
| close_panel / transition_finding | 迁移表守卫（已 voted/终态拒绝或零事件） |
| re_route_finding | 每 finding 至多一次 |
| on_cost trip | 首次 trip 才落 SCALING 事件 |
| expire_run | 已终态跳过 |
| promote_scan | M0 转换前件守卫（不动） |

「静默跳过」与「ValueError」的界线：**读侧幂等**（重复触发同一业务事实）→ 跳过返回 []；**写侧非法**（状态机不允许的动作）→ ValueError。测试两侧都要钉（§9）。

### 6.5 确定性（注入 rng + 字典序候选集）

- M1 全部新函数**零随机性**：不接 rng 参数、不消费任何随机源；内核中唯一的随机消费点仍是 `route` / `route_thompson`（M0 既有，注入 `random.Random`）；
- 一切集合展开/排序用**字典序或钉死的规范序**：聚类候选 (path, line_start, line_end, finding_id)、簇成员 finding_id、侦察/评审行 agent 字典序、S2 排序键平手字典序、格子遍历 sorted(view.cells)；
- 时间由注入时钟签发（M0 三时钟不动）；`同 (种子, 输入事件序, params)` ⇒ 内核决策与事件序列**逐位一致**，跨 PYTHONHASHSEED 成立（禁内建 hash，承 simulator.rng 纪律）。

---

## 7. 读写并发纪律（§4.5 落地）

### 7.1 事务边界（单事务原子性）

```python
@contextmanager
def transaction(ledger: KernelLedger):
    """多写原子上下文（kernel.ledger M1 最小扩展）：

    - 以 BEGIN IMMEDIATE 显式开事务，正常退出 COMMIT、异常 ROLLBACK；
    - **append 语义修订**：conn.in_transaction 为真时 append() 不再逐条 commit，
      由本上下文边界统一提交——无上下文时行为与 M0 逐条提交完全一致（M0 零影响）；
    - 全部「表行 + 账本事件」复合操作（§5 表事务边界列）必须在本上下文内执行，
      保证 §4.5 纪律 1「不存在半写态」。
    """
```

§4.5 五种读写对象与存储映射的 M1 归位：任务契约（tasks 表 + 进程内队列，写者=内核 dispatch）；工件（内容寻址库，产出蜂私有区，1→N）；Finding/Cluster/Vote/Verify（SQLite 各表，内核代落账，大字段只存 artifact:// 引用）；账本（append-only，仅内核串行 N→1 fold）；口碑视图（cells 快照，1→∞）。**没有蜂间消息存储**——协同的全部状态就是这两层存储（§4.5 逐字）。

### 7.2 并发三纪律的内核表达

1. **单写者**：§6.1 写口表是穷尽枚举；蜂的「提交」= 写私有工件区 → 回传结构化载荷 → `submit()` 过 schema 校验代为落账；Finding 与其首条 deposit 同事务；
2. **三种读各司其职**：1→1（派单契约、一簇一沙箱）、1→N（一个 finding 喂 params.panel.SIZE 只跨模型族评审蜂，§16.7-6）、N→1（存款→浓度、票→共识、finding→簇）；
3. **时间/身份解耦**：写一次读无数次；蜂熄灭后历史存款仍影响路由（stigmergy 本体）——时钟注入 + 账本重放保证该性质与部署形态无关。

### 7.3 本节决策记录

| # | 决策 | 理由 |
|---|---|---|
| D24 | transaction() 可重入（计数式嵌套）：已在事务中时内层不再 BEGIN/COMMIT，仅登记上下文，由最外层边界统一 COMMIT/ROLLBACK；内核复合函数（submit/apply_merges/propagate_verdict/on_cost 等）内部自开事务，嵌套调用时沿用外层 | BEGIN IMMEDIATE 事务中重入直接报错；复合函数与 harness 批处理两侧都需要原子性，禁嵌套会迫使内核暴露「裸/带事务」双形态；无上下文时 append 逐条提交与 M0 逐位一致（§7.1 语义不变） |

---

## 8. 新旋钮清单（缺省值 + 规格出处；缺省关闭=规格逐字）

> 纪律（承 M0 审计结论）：**凡新增旋钮，缺省值必须使行为 = 设计文档逐字口径**；扩展机制一律默认关闭。设计文档已定死的数值做成模块常量（不进 params.yaml，extra=forbid 纪律，同 route.SPEC_CAP 先例）；部署相关估计值才进 params.yaml。

### 8.1 params.yaml 新键（budget 节；实现子任务回填，SPEC-1 §5.2 budget 节扩展）

| 键 | 缺省 | 语义 | 出处 / 缺省时行为 |
|---|---|---|---|
| `budget.FAST_CENTS` | `2` | fast 档单 run 预算（美分） | §2.2 fast 成本锚点上界 $0.02；熔断为 §2.4 常设机制、无开关 |
| `budget.COST_PER_L2_CENTS` | `5` | L2 单点成本估计（配额估计用，非记账费率——费率以 on_cost 实测回报为准） | §2.4 公式 cost_per_L2 输入；§2.2 成本假设（L2 单点 12k in/2k out @强档 ≈$0.05）；为 0 → quota 恒 0（保守不派） |
| `budget.COST_PER_S2_CENTS` | `10` | S2 单次验证成本估计（同上） | §2.4 公式 cost_per_S2 输入；工程档调用+沙箱重试余量 ≈$0.10；为 0 → quota 恒 0 |

既有键（`PR_CENTS=100` / `FULL_CENTS=600` / `L2_QUOTA_CAP=45` / `RESCAN_WEEKLY=0.07`）缺省值不动。

### 8.2 模块常量（不进 params.yaml）

| 常量 | 落位 | 值 | 出处 / 缺省行为 |
|---|---|---|---|
| `CLUSTER_OVERLAP_MIN` | kernel.cluster | `0.5` | §3.4 逐字「行窗重叠 ≥50%」 |
| `CLUSTER_EMBED_SIM_MIN` | kernel.cluster | `0.9` | §3.4 逐字「向量相似度 ≥0.9」 |
| `S1_SOURCE_W` | kernel.cluster | `0.05` | §6.1 S1 权重区间 [0.05, 0.1] 下沿（世界侧实验旋钮不进内核，DECISIONS 2026-10-08 先例） |
| `S2_SOURCE_W` / `S3_SOURCE_W` | kernel.cluster | `0.5` / `1.0` | §6.1 规格常量（与 simulator.verdict 同值同源） |
| `RE_ROUTE_WEAK_W` | kernel.reroute | `0.05` | §6.5 附带逐字「−0.05 弱存款」 |
| `DISPATCH_MAX_ATTEMPTS` | kernel.dispatch | `2` | §16.7-8 逐字「重试 1 次」（初派+重试） |
| `HOP_BUDGET_BY_MODE` | kernel.dispatch | `{fast:0, pr:1, full:2}` | §2.2 档位表「深挖跳数」列逐字 |
| `ROLE_OUTPUT_SCHEMA` / `ROLE_TOOL_BOUNDS` / `ROLE_TASK_BOUNDS` | kernel.dispatch | 角色表 | §4.3 四件套 ②③④ 缺省；§4.1 权限边界；文案细化归 SPEC-3 |
| `FINDING_TRANSITIONS` | kernel.dispatch | §2.4 迁移表 | §10.2 状态机图逐字 + D8 一条 S3 直达边 |

### 8.3 注入点（非参数的装配面）

| 注入点 | 形态 | 缺省 | 缺省时行为 / 出处 |
|---|---|---|---|
| `sim_fn` | `SimFn \| None`（cluster_findings/fine_mergeable） | `None` | 精聚类不产生合并（保守漏并；§3.4 宁漏勿错）；仿真接通时注入确定性文本相似度（§0.4） |
| `BudgetPolicy` | budget 各函数可选注入 | `None` | 无预算约束路径 = M0 仿真轨迹逐字兼容；§2.4 熔断本身常设，生产 harness 必配（D18） |
| 谱系 `VerdictContext` / `lineage` / `scout_ids` | propagate/close_panel/submit 显式接参 | 必填 | 谱系解析归 harness（D14）；不改 SPEC-1 DDL |
| `embedder` 调用 | SimFn 内部自由实现 | — | 内核零模型依赖（§4.2 内核=纯代码确定性，LLM 只出建议工件） |

### 8.4 M0 既有旋钮（本 SPEC 全部不动）

`promote.*`（含 M_PRORATE/UNIQUE_PER_FAMILY/WINDOW_ADVANTAGE）、`tilt.*`、`shrink.LAMBDA`、`decay.*`、`demote.*`（含 FC_*）、`exam.*`（含 ENTRY_*/TYPE_ENTRY*）、`explore.UNIFORM_FLOOR`、`deposits.*`、`panel.*`、`roster.*`——缺省值与语义照 DECISIONS.md 既有定稿，M1 不回填不重释。

---

## 9. M1 门禁要点（SPEC-5 滚动导出的输入；每项机器可检）

1. **写口静态扫描扩展**：§6.1 表全词表越界即红；
2. **重放等价三投影**：随机事件前缀 rebuild vs 在线投影，`diff_cells`/`diff_clusters`/`diff_findings` 全空（逐位 ==）；
3. **幂等双跑**：同一账本连续两次 apply_merges / close_panel / propagate_verdict / re_route / on_cost / expire_run → 第二次零新事件；状态守卫类（dispatch 重复、终态流转、attempts 超限）→ ValueError；
4. **确定性**：同种子同输入序列两次运行事件流逐位一致；跨 PYTHONHASHSEED 一致；
5. **熔断状态机表驱动用例**：quota 公式边界（0 成本 / 0 余额 / 恰压线 ≥）、trip 一次性、降级阶梯、expire 零存款断言（expired 路径不产任何 deposit）；
6. **守卫负用例**：§10.4 verified_fp 缺 blocking 强制改判；纪律 2 reviewer==reporter 拒绝；§4.4 L3 必经 S2（单蜂 finding grade 不得为 L3）；
7. **仿真接通回归**：M1 引擎接真实聚类/预算后，M0 九度量与既有 golden 轨迹不回归（SPEC-4 §0 复用纪律的验收面）。

---

## 10. 决策记录索引（本 SPEC 拍板的未定细节；已同步 DECISIONS.md 2026-10-10 行）

| # | 一句话 | 详见 |
|---|---|---|
| D1 | 行窗重叠口径 = 交集/min | §1.2 |
| D2 | 聚类 all-pairwise，不做单链传递闭包 | §1.2 |
| D3 | CWE 双 None 退 (family,type)；单 None 不可并 | §1.2 |
| D4 | sim_fn 缺省 None = 精聚类不并 | §1.2/§8.3 |
| D5 | cluster_merge.members 记增量 | §1.6 |
| D6 | DepositPayload 增可选 src_seq | §5.1 |
| D7 | 结论传播展开序固定 | §1.4 |
| D8 | 迁移表增 S3 直达边（open→confirmed/rejected） | §2.4 |
| D9 | 状态流转以 kind=finding 事件承载 | §2.4 |
| D10 | 空池派单落 null assignee 事件、tasks 留 queued | §2.3 |
| D11 | verified_fp → rejected | §2.4 |
| D12 | re_route 确定性比对触发；细化不算错配 | §3.1 |
| D13 | 弱存款 = S1/−0.05/猜测路径 | §3.2 |
| D14 | 谱系显式接参，不改 DDL | §3.3 |
| D15 | S2 排序键三元组字典序 | §4.2 |
| D16 | 熔断判据 spent ≥ budget | §4.2 |
| D17 | 降级以 SCALING 事件承载 | §4.2 |
| D18 | BudgetPolicy 缺省 None = 无约束路径 | §4.4 |
| D19 | expire_run 非终态集含 verified（定稿修订） | §2.3/§2.6/§10.1 |
| D20 | finding_id/cluster_id 自增域 = 同前缀库内 max+1 | §1.3/§2.5/§10.1 |
| D21 | apply_human_verdict 终态守卫先于 direction 分支 | §1.4/§10.1 |
| D22 | aggregate_votes 分歧语义定稿补记（DECISIONS 缺行补齐） | §1.5/§10.1 |
| D23 | source_kind 词表增 FINDING + 源事件一致性校验 | §1.4/§10.1 |
| D24 | transaction() 可重入（计数式嵌套） | §7.3/§10.1 |

### 10.1 定稿拍板表（2026-10-10，规格门歧义拍板；五字段格式；已逐条同步 `docs/spec/DECISIONS.md`）

| 日期 | 模块 | 决策 | 理由 | 影响 |
|---|---|---|---|---|
| 2026-10-10 | SPEC-2/kernel | D19：expire_run 非终态覆盖集 = 迁移表全部非终态 {open, voted, inconclusive, verified}（修正 §2.3 原枚举遗漏 verified） | §2.4 迁移表 VERIFIED→EXPIRED 出边非空，verified 非终态；熔断/超时收尾若漏 verified 会留下永悬行，破坏「run 收尾无非终态」 | kernel/dispatch.expire_run；tests/test_kernel_dispatch.py test_expire_run_zero_deposit_terminal_idempotent |
| 2026-10-10 | SPEC-2/kernel | D20：finding_id/cluster_id 自增域 = 同日期前缀在账本库内已有最大序号 +1（§1.3/§2.5「run 内自增」措辞同步修订） | 引擎支持复用 db_path 追加写（多 run 同库同日期），findings.id/clusters.id 为 TEXT PRIMARY KEY，按 run 内计数第二 run 即撞主键 IntegrityError；单 run 独库时 max+1 与 run 内自增逐位同值，M0 轨迹（世界侧 F-SIM-<t> 自成前缀）零影响 | kernel/cluster.apply_merges；kernel/dispatch.submit；tests/test_kernel_cluster.py 跨 run 同库断言 |
| 2026-10-10 | SPEC-2/kernel | D21：apply_human_verdict 守卫次序 = 终态守卫先于 direction 分支；direction=0 对终态簇同样 ValueError，单事务回滚零落库 | 「终态再裁 ValueError」须无条件优先，否则终态簇可被忽略态反复刷审计事件、守卫形同虚设；事务语义下异常即整体回滚，无「半落事件」 | kernel/cluster.apply_human_verdict；tests/test_kernel_cluster.py test_apply_human_verdict_three_way_and_terminal_guard |
| 2026-10-10 | SPEC-2/kernel | D22：aggregate_votes 分歧语义定稿补记——direction=符号和符号（平票 0）、confidence=多数方向票四维均值之平均（无多数 0.5）、存在反向票 → disagreement=True 显式上报不强制仲裁；同步修正 §1.5 错引「见 §10-8」 | §10.3「分歧是资产」逐字；强制仲裁吞掉评审面信号，disagreement 供 queen_advisor 仲裁建议稿消费；该决策此前声称已同步 DECISIONS.md 但无对应行，本次补齐 | kernel/cluster.aggregate_votes；tests/test_kernel_cluster.py test_aggregate_votes_majority_tie_disagreement |
| 2026-10-10 | SPEC-2/kernel | D23：propagate_verdict source_kind 词表增 FINDING（S1 收口源事件 = panel 收口 finding 事件），实现增加源事件一致性校验（ledger[source_seq] 缺失或 kind 不符 → ValueError）；source_kind 仅审计用，payload 不承载、不新增 LedgerKind | 原词表（VERIFY_RESULT\|HUMAN_VERDICT\|VOTE）与 close_panel「source_seq=收口 finding 事件 seq」客观错位；一致性校验使 (cluster, tier, src_seq) 幂等键的 src_seq 可信 | kernel/cluster.propagate_verdict；kernel/dispatch.close_panel；tests/test_kernel_cluster.py 源事件校验负用例 |
| 2026-10-10 | SPEC-2/ledger | D24：ledger.transaction() 可重入（计数式嵌套，最外层边界统一提交/回滚）；内核复合函数内部自开事务、嵌套时沿用外层；无上下文时 append 逐条提交与 M0 逐位一致 | BEGIN IMMEDIATE 事务中重入直接报错；复合函数与 harness 批处理两侧都需要原子性；接口最小惊讶要求可重入而非双形态 API | kernel/ledger.transaction；tests/test_kernel_transaction.py 嵌套用例 |

> 滚动协议 3：本 SPEC 落地时上述决策已同步 `docs/spec/DECISIONS.md`；实现子任务发现的进一步未定细节按协议记入该文件，不回改本文件编号语义。
