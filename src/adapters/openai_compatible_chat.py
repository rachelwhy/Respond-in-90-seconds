"""OpenAI Chat Completions 兼容 HTTP 调用：统一重试、UTF-8 载荷与可选 LiteLLM 短路。

各国内云与自建网关均走 ``POST {base_url}/chat/completions``；``base_url`` 由各厂商在 ``provider_env`` / ``src.config`` 中给出并由 ``normalize_chat_base_url`` 做最小路径修正。
"""

from __future__ import annotations

import json
import logging
import re
import time as _time
from typing import Any, Callable, Dict, Optional

import requests

from src.adapters.litellm_adapter import attempt_litellm_parsed_json
from src.adapters.shared_http_session import get_shared_session
from src.config import MAX_TOKENS, TEMPERATURE

logger = logging.getLogger(__name__)


def normalize_chat_base_url(model_type: str, base_url: str) -> str:
    """将控制台给出的根 URL 规范为带 ``/vN`` 的 chat 前缀（请求仍拼接 ``/chat/completions``）。"""
    mt = (model_type or "").strip().lower()
    if mt == "glm":
        mt = "zhipu"
    u = (base_url or "").strip().rstrip("/")
    if not u:
        return u
    if mt == "qwen":
        if "/compatible-mode/v1" not in u:
            return u + "/compatible-mode/v1"
        return u
    if mt in ("openai", "moonshot", "baichuan", "siliconflow", "deepseek", "zhipu", "doubao"):
        if not re.search(r"/v\d+$", u):
            return u + "/v1"
        return u
    return u


def _per_request_http_timeout(attempt: int, request_timeout: Optional[int]) -> int:
    if request_timeout is not None:
        return max(1, int(request_timeout))
    return 120 + attempt * 60


def call_openai_compatible_chat(
    prompt: str,
    *,
    model_type: str,
    config: Dict[str, Any],
    total_deadline: Optional[float] = None,
    request_timeout: Optional[int] = None,
    temperature: Optional[float] = None,
    plain_text: bool = False,
    route_label: str = "OpenAI 兼容",
    response_text_to_dict: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """POST ``{base_url}/chat/completions``，解析 ``choices[0].message.content`` 为 dict。"""
    mt = (model_type or "").strip().lower()

    headers = {"Content-Type": "application/json; charset=utf-8"}
    api_key = str(config.get("api_key") or "").strip()
    if api_key and api_key != "not-needed":
        headers["Authorization"] = f"Bearer {api_key}"

    if isinstance(prompt, bytes):
        prompt = prompt.decode("utf-8", errors="replace")
    else:
        prompt = prompt.encode("utf-8", errors="replace").decode("utf-8")

    raw_base = str(config.get("base_url") or "").strip()
    base_url = normalize_chat_base_url(mt, raw_base)
    payload = {
        "model": str(config.get("model") or "").strip(),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature if temperature is not None else config.get("temperature", TEMPERATURE),
        "max_tokens": int(config.get("max_tokens") or MAX_TOKENS),
        "stream": False,
    }
    json_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    max_retries = 3
    last_exception: Optional[BaseException] = None

    for attempt in range(max_retries):
        if total_deadline and _time.time() > total_deadline:
            raise TimeoutError(f"{route_label}: 已超过总时间限制")
        try:
            t_http = _per_request_http_timeout(attempt, request_timeout)
            parsed_lm = None
            if not plain_text:
                parsed_lm = attempt_litellm_parsed_json(
                    prompt,
                    mt,
                    {**config, "base_url": base_url},
                    t_http,
                    temperature,
                    route_label,
                    response_text_to_dict,
                )
            if parsed_lm is not None:
                return parsed_lm

            url = f"{base_url.rstrip('/')}/chat/completions"
            resp = get_shared_session().post(url, data=json_bytes, headers=headers, timeout=t_http)
            resp.raise_for_status()
            response_data = resp.json()
            result = response_data["choices"][0]["message"]["content"].strip()
            logger.debug("%s 原始响应长度: %s", route_label, len(result))
            parsed = response_text_to_dict(result, plain_text=plain_text)
            logger.debug("%s 解析后类型: %s", route_label, type(parsed).__name__)
            return parsed

        except requests.exceptions.Timeout as e:
            logger.warning(
                "%s 调用超时，尝试 %s/%s，超时 %s 秒",
                route_label,
                attempt + 1,
                max_retries,
                t_http,
            )
            last_exception = e
            if attempt < max_retries - 1:
                _time.sleep(2)
        except Exception as e:
            raise e

    raise Exception(f"{route_label} 调用失败，重试{max_retries}次后仍超时: {last_exception}")
