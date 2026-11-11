"""夹具目标⑤：缺依赖——顶层 import 不存在的模块，目标自身无法启动。"""

import qswarm_missing_dep_xyz   # noqa: F401 — 故意缺失（第 ⑤ 类）


def main() -> None:
    print("never reached")


if __name__ == "__main__":
    main()
