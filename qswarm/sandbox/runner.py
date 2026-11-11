"""sandbox.runner：SandboxRunner 双档实现（SPEC-9 §2.1/§2.2；D38/D43；R1-T1 收紧）。

- **ContainerRunner**（container 档）：docker CLI 单容器执行——``--network none``
  （禁网）、``--user``（非特权）、``--read-only`` + ``--tmpfs /work``（只读根 +
  可写点）、``--memory/--cpus/--ulimit``（资源限额）、子进程墙钟超时 + 容器收割
  （超时 → error_kind=timeout）；repo 只读挂载 ``/repo:ro``，docker.sock 零挂载；
  docker 不可达 → EnvBuildError（判 env_build_fail，不误判 poc_fail）。
- **ProcessRunner**（process 档）：subprocess + rlimits（NOFILE/NPROC/AS/CPU）+
  仓快照副本（原件零触碰）+ unshare 禁网（可用性探测，缺则如实降级记录）+
  setuid 非特权（严格模式实际设置失败即拒绝，弱隔离仅显式 dev opt-out）。**近似红线**：只产工件与报告、
  不写生产账本（D43-a，写口门在 kernel.verify）。

R1-T1 执行边界（HANDOVER §5.2；docs/R1_CHANGE_NOTE.md §1-T1）：

- **process 档可信门**：repo 必须落在宿主侧可信根内（打包 fixtures + 配置
  ``trusted_roots``）——不可信目标仓库 = 不可信代码，即便 PoC 出自冻结模板；
  越界/符号链接逃逸 → :class:`UntrustedExecutionError`，**先于 pip/copytree/
  Popen**。禁网探针失败且 ``process_require_isolation``（缺省）→ 拒绝执行
  （fail-closed；弱隔离 opt-out = dev-only 显式配置）。
- **子进程环境最小 allowlist**：PATH/locale/TZ/TMPDIR + 注入的 REPO/WORK/
  PYTHONPATH（仅指向预置 sitedeps 的宿主控制值）——API keys/云凭据/
  PYTHONSTARTUP 等一律不继承。
- **输出限额读取时生效**：每流至多 OUTPUT_CAP_BYTES+1 字节即停读并收割
  **进程组**（``start_new_session`` + ``killpg``）；超限 → 有界诊断 + 不算成功
  （ok=False，mitigations.output_capped 如实记录）。
- container 档镜像/构建面收紧在 envcache（拒绝联网拉取与 build，缺省）。

四分类落点（§6.3）：env 构建失败 → env_build_fail（EnvBuildError 捕获面）；
墙钟超时 → timeout；poc_fail vs flaky 由编排层按同 PoC 重复执行结果集判定
（:func:`classify_result` 只承载 runner 侧两类）。

威胁模型边界（如实）：同内核容器共享内核面仍有残余风险——container 档是
「分级隔离」而非绝对安全保证；process 档为开发/CI 近似档（trusted-fixture
instrumentation 语义，oracle.py §6.5 同口径）。
"""

from __future__ import annotations

import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .config import SandboxConfig
from .snapshot import SnapshotError, safe_snapshot
from .envcache import (
    EnvBuildError,
    EnvHandle,
    build_container_env,
    build_process_env,
    validate_deps_spec,
)

__all__ = [
    "OUTPUT_CAP_BYTES",
    "ContainerRunner",
    "FakeRunner",
    "ProcessRunner",
    "SandboxResult",
    "UntrustedExecutionError",
    "classify_result",
    "make_runner",
]

#: stdout/stderr 捕获上限（R1-T1：**读取时**生效——超限即停读并收割进程组，
#: 防失控输出撑爆宿主内存；截断如实记录）。
OUTPUT_CAP_BYTES = 1_000_000

#: docker 探针命令（可用性一次性判定）。
_DOCKER_PROBE = ("docker", "info", "--format", "ok")

#: 子进程环境最小 allowlist（R1-T1：不继承 os.environ——API keys/云凭据/
#: PYTHONPATH/PYTHONSTARTUP 等结构性不透传；REPO/WORK/PYTHONPATH 由 runner
#: 以宿主控制值显式注入）。
_ENV_ALLOWLIST = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR")

#: 清理回收自身的墙钟上界（秒）——清理卡死不拖住编排（如实泄漏记录）。
_CLEANUP_TIMEOUT_S = 10.0


class UntrustedExecutionError(ValueError):
    """不可信输入进入宽松宿主执行路径（R1-T1：repo 越可信根 / deps 违规 /
    隔离缺失）——先于 pip/copytree/Popen 抛出（fail-closed）。"""


def _cap(text: str) -> str:
    raw = text.encode("utf-8", "replace")
    if len(raw) <= OUTPUT_CAP_BYTES:
        return text
    return raw[:OUTPUT_CAP_BYTES].decode("utf-8", "ignore") + "\n…(truncated)"


def _kill_descendants(proc: subprocess.Popen) -> None:
    """收割整个进程组（R1-T1：超时/超限时后代不存活；组失败退回单杀）。"""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (AttributeError, PermissionError, ProcessLookupError, OSError):
        try:
            proc.kill()
        except OSError:
            pass


def _rmtree_bounded(path: Path) -> bool:
    """带墙钟的目录回收（清理自身不拖编排；失败/超时 → False，如实泄漏）。"""
    done = threading.Event()

    def _clean() -> None:
        shutil.rmtree(path, ignore_errors=True)
        done.set()

    threading.Thread(target=_clean, daemon=True).start()
    if not done.wait(_CLEANUP_TIMEOUT_S):
        return False
    return not path.exists()


def _run_capped(
    cmd: list[str],
    *,
    timeout_s: float,
    cwd: str | None = None,
    env: dict | None = None,
    preexec_fn=None,
    cap_bytes: int = OUTPUT_CAP_BYTES,
) -> tuple[int | None, str, str, bool, bool]:
    """ capped 执行（R1-T1 核心）：返回 (rc, stdout, stderr, timed_out, capped)。

    - 读侧限额：每流至多 cap_bytes+1 字节即停读；超限 → 收割进程组、
      rc 照收（通常 -9）、capped=True（调用方不算成功）；
    - 墙钟超时：killpg 收割后代，timed_out=True；
    - ``start_new_session=True`` 使子进程自立进程组（killpg 可达后代；
      preexec_fn 与 start_new_session 不能同用——preexec 路径由调用方
      在 preexec 内自建会话）。"""
    if preexec_fn is not None:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=env, preexec_fn=preexec_fn,
        )
    else:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=env, start_new_session=True,
        )
    out_buf = bytearray()
    err_buf = bytearray()
    capped_evt = threading.Event()

    def _read(stream, buf: bytearray) -> None:
        try:
            while len(buf) <= cap_bytes:
                chunk = stream.read(min(65536, cap_bytes + 1 - len(buf)))
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > cap_bytes:
                    capped_evt.set()
                    _kill_descendants(proc)
                    break
        except (OSError, ValueError):
            pass

    t_out = threading.Thread(target=_read, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_read, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()
    timed_out = False
    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_descendants(proc)
        try:
            rc = proc.wait(timeout=_CLEANUP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            rc = None
    t_out.join(_CLEANUP_TIMEOUT_S)
    t_err.join(_CLEANUP_TIMEOUT_S)
    capped = capped_evt.is_set() or len(out_buf) > cap_bytes or len(err_buf) > cap_bytes
    return (
        rc,
        bytes(out_buf[:cap_bytes]).decode("utf-8", "replace"),
        bytes(err_buf[:cap_bytes]).decode("utf-8", "replace"),
        timed_out,
        capped,
    )


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
                allow_build=self._cfg.container_allow_build,
            )
        except EnvBuildError as exc:
            return self._fail_env(exc, t0)
        self.env_build_s += time.monotonic() - t0
        run_dir = Path(tempfile.mkdtemp(prefix="qswarm-sb-"))
        name = None
        cleanup_error = ""
        snapshot_failure = None
        directory_cleaned = False
        try:
            poc_path = run_dir / "poc.py"
            poc_path.write_text(poc_script, encoding="utf-8")
            # 每轮始终冻结独立副本，绝不把 live source bind 进容器。
            repo_path = run_dir / "repo"
            snapshot = safe_snapshot(Path(repo).resolve(), repo_path)
            name = f"qswarm-sb-{env.env_key}-{uuid.uuid4().hex[:8]}"
            cmd = [
                # 不用 --rm：由 finally 中有界 rm -f 唯一负责收割、校验结果。
                "docker", "run", "--name", name,
                "--network", "none", "--user", self._cfg.user,
                "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                "--pids-limit", str(self._cfg.nproc),
                "--memory", f"{self._cfg.mem_mb}m", "--cpus", str(self._cfg.cpus),
                "--ulimit", f"nofile={self._cfg.nofile}:{self._cfg.nofile}",
                "--ulimit", f"nproc={self._cfg.nproc}:{self._cfg.nproc}",
                "-v", f"{repo_path}:/repo:ro", "-v", f"{poc_path}:/poc.py:ro",
                "-w", "/work", "-e", "REPO=/repo", "-e", "WORK=/work",
                "--read-only", "--tmpfs",
                f"/work:rw,nosuid,size={self._cfg.tmpfs_mb}m,mode=1777",
                env.env_base, "python3", "/poc.py",
            ]
            t1 = time.monotonic()
            rc, out, err, timed_out, capped = _run_capped(
                cmd, timeout_s=float(self._cfg.timeout_s)
            )
        except SnapshotError as exc:
            snapshot_failure = exc
        finally:
            try:
                if name is not None:
                    try:
                        cleanup = subprocess.run(
                            ["docker", "rm", "-f", name], capture_output=True,
                            text=True, timeout=_CLEANUP_TIMEOUT_S,
                        )
                        if cleanup.returncode != 0:
                            cleanup_error = f"docker cleanup failed (rc={cleanup.returncode})"
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        cleanup_error = f"docker cleanup failed: {type(exc).__name__}"
            finally:
                directory_cleaned = _rmtree_bounded(run_dir)
        if snapshot_failure is not None:
            result = self._fail_env(EnvBuildError(str(snapshot_failure)), t0)
            if not directory_cleaned:
                result.workdir = str(run_dir)
                result.mitigations["cleanup_failed"] = str(run_dir)
            return result
        duration = time.monotonic() - t1
        if not directory_cleaned:
            cleanup_error += f" temporary directory cleanup failed: {run_dir}"
        if cleanup_error:
            err = f"{err}\n{cleanup_error}".strip()
        error_kind = "env_build_fail" if cleanup_error else ("timeout" if timed_out else None)
        log_text = f"$ docker run …\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        mitigations = {
            "network": "docker-network-none", "user": self._cfg.user,
            "rootfs": "read-only+tmpfs", "repo": "safe-frozen-snapshot",
            "snapshot_digest": snapshot.digest, "snapshot_files": snapshot.file_count,
            "snapshot_bytes": snapshot.total_bytes,
            "docker_sock_mounted": False, "cap_drop": "ALL",
            "no_new_privileges": True, "pids_limit": self._cfg.nproc,
        }
        if cleanup_error:
            mitigations["cleanup_failed"] = cleanup_error
        if capped:
            mitigations["output_capped"] = True
        return SandboxResult(
            tier=self.tier, ok=(rc == 0 and not timed_out and not capped and not cleanup_error),
            exit_code=rc, timed_out=timed_out, duration_s=duration,
            stdout=_cap(out), stderr=_cap(err), env_key=env.env_key, env_base=env.env_base,
            workdir=None if directory_cleaned else str(run_dir), mitigations=mitigations,
            error_kind=error_kind, log_text=log_text,
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
    """process 档（D38-c 开发/CI 档；近似红线如实记录，D43-a 零账本写）。

    R1-T1：**可信开发夹具档**——repo 必须落在宿主侧可信根内（打包 fixtures +
    配置 ``trusted_roots``），deps 过拒绝矩阵且缺省拒绝运行时安装，子进程环境
    走最小 allowlist，输出限额读取时生效，禁网探针失败即拒（fail-closed）。
    """

    tier = "process"

    def __init__(self, config: SandboxConfig) -> None:
        if config.tier != "process":
            raise ValueError("ProcessRunner 需要 tier=process 配置")
        self._cfg = config
        self.env_build_s: float = 0.0
        self._net_ok: bool | None = None
        self._drop_user: tuple[int, int] | None | False = None   # None=未探测
        # 可信根 = 配置显式根 + 打包 fixtures（宿主侧已核验的开发输入面）。
        from .fixtures import FIXTURES_ROOT

        self._trusted_roots = tuple(
            Path(p).resolve() for p in (*config.trusted_roots, str(FIXTURES_ROOT))
        )

    # -- 可信门（R1-T1：先于一切副作用）────────────────────────────

    def _assert_trusted_repo(self, repo: Path) -> Path:
        """repo 必须落在可信根内（realpath 防符号链接逃逸）——越界即拒。"""
        resolved = Path(repo).resolve()
        for root in self._trusted_roots:
            if resolved == root or root in resolved.parents:
                return resolved
        raise UntrustedExecutionError(
            f"process 档拒绝执行不可信目标仓库 {str(repo)!r}：不在宿主侧可信根内"
            f"（{[str(r) for r in self._trusted_roots]}）——不可信仓库 = 不可信"
            "代码，即便 PoC 出自冻结模板（R1-T1；dev 输入经 trusted_roots 显式"
            "核验后放行）"
        )

    def _assert_isolation_available(self, net_isolated: bool) -> None:
        """禁网探针失败 + process_require_isolation（缺省）→ 拒绝执行。"""
        if not net_isolated and self._cfg.process_require_isolation:
            raise UntrustedExecutionError(
                "process 档禁网隔离不可用（unshare 探针失败）——R1-T1 fail-closed"
                "拒绝执行；弱隔离 dev-only 模式须经 process_require_isolation="
                "false 显式配置，不构成生产降级入口"
            )

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
        # ① 可信门：repo 越界 / deps 违规 / 隔离缺失 → 拒绝（先于 pip/copytree/
        #    Popen；UntrustedExecutionError ⊂ ValueError，编排层不可吞作执行结果）
        repo_resolved = self._assert_trusted_repo(repo)
        validate_deps_spec(list(deps))
        net_isolated = self._probe_net()
        self._assert_isolation_available(net_isolated)
        drop = self._probe_user()
        if (self._cfg.process_require_isolation and os.geteuid() == 0
                and (drop is None or drop[1] == 0)):
            raise UntrustedExecutionError("process 档无法降权到非 root 用户，拒绝执行")
        try:
            env = build_process_env(
                deps, cache_dir=Path(self._cfg.env_cache_dir),
                timeout_s=self._cfg.timeout_s,
                allow_install=self._cfg.process_allow_install,
            )
        except EnvBuildError as exc:
            return self._fail_env(exc, t0)
        self.env_build_s += time.monotonic() - t0
        workdir = Path(tempfile.mkdtemp(prefix="qswarm-sb-"))
        result = None
        try:
            result = self._execute_in_workdir(
                poc_script, repo_resolved=repo_resolved, env=env, workdir=workdir,
                drop=drop, net_isolated=net_isolated, t0=t0,
            )
            return result
        except SnapshotError as exc:
            result = self._fail_env(EnvBuildError(str(exc)), t0)
            return result
        finally:
            cleaned = _rmtree_bounded(workdir)
            if result is not None:
                result.workdir = None if cleaned else str(workdir)
                if not cleaned:
                    result.ok = False
                    result.error_kind = "env_build_fail"
                    result.mitigations["cleanup_failed"] = str(workdir)
                    result.stderr = _cap(result.stderr + "\ntemporary directory cleanup failed")
                    result.log_text += "\ntemporary directory cleanup failed"

    def _execute_in_workdir(
        self, poc_script: str, *, repo_resolved: Path, env: EnvHandle,
        workdir: Path, drop, net_isolated: bool, t0: float,
    ) -> SandboxResult:
        """执行细节；所有返回/异常路径均由 execute 的 finally 回收临时目录。"""
        repo_copy = workdir / "repo"
        snapshot = safe_snapshot(repo_resolved, repo_copy)
        poc_path = workdir / "poc.py"
        poc_path.write_text(poc_script, encoding="utf-8")
        # ② 子进程环境最小 allowlist（R1-T1：不继承 os.environ）
        run_env = {
            k: os.environ[k]
            for k in _ENV_ALLOWLIST
            if k in os.environ and os.environ[k]
        }
        run_env["REPO"] = str(repo_copy)
        run_env["WORK"] = str(workdir)
        if env.sitedeps is not None and env.sitedeps.exists():
            run_env["PYTHONPATH"] = str(env.sitedeps)   # 宿主控制值（预置 env）
        cmd = [sys.executable, str(poc_path)]
        # 禁网在 preexec 内以当前特权先 unshare(2) 再降权——wrapper 外挂 unshare
        # 会在降权后执行而 EPERM（2026-10-21 实测）。
        if drop is not None:
            for p in (workdir, repo_copy):
                try:
                    os.chmod(p, 0o755)
                    for root, dirs, files in os.walk(p):
                        for d in dirs:
                            os.chmod(os.path.join(root, d), 0o755)
                        for f in files:
                            os.chmod(os.path.join(root, f), 0o444)
                except OSError as exc:
                    if self._cfg.process_require_isolation:
                        raise UntrustedExecutionError("process 降权前权限准备失败") from exc
                    drop = None
                    break
        status_r, status_w = os.pipe()
        preexec = self._make_preexec(drop, status_w)
        t1 = time.monotonic()
        try:
            try:
                rc, out, err, timed_out, capped = _run_capped(
                    cmd, timeout_s=float(self._cfg.timeout_s),
                    cwd=str(workdir), env=run_env, preexec_fn=preexec,
                )
            finally:
                os.close(status_w)
            net_flag = os.read(status_r, 1)
        except (OSError, subprocess.SubprocessError) as exc:
            return self._fail_env(EnvBuildError(f"process isolation setup failed: {exc}"), t0)
        finally:
            os.close(status_r)
        net_isolated = net_flag == b"1"
        if self._cfg.process_require_isolation and not net_isolated:
            return self._fail_env(EnvBuildError("process isolation not confirmed"), t0)
        duration = time.monotonic() - t1
        log_text = f"$ {' '.join(cmd)}\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        result_workdir = str(workdir)  # execute.finally 统一回收并回填结果。
        mitigations = {
            "network": "unshare-net" if net_isolated else "not-isolated",
            "user": self._cfg.user if drop else "caller",
            "rootfs": "repo-snapshot-copy" if self._cfg.read_only_root else "in-place",
            "rlimits": f"nofile={self._cfg.nofile},nproc={self._cfg.nproc},"
                       f"as={self._cfg.mem_mb}m",
            "env_allowlist": True, "repo": "safe-frozen-snapshot",
            "snapshot_digest": snapshot.digest, "snapshot_files": snapshot.file_count,
            "snapshot_bytes": snapshot.total_bytes,
        }
        if capped:
            mitigations["output_capped"] = True
        return SandboxResult(
            tier=self.tier,
            ok=(rc == 0 and not timed_out and not capped),
            exit_code=rc,
            timed_out=timed_out,
            duration_s=duration,
            stdout=_cap(out),
            stderr=_cap(err),
            env_key=env.env_key,
            env_base=env.env_base,
            workdir=result_workdir,
            mitigations=mitigations,
            error_kind="timeout" if timed_out else None,
            log_text=log_text,
        )

    def _make_preexec(self, drop, status_w: int):
        cfg = self._cfg

        def _preexec() -> None:              # noqa: ANN001 — 子进程钩子
            # ⑴ 自立进程组（R1-T1：宿主 killpg 可收割后代；preexec 与
            #    start_new_session 互斥，故在此显式 setsid）
            os.setsid()  # 无独立会话时不能安全收割进程组；任何档位均拒绝继续。
            # ⑵ 禁网（当前特权下 unshare(2)；降权后再做必 EPERM）
            net_ok = b"0"
            try:
                import ctypes

                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                if libc.unshare(0x40000000) == 0:    # CLONE_NEWNET
                    net_ok = b"1"
            except Exception:
                if cfg.process_require_isolation:
                    raise
            if cfg.process_require_isolation and net_ok != b"1":
                raise UntrustedExecutionError("actual process network isolation failed")
            # ⑶ rlimits
            limit = cfg.mem_mb * 1024 * 1024
            try:
                resource.setrlimit(resource.RLIMIT_NOFILE, (cfg.nofile, cfg.nofile))
                resource.setrlimit(resource.RLIMIT_NPROC, (cfg.nproc, cfg.nproc))
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
                resource.setrlimit(
                    resource.RLIMIT_CPU, (cfg.timeout_s, cfg.timeout_s)
                )
            except (ValueError, OSError):
                if cfg.process_require_isolation:
                    raise
            # ⑷ 降权（root 时）；补充组必须清空，避免保留宿主组权限。
            if drop is not None:
                try:
                    os.setgroups([])
                    os.setgid(drop[0])
                    os.setuid(drop[1])
                    signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                except OSError:
                    if cfg.process_require_isolation:
                        raise
            # 所有要求的设置成功后才确认；异常由 Popen 错误管道传给父进程，
            # 不会 exec PoC，也不会将探针成功冒充实际隔离成功。
            os.write(status_w, net_ok)

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
