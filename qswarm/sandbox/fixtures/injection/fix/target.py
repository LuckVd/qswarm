"""夹具目标②：命令注入修复版——argv 列表形态（不经 shell，payload 字面量化）。"""
import subprocess
import sys

def main() -> None:
    args = sys.argv[1:] or [""]
    subprocess.run(["echo", "hello"] + args, check=False)   # 无 shell 拼接

if __name__ == "__main__":
    main()
