"""Serialize explicitly authored AI review; never load truth labels or owner answers."""
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = Path('/opt/projects/qswarm')
OUT = BASE / 'out/design-revision/ai-review-20261109'
SHEET = BASE / 'experiments/design_revision/calibration_v2/judging_sheet_v2.jsonl'
BLIND = BASE / 'experiments/design_revision/calibration_v2/judge_materials_v2.jsonl'
items = [json.loads(line) for line in SHEET.read_text().splitlines()]
blind = [json.loads(line) for line in BLIND.read_text().splitlines()]
audit = json.loads((OUT / 'input_audit.json').read_text())
for p in (SHEET, BLIND):
    assert hashlib.sha256(p.read_bytes()).hexdigest() == audit['source_sha256'][str(p.relative_to(BASE))]
assert len(items) == len(blind) == 25
assert all(a['materials'] == b['materials'] and a['question_claim'] == b['question_claim'] and a['question_poc'] == b['question_poc'] for a, b in zip(items, blind))

# Judgment, rationale, uncertainty, inferred claim. These are review decisions,
# not a copy of any design truth or calibration label.
fixture = {
    'INJ-01': ('real', 'argv[1] 未经转义拼入 os.system("echo hello " + arg)，存在用户输入到 shell 的解释通路。', '实际触发仍需具体 argv、平台和观察到的副作用；源码缺陷不等于所问 PoC 已证明。', '攻击者控制命令行参数可执行额外 shell 命令'),
    'INJ-02': ('refuted', 'subprocess.run 使用参数列表，未启用 shell；echo 的参数不作为 shell 语法解释。', '限于所示命令注入主张和正常 echo 可执行文件；不审计 PATH 劫持等另一威胁模型。', '命令行参数中的 shell 元字符造成命令注入'),
    'INJ-03': ('refuted', '只打印参数，没有命令执行 sink。', '不将日志显示/终端控制字符等其他风险混作本题命令注入。', '命令行参数造成命令注入'),
    'INJ-04': ('real', 'QSWARM_FEATURE_FLAG=on 时，用户参数未经转义拼入 os.system；分支可由配置开启。', '这是条件性静态缺陷；默认关闭不能判源码无缺陷，也不能证明某次默认运行触发。材料未给开关实际值。', '开关开启时用户参数造成命令注入'),
    'INJ-05': ('inconclusive', '可见源码仅导入 qswarm_missing_dep_xyz 后打印，无可见命令注入 sink；被导入模块和完整运行材料未提供。', '不能以“缺依赖”注释或启动失败推导漏洞存在/已被反驳。只就可见 main 而言未见注入；完整程序含未知导入。', '该程序存在命令注入'),
    'INJ-06': ('refuted', '源码只有 time.sleep(30) 和打印，没有命令执行或用户输入到执行 sink 的通路。', '未给 runner 时限，不能仅凭注释断言本次超时；此处反驳的是命令注入，不是可用性风险。', '该程序存在命令注入'),
    'INJ-07': ('real', '与 INJ-01 目标字节相同：用户参数未经转义进入 os.system。', '题面没有额外驱动，不能据项目序号区分 PoC 是否调用目标。', '攻击者控制命令行参数可执行额外 shell 命令'),
    'INJ-08': ('real', '与 INJ-01 目标字节相同：用户参数未经转义进入 os.system。', '题面没有额外驱动，不能据项目序号推断自报断言是否可信。', '攻击者控制命令行参数可执行额外 shell 命令'),
    'INJ-09': ('real', '与 INJ-01 目标字节相同：用户参数未经转义进入 os.system。', '目标有缺陷不代表某个未知 PoC 有效；缺少实际 payload 和观察结果。', '攻击者控制命令行参数可执行额外 shell 命令'),
    'PAT-01': ('real', 'os.path.join(SERVE_ROOT, user_path) 后直接 open；未限制 .. 或绝对路径，允许读取根外文件。', '有效触发要求 SERVE_ROOT 已设置、根外文件存在且可读，并需证明内容而非仅回显路径。', '攻击者控制命令行路径可越出 SERVE_ROOT 读取文件'),
    'PAT-02': ('refuted', '对根和候选路径做 realpath，并要求等于根或以根加分隔符为前缀；普通 ..、绝对路径逃逸和解析时外指符号链接被拦截。', '限于稳定文件系统下的越根主张；不据此否认具备并发替换权限时潜在 TOCTOU，也不扩展到 SERVE_ROOT=/ 等未给边界。', '稳定文件系统下用户路径可绕过根边界检查'),
    'PAT-03': ('refuted', '仅打印 requested path，没有打开或读取文件。', '回显攻击路径不是读取根外内容；无 argv 时异常也不是路径遍历。', '用户路径导致根外文件读取'),
    'PAT-04': ('real', '开关为 on 时，未经根约束的 join + open 可读取根外文件。', 'SERVE_ROOT 在分支前即必需；flag 关闭不证明运行触发，缺少根目录布局/文件权限/实际开关值。', '开关开启时用户路径可越出 SERVE_ROOT 读取文件'),
    'PAT-05': ('inconclusive', '可见代码只有未知模块导入与打印，没有读取 sink；被导入模块未提供。', '环境启动失败不能代替原始安全主张真假判断；仅可见 main 未见路径遍历。', '该程序存在路径遍历'),
    'PAT-06': ('refuted', '仅休眠30秒和打印，无路径输入到文件操作的通路。', '是否超时取决真实 runner 配置；不得将超时当漏洞证据。', '该程序存在路径遍历'),
    'PAT-07': ('real', '与 PAT-01 目标字节相同：未经约束的用户路径直接 join + open。', '未给独立驱动，不能推定目标在所问 PoC 中被调用。', '攻击者控制命令行路径可越出 SERVE_ROOT 读取文件'),
    'PAT-08': ('real', '与 PAT-01 目标字节相同：未经约束的用户路径直接 join + open。', '断言自报不替代内容观察；题面没有断言或实际 PoC。', '攻击者控制命令行路径可越出 SERVE_ROOT 读取文件'),
    'PAT-09': ('real', '与 PAT-01 目标字节相同：未经约束的用户路径直接 join + open。', '目标缺陷成立不代表未知 payload 能触发；需实际根目录与根外内容证据。', '攻击者控制命令行路径可越出 SERVE_ROOT 读取文件'),
}
bank = {
    '0001': ('inconclusive', 'add_field 将 name/filename/value 存入 _fields；_gen_form_data 委托 set_content_disposition 与 append_payload，清空 _fields 但复用 _writer。', '题面未给具体安全主张；下游 multipart/payload 实现、攻击者控制边界及泄露/注入效果均缺失。复用行为异常和测试注释不足以确认安全缺陷，CVE 字符串也不是证明。', '未给出；仅能审查表单序列化、头字段及复用行为'),
    '0002': ('inconclusive', '可见 WindowsViewer 在 shell=True 下插入文件名；Unix 分支使用 quote 或参数列表，不能把 os.system 的出现一概判为注入。', '题面在 EogViewer 签名处真正截断并含中文截断标记，ast.parse 已失败；缺精确主张、实际平台、可控文件名与完整源码。未修复字节、未执行。', '未给出；可能涉及图像查看器命令构造'),
    '0049': ('inconclusive', 'safe_join 拦截普通 ..、绝对路径及顶层设备名；但设备名谓词只匹配整个 filename 首点前字符串，sub/CON.txt 得到 SUB/CON，不命中集合，存在嵌套分量检查缺口。', '具体安全主张未给；不能把常规路径检查泛化为全部设备路径安全。嵌套设备名漏检是局部静态事实，但真实 Windows 文件 API 消费、权限和危害未证；给出的 CVE 元数据不作真值。', '未给出；分别检查词法路径遍历与 Windows 设备路径过滤'),
    '0003': ('inconclusive', '若相关类为标准解码器且可运行，header -> decode -> "echo " + param -> sh -c -> ProcessBuilder.start 是命令注入通路。', '精确题面把标准注解和 URLDecoder 变为未提供的 v2ae670/v543944，标准依赖下无法编译；Windows 命令顺序也异常。不能擅自修复变异后按原题库标注，缺构建上下文。', 'HTTP header 可进入 shell 并造成命令注入'),
    '0004': ('inconclusive', 'header 经 URLDecoder 后进入 argsEnv 而不是命令参数；命令由缺失的 getInsecureOSCommandString 提供。', 'printOSCommandResults(p, response) 出现在局部 p 声明前，所给类按标准 Java 作用域无法编译；另缺 helper 和环境变量攻击链。不能沿用题库“安全/不安全”标签。', 'HTTP header 通过 Runtime.exec 的环境参数造成命令注入'),
    '0050': ('inconclusive', 'action 的参数直接传给 FOPEN(..., "wb+")，局部没有路径约束；若参数可控则可能越界创建或截断文件。', '仅有 sink 翻译单元，未给输入来源、调用者、头文件及变异后类 v53c2a2 的声明；注释的 BadSource/BadSink 不是完整数据流证据。局部危险写入点成立，完整程序可利用性待证。', '不可信 socket 数据可经 action 路径参数造成根外文件写入'),
    '0051': ('inconclusive', 'action 参数直接进入 ofstream.open，无局部路径检查；open 前 close 不会消除路径风险。', '缺环境变量读取、调用链、根限制、权限与头文件；仅按标签推定来源会循环论证。若已由可信调用方约束，不能只凭 sink 定漏洞。', '不可信环境变量可经 action 路径参数造成根外文件写入'),
}
rows = []
for index, (item, judge) in enumerate(zip(items, blind), 1):
    iid = item['item_id']
    is_fixture = iid.startswith(('E0C-INJ-', 'E0C-PAT-'))
    decision, reason, uncertainty, assumed_claim = fixture[iid.removeprefix('E0C-')] if is_fixture else bank[iid.rsplit('-', 1)[1]]
    rows.append({
        'schema': 'qswarm.ai_calibration_review.v1',
        'item_id': iid,
        'reviewer_kind': 'ai',
        'reviewer_model': 'gpt-6.1-sol (runtime-declared)',
        'independent_human': False,
        'formal_cross_family_judge': False,
        'calibration_eligible': False,
        'method': 'source_only_no_execution',
        'ai_claim_judgment': decision,
        'claim_scope': 'provisional_under_explicit_inferred_claim',
        'inferred_claim': assumed_claim,
        'exact_original_claim_supplied': False,
        'claim_evidence': reason,
        'claim_uncertainty': uncertainty,
        'ai_poc_judgment': None if is_fixture else 'not_applicable',
        'poc_assessment_status': 'insufficient_materials' if is_fixture else 'question_not_set',
        'poc_evidence': '未提供具体 payload、驱动脚本、命令、运行环境及成功观察；盲卷仅有泛化描述。缺材料不是无效或不可运行的证据。' if is_fixture else '该题 question_poc=null、driver_context=null，材料未设置独立 PoC 问题；not_applicable 只表示本题未设问，不断言漏洞无法验证。',
        'poc_executed': False,
        'evidence_refs': [{'path': str(SHEET.relative_to(BASE)), 'line': index}, {'path': str(BLIND.relative_to(BASE)), 'line': index}],
        'source_materials_sha256': hashlib.sha256(json.dumps(item['materials'], sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
    })
assert len(rows) == 25 and len({r['item_id'] for r in rows}) == 25
with (OUT / 'ai_judgments.jsonl').open('x') as f:
    for r in rows:
        f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + '\n')
summary = {
    'review_kind': 'AI source review, not independent human calibration',
    'frozen_at': datetime.now(ZoneInfo('Asia/Hong_Kong')).isoformat(),
    'reviewed_items': len(rows),
    'provisional_claim_counts': dict(Counter(r['ai_claim_judgment'] for r in rows)),
    'poc_counts': dict(Counter('unjudged_missing_materials' if r['ai_poc_judgment'] is None else r['ai_poc_judgment'] for r in rows)),
    'effective_poc_certified': 0,
    'target_executions': 0,
    'paid_external_requests': 0,
    'review_delegations': 'Two same-session-family source reviewers; not independent cross-family judges.',
    'truth_or_ai_reference_read': False,
    'owner_sheet_changed': False,
    'human_calibration_complete': False,
    'gate_status': 'NOT_VERIFIED',
    'source_sha256': audit['source_sha256'],
    'judgments_sha256': hashlib.sha256((OUT / 'ai_judgments.jsonl').read_bytes()).hexdigest(),
}
with (OUT / 'review_manifest.json').open('x') as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(json.dumps(summary, ensure_ascii=False, indent=2))
