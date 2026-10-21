"""sandbox.config：沙箱红线配置（SPEC-9 §2；params ``sandbox`` 节的运行面形态）。

红线清单（§16.7-5 + R4 逐条，验收卷机器可检 = :meth:`SandboxConfig.redline_report`）：
默认禁网（network="none"；逐例白名单归后续里程碑，v1 词表只收 "none"）、非特权
用户、只读根 + tmpfs 可写点、无 docker.sock 挂载（container 档缺省即天然成立，
runner 不接受任何守护进程套接字挂载形态）、资源 rlimits（mem/cpus/nofile/nproc）、
墙钟超时（超时 → error_kind=timeout）。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SandboxConfig"]


@dataclass(frozen=True)
class SandboxConfig:
    """一次沙箱执行的红线配置（不可变；SandboxParams 的等价 dataclass 形）。

    ``tier``："container"（Docker 分级隔离档，S2 生产存款唯一口径，D43-a）|
    "process"（subprocess+rlimits 近似档，开发/CI 档，零账本写）。
    ``base_image`` 为 container 档 env 构建的 FROM 基底（v1 固定 python:3.11，
    逐例镜像面随 env_requirements 演进）；process 档 base = 当前解释器。"""

    tier: str = "process"
    timeout_s: int = 340
    mem_mb: int = 512
    cpus: float = 1.0
    nofile: int = 64
    nproc: int = 32
    tmpfs_mb: int = 64
    network: str = "none"
    read_only_root: bool = True
    user: str = "nobody"
    env_cache_dir: str = "out/sandbox-env"
    base_image: str = "python:3.11"

    def __post_init__(self) -> None:
        if self.tier not in ("container", "process"):
            raise ValueError(
                f"tier 必须是 container|process，实际 {self.tier!r}（D38 双档词表）"
            )
        if self.network != "none":
            raise ValueError(
                f"network 词表 v1 只收 'none'（默认禁网，§16.7-5），实际 {self.network!r}"
            )
        for name in ("timeout_s", "mem_mb", "nofile", "nproc", "tmpfs_mb"):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"SandboxConfig.{name} 必须 ≥1")
        if float(self.cpus) <= 0:
            raise ValueError("SandboxConfig.cpus 必须 >0")

    @classmethod
    def from_params(cls, params, *, tier: str | None = None) -> "SandboxConfig":
        """params.sandbox 节 → 配置（``tier`` 显式覆盖 params 值——run_verify/
        trial 的 D43 双口径入口各自钉档）。"""
        s = params.sandbox
        return cls(
            tier=str(tier if tier is not None else s.tier),
            timeout_s=int(s.timeout_s),
            mem_mb=int(s.mem_mb),
            cpus=float(s.cpus),
            nofile=int(s.nofile),
            nproc=int(s.nproc),
            tmpfs_mb=int(s.tmpfs_mb),
            network=str(s.network),
            read_only_root=bool(s.read_only_root),
            user=str(s.user),
            env_cache_dir=str(s.env_cache_dir),
        )

    def redline_report(self) -> dict:
        """红线逐条落档（验收卷机器可检；值即 runner 实际施加的形态）。"""
        return {
            "tier": self.tier,
            "network": self.network,
            "user": self.user,
            "read_only_root": bool(self.read_only_root),
            "tmpfs_mb": int(self.tmpfs_mb),
            "mem_mb": int(self.mem_mb),
            "cpus": float(self.cpus),
            "nofile": int(self.nofile),
            "nproc": int(self.nproc),
            "timeout_s": int(self.timeout_s),
            "docker_sock_mounted": False,
        }
