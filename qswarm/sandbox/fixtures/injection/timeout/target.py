"""夹具目标⑥：超时——sink 前长睡眠（墙钟收割面；有界 30s 防孤儿残留）。"""

import time


def main() -> None:
    time.sleep(30)                            # runner 墙钟先收割
    print("never reached")


if __name__ == "__main__":
    main()
