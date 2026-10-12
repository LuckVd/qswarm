#!/usr/bin/env python3
"""一键写口审计（SPEC-2 §6.1/§7、SPEC-5 §2.1 判据 2；终检复用入口）。

实现本体在 ``qswarm/kernel/write_guard.py``（与 tests 写口纪律测试共用单一实现，
本脚本只是薄封装）。对 qswarm 包全量扫描写口词表（SPEC-2 §6.1 内核 11 表 +
M2 考试域 4 表 = 15 表，见 write_guard.WRITE_TABLES）的写语句：

- 圣化区域（内核 11 表 ``kernel/`` · 考试域 4 表 ``exams/``）外命中即 VIOLATION
  （对齐 SPEC-5 §2.1 判据 2 的独立 grep：空输出=通过）；
- 圣化区域内命中列为写口清单，``--strict`` 时与「表 → 唯一写口模块」比对，
  写语句落在非圣化模块即 DRIFT 失败。

用法（仓库根执行）：

    python3 scripts/audit_write_ports.py             # 一键审计，PASS/FAIL + 清单
    python3 scripts/audit_write_ports.py --strict    # 另查圣化写口模块漂移
    python3 scripts/audit_write_ports.py --json      # 机器可读（取证/CI 消费用）

退出码：0=通过 / 1=存在 kernel/ 外违规 / 2=仅 --strict 清单漂移。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from qswarm.kernel.write_guard import main

if __name__ == "__main__":
    raise SystemExit(main())
