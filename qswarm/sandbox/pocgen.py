"""sandbox.pocgen：PoC 生成与 harness 断言约定（SPEC-9 §3；G2 + P1 模板 v2）。

**harness 契约**（PatchEval Docker 验证管线参照；PoC 与内核的唯一协议面）：
PoC 为可独立运行的 Python 脚本，环境变量 ``REPO``=被审仓库（只读副本）、
``WORK``=可写工作目录；断言逐条以行 ``ASSERT|<名称>|PASS|<详情>``（或 FAIL）
打印到 stdout；退出码 0 = 断言全过（复现成立面），非 0 = 断言失败/脚本身亡。
内核解析 ASSERT 行回填 VerifierReport.assertions（observed/log 派单方回填通道，
模板 §2.2 逐字「由派单方落盘后回填」）；无 ASSERT 行时按退出码合成单条断言。

**模板 v2（P1 工作包 D，判据升级 oc-v2）**：断言从 echo-marker（stdout 回显
可满足——仅回显安全版可击穿，E0 前实证仪器缺陷）升级为**副作用金丝雀**：
- 命令注入：判据 = 注入命令在目标进程内创建含 marker 的金丝雀文件（PoC 自身
  绝不创建；回显目标的 stdout 结构性不可能满足）；
- 路径穿越：判据 = 目标输出含 nonce（nonce 只存在于允许根外金丝雀**文件内容**
  里，回显路径不可能命中）；附合法对照断言（读 REPO 内既存文件的诊断面）。
冻结断言名（内核 assess_single 白名单）：``injection_payload_executed`` /
``path_traversal_read``（violation 面）+ ``legal_control``（对照面）。

**生成双源**（D39 拍板 verifier 蜂一体产 + 离线模板兜底）：
- ``extract_poc``：verifier 蜂同次响应的 ```` ```python ```` 围栏（D39 一体产；
  extract_json 只认 json 围栏，python 围栏不干扰既有解析纪律）；
- ``template_poc``：确定性家族模板（零 LLM）——G2 前置离线试跑的生成面 +
  LLM PoC 缺围栏时的兜底；v2 覆盖 python 命令注入/路径穿越两族，他族如实返回
  None（编排层计 template_unsupported，不硬凑）。
"""

from __future__ import annotations

import re

__all__ = [
    "extract_poc", "parse_assertions", "template_poc",
    "VIOLATION_ASSERTIONS", "CONTROL_ASSERTIONS",
]

#: 冻结断言名白名单（assess_single 的判据面；断言名变更 = 判据变更须升 oc 版本）。
#: violation 面 = 违规副作用判据；control 面 = 合法对照门（§6.2，只降不升）。
VIOLATION_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "injection": ("injection_payload_executed",),
    "authz": ("path_traversal_read",),
    "config_dep": ("path_traversal_read",),
}
CONTROL_ASSERTIONS: dict[str, tuple[str, ...]] = {
    "injection": (),
    "authz": ("legal_control",),
    "config_dep": ("legal_control",),
}

_PY_FENCE_RE = re.compile(r"```python\s*(.*?)```", re.DOTALL)

#: 断言行约定（harness 契约逐字）。
ASSERT_LINE_RE = re.compile(r"^ASSERT\|(.+?)\|(PASS|FAIL)\|(.*)$", re.MULTILINE)


def extract_poc(raw: str) -> str | None:
    """verifier 响应 → PoC 脚本（最后一个 ```` ```python ```` 围栏；无 → None）。

    与 prompts.parse.extract_json 的「取最后一个围栏」纪律同形（E3 确定性）。"""
    fences = _PY_FENCE_RE.findall(raw)
    return fences[-1].strip() if fences else None


def parse_assertions(stdout: str) -> list[tuple[str, bool, str]]:
    """PoC stdout → [(名称, observed, 详情)]（ASSERT 行序即输出序）。"""
    return [
        (name.strip(), flag == "PASS", detail.strip())
        for name, flag, detail in ASSERT_LINE_RE.findall(stdout)
    ]


def _py_command_injection(path: str, line: int, seed: str) -> str:
    return f'''"""PoC（模板 v2）：{path}:{line} 命令注入复现——副作用金丝雀判据（oc-v2）。

判据 = 注入命令在**目标进程内**执行并创建含 marker 的金丝雀文件（PoC 自身
绝不创建金丝雀；仅回显目标的 stdout 不可能满足——仅回显安全版结构性 FAIL）。"""
import os
import subprocess
import sys
import tempfile

REPO = os.environ.get("REPO", ".")
target = os.path.join(REPO, {path!r})
marker = "QSWARM_POC_{seed}"


def _obs_dir():
    # harness 契约可写点优先（container 档 WORK=tmpfs；process 档 drop 场景
    # WORK 不可写时回落系统临时目录——两级都试，如实取向可用面）。
    work = os.environ.get("WORK", "")
    if work and os.path.isdir(work) and os.access(work, os.W_OK):
        return tempfile.mkdtemp(prefix="qswarm-poc-", dir=work)
    return tempfile.mkdtemp(prefix="qswarm-poc-")


obs = _obs_dir()
canary = os.path.join(obs, "canary_" + marker)
payload = "; echo " + marker + " > " + canary


def canary_hit():
    try:
        with open(canary, encoding="utf-8") as fh:
            return fh.read().strip() == marker
    except OSError:
        return False


# ① argv 直跑（sink 在模块顶层/直接消费 argv 的形态）
proc = subprocess.run(
    [sys.executable, target, payload],
    capture_output=True, text=True, timeout=60,
)
if canary_hit():
    print("ASSERT|injection_payload_executed|PASS|stage=argv marker=" + marker)
    sys.exit(0)
print("ASSERT|injection_payload_executed|FAIL|stage=argv")

# ② import 驱动（sink 在模块函数内的形态，如 run(cmd)）：遍历公共函数 ×
#    拼接/直传 payload 变体；判据同为金丝雀（不捕获 stdout——回显不算证据）。
try:
    import importlib.util

    spec = importlib.util.spec_from_file_location("qswarm_poc_target", target)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fns = [
        getattr(mod, n) for n in sorted(dir(mod))
        if callable(getattr(mod, n)) and not n.startswith("_")
        and getattr(getattr(mod, n), "__module__", "") == "qswarm_poc_target"
    ]
    if not fns:
        raise RuntimeError("no public callable in target module")
    argv_backup = list(sys.argv)
    got = False
    try:
        for fn in fns:
            for pl in (payload, payload[2:]):   # "; cmd" 与 "cmd" 两形
                sys.argv = [target, pl]         # sink 经 sys.argv 取污点的形态
                try:
                    fn(pl)
                except TypeError:
                    try:
                        fn()
                    except Exception:  # noqa: BLE001 — 单函数失败继续
                        pass
                except Exception:  # noqa: BLE001 — 单函数失败继续
                    pass
                finally:
                    sys.argv = argv_backup
                if canary_hit():
                    got = True
                    break
            if got:
                break
    finally:
        sys.argv = argv_backup
    if got:
        print("ASSERT|injection_payload_executed|PASS|stage=import marker="
              + marker)
        sys.exit(0)
    print("ASSERT|injection_payload_executed|FAIL|stage=import")
    sys.exit(1)
except Exception as exc:  # noqa: BLE001 — 驱动失败如实 FAIL
    print("ASSERT|injection_payload_executed|FAIL|stage=import err="
          + type(exc).__name__)
    sys.exit(1)
'''


def _py_path_traversal(path: str, line: int, seed: str) -> str:
    return f'''"""PoC（模板 v2）：{path}:{line} 路径穿越复现——nonce 金丝雀判据（oc-v2）。

判据 = 目标输出含 nonce（nonce 只存在于允许根（REPO）之外的金丝雀**文件内容**
里；回显路径/参数不可能命中）。合法对照 = 读 REPO 内既存文件（目标自身源码，
接口可用性诊断面）。"""
import os
import subprocess
import sys
import tempfile

REPO = os.environ.get("REPO", ".")
target = os.path.join(REPO, {path!r})
nonce = "qsw-trav-{seed}"


def _obs_dir():
    work = os.environ.get("WORK", "")
    if work and os.path.isdir(work) and os.access(work, os.W_OK):
        return tempfile.mkdtemp(prefix="qswarm-poc-", dir=work)
    return tempfile.mkdtemp(prefix="qswarm-poc-")


obs = _obs_dir()
canary = os.path.join(obs, "canary.txt")
with open(canary, "w", encoding="utf-8") as fh:
    fh.write(nonce + "\\n")
# 允许根 = REPO（目标 cwd）；穿越 = 相对路径逃出 REPO 到 obs 金丝雀。
traversal = os.path.relpath(canary, REPO)


def run_arg(arg):
    return subprocess.run(
        [sys.executable, target, arg],
        capture_output=True, text=True, timeout=60, cwd=REPO,
    )


# 合法对照（诊断面）：请求 REPO 内既存文件（目标自身）——接口可用性记录。
legal = run_arg({path!r})
legal_ok = legal.returncode == 0
print("ASSERT|legal_control|" + ("PASS" if legal_ok else "FAIL")
      + "|stage=legal rc=" + str(legal.returncode))

# 穿越请求：nonce 命中 = 目标真读到允许根外文件内容。
proc = run_arg(traversal)
blob = (proc.stdout or "") + (proc.stderr or "")
hit = nonce in blob
print("ASSERT|path_traversal_read|"
      + ("PASS" if hit else "FAIL") + "|nonce=" + nonce)
sys.exit(0 if hit else 1)
'''


def template_poc(
    family: str,
    type_: str | None,
    *,
    path: str,
    line_start: int,
    lang: str,
    seed: str,
) -> str | None:
    """确定性家族模板 PoC（零 LLM；v0 两族，他族 None = 不硬凑）。

    ``seed`` 进 marker（同簇重跑可区分）；返回文本即可执行脚本（写入沙箱
    workdir 后按 harness 契约执行）。"""
    fam = str(family).casefold()
    if lang == "python" and fam == "injection":
        return _py_command_injection(path, int(line_start), seed)
    if lang == "python" and fam in ("authz", "config_dep"):
        return _py_path_traversal(path, int(line_start), seed)
    return None
