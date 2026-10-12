"""supply.adapter：候选模型补全适配层（SPEC-6 §5.2；全仓唯一网络落点，S5/E6）。

stdlib urllib 实现，零 LLM SDK（第三方闭集 {pydantic, yaml, pytest} 不破）。
线格式两族（按 provider 推断，SPEC-6 §5.2）：

- openai_compatible（provider ∈ {deepseek, openai, …}）：POST {base}/chat/
  completions，messages 直传，response_format 不用（提示词已约束 JSON）；
- anthropic（provider = "anthropic"）：POST {base}/v1/messages，system 单列
  （多条 system Message 拼接为单 system 参数）、user 直传；

温度逐字透传；输出上限/超时/思考模式走 ``runtime`` 配置（缺省 4096 封顶——
思考型模型按档位上调，DECISIONS 2026-10-12）。
**传输入口可注入**（D29：构造参数 opener，缺省 urllib.request.urlopen）——测试以
fake opener 离线验证线格式与错误映射，禁真实网络测试（SPEC-5 M2 卷 A 组判据）。

错误纪律（§11.6-5）：网络失败/非 2xx → AdapterError（含 provider/model/状态码，
**不含 key 值**）；api_key_env 只在 complete() 时读取（构造不触 env，值永不入
日志/账本/工件）。重试与 fallback 编排归调用方（考试侧不走 fallback，S6）。
RPM 限速：最小调用间隔 ``60/rpm_limit`` 秒（令牌桶 v0 简化形；播种量级远低于限，
间隔逻辑只为纪律在位）。
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Protocol

from .config import PROVIDER_BASE_URLS, is_anthropic_wire

__all__ = ["AdapterError", "CandidateAdapter", "MAX_OUTPUT_TOKENS", "UrllibAdapter"]

#: 单次补全输出 token 上限（§5.2：考试面足够，防失控账单）。
MAX_OUTPUT_TOKENS = 4096


class AdapterError(RuntimeError):
    """补全调用失败（网络/非 2xx/超时/身份反查失败/响应缺产出/密钥未设置）。

    消息含 provider/model/状态码，**不含 api key 值**（§11.6-5）。RuntimeError
    而非 ValueError：调用失败是环境/服务侧事件，不是值域错误。"""


class CandidateAdapter(Protocol):
    """候选模型补全协议（SPEC-6 §5.2 逐字签名）。

    实现只认 models.yaml 反查到的条目（S6）；网络失败/非 2xx → AdapterError；
    重试与 fallback 编排归调用方（考试侧不走 fallback，§3.2；生产侧 M3 再接）。"""

    def complete(
        self,
        *,
        model_ref: str,
        messages: "list[Message]",
        temperature: float,
        timeout_s: float | None = None,
    ) -> str:
        """一次补全调用：返回模型原文输出（timeout_s 缺省走供给配置）。"""
        ...


class UrllibAdapter:
    """stdlib urllib 实现（全仓唯一网络落点；传输入口 opener 可注入，D29）。"""

    def __init__(
        self,
        models: "ModelsConfig",
        *,
        opener=None,
        timeout_s: float | None = None,
    ) -> None:
        self._models = models
        self._opener = opener if opener is not None else urllib.request.urlopen
        # 超时/输出上限/思考模式走供给配置（DECISIONS 2026-10-12：思考型模型
        # 需要大输出预算与长超时——GLM 5.x 思考通道计入输出预算，4096 封顶时
        # reasoning 烧穿正文得 0 字符；缺省 = M2 定稿旧行为，参考形态零扰动）。
        self._timeout_s = float(
            timeout_s
            if timeout_s is not None
            else getattr(models.runtime, "exam_timeout_s", 120.0)
        )
        self._max_output_tokens = int(
            getattr(models.runtime, "exam_max_output_tokens", MAX_OUTPUT_TOKENS)
        )
        self._thinking: dict[str, str] = dict(
            getattr(models.runtime, "thinking", None) or {}
        )
        self._min_interval_s = 60.0 / max(int(models.runtime.rpm_limit), 1)
        self._last_call_monotonic: float | None = None

    # -- 身份反查与密钥（S6：只认 models.yaml 反查到的条目）────────────

    def _resolve(self, model_ref: str) -> "ModelEntry":
        """model_ref（"{tier}:{provider}/{model}"）→ 唯一 ModelEntry。

        反查失败（形态非法 / 档位不存在 / 条目不在该档 primary+fallbacks 内 /
        多条命中）→ AdapterError。"""
        tier, sep, rest = model_ref.partition(":")
        if not sep or not rest:
            raise AdapterError(
                f"model_ref 形态非法：{model_ref!r}（期望 tier:provider/model）"
            )
        tier_cfg = self._models.tiers.get(tier)
        if tier_cfg is None:
            raise AdapterError(
                f"model_ref {model_ref!r} 反查失败：档位 {tier!r} 不在 models.yaml"
            )
        candidates = [tier_cfg.primary, *tier_cfg.fallbacks]
        hits = [e for e in candidates if f"{e.provider}/{e.model}" == rest]
        if len(hits) != 1:
            raise AdapterError(
                f"model_ref {model_ref!r} 反查失败：命中 {len(hits)} 条（期望唯一条目）"
            )
        return hits[0]

    def _api_key(self, entry: "ModelEntry") -> str:
        """调用时读 env（构造不触；缺 env → AdapterError；值不入错误消息）。"""
        name = entry.api_key_env
        value = os.environ.get(name)
        if not value:
            raise AdapterError(
                f"api_key_env 未设置：provider={entry.provider} model={entry.model}"
                f" env={name}"
            )
        return value

    def _throttle(self) -> None:
        """最小调用间隔（60/rpm_limit 秒；令牌桶 v0 简化形）。"""
        if self._last_call_monotonic is not None:
            elapsed = time.monotonic() - self._last_call_monotonic
            if elapsed < self._min_interval_s:
                time.sleep(self._min_interval_s - elapsed)
        self._last_call_monotonic = time.monotonic()

    # -- 线格式装配 ────────────────────────────────────────────────

    @staticmethod
    def _wire_messages(messages: "list[Message]") -> list[dict[str, str]]:
        return [{"role": m.role, "content": m.content} for m in messages]

    def _openai_body(
        self,
        entry: "ModelEntry",
        messages: "list[Message]",
        temperature: float,
        *,
        tier: str,
    ) -> dict:
        """chat/completions 族请求体：messages 直传、温度逐字、输出上限走配置；
        ``thinking`` 仅在档位被显式钉住时注入（GLM 族扩展参数；anthropic 线格式
        思考形态不同，本层不注入）。"""
        body = {
            "model": entry.model,
            "messages": self._wire_messages(messages),
            "temperature": temperature,
            "max_tokens": self._max_output_tokens,
        }
        mode = self._thinking.get(tier)
        if mode:
            body["thinking"] = {"type": mode}
        return body

    def _anthropic_body(
        self, entry: "ModelEntry", messages: "list[Message]", temperature: float
    ) -> dict:
        """/v1/messages 请求体：多条 system Message 拼接为单 system 参数、user 直传。"""
        system = [m for m in messages if m.role == "system"]
        rest = [m for m in messages if m.role != "system"]
        return {
            "model": entry.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "temperature": temperature,
            "system": "\n\n".join(m.content for m in system),
            "messages": [{"role": m.role, "content": m.content} for m in rest],
        }

    # -- 调用 ─────────────────────────────────────────────────────

    def complete(
        self,
        *,
        model_ref: str,
        messages: "list[Message]",
        temperature: float,
        timeout_s: float | None = None,
    ) -> str:
        """一次补全调用（CandidateAdapter 协议实现）：返回模型原文输出。

        ``timeout_s`` 显式传值覆盖配置；缺省 None → 落 ``runtime.exam_timeout_s``
        （构造缺省 120.0）；网络失败/非 2xx/超时/响应缺产出 →
        AdapterError（不含 key 值）；重试归调用方（S6/S7）。"""
        entry = self._resolve(model_ref)
        key = self._api_key(entry)
        tier = model_ref.partition(":")[0]
        if is_anthropic_wire(entry.provider):
            url = f"{PROVIDER_BASE_URLS[entry.provider]}/v1/messages"
            body = self._anthropic_body(entry, messages, temperature)
            headers = {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            }
        else:
            url = f"{PROVIDER_BASE_URLS[entry.provider]}/chat/completions"
            body = self._openai_body(entry, messages, temperature, tier=tier)
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {key}",
            }
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        self._throttle()
        try:
            resp = self._opener(req, timeout=float(timeout_s) if timeout_s is not None else self._timeout_s)
        except urllib.error.HTTPError as exc:
            raise AdapterError(
                f"adapter 调用失败：provider={entry.provider} model={entry.model}"
                f" status={exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdapterError(
                f"adapter 调用失败：provider={entry.provider} model={entry.model}"
                f" error={type(exc).__name__}"
            ) from exc
        try:
            raw = resp.read()
        finally:
            close = getattr(resp, "close", None)
            if close is not None:
                close()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise AdapterError(
                f"adapter 响应非 JSON：provider={entry.provider} model={entry.model}"
            ) from exc
        return self._extract_text(entry, data)

    @staticmethod
    def _extract_text(entry: "ModelEntry", data: dict) -> str:
        """线格式响应 → 文本产出（chat/completions：choices[0].message.content；
        /v1/messages：content 文本块拼接）。缺产出 → AdapterError。"""
        try:
            if is_anthropic_wire(entry.provider):
                parts = [
                    block.get("text", "")
                    for block in data["content"]
                    if block.get("type") == "text"
                ]
                text = "".join(parts)
            else:
                text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AdapterError(
                f"adapter 响应缺产出：provider={entry.provider} model={entry.model}"
            ) from exc
        if not isinstance(text, str):
            raise AdapterError(
                f"adapter 响应产出非文本：provider={entry.provider} model={entry.model}"
            )
        return text
