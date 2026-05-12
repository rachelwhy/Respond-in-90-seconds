"""探测当前配置的模型端点是否就绪（短时缓存），供 ``llm_runtime`` 决定是否仅规则抽取。"""

from __future__ import annotations

import time
from typing import Dict, Optional, Tuple

import requests

from src.adapters.provider_env import default_chat_provider_dict, is_chat_openai_compatible
from src.config import MODEL_TYPE, OLLAMA_URL

_CACHE_TTL_SECONDS = 30.0
_READY_CACHE: Dict[str, Tuple[float, bool, str]] = {}


def resolve_model_type(model_type: Optional[str] = None) -> str:
    mt = (model_type or MODEL_TYPE or "deepseek").strip().lower()
    return mt or "deepseek"


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

    if mt == "ollama":
        if check_ollama:
            try:
                base = OLLAMA_URL.rsplit("/api/", 1)[0] if "/api/" in OLLAMA_URL else OLLAMA_URL.rstrip("/")
                tags_url = f"{base}/api/tags"
                resp = requests.get(tags_url, timeout=timeout_seconds)
                if resp.status_code >= 400:
                    ready = False
                    reason = f"ollama_http_{resp.status_code}"
            except Exception:
                ready = False
                reason = "ollama_unreachable"
    elif is_chat_openai_compatible(mt):
        cfg = default_chat_provider_dict(mt)
        key = str(cfg.get("api_key") or "").strip()
        if mt == "openai" and key in ("", "not-needed"):
            pass
        elif not key:
            ready = False
            reason = f"missing_{mt}_api_key"
        if ready and mt == "doubao":
            base = str(cfg.get("base_url") or "").strip()
            if not base:
                ready = False
                reason = "missing_doubao_base_url"
    else:
        ready = False
        reason = f"unsupported_model_type:{mt}"

    _READY_CACHE[mt] = (now, ready, reason)
    return {"ready": ready, "reason": reason, "model_type": mt}
