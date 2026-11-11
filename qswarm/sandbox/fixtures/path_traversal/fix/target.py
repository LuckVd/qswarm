"""夹具目标②：路径穿越修复版——realpath 规范化 + 允许根前缀校验，越界拒绝。"""

import os
import sys


def main() -> None:
    serve_root = os.path.realpath(os.environ["SERVE_ROOT"])
    user_path = sys.argv[1] if len(sys.argv) > 1 else ""
    full = os.path.realpath(os.path.join(serve_root, user_path))
    if full != serve_root and not full.startswith(serve_root + os.sep):
        print("rejected: outside serve root")
        raise SystemExit(2)
    with open(full, encoding="utf-8") as fh:
        print(fh.read())


if __name__ == "__main__":
    main()
