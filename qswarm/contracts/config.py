"""配置加载器：configs/params.yaml（§5.2）与 configs/taxonomy.yaml（§5.3）。

YAML → pydantic 严格校验：缺字段、未知字段（extra=forbid）、越界值、交叉约束
（T_MIN≤T_MAX 等）一律 ValidationError。初值=设计默认（SPEC-1 §5.2），
仿真器 M0 定稿后回填（SPEC-4 §4 输出物 1）。
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = _REPO_ROOT / "configs"
DEFAULT_PARAMS_PATH = CONFIGS_DIR / "params.yaml"
DEFAULT_TAXONOMY_PATH = CONFIGS_DIR / "taxonomy.yaml"


# ── params.yaml（§5.2）──────────────────────────────────────────

class PromoteParams(BaseModel):
    N: int = Field(ge=1)                       # 窗口内存款笔数门槛
    N_S2S3: int = Field(ge=0)                  # 其中 S2/S3 当量门槛
    DELTA: float = Field(gt=0, le=1)           # 质量分位阈值
    M: int = Field(ge=1)                       # 长窗任务数
    M_window_days: int = Field(ge=1)
    # M 按节点期望簇份额等比缩放（M0 定稿旋钮，DECISIONS 2026-10-09）：
    # False = §7.3 逐字（全节点同一 M）；True = 最高份额节点保持 M，其余按
    # share = (v1_weight/节点扇出)/max 同比例缩放（家族不含类型扇出）。
    M_PRORATE: bool = False
    # 同族唯一专员（M0 定稿旋钮，DECISIONS 2026-10-09「晋升加同族唯一专员约束
    # K=3→1」）：True = 同 (role, family) 至多一个 active 格——家族内已有 active
    # 专员（含本轮新晋/重激活）时，其余格不再晋升（路由侧 §7.4 K=3 冗余不动）。
    UNIQUE_PER_FAMILY: bool = False
    # 多窗优势稳定性（M0 定稿旋钮，DECISIONS 2026-10-09「多窗 LB 稳定性判据」）：
    # 0 = 关闭（§7.3 逐字：仅累计 LB）；K ≥ 1 = 另要求最近 K 个衰退窗逐窗
    # Wilson_LB(窗内质量) > 同窗 LOO 基线 + DELTA（任一窗无数据/基线击穿 → 不晋升）。
    WINDOW_ADVANTAGE: int = Field(default=0, ge=0)


class TiltParams(BaseModel):
    T_BASE: float = Field(ge=0, le=1)          # 基础温度
    GAMMA: float = Field(ge=0)                 # 自适应强度
    T_MIN: float = Field(ge=0, le=1)
    T_MAX: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _min_le_max(self) -> "TiltParams":
        if self.T_MIN > self.T_MAX:
            raise ValueError(f"T_MIN ({self.T_MIN}) 必须 ≤ T_MAX ({self.T_MAX})")
        return self


class ShrinkParams(BaseModel):
    LAMBDA: float = Field(gt=0)                # 收缩系数 = 2.5 个 S3 当量（§5.1）


class DecayParams(BaseModel):
    T_HALF_DAYS: float = Field(gt=0)           # prod 半衰期（天）
    T_HALF_DAYS_EXAM: float = Field(gt=0)      # exam 半衰期（天）
    EPS_FLOOR: float = Field(ge=0, le=1)       # 存款下限


class DemoteParams(BaseModel):
    WINDOWS: int = Field(ge=1)                 # 连续不达标窗口数
    DORMANT_DAYS: int = Field(ge=1)            # 休眠判定天数
    REACTIVATE_N: int = Field(ge=1)            # 复活所需存款笔数
    # ── 周期复核熄灭通道（M0 定稿旋钮，默认 FC_DAYS=0=关闭=§7.3 逐字；§8.1
    # 「淘汰仍只看后续周期考」+「连续 2 个考核周期低于及格线」的落地）──
    # FC_DAYS > 0 时：每 FC_DAYS 天对全部 active 格传唤一场复核考（FC_ITEMS 题，
    # 逐题判对概率由真实能力映射，与暗考同式）。判据为**趋势式**：单场得分低于
    # 「该格冻结基线 − FC_MARGIN」记一次不达标（基线 = 该格前 FC_BASE_EXAMS 场
    # 复核考得分的均值，首场只立基线不判）；连续 FC_CONSEC 场不达标 →
    # demotion(reason=decay) 熄灭。趋势式而非绝对及格线的理由：退化型（前 top
    # 漂移 −0.25）与平庸型考后 q 分布重叠，任何绝对线都无法同时满足 ④ 与 ⑤
    # ——相对自身获任水平的显著回落才有判别力（sim_report ⑤/④ 联合分析）。
    # 周期考无生产信号依赖（投票/标记存款不反映评审蜂/验证蜂自身能力），对全
    # 角色可观测；不写存款（§8.2 防污染纪律）。
    FC_DAYS: int = Field(default=0, ge=0)
    FC_ITEMS: int = Field(default=60, ge=1)
    FC_MARGIN: float = Field(default=0.18, gt=0, le=1)
    FC_CONSEC: int = Field(default=2, ge=1)
    FC_BASE_EXAMS: int = Field(default=2, ge=1)


class ExamParams(BaseModel):
    PROMO_ITEMS: int = Field(ge=1)             # 晋升考题数
    PROMO_REAL: int = Field(ge=0)              # 至少判对 real 题数
    PROMO_MIN_REJECT_FP: int = Field(ge=0)     # 至少拒绝 fp 题数
    PERIODIC_ITEMS: int = Field(ge=1)          # 周期考题数
    PERIODIC_PASS: float = Field(ge=0, le=1)   # 周期考通过率
    COOLDOWN_DAYS: int = Field(ge=0)           # 重考冷却（天）
    # 入学播种直晋 candidate 门槛（M0 定稿旋钮，DECISIONS 2026-10-09；§8.4 扩展）：
    # 0.0 = 关闭（§8.2/§7.3 原口径）；>0 = 入学考正确率 ≥ 该值且方向为正的
    # (agent, role, family) 格在播种时直转 candidate（暗考闸门不变，§8.2）。
    ENTRY_DIRECT: float = Field(default=0.0, ge=0, le=1)
    # ── 孵化直晋通道 v2（M0 定稿旋钮，全部默认关闭=上一行原口径；§8.4 播种通道
    # 的孵化侧延伸，DECISIONS 2026-10-09 v2 条）──
    # ENTRY_TOP_FRAC > 0：直晋提名席位从「K=1 唯一 ace」扩展为每 (role, node) 按
    # 考试正确率排名的前 ⌈frac·n⌉ 席（与 §2 度量的 top-20% 口径对齐），
    # ENTRY_DIRECT/TYPE_ENTRY 仍为正确率门槛、ENTRY_MARGIN 为席位边际
    # （入选最低正确数 − 未入选最高正确数 ≥ m 题才成席）。
    ENTRY_TOP_FRAC: float = Field(default=0.0, ge=0, le=1)
    # 席位边际（题数；0 = 关闭）。
    ENTRY_MARGIN: int = Field(default=0, ge=0)
    # 类型级孵化考（0 = 关闭；>0 = 类型 quiz 正确率门槛）：§8.4 入学考只覆盖家族级，
    # 类型节点由世界侧 type_quiz_items 题的类型 quiz 测 q_t，提名规则同家族级。
    TYPE_ENTRY: float = Field(default=0.0, ge=0, le=1)
    # 提名候选人的暗考宽限天数（0 = 关闭=§8.2 逐字：当夜即考）：宽限期内直晋
    # 候选人等待生产佐证（ENTRY_CORROB_S2S3），期满仍未佐证才落 §8.2 暗考。
    ENTRY_GRACE_DAYS: int = Field(default=0, ge=0)
    # 生产佐证门槛（0 = 关闭）：直晋候选人格 n_eff_s2s3（纯生产 S2/S3 当量，
    # §8.4 考试存款不计）≥ 该值且 C* 高于同 (role,node) LOO 基线时，免暗考
    # 直转 active（考试提名 + 生产佐证双闸；宽限期内每晚复查）。
    ENTRY_CORROB_S2S3: float = Field(default=0.0, ge=0)
    # 直晋席位兑现窗（0 = 关闭=不设窗）：播种后 ENTRY_WINDOW_DAYS 天内的夜批
    # 才对直晋提名格兑现（佐证/暗考/重考），窗闭后通道关闭、格回归 §7.3 常规
    # 判据——§8.4 孵化是冷启动机制，兑现窗限定其时间域（≥ ② 的 500 任务窗）。
    ENTRY_WINDOW_DAYS: int = Field(default=0, ge=0)
    # 类型席位合成分（0 = 关闭，纯类型 quiz 分）：score = (1−w)·quiz_acc +
    # w·entry_acc——家族级入学分是 q_f 的独立测量，与类型 quiz 合成降低席位
    # 选择的测量噪声（DECISIONS 2026-10-09 v2 条）。
    TYPE_ENTRY_COMBINE: float = Field(default=0.0, ge=0, le=1)

    @model_validator(mode="after")
    def _real_le_items(self) -> "ExamParams":
        if self.PROMO_REAL > self.PROMO_ITEMS:
            raise ValueError(f"PROMO_REAL ({self.PROMO_REAL}) 必须 ≤ PROMO_ITEMS ({self.PROMO_ITEMS})")
        return self


class ExploreParams(BaseModel):
    UNIFORM_FLOOR: float = Field(ge=0, le=1)   # 探索通道 20% 均匀保底（§5.2）


class BudgetParams(BaseModel):
    PR_CENTS: int = Field(ge=0)                # pr 档预算（美分）
    FULL_CENTS: int = Field(ge=0)              # full 档预算（美分）
    L2_QUOTA_CAP: int = Field(ge=0)            # L2 配额上限
    RESCAN_WEEKLY: float = Field(ge=0, le=1)   # 周期重扫任务占比
    # ── M1 预算旋钮（SPEC-2 §8.1 新键；DECISIONS 2026-10-10「旋钮缺省纪律落地」
    # 行回填）──缺省值=§2.2 成本锚点换算的规格逐字值；熔断为 §2.4 常设机制、无
    # 开关——「缺省关闭=规格逐字」在 M1 的落点是无预算约束路径（kernel.
    # BudgetPolicy 注入缺省 None = M0 仿真轨迹逐字兼容），本节三键只是成本锚点
    # 估计值，不改变仿真器任何行为。带 pydantic 缺省（而非 required）是硬约束：
    # 黄金回归 manifest_params() 用早于 M1 的旧参数校验全模型，缺键必须回落
    # 规格缺省（黄金 digest 逐位不变门禁，tests/golden_loader.py）。
    # fast 档单 run 预算（美分）：budget_for_mode(fast) 取值；§2.2 锚点上界 $0.02。
    FAST_CENTS: int = Field(default=2, ge=0)
    # L2 单点成本估计（美分；配额估计用，非记账费率——费率以 on_cost 实测回报
    # 为准）：quota_l2 = min(cap, ⌊remaining / cost⌋)，为 0 → quota 恒 0（保守不派）。
    COST_PER_L2_CENTS: int = Field(default=5, ge=0)
    # S2 单次验证成本估计（美分；工程档调用+沙箱重试余量）：
    # quota_s2 = ⌊存活数 × remaining / cost⌋，为 0 → quota 恒 0（保守不派）。
    COST_PER_S2_CENTS: int = Field(default=10, ge=0)


class DepositsParams(BaseModel):
    # 角色系数（§6.5 归属矩阵；verifier 冲突向系数以幅值存，符号由机制施加）
    SCOUT_X: float = Field(ge=0, le=2)
    REVIEWER_X: float = Field(ge=0, le=2)
    VERIFIER_CONFLICT: float = Field(ge=0, le=2)
    VERIFIER_CONFIRM: float = Field(ge=0, le=2)


class PanelParams(BaseModel):
    SIZE: int = Field(ge=1)                    # 评审团人数（§16.7-6）
    MIN_FAMILIES: int = Field(ge=1)            # 最少家族数

    @model_validator(mode="after")
    def _families_le_size(self) -> "PanelParams":
        if self.MIN_FAMILIES > self.SIZE:
            raise ValueError(f"MIN_FAMILIES ({self.MIN_FAMILIES}) 必须 ≤ SIZE ({self.SIZE})")
        return self


class RosterParams(BaseModel):
    scout: int = Field(ge=0)
    auditor: int = Field(ge=0)
    reviewer: int = Field(ge=0)
    verifier: int = Field(ge=0)


# ── perception 节（SPEC-7 §6 新旋钮清单；M3 回填）────────────────
# 全部带缺省 = 规格逐字值（「缺省关闭/缺省值=规格逐字」纪律同 BudgetParams）：
# 旧参数集（黄金 manifest_params()）无本节时回落缺省，黄金 digest 逐位不变。
# 嵌套键面与 SPEC-7 §6 表逐字对应：perception.model.enabled /
# perception.rules.{enabled,timeout_s} / perception.l0.max_file_lines /
# perception.l1.{fast_top_k,files_per_shard} / perception.scan.rescan_sample_frac。

class PerceptionModelParams(BaseModel):
    """通道 B（模型直扫）开关。执行者 = 侦察蜂兼任（SPEC-7 §1.3/D26）。"""

    enabled: bool = True


class PerceptionRulesParams(BaseModel):
    """通道 C（semgrep 规则）参数。必选件（D30）：enabled 缺省开、门禁实跑判据；
    运行期单次超时/故障 → 该轮通道缺席计数，漏斗不停摆（SPEC-7 §1.4 红线）。"""

    enabled: bool = True
    timeout_s: int = Field(default=120, gt=0)


class PerceptionL0Params(BaseModel):
    """L0 通道 B 分片参数：单片行上限（超长按函数边界切）。"""

    max_file_lines: int = Field(default=800, ge=1)


class PerceptionL1Params(BaseModel):
    """L1 侦察参数：fast 档候选数（§2.2 档位表 Top-3）与分片大小。"""

    fast_top_k: int = Field(default=3, ge=1)
    files_per_shard: int = Field(default=20, ge=1)


class PerceptionScanParams(BaseModel):
    """周期重扫抽样率（§16.7-4；SPEC-7 §6 v1 只落开关，重扫调度随后续里程碑）。"""

    rescan_sample_frac: float = Field(default=0.05, ge=0, le=1)


class PerceptionRepomapParams(BaseModel):
    """repo map 切片预算（SPEC-8 §1.2/D37）：邻域子图切片的文件数与总行数上限
    （种子文件恒在；截断计数入 RepoSlice 与报告）。"""

    model_config = ConfigDict(extra="forbid")

    max_files: int = Field(default=12, ge=1)
    max_lines: int = Field(default=1500, ge=1)


class ConsultParams(BaseModel):
    """L2 会诊编排旋钮（SPEC-8 §6/D31–D33；extra=forbid 纪律同根模型）。"""

    model_config = ConfigDict(extra="forbid")

    wall_clock_budget_s: int = Field(default=3600, gt=0)   # D33 pr 档墙钟预算
    min_panel: int = Field(default=2, ge=2)                # 面板 fail-closed 下限（<2 不开）
    s1_rho: float = Field(default=0.4, ge=0, le=1)         # D32 共模份额（=M0 Z_PANEL_W）
    l2_context_lines: int = Field(default=40, ge=1)        # L2 候选块窗（深诊档）


class PerceptionParams(BaseModel):
    """感知层旋钮根（SPEC-7 §6；extra=forbid 纪律同根模型）。"""

    model_config = ConfigDict(extra="forbid")

    model: PerceptionModelParams = PerceptionModelParams()
    rules: PerceptionRulesParams = PerceptionRulesParams()
    l0: PerceptionL0Params = PerceptionL0Params()
    l1: PerceptionL1Params = PerceptionL1Params()
    scan: PerceptionScanParams = PerceptionScanParams()
    repomap: PerceptionRepomapParams = PerceptionRepomapParams()


class ParamsConfig(BaseModel):
    """params.yaml 根模型。初值见 configs/params.yaml（=设计默认）。"""

    model_config = ConfigDict(extra="forbid")

    promote: PromoteParams
    tilt: TiltParams
    shrink: ShrinkParams
    decay: DecayParams
    demote: DemoteParams
    exam: ExamParams
    explore: ExploreParams
    budget: BudgetParams
    deposits: DepositsParams
    panel: PanelParams
    roster: RosterParams
    perception: PerceptionParams = PerceptionParams()
    consult: ConsultParams = ConsultParams()


# ── taxonomy.yaml（§5.3）────────────────────────────────────────

class FamilySpec(BaseModel):
    types: list[str] = Field(min_length=1)     # 家族内细分类型，非空
    v1_weight: float = Field(default=1.0, gt=0)  # 缺省=1.0，路由前归一化（SPEC-4 §1.2）


class TaxonomyConfig(BaseModel):
    """taxonomy.yaml 家族树。Family 枚举为种子版六家族；本文件可扩展（§7.2）。"""

    model_config = ConfigDict(extra="forbid")

    families: dict[str, FamilySpec] = Field(min_length=1)


# ── 加载入口 ────────────────────────────────────────────────────

def _read_yaml(path: str | Path) -> dict:
    p = Path(path)
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: YAML 顶层必须是映射（dict），实际为 {type(data).__name__}")
    return data


def load_params(path: str | Path = DEFAULT_PARAMS_PATH) -> ParamsConfig:
    """读取并校验 params.yaml；缺字段/未知字段/越界/交叉约束违反 → pydantic ValidationError。"""
    return ParamsConfig.model_validate(_read_yaml(path))


def load_taxonomy(path: str | Path = DEFAULT_TAXONOMY_PATH) -> TaxonomyConfig:
    """读取并校验 taxonomy.yaml；缺字段/未知字段/越界 → pydantic ValidationError。"""
    return TaxonomyConfig.model_validate(_read_yaml(path))
