"""sandbox.envcache：沙箱环境缓存（§16.4 缓解项；SPEC-9 §2.3）。

env 身份 = blake2b(base + 排序后依赖清单) 前 16 hex（确定性；同 env 重跑零重建）：

- container 档：env = docker 镜像 ``qswarm-sb:{env_key}``——依赖非空时
  ``docker build``（FROM base + pip install -r）落镜像，本地已有即复用；
- process 档：env = 缓存目录 ``{env_cache_dir}/process/{env_key}/``——
  ``pip install --target sitedeps`` 落依赖，标记文件在即复用。

缓存目录只存派生物（镜像/依赖），零账本零 LLM；构建失败 → :class:`EnvBuildError`
（编排层据此判 verify_error(env_build_fail)，§6.3）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["EnvBuildError", "EnvHandle", "env_key_for"]


class EnvBuildError(RuntimeError):
    """env 构建失败（镜像 build / 依赖安装 / docker 不可达）→ env_build_fail。

    RuntimeError 而非 ValueError：环境侧事件，非值域错误（AdapterError 同式）。"""


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


def build_container_env(
    base_image: str,
    deps: list[str],
    *,
    cache_dir: Path,
    timeout_s: int,
) -> EnvHandle:
    """container 档 env：依赖非空 → docker build 镜像 ``qswarm-sb:{key}``（本地
    已有即复用）；依赖为空 → 直接用 base_image（零构建）。"""
    deps = [str(d) for d in deps if str(d).strip()]
    key = env_key_for(f"container:{base_image}", deps)
    if not deps:
        return EnvHandle(env_key=key, env_base=base_image, deps_text="")
    tag = f"qswarm-sb:{key}"
    inspect = subprocess.run(
        ["docker", "image", "inspect", tag], capture_output=True, text=True
    )
    if inspect.returncode == 0:
        return EnvHandle(env_key=key, env_base=tag, deps_text=_requirements_text(deps))
    ctx = Path(cache_dir) / "container" / key
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "requirements.txt").write_text(_requirements_text(deps), encoding="utf-8")
    dockerfile = (
        f"FROM {base_image}\n"
        "COPY requirements.txt /tmp/requirements.txt\n"
        "RUN pip install --no-cache-dir -r /tmp/requirements.txt\n"
    )
    (ctx / "Dockerfile").write_text(dockerfile, encoding="utf-8")
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
) -> EnvHandle:
    """process 档 env：pip install --target 落 ``sitedeps``（标记文件在即复用）。"""
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
