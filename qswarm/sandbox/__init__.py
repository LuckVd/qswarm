"""qswarm.sandbox：S2 沙箱执行面（SPEC-9 §2/§3；M5，依赖序第 8 位，两大重件之二）。

双档 runner（D38 拍板「SandboxRunner tier 抽象双档并存」）：

- **container 档**（D38-a）：Docker 分级隔离（默认禁网、非特权用户、只读根 +
  tmpfs 可写点、资源限额、墙钟超时）——S2 生产存款唯一口径（D43-a）；
- **process 档**（D38-c 开发/CI 档）：subprocess + rlimits + 仓快照副本 +
  unshare 禁网（可用性探测，缺则如实降级记录）——只产工件与报告、**不写生产
  账本**（D43-a：写口拒绝由 kernel.verify 的 tier 门承担，本包零账本依赖）。

红线配置逐条见 :class:`sandbox.config.SandboxConfig`（params ``sandbox`` 节，
缺省=process 档规格逐字值）；verify_error 四分类（env_build_fail/poc_fail/
timeout/flaky，§6.3）落点 = :func:`sandbox.runner.classify_result` + 编排层
flaky 判据（同 PoC 重复执行结果集 >1 → flaky）。PoC 断言约定（harness 契约，
PatchEval Docker 验证管线参照）见 :mod:`sandbox.pocgen`。
"""

from .config import SandboxConfig
from .envcache import EnvBuildError, EnvHandle, env_key_for
from .pocgen import extract_poc, parse_assertions, template_poc
from .runner import (
    FakeRunner,
    ProcessRunner,
    SandboxResult,
    classify_result,
    make_runner,
)

__all__ = [
    "EnvBuildError",
    "EnvHandle",
    "FakeRunner",
    "ProcessRunner",
    "SandboxConfig",
    "SandboxResult",
    "classify_result",
    "env_key_for",
    "extract_poc",
    "make_runner",
    "parse_assertions",
    "template_poc",
]
