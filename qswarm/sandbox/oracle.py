"""sandbox.oracle：S2 执行式判据与负对照（方案 §6 / SPEC-10 §6、§8；P1 工作包 D）。

**两层结果**（方案 §6.1——不扩公共枚举，本模块内类型）：
- ``ExecutionStatus``：环境/超时/退出码/实际调用目标/断言自报——执行面发生了什么；
- ``ClaimValidation``：supported / refuted / inconclusive——漏洞主张是否成立。

**观察器分离**（§6.2 逐字）：判据不由被验方自定——冻结驱动脚本（本模块生成，
模型只可能替换驱动面、判读面永远在 ``observe_*``）只负责驱动目标并**原样记录**
（金丝雀目录由驱动创建、目标输出原样落盘）；机械判定在宿主侧
（``observe_injection`` / ``observe_traversal``）读取观察文件完成。
ASSERT 自报行（harness 契约）仅入 ``ExecutionStatus.assertions`` 留档，
**永不参与判定**——无条件 PASS（第 ⑦ 类）因此结构性不可信。

**成对判据**（§6.2「已知漏洞任务必须跑同一 PoC 的漏洞版与修复版」）：
``validate_pair``——漏洞版触发违规副作用 ∧ 修复版不触发 → supported；
修复版环境失败/超时/目标不健康（oc-v2 §6.4：缺依赖/未调用/合法对照失败）**都不算负对照通过**（→inconclusive）；
漏洞版未触发 → inconclusive（第 ⑨ 类：真实漏洞但 PoC 无效，绝不 verified_fp）。
refuted 不出自执行对照（须独立 blocking 证据覆盖前提，§6.1——内核 §10.4 面）。

**边界（如实）**：process 档同一 uid 执行，创建者不可逐进程归因——判别力来自
对照类（⑧ 不调用目标 ⇒ 无金丝雀 ⇒ 证明金丝雀依赖目标执行）而非每次运行的
取证；container 档跨 ns 观察面（独立挂载/属主）随生产面另行设计，P1 仅 process。
"""

from __future__ import annotations

import json
import os
import secrets as _secrets
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "ORACLE_VERSION",
    "OracleKind",
    "ClaimValidation",
    "ExecutionStatus",
    "ControlRun",
    "OracleVerdict",
    "build_fixture_driver",
    "observe_injection",
    "observe_traversal",
    "run_control",
    "validate_pair",
    "assess_single",
]


#: 判据版本（SPEC-10 §6：证据结构必填 oracle_version；判据公式变更须升版）。
#: oc-v2（R1-T2）：paired 健康分立（driver/目标执行/依赖健康/合法控制/攻击终态
#: 分记——HANDOVER §6.4）+ 观察路径宿主侧边界（§6.5）。oc-v1 的「驱动 exit 0 即
#: 修复版健康」口径作废（目标缺依赖退出 1 而驱动 exit 0 可成伪负对照）。
ORACLE_VERSION = "oc-v2"


class OracleKind(StrEnum):
    """判据由谁做出（D46 四层级锚点 + ① 的无配对降格形态）。

    - ``execution_paired``：①执行式 oracle——漏洞版/修复版成对对照，裁判=观察器；
    - ``upstream_truth``：②上游真值（Juliet/CVE 修复提交，标签来自补丁）；
    - ``cross_family_llm``：③跨族强 LLM 兜底（禁同族，D46 红线）；
    - ``blind_mechanical``：④证据单盲卷 + 只喂机械证据；
    - ``mechanical_single``：①的无配对降格——冻结模板 harness 单次执行、零模型
      意见（内核 verify 模板 PoC 路径）；学习标签面按 SPEC-10 §7 记录限制。
    """

    EXECUTION_PAIRED = "execution_paired"
    UPSTREAM_TRUTH = "upstream_truth"
    CROSS_FAMILY_LLM = "cross_family_llm"
    BLIND_MECHANICAL = "blind_mechanical"
    MECHANICAL_SINGLE = "mechanical_single"


class ClaimValidation(StrEnum):
    """漏洞主张判定（SPEC-10 §8 内部双层结果的判层面）。"""

    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ExecutionStatus:
    """执行面事实（方案 §6.1：环境、超时、退出码、实际调用目标、断言输出；
    R1-T2/oc-v2 健康分立——HANDOVER §6.4）。

    ``target_executed``：驱动确实调用了目标（target_rc 有记录）；
    ``target_health``：目标初始化/依赖健康（healthy | rejected_attack——合法
    修复对攻击输入 rc≠0 拒绝但合法输入服务正常 | missing_dep | not_invoked |
    timeout | unknown）；``legal_control``：正常输入对照的观察器判读（与 ASSERT
    自报无关）。"""

    target: str                       # 实际调用目标（fixture 变体名 / cluster 标识）
    sandbox_ok: bool                  # runner 面退出 0 且未超时
    timed_out: bool
    exit_code: int | None
    env_base: str
    assertions: list[tuple[str, bool, str]] = field(default_factory=list)
    target_exit_code: int | None = None   # 驱动内目标子进程退出码（记录面）
    obs_dir: str | None = None           # 观察目录（宿主侧判读入口；OBS 行回传）
    target_executed: bool | None = None   # oc-v2：目标确实执行（§6.4 分立）
    target_health: str = "unknown"        # oc-v2：目标健康（§6.4 分立）
    legal_control: bool | None = None     # oc-v2：合法输入对照（观察器判读）

    def to_dict(self) -> dict:
        return {
            "target": self.target, "sandbox_ok": self.sandbox_ok,
            "timed_out": self.timed_out, "exit_code": self.exit_code,
            "env_base": self.env_base,
            "assertions": [
                {"name": n, "observed": o, "detail": d}
                for n, o, d in self.assertions
            ],
            "target_exit_code": self.target_exit_code,
            "obs_dir": self.obs_dir,
            "target_executed": self.target_executed,
            "target_health": self.target_health,
            "legal_control": self.legal_control,
        }


@dataclass
class ControlRun:
    """一次对照运行：执行面事实 + 观察器机械判定。"""

    variant: str                      # vuln | fix | echo_safe | ... （对照变体名）
    execution: ExecutionStatus
    violation_observed: bool          # 观察器机械判定（与 ASSERT 自报无关）
    violation_evidence: str           # 判定依据（金丝雀/nonce 命中详情）

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "execution": self.execution.to_dict(),
            "violation_observed": self.violation_observed,
            "violation_evidence": self.violation_evidence,
        }


@dataclass
class OracleVerdict:
    """oracle 裁定（SPEC-10 §8 双层结果的聚合面）。"""

    claim_validation: ClaimValidation
    oracle_kind: OracleKind
    oracle_version: str
    control_results: list[ControlRun]
    reason: str
    evidence_ref: str | None = None

    def to_dict(self) -> dict:
        return {
            "claim_validation": self.claim_validation.value,
            "oracle_kind": self.oracle_kind.value,
            "oracle_version": self.oracle_version,
            "control_results": [c.to_dict() for c in self.control_results],
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
        }


# ── 冻结驱动脚本（只驱动、只记录、不判定）───────────────────────
#
# 驱动契约（harness 冻结面；模型生成面只可能替换本脚本，判读始终在宿主）：
# - stdout 首行 ``DRIVER|<family>|<variant>|<driver_kind>``；
# - 随后逐行 ``ASSERT|<名>|PASS/FAIL|<详情>``（自报留档，判定不用）；
# - 观察目录：驱动自建 ``<tmp>/qswarm-obs-<nonce>/``，金丝雀只可能由**目标
#   进程**（注入的命令/穿越读到的内容落盘）产生；目标 stdout/stderr 原样写
#   ``target_output.txt``、目标退出码写 ``target_rc.txt``；
#   ``OBS|<obs_dir>`` 行回传路径供宿主判读。
# - oc-v2 健康分立（HANDOVER §6.4）：穿越族合法对照单独落 ``legal_output.txt``
#   /``legal_rc.txt``（不与攻击面输出互覆）；注入族单面运行。
# - 布局（traversal）：``served/hello.txt``（合法面）+ ``secret/canary.txt``
#   （nonce 内容，允许根之外）；injection：payload 为
#   ``; echo <nonce> > <obs>/canary_<nonce>``。

_DRIVER_TMPL = '''\
"""qswarm 冻结驱动（oracle.py 生成；只驱动只记录不判定——判读在宿主 observe_*）。"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

FAMILY = {family!r}
VARIANT = {variant!r}
DRIVER_KIND = {driver_kind!r}
NONCE = {nonce!r}
TARGET_REL = {target_rel!r}

print(f"DRIVER|{{FAMILY}}|{{VARIANT}}|{{DRIVER_KIND}}", flush=True)
obs = Path(tempfile.mkdtemp(prefix="qswarm-obs-"))
obs.chmod(0o755)
print(f"OBS|{{obs}}", flush=True)
repo = Path(os.environ.get("REPO", "."))
target = repo / TARGET_REL

def record(name, ok, detail=""):
    print(f"ASSERT|{{name}}|{{'PASS' if ok else 'FAIL'}}|{{detail}}", flush=True)

def run_target(args, env_extra=None, capture=True, out_prefix="target"):
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        p = subprocess.run(
            [sys.executable, str(target)] + [str(a) for a in args],
            capture_output=capture, text=True, timeout=600, env=env,
        )
        (obs / (out_prefix + "_output.txt")).write_text(
            (p.stdout or "") + "\\n--- stderr ---\\n" + (p.stderr or ""),
            encoding="utf-8",
        )
        (obs / (out_prefix + "_rc.txt")).write_text(str(p.returncode), encoding="utf-8")
        return p
    except subprocess.TimeoutExpired:
        (obs / (out_prefix + "_rc.txt")).write_text("timeout", encoding="utf-8")
        return None

if FAMILY == "injection":
    canary = obs / ("canary_" + NONCE)
    payload = "; echo " + NONCE + " > " + str(canary)
    if DRIVER_KIND == "unconditional_pass":
        # 第 ⑦ 类：无条件自报 PASS、不驱动目标——判读面结构性不信自报。
        record("injection_payload_executed", True, "self-reported, target not driven")
        sys.exit(0)
    if DRIVER_KIND == "no_target_call":
        # 第 ⑧ 类：只准备环境、从不调用目标。
        record("prepared", True, "target never invoked")
        sys.exit(0)
    if DRIVER_KIND == "ineffective":
        # 第 ⑨ 类：真实漏洞目标 + 无效驱动（payload 不含注入形）。
        p = run_target(["benign-argument"])
        record("injection_payload_executed", False, "benign arg, no injection shape")
        sys.exit(0 if p is not None else 1)
    p = run_target([payload])
    hit = p is not None and canary.exists() and canary.read_text().strip() == NONCE
    record("injection_payload_executed", hit, "canary=" + str(canary))
    sys.exit(0 if p is not None else 1)

if FAMILY == "path_traversal":
    served = obs / "served"
    secret = obs / "secret"
    served.mkdir()
    secret.mkdir()
    (served / "hello.txt").write_text("public-hello", encoding="utf-8")
    (secret / "canary.txt").write_text("qsw-trav-" + NONCE, encoding="utf-8")
    traversal = os.path.join("..", "secret", "canary.txt")
    if DRIVER_KIND == "unconditional_pass":
        record("path_traversal_read", True, "self-reported, target not driven")
        sys.exit(0)
    if DRIVER_KIND == "no_target_call":
        record("prepared", True, "target never invoked")
        sys.exit(0)
    if DRIVER_KIND == "ineffective":
        # 第 ⑨ 类：只请求合法面文件（请求形不含穿越）。合法面单独落盘（oc-v2）。
        p = run_target([os.path.join("hello.txt")], env_extra={{"SERVE_ROOT": str(served)}},
                       out_prefix="legal")
        record("path_traversal_read", False, "legal request only")
        sys.exit(0 if p is not None else 1)
    # 合法对照（接口必须真的能工作；输出独立落盘 oc-v2）→ 穿越请求（违规面）
    pc = run_target(["hello.txt"], env_extra={{"SERVE_ROOT": str(served)}},
                    out_prefix="legal")
    pv = run_target([traversal], env_extra={{"SERVE_ROOT": str(served)}})
    legal_ok = pc is not None and "public-hello" in (pc.stdout or "")
    blob = (pv.stdout or "") + (pv.stderr or "") if pv is not None else ""
    hit = "qsw-trav-" + NONCE in blob
    record("legal_control", legal_ok, "hello.txt via served root")
    record("path_traversal_read", hit, "nonce=qsw-trav-" + NONCE)
    sys.exit(0 if (pc is not None and pv is not None) else 1)

raise RuntimeError("unknown family: " + FAMILY)
'''


#: 驱动变体词表（九类验收集的驱动面；effective 为缺省）。
DRIVER_KINDS = ("effective", "unconditional_pass", "no_target_call", "ineffective")


def build_fixture_driver(
    family: str, variant: str, *, driver_kind: str = "effective", nonce: str | None = None,
) -> str:
    """生成冻结驱动脚本（oracle 判读面的唯一驱动源；判据变更须升 ORACLE_VERSION）。"""
    if driver_kind not in DRIVER_KINDS:
        raise ValueError(f"未知 driver_kind：{driver_kind!r}（词表 {DRIVER_KINDS}）")
    if nonce is None:
        nonce = _secrets.token_hex(8)
    target_rel = f"{family}/{variant}/target.py"
    return _DRIVER_TMPL.format(
        family=family, variant=variant, driver_kind=driver_kind,
        nonce=nonce, target_rel=target_rel,
    )


# ── 宿主侧机械判读（观察器；与 ASSERT 自报无关）─────────────────

#: 观察文件读取上界（oc-v2 §6.5：拒绝过大文件的宿主读取）。
_OBS_FILE_MAX_BYTES = 1_000_000


def _read_obs_dir(stdout: str) -> Path | None:
    for line in stdout.splitlines():
        if line.startswith("OBS|"):
            return Path(line[4:].strip())
    return None


def _obs_root_ok(obs_dir: Path) -> bool:
    """观察目录宿主侧边界（oc-v2 §6.5）：

    - 必须是系统临时目录下**直接**的 ``qswarm-obs-*`` 目录（驱动契约形态）；
    - 路径不得含符号链接（resolve 前后一致——同 uid 可伪造文件，越界路径/
      symlink 一律不读）；
    - process 档判读面 = trusted-fixture instrumentation（同 uid、无命名空间
      分离），**不构成对抗性生产 oracle**（生产面独立采集随 container 面另设）。"""
    import tempfile

    if not obs_dir.is_absolute():
        return False
    name = obs_dir.name
    parent = obs_dir.parent
    tmp = Path(tempfile.gettempdir()).resolve()
    if not name.startswith("qswarm-obs-") or parent.resolve() != tmp:
        return False
    return obs_dir.resolve() == obs_dir and not obs_dir.is_symlink()


def _obs_read(obs_dir: Path, rel: str) -> bytes | None:
    """有界安全读取观察文件（越界/symlink/过大 → None；判定记 untrusted）。"""
    if not _obs_root_ok(obs_dir):
        return None
    raw = obs_dir / rel
    # 词法约束（无 ../ 越界）+ 符号链接拒——**不得 resolve 后再查**（跟随链接即击穿）
    if raw.parent != obs_dir or raw.is_symlink():
        return None
    try:
        if raw.stat().st_size > _OBS_FILE_MAX_BYTES:
            return None
        return raw.read_bytes()
    except OSError:
        return None


def observe_injection(execution: ExecutionStatus, nonce: str) -> tuple[bool, str]:
    """injection 判读：金丝雀存在 ∧ 内容 = nonce（由目标内执行的注入命令产生）。"""
    if execution.obs_dir is None:
        return False, "obs_dir_missing"
    obs = Path(execution.obs_dir)
    raw = _obs_read(obs, f"canary_{nonce}")
    if raw is None:
        if not _obs_root_ok(obs):
            return False, "obs_path_untrusted"
        return False, "canary_absent_or_unreadable"
    content = raw.decode("utf-8", errors="replace").strip()
    if content != nonce:
        return False, "canary_content_mismatch"
    return True, f"canary=canary_{nonce} content=nonce"


def observe_traversal(execution: ExecutionStatus, nonce: str) -> tuple[bool, str]:
    """traversal 判读：目标输出含 nonce（内容只在允许根外的金丝雀文件里）。"""
    if execution.obs_dir is None:
        return False, "obs_dir_missing"
    obs = Path(execution.obs_dir)
    raw = _obs_read(obs, "target_output.txt")
    if raw is None:
        if not _obs_root_ok(obs):
            return False, "obs_path_untrusted"
        return False, "target_output_absent"
    blob = raw.decode("utf-8", errors="replace")
    if f"qsw-trav-{nonce}" not in blob:
        return False, "nonce_not_in_target_output"
    return True, "nonce_in_target_output"


def _assess_target_health(
    target_rc: int | str | None,
    target_output: bytes | None,
    legal_rc: int | str | None,
    legal_output: bytes | None,
    family: str,
) -> tuple[bool, str, bool | None]:
    """oc-v2 目标健康分立（HANDOVER §6.4——修复版健康不再混用驱动 exit 0）。

    返回 (target_executed, target_health, legal_control)：
    - 目标确实执行 = target_rc 有记录；
    - missing_dep = 输出含 ModuleNotFoundError/ImportError（目标自身无法启动）；
    - healthy = rc==0（或穿越族合法对照独立成立时的攻击拒绝 rc≠0——
      ``rejected_attack``：合法修复可对恶意输入拒绝，但须证明正常服务合法输入）；
    - legal_control 只按观察器读取的 legal_output 判（ASSERT 自报不参与）。"""
    target_executed = target_rc is not None
    health = "unknown"
    out_text = (target_output or b"").decode("utf-8", "replace")
    legal_control: bool | None = None
    if target_rc == "timeout":
        health = "timeout"
    elif target_rc is None:
        health = "not_invoked"
    elif "ModuleNotFoundError" in out_text or "ImportError" in out_text:
        health = "missing_dep"
    else:
        if family == "path_traversal":
            legal_text = (legal_output or b"").decode("utf-8", "replace")
            legal_control = (
                legal_rc == 0 and "public-hello" in legal_text
            )
            if legal_control:
                health = "healthy" if target_rc == 0 else "rejected_attack"
            else:
                health = "unhealthy_legal_control_failed"
        else:
            health = "healthy" if target_rc == 0 else "unhealthy_crash"
    return target_executed, health, legal_control


def run_control(
    runner, repo: Path, *, family: str, variant: str,
    driver_kind: str = "effective", nonce: str | None = None,
    observe=None,
) -> ControlRun:
    """跑一次对照：驱动脚本经 runner 执行 → 宿主侧观察器机械判定。

    ``observe`` 缺省按 family 选 ``observe_injection`` / ``observe_traversal``。
    观察目录由驱动在沙箱内创建、路径经 stdout ``OBS|`` 行回传（process 档与宿主
    共享文件系统；container 档跨 ns 观察面随生产面另行设计，P1 不涉）。

    oc-v2：观察路径过宿主侧边界检查（§6.5——临时根直下 ``qswarm-obs-*``、拒
    symlink/越界/过大）；目标健康按 target_rc/legal 面分立判读（§6.4），
    **驱动 exit 0 不再等于修复版健康**。"""
    from .pocgen import parse_assertions   # 契约面复用（自报留档）

    if nonce is None:
        nonce = _secrets.token_hex(8)
    if observe is None:
        observe = observe_injection if family == "injection" else observe_traversal
    driver = build_fixture_driver(family, variant, driver_kind=driver_kind, nonce=nonce)
    result = runner.execute(driver, repo=repo, deps=[], seed=f"oracle-{family}-{variant}")
    obs_dir = _read_obs_dir(result.stdout) if result.stdout else None
    target_rc = _read_target_rc(obs_dir)
    target_output = (
        _obs_read(Path(obs_dir), "target_output.txt") if obs_dir is not None else None
    )
    legal_rc = (
        _read_named_rc(Path(obs_dir), "legal_rc.txt") if obs_dir is not None else None
    )
    legal_output = (
        _obs_read(Path(obs_dir), "legal_output.txt") if obs_dir is not None else None
    )
    target_executed, target_health, legal_control = _assess_target_health(
        target_rc, target_output, legal_rc, legal_output, family,
    )
    execution = ExecutionStatus(
        target=f"{family}/{variant}",
        sandbox_ok=bool(result.ok),
        timed_out=bool(result.timed_out),
        exit_code=result.exit_code,
        env_base=result.env_base,
        assertions=list(parse_assertions(result.stdout)),
        target_exit_code=target_rc,
        obs_dir=str(obs_dir) if obs_dir is not None else None,
        target_executed=target_executed,
        target_health=target_health,
        legal_control=legal_control,
    )
    violation, evidence = observe(execution, nonce)
    return ControlRun(
        variant=variant, execution=execution,
        violation_observed=violation, violation_evidence=evidence,
    )


def _read_target_rc(obs_dir: Path | None) -> int | str | None:
    return _read_named_rc(obs_dir, "target_rc.txt")


def _read_named_rc(obs_dir: Path | None, name: str) -> int | str | None:
    if obs_dir is None:
        return None
    rc = obs_dir / name
    if not rc.exists():
        return None
    text = rc.read_text(encoding="utf-8", errors="replace").strip()
    if text == "timeout":
        return "timeout"
    try:
        return int(text)
    except ValueError:
        return None


# ── 判定（成对 + 单次）──────────────────────────────────────────

def validate_pair(vuln: ControlRun, fix: ControlRun) -> OracleVerdict:
    """成对判据（§6.2 + oc-v2 §6.4 健康分立）：同一驱动 × {漏洞版, 修复版}。

    - 修复版执行面失败/超时 → inconclusive（fix_control_invalid）；
    - **修复版目标不健康 → inconclusive（fix_target_unhealthy）**——目标缺依赖
      （missing_dep）/未调用（not_invoked）/合法对照失败
      （unhealthy_legal_control_failed）都不是有效负对照；「驱动 exit 0」不再
      等于健康（oc-v1 仪器缺陷，R1 修正）；
    - 修复版可对攻击输入以 rc≠0 拒绝（rejected_attack）——但须合法输入对照
      成立（legal_control=True 由观察器独立判读）；
    - 漏洞版目标未执行/环境失败 → inconclusive（vuln_execution_failed）；
    - 漏洞版违规 ∧ 修复版健康不违规 → supported；
    - 双版皆违规 → inconclusive（poc_not_discriminating）；
    - 漏洞版无违规 → inconclusive（violation_not_observed——第 ⑨ 类，
      绝不支持也绝不据此 refuted）。"""
    controls = [vuln, fix]
    if fix.execution.timed_out or not fix.execution.sandbox_ok:
        return OracleVerdict(
            ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="fix_control_invalid",
        )
    if (
        not fix.execution.target_executed
        or fix.execution.target_health
        not in ("healthy", "rejected_attack")
    ):
        return OracleVerdict(
            ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="fix_target_unhealthy",
        )
    if fix.execution.legal_control is False:
        return OracleVerdict(
            ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="fix_target_unhealthy",
        )
    if not vuln.execution.target_executed or (
        not vuln.execution.sandbox_ok and not vuln.violation_observed
    ):
        # 漏洞版自身执行失败（缺依赖/超时/未调用类）——无从观察，不入任何可信面。
        return OracleVerdict(
            ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="vuln_execution_failed",
        )
    if vuln.violation_observed and not fix.violation_observed:
        return OracleVerdict(
            ClaimValidation.SUPPORTED, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="vuln_violates_fix_does_not",
        )
    if vuln.violation_observed and fix.violation_observed:
        return OracleVerdict(
            ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
            controls, reason="poc_not_discriminating",
        )
    return OracleVerdict(
        ClaimValidation.INCONCLUSIVE, OracleKind.EXECUTION_PAIRED, ORACLE_VERSION,
        controls, reason="violation_not_observed",
    )


def assess_single(
    *, sandbox_ok: bool, timed_out: bool, exit_code: int | None,
    assertions: list[tuple[str, bool, str]] | None = None,
    violation_assertions: list[str] | None = None,
    control_assertions: list[str] | None = None,
    poc_source: str = "template", target: str = "unknown",
    env_base: str = "unknown",
) -> OracleVerdict:
    """内核单次执行面的判层（H4；mechanical_single——① 的无配对降格形态）。

    - 判据只认**冻结面断言**（模板 PoC 的副作用断言名，``violation_assertions``
      白名单）——自报 PASS 不在白名单即不构成判据（第 ⑦ 类结构性不可信）；
    - ``control_assertions``（对照断言白名单，如 traversal 的 legal_control）：
      任一缺失/FAIL → 不 supported（§6.2「合法输入对照」门；对照面只降不升）；
    - 模板路径（poc_source="template"）：violation 断言 PASS ∧ 对照过 →
      supported；蜂产 PoC（poc_source="bee"）：断言由蜂脚本自报，非独立判据 →
      如实 inconclusive（judge_not_independent）——学习标签面按 SPEC-10 §7
      记录限制，不改 M5 outcome 行为（双层结果，方案 §6.1）；
    - 执行失败/超时/无白名单断言 → inconclusive（reason 注明具体原因）。
    """
    assertions = list(assertions or [])
    names_pass = {n for n, ok, _d in assertions if ok}
    controls_ok = not control_assertions or all(
        c in names_pass for c in control_assertions
    )
    control = ControlRun(
        variant="single_run",
        execution=ExecutionStatus(
            target=target, sandbox_ok=sandbox_ok, timed_out=timed_out,
            exit_code=exit_code, env_base=env_base, assertions=assertions,
        ),
        violation_observed=bool(
            violation_assertions and sandbox_ok and not timed_out and controls_ok
            and names_pass.intersection(violation_assertions)
        ),
        violation_evidence=(
            "frozen_harness_assertions=" + ",".join(sorted(names_pass))
            if assertions else "no_assertions"
        ),
    )
    if timed_out:
        reason = "execution_timeout"
    elif not sandbox_ok:
        reason = "execution_failed"
    elif not controls_ok:
        reason = "control_failed"
    elif control.violation_observed:
        reason = (
            "frozen_harness_violation_asserted"
            if poc_source == "template" else "judge_not_independent_self_reported"
        )
    else:
        reason = "violation_not_observed"
    claim = (
        ClaimValidation.SUPPORTED
        if control.violation_observed and poc_source == "template"
        else ClaimValidation.INCONCLUSIVE
    )
    return OracleVerdict(
        claim, OracleKind.MECHANICAL_SINGLE, ORACLE_VERSION, [control], reason=reason,
    )
