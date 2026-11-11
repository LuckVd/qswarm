"""夹具目标①：路径穿越漏洞版——直接 join 用户输入、不规范化不检查根边界。

「../secret/canary.txt」读到允许根外金丝雀并把内容（nonce）打到 stdout
（nonce 在文件内容里——回显路径/参数不可能命中，观察器判读面）。"""

import os
import sys


def main() -> None:
    serve_root = os.environ["SERVE_ROOT"]
    user_path = sys.argv[1] if len(sys.argv) > 1 else ""
    with open(os.path.join(serve_root, user_path), encoding="utf-8") as fh:
        print(fh.read())


if __name__ == "__main__":
    main()
