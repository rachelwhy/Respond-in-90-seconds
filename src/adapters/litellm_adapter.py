"""可选 LiteLLM 聚合调用：模型 id 解析、completion 与 JSON 解析回调注入（与 ``model_client`` 单向依赖）。

启用条件：``A23_USE_LITELLM=true`` 且已安装 ``litellm``；模型串可用 ``A23_LITELLM_MODEL`` 覆盖。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from src.config import LITELLM_MODEL, MAX_TOKENS, TEMPERATURE, USE_LITELLM

logger = logging.getLogger(__name__)


def resolve_litellm_model_id(model_type: str, model_name: str, override: Optional[str] = None) -> str:
    """解析 LiteLLM 的 ``model`` 字符串（与 ``provider_env`` 中 ``MODEL_TYPE`` 取值对齐）。"""
    o = (override if override is not None else LITELLM_MODEL or "").strip()
    if o:
        return o
    mn = (model_name or "").strip()
    if mn and "/" in mn:
        return mn
    mt = (model_type or "").strip().lower()
    if mt == "deepseek":
        return f"deepseek/{mn}" if mn else mn
    if mt == "qwen":
        return f"dashscope/{mn}" if mn else mn
    if mt == "moonshot":
        return f"moonshot/{mn}" if mn else mn
    if mt in ("zhipu", "glm"):
        return f"zhipu/{mn}" if mn else mn
    if mt == "baichuan":
        return f"openai/{mn}" if mn else mn
    if mt == "siliconflow":
        return f"openai/{mn}" if mn else mn
    if mt == "doubao":
        return f"openai/{mn}" if mn else mn
    return f"openai/{mn}" if mn else mn


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
        logger.warning("LiteLLM(%s) 失败，改用直连 HTTP: %s", route_label, e)
        return None
