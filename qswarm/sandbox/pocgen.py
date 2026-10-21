"""sandbox.pocgen：PoC 生成与 harness 断言约定（SPEC-9 §3；G2）。

**harness 契约**（PatchEval Docker 验证管线参照；PoC 与内核的唯一协议面）：
PoC 为可独立运行的 Python 脚本，环境变量 ``REPO``=被审仓库（只读副本）、
``WORK``=可写工作目录；断言逐条以行 ``ASSERT|<名称>|PASS|<详情>``（或 FAIL）
打印到 stdout；退出码 0 = 断言全过（复现成立面），非 0 = 断言失败/脚本身亡。
内核解析 ASSERT 行回填 VerifierReport.assertions（observed/log 派单方回填通道，
模板 §2.2 逐字「由派单方落盘后回填」）；无 ASSERT 行时按退出码合成单条断言。

**生成双源**（D39 拍板 verifier 蜂一体产 + 离线模板兜底）：
- ``extract_poc``：verifier 蜂同次响应的 ```` ```python ```` 围栏（D39 一体产；
  extract_json 只认 json 围栏，python 围栏不干扰既有解析纪律）；
- ``template_poc``：确定性家族模板（零 LLM）——G2 前置离线试跑的生成面 +
  LLM PoC 缺围栏时的兜底；v0 覆盖 python 命令注入/路径穿越两族，他族如实返回
  None（编排层计 template_unsupported，不硬凑）。
"""

from __future__ import annotations

import re

__all__ = ["extract_poc", "parse_assertions", "template_poc"]

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
    return f'''"PoC（模板生成）：{path}:{line} 命令注入复现（D39 harness 契约）——双级驱动"
import os
import subprocess
import sys
import tempfile

REPO = os.environ.get("REPO", ".")
target = os.path.join(REPO, {path!r})
marker = "QSWARM_POC_{seed}"
hits = []

# ① argv 直跑（sink 在模块顶层/直接消费 argv 的形态；payload 为拼接形态）
proc = subprocess.run(
    [sys.executable, target, "; echo " + marker],
    capture_output=True, text=True, timeout=60,
)
blob = (proc.stdout or "") + (proc.stderr or "")
if marker in blob:
    hits.append("argv")
    print("ASSERT|injection_payload_executed|PASS|stage=argv marker=" + marker)
else:
    print("ASSERT|injection_payload_executed|FAIL|stage=argv")

# ② import 驱动（sink 在模块函数内的形态，如 run(cmd)/render(path)）：
#    dup2 捕获 os.system 继承的 fd1；遍历模块公共函数 × 拼接/直传 payload 变体。
if "argv" not in hits:
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
        cap = tempfile.TemporaryFile()
        fd_backup = os.dup(1)
        argv_backup = list(sys.argv)
        got = False
        try:
            os.dup2(cap.fileno(), 1)
            for fn in fns:
                for payload in ("; echo " + marker, "echo " + marker):
                    sys.argv = [target, payload]   # sink 经 sys.argv 取污点的形态
                    try:
                        fn(payload)
                    except TypeError:
                        try:
                            fn()
                        except Exception:  # noqa: BLE001 — 单函数失败继续
                            pass
                    except Exception:  # noqa: BLE001 — 单函数失败继续
                        pass
                    finally:
                        sys.argv = argv_backup
                    sys.stdout.flush()
                    cap.seek(0)
                    if marker in cap.read().decode("utf-8", "replace"):
                        got = True
                        break
                    cap.seek(0, 2)
                if got:
                    break
        finally:
            sys.argv = argv_backup
            os.dup2(fd_backup, 1)
            os.close(fd_backup)
            cap.close()
        if got:
            hits.append("import")
            print("ASSERT|injection_payload_executed|PASS|stage=import marker="
                  + marker)
            sys.exit(0)
        print("ASSERT|injection_payload_executed|FAIL|stage=import")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 — 驱动失败如实 FAIL
        print("ASSERT|injection_payload_executed|FAIL|stage=import err="
              + type(exc).__name__)
        sys.exit(1)

sys.exit(0)
'''


def _py_path_traversal(path: str, line: int, seed: str) -> str:
    return f'''"PoC（模板生成）：{path}:{line} 路径穿越复现（D39 harness 契约）"
import os
import subprocess
import sys

REPO = os.environ.get("REPO", ".")
WORK = os.environ.get("WORK", ".")
target = os.path.join(REPO, {path!r})
canary = os.path.join(WORK, "qswarm_canary.txt")
with open(canary, "w", encoding="utf-8") as fh:
    fh.write("qswarm-poc-{seed}\\n")
# 经目标程序参数注入相对穿越，读 WORK 侧金丝雀（容器内 /etc 不可写的替身靶标）。
traversal = os.path.join("..", os.path.basename(WORK), "qswarm_canary.txt")
proc = subprocess.run(
    [sys.executable, target, traversal],
    capture_output=True, text=True, timeout=60, cwd=REPO,
)
blob = (proc.stdout or "") + (proc.stderr or "")
hit = "qswarm-poc-{seed}" in blob
print("ASSERT|path_traversal_read|"
      + ("PASS" if hit else "FAIL") + "|canary=qswarm-poc-{seed}")
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
