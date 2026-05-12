"""各模型后端的默认连接参数（来自 ``src.config`` / 环境变量 ``A23_*``）。

供 ``model_client``、LangExtract、可用性探测等读取；运行时每次从 ``src.config`` 取当前值，便于测试与热重载配置。
"""

from __future__ import annotations

import importlib
from typing import Any, Dict, FrozenSet, Optional


def _cfg():
    return importlib.import_module("src.config")


# OpenAI Chat Completions 兼容（POST …/chat/completions）；不含 ollama
CHAT_OPENAI_COMPATIBLE: FrozenSet[str] = frozenset(
    {
        "deepseek",
        "openai",
        "qwen",
        "moonshot",
        "zhipu",
        "glm",
        "baichuan",
        "siliconflow",
        "doubao",
    }
)

_LANGEX_OPENAI: FrozenSet[str] = frozenset(
    {"deepseek", "openai", "qwen", "moonshot", "zhipu", "glm", "baichuan", "siliconflow", "doubao"}
)


def is_chat_openai_compatible(model_type: str) -> bool:
    mt = (model_type or "").strip().lower()
    return mt in CHAT_OPENAI_COMPATIBLE


def is_langextract_openai_compatible(model_type: str) -> bool:
    mt = (model_type or "").strip().lower()
    return mt in _LANGEX_OPENAI


def default_chat_provider_dict(model_type: Optional[str] = None) -> Dict[str, Any]:
    """构造 ``get_model_config`` 兼容字典：含 ``base_url``、``api_key``、``model``、``temperature``、``max_tokens``、``url``（Ollama 占位）。"""
    cfg = _cfg()
    mt = (model_type or cfg.MODEL_TYPE or "deepseek").strip().lower()
    if mt == "glm":
        mt = "zhipu"

    common: Dict[str, Any] = {
        "type": mt,
        "url": cfg.OLLAMA_URL,
        "temperature": cfg.TEMPERATURE,
        "max_tokens": cfg.MAX_TOKENS,
    }

    if mt == "deepseek":
        return {
            **common,
            "base_url": cfg.DEEPSEEK_BASE_URL,
            "api_key": cfg.DEEPSEEK_API_KEY,
            "model": cfg.DEEPSEEK_MODEL,
        }
    if mt == "qwen":
        return {
            **common,
            "base_url": cfg.QWEN_BASE_URL,
            "api_key": cfg.QWEN_API_KEY,
            "model": cfg.QWEN_MODEL,
        }
    if mt == "moonshot":
        return {
            **common,
            "base_url": cfg.MOONSHOT_BASE_URL,
            "api_key": cfg.MOONSHOT_API_KEY,
            "model": cfg.MOONSHOT_MODEL,
        }
    if mt == "zhipu":
        return {
            **common,
            "base_url": cfg.ZHIPU_BASE_URL,
            "api_key": cfg.ZHIPU_API_KEY,
            "model": cfg.ZHIPU_MODEL,
        }
    if mt == "baichuan":
        return {
            **common,
            "base_url": cfg.BAICHUAN_BASE_URL,
            "api_key": cfg.BAICHUAN_API_KEY,
            "model": cfg.BAICHUAN_MODEL,
        }
    if mt == "siliconflow":
        return {
            **common,
            "base_url": cfg.SILICONFLOW_BASE_URL,
            "api_key": cfg.SILICONFLOW_API_KEY,
            "model": cfg.SILICONFLOW_MODEL,
        }
    if mt == "doubao":
        return {
            **common,
            "base_url": cfg.DOUBAO_BASE_URL,
            "api_key": cfg.DOUBAO_API_KEY,
            "model": cfg.DOUBAO_MODEL,
        }
    if mt == "ollama":
        return {
            **common,
            "base_url": "",
            "api_key": "",
            "model": cfg.OLLAMA_MODEL,
        }
    if mt == "openai":
        return {
            **common,
            "base_url": cfg.OPENAI_BASE_URL,
            "api_key": cfg.OPENAI_API_KEY,
            "model": cfg.OPENAI_MODEL,
        }

    return {
        **common,
        "base_url": cfg.OPENAI_BASE_URL,
        "api_key": cfg.OPENAI_API_KEY,
        "model": cfg.OPENAI_MODEL,
    }
