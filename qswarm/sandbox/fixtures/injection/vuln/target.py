"""夹具目标①：命令注入漏洞版——shell 拼接 sink（os.system + 用户参数拼接）。

注入 payload「; echo <nonce> > <canary>」经 argv 进入拼接命令，由目标进程的
shell 子进程执行 → 金丝雀产生（违规副作用；观察器判读面）。"""
import os
import sys

def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    os.system("echo hello " + arg)          # sink：拼接进 shell

if __name__ == "__main__":
    main()
