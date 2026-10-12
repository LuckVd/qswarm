# pytest 根配置在仓库根 conftest.py（把仓库根注入 sys.path 使 `import qswarm` 免安装）。
# 此处补 tests/ 目录自身，使测试模块可直接 `import golden_loader`
# （黄金夹具装载器，tests/golden_loader.py，与 scripts/freeze_golden.py 共用）。
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
