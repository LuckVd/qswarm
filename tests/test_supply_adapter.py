"""supply.adapter 测试（SPEC-6 §5.2；SPEC-5 M2 卷 §1.2/§6.2 判据）。

全部断言经注入 opener 离线完成（D29：构造参数 opener，缺省 urllib.request.urlopen）；
钉死：构造不读 env、complete() 时才取 api_key_env、openai_compatible 线格式
（URL/方法/体结构、messages 直传、temperature 逐字、输出 4096 封顶）、anthropic
线格式 system 合并、非 2xx/超时 → AdapterError 不含 key、RPM 最小间隔、import
根白名单 AST 钉死（全仓唯一网络落点的逐文件豁免口径）。
"""

from __future__ import annotations

import ast
import io
import json
import time
import urllib.error

import pytest

from qswarm.prompts import Message
from qswarm.supply import AdapterError, UrllibAdapter, load_models

from m2_harness import MODELS_YAML

KEY = "sk-m2-test-secret-123"


class FakeResp(io.BytesIO):
    def close(self):  # 保持 BytesIO 语义即可
        super().close()


def _fake_opener(capture: dict, payload: dict, status: int = 200):
    def opener(req, timeout=None):
        capture["url"] = req.full_url
        capture["method"] = req.get_method()
        capture["headers"] = {k.lower(): v for k, v in req.header_items()}
        capture["body"] = json.loads(req.data.decode("utf-8"))
        if status != 200:
            raise urllib.error.HTTPError(req.full_url, status, "boom", hdrs=None, fp=io.BytesIO(b""))
        return FakeResp(json.dumps(payload).encode("utf-8"))

    return opener


@pytest.fixture()
def cfg(tmp_path):
    p = tmp_path / "models.yaml"
    p.write_text(MODELS_YAML, encoding="utf-8")
    return load_models(p)


def test_adapter_lazy_key_lookup(cfg, monkeypatch):
    """构造不读 env；complete() 时才取 api_key_env（缺 env → AdapterError，值不入消息）。"""
    monkeypatch.delenv("M2_TEST_KEY_CHEAP", raising=False)
    capture: dict = {}
    adapter = UrllibAdapter(cfg, opener=_fake_opener(capture, {"choices": [{"message": {"content": "ok"}}]}))
    assert "M2_TEST_KEY_CHEAP" not in capture      # 构造零 env 读取、零调用
    monkeypatch.setenv("M2_TEST_KEY_CHEAP", KEY)
    out = adapter.complete(
        model_ref="cheap:deepseek/deepseek-chat",
        messages=[Message(role="user", content="hi")],
        temperature=0.0,
    )
    assert out == "ok"
    # 缺 env 的档位 → AdapterError 且不泄漏
    with pytest.raises(AdapterError) as ei:
        adapter.complete(
            model_ref="strong:anthropic/claude-sonnet-4-5",
            messages=[Message(role="user", content="x")],
            temperature=0.0,
        )
    assert "M2_TEST_KEY_STRONG" in str(ei.value) and KEY not in str(ei.value)


def test_adapter_openai_compatible_wire(cfg, monkeypatch):
    """fake opener 下请求 URL/方法/体结构符合 chat/completions 族；messages 直传；
    temperature 逐字透传；max_tokens=4096（输出上限）；Authorization: Bearer <key>。"""
    monkeypatch.setenv("M2_TEST_KEY_CHEAP", KEY)
    monkeypatch.setenv("M2_TEST_KEY_OPENAI", KEY)
    capture: dict = {}
    adapter = UrllibAdapter(cfg, opener=_fake_opener(capture, {"choices": [{"message": {"content": "yo"}}]}))
    out = adapter.complete(
        model_ref="cheap:deepseek/deepseek-chat",
        messages=[Message(role="system", content="sys"), Message(role="user", content="u1")],
        temperature=0.3,
    )
    assert out == "yo"
    assert capture["url"] == "https://api.deepseek.com/chat/completions"
    assert capture["method"] == "POST"
    body = capture["body"]
    assert body["model"] == "deepseek-chat"
    assert body["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "u1"}]
    assert body["temperature"] == 0.3
    assert body["max_tokens"] == 4096
    auth = capture["headers"].get("authorization")
    assert auth == f"Bearer {KEY}"
    # openai 供应商（openai_compatible 族另一成员）走同族线格式
    capture.clear()
    adapter.complete(
        model_ref="cheap:openai/gpt-4o-mini",
        messages=[Message(role="user", content="x")],
        temperature=0.0,
    )
    assert capture["url"] == "https://api.openai.com/v1/chat/completions"
    # 反查失败形态
    for bad in ("nope:deepseek/deepseek-chat", "cheap:deepseek/not-a-model", "cheap:deepseek/deepseek-chat@x"):
        with pytest.raises(AdapterError, match="反查失败|形态非法"):
            adapter.complete(model_ref=bad, messages=[], temperature=0.0)


def test_adapter_anthropic_wire_system_merge(cfg, monkeypatch):
    """多条 system Message 拼接为单 system 参数；user 直传；x-api-key 头 +
    anthropic-version；URL = {base}/v1/messages。"""
    monkeypatch.setenv("M2_TEST_KEY_STRONG", KEY)
    capture: dict = {}
    adapter = UrllibAdapter(cfg, opener=_fake_opener(capture, {"content": [{"type": "text", "text": "hey"}]}))
    out = adapter.complete(
        model_ref="strong:anthropic/claude-sonnet-4-5",
        messages=[
            Message(role="system", content="s1"),
            Message(role="system", content="s2"),
            Message(role="user", content="u"),
        ],
        temperature=0.0,
    )
    assert out == "hey"
    assert capture["url"] == "https://api.anthropic.com/v1/messages"
    body = capture["body"]
    assert body["system"] == "s1\n\ns2"
    assert body["messages"] == [{"role": "user", "content": "u"}]
    assert body["temperature"] == 0.0 and body["max_tokens"] == 4096
    assert capture["headers"].get("x-api-key") == KEY
    assert "anthropic-version" in capture["headers"]


def test_adapter_error_no_key_leak(cfg, monkeypatch):
    """非 2xx/超时/响应缺产出 → AdapterError；异常消息与 __str__ 不含 key 值。"""
    monkeypatch.setenv("M2_TEST_KEY_CHEAP", KEY)
    capture: dict = {}
    # 非 2xx
    adapter = UrllibAdapter(cfg, opener=_fake_opener(capture, {}, status=503))
    with pytest.raises(AdapterError) as ei:
        adapter.complete(model_ref="cheap:deepseek/deepseek-chat", messages=[Message(role="user", content="x")], temperature=0.0)
    assert "503" in str(ei.value) and KEY not in str(ei.value)
    # 超时（TimeoutError 族）
    def timeout_opener(req, timeout=None):
        raise TimeoutError("timed out")

    adapter2 = UrllibAdapter(cfg, opener=timeout_opener)
    with pytest.raises(AdapterError) as ei2:
        adapter2.complete(model_ref="cheap:deepseek/deepseek-chat", messages=[Message(role="user", content="x")], temperature=0.0)
    assert KEY not in str(ei2.value) and "deepseek-chat" in str(ei2.value)
    # 响应缺产出
    adapter3 = UrllibAdapter(cfg, opener=_fake_opener(capture, {"choices": []}))
    with pytest.raises(AdapterError) as ei3:
        adapter3.complete(model_ref="cheap:deepseek/deepseek-chat", messages=[Message(role="user", content="x")], temperature=0.0)
    assert KEY not in str(ei3.value)


def test_adapter_offline_via_injected_opener(cfg, monkeypatch):
    """全部断言经注入 opener 离线完成；RPM 最小间隔在位（60/rpm_limit）；
    本用例文件无真实网络调用；adapter.py import 根闭集 AST 钉死（SPEC-5 M2 卷
    §6.2 白名单判据的测试内形态）。"""
    assert cfg.runtime.rpm_limit == 600
    monkeypatch.setenv("M2_TEST_KEY_CHEAP", KEY)
    capture: dict = {}
    adapter = UrllibAdapter(cfg, opener=_fake_opener(capture, {"choices": [{"message": {"content": "a"}}]}))
    assert adapter._min_interval_s == pytest.approx(60 / 600)
    t0 = time.monotonic()
    adapter.complete(model_ref="cheap:deepseek/deepseek-chat", messages=[Message(role="user", content="1")], temperature=0.0)
    adapter.complete(model_ref="cheap:deepseek/deepseek-chat", messages=[Message(role="user", content="2")], temperature=0.0)
    assert time.monotonic() - t0 >= 0.09          # 两连调被最小间隔节流

    # import 根白名单（网络判据修订口径：urllib.request/urllib.error/urllib.parse/json/
    # os/time/dataclasses/typing —— 相对导入不在此列）
    allowed = {"urllib.request", "urllib.error", "urllib.parse", "json", "os", "time",
               "dataclasses", "typing"}
    src = open("qswarm/supply/adapter.py", encoding="utf-8").read()
    roots = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module)
    assert roots <= allowed, roots - allowed
    # 网络词根 grep（修订口径）：全仓零命中，豁免 supply/adapter.py
    import subprocess
    import sys as _sys
    proc = subprocess.run(
        [_sys.executable, "-c",
         "import pathlib,re,sys\n"
         "pat = re.compile(r'\\b(socket|ssl|urllib|urllib3|http\\.client|httpx|requests|aiohttp|ftplib|smtplib|telnetlib)\\b')\n"
         "hits = []\n"
         "for p in pathlib.Path('qswarm').rglob('*.py'):\n"
         "    if '__pycache__' in p.parts or str(p).replace('\\\\','/') == 'qswarm/supply/adapter.py':\n"
         "        continue\n"
         "    for i, ln in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):\n"
         "        if pat.search(ln):\n"
         "            hits.append(f'{p}:{i}:{ln.strip()}')\n"
         "print('\\n'.join(hits))\n"],
        capture_output=True, text=True, cwd=".",
    )
    assert proc.stdout.strip() == "", proc.stdout


def test_adapter_thinking_and_budget_from_config(tmp_path, monkeypatch):
    """配置钉住思考模式与输出预算（2026-10-12 DECISIONS）：max_tokens/thinking
    键入体、超时走配置值；未钉档位不发 thinking 键（openai 兼容族；参考形态
    缺省仍 4096 无 thinking，见 test_adapter_openai_compatible_wire）。"""
    tuned = MODELS_YAML.replace(
        "runtime: {rpm_limit: 600, production_temp: 0.2}",
        "runtime: {rpm_limit: 600, production_temp: 0.2, exam_max_output_tokens: 32768,"
        " exam_timeout_s: 600, thinking: {cheap: enabled}}",
    )
    p = tmp_path / "models.yaml"
    p.write_text(tuned, encoding="utf-8")
    cfg = load_models(p)
    capture: dict = {}

    def opener(req, timeout=None):
        capture["body"] = json.loads(req.data.decode("utf-8"))
        capture["timeout"] = timeout
        return FakeResp(json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8"))

    monkeypatch.setenv("M2_TEST_KEY_CHEAP", KEY)
    monkeypatch.setenv("M2_TEST_KEY_OPENAI", KEY)
    adapter = UrllibAdapter(cfg, opener=opener)
    adapter.complete(
        model_ref="cheap:deepseek/deepseek-chat",
        messages=[Message(role="user", content="hi")],
        temperature=0.0,
    )
    assert capture["body"]["max_tokens"] == 32768
    assert capture["body"]["thinking"] == {"type": "enabled"}
    assert capture["timeout"] == 600.0
    # 未钉思考的档位（engineering 走 openai 兼容族）→ 不注入 thinking 键
    adapter.complete(
        model_ref="engineering:openai/gpt-4o",
        messages=[Message(role="user", content="hi")],
        temperature=0.0,
    )
    assert "thinking" not in capture["body"]
    assert capture["body"]["max_tokens"] == 32768
