"""模型可用性探测（轻量、可缓存）。"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import requests

from src.config import (
    DEEPSEEK_API_KEY,
    MODEL_TYPE,
    OLLAMA_URL,
    OPENAI_API_KEY,
    QWEN_API_KEY,
)

_CACHE_TTL_SECONDS = 30.0
_READY_CACHE: Dict[str, Tuple[float, bool, str]] = {}


def resolve_model_type(model_type: Optional[str] = None) -> str:
    mt = (model_type or MODEL_TYPE or "ollama").strip().lower()
    return mt or "ollama"


def detect_model_readiness(
    model_type: Optional[str] = None,
    *,
    check_ollama: bool = True,
    timeout_seconds: float = 1.5,
) -> Dict[str, object]:
    """探测模型后端是否可用。

    返回:
        {"ready": bool, "reason": str, "model_type": str}
    """
    mt = resolve_model_type(model_type)

    now = time.time()
    cached = _READY_CACHE.get(mt)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return {"ready": cached[1], "reason": cached[2], "model_type": mt}

    ready = True
    reason = "ok"

    if mt == "deepseek":
        if not str(DEEPSEEK_API_KEY or "").strip():
            ready = False
            reason = "missing_deepseek_api_key"
    elif mt == "openai":
        if not str(OPENAI_API_KEY or "").strip():
            ready = False
            reason = "missing_openai_api_key"
    elif mt == "qwen":
        if not str(QWEN_API_KEY or "").strip():
            ready = False
            reason = "missing_qwen_api_key"
    elif mt == "ollama":
        if check_ollama:
            try:
                # OLLAMA_URL 形如 http://127.0.0.1:11434/api/generate
                base = OLLAMA_URL.rsplit("/api/", 1)[0] if "/api/" in OLLAMA_URL else OLLAMA_URL.rstrip("/")
                tags_url = f"{base}/api/tags"
                resp = requests.get(tags_url, timeout=timeout_seconds)
                if resp.status_code >= 400:
                    ready = False
                    reason = f"ollama_http_{resp.status_code}"
            except Exception:
                ready = False
                reason = "ollama_unreachable"
    else:
        ready = False
        reason = f"unsupported_model_type:{mt}"

    _READY_CACHE[mt] = (now, ready, reason)
    return {"ready": ready, "reason": reason, "model_type": mt}
