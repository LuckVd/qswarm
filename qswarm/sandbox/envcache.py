"""sandbox.envcache：沙箱环境缓存（§16.4 缓解项；SPEC-9 §2.3；R1-T1 边界收紧）。

env 身份 = blake2b(base + 排序后依赖清单) 前 16 hex（确定性；同 env 重跑零重建）：

- container 档：env = docker 镜像 ``qswarm-sb:{env_key}``——依赖非空时
  ``docker build``（FROM base + pip install -r）落镜像，本地已有即复用；
- process 档：env = 缓存目录 ``{env_cache_dir}/process/{env_key}/``——
  ``pip install --target sitedeps`` 落依赖，标记文件在即复用。

R1-T1 纪律（HANDOVER §5.2；docs/R1_CHANGE_NOTE.md §1-T1）：

- **依赖字符串先过 :func:`validate_deps_spec` 拒绝矩阵**（raw URL/VCS/local
  path/pip 选项/换行/空段全拒）——模型可填的 requirements 不得携带安装面
  任意性，两档共用同一校验；
- **process 档缺省拒绝运行时安装**（``allow_install=False``）：marker 不在而
  deps 非空 → EnvBuildError——只复用宿主预置 env，不对模型的 requirements
  执行宿主 pip；
- **container 档缺省拒绝联网构建**（``allow_build=False``）：镜像本地缺失 →
  EnvBuildError（不拉取不联网 build）；deps 为空时 base 镜像同样要求本地在库
  （docker run 的自动拉取面在 runner 侧由本检查前置封死）。

缓存目录只存派生物（镜像/依赖），零账本零 LLM；构建失败 → :class:`EnvBuildError`
（编排层据此判 verify_error(env_build_fail)，§6.3）。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "EnvBuildError",
    "EnvHandle",
    "env_key_for",
    "validate_deps_spec",
]


class EnvBuildError(RuntimeError):
    """env 构建失败（镜像 build / 依赖安装 / docker 不可达）→ env_build_fail。

    RuntimeError 而非 ValueError：环境侧事件，非值域错误（AdapterError 同式）。"""


#: 合法依赖串：普通名（可带 extras）+ 可选单版本说明符（==/>=/<=/!=/~=/>/<）。
#: R1-T1 拒绝矩阵的**允许面**（其余一律拒）：无 URL/VCS/local path/选项/空白/换行。
_DEP_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?"          # 名字段
    r"(\[[A-Za-z0-9,._-]+\])?"                            # extras
    r"((==|~=|!=|<=|>=|<|>)[A-Za-z0-9.+_*!-]+)?$"         # 单版本说明符
)

#: 依赖串长度上界（防退化长串进入安装面）。
_DEP_MAX_LEN = 200


def validate_deps_spec(deps: list[str]) -> None:
    """R1-T1 依赖拒绝矩阵（两档共用；违规 → EnvBuildError）。

    拒绝：空段/纯空白、换行与任何空白字符、pip 选项（``-`` 开头 / ``--``）、
    URL（``://``）、VCS（``git+``/``svn+``/``hg+``/``bzr+`` 前缀、``@`` 引用）、
    local path（``/``、``.``、``~`` 开头或含 ``\\``）、非法字符与超长。"""
    for d in deps:
        s = str(d)
        if not s.strip():
            raise EnvBuildError(f"依赖串为空段：{d!r}")
        if any(ch.isspace() for ch in s):
            raise EnvBuildError(f"依赖串含空白/换行（拒绝矩阵）：{d!r}")
        if s.startswith("-") or "--" in s:
            raise EnvBuildError(f"依赖串形似 pip 选项（拒绝矩阵）：{d!r}")
        if "://" in s or s.startswith(("git+", "svn+", "hg+", "bzr+")) or "@" in s:
            raise EnvBuildError(f"依赖串形似 URL/VCS 引用（拒绝矩阵）：{d!r}")
        if s.startswith(("/", ".", "~")) or "\\" in s:
            raise EnvBuildError(f"依赖串形似 local path（拒绝矩阵）：{d!r}")
        if len(s) > _DEP_MAX_LEN:
            raise EnvBuildError(f"依赖串超长（>{_DEP_MAX_LEN}）：{d[:32]!r}…")
        if not _DEP_RE.match(s):
            raise EnvBuildError(f"依赖串不匹配允许语法（拒绝矩阵）：{d!r}")


@dataclass(frozen=True)
class EnvHandle:
    """一次构建的 env 句柄（runner 执行 PoC 的输入）。"""

    env_key: str
    env_base: str                 # container: 镜像 tag；process: "process:<解释器>"
    deps_text: str                # requirements 文本（deps_ref 工件内容源）
    # process 档专用：依赖落点（container 档 None——依赖已烘进镜像）
    sitedeps: Path | None = None


def env_key_for(base: str, deps: list[str]) -> str:
    """env 身份（确定性）：blake2b(base + 排序依赖) 前 16 hex。"""
    h = hashlib.blake2b(digest_size=16)
    h.update(base.encode("utf-8"))
    for d in sorted(deps):
        h.update(b"\x00")
        h.update(str(d).encode("utf-8"))
    return h.hexdigest()[:16]


def _requirements_text(deps: list[str]) -> str:
    return "".join(f"{d}\n" for d in sorted(deps))


def _run(cmd: list[str], *, timeout_s: int) -> None:
    """构建子进程（docker/pip）；非零或超时 → EnvBuildError。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=int(timeout_s)
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise EnvBuildError(
            f"env 构建失败：{' '.join(cmd[:3])}…：{type(exc).__name__}"
        ) from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise EnvBuildError(
            f"env 构建失败（rc={proc.returncode}）：{' '.join(cmd[:3])}…："
            f"{' | '.join(tail)}"
        )


def _image_present(tag_or_image: str) -> bool:
    """镜像本地在库判定（docker image inspect rc==0；不可达/缺失 → False）。"""
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", tag_or_image],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


def build_container_env(
    base_image: str,
    deps: list[str],
    *,
    cache_dir: Path,
    timeout_s: int,
    allow_build: bool = False,
) -> EnvHandle:
    """container 档 env（R1-T1：缺省拒绝联网构建与拉取）。

    - deps 先过 :func:`validate_deps_spec`；
    - 依赖非空：镜像 ``qswarm-sb:{key}`` 本地已有即复用（预置面）；缺失且
      ``allow_build=False``（缺省）→ EnvBuildError——不联网 build；
    - 依赖为空：base 镜像必须本地在库（封死 docker run 自动拉取面），缺失 →
      EnvBuildError。"""
    validate_deps_spec(deps)
    deps = [str(d) for d in deps if str(d).strip()]
    key = env_key_for(f"container:{base_image}", deps)
    if not deps:
        if not _image_present(base_image):
            raise EnvBuildError(
                f"base 镜像 {base_image!r} 本地缺失——R1-T1 拒绝联网拉取"
                "（预置镜像或显式授权后重试）"
            )
        return EnvHandle(env_key=key, env_base=base_image, deps_text="")
    tag = f"qswarm-sb:{key}"
    if _image_present(tag):
        return EnvHandle(env_key=key, env_base=tag, deps_text=_requirements_text(deps))
    if not allow_build:
        raise EnvBuildError(
            f"镜像 {tag} 本地缺失且 deps 非空——R1-T1 缺省拒绝联网构建"
            "（allow_build=False；预置 env 或显式 dev 授权后重试）"
        )
    ctx = Path(cache_dir) / "container" / key
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "requirements.txt").write_text(_requirements_text(deps), encoding="utf-8")
    dockerfile = (
        f"FROM {base_image}\n"
        "COPY requirements.txt /tmp/requirements.txt\n"
        "RUN pip install --no-cache-dir -r /tmp/requirements.txt\n"
    )
    (ctx / "Dockerfile").write_text(dockerfile, encoding="utf-8")
    if not _image_present(base_image):
        raise EnvBuildError(
            f"base 镜像 {base_image!r} 本地缺失——R1-T1 拒绝联网拉取（build 前置）"
        )
    _run(
        ["docker", "build", "-t", tag, str(ctx)],
        timeout_s=timeout_s,
    )
    return EnvHandle(env_key=key, env_base=tag, deps_text=_requirements_text(deps))


def build_process_env(
    deps: list[str],
    *,
    cache_dir: Path,
    timeout_s: int,
    allow_install: bool = False,
) -> EnvHandle:
    """process 档 env（R1-T1：缺省拒绝运行时宿主 pip 安装）。

    - deps 先过 :func:`validate_deps_spec`；
    - marker 在 → 复用预置 env（宿主预装面，合法）；
    - marker 不在且 deps 非空：``allow_install=False``（缺省）→ EnvBuildError
      ——不对模型的 requirements 字符串执行宿主 pip。"""
    validate_deps_spec(deps)
    deps = [str(d) for d in deps if str(d).strip()]
    base = f"process:{sys.executable}"
    key = env_key_for(base, deps)
    envdir = Path(cache_dir) / "process" / key
    marker = envdir / ".qswarm-env-ok"
    sitedeps = envdir / "sitedeps"
    deps_text = _requirements_text(deps)
    if marker.exists():
        return EnvHandle(env_key=key, env_base=base, deps_text=deps_text,
                         sitedeps=sitedeps)
    if deps and not allow_install:
        raise EnvBuildError(
            f"process env 未预置（deps={deps}）——R1-T1 缺省拒绝运行时宿主 pip"
            "（allow_install=False；预置 env 或显式 dev 授权后重试）"
        )
    sitedeps.mkdir(parents=True, exist_ok=True)
    if deps:
        _run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--target", str(sitedeps), *deps],
            timeout_s=timeout_s,
        )
    marker.write_text(
        json.dumps({"base": base, "deps": sorted(deps)}, ensure_ascii=False),
        encoding="utf-8",
    )
    return EnvHandle(env_key=key, env_base=base, deps_text=deps_text,
                     sitedeps=sitedeps)
