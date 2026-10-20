"""M4 E 组联调探针（2026-10-19）：请求名 vs 实际服务模型。

纪律（M4_DELIVERY_REPORT §4-①）：4–8 次调用；判据 = 响应 model 字段实测。
覆盖：帽蜂请求名（glm-4.5-air / glm-4.6）、新 reviewer 主备（glm-5.3-flash）、
strong/engineering 拟换档名（同为 5.3-flash，抽查一次）、旗舰对照（glm-5.3，
记录为何全 flash 规避）+ 考试运行点抽查（5.3-flash × thinking enabled）。

key 只从 QSWARM_KEY_ZHIPU 环境变量读（§11.6-5：值不入日志/输出）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request

BASE = "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
OUT = "out/m4-seeding-rv/probe_results.json"

PROBES = [
    # (label, model, thinking, max_tokens)
    ("hat_air", "glm-4.5-air", "disabled", 64),
    ("hat_4_6", "glm-4.6", "disabled", 64),
    ("mid_flash", "glm-5.3-flash", "disabled", 64),
    ("flagship_probe", "glm-5.3", "disabled", 64),
    ("runpoint_flash_thinking", "glm-5.3-flash", "enabled", 2048),
]


def probe(label: str, model: str, thinking: str, max_tokens: int) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "回复一个字：好"}],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "thinking": {"type": thinking},
    }
    req = urllib.request.Request(
        BASE,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.environ['QSWARM_KEY_ZHIPU']}",
        },
        method="POST",
    )
    t0 = time.monotonic()
    rec: dict = {"label": label, "requested": model, "thinking": thinking,
                 "max_tokens": max_tokens}
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        rec["http"] = "ok"
        rec["served_model"] = data.get("model")
        ch = (data.get("choices") or [{}])[0]
        rec["finish"] = ch.get("finish_reason")
        msg = ch.get("message") or {}
        rec["content_chars"] = len(msg.get("content") or "")
        rec["reasoning_chars"] = len(msg.get("reasoning_content") or "")
        rec["usage"] = data.get("usage")
    except urllib.error.HTTPError as e:
        rec["http"] = f"HTTP {e.code}"
        rec["body"] = e.read().decode(errors="replace")[:500]
    except Exception as e:  # noqa: BLE001 — 探针侧记录一切失败形态
        rec["http"] = f"ERROR {type(e).__name__}: {e}"
    rec["latency_s"] = round(time.monotonic() - t0, 2)
    return rec


def main() -> int:
    key = os.environ.get("QSWARM_KEY_ZHIPU")
    if not key:
        print("QSWARM_KEY_ZHIPU 未设置", file=sys.stderr)
        return 2
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    results = [probe(*p) for p in PROBES]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    for r in results:
        if r.get("http") == "ok":
            print(f"{r['label']:<24} requested={r['requested']:<14} "
                  f"served={r.get('served_model'):<14} finish={r.get('finish'):<8} "
                  f"content={r.get('content_chars')}ch reasoning={r.get('reasoning_chars')}ch "
                  f"{r['latency_s']}s")
        else:
            print(f"{r['label']:<24} requested={r['requested']:<14} {r['http']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
