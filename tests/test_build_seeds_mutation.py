# v0.3 题库构建校验测试（2026-10-12 答案键缺陷修复）
#
# 覆盖三件：
# ① taint_param_live——取材面污点存活校验（拦 Juliet sources-sink-81 exec* 空壳模板，
#    bad/good 两面同拦；docs/answers_key_audit_20261012.md §2/§6）；
# ② mutate_code dead_code 插入点——c/java 只允许函数体内（此前 39/39 juliet C 题
#    落在文件作用域=非法 C++，OWASP Java 题切开 @Override 与方法声明）；
# ③ rename 注释行跳过——注释 token 不再被改写（Filename: vac27e2.cpp 类外观损伤）。
#
# 构建器在 scripts/build_seeds/（无包结构），测试以路径注入导入。

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_BUILD_DIR = _HERE.parent / "scripts" / "build_seeds"
if str(_BUILD_DIR) not in sys.path:
    sys.path.insert(0, str(_BUILD_DIR))

import build  # noqa: E402  (scripts/build_seeds/build.py)

# ── 夹具：Juliet sources-sink-81 两族的最小形态 ────────────────────────────

VACUOUS_EXEC = """/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81_bad.cpp
*/
#ifndef OMITBAD
#include "std_testcase.h"
namespace CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81
{
void CWE78_OS_Command_Injection__char_listen_socket_w32_spawnv_81_bad::action(char * data) const
{
    {
        char *args[] = {COMMAND_INT_PATH, COMMAND_ARG1, COMMAND_ARG3, NULL};
        /* POTENTIAL FLAW: Execute command without validating input possibly leading to command injection */
        _spawnv(_P_WAIT, COMMAND_INT_PATH, args);
    }
}
}
#endif
"""

LIVE_FOPEN = """/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad.cpp
*/
#ifndef OMITBAD
#include "std_testcase.h"
namespace CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81
{
void CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad::action(char * data) const
{
    {
        FILE *pFile = NULL;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        pFile = FOPEN(data, "wb+");
        if (pFile != NULL)
        {
            fclose(pFile);
        }
    }
}
}
#endif
"""

LOCAL_TAINT = """// 无指针形参的变体（污点为局部变量）——无从判定则不拦
#include "std_testcase.h"
void CWE121_bad(void)
{
    char data[100];
    char dest[50] = "";
    strcpy(dest, data);
}
"""


class TestTaintParamLive:
    def test_vacuous_exec_sink_rejected(self):
        """CWE78 exec* 空壳：data 只在签名，函数体零引用 → 拦。"""
        assert build.taint_param_live(VACUOUS_EXEC) is False

    def test_live_fopen_sink_accepted(self):
        """CWE23 族：data 流入 FOPEN → 放行。"""
        assert build.taint_param_live(LIVE_FOPEN) is True

    def test_local_taint_no_param_accepted(self):
        """无指针/对象形参 → 不拦（最小惊讶）。"""
        assert build.taint_param_live(LOCAL_TAINT) is True

    def test_object_param_java(self):
        """对象形参（juliet-java deser 面）：readObject(data) 引用即活。"""
        live = (
            "public class CWE502_bad {\n"
            "  public void badSink(Object data) throws Exception {\n"
            "    ObjectInputStream ois = (ObjectInputStream) data;\n"
            "    ois.readObject();\n"
            "  }\n"
            "}\n"
        )
        assert build.taint_param_live(live) is True
        vac = (
            "public class CWE502_good {\n"
            "  public void goodSink(Object data) throws Exception {\n"
            "    System.out.println(\"fixed\");\n"
            "  }\n"
            "}\n"
        )
        assert build.taint_param_live(vac) is False


class TestMutateDeadCodeScope:
    def test_c_dead_block_inside_function(self):
        """c：dead 块必须落在函数体内（插入行之前花括号深度 ≥1）。"""
        out = build.mutate_code(LIVE_FOPEN, family="authz", seed=6, lang="c")
        lines = out.splitlines()
        depth = 0
        inside = False
        for ln in lines:
            if "/* dead */" in ln:
                inside = depth >= 1
                break
            depth += ln.count("{") - ln.count("}")
        assert any("/* dead */" in ln for ln in lines), "dead 块必须存在（有函数体落点）"
        assert inside, "dead 块不得落在文件作用域"

    def test_java_dead_block_not_split_annotation(self):
        """java：dead 块不得落在类作用域（@Override 与方法声明之间）。"""
        java_src = (
            "public class BenchmarkTest00087 extends HttpServlet {\n"
            "    @Override\n"
            "    public void doPost(HttpServletRequest request, HttpServletResponse response)\n"
            "            throws ServletException, IOException {\n"
            "        doPost(request, response);\n"
            "    }\n"
            "}\n"
        )
        out = build.mutate_code(java_src, family="injection", seed=10, lang="java")
        lines = out.splitlines()
        idx = next(i for i, ln in enumerate(lines) if "/* dead */" in ln)
        depth = sum(ln.count("{") - ln.count("}") for ln in lines[:idx])
        assert depth >= 2, "java dead 块应在方法体内（类=1、方法=2）"

    def test_no_function_body_no_dead_block(self):
        """无函数体落点（纯声明文件）→ 跳过 dead_code 而非硬插。"""
        decl = "#include <stdio.h>\nextern int g;\n"
        out = build.mutate_code(decl, family="injection", seed=3, lang="c")
        assert "/* dead */" not in out

    def test_python_module_level_unchanged(self):
        """python：模块级合法，仍中点插入。"""
        src = "a = 1\nb = 2\nc = 3\nd = 4\n"
        out = build.mutate_code(src, family="injection", seed=1, lang="python")
        assert "if False:" in out


class TestMutateRenameScope:
    def test_rename_skips_comment_lines(self):
        """注释行不收集不替换：Filename/描述注释保持原文。"""
        out = build.mutate_code(LIVE_FOPEN, family="authz", seed=6, lang="c")
        assert "Filename: CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad.cpp" in out
        assert "/* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */" in out

    def test_rename_deterministic(self):
        """同输入同种子 → 位级一致（S9 前提）。"""
        a = build.mutate_code(LIVE_FOPEN, family="authz", seed=6, lang="c")
        b = build.mutate_code(LIVE_FOPEN, family="authz", seed=6, lang="c")
        assert a == b


class TestMutationPreservesTaint:
    def test_mutation_keeps_sink_taint(self):
        """变异（存活材料）后污点 sink 仍在：rename 一致性回归。"""
        out = build.mutate_code(LIVE_FOPEN, family="authz", seed=6, lang="c")
        # data 或其一致改名（v+hex）形式仍出现在 FOPEN 调用行
        import re
        assert re.search(r"FOPEN\(\s*(data|v[0-9a-f]{6})\s*,", out), (
            "rename 必须全局一致，污点链不得断"
        )


class TestV03RetireAndQuota:
    """v0.3 重建三件套（2026-10-12）：retire 排除表 / bank_version 提升 / 配额联动。

    背景见 docs/answers_key_audit_20261012.md（BROKEN_FAMILY 4 道）与
    docs/spec/DECISIONS.md 2026-10-12 行（SEED-0050~0053 retire，负责人拍板）。"""

    def test_retired_cve_set_literal(self):
        """排除表恰为 audit BROKEN_FAMILY 四道 CVE（字面钉死，防误改）。"""
        assert build.RETIRED_CVES == frozenset({
            "CVE-2026-40192",   # SEED-0050
            "CVE-2026-54059",   # SEED-0051
            "CVE-2026-55380",   # SEED-0052
            "CVE-2026-59203",   # SEED-0053
        })

    def test_vuln_retired_matches_any_alias(self):
        """OSV primary id 可能是 GHSA——CVE 在 aliases 任意位都命中。"""
        assert build.vuln_retired(["CVE-2026-40192"]) is True
        assert build.vuln_retired(["GHSA-xxxx-yyyy", "CVE-2026-54059"]) is True
        assert build.vuln_retired(["CVE-2026-12345"]) is False
        assert build.vuln_retired([]) is False

    def test_v03_plan_matches_v0_quota_exactly(self):
        """plan 逐家族与 bank.V0_QUOTA 精确相等（validate_bank 相等校验的构建侧镜像）。

        v0.3 仅 authz 来源拆分变化：cve 5→1（retire 4 道）、jo_real 7→11 同额回补；
        家族 (real, fp) 配额表不得动。"""
        from qswarm.exams.bank import V0_QUOTA

        assert build.V03_PLAN["authz"] == {"cve": 1, "jo_real": 11, "fp": 8}
        assert set(build.V03_PLAN) == set(V0_QUOTA), "五家族必须齐备（deser 已退出）"
        for fam, q in build.V03_PLAN.items():
            real, fp = V0_QUOTA[fam]
            assert q["cve"] + q["jo_real"] == real, f"{fam} real 拆分不符"
            assert q["fp"] == fp, f"{fam} fp 不符"

    def test_bank_version_bumped_to_2(self):
        """v0.3 重建 bank_version=2：出卷 derive_seed 含 bank_version，不换版本不换卷。"""
        assert build.BANK_VERSION == 2
