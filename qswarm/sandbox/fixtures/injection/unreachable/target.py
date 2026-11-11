"""夹具目标④：不可达 sink——漏洞代码在场但产品缺省关闭（特性开关 off）。
预期判读：无违规 → inconclusive（绝非 verified_fp——§6.1 不可达不足以否定）。"""
import os
import sys

def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if os.environ.get("QSWARM_FEATURE_FLAG") == "on":   # 缺省 off：sink 不可达
        os.system("echo hello " + arg)
    print("processed:", arg)

if __name__ == "__main__":
    main()
