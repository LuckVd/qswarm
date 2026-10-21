"""sandbox.runner：SandboxRunner 双档实现（SPEC-9 §2.1/§2.2；D38/D43）。

- **ContainerRunner**（container 档）：docker CLI 单容器执行——``--network none``
  （禁网）、``--user``（非特权）、``--read-only`` + ``--tmpfs /work``（只读根 +
  可写点）、``--memory/--cpus/--ulimit``（资源限额）、子进程墙钟超时 + 容器收割
  （超时 → error_kind=timeout）；repo 只读挂载 ``/repo:ro``，docker.sock 零挂载；
  docker 不可达 → EnvBuildError（判 env_build_fail，不误判 poc_fail）。
- **ProcessRunner**（process 档）：subprocess + rlimits（NOFILE/NPROC/AS/CPU）+
  仓快照副本（原件零触碰）+ unshare 禁网（可用性探测，缺则如实降级记录）+
  setuid 非特权（root 时尝试，失败如实降级）。**近似红线**：只产工件与报告、
  不写生产账本（D43-a，写口门在 kernel.verify）。

四分类落点（§6.3）：env 构建失败 → env_build_fail（EnvBuildError 捕获面）；
墙钟超时 → timeout；poc_fail vs flaky 由编排层按同 PoC 重复执行结果集判定
（:func:`classify_result` 只承载 runner 侧两类）。
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import SandboxConfig
from .envcache import (
    EnvBuildError,
    EnvHandle,
    build_container_env,
    build_process_env,
)

__all__ = [
    "OUTPUT_CAP_BYTES",
    "ContainerRunner",
    "FakeRunner",
    "ProcessRunner",
    "SandboxResult",
    "classify_result",
    "make_runner",
]

#: stdout/stderr 捕获上限（防失控输出撑爆内存；截断如实记录）。
OUTPUT_CAP_BYTES = 1_000_000

#: docker 探针命令（可用性一次性判定）。
_DOCKER_PROBE = ("docker", "info", "--format", "ok")


def _cap(text: str) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= OUTPUT_CAP_BYTES:
        return text
    return raw[:OUTPUT_CAP_BYTES].decode("utf-8", "ignore") + "\n…(truncated)"


@dataclass
class SandboxResult:
    """一次沙箱执行的完整结果面（编排层 reconciliation 的唯一输入）。"""

    tier: str
    ok: bool                       # exit 0 且未超时
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout: str
    stderr: str
    env_key: str
    env_base: str
    workdir: str | None
    mitigations: dict = field(default_factory=dict)   # 红线近似面如实记录
    error_kind: str | None = None  # env_build_fail | timeout | None（runner 侧两类）
    log_text: str = ""             # stdout+stderr 全文（工件内容源）

    @property
    def env_build_failed(self) -> bool:
        return self.error_kind == "env_build_fail"


def classify_result(result: SandboxResult) -> str | None:
    """runner 侧四分类切片：env_build_fail / timeout；执行完成 → None
    （poc_fail vs flaky 由编排层按重复执行判，SPEC-9 §3.4）。"""
    return result.error_kind


class ContainerRunner:
    """container 档（D38-a；S2 生产存款唯一口径，D43-a）。"""

    tier = "container"

    def __init__(self, config: SandboxConfig) -> None:
        if config.tier != "container":
            raise ValueError("ContainerRunner 需要 tier=container 配置")
        self._cfg = config
        self._docker_ok: bool | None = None
        self.env_build_s: float = 0.0        # R7-② 披露面：env 构建墙钟

    def _probe_docker(self) -> bool:
        """docker 可用性一次性判定（缺命令/守护进程不可达 → False，不抛穿）。"""
        if self._docker_ok is None:
            if shutil.which("docker") is None:
                self._docker_ok = False
            else:
                try:
                    proc = subprocess.run(
                        _DOCKER_PROBE, capture_output=True, text=True, timeout=30
                    )
                    self._docker_ok = proc.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    self._docker_ok = False
        return bool(self._docker_ok)

    def execute(
        self, poc_script: str, *, repo: Path, deps: list[str], seed: str
    ) -> SandboxResult:
        """构建 env（缓存复用）→ 单容器执行 PoC → SandboxResult。"""
        t0 = time.monotonic()
        if not self._probe_docker():
            return self._fail_env(
                EnvBuildError(
                    "docker 不可用（命令缺失或守护进程不可达）——container 档红线"
                    "无法施加，拒绝降级执行（D38/D43：process 档须显式选档）"
                ),
                t0,
            )
        try:
            env = build_container_env(
                self._cfg.base_image, deps,
                cache_dir=Path(self._cfg.env_cache_dir),
                timeout_s=self._cfg.timeout_s,
            )
        except EnvBuildError as exc:
            return self._fail_env(exc, t0)
        self.env_build_s += time.monotonic() - t0
        run_dir = Path(tempfile.mkdtemp(prefix="qswarm-sb-"))
        poc_path = run_dir / "poc.py"
        poc_path.write_text(poc_script, encoding="utf-8")
        repo_path = Path(repo).resolve()
        staged = False
        # repo 目录非「其他用户可读可入」（如 0700 的 mkdtemp/私有检出）→
        # container 内非特权用户不可穿越挂载根（2026-10-21 实测 EACCES）。
        # 处置：stage 一份 0755 副本（原件零触碰），mitigations 如实记录。
        if (repo_path.stat().st_mode & 0o005) != 0o005:
            staged_repo = run_dir / "repo"
            shutil.copytree(
                repo_path, staged_repo,
                ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"),
            )
            os.chmod(staged_repo, 0o755)
            for sroot, sdirs, sfiles in os.walk(staged_repo):
                for d in sdirs:
                    os.chmod(os.path.join(sroot, d), 0o755)
                for f in sfiles:
                    os.chmod(os.path.join(sroot, f), 0o444)
            repo_path = staged_repo
            staged = True
        name = f"qswarm-sb-{env.env_key}-{uuid.uuid4().hex[:8]}"
        cmd = [
            "docker", "run", "--rm", "--name", name,
            "--network", "none",
            "--user", self._cfg.user,
            "--memory", f"{self._cfg.mem_mb}m",
            "--cpus", str(self._cfg.cpus),
            "--ulimit", f"nofile={self._cfg.nofile}:{self._cfg.nofile}",
            "--ulimit", f"nproc={self._cfg.nproc}:{self._cfg.nproc}",
            "-v", f"{repo_path}:/repo:ro",
            "-v", f"{poc_path}:/poc.py:ro",
            "-w", "/work",
            "-e", "REPO=/repo", "-e", "WORK=/work",
        ]
        if self._cfg.read_only_root:
            cmd += ["--read-only", "--tmpfs", f"/work:rw,size={self._cfg.tmpfs_mb}m"]
        cmd += [env.env_base, "python3", "/poc.py"]
        t1 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=float(self._cfg.timeout_s),
            )
            rc, out, err = proc.returncode, proc.stdout or "", proc.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            rc = None
            out = (exc.stdout or "")
            out = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
            err = (exc.stderr or "")
            err = err.decode("utf-8", "replace") if isinstance(err, bytes) else (err or "")
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name], capture_output=True, text=True
            )
        duration = time.monotonic() - t1
        error_kind = "timeout" if timed_out else None
        log_text = f"$ {' '.join(cmd[:2])} …\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        shutil.rmtree(run_dir, ignore_errors=True)
        return SandboxResult(
            tier=self.tier,
            ok=(rc == 0 and not timed_out),
            exit_code=rc,
            timed_out=timed_out,
            duration_s=duration,
            stdout=_cap(out),
            stderr=_cap(err),
            env_key=env.env_key,
            env_base=env.env_base,
            workdir=None,
            mitigations={
                "network": "docker-network-none",
                "user": self._cfg.user,
                "rootfs": "staged-0755-copy" if staged else (
                    "read-only+tmpfs" if self._cfg.read_only_root else "rw"
                ),
                "docker_sock_mounted": False,
            },
            error_kind=error_kind,
            log_text=log_text,
        )

    def _fail_env(self, exc: EnvBuildError, t0: float) -> SandboxResult:
        return SandboxResult(
            tier=self.tier, ok=False, exit_code=None, timed_out=False,
            duration_s=time.monotonic() - t0, stdout="", stderr=str(exc),
            env_key="", env_base=self._cfg.base_image, workdir=None,
            mitigations={"env": "build-failed"}, error_kind="env_build_fail",
            log_text=str(exc),
        )


class ProcessRunner:
    """process 档（D38-c 开发/CI 档；近似红线如实记录，D43-a 零账本写）。"""

    tier = "process"

    def __init__(self, config: SandboxConfig) -> None:
        if config.tier != "process":
            raise ValueError("ProcessRunner 需要 tier=process 配置")
        self._cfg = config
        self.env_build_s: float = 0.0
        self._net_ok: bool | None = None
        self._drop_user: tuple[int, int] | None | False = None   # None=未探测

    # -- 近似红线探针（一次性）────────────────────────────────────

    def _probe_net(self) -> bool:
        if self._net_ok is None:
            self._net_ok = False
            if shutil.which("unshare") is not None:
                try:
                    proc = subprocess.run(
                        ["unshare", "--net", "true"],
                        capture_output=True, timeout=10,
                    )
                    self._net_ok = proc.returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    self._net_ok = False
        return self._net_ok

    def _probe_user(self) -> tuple[int, int] | None:
        if self._drop_user is None:
            self._drop_user = False
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                try:
                    import pwd

                    pw = pwd.getpwnam(self._cfg.user)
                    self._drop_user = (pw.pw_gid, pw.pw_uid)
                except (KeyError, OSError):
                    self._drop_user = False
        return self._drop_user or None

    # -- 执行 ─────────────────────────────────────────────────────

    def execute(
        self, poc_script: str, *, repo: Path, deps: list[str], seed: str
    ) -> SandboxResult:
        t0 = time.monotonic()
        try:
            env = build_process_env(
                deps, cache_dir=Path(self._cfg.env_cache_dir),
                timeout_s=self._cfg.timeout_s,
            )
        except EnvBuildError as exc:
            return self._fail_env(exc, t0)
        self.env_build_s += time.monotonic() - t0
        workdir = Path(tempfile.mkdtemp(prefix="qswarm-sb-"))
        repo_copy = workdir / "repo"
        shutil.copytree(
            repo, repo_copy,
            ignore=shutil.ignore_patterns(".git", "__pycache__", ".venv"),
        )
        poc_path = workdir / "poc.py"
        poc_path.write_text(poc_script, encoding="utf-8")
        run_env = dict(os.environ)
        run_env["REPO"] = str(repo_copy)
        run_env["WORK"] = str(workdir)
        if env.sitedeps is not None and env.sitedeps.exists():
            run_env["PYTHONPATH"] = (
                str(env.sitedeps) + os.pathsep + run_env.get("PYTHONPATH", "")
            ).rstrip(os.pathsep)
        cmd = [sys.executable, str(poc_path)]
        # 禁网在 preexec 内以当前特权先 unshare(2) 再降权——wrapper 外挂 unshare
        # 会在降权后执行而 EPERM（2026-10-21 实测）；探测失败则如实降级记录。
        net_isolated = self._probe_net()
        drop = self._probe_user()
        if drop is not None:
            for p in (workdir, repo_copy):
                try:
                    os.chmod(p, 0o755)
                    for root, dirs, files in os.walk(p):
                        for d in dirs:
                            os.chmod(os.path.join(root, d), 0o755)
                        for f in files:
                            os.chmod(os.path.join(root, f), 0o444)
                except OSError:
                    drop = None
                    break
        status_r, status_w = os.pipe()
        preexec = self._make_preexec(drop, status_w)
        t1 = time.monotonic()
        timed_out = False
        rc = None
        out = err = ""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=str(workdir), env=run_env, preexec_fn=preexec,
            )
            os.close(status_w)
            try:
                out_b, err_b = proc.communicate(timeout=float(self._cfg.timeout_s))
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                out_b, err_b = proc.communicate()
            out = (out_b or b"").decode("utf-8", "replace")
            err = (err_b or b"").decode("utf-8", "replace")
            net_flag = os.read(status_r, 1)
            os.close(status_r)
            if net_isolated:
                net_isolated = net_flag == b"1"
        except OSError:
            try:
                os.close(status_r)
                os.close(status_w)
            except OSError:
                pass
            raise
        duration = time.monotonic() - t1
        log_text = f"$ {' '.join(cmd)}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        result_workdir = str(workdir)
        if not drop:
            shutil.rmtree(workdir, ignore_errors=True)
            result_workdir = None                    # root 直执：现场随清（降级面）
        return SandboxResult(
            tier=self.tier,
            ok=(rc == 0 and not timed_out),
            exit_code=rc,
            timed_out=timed_out,
            duration_s=duration,
            stdout=_cap(out),
            stderr=_cap(err),
            env_key=env.env_key,
            env_base=env.env_base,
            workdir=result_workdir,
            mitigations={
                "network": "unshare-net" if net_isolated else "not-isolated",
                "user": self._cfg.user if drop else "caller",
                "rootfs": "repo-snapshot-copy" if self._cfg.read_only_root else "in-place",
                "rlimits": f"nofile={self._cfg.nofile},nproc={self._cfg.nproc},"
                           f"as={self._cfg.mem_mb}m",
            },
            error_kind="timeout" if timed_out else None,
            log_text=log_text,
        )

    def _make_preexec(self, drop, status_w: int):
        cfg = self._cfg

        def _preexec() -> None:              # noqa: ANN001 — 子进程钩子
            # ① 禁网（当前特权下 unshare(2)；降权后再做必 EPERM）
            net_ok = b"0"
            try:
                import ctypes

                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                if libc.unshare(0x40000000) == 0:    # CLONE_NEWNET
                    net_ok = b"1"
            except Exception:                # noqa: BLE001 — 隔离尽力而为面
                pass
            try:
                os.write(status_w, net_ok)
            except OSError:
                pass
            # ② rlimits
            limit = cfg.mem_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (cfg.nofile, cfg.nofile))
                resource.setrlimit(resource.RLIMIT_NPROC, (cfg.nproc, cfg.nproc))
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
                resource.setrlimit(
                    resource.RLIMIT_CPU, (cfg.timeout_s, cfg.timeout_s)
                )
            except (ValueError, OSError):
                pass
            # ③ 降权（root 时）
            if drop is not None:
                try:
                    os.setgid(drop[0])
                    os.setuid(drop[1])
                    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                except OSError:
                    pass

        return _preexec

    def _fail_env(self, exc: EnvBuildError, t0: float) -> SandboxResult:
        return SandboxResult(
            tier=self.tier, ok=False, exit_code=None, timed_out=False,
            duration_s=time.monotonic() - t0, stdout="", stderr=str(exc),
            env_key="", env_base="process", workdir=None,
            mitigations={"env": "build-failed"}, error_kind="env_build_fail",
            log_text=str(exc),
        )


class FakeRunner:
    """脚本化沙箱（离线判据；SPE-5 M5 卷 A 组）——按脚本序返回结果。

    ``script``：SandboxResult 或 Callable(poc_script, repo, deps, seed) →
    SandboxResult；``calls`` 记录全部入参供断言（真实子进程零启动）。"""

    tier = "fake"

    def __init__(self, script=None, *, env_build_s: float = 0.0) -> None:
        self._script = script
        self.env_build_s = env_build_s
        self.calls: list[dict] = []

    def execute(
        self, poc_script: str, *, repo: Path, deps: list[str], seed: str
    ) -> SandboxResult:
        self.calls.append(
            {"poc": poc_script, "repo": str(repo), "deps": list(deps), "seed": seed}
        )
        if callable(self._script):
            out = self._script(poc=poc_script, repo=repo, deps=list(deps), seed=seed)
        else:
            out = self._script
        if isinstance(out, SandboxResult):
            return out
        return SandboxResult(
            tier="fake", ok=bool(out), exit_code=0 if out else 1,
            timed_out=False, duration_s=0.0,
            stdout=f"ASSERT|fake|{'PASS' if out else 'FAIL'}|scripted\n",
            stderr="", env_key="fake", env_base="fake", workdir=None,
            mitigations={"fake": True},
            log_text=f"ASSERT|fake|{'PASS' if out else 'FAIL'}|scripted",
        )


def make_runner(config: SandboxConfig) -> ContainerRunner | ProcessRunner:
    """配置 → 档位 runner（D38 双档并存；未知档 ValueError）。"""
    if config.tier == "container":
        return ContainerRunner(config)
    if config.tier == "process":
        return ProcessRunner(config)
    raise ValueError(f"未知沙箱档：{config.tier!r}")
