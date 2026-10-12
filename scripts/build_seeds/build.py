#!/usr/bin/env python3
"""build_seeds：种子题库 v0 三源构建流水线（SPEC-6 §2；外网侧数据工程，不入 qswarm 包）。

三段式，全部确定性（种子注入），产物入库前过 qswarm.exams.bank.validate_bank：

1. **取材**（网络段，逐文件缓存 .cache-seeds/，缓存后可离线重放 ``--offline``）：
   ① CVE 回滚——OSV.dev FIX 引用 → GitHub fix commit → **parent 快照**
   （expected=real，planted=False；commit 日期 > 知识截止线，§16.7-3 原版直通红线）；
   ② Juliet/OWASP——测试用例对（漏洞面/修复面）分别作真/雷（Juliet *_bad/*_good
   函数对；OWASP expectedresults-1.2.csv expected true/false 对）；
   ③ 取材语料：Juliet C（arichardson 镜像稀疏克隆）、OWASP Benchmark Java、
   Juliet Java（CWE502 反序列化）。
2. **变异**（§16.7-3 逐字缓解）：标识符重命名、语句重排、死代码注入、结构变换
   四族；每题独立种子（derive_seed 同型 blake2b）；变异不动保护 token（家族签名
   词），变异后用固定规则集复核 expected 标注（签名 token 仍在位），不一致 → 弃题。
   CVE 回滚原题**不变异**（原样快照，planted=False）。
3. **定稿**：canary 植入（qsw-canary-<item_id>-<8hex>，无害注释形态）→ item.json /
   快照落盘 → bank.yaml 签发（sha256 清单）→ validate_bank 全绿。

家族配额：SPEC-6 §1.2 v0.2 表 + v0.3 来源拆分修订（V03_PLAN；100 题 = 7 CVE 回滚真 +
55 Juliet/OWASP 真 + 38 变异雷；deser 家族退出 v0 题库——Juliet Java v1.3 无 CWE502 且
OWASP 无 deser 类目，fp 面无源，DECISIONS 2026-10-11 行；v0.3 authz cve 5→1——4 道
BROKEN_FAMILY retire，RETIRED_CVES 排除表，jo_real 同额回补，2026-10-12 行）。
``--family-quota`` 降级模式：外网取材受限（CVE 段产出不足）时按家族级 real/fp 配额
出库，逐源计数如实写入 bank.yaml（source_counts），逐源配额偏离记 DECISIONS——
不伪造来源、不放宽红线。

用法（仓库根执行）::

    python3 scripts/build_seeds/build.py --out seeds --cache .cache-seeds
    python3 scripts/build_seeds/build.py --out seeds --offline     # 纯缓存重放
    python3 scripts/build_seeds/build.py --check                   # 只校验现库
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import random
import re
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# ── IPv4 优先（网络环境适配，DECISIONS 2026-10-11 行）────────────────────────
# 本机 IPv6 不通（curl -6 秒败）而 OSV 的 DNS 间歇性 v6 前置——urllib 侧表现为
# 间歇性 URLError(Errno 101)。getaddrinfo 结果 AF_INET 置顶，v4 可用则永不走 v6。
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_first_getaddrinfo(*args, **kwargs):
    res = _orig_getaddrinfo(*args, **kwargs)
    v4 = [r for r in res if r[0] == socket.AF_INET]
    return v4 + [r for r in res if r[0] != socket.AF_INET] if v4 else res


socket.getaddrinfo = _ipv4_first_getaddrinfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qswarm.exams.bank import dir_sha256, load_bank, validate_bank  # noqa: E402
from qswarm.simulator.rng import derive_seed  # noqa: E402

#: 模型知识截止线（§16.7-3 原版直通红线：cve_rollback fix commit 日期必须晚于该线）。
#: 取 configured models.yaml 档位模型中已知最晚训练截止（claude-sonnet-4-5，2025 初）
#: 加安全边际；v0 构建器常量，DECISIONS M2 行留痕。
KNOWLEDGE_CUTOFF = "2025-03-31"

#: 快照规模约束（S2；与 bank.py 常量一致，构建器执行）。
MAX_FILES, MAX_LINES = 8, 500

#: v0.3 retire 排除表（2026-10-12 负责人拍板，DECISIONS 同日行）：答案键人工复核判
#: BROKEN_FAMILY 的 4 道 cve_rollback——解压炸弹/DoS 漏洞真实存在（post-cutoff 修复），
#: 但其形态在六家族树无归宿（taxonomy 缺 resource_exhaustion 家族），authz 标注无法
#: 由代码真值推出，强行保留会制造不可判分题。题库 v0.3 起剔除不入库；CVE 段取材为
#: 确定性缓存重放，无排除表则四道原样再入池。来源：results/_answers_key_audit.json
#: （disposition 字段）。
RETIRED_CVES: frozenset[str] = frozenset({
    "CVE-2026-40192",   # SEED-0050
    "CVE-2026-54059",   # SEED-0051
    "CVE-2026-55380",   # SEED-0052
    "CVE-2026-59203",   # SEED-0053
})

#: 题库版本（bank.yaml bank_version；bank.py 语义「单调递增，跨版本不可混卷」——
#: 出卷 derive_seed 含 bank_version，换版本才换卷）。v0.3 重建 = 2（v0.2 = 1）；
#: 重考必须新 DB（exam_results 幂等键 + deposit_exists 守卫，同库重跑静默复用旧判定）。
BANK_VERSION = 2

#: v0.3 家族配额装配（SPEC-6 §1.2 v0.2 表 + v0.3 来源拆分修订）：逐家族
#: cve_real / jo_real / fp（deser 已退出 v0 题库）。硬约束：cve + jo_real 与 fp 必须
#: 与 qswarm.exams.bank.V0_QUOTA 的 (real, fp) 精确相等（validate_bank 逐家族相等
#: 校验，多一题少一题都拒签）。v0.3 变更仅 authz 来源拆分：cve 5→1（4 道
#: BROKEN_FAMILY retire，见 RETIRED_CVES）、jo_real 7→11 同额回补，家族配额表不变。
V03_PLAN: dict[str, dict[str, int]] = {
    "injection": {"cve": 2, "jo_real": 16, "fp": 10},
    "crypto": {"cve": 0, "jo_real": 12, "fp": 8},
    "authz": {"cve": 1, "jo_real": 11, "fp": 8},
    "config_dep": {"cve": 0, "jo_real": 12, "fp": 8},
    "mem_safety": {"cve": 0, "jo_real": 8, "fp": 4},
}

UA = {"User-Agent": "qswarm-build-seeds/1.0 (M2 seed bank v0)"}

#: GitHub API 鉴权（可选）：GITHUB_TOKEN 在环境时 5000/h（unauth 60/h）——扩池
#: 挖矿的吞吐前提（DECISIONS 2026-10-11 行）。只读公开数据，无写操作。
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()

#: Juliet C 稀疏克隆的 CWE 目录 → (family, expected_face) 映射（bad=真 / good=雷面）。
JULIET_C_DIRS = {
    "CWE78_OS_Command_Injection": "injection",
    "CWE23_Relative_Path_Traversal": "authz",
    "CWE36_Absolute_Path_Traversal": "authz",
    "CWE327_Use_Broken_Crypto": "crypto",
    "CWE328_Reversible_One_Way_Hash": "crypto",
    "CWE338_Weak_PRNG": "crypto",
    "CWE121_Stack_Based_Buffer_Overflow": "mem_safety",
    "CWE122_Heap_Based_Buffer_Overflow": "mem_safety",
    "CWE415_Double_Free": "mem_safety",
    "CWE416_Use_After_Free": "mem_safety",
    "CWE590_Free_Memory_Not_on_Heap": "mem_safety",
}

#: OWASP Benchmark category → family（expectedresults-1.2.csv category 列逐字）。
OWASP_CATEGORY_FAMILY = {
    "sqli": "injection",
    "cmdi": "injection",
    "xss": "injection",
    "ldapi": "injection",
    "xpathi": "injection",
    "weakrand": "crypto",
    "crypto": "crypto",
    "hash": "crypto",
    "pathtraver": "authz",
    "trustbound": "authz",
    "securecookie": "config_dep",
}

#: 家族签名 token（变异保护词 + expected 复核规则集；token 在位 = 真假属性未变）。
FAMILY_SIGNATURES = {
    "injection": ("getParameter", "Query", "exec", "ProcessBuilder", "DocumentBuilder",
                  "SELECT", "print", "Ldap", "runtime", "request"),
    "deser": ("readObject", "yaml.load", "loads", "ObjectInputStream", "unserialize",
              "Deserialize", "BinaryFormatter", "ObjectStateFormatter"),
    "crypto": ("Cipher", "MessageDigest", "DES", "MD5", "SHA1", "Random", "ECB", "SecretKey"),
    "authz": ("File", "Path", "getParameter", "Session", "isAdmin", "new ", "../"),
    "config_dep": ("Cookie", "setSecure", "setHttpOnly", "Config", "password", "secret"),
    "mem_safety": ("strcpy", "memcpy", "strcat", "gets", "free(", "malloc", "delete ", "new char"),
}

_LANG_BY_EXT = {
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".java": "java", ".js": "javascript",
    ".ts": "typescript", ".py": "python", ".go": "go", ".rb": "ruby", ".php": "php",
    ".cs": "csharp", ".rs": "rust", ".jsp": "java",
}


# ── 缓存层（网络段逐文件缓存；缓存后 --offline 可完整重放）────────────────


def _cache_path(cache: Path, url: str) -> Path:
    return cache / "http" / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".body")


def http_get(url: str, cache: Path, *, binary: bool = True, retries: int = 3,
             timeout: float = 30.0, offline: bool = False,
             headers: dict | None = None) -> bytes | None:
    """带磁盘缓存的 GET（命中缓存即回；未命中离线模式返回 None；重试退避）。"""
    cp = _cache_path(cache, url)
    if cp.exists():
        return cp.read_bytes()
    if offline:
        return None
    req_headers = {**UA, **(headers or {})}
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(data)
            return data
        except Exception as exc:  # noqa: BLE001（外网段尽力取材，逐源可失败）
            if attempt == retries - 1:
                print(f"  [fetch-fail] {url} → {type(exc).__name__}: {str(exc)[:80]}")
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def github_api(path: str, cache: Path, *, offline: bool = False) -> dict | list | None:
    """GitHub API GET（api.github.com；unauth 60/h，GITHUB_TOKEN 鉴权 5000/h——
    调用方按预算消费）。"""
    url = f"https://api.github.com{path}"
    data = http_get(url, cache, binary=False, offline=offline,
                    headers=({"Authorization": f"Bearer {_GITHUB_TOKEN}"}
                             if _GITHUB_TOKEN else None))
    if data is None:
        return None
    try:
        return json.loads(data)
    except ValueError:
        return None


def osv_vuln(cve_id: str, cache: Path, *, offline: bool = False) -> dict | None:
    data = http_get(f"https://api.osv.dev/v1/vulns/{cve_id}", cache, binary=False, offline=offline)
    return json.loads(data) if data else None


# ── 变异引擎（四族；确定性 per-item 种子；保护 token 不动）────────────────

_KEYWORDS = frozenset(
    """int char void if else for while return struct sizeof const static unsigned signed
    long short double float break continue switch case default do goto extern typedef
    public private protected class new delete this super throws try catch finally
    import package extends implements final abstract interface enum null true false
    def lambda elif None True False and or not in is pass raise with as yield from
    function var let const async await of typeof instanceof
    package func chan go defer select map range type var"""
    .split()
)

_IDENT_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]{3,})\b")
_LINE_STMT_RE = re.compile(r"^(\s+)([^;\n{}]+;)\s*$")


def _protected_tokens(family: str) -> frozenset[str]:
    return frozenset(t for t in FAMILY_SIGNATURES.get(family, ()) if re.match(r"^[A-Za-z_]", t))


def _is_comment_line(ln: str, lang: str) -> bool:
    """行首即注释的行（c/java: // /* * …；python: #）——rename 不收集不替换。"""
    s = ln.lstrip()
    if lang == "python":
        return s.startswith("#")
    return s.startswith(("//", "/*", "*", "*/"))


def _func_body_open_lines(lines: list[str]) -> list[int]:
    """函数体开括号所在行号（v0.3：dead_code 只能插进函数体内）。

    三种形态：行尾开体（`… ) {` / `… ) const {` / `… throws X, Y {`）、
    续行开体（前一非空行以 `)`/`,` 结尾、本行 `throws … {` 或 `{` 收尾）、
    独占 `{`（juliet 风格：前一非空行以 `)`/`const`/`throws …` 结尾）。
    类/结构体/命名空间声明行不含参数括号，天然不命中。"""
    idx: list[int] = []
    depth = 0
    for i, ln in enumerate(lines):
        depth += ln.count("{") - ln.count("}")
        prev = lines[i - 1].strip() if i else ""
        inline = re.search(r"\)\s*(?:const\s*)?(?:throws\s+[\w.,\s]+)?\{\s*$", ln)
        continuation = ln.rstrip().endswith("{") and (
            "throws" in ln or re.search(r"\)", ln)
            or prev.endswith(")") or prev.endswith(",")
        )
        brace_only = ln.strip() == "{" and bool(
            re.search(r"(?:\)\s*|const\s*|throws\s+\w[\w.,\s]*|,\s*)$", prev)
        )
        if (inline or continuation or brace_only) and depth >= 1:
            idx.append(i)
    return idx


def mutate_code(text: str, *, family: str, seed: int, lang: str) -> str:
    """四族变异（§16.7-3）：rename ×2 + reorder ×1 + dead_code ×1 + restructure。

    保护 token（家族签名词与 #include/#define 行）不参与 rename；全部变换确定性
    （derive_seed(seed, "mutate", family) 派生 rng）；语义不变（死代码/重排均
    无副作用面），expected 复核 = 家族签名 token 在位。
    v0.3（2026-10-12 答案键缺陷复盘）两处收紧：
    ① rename 的收集与替换均跳过注释行（此前 Filename:/strings.label.xml 等
      注释 token 被改名，外观损伤）；
    ② dead_code 在 c/java 只插入函数体内（此前盲插文件中点：39/39 juliet C 题
      落在文件作用域=非法 C++，OWASP Java 题切开 @Override 与方法声明）；
      无合法落点则跳过该变异（python 模块级合法，维持原位）。"""
    rng = random.Random(derive_seed(seed, "mutate", family))
    protected = _protected_tokens(family)
    lines = text.splitlines(keepends=True)

    # ① rename ×2：挑 2 个非保护标识符，逐个改名（rename → var_<hex4>）
    idents: list[str] = []
    for ln in lines:
        if ln.lstrip().startswith(("#", "//", "import ", "from ", "package ")) or _is_comment_line(ln, lang):
            continue
        for m in _IDENT_RE.finditer(ln):
            w = m.group(1)
            if w not in _KEYWORDS and w not in protected and w not in idents:
                idents.append(w)
        if len(idents) >= 24:
            break
    rng.shuffle(idents)
    for old in idents[:2]:
        new = "v" + f"{derive_seed(seed, 'rename', old):06x}"[-6:]
        lines = [
            (re.sub(rf"\b{re.escape(old)}\b", new, ln) if not _is_comment_line(ln, lang) else ln)
            for ln in lines
        ]

    # ② reorder ×1：同缩进相邻语句对交换（语义无关的纯净交换不保证——只交换
    #   同缩进的独立声明语句对；无候选则跳过）
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if (
            _LINE_STMT_RE.match(a) and _LINE_STMT_RE.match(b)
            and _LINE_STMT_RE.match(a).group(1) == _LINE_STMT_RE.match(b).group(1)
            and rng.random() < 1.0
        ):
            lines[i], lines[i + 1] = b, a
            break

    # ③ dead_code ×1：语言形 inert 块（v0.3：c/java 插函数体内，无落点跳过）
    dead = {
        "python": "\nif False:\n    pass  # dead\n",
        "c": "\nif (0) { /* dead */ } \n",
    }.get(lang, "\nif (false) { /* dead */ } \n")
    if lang == "python":
        at = max(len(lines) // 2, 1)
        lines.insert(at, dead)
    else:
        opens = _func_body_open_lines(lines)
        if opens:
            lines.insert(opens[len(opens) // 2] + 1, dead)

    # ④ restructure：块注释横幅 + 尾部空行规整（结构面变换，不改语义）
    banner = f"/* mutated seed={derive_seed(seed, 'mutate', family) & 0xffffffff:08x} */\n"
    out = "".join(lines)
    return banner + out


def expected_recheck(text: str, family: str) -> bool:
    """固定规则集复核：家族签名 token ≥1 在位（变异不得改变真/雷属性，§2）。"""
    tokens = FAMILY_SIGNATURES.get(family, ())
    return any(t in text for t in tokens)


# ── 取材面 taint 存活校验（v0.3，2026-10-12 答案键缺陷修复）────────────────
#
# 背景：Juliet 官方 sources-sink-81 exec* sink 模板是空壳——action(char * data)
# 的污点参数从未进入 sink（bad 与 goodG2B 的 sink 逐字相同，见
# docs/answers_key_audit_20261012.md §2）。此类材料标 real 入池后，"发现漏洞"
# 只能靠注释模式匹配得分、正确拒报反被判错，双向污染分数。变异后校验等于用
# bug 验 bug——必须在取材面（bad/good 两面）拦截。

_FUNC_SIG_RE = re.compile(
    r"^[^\n()]*?\b(?:void|int|char|wchar_t|boolean|Object|String|size_t|long|unsigned)\b[^\n()]*?"
    r"(?:\w+::)*\w+\s*\(([^)]*)\)\s*(?:const\s*)?(?:throws\s+[\w.,\s]+)?\{",
    re.MULTILINE,
)


def taint_param_live(text: str) -> bool:
    """juliet 用例污点存活：签名中的指针/对象参数必须在函数体内被引用 ≥1 次。

    - 只要有任一带指针/对象参数的函数把参数用进了函数体 → True（CWE23/121/122
      等存活族）；
    - 全部此类函数的参数都只出现在签名（函数体零引用）→ False（CWE78 exec*
      空壳族：EXECL(COMMAND_INT_PATH, ...)）；
    - 无带污点形参的函数（如污点为局部变量的变体）→ True（无从判定则不拦）。
    """
    saw_tainted = False
    for m in _FUNC_SIG_RE.finditer(text):
        params = [
            p.strip() for p in m.group(1).split(",")
            if p.strip() and p.strip() != "void"
        ]
        # 形如 `char * data` / `TwoIntsClass * data` / `Object data` 的污点形参
        tainted = [
            tok.split()[-1].lstrip("*&") for tok in params
            if "*" in tok or (tok.split() and tok.split()[0][:1].isupper())
        ]
        if not tainted:
            continue
        saw_tainted = True
        # 函数体：签名行之后到配对的 `}`（深度回零）为止
        start = m.end()
        depth, body_end = 1, len(text)
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    body_end = i
                    break
        body = text[start:body_end]
        # 引用判定忽略整行注释（行首 // /* *）
        body_code = "\n".join(
            ln for ln in body.splitlines() if not ln.lstrip().startswith(("//", "/*", "*"))
        )
        if any(re.search(rf"\b{re.escape(name)}\b", body_code) for name in tainted):
            return True
    return not saw_tainted


def inject_canary(text: str, canary: str, *, lang: str, rng: random.Random) -> str:
    """金丝雀以无害注释形态植入快照（§1.4；构建器随机选位置）。"""
    comment = {"python": "#", "c": "//", "cpp": "//", "java": "//", "javascript": "//"}.get(lang, "//")
    lines = text.splitlines(keepends=True)
    at = rng.randrange(len(lines) + 1)
    lines.insert(at, f"{comment} {canary}\n")
    return "".join(lines)


# ── 源一：CVE 回滚（OSV FIX → GitHub fix commit → parent 快照）─────────────

#: 候选包池（小仓、修复活跃、生态覆盖；OSV 按 package 查询 post-cutoff advisory）。
#: v0.1 扩池（2026-10-11，DECISIONS 同日行）：31 → 91 包——原池 101 候选已全评估
#: 且仅 7 条过知识截止线，缺口 26 题只能靠扩池；新池覆盖 npm/PyPI/Go/Maven/crates/
#: RubyGems 六生态、按"修复活跃 + 安全面签名命中概率"取材。
CVE_PACKAGES = [
    # ── 原池（31）──────────────────────────────────────────────────────────
    ("npm", "minimatch"), ("npm", "semver"), ("npm", "node-fetch"), ("npm", "axios"),
    ("npm", "tar"), ("npm", "moment"), ("npm", "browserslist"), ("npm", "json-schema"),
    ("npm", "shell-quote"), ("npm", "braces"), ("npm", "micromatch"), ("npm", "lodash"),
    ("npm", "hosted-git-info"), ("npm", "glob-parent"), ("npm", "follow-redirects"),
    ("npm", "axios-retry"), ("PyPI", "requests"), ("PyPI", "pyyaml"), ("PyPI", "aiohttp"),
    ("PyPI", "paramiko"), ("PyPI", "certifi"), ("PyPI", "jinja2"), ("PyPI", "werkzeug"),
    ("PyPI", "cryptography"), ("PyPI", "pillow"), ("PyPI", "starlette"), ("PyPI", "fastapi"),
    ("Go", "github.com/golang-jwt/jwt/v5"), ("Go", "github.com/gin-gonic/gin"),
    ("Go", "golang.org/x/net"), ("Maven", "com.squareup.okhttp3:okhttp"),
    # ── v0.1 扩池（60）─────────────────────────────────────────────────────
    # npm：web/序列化/SQL/加密/路径面
    ("npm", "node-forge"), ("npm", "crypto-js"), ("npm", "elliptic"), ("npm", "jsonwebtoken"),
    ("npm", "jose"), ("npm", "express"), ("npm", "cookie"), ("npm", "path-to-regexp"),
    ("npm", "cross-spawn"), ("npm", "ws"), ("npm", "got"), ("npm", "undici"),
    ("npm", "ejs"), ("npm", "handlebars"), ("npm", "marked"), ("npm", "dompurify"),
    ("npm", "prismjs"), ("npm", "sanitize-html"), ("npm", "mysql2"), ("npm", "mongoose"),
    ("npm", "sequelize"), ("npm", "knex"), ("npm", "typeorm"), ("npm", "redis"),
    ("npm", "js-yaml"), ("npm", "yaml"), ("npm", "serialize-javascript"), ("npm", "underscore"),
    ("npm", "tough-cookie"), ("npm", "fast-xml-parser"),
    # PyPI：web 框架/SQL/加密/token
    ("PyPI", "django"), ("PyPI", "flask"), ("PyPI", "tornado"), ("PyPI", "sqlalchemy"),
    ("PyPI", "sqlparse"), ("PyPI", "pyjwt"), ("PyPI", "python-jose"), ("PyPI", "pycryptodome"),
    ("PyPI", "uvicorn"), ("PyPI", "gunicorn"), ("PyPI", "redis"), ("PyPI", "pymongo"),
    ("PyPI", "ecdsa"), ("PyPI", "urllib3"),
    # Go：加密/容器/HTTP/存储
    ("Go", "golang.org/x/crypto"), ("Go", "github.com/gogo/protobuf"),
    ("Go", "github.com/docker/distribution"), ("Go", "github.com/valyala/fasthttp"),
    ("Go", "github.com/beego/beego"), ("Go", "github.com/redis/go-redis"),
    ("Go", "github.com/microcosm-cc/bluemonday"), ("Go", "github.com/go-ldap/ldap"),
    # Maven：反序列化/SQL/ORM
    ("Maven", "com.fasterxml.jackson.core:jackson-databind"), ("Maven", "org.mybatis:mybatis"),
    ("Maven", "org.springframework:spring-web"), ("Maven", "org.apache.commons:commons-text"),
    ("Maven", "com.google.code.gson:gson"), ("Maven", "org.yaml:snakeyaml"),
    # crates.io / RubyGems
    ("crates.io", "openssl"), ("crates.io", "rustls"), ("crates.io", "tokio"),
    ("crates.io", "serde_json"), ("crates.io", "regex"),
    ("RubyGems", "rails"), ("RubyGems", "rack"), ("RubyGems", "devise"),
    ("RubyGems", "sprockets"), ("RubyGems", "nokogiri"),
]

_GH_COMMIT_RE = re.compile(r"https://github\.com/([^/]+)/([^/]+)/commit/([0-9a-f]{7,40})")


def _api_file_contents(owner: str, repo: str, ref: str, path: str, cache: Path) -> bytes | None:
    """GitHub contents API 取单文件（base64；走 token 鉴权——raw/codeload 受网络
    抖动时第三路回退，DECISIONS 2026-10-11 行）。失败返回 None（调用方按未取得
    处理，不据此判废）。"""
    data = github_api(f"/repos/{owner}/{repo}/contents/{path}?ref={ref}", cache)
    if not isinstance(data, dict) or data.get("encoding") != "base64":
        return None
    try:
        return base64.decodebytes(data["content"].encode())
    except Exception:  # noqa: BLE001
        return None


def _parent_file_contents(
    owner: str, repo: str, parent_sha: str, files: list[str], cache: Path, *, offline: bool
) -> tuple[dict[str, str] | None, bool]:
    """parent commit 的指定文件全文：raw → codeload tarball → contents API 三路回退。

    返回 (contents | None, definitive)——definitive=True 表示结果确定（全部取到，
    或任一文件确定不存在/二进制不可解码 → None 且调用方可缓存判废）；
    definitive=False 表示网络性失败（调用方**不得**缓存判废，下轮重评——v0.1
    修复：原先 raw/codeload 瞬断被误判废永久损失候选，实证 flask/tornado 等）。"""
    out: dict[str, str] = {}
    for path in files[:MAX_FILES]:
        raw = http_get(
            f"https://raw.githubusercontent.com/{owner}/{repo}/{parent_sha}/{path}",
            cache, offline=offline, retries=3, timeout=30,
        )
        if raw is not None:
            try:
                out[path] = raw.decode("utf-8")
                continue
            except UnicodeDecodeError:
                return None, True       # 二进制文件不入快照（确定性）
        tar = http_get(
            f"https://codeload.github.com/{owner}/{repo}/tar.gz/{parent_sha}",
            cache, offline=offline, retries=2, timeout=120,
        )
        if tar is not None:
            try:
                with tarfile.open(fileobj=io.BytesIO(tar), mode="r:gz") as tf:
                    prefix = tf.getnames()[0].split("/")[0]
                    member = f"{prefix}/{path}"
                    found = [n for n in tf.getnames() if n == member]
                    if not found:
                        return None, True   # 全树在位而无此文件：确定性
                    out[path] = tf.extractfile(found[0]).read().decode("utf-8")
                    continue
            except UnicodeDecodeError:
                return None, True
            except Exception:  # noqa: BLE001
                pass                        # tarball 下载不完整/损坏 → 继续回退
        if offline:
            return None, False
        blob = _api_file_contents(owner, repo, parent_sha, path, cache)
        if blob is not None:
            try:
                out[path] = blob.decode("utf-8")
                continue
            except UnicodeDecodeError:
                return None, True
        return None, False                  # 三路皆败 → 网络性失败，不判废
    return out, True


def vuln_retired(cve_ids: list[str]) -> bool:
    """v0.3 retire 排除判定：公告任一 CVE alias 命中排除表即弃。

    在 aliases 面拦截（OSV 的 primary id 可能是 GHSA，CVE 在 aliases 任意位），
    缓存命中与新鲜取材两条路同覆盖（判定先于缓存查找）。"""
    return any(c in RETIRED_CVES for c in cve_ids)


def fetch_cve_items(
    cache: Path, *, offline: bool, family_need: dict[str, int], rng: random.Random,
    api_budget: int,
) -> list[dict]:
    """CVE 回滚取材（OSV → GitHub fix commit → parent 快照）。

    返回 material 列表 [{family, cve, parent_sha, commit_date, files:{path: text}}]。
    逐源可失败（外网尽力取材）；api_budget 限 GitHub API 调用数（鉴权 5000/h）。
    family_need = 逐家族需求（§1.2 配额 CVE 列）：逐家族计数达标后该家族候选跳过
    （有效记录仍入缓存供复用），全部家族达标或预算/包池耗尽即止。同一 fix commit
    被多个 advisory 引用时按缓存键去重（一份快照只入池一次——v0.1 修复：原先缓存
    命中重复入池挤占配额坑位）。commit 查询失败（限流/网络）不入缓存、下轮重评
    （v0.1 修复：原先误写"判废 null"永久损失候选）。"""
    items: list[dict] = []
    used_api = 0
    fam_count: dict[str, int] = {f: 0 for f in family_need}
    seen: set[str] = set()

    def _met() -> bool:
        return all(fam_count[f] >= n for f, n in family_need.items())

    for ecosystem, package in CVE_PACKAGES:
        if used_api >= api_budget or _met():
            break
        body = json.dumps({"package": {"name": package, "ecosystem": ecosystem}}).encode()
        # POST 形态不进 GET 缓存——直连（OSV 无鉴权、限速宽松）
        try:
            req = urllib.request.Request(
                "https://api.osv.dev/v1/query", data=body,
                headers={"Content-Type": "application/json", **UA},
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                vulns = json.loads(r.read()).get("vulns", [])
        except Exception as exc:  # noqa: BLE001（外网段尽力取材，逐源可失败）
            print(f"  [osv-fail] {ecosystem}/{package}: {type(exc).__name__}")
            continue
        for vuln in vulns:
            if used_api >= api_budget:
                break
            cve_ids = [a for a in (vuln.get("aliases") or []) if a.startswith("CVE-")]
            if not cve_ids:
                continue
            # v0.3 retire 排除（2026-10-12 拍板）：BROKEN_FAMILY 四道不再入池。
            if vuln_retired(cve_ids):
                continue
            # 公告发布日期预过滤（v0.1 修复⑥）：fix commit 几乎总不晚于公告发布，
            # published < 截止线前 3 个月的公告不可能产出 post-cutoff 修复——跳过
            # 以省 API（实证：axios/django 候选 11/12 死于 2012-2021 日期检查）。
            # 精确判废仍由 commit 日期逐条把关；留 3 个月边际覆盖滞后披露。
            pub = (vuln.get("published") or "")[:10]
            if pub and pub < "2025-01-01":
                continue
            # FIX 引用中的 GitHub commit（确定性取首个）
            commit_url = next(
                (r["url"] for r in vuln.get("references", [])
                 if r.get("type") == "FIX" and _GH_COMMIT_RE.match(r["url"])),
                None,
            )
            if commit_url is None:
                continue
            m = _GH_COMMIT_RE.match(commit_url)
            owner, repo, fix_sha = m.group(1), m.group(2), m.group(3)
            cache_key = f"cve:{owner}/{repo}@{fix_sha}"
            cache.joinpath("cve").mkdir(parents=True, exist_ok=True)
            cached = cache / "cve" / f"{hashlib.sha256(cache_key.encode()).hexdigest()}.json"
            if cached.exists():
                record = json.loads(cached.read_text(encoding="utf-8"))
                if (
                    record is not None and cache_key not in seen
                    and fam_count.get(record["family"], 0) < family_need.get(record["family"], 0)
                ):
                    seen.add(cache_key)
                    fam_count[record["family"]] += 1
                    items.append(record)
                continue
            if offline:
                continue
            commit = github_api(f"/repos/{owner}/{repo}/commits/{fix_sha}", cache)
            used_api += 1
            if not commit:
                continue            # 查询失败（限流/网络）：不入缓存，下轮重评
            if not commit.get("parents"):
                cached.write_text("null")
                continue            # 根提交：确定性判废
            parent_sha = commit["parents"][0]["sha"]
            date = (commit.get("commit", {}).get("committer", {}) or {}).get("date", "")
            if not date or date[:10] <= KNOWLEDGE_CUTOFF:
                cached.write_text("null")   # 原版直通红线：fix commit 必须晚于知识截止线
                continue
            changed = [f["filename"] for f in commit.get("files", [])]
            code_files = [
                f for f in changed
                if Path(f).suffix in _LANG_BY_EXT and not f.endswith((".md", ".json", ".yml", ".yaml", ".txt"))
            ]
            if not code_files or len(code_files) > MAX_FILES:
                cached.write_text("null")
                continue
            contents, definitive = _parent_file_contents(owner, repo, parent_sha, code_files, cache, offline=offline)
            if contents is None or any(
                t.count("\n") + 1 > MAX_LINES or not t.strip() for t in contents.values()
            ):
                if definitive:
                    cached.write_text("null")   # 确定性判废；网络性失败不缓存（下轮重评）
                continue
            # 家族归属：签名命中最多的家族（跨家族 tie 取表序；无命中 → 弃题）
            all_text = "\n".join(contents.values())
            scores = {
                fam: sum(1 for t in tokens if t in all_text)
                for fam, tokens in FAMILY_SIGNATURES.items()
            }
            family = max(scores, key=lambda k: (scores[k], -list(scores).index(k)))
            if scores[family] == 0:
                cached.write_text("null")
                continue
            record = {
                "kind": "cve_rollback",
                "cve": cve_ids[0],
                "owner": owner, "repo": repo,
                "parent_sha": parent_sha,
                "commit_date": date[:10],
                "family": family,
                "lang": _LANG_BY_EXT[Path(code_files[0]).suffix],
                "files": contents,
            }
            cached.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            if fam_count.get(family, 0) < family_need.get(family, 0):
                fam_count[family] += 1
                seen.add(cache_key)
                items.append(record)
                print(f"  [cve-rollback] {cve_ids[0]} {owner}/{repo}@{parent_sha[:8]} → {family}")
    return items


# ── 源二/三：Juliet / OWASP（本地稀疏克隆或缓存）──────────────────────────


def _read_clone(base: Path, rel: str) -> str | None:
    p = base / rel
    if p.exists():
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
    return None


def load_juliet_material(clone: Path | None, cache: Path, *, offline: bool) -> list[dict]:
    """Juliet C bad/good 用例对（*_bad.c → real 面；*_good*.c → fp 面）。"""
    material: list[dict] = []
    if clone is None or not clone.exists():
        return material
    tc = clone / "testcases"
    for cwe_dir, family in JULIET_C_DIRS.items():
        d = tc / cwe_dir
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            if not sub.is_dir():
                continue
            bad = sorted(sub.glob("*_bad.c")) or sorted(sub.glob("*_bad.cpp"))
            good = sorted(sub.glob("*_good*.c")) or sorted(sub.glob("*_good*.cpp"))
            for face, expected in (("bad", "real"), ("good", "fp")):
                for f in (bad if face == "bad" else good)[:1]:
                    text = _read_clone(f.parent, f.name)
                    if text is None or not text.strip():
                        continue
                    if text.count("\n") + 1 > MAX_LINES:
                        continue
                    # v0.3 取材面 taint 存活校验（bad/good 两面同拦，2026-10-12
                    # 答案键缺陷修复：CWE78 exec* 空壳模板整族在此被拒）
                    if not taint_param_live(text):
                        print(f"  [drop] taint 存活未过（污点参数未进函数体）：{cwe_dir}/{sub.name}/{f.name}")
                        continue
                    material.append(
                        {"kind": "juliet_mutation", "family": family, "expected": expected,
                         "lang": "c", "origin": f"juliet-c/{cwe_dir}/{sub.name}/{f.name}",
                         "text": text}
                    )
    return material


def load_juliet_java_material(zip_path: Path | None) -> list[dict]:
    """Juliet Java（NIST v1.3 官方 zip）CWE502 反序列化用例对——deser 家族的
    Juliet/OWASP 源（C/C++/C# 套件均无 CWE502；OWASP Benchmark 无 deser 类目）。
    bad → real 面；good → fp 面（Juliet 漏洞面/修复面用例对，§2 取材②）。"""
    import zipfile

    material: list[dict] = []
    if zip_path is None or not zip_path.exists():
        return material
    with zipfile.ZipFile(zip_path) as zf:
        for name in sorted(zf.namelist()):
            if "/CWE502" not in name or not name.endswith(".java"):
                continue
            base = name.split("/")[-1]
            if base.startswith("_") or "(" in base or "$" in base:
                continue
            expected = "fp" if "_good" in base else ("real" if "_bad" in base else None)
            if expected is None:
                continue
            try:
                text = zf.read(name).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            if not text.strip() or text.count("\n") + 1 > MAX_LINES:
                continue
            # v0.3 取材面 taint 存活校验（与 Juliet C 面同规则）
            if not taint_param_live(text):
                print(f"  [drop] taint 存活未过（污点参数未进函数体）：juliet-java13/{base}")
                continue
            material.append(
                {"kind": "juliet_mutation", "family": "deser", "expected": expected,
                 "lang": "java", "origin": f"juliet-java13/{base}", "text": text}
            )
    return material


def load_owasp_material(clone: Path | None, cache: Path, *, offline: bool) -> list[dict]:
    """OWASP Benchmark 用例对（expectedresults-1.2.csv expected true/false 对）。"""
    material: list[dict] = []
    if clone is None or not clone.exists():
        return material
    csv_path = clone / "expectedresults-1.2.csv"
    if not csv_path.exists():
        return material
    tc = clone / "src" / "main" / "java" / "org" / "owasp" / "benchmark" / "testcode"
    for line in csv_path.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 4:
            continue
        testcase, category, expected = parts[0], parts[1], parts[2]
        # 列序（CSV 头逐字）：# test name, category, real vulnerability, cwe, …
        family = OWASP_CATEGORY_FAMILY.get(category.strip().lower())
        if family is None:
            continue
        java = f"{testcase.strip()}.java"
        text = _read_clone(tc, java)
        if text is None or not text.strip() or text.count("\n") + 1 > MAX_LINES:
            continue
        material.append(
            {"kind": "owasp_mutation", "family": family,
             "expected": "real" if expected.strip().lower() == "true" else "fp",
             "lang": "java", "origin": f"owasp-benchmark/{java}", "text": text}
        )
    return material


# ── 定稿：配额装配 → item 落盘 → bank.yaml ────────────────────────────────


def _write_item(
    items_dir: Path, seq: int, *, family: str, expected: str, planted: bool,
    lang: str, source: str, files: dict[str, str], seed: int, version: int = 1,
    upstream: dict | None = None, mutation: dict | None = None,
) -> dict:
    item_id = f"SEED-{seq:04d}"
    # canary 8 位 hex 是 §1.4 契约（bank._CANARY_RE 逐字）——derive_seed 为 64 位，
    # 须掩码 32 位（与上方变异横幅同一处理；v0.2 首次走到段三暴露，DECISIONS 同日行）
    canary = f"qsw-canary-{item_id}-{derive_seed(seed, 'canary', item_id) & 0xffffffff:08x}"
    item_dir = items_dir / item_id
    snap = item_dir / "snapshot"
    snap.mkdir(parents=True, exist_ok=True)
    rng = random.Random(derive_seed(seed, "canary-place", item_id))
    names = []
    for i, (name, text) in enumerate(sorted(files.items())):
        text = inject_canary(text, canary, lang=lang, rng=rng)
        fname = Path(name).name or f"file{i}.txt"
        while fname in names:
            fname = f"f{i}_{fname}"
        names.append(fname)
        (snap / fname).write_text(text, encoding="utf-8")
    payload = {
        "item_id": item_id,
        "taxonomy_path": [family],
        "lang": lang,
        "repo_snapshot_ref": f"artifact://seeds/{item_id}/",
        "planted": planted,
        "expected": expected,
        "difficulty": ("easy", "medium", "hard")[seq % 3],
        "version": version,
        "canary": canary,
        "source": source,
        "snapshot_files": names,
    }
    if upstream:
        payload["upstream"] = upstream
    if mutation:
        payload["mutation"] = mutation
    (item_dir / "item.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return payload


def build_bank(out: Path, cache: Path, *, offline: bool, family_quota_only: bool,
               juliet_clone: Path | None, owasp_clone: Path | None,
               juliet_java_zip: Path | None = None) -> None:
    """三段式主流程：取材 → 变异 → 定稿（validate_bank 全绿才签发 bank.yaml）。"""
    out = Path(out)
    items_dir = out / "items"
    if out.exists() and (out / "bank.yaml").exists():
        print(f"[skip] {out}/bank.yaml 已存在（重跑请先清空 out 目录）")
        return
    # 半成品清理：无 bank.yaml 的旧 items/ 全清（确定性重建，不留脏题）
    if items_dir.exists():
        import shutil as _shutil

        _shutil.rmtree(items_dir)
    items_dir.mkdir(parents=True, exist_ok=True)
    master_seed = 0

    # 家族配额装配（SPEC-6 §1.2 v0.2 表 + v0.3 修订，见 V03_PLAN 注）；
    # plan 须在段一之前——CVE 取材按其 cve 列逐家族计量（family_need）。
    plan = V03_PLAN

    print("== 段一：CVE 回滚取材（OSV → GitHub parent 快照）==")
    cve_material = fetch_cve_items(
        cache, offline=offline,
        family_need={fam: q["cve"] for fam, q in plan.items()},
        rng=random.Random(derive_seed(master_seed, "cve-order")),
        api_budget=2000 if _GITHUB_TOKEN else 45,
    )
    print(f"  CVE 回滚产出：{len(cve_material)}")

    print("== 段二：Juliet / OWASP 取材 ==")
    juliet = load_juliet_material(juliet_clone, cache, offline=offline)
    juliet_java = load_juliet_java_material(juliet_java_zip)
    owasp = load_owasp_material(owasp_clone, cache, offline=offline)
    print(f"  Juliet C 用例面：{len(juliet)}；Juliet Java CWE502：{len(juliet_java)}；"
          f"OWASP 用例面：{len(owasp)}")

    source_counts: dict[str, int] = {}
    payloads: list[dict] = []
    seq = 0
    by_family_cve: dict[str, list[dict]] = {}
    shortfalls: list[str] = []
    for rec in cve_material:
        by_family_cve.setdefault(rec["family"], []).append(rec)

    def take_pool(pool: list[dict], n: int, label: str) -> list[dict]:
        got = pool[:n]
        if len(got) < n:
            shortfalls.append(f"{label}: {len(got)}/{n}")
        return got

    jo_by_family: dict[str, dict[str, list[dict]]] = {}
    for rec in juliet + juliet_java + owasp:
        jo_by_family.setdefault(rec["family"], {}).setdefault(rec["expected"], []).append(rec)

    for family, quota in plan.items():
        # ① CVE 回滚真（原样快照，不变异）
        for rec in take_pool(by_family_cve.get(family, []), quota["cve"], f"{family}/cve_rollback"):
            seq += 1
            payloads.append(_write_item(
                items_dir, seq, family=family, expected="real", planted=False,
                lang=rec["lang"], source="cve_rollback", files=rec["files"],
                seed=master_seed + seq,
                upstream={"cve": rec["cve"], "parent_sha": rec["parent_sha"],
                          "commit_date": rec["commit_date"]},
            ))
            source_counts["cve_rollback"] = source_counts.get("cve_rollback", 0) + 1
        # ② Juliet/OWASP 真（变异；expected 复核不过弃题并顺延下一候选——弃题不占
        #    配额坑，v0.1 修复：原先 take_pool 截前 n 个、弃题静默缩编）
        jo_real = jo_by_family.get(family, {}).get("real", [])
        wrote_real = 0
        for rec in jo_real:
            if wrote_real >= quota["jo_real"]:
                break
            seq += 1
            mutated = mutate_code(rec["text"], family=family, seed=master_seed + seq, lang=rec["lang"])
            if not expected_recheck(mutated, family):
                print(f"  [drop] expected 复核未过：{rec['origin']}")
                seq -= 1
                continue
            payloads.append(_write_item(
                items_dir, seq, family=family, expected="real", planted=True,
                lang=rec["lang"], source=rec["kind"], files={"source" + Path(rec["origin"]).suffix: mutated},
                seed=master_seed + seq,
                mutation={"kinds": ["rename", "reorder", "dead_code", "restructure"],
                          "seed": master_seed + seq},
            ))
            source_counts[rec["kind"]] = source_counts.get(rec["kind"], 0) + 1
            wrote_real += 1
        if wrote_real < quota["jo_real"]:
            shortfalls.append(f"{family}/jo_real: {wrote_real}/{quota['jo_real']}")
        # ③ 变异雷（安全面 → fp；弃题回补同 ②）
        jo_fp = jo_by_family.get(family, {}).get("fp", [])
        wrote_fp = 0
        for rec in jo_fp:
            if wrote_fp >= quota["fp"]:
                break
            seq += 1
            mutated = mutate_code(rec["text"], family=family, seed=master_seed + seq, lang=rec["lang"])
            if not expected_recheck(mutated, family):
                print(f"  [drop] expected 复核未过：{rec['origin']}")
                seq -= 1
                continue
            payloads.append(_write_item(
                items_dir, seq, family=family, expected="fp", planted=True,
                lang=rec["lang"], source=rec["kind"], files={"source" + Path(rec["origin"]).suffix: mutated},
                seed=master_seed + seq,
                mutation={"kinds": ["rename", "reorder", "dead_code", "restructure"],
                          "seed": master_seed + seq},
            ))
            source_counts[rec["kind"]] = source_counts.get(rec["kind"], 0) + 1
            wrote_fp += 1
        if wrote_fp < quota["fp"]:
            shortfalls.append(f"{family}/fp: {wrote_fp}/{quota['fp']}")

    if shortfalls or len(payloads) != 100:
        # 如实报缺（SPEC-5 M2 卷 §9.2 停止判定 5：不得伪造来源补额）。缺口清单
        # 落盘供负责人决策：补外网取材资源重跑（缓存优先），或拍板配额表修订
        # 走 SPEC 修订流程。
        gap = {
            "verdict": "insufficient_material",
            "shortfalls": shortfalls,
            "built_items": len(payloads),
            "required_items": 100,
            "source_counts": source_counts,
            "note": "取材产出不足（cve_rollback post-cutoff 产出不足为主）；"
                    "不伪造来源、不放宽红线（SPEC-6 §1.1-4/§1.2 v0.1）",
        }
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / ".build-shortfall.json").write_text(
            json.dumps(gap, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        raise SystemExit(
            "[stop] 取材产出不足（如实报缺，不伪造）：\n  "
            + "\n  ".join(shortfalls)
            + f"\n  已建 {len(payloads)}/100 题；缺口清单 {(Path(out) / '.build-shortfall.json')}"
        )

    print("== 段三：bank.yaml 签发 + 校验器全绿 ==")
    bank_meta = {
        "bank_version": BANK_VERSION,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "items": [
            {"item_id": p["item_id"], "sha256": dir_sha256(items_dir / p["item_id"])}
            for p in payloads
        ],
        "source_counts": source_counts,
        "quota_mode": "family" if family_quota_only else "v0-full",
    }
    (out / "bank.yaml").write_text(
        json.dumps(bank_meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    bank = load_bank(out / "bank.yaml")
    validate_bank(bank)
    print(
        f"[done] bank_version={BANK_VERSION} items={len(bank.items)} quota={bank.quota_table()} "
        f"sources={source_counts}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="build_seeds", description="种子题库 v0 三源构建流水线（SPEC-6 §2）")
    parser.add_argument("--out", default="seeds", help="题库输出目录（seeds/）")
    parser.add_argument("--cache", default=".cache-seeds", help="网络缓存目录")
    parser.add_argument("--offline", action="store_true", help="纯缓存重放（CI 形态）")
    parser.add_argument("--family-quota", action="store_true",
                        help="家族级配额降级模式（逐源缺口如实记 bank.yaml.source_counts）")
    parser.add_argument("--check", action="store_true", help="只校验现库")
    args = parser.parse_args(argv)

    if args.check:
        bank = load_bank(Path(args.out) / "bank.yaml")
        validate_bank(bank)
        print(f"[check] bank_version={bank.bank_version} items={len(bank.items)} OK")
        return 0

    juliet_clone = Path("/tmp/juliet-c") if Path("/tmp/juliet-c/testcases").exists() else None
    owasp_clone = Path("/tmp/owasp-bench") if Path("/tmp/owasp-bench/expectedresults-1.2.csv").exists() else None
    juliet_java_zip = Path("/tmp/juliet-java13.zip") if Path("/tmp/juliet-java13.zip").exists() else None
    build_bank(
        Path(args.out), Path(args.cache),
        offline=args.offline, family_quota_only=args.family_quota,
        juliet_clone=juliet_clone, owasp_clone=owasp_clone,
        juliet_java_zip=juliet_java_zip,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
