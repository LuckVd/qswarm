#!/usr/bin/env python3
"""Build an auditable v2.1 revision; never write to the frozen v2 directory.

Only the trusted, locally constructed fixture programs below may be executed
by --verify-runtime. This is not a sandbox for judge submissions or repositories.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tokenize

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
V2 = HERE.parent / "calibration_v2"
VERSION = "e0-cal-v2.1"
CLAIM_ENUM = {"real", "refuted", "inconclusive"}
POC_ENUM = {"effective", "ineffective", "not_applicable", "not_runnable"}


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def v2_inventory() -> dict[str, str]:
    return {str(p.relative_to(V2)): digest(p.read_bytes())
            for p in sorted(V2.rglob("*")) if p.is_file()}


def strip_python_comments(source: str) -> str:
    """Blank real COMMENT tokens, including inline comments, preserving line numbers."""
    lines = source.splitlines(keepends=True)
    stream = tokenize.generate_tokens(io.StringIO(source).readline)
    try:
        for token in stream:
            if token.type == tokenize.COMMENT:
                line, col = token.start
                end_line, end_col = token.end
                if line != end_line:
                    raise ValueError("unexpected multiline comment")
                lines[line - 1] = lines[line - 1][:col] + " " * (end_col - col) + lines[line - 1][end_col:]
    except (tokenize.TokenError, IndentationError) as exc:
        raise ValueError("incomplete Python source; restore it before publishing") from exc
    return "".join(line.rstrip() + "\n" for line in "".join(lines).splitlines())


def strip_c_comments(source: str) -> str:
    """Lexically blank Java/C++ comments; keep strings, chars and newlines intact."""
    chars = list(source)
    i, state = 0, "code"
    while i < len(chars):
        ch = source[i]
        nxt = source[i:i + 2]
        if state in {"string", "char"}:
            if ch == "\\":
                i += 2
                continue
            if ch == ('"' if state == "string" else "'"):
                state = "code"
        elif state == "line":
            if ch == "\n":
                state = "code"
            else:
                chars[i] = " "
        elif state == "block":
            if nxt == "*/":
                chars[i:i + 2] = [" ", " "]
                i += 2
                state = "code"
                continue
            if ch != "\n":
                chars[i] = " "
        elif nxt in {"//", "/*"}:
            chars[i:i + 2] = [" ", " "]
            state = "line" if nxt == "//" else "block"
            i += 2
            continue
        elif ch in {'"', "'"}:
            state = "string" if ch == '"' else "char"
        i += 1
    if state == "block":
        raise ValueError("unterminated block comment")
    return "".join(line.rstrip() + "\n" for line in "".join(chars).splitlines())


def neutral_python(source: str) -> str:
    """New fixture sources are AST-rendered: no comments or docstring hints."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
                node.body.pop(0)
                if not node.body:
                    node.body.append(ast.Pass())
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def fixture_source(family: str, number: int) -> str:
    """Replace ambiguous 05/06 stubs with actual operations after import/delay."""
    if family == "INJ":
        prefix = "import os\nimport sys\n"
        operation = 'os.system("echo hello " + arg)'
        setup = 'arg = sys.argv[1] if len(sys.argv) > 1 else ""'
        if number == 2:
            return neutral_python('import subprocess\nimport sys\ndef main():\n    args = sys.argv[1:] or [""]\n    subprocess.run(["echo", "hello"] + args, check=False)\nif __name__ == "__main__":\n    main()\n')
        if number == 3:
            return neutral_python('import sys\ndef main():\n    print("requested:", sys.argv[1:])\nif __name__ == "__main__":\n    main()\n')
    else:
        prefix = "import os\nimport sys\n"
        operation = 'with open(os.path.join(serve_root, arg), encoding="utf-8") as fh:\n    print(fh.read())'
        setup = 'serve_root = os.environ["SERVE_ROOT"]\narg = sys.argv[1] if len(sys.argv) > 1 else ""'
        if number == 2:
            return neutral_python('import os\nimport sys\ndef main():\n    serve_root = os.path.realpath(os.environ["SERVE_ROOT"])\n    arg = sys.argv[1] if len(sys.argv) > 1 else ""\n    full = os.path.realpath(os.path.join(serve_root, arg))\n    if full != serve_root and not full.startswith(serve_root + os.sep):\n        print("rejected: outside serve root")\n        raise SystemExit(2)\n    with open(full, encoding="utf-8") as fh:\n        print(fh.read())\nif __name__ == "__main__":\n    main()\n')
        if number == 3:
            return neutral_python('import sys\ndef main():\n    print("requested path:", sys.argv[1:])\nif __name__ == "__main__":\n    main()\n')
    if number == 5:
        prefix = "import case_dependency\n" + prefix
    if number == 6:
        prefix += "import time\n"
    body = setup + "\n"
    if number == 6:
        body += "time.sleep(30)\n"
    if number == 4:
        body += 'if os.environ.get("QSWARM_FEATURE_FLAG") == "on":\n' + "\n".join("    " + line for line in operation.splitlines()) + '\nprint("processed:", arg)\n'
    else:
        body += operation + "\n"
    return neutral_python(prefix + "\ndef main():\n" + "\n".join("    " + line for line in body.splitlines()) + '\n\nif __name__ == "__main__":\n    main()\n')


DRIVER_PREFIX = '''import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

root = Path(os.environ["CASE_ROOT"])

def invoke(argument):
    command = [sys.executable, "-I", "-S", str(root / "target.py"), argument]
    try:
        p = subprocess.run(command, capture_output=True, text=True, timeout=0.5, env=dict(os.environ))
        record = {"argument": argument, "returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "timed_out": False}
    except subprocess.TimeoutExpired:
        record = {"argument": argument, "returncode": None, "stdout": "", "stderr": "", "timed_out": True}
    print(json.dumps(record), flush=True)

'''


def fixture_driver(family: str, number: int) -> dict:
    env = {"CASE_ROOT": "{case_root}", "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8", "QSWARM_FEATURE_FLAG": "off"}
    token = "OBSERVATION_73ab901c"
    if family == "INJ":
        preparation = 'payload = "; printf %s OBSERVATION_73ab901c > " + shlex.quote(str(root / "observation.txt"))\n'
        arguments = ["normal-argument"] if number == 9 else ["; printf %s OBSERVATION_73ab901c > {shell_quoted_case_root}/observation.txt"]
        call = 'invoke("normal-argument")\n' if number == 9 else "invoke(payload)\n"
        files = {}
        observation = "必须由目标进程的 shell 注入产生 observation.txt 且内容严格等于 OBSERVATION_73ab901c；驱动自报成功或只准备文件不算触发。"
    else:
        preparation = 'payload = "../secret.txt"\n'
        arguments = ["allowed.txt"] if number == 9 else ["allowed.txt", "../secret.txt"]
        call = 'invoke("allowed.txt")\n' if number == 9 else 'invoke("allowed.txt")\ninvoke(payload)\n'
        files = {"serve/allowed.txt": "PUBLIC_CONTENT_612a", "secret.txt": token}
        env["SERVE_ROOT"] = "{case_root}/serve"
        observation = "只有目标处理 ../secret.txt 的独立 stdout 返回根外 secret.txt 的完整内容 OBSERVATION_73ab901c 才算触发；合法 allowed.txt 输出单独记录。"
    if number == 7:
        call = 'print(json.dumps({"ok": True}))\n'
        arguments = []
    elif number == 8:
        call = '(root / "prepared.txt").write_text("ready", encoding="utf-8")\nprint(json.dumps({"prepared": True}))\n'
        arguments = []
    source = neutral_python(DRIVER_PREFIX + preparation + call)
    return {
        "kind": "python_script", "entrypoint": "driver.py", "driver_source": source,
        "argv": ["{python}", "-I", "-S", "{case_root}/driver.py"], "cwd": "{case_root}",
        "environment": env, "inherit_environment": False,
        "file_layout": files, "target_arguments_in_order": arguments,
        "timeout_seconds": 4, "child_timeout_seconds": 0.5,
        "platform": "Linux/POSIX，CPython >=3.11，/bin/sh 与 /usr/bin/echo 或 /bin/echo 可用",
        "dependencies": "仅 Python 标准库；-I -S 隔离用户及系统 site-packages；case_dependency 不可导入；不联网、不安装依赖。",
        "substitution": "{python} 是验证器的 sys.executable；{case_root} 是本题新建的临时目录；脚本通过 CASE_ROOT 构造精确 argv，脚本为权威输入。",
        "initial_state": "临时目录初始仅含展示的 target.py、driver.py 及 file_layout；observation.txt 和 prepared.txt 不存在。文件系统稳定，无并发修改或符号链接。",
        "observation": observation,
    }


def fixture_claim(family: str, number: int) -> str:
    if family == "INJ":
        description = "攻击者仅控制目标的命令行参数，能利用其中 shell 元字符让目标执行 echo 本身以外的命令，越过参数仅作为数据的边界"
    else:
        description = "攻击者仅控制目标的命令行路径，能使目标读取 SERVE_ROOT 之外的可读文件，越过服务根目录边界"
    condition = ""
    if number == 4:
        condition = "本问评估支持的 QSWARM_FEATURE_FLAG=on 配置；PoC 环境另按 driver_context 指定，不能将两者混同。"
    elif number == 5:
        condition = "本问评估 case_dependency 由运行方提供为空模块、导入成功的参照配置；不评估导入模块内部行为。PoC 环境另按 driver_context 指定。"
    elif number == 6:
        condition = "本问允许 main 中固定休眠完成，不把某次驱动的时间限额作为静态缺陷不存在的理由。"
    return "主张：" + description + "。范围：Linux/POSIX、所示 main 及其调用；系统命令与标准库可信；文件权限允许、文件系统稳定，不授予并发改链或修改程序的能力。" + condition + "请判 real/refuted/inconclusive，并给源码函数或行号证据。"


BANK_CLAIMS = {
    "0001": "主张：攻击者控制 FormData.add_field 的 name、filename 或 content_type，可经 multipart 头序列化注入额外头部或字段；不限于参数被保存，必须有跨越头部边界的实际编码链。仅依据给出的文件判断，未展示模块的转义规则不得假定。",
    "0002": "主张：在 POSIX 上，攻击者仅控制 title，能在 DisplayViewer 或 XVViewer 的 show_file/get_command 路径中把该标题解释为 shell 命令。图片路径由可信运行方提供且存在，外部查看器可信；不讨论 Windows、PATH 劫持或其他查看器。",
    "0049": "主张：Windows 下攻击者控制 safe_join 的不可信路径参数，可以用含目录前缀的设备名（例如 sub/NUL）绕过应拒绝 Windows 设备路径的拼接策略，使函数返回非 None 的设备路径。本问只评估过滤契约，不主张已证明实际设备打开、任意文件读取或拒绝服务。",
    "0003": "主张：攻击者控制 BenchmarkTest00006 HTTP 请求头，能通过所示程序在 POSIX 部署中执行额外 shell 命令。评估给定版本的完整可用攻击链，不允许默修异常的类名、注解或解码函数，不假定未展示的 helper/依赖定义；若这些缺口阻碍判定则判 inconclusive 并说明。",
    "0004": "主张：攻击者控制 BenchmarkTest00007 HTTP 请求头，可通过传给 Runtime.exec 的环境数组让实际子进程执行攻击者代码。必须说明环境输入怎样作用于所执行程序；不能把可控环境数组等同于命令字符串拼接，不允许默修局部变量作用域错误；缺上下文时判 inconclusive。",
    "0050": "主张：攻击者控制套接字内容，能沿给定程序的实际调用链进入 action 参数并以父目录路径使 fopen 创建或截断预期目录以外的文件。所示局部文件打开操作本身不等于已证明套接字到写入的可达性；未展示头文件、调用方和输入约束不得假定。",
    "0051": "主张：攻击者控制环境变量，能沿给定程序的实际调用链进入 action 参数并使 ofstream::open 创建或截断预期目录以外的文件。须区分局部危险操作与完整环境输入来源、可达性和目录安全边界；未展示内容不得假定。",
}
BANK_TRUTH = {
    "0001": ("inconclusive", "formdata.py:FormData.add_field/_gen_form_data：参数委托给未展示的 payload 和 Content-Disposition 序列化器，不能确认其换行转义。"),
    "0002": ("refuted", "ImageShow.py:DisplayViewer/XVViewer：字符串路径用 quote(title)，实际 show_file 将标题作为 argv 元素传给未启用 shell 的 Popen。"),
    "0049": ("real", "security.py:safe_join：设备集合匹配整个 filename.partition('.')[0]，sub/NUL 不命中且无其他拒绝条件，最后返回 a/sub/NUL；结论仅为 Windows 设备过滤契约。"),
    "0003": ("inconclusive", "source.java:doPost：可见 sh -c 拼接意图，但 java.net.v543944 与 v2ae670 定义缺失，不能默修后断言给定版本运行利用。"),
    "0004": ("inconclusive", "source.java:doPost：p 在局部声明前使用，实际执行命令由未展示 helper 生成；env 数组本身不是完整命令注入证明。"),
    "0050": ("inconclusive", "source.cpp:action：形参传入 fopen(wb+)，但 socket 输入、调用方、类声明和目录约束未展示，不能闭合题目主张。"),
    "0051": ("inconclusive", "source.cpp:action：形参传入 ofstream.open，close 不作路径校验，但环境输入和调用链未展示，不能闭合题目主张。"),
}


def audit_v2(rows: list[dict], mapping: dict) -> dict:
    reverse = {t["blind_id"]: iid for iid, t in mapping.items()}
    groups: dict[str, list[str]] = {}
    hints, identities = [], []
    patterns = ("sink：拼接进 shell", "无 shell 拼接", "缺省 off：sink 不可达", "故意缺失", "runner 墙钟先收割", "POTENTIAL FLAW")
    for row in rows:
        iid = reverse[row["blind_id"]]
        groups.setdefault(canonical({k: v for k, v in row.items() if k != "blind_id"}), []).append(iid)
        material_text = canonical(row["materials"])
        if any(pattern in material_text for pattern in patterns):
            hints.append(iid)
        if "qsw-canary-SEED-" in material_text:
            identities.append(iid)
    duplicated = [members for members in groups.values() if len(members) > 1]
    return {
        "n_items": len(rows), "unique_complete_questions": len(groups),
        "exact_duplicate_groups": duplicated, "excess_duplicate_items": sum(len(g) - 1 for g in duplicated),
        "hint_comment_definition": "source/sink/阻断/运行分类等解释性注释的六种已核验模式，不等同于每句都直接给 verdict",
        "hint_comment_items": hints, "hint_comment_count": len(hints), "source_identity_comment_items": identities,
        "fixture_poc_missing_count": sum(row["question_poc"] is not None and row["driver_context"].endswith("观察其行为。") for row in rows),
        "generic_claim_count": sum("题目主张的安全缺陷" in row["question_claim"] for row in rows),
        "interpretation": "旧成绩仅为带提示、重复且缺材料的旧卷描述性一致率；不支持可靠性资格结论。",
    }


def build_bundle() -> dict[str, bytes]:
    old_rows = load_rows(V2 / "judge_materials_v2.jsonl")
    old_map = json.loads((V2 / "truth_map_v2.json").read_text(encoding="utf-8"))["items"]
    reverse = {item["blind_id"]: iid for iid, item in old_map.items()}
    public, sheet, truths, changes, provenance = [], [], {}, [], {}
    for old in old_rows:
        iid = reverse[old["blind_id"]]
        match = re.fullmatch(r"E0C-(INJ|PAT)-(\d+)", iid)
        blind = "J21-" + digest((VERSION + ":" + iid).encode())[:12]
        if match:
            family, number = match.group(1), int(match.group(2))
            materials = {"lang": "python", "target_source": fixture_source(family, number)}
            driver = fixture_driver(family, number)
            claim = fixture_claim(family, number)
            poc_question = "独立评价给出的完整驱动在指定环境和时间限额内是否触发上述缺陷：effective=经目标触发；ineffective=能运行但未触发，包括未调用目标、良性输入或执行后超时；not_applicable=所示目标没有相关命令/文件操作可供此驱动触发；not_runnable=因启动或依赖错误无法进入目标。不能以漏洞存在替代本次 PoC 有效性。"
            claim_label = "refuted" if number in {2, 3} else "real"
            poc_label = "effective" if number == 1 else "not_applicable" if number == 3 else "not_runnable" if number == 5 else "ineffective"
            root_number = 1 if number in {7, 8, 9} else number
            group = f"fixture-{family.lower()}-{root_number:02d}"
            claim_evidence = "target_source:main：" + ("使用参数列表而不启用 shell，或 realpath 后核验根目录边界。" if number == 2 else "只有打印，没有相关命令执行/文件读取。" if number == 3 else "受控参数未经边界保护进入命令字符串或文件打开；题面已声明静态评估配置，启动条件不消除这段逻辑。")
            notes = {1: "实际调用目标，观察其越界副作用或内容。", 2: "目标将元字符视为参数数据或拒绝根外路径。", 3: "目标只有打印，没有本题相关操作。", 4: "本次环境明确 QSWARM_FEATURE_FLAG=off，分支不执行。", 5: "本次隔离环境不可导入 case_dependency，目标启动失败；main 中真实操作仍在静态主张范围。", 6: "目标已启动，但固定30秒等待超过驱动的0.5秒子进程限额，不能推导无漏洞。", 7: "驱动仅输出 ok=true，没有调用目标。", 8: "驱动只创建 prepared.txt，没有调用目标。", 9: "驱动调用目标但仅给普通参数或合法根内路径。"}
            poc_evidence = "driver_context.driver_source：" + notes[number]
            change = "剥除注释、显式主张、完整可执行驱动、明确环境、超时和独立观察；不再使用泛化 payload 描述。"
            if number in {5, 6}:
                change += "替换旧 import/print 或 sleep/print 替身：在导入或休眠之后保留实际主逻辑；这不是旧题源码的等价清理，不能沿用旧判定。"
        else:
            seed = iid.rsplit("-", 1)[1]
            old_materials = old["materials"]
            files = {}
            sources = {}
            for name, original in old_materials["snapshot_files"].items():
                original_path = ROOT / "seeds" / "items" / f"SEED-{seed}" / "snapshot" / name
                if seed == "0002":
                    original = original_path.read_text(encoding="utf-8")
                sources[name] = {"source_path": str(original_path.relative_to(ROOT)), "source_sha256": digest(original.encode()), "old_display_sha256": digest(old_materials["snapshot_files"][name].encode())}
                files[name] = strip_python_comments(original) if name.endswith(".py") else strip_c_comments(original)
                if name.endswith(".py"):
                    ast.parse(files[name])
            materials = {"snapshot_files": files}
            driver, poc_question = None, None
            claim = BANK_CLAIMS[seed] + "请判 real/refuted/inconclusive，并引用函数或行号；本题仅问静态主张，未设置 PoC 任务，poc_verdict 必须为 null。"
            claim_label, claim_evidence = BANK_TRUTH[seed]
            poc_label, poc_evidence = None, "未设置 PoC 问题；这不是 ineffective、not_runnable 或 not_applicable 的运行标签。"
            group = "snapshot-" + seed
            provenance[iid] = {"files": sources, "upstream_background_only": old_materials.get("upstream"), "license_policy": "原本地 snapshot 与版权/许可注释完整保留；仅盲判展示本移除注释，禁止把盲卷展示本当作无许可来源的新代码。"}
            change = "清理 Python/Java/C++ 注释及来源ID，CVE/commit移到私有溯源；明确输入、平台和安全边界；无执行闭包不虚构PoC，显式claim_only。"
            if seed == "0002":
                change += "从本地原快照恢复完整ImageShow.py，不再截到8000字符；限定POSIX两种查看器的title注入主张。"
        row = {"blind_id": blind, "assessment_mode": "claim_and_poc" if driver is not None else "claim_only", "lang": "python" if match else old.get("lang"), "materials": materials, "question_claim": claim, "question_poc": poc_question, "driver_context": driver}
        public.append(row)
        sheet.append({"item_id": iid, "blind_id": blind, "owner_claim_judgment": None, "owner_poc_judgment": None, "owner_note": None})
        truths[iid] = {"blind_id": blind, "old_blind_id": old["blind_id"], "root_cause_group": group, "design_truth_claim": claim_label, "design_truth_poc": poc_label, "claim_evidence": claim_evidence, "poc_evidence": poc_evidence, "label_provenance": "ai_authored_design_draft_not_independent_human_truth"}
        changes.append({"item_id": iid, "old_blind_id": old["blind_id"], "blind_id": blind, "old_question_sha256": digest(canonical({k: v for k, v in old.items() if k != "blind_id"}).encode()), "new_question_sha256": digest(canonical({k: v for k, v in row.items() if k != "blind_id"}).encode()), "change": change})
    # New deterministic order is not the old fixture/family block order.
    public.sort(key=lambda row: digest(("order:" + row["blind_id"]).encode()))
    sheet_by_blind = {r["blind_id"]: r for r in sheet}
    sheet = [sheet_by_blind[r["blind_id"]] for r in public]
    validate_bundle(public, sheet, truths)
    truth_doc = {"version": VERSION, "label_status": "draft_requires_owner_confirmation", "n_items": len(public), "n_root_cause_groups": len({t["root_cause_group"] for t in truths.values()}), "label_provenance": "AI-authored design and local trusted-fixture checks; not independent gold; user confirmation and independent judge required anew.", "items": truths}
    def pretty(value: object) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    def jsonl(rows: list[dict]) -> bytes:
        return ("\n".join(canonical(row) for row in rows) + "\n").encode()
    outputs = {
        "judge_materials_v2_1.jsonl": jsonl(public), "judging_sheet_v2_1.jsonl": jsonl(sheet),
        "truth_map_v2_1.json": pretty(truth_doc), "changes_v2_1.json": pretty({"version": VERSION, "items": changes}),
        "provenance_v2_1.json": pretty(provenance), "audit_v2.json": pretty(audit_v2(old_rows, old_map)),
    }
    outputs["materials_v2_1_manifest.json"] = pretty({
        "version": VERSION, "generator": "gen_materials_v2_1.py", "n_items": 25, "claim_questions": 25,
        "poc_questions": 18, "claim_only_questions": 7, "root_cause_groups": 19,
        "unique_complete_questions": 25, "label_status": "draft_requires_owner_confirmation",
        "generator_sha256": digest(Path(__file__).read_bytes()), "frozen_v2_inventory_sha256": digest(canonical(v2_inventory()).encode()),
        "artifacts": {name: digest(data) for name, data in outputs.items()},
        "checks": ["exact complete-question deduplication ignoring blind_id", "Python AST syntax and token comment gate", "private metadata/source identity hint gate", "all owner labels empty", "explicit18drivers7claim_only", "valid claim/poc design enums"],
        "limitations": ["25 items are 19 declared groups, not 25 independent vulnerability roots", "7 snapshots remain static-only; no fabricated runtime labels", "benchmark code identifiers can retain provenance; this is not source-anonymity research", "draft design labels and fixture runs are not independent calibration evidence"],
    })
    return outputs


def validate_bundle(rows: list[dict], sheet: list[dict], truths: dict) -> None:
    if len(rows) != 25 or len(truths) != 25 or len(sheet) != 25:
        raise ValueError("expected exactly 25 cases")
    seen = set()
    for row in rows:
        body = canonical({k: v for k, v in row.items() if k != "blind_id"})
        if body in seen:
            raise ValueError("duplicate complete question")
        seen.add(body)
        text = canonical(row["materials"])
        for hint in ("qsw-canary-", "# sink", "无 shell 拼接", "故意缺失", "runner 墙钟", "POTENTIAL FLAW", "BadSource:", "BadSink", "CVE-", "（截断，全文见快照）"):
            if hint in text:
                raise ValueError(f"leaking or truncated materials: {hint}")
        if any(key in row["materials"] for key in ("upstream", "driver_attempt", "family")):
            raise ValueError("private metadata in public materials")
        if row["driver_context"] is not None:
            ast.parse(row["materials"]["target_source"])
            ast.parse(row["driver_context"]["driver_source"])
            if any(tok.type == tokenize.COMMENT for tok in tokenize.generate_tokens(io.StringIO(row["materials"]["target_source"]).readline)):
                raise ValueError("fixture comment survived")
    if len({row["blind_id"] for row in rows}) != 25:
        raise ValueError("duplicate blind ID")
    if any(row[k] is not None for row in sheet for k in ("owner_claim_judgment", "owner_poc_judgment", "owner_note")):
        raise ValueError("owner labels must be empty")
    for item in truths.values():
        if item["design_truth_claim"] not in CLAIM_ENUM or item["design_truth_poc"] not in POC_ENUM | {None}:
            raise ValueError("invalid design label")


AUDIT_WRAPPER = '''import json, runpy, sys
from pathlib import Path
trace, driver = Path(sys.argv[1]), sys.argv[2]
def hook(event, args):
    if event == "subprocess.Popen":
        with trace.open("a", encoding="utf-8") as out:
            out.write(json.dumps({"event": event, "argv": args[1]}) + "\\n")
sys.addaudithook(hook)
runpy.run_path(driver, run_name="__main__")
'''


def run_fixture(row: dict) -> dict:
    """Execute a freshly built, trusted fixture with no inherited secrets/network use."""
    context = row["driver_context"]
    with tempfile.TemporaryDirectory(prefix="qsw-cal-v21-") as tmp:
        root = Path(tmp) / "case with space"
        root.mkdir()
        (root / "target.py").write_text(row["materials"]["target_source"], encoding="utf-8")
        (root / "driver.py").write_text(context["driver_source"], encoding="utf-8")
        for relative, data in context["file_layout"].items():
            dest = root / relative
            if not dest.resolve().is_relative_to(root):
                raise ValueError("invalid fixture layout")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(data, encoding="utf-8")
        env = {key: value.replace("{case_root}", str(root)) for key, value in context["environment"].items()}
        trace = Path(tmp) / "parent-observer.jsonl"
        p = subprocess.run([sys.executable, "-I", "-S", "-c", AUDIT_WRAPPER, str(trace), str(root / "driver.py")], env=env, cwd=root, capture_output=True, text=True, timeout=context["timeout_seconds"])
        events = load_rows(trace) if trace.exists() else []
        records = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
        calls = [record for record in records if "argument" in record]
        target_invocations = sum(str(root / "target.py") in event.get("argv", []) for event in events)
        source = row["materials"]["target_source"]
        is_file_case = "SERVE_ROOT" in context["environment"]
        has_operation = bool(re.search(r"\bopen\(", source)) if is_file_case else ("os.system(" in source or "subprocess.run(" in source)
        token = "OBSERVATION_73ab901c"
        observed = any(record["argument"] == "../secret.txt" and record["stdout"].strip() == token and record["returncode"] == 0 for record in calls) if is_file_case else (root / "observation.txt").exists() and (root / "observation.txt").read_text() == token
        startup_failed = bool(calls) and all("ModuleNotFoundError" in record["stderr"] for record in calls)
        label = "not_applicable" if not has_operation else "not_runnable" if startup_failed else "effective" if target_invocations and observed else "ineffective"
        # Paths in the evidence are normalized for reproducible review, never shell-expanded.
        clean = lambda text: text.replace(str(root), "{case_root}")
        return {"blind_id": row["blind_id"], "observed_poc_verdict": label, "driver_returncode": p.returncode, "target_invocations": target_invocations, "effect_observed": bool(observed), "timed_out": any(r["timed_out"] for r in calls), "legal_control_ok": any(r["argument"] == "allowed.txt" and r["stdout"].strip() == "PUBLIC_CONTENT_612a" and r["returncode"] == 0 for r in calls) if is_file_case else None, "records": [{key: clean(value) if isinstance(value, str) else value for key, value in record.items()} for record in records], "driver_stderr": clean(p.stderr)}


def verify_runtime(bundle: dict[str, bytes]) -> dict:
    if os.name != "posix" or not Path("/bin/sh").exists() or not any(Path(p).exists() for p in ("/usr/bin/echo", "/bin/echo")):
        raise RuntimeError("fixture verification requires documented POSIX tools")
    rows = [json.loads(line) for line in bundle["judge_materials_v2_1.jsonl"].decode().splitlines()]
    truth = json.loads(bundle["truth_map_v2_1.json"])["items"]
    expected = {t["blind_id"]: t["design_truth_poc"] for t in truth.values()}
    results = [run_fixture(row) for row in rows if row["driver_context"] is not None]
    problems = [r["blind_id"] for r in results if r["observed_poc_verdict"] != expected[r["blind_id"]] or r["driver_returncode"] != 0 or r["driver_stderr"]]
    return {"version": VERSION, "status": "pass" if not problems else "fail", "n_executed": len(results), "problems": problems, "interpretation": "trusted local fixture consistency only; not independent human truth or arbitrary-code sandbox evidence", "python_version": sys.version, "results": results}


def publish(path: Path, data: bytes, *, check: bool = False) -> None:
    """Fail closed on different existing bytes; never silently overwrite a release."""
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"existing artifact differs; create a new revision, do not overwrite: {path}")
    elif check:
        raise ValueError(f"missing artifact: {path}")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as out:
            out.write(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="compare deterministic artifacts without writing")
    parser.add_argument("--verify-runtime", action="store_true", help="run only the 18 generated trusted fixtures")
    args = parser.parse_args(argv)
    before = v2_inventory()
    freeze_path = HERE / "v2_frozen_inventory.json"
    frozen = {"directory": "../calibration_v2", "files_sha256": before, "policy": "byte-for-byte frozen; do not regenerate v2 or reuse its score as v2.1"}
    publish(freeze_path, (json.dumps(frozen, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(), check=args.check)
    bundle = build_bundle()
    for name, data in bundle.items():
        publish(HERE / name, data, check=args.check)
    if args.verify_runtime:
        report = verify_runtime(bundle)
        # Runtime records contain diagnostic interpreter paths/timings, not release truth.
        out = HERE / "runtime_checks_v2_1.json"
        data = (json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if args.check:
            if report["status"] != "pass":
                raise RuntimeError("runtime verification failed: " + canonical(report["problems"]))
        else:
            publish(out, data)
        if report["status"] != "pass":
            raise RuntimeError("runtime verification failed: " + canonical(report["problems"]))
    if v2_inventory() != before:
        raise RuntimeError("frozen v2 changed during generation")
    print("v2.1: 25 explicit claims, 18 complete PoC tasks, 7 explicit claim-only tasks; 19 declared groups; v2 unchanged; labels pending owner confirmation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
