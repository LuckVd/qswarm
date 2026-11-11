"""夹具目标③：仅回显安全版——只打印参数、绝不执行（旧 echo-marker 仪器缺陷
的击穿件：回显满足不了副作用金丝雀）。"""
import sys

def main() -> None:
    print("requested:", sys.argv[1:])

if __name__ == "__main__":
    main()
