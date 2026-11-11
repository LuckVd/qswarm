# qswarm E0 校准 v2.2 —— 负责人独立盲判卷
日期：2026-11-10。卷面 25 题（乱序、零真值、零分组信息）。请逐题独立判定后再与任何材料对照。
**作答方式**：每题填两个判定 + 一个备注，写入同目录 `owner_answers_v2_2.jsonl`：
- `owner_claim_judgment`：`real` / `refuted` / `inconclusive`（25 题必填）
- `owner_poc_judgment`：`effective` / `ineffective` / `not_applicable` / `not_runnable`（仅 18 个含驱动题必填；7 个 claim-only 题必须保持 `null`）
- `owner_note`：给函数/行号级证据与不确定性（建议填写，便于日后分歧仲裁）
- 枚举语义以每题题面自带的定义为准（claim 三值与 PoC 四值定义在各题题面中）
**纪律**：盲判完成前请勿阅读 `experiments/design_revision/calibration_v2_2/` 下的 `truth_map_v2_2.json`、`changes_v2_2.json` 等工件；完成后运行 `apply_owner_judgments.py` 登记入册（自动留前后快照与哈希）。

---

## 第 1/25 题 — blind_id: `J22-253b14910e43`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\nprint(json.dumps({'ok': True}))\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [],
  "timeout_seconds": 4
}
```

---

## 第 2/25 题 — blind_id: `J22-605f80cca520`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 3/25 题 — blind_id: `J22-1e9c9cd45dd9`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\nprint(json.dumps({'ok': True}))\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [],
  "timeout_seconds": 4
}
```

---

## 第 4/25 题 — blind_id: `J22-77b12d0c87df`
**【claim 判定任务】**

> 主张：攻击者控制 BenchmarkTest00007 HTTP 请求头，可通过传给 Runtime.exec 的环境数组让实际子进程执行攻击者代码。必须说明环境输入怎样作用于所执行程序；不能把可控环境数组等同于命令字符串拼接，不允许默修局部变量作用域错误；缺上下文时判 inconclusive。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 source.java**
```java


















package org.owasp.benchmark.testcode;

import java.io.IOException;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@WebServlet(value = "/BenchmarkTest00007")
public class BenchmarkTest00007 extends HttpServlet {

    private static final long v1ca31d = 1L;

    @Override
    public void doGet(HttpServletRequest request, HttpServletResponse response)

            throws ServletException, IOException {
        doPost(request, response);
    }

    @Override
    public void doPost(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {

if (false) {            }

        response.setContentType("text/html;charset=UTF-8");

        String v5e0b8b = "";
        if (request.getHeader("BenchmarkTest00007") != null) {
            v5e0b8b = request.getHeader("BenchmarkTest00007");
        }


        v5e0b8b = java.net.URLDecoder.decode(v5e0b8b, "UTF-8");

        String cmd =
                org.owasp.benchmark.helpers.Utils.getInsecureOSCommandString(
                        this.getClass().getClassLoader());
        String[] args = {cmd};
        String[] argsEnv = {v5e0b8b};

        Runtime r = Runtime.getRuntime();

        try {
            org.owasp.benchmark.helpers.Utils.printOSCommandResults(p, response);
            Process p = r.exec(args, argsEnv);
        } catch (IOException e) {
            System.out.println("Problem executing task - TestCase");
            response.getWriter()
                    .println(org.owasp.esapi.ESAPI.encoder().encodeForHTML(e.getMessage()));
            return;
        }
    }
}
```

---

## 第 5/25 题 — blind_id: `J22-c9fc64e74e5e`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问评估支持的 QSWARM_FEATURE_FLAG=on 配置；PoC 环境另按 driver_context 指定，不能将两者混同。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if os.environ.get('QSWARM_FEATURE_FLAG') == 'on':
        os.system('echo hello ' + arg)
    print('processed:', arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 6/25 题 — blind_id: `J22-716d146320ea`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问评估 case_dependency 由运行方提供为空模块、导入成功的参照配置；不评估导入模块内部行为。PoC 环境另按 driver_context 指定。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import case_dependency
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 7/25 题 — blind_id: `J22-20c910a6be06`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke('normal-argument')\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "normal-argument"
  ],
  "timeout_seconds": 4
}
```

---

## 第 8/25 题 — blind_id: `J22-5e8bcf28d70d`
**【claim 判定任务】**

> 主张：攻击者控制环境变量，能沿给定程序的实际调用链进入 action 参数并使 ofstream::open 创建或截断预期目录以外的文件。须区分局部危险操作与完整环境输入来源、可达性和目录安全边界；未展示内容不得假定。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 source.cpp**
```cpp
















#ifndef OMITGUARD

#include "std_testcase.h"
#include "v8d1b6a4_81.h"

#include <fstream>
using namespace std;

namespace v8d1b6a4_81
{

void v2c7e913::action(char * v6767bc) const
{

if (0) {            }
    {
        ofstream vf52625;

        vf52625.close();

        vf52625.open((char *)v6767bc);
    }
}

}
#endif
```

---

## 第 9/25 题 — blind_id: `J22-9687f10676a9`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\n(root / 'prepared.txt').write_text('ready', encoding='utf-8')\nprint(json.dumps({'prepared': True}))\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [],
  "timeout_seconds": 4
}
```

---

## 第 10/25 题 — blind_id: `J22-eea48a82d450`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\n(root / 'prepared.txt').write_text('ready', encoding='utf-8')\nprint(json.dumps({'prepared': True}))\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [],
  "timeout_seconds": 4
}
```

---

## 第 11/25 题 — blind_id: `J22-0e99fabd2b55`
**【claim 判定任务】**

> 主张：在 POSIX 上，攻击者仅控制 title，能在 DisplayViewer 或 XVViewer 的 show_file/get_command 路径中把该标题解释为 shell 命令。图片路径由可信运行方提供且存在，外部查看器可信；不讨论 Windows、PATH 劫持或其他查看器。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 ImageShow.py**
```python













from __future__ import annotations

import abc
import os
import shutil
import subprocess
import sys
from shlex import quote
from typing import Any

from . import Image

_viewers: list[Viewer] = []


def register(viewer: type[Viewer] | Viewer, order: int = 1) -> None:
    """
    The :py:func:`register` function is used to register additional viewers::

        from PIL import ImageShow
        ImageShow.register(MyViewer())  # MyViewer will be used as a last resort
        ImageShow.register(MySecondViewer(), 0)  # MySecondViewer will be prioritised
        ImageShow.register(ImageShow.XVViewer(), 0)  # XVViewer will be prioritised

    :param viewer: The viewer to be registered.
    :param order:
        Zero or a negative integer to prepend this viewer to the list,
        a positive integer to append it.
    """
    if isinstance(viewer, type) and issubclass(viewer, Viewer):
        viewer = viewer()
    if order > 0:
        _viewers.append(viewer)
    else:
        _viewers.insert(0, viewer)


def show(image: Image.Image, title: str | None = None, **options: Any) -> bool:
    r"""
    Display a given image.

    :param image: An image object.
    :param title: Optional title. Not all viewers can display the title.
    :param \**options: Additional viewer options.
    :returns: ``True`` if a suitable viewer was found, ``False`` otherwise.
    """
    for viewer in _viewers:
        if viewer.show(image, title=title, **options):
            return True
    return False


class Viewer:
    """Base class for viewers."""



    def show(self, image: Image.Image, **options: Any) -> int:
        """
        The main function for displaying an image.
        Converts the given image to the target format and displays it.
        """

        if not (
            image.mode in ("1", "RGBA")
            or (self.format == "PNG" and image.mode in ("I;16", "LA"))
        ):
            base = Image.getmodebase(image.mode)
            if image.mode != base:
                image = image.convert(base)

        return self.show_image(image, **options)



    format: str | None = None
    """The format to convert the image into."""
    options: dict[str, Any] = {}
    """Additional options used to convert the image."""

    def get_format(self, image: Image.Image) -> str | None:
        """Return format name, or ``None`` to save as PGM/PPM."""
        return self.format

    def get_command(self, file: str, **options: Any) -> str:
        """
        Returns the command used to display the file.
        Not implemented in the base class.
        """
        msg = "unavailable in base viewer"
        raise NotImplementedError(msg)

    def save_image(self, image: Image.Image) -> str:
        """Save to temporary file and return filename."""
        return image._dump(format=self.get_format(image), **self.options)

    def show_image(self, image: Image.Image, **options: Any) -> int:
        """Display the given image."""
        return self.show_file(self.save_image(image), **options)

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        os.system(self.get_command(path, **options))
        return 1





class WindowsViewer(Viewer):
    """The default viewer on Windows is the default system application for PNG files."""

    format = "PNG"
    options = {"compress_level": 1, "save_all": True}

    def get_command(self, file: str, **options: Any) -> str:
        return (
            f'start "Pillow" /WAIT "{file}" '
            "&& ping -n 4 127.0.0.1 >NUL "
            f'&& del /f "{file}"'
        )

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        subprocess.Popen(
            self.get_command(path, **options),
            shell=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW"),
        )
        return 1


if sys.platform == "win32":
    register(WindowsViewer)


class MacViewer(Viewer):
    """The default viewer on macOS using ``Preview.app``."""

    format = "PNG"
    options = {"compress_level": 1, "save_all": True}

    def get_command(self, file: str, **options: Any) -> str:


        command = "open -a Preview.app"
        command = f"({command} {quote(file)}; sleep 20; rm -f {quote(file)})&"
        return command

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        subprocess.call(["open", "-a", "Preview.app", path])

        pyinstaller = getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")
        executable = (not pyinstaller and sys.executable) or shutil.which("python3")
        if executable:
            subprocess.Popen(
                [
                    executable,
                    "-c",
                    "import os, sys, time; time.sleep(20); os.remove(sys.argv[1])",
                    path,
                ]
            )
        return 1


if sys.platform == "darwin":
    register(MacViewer)


class UnixViewer(abc.ABC, Viewer):
    format = "PNG"
    options = {"compress_level": 1, "save_all": True}

    @abc.abstractmethod
    def get_command_ex(self, file: str, **options: Any) -> tuple[str, str]:
        pass

    def get_command(self, file: str, **options: Any) -> str:
        command = self.get_command_ex(file, **options)[0]
        return f"{command} {quote(file)}"


class XDGViewer(UnixViewer):
    """
    The freedesktop.org ``xdg-open`` command.
    """

    def get_command_ex(self, file: str, **options: Any) -> tuple[str, str]:
        command = executable = "xdg-open"
        return command, executable

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        subprocess.Popen(["xdg-open", path])
        return 1


class DisplayViewer(UnixViewer):
    """
    The ImageMagick ``display`` command.
    This viewer supports the ``title`` parameter.
    """

    def get_command_ex(
        self, file: str, title: str | None = None, **options: Any
    ) -> tuple[str, str]:
        command = executable = "display"
        if title:
            command += f" -title {quote(title)}"
        return command, executable

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        args = ["display"]
        title = options.get("title")
        if title:
            args += ["-title", title]
        args.append(path)

        subprocess.Popen(args)
        return 1


class GmDisplayViewer(UnixViewer):
    """The GraphicsMagick ``gm display`` command."""

    def get_command_ex(self, file: str, **options: Any) -> tuple[str, str]:
        executable = "gm"
        command = "gm display"
        return command, executable

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        subprocess.Popen(["gm", "display", path])
        return 1


class EogViewer(UnixViewer):
    """The GNOME Image Viewer ``eog`` command."""

    def get_command_ex(self, file: str, **options: Any) -> tuple[str, str]:
        executable = "eog"
        command = "eog -n"
        return command, executable

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        subprocess.Popen(["eog", "-n", path])
        return 1


class XVViewer(UnixViewer):
    """
    The X Viewer ``xv`` command.
    This viewer supports the ``title`` parameter.
    """

    def get_command_ex(
        self, file: str, title: str | None = None, **options: Any
    ) -> tuple[str, str]:


        command = executable = "xv"
        if title:
            command += f" -name {quote(title)}"
        return command, executable

    def show_file(self, path: str, **options: Any) -> int:
        """
        Display given file.
        """
        if not os.path.exists(path):
            raise FileNotFoundError
        args = ["xv"]
        title = options.get("title")
        if title:
            args += ["-name", title]
        args.append(path)

        subprocess.Popen(args)
        return 1


if sys.platform not in ("win32", "darwin"):
    if shutil.which("xdg-open"):
        register(XDGViewer)
    if shutil.which("display"):
        register(DisplayViewer)
    if shutil.which("gm"):
        register(GmDisplayViewer)
    if shutil.which("eog"):
        register(EogViewer)
    if shutil.which("xv"):
        register(XVViewer)


class IPythonViewer(Viewer):
    """The viewer for IPython frontends."""


    def show_image(self, image: Image.Image, **options: Any) -> int:
        ipython_display(image)
        return 1


try:
    from IPython.display import display as ipython_display
except ImportError:
    pass
else:
    register(IPythonViewer)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Syntax: python3 ImageShow.py imagefile [title]")
        sys.exit()

    with Image.open(sys.argv[1]) as im:
        print(show(im, *sys.argv[2:]))
```

---

## 第 12/25 题 — blind_id: `J22-89ed7b6399ee`
**【claim 判定任务】**

> 主张：攻击者控制 BenchmarkTest00006 HTTP 请求头，能通过所示程序在 POSIX 部署中执行额外 shell 命令。评估给定版本的完整可用攻击链，不允许默修异常的类名、注解或解码函数，不假定未展示的 helper/依赖定义；若这些缺口阻碍判定则判 inconclusive 并说明。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 source.java**
```java


















package org.owasp.benchmark.testcode;


import java.io.IOException;
import javax.servlet.ServletException;
import javax.servlet.annotation.v2ae670;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@v2ae670(value = "/BenchmarkTest00006")
public class BenchmarkTest00006 extends HttpServlet {

    private static final long serialVersionUID = 1L;

    @Override
    public void doGet(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {
        doPost(request, response);
    }

    @Override
    public void doPost(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {

        response.setContentType("text/html;charset=UTF-8");

        String param = "";
        if (request.getHeader("BenchmarkTest00006") != null) {

if (false) {            }
            param = request.getHeader("BenchmarkTest00006");
        }


        param = java.net.v543944.decode(param, "UTF-8");

        java.util.List<String> argList = new java.util.ArrayList<String>();

        String osName = System.getProperty("os.name");
        if (osName.indexOf("Windows") != -1) {
            argList.add("/c");
            argList.add("cmd.exe");
        } else {
            argList.add("sh");
            argList.add("-c");
        }
        argList.add("echo " + param);

        ProcessBuilder pb = new ProcessBuilder();

        pb.command(argList);

        try {
            Process p = pb.start();
            org.owasp.benchmark.helpers.Utils.printOSCommandResults(p, response);
        } catch (IOException e) {
            System.out.println(
                    "Problem executing task - java.lang.ProcessBuilder(java.util.List) Test Case");
            throw new ServletException(e);
        }
    }
}
```

---

## 第 13/25 题 — blind_id: `J22-36e465e626bf`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import sys

def main():
    print('requested path:', sys.argv[1:])
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 14/25 题 — blind_id: `J22-a10e12215e1b`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问评估 case_dependency 由运行方提供为空模块、导入成功的参照配置；不评估导入模块内部行为。PoC 环境另按 driver_context 指定。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import case_dependency
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 15/25 题 — blind_id: `J22-64246acf8fb9`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 16/25 题 — blind_id: `J22-d3a5404e8a88`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import sys

def main():
    print('requested:', sys.argv[1:])
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 17/25 题 — blind_id: `J22-6bd66d19cd1a`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问允许 main 中固定休眠完成，不把某次驱动的时间限额作为静态缺陷不存在的理由。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys
import time

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    time.sleep(30)
    with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 18/25 题 — blind_id: `J22-b389e022c980`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.path.realpath(os.environ['SERVE_ROOT'])
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    full = os.path.realpath(os.path.join(serve_root, arg))
    if full != serve_root and (not full.startswith(serve_root + os.sep)):
        print('rejected: outside serve root')
        raise SystemExit(2)
    with open(full, encoding='utf-8') as fh:
        print(fh.read())
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 19/25 题 — blind_id: `J22-4db9bc6f6078`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import subprocess
import sys

def main():
    args = sys.argv[1:] or ['']
    subprocess.run(['echo', 'hello'] + args, check=False)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 20/25 题 — blind_id: `J22-d5663c5570c4`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问评估支持的 QSWARM_FEATURE_FLAG=on 配置；PoC 环境另按 driver_context 指定，不能将两者混同。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    serve_root = os.environ['SERVE_ROOT']
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    if os.environ.get('QSWARM_FEATURE_FLAG') == 'on':
        with open(os.path.join(serve_root, arg), encoding='utf-8') as fh:
            print(fh.read())
    print('processed:', arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '../secret.txt'\ninvoke('allowed.txt')\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off",
    "SERVE_ROOT": "{case_root}/serve"
  },
  "file_layout": {
    "secret.txt": "OBSERVATION_73ab901c",
    "serve/allowed.txt": "PUBLIC_CONTENT_612a"
  },
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "allowed.txt",
    "../secret.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 21/25 题 — blind_id: `J22-844c49773a36`
**【claim 判定任务】**

> 主张：Windows 下攻击者控制 safe_join 的不可信路径参数，可以用含目录前缀的设备名（例如 sub/NUL）绕过应拒绝 Windows 设备路径的拼接策略，使函数返回非 None 的设备路径。本问只评估过滤契约，不主张已证明实际设备打开、任意文件读取或拒绝服务。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 security.py**
```python
from __future__ import annotations

import hashlib
import hmac
import os
import posixpath
import secrets

SALT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
DEFAULT_PBKDF2_ITERATIONS = 1_000_000

_os_alt_seps: list[str] = list(
    sep for sep in [os.sep, os.path.altsep] if sep is not None and sep != "/"
)

_windows_device_files = {
    "AUX",
    "CON",
    "CONIN$",
    "CONOUT$",
    *(f"COM{c}" for c in "123456789¹²³"),
    *(f"LPT{c}" for c in "123456789¹²³"),
    "NUL",
    "PRN",
}


def gen_salt(length: int) -> str:
    """Generate a random string of SALT_CHARS with specified ``length``."""
    if length <= 0:
        raise ValueError("Salt length must be at least 1.")

    return "".join(secrets.choice(SALT_CHARS) for _ in range(length))


def _hash_internal(method: str, salt: str, password: str) -> tuple[str, str]:
    method, *args = method.split(":")
    salt_bytes = salt.encode()
    password_bytes = password.encode()

    if method == "scrypt":
        if not args:
            n = 2**15
            r = 8
            p = 1
        else:
            try:

                n, r, p = map(int, args)
            except ValueError:
                raise ValueError("'scrypt' takes 3 arguments.") from None

        maxmem = 132 * n * r * p
        return (
            hashlib.scrypt(
                password_bytes, salt=salt_bytes, n=n, r=r, p=p, maxmem=maxmem
            ).hex(),
            f"scrypt:{n}:{r}:{p}",
        )
    elif method == "pbkdf2":
        len_args = len(args)

        if len_args == 0:
            hash_name = "sha256"
            iterations = DEFAULT_PBKDF2_ITERATIONS
        elif len_args == 1:
            hash_name = args[0]
            iterations = DEFAULT_PBKDF2_ITERATIONS
        elif len_args == 2:
            hash_name = args[0]
            iterations = int(args[1])
        else:
            raise ValueError("'pbkdf2' takes 2 arguments.")

        return (
            hashlib.pbkdf2_hmac(
                hash_name, password_bytes, salt_bytes, iterations
            ).hex(),
            f"pbkdf2:{hash_name}:{iterations}",
        )
    else:
        raise ValueError(f"Invalid hash method '{method}'.")


def generate_password_hash(
    password: str, method: str = "scrypt", salt_length: int = 16
) -> str:
    """Securely hash a password for storage. A password can be compared to a stored hash
    using :func:`check_password_hash`.

    The following methods are supported:

    -   ``scrypt``, the default. The parameters are ``n``, ``r``, and ``p``, the default
        is ``scrypt:32768:8:1``. See :func:`hashlib.scrypt`.
    -   ``pbkdf2``, less secure. The parameters are ``hash_method`` and ``iterations``,
        the default is ``pbkdf2:sha256:600000``. See :func:`hashlib.pbkdf2_hmac`.

    Default parameters may be updated to reflect current guidelines, and methods may be
    deprecated and removed if they are no longer considered secure. To migrate old
    hashes, you may generate a new hash when checking an old hash, or you may contact
    users with a link to reset their password.

    :param password: The plaintext password.
    :param method: The key derivation function and parameters.
    :param salt_length: The number of characters to generate for the salt.

    .. versionchanged:: 3.1
        The default iterations for pbkdf2 was increased to 1,000,000.

    .. versionchanged:: 2.3
        Scrypt support was added.

    .. versionchanged:: 2.3
        The default iterations for pbkdf2 was increased to 600,000.

    .. versionchanged:: 2.3
        All plain hashes are deprecated and will not be supported in Werkzeug 3.0.
    """
    salt = gen_salt(salt_length)
    h, actual_method = _hash_internal(method, salt, password)
    return f"{actual_method}${salt}${h}"


def check_password_hash(pwhash: str, password: str) -> bool:
    """Securely check that the given stored password hash, previously generated using
    :func:`generate_password_hash`, matches the given password.

    Methods may be deprecated and removed if they are no longer considered secure. To
    migrate old hashes, you may generate a new hash when checking an old hash, or you
    may contact users with a link to reset their password.

    :param pwhash: The hashed password.
    :param password: The plaintext password.

    .. versionchanged:: 2.3
        All plain hashes are deprecated and will not be supported in Werkzeug 3.0.
    """
    try:
        method, salt, hashval = pwhash.split("$", 2)
    except ValueError:
        return False

    return hmac.compare_digest(_hash_internal(method, salt, password)[0], hashval)


def safe_join(directory: str, *pathnames: str) -> str | None:
    """Safely join zero or more untrusted path components to a base
    directory to avoid escaping the base directory.

    :param directory: The trusted base directory.
    :param pathnames: The untrusted path components relative to the
        base directory.
    :return: A safe path, otherwise ``None``.

    .. versionchanged:: 3.1.5
        More special device names, regardless of extension or trailing spaces,
        are not allowed on Windows.

    .. versionchanged:: 3.1.4
        Special device names are not allowed on Windows.
    """
    if not directory:


        directory = "."

    parts = [directory]

    for filename in pathnames:
        if filename != "":
            filename = posixpath.normpath(filename)

        if (
            any(sep in filename for sep in _os_alt_seps)
            or (
                os.name == "nt"
                and filename.partition(".")[0].strip().upper() in _windows_device_files
            )
            or os.path.isabs(filename)

            or filename.startswith("/")
            or filename == ".."
            or filename.startswith("../")
        ):
            return None

        parts.append(filename)

    return posixpath.join(*parts)
```

**材料文件 test_security.py**
```python
import os
import sys

import pytest

from werkzeug.security import check_password_hash
from werkzeug.security import generate_password_hash
from werkzeug.security import safe_join


def test_default_password_method():
    value = generate_password_hash("secret")
    assert value.startswith("scrypt:")



@pytest.mark.xfail(
    sys.implementation.name == "pypy", reason="scrypt unavailable on pypy"
)
def test_scrypt():
    value = generate_password_hash("secret", method="scrypt")
    assert check_password_hash(value, "secret")
    assert value.startswith("scrypt:32768:8:1$")


def test_pbkdf2():
    value = generate_password_hash("secret", method="pbkdf2")
    assert check_password_hash(value, "secret")
    assert value.startswith("pbkdf2:sha256:1000000$")


def test_salted_hashes():
    hash1 = generate_password_hash("secret")
    hash2 = generate_password_hash("secret")
    assert hash1 != hash2
    assert check_password_hash(hash1, "secret")
    assert check_password_hash(hash2, "secret")


def test_require_salt():
    with pytest.raises(ValueError):
        generate_password_hash("secret", salt_length=0)


def test_invalid_method():
    with pytest.raises(ValueError, match="Invalid hash method"):
        generate_password_hash("secret", "sha256")


@pytest.mark.parametrize(
    ("path", "expect"),
    [
        ("b/c", "a/b/c"),
        ("../b/c", None),
        ("b\\c", None if os.name == "nt" else "a/b\\c"),
        ("//b/c", None),
    ],
)
def test_safe_join(path, expect):
    assert safe_join("a", path) == expect


def test_safe_join_os_sep():
    import werkzeug.security as sec

    prev_value = sec._os_alt_seps
    sec._os_alt_seps = "*"
    assert safe_join("foo", "bar/baz*") is None
    sec._os_alt_steps = prev_value


def test_safe_join_empty_trusted():
    assert safe_join("", "c:test.txt") == "./c:test.txt"


@pytest.mark.parametrize(
    "name", ["CON", "CON.txt", "CON.txt.html", "CON  ", "CON . txt"]
)
def test_safe_join_windows_special(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Windows special device name is not allowed on Windows."""
    monkeypatch.setattr("os.name", "nt")
    assert safe_join("a", name) is None


def test_safe_join_not_windows_special(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.name", "posix")
    assert safe_join("a", "CON") == "a/CON"
```

---

## 第 22/25 题 — blind_id: `J22-60cc86fb6ec0`
**【claim 判定任务】**

> 主张：攻击者控制套接字内容，能沿给定程序的实际调用链进入 action 参数并以父目录路径使 fopen 创建或截断预期目录以外的文件。所示局部文件打开操作本身不等于已证明套接字到写入的可达性；未展示头文件、调用方和输入约束不得假定。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 source.cpp**
```cpp
















#ifndef OMITGUARD

#include "std_testcase.h"
#include "v3f9c2e7_81.h"

#ifdef _WIN32
#define FOPEN fopen
#else
#define FOPEN fopen

#endif

namespace v3f9c2e7_81
{

void v53c2a2::action(char * v5d7314) const
{
    {
        FILE *pFile = NULL;

        pFile = FOPEN(v5d7314, "wb+");
        if (pFile != NULL)
        {

if (0) {            }
            fclose(pFile);
        }
    }
}

}
#endif
```

---

## 第 23/25 题 — blind_id: `J22-478fc6082c58`
**【claim 判定任务】**

> 主张：攻击者控制 FormData.add_field 的 name、filename 或 content_type，可经 multipart 头序列化注入额外头部或字段；不限于参数被保存，必须有跨越头部边界的实际编码链。仅依据给出的文件判断，未展示模块的转义规则不得假定。请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。

**【PoC 任务】**：本题未设置 PoC 任务，`owner_poc_judgment` 保持 `null`。

**材料文件 formdata.py**
```python
import io
import warnings
from typing import Any, Iterable, List, Optional
from urllib.parse import urlencode

from multidict import MultiDict, MultiDictProxy

from . import hdrs, multipart, payload
from .helpers import guess_filename
from .payload import Payload

__all__ = ("FormData",)


class FormData:
    """Helper class for form body generation.

    Supports multipart/form-data and application/x-www-form-urlencoded.
    """

    def __init__(
        self,
        fields: Iterable[Any] = (),
        quote_fields: bool = True,
        charset: Optional[str] = None,
        *,
        default_to_multipart: bool = False,
    ) -> None:
        self._writer = multipart.MultipartWriter("form-data")
        self._fields: List[Any] = []
        self._is_multipart = default_to_multipart
        self._quote_fields = quote_fields
        self._charset = charset

        if isinstance(fields, dict):
            fields = list(fields.items())
        elif not isinstance(fields, (list, tuple)):
            fields = (fields,)
        self.add_fields(*fields)

    @property
    def is_multipart(self) -> bool:
        return self._is_multipart


    def add_field(
        self,
        name: str,
        value: Any,
        *,
        content_type: Optional[str] = None,
        filename: Optional[str] = None,
        content_transfer_encoding: Optional[str] = None,
    ) -> None:

        if isinstance(value, io.IOBase):
            self._is_multipart = True
        elif isinstance(value, (bytes, bytearray, memoryview)):
            msg = (
                "In v4, passing bytes will no longer create a file field. "
                "Please explicitly use the filename parameter or pass a BytesIO object."
            )
            if filename is None and content_transfer_encoding is None:
                warnings.warn(msg, DeprecationWarning)
                filename = name

        type_options: MultiDict[str] = MultiDict({"name": name})
        if filename is not None and not isinstance(filename, str):
            raise TypeError("filename must be an instance of str. Got: %s" % filename)
        if filename is None and isinstance(value, io.IOBase):
            filename = guess_filename(value, name)
        if filename is not None:
            type_options["filename"] = filename
            self._is_multipart = True

        headers = {}
        if content_type is not None:
            if not isinstance(content_type, str):
                raise TypeError(
                    "content_type must be an instance of str. Got: %s" % content_type
                )
            headers[hdrs.CONTENT_TYPE] = content_type
            self._is_multipart = True
        if content_transfer_encoding is not None:
            if not isinstance(content_transfer_encoding, str):
                raise TypeError(
                    "content_transfer_encoding must be an instance"
                    " of str. Got: %s" % content_transfer_encoding
                )
            msg = (
                "content_transfer_encoding is deprecated. "
                "To maintain compatibility with v4 please pass a BytesPayload."
            )
            warnings.warn(msg, DeprecationWarning)
            self._is_multipart = True

        self._fields.append((type_options, headers, value))

    def add_fields(self, *fields: Any) -> None:
        to_add = list(fields)

        while to_add:
            rec = to_add.pop(0)

            if isinstance(rec, io.IOBase):
                k = guess_filename(rec, "unknown")
                self.add_field(k, rec)

            elif isinstance(rec, (MultiDictProxy, MultiDict)):
                to_add.extend(rec.items())

            elif isinstance(rec, (list, tuple)) and len(rec) == 2:
                k, fp = rec
                self.add_field(k, fp)

            else:
                raise TypeError(
                    "Only io.IOBase, multidict and (name, file) "
                    "pairs allowed, use .add_field() for passing "
                    "more complex parameters, got {!r}".format(rec)
                )

    def _gen_form_urlencoded(self) -> payload.BytesPayload:

        data = []
        for type_options, _, value in self._fields:
            data.append((type_options["name"], value))

        charset = self._charset if self._charset is not None else "utf-8"

        if charset == "utf-8":
            content_type = "application/x-www-form-urlencoded"
        else:
            content_type = "application/x-www-form-urlencoded; charset=%s" % charset

        return payload.BytesPayload(
            urlencode(data, doseq=True, encoding=charset).encode(),
            content_type=content_type,
        )

    def _gen_form_data(self) -> multipart.MultipartWriter:
        """Encode a list of fields using the multipart/form-data MIME format"""
        for dispparams, headers, value in self._fields:
            try:
                if hdrs.CONTENT_TYPE in headers:
                    part = payload.get_payload(
                        value,
                        content_type=headers[hdrs.CONTENT_TYPE],
                        headers=headers,
                        encoding=self._charset,
                    )
                else:
                    part = payload.get_payload(
                        value, headers=headers, encoding=self._charset
                    )
            except Exception as exc:
                raise TypeError(
                    "Can not serialize value type: %r\n "
                    "headers: %r\n value: %r" % (type(value), headers, value)
                ) from exc

            if dispparams:
                part.set_content_disposition(
                    "form-data", quote_fields=self._quote_fields, **dispparams
                )


                assert part.headers is not None
                part.headers.popall(hdrs.CONTENT_LENGTH, None)

            self._writer.append_payload(part)

        self._fields.clear()
        return self._writer

    def __call__(self) -> Payload:
        if self._is_multipart:
            return self._gen_form_data()
        else:
            return self._gen_form_urlencoded()
```

**材料文件 test_formdata.py**
```python
import io
from unittest import mock

import pytest

from aiohttp import FormData, web
from aiohttp.http_writer import StreamWriter
from aiohttp.pytest_plugin import AiohttpClient


@pytest.fixture
def buf():
    return bytearray()


@pytest.fixture
def writer(buf):
    writer = mock.Mock()

    async def write(chunk):
        buf.extend(chunk)

    writer.write.side_effect = write
    return writer


def test_formdata_multipart(buf: bytearray) -> None:
    form = FormData(default_to_multipart=False)
    assert not form.is_multipart

    form.add_field("test", b"test", filename="test.txt")
    assert form.is_multipart


def test_form_data_is_multipart_param(buf: bytearray) -> None:
    form = FormData(default_to_multipart=True)
    assert form.is_multipart

    form.add_field("test", "test")
    assert form.is_multipart


def test_invalid_formdata_payload() -> None:
    form = FormData()
    form.add_field("test", object(), filename="test.txt")
    with pytest.raises(TypeError):
        form()


def test_invalid_formdata_params() -> None:
    with pytest.raises(TypeError):
        FormData("asdasf")


def test_invalid_formdata_params2() -> None:
    with pytest.raises(TypeError):
        FormData("as")


async def test_formdata_textio_charset(buf: bytearray, writer) -> None:
    form = FormData()
    body = io.TextIOWrapper(io.BytesIO(b"\xe6\x97\xa5\xe6\x9c\xac"), encoding="utf-8")
    form.add_field("foo", body, content_type="text/plain; charset=shift-jis")
    payload = form()
    await payload.write(writer)
    assert b"charset=shift-jis" in buf
    assert b"\x93\xfa\x96{" in buf


def test_invalid_formdata_content_type() -> None:
    form = FormData()
    invalid_vals = [0, 0.1, {}, [], b"foo"]
    for invalid_val in invalid_vals:
        with pytest.raises(TypeError):
            form.add_field("foo", "bar", content_type=invalid_val)


def test_invalid_formdata_filename() -> None:
    form = FormData()
    invalid_vals = [0, 0.1, {}, [], b"foo"]
    for invalid_val in invalid_vals:
        with pytest.raises(TypeError):
            form.add_field("foo", "bar", filename=invalid_val)


def test_invalid_formdata_content_transfer_encoding() -> None:
    form = FormData()
    invalid_vals = [0, 0.1, {}, [], b"foo"]
    for invalid_val in invalid_vals:

        with pytest.raises(TypeError):
            form.add_field("foo", "bar", content_transfer_encoding=invalid_val)


async def test_formdata_field_name_is_quoted(buf, writer) -> None:
    form = FormData(charset="ascii")
    form.add_field("email 1", "xxx@x.co", content_type="multipart/form-data")
    payload = form()
    await payload.write(writer)
    assert b'name="email\\ 1"' in buf


async def test_formdata_field_name_is_not_quoted(buf, writer) -> None:
    form = FormData(quote_fields=False, charset="ascii")
    form.add_field("email 1", "xxx@x.co", content_type="multipart/form-data")
    payload = form()
    await payload.write(writer)
    assert b'name="email 1"' in buf


async def test_formdata_is_reusable(aiohttp_client: AiohttpClient) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.Response()

    app = web.Application()
    app.add_routes([web.post("/", handler)])

    client = await aiohttp_client(app)

    data = FormData()
    data.add_field("test", "test_value", content_type="application/json")


    resp1 = await client.post("/", data=data)
    assert resp1.status == 200
    resp1.release()


    resp2 = await client.post("/", data=data)
    assert resp2.status == 200
    resp2.release()


    resp3 = await client.post("/", data=data)
    assert resp3.status == 200
    resp3.release()


async def test_formdata_reusability_multipart(
    writer: StreamWriter, buf: bytearray
) -> None:
    form = FormData()
    form.add_field("name", "value")
    form.add_field("file", b"content", filename="test.txt", content_type="text/plain")


    payload1 = form()
    assert form.is_multipart
    buf.clear()
    await payload1.write(writer)
    result1 = bytes(buf)


    assert b"name" in result1
    assert b"value" in result1
    assert b"test.txt" in result1
    assert b"content" in result1
    assert b"text/plain" in result1


    payload2 = form()
    buf.clear()
    await payload2.write(writer)
    result2 = bytes(buf)


    assert result1 == result2


    payload3 = form()
    buf.clear()
    await payload3.write(writer)
    result3 = bytes(buf)

    assert result1 == result3


async def test_formdata_reusability_urlencoded(
    writer: StreamWriter, buf: bytearray
) -> None:
    form = FormData()
    form.add_field("key1", "value1")
    form.add_field("key2", "value2")


    payload1 = form()
    assert not form.is_multipart
    buf.clear()
    await payload1.write(writer)
    result1 = bytes(buf)


    assert b"key1=value1" in result1
    assert b"key2=value2" in result1


    payload2 = form()
    buf.clear()
    await payload2.write(writer)
    result2 = bytes(buf)


    assert result1 == result2


    payload3 = form()
    buf.clear()
    await payload3.write(writer)
    result3 = bytes(buf)

    assert result1 == result3


async def test_formdata_reusability_after_adding_fields(
    writer: StreamWriter, buf: bytearray
) -> None:
    form = FormData()
    form.add_field("field1", "value1")


    payload1 = form()
    buf.clear()
    await payload1.write(writer)
    result1 = bytes(buf)


    form.add_field("field2", "value2")


    payload2 = form()
    buf.clear()
    await payload2.write(writer)
    result2 = bytes(buf)


    assert result1 != result2
    assert b"field1=value1" in result2
    assert b"field2=value2" in result2
    assert b"field2=value2" not in result1


    payload3 = form()
    buf.clear()
    await payload3.write(writer)
    result3 = bytes(buf)

    assert result2 == result3


async def test_formdata_reusability_with_io_fields(
    writer: StreamWriter, buf: bytearray
) -> None:
    form = FormData()


    bytes_io = io.BytesIO(b"bytes content")
    string_io = io.StringIO("string content")

    form.add_field(
        "bytes_field",
        bytes_io,
        filename="bytes.bin",
        content_type="application/octet-stream",
    )
    form.add_field(
        "string_field", string_io, filename="text.txt", content_type="text/plain"
    )


    payload1 = form()
    buf.clear()
    await payload1.write(writer)
    result1 = bytes(buf)

    assert b"bytes content" in result1
    assert b"string content" in result1


    bytes_io.seek(0)
    string_io.seek(0)


    payload2 = form()
    buf.clear()
    await payload2.write(writer)
    result2 = bytes(buf)


    assert result1 == result2
```

---

## 第 24/25 题 — blind_id: `J22-11160cd1bd26`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。本问允许 main 中固定休眠完成，不把某次驱动的时间限额作为静态缺陷不存在的理由。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys
import time

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    time.sleep(30)
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---

## 第 25/25 题 — blind_id: `J22-904022e828a7`
**【claim 判定任务】**

> 主张：攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。请判 real/refuted/inconclusive，并给源码函数或行号证据。

**【PoC 判定任务】**

> 独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。

**目标源码 target.py**（python）
```python
import os
import sys

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else ''
    os.system('echo hello ' + arg)
if __name__ == '__main__':
    main()
```

**驱动上下文 driver_context**（PoC 判定依据；判定目标是“该驱动在指定环境/限额下是否触发上述缺陷”）
```json
{
  "argv": [
    "{python}",
    "-I",
    "-S",
    "{case_root}/driver.py"
  ],
  "child_timeout_seconds": 0.5,
  "cwd": "{case_root}",
  "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
  "driver_source": "import json\nimport os\nfrom pathlib import Path\nimport shlex\nimport subprocess\nimport sys\nroot = Path(os.environ['CASE_ROOT'])\n\ndef invoke(argument):\n    command = [sys.executable, '-I', '-S', str(root / 'target.py'), argument]\n    try:\n        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))\n        record = {'argument': argument, 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr, 'timed_out': False}\n    except subprocess.TimeoutExpired:\n        record = {'argument': argument, 'returncode': None, 'stdout': '', 'stderr': '', 'timed_out': True}\n    print(json.dumps(record), flush=True)\npayload = '; printf %s OBSERVATION_73ab901c > ' + shlex.quote(str(root / 'observation.txt'))\ninvoke(payload)\n",
  "entrypoint": "driver.py",
  "environment": {
    "CASE_ROOT": "{case_root}",
    "LC_ALL": "C.UTF-8",
    "PATH": "/usr/bin:/bin",
    "QSWARM_FEATURE_FLAG": "off"
  },
  "file_layout": {},
  "inherit_environment": false,
  "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
  "kind": "python_script",
  "observation": "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。",
  "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
  "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
  "target_arguments_in_order": [
    "; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"
  ],
  "timeout_seconds": 4
}
```

---
