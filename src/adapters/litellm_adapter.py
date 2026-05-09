"""
可选 LiteLLM 适配层（集中在此文件，便于维护）：

- 模型 id 解析、completion 调用
- 启用开关、异常与回退日志
- 与业务 JSON 解析通过 parse_fn 注入，避免与 model_client 循环引用

启用：A23_USE_LITELLM=true 且已 pip install litellm。
模型 id：可用 A23_LITELLM_MODEL 覆盖；否则按 model_type 自动拼接。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.config import LITELLM_MODEL, MAX_TOKENS, TEMPERATURE, USE_LITELLM

logger = logging.getLogger(__name__)


def resolve_litellm_model_id(model_type: str, model_name: str, override: Optional[str] = None) -> str:
    """解析 LiteLLM 的 model 字符串（便于单测与排查）。"""
    o = (override if override is not None else LITELLM_MODEL or "").strip()
    if o:
        return o
    mn = (model_name or "").strip()
    if model_type == "deepseek":
        return f"deepseek/{mn}" if mn and "/" not in mn else mn
    return f"openai/{mn}" if mn and "/" not in mn else mn


def litellm_chat_user_text(
    prompt: str,
    model_type: str,
    config: Dict[str, Any],
    timeout: int,
    temperature: Optional[float],
) -> str:
    """
    调用 LiteLLM completion，返回单条 user 消息对应的 assistant 文本。

    Raises:
        ImportError: 未安装 litellm
        ValueError: 空响应
        其他：底层 API 错误
    """
    import litellm

    api_key = (config.get("api_key") or "").strip()
    base_url = (config.get("base_url") or "").strip().rstrip("/")
    model_name = (config.get("model") or "").strip()

    litellm_model = resolve_litellm_model_id(model_type, model_name)

    kwargs: Dict[str, Any] = {
        "model": litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature if temperature is not None else config.get("temperature", TEMPERATURE),
        "max_tokens": config.get("max_tokens", MAX_TOKENS),
        "timeout": timeout,
    }
    if api_key and api_key != "not-needed":
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["api_base"] = base_url

    resp = litellm.completion(**kwargs)
    if not resp or not getattr(resp, "choices", None):
        raise ValueError("LiteLLM 返回空 choices")
    msg = resp.choices[0].message
    return (getattr(msg, "content", None) or "").strip()


def attempt_litellm_parsed_json(
    prompt: str,
    model_type: str,
    config: Dict[str, Any],
    timeout: int,
    temperature: Optional[float],
    route_label: str,
    parse_fn: Callable[[str], dict],
) -> Optional[dict]:
    """
    若启用 LiteLLM：拉取文本并用 parse_fn 解析为 dict；失败或未启用则返回 None（由调用方走 HTTP）。

    parse_fn 通常为 model_client._parse_model_response，由调用方注入以避免本模块依赖 JSON 规则细节。
    """
    if not USE_LITELLM:
        return None
    try:
        text = litellm_chat_user_text(prompt, model_type, config, timeout, temperature)
        parsed = parse_fn(text)
        logger.debug("%s(LiteLLM)解析后结果类型: %s", route_label, type(parsed).__name__)
        return parsed
    except ImportError:
        logger.warning("USE_LITELLM 已开启但未安装 litellm，使用 HTTP")
        return None
    except Exception as e:
        logger.warning("LiteLLM(%s) 失败，回退 HTTP: %s", route_label, e)
        return None
