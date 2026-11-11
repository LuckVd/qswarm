"""夹具目标③：仅回显安全版——只回显请求路径串、从不打开文件。

nonce 在文件内容里，回显路径不可能命中——机械区分「打印路径」与「读到内容」
（旧 echo-marker 仪器缺陷的击穿件，第 ③ 类）。"""

import sys


def main() -> None:
    print("requested path:", sys.argv[1])


if __name__ == "__main__":
    main()
