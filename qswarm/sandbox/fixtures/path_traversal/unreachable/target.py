"""夹具目标④：不可达 sink——读文件面在特性开关后（缺省 off）。

预期判读：无违规 → inconclusive（绝非 verified_fp——§6.1 路径不可达不足以
否定原漏洞主张）。"""

import os
import sys


def main() -> None:
    serve_root = os.environ["SERVE_ROOT"]
    user_path = sys.argv[1] if len(sys.argv) > 1 else ""
    if os.environ.get("QSWARM_FEATURE_FLAG") == "on":   # 缺省 off：sink 不可达
        with open(os.path.join(serve_root, user_path), encoding="utf-8") as fh:
            print(fh.read())
    print("processed:", user_path)


if __name__ == "__main__":
    main()
