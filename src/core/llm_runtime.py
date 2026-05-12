"""规范化 LLM 模式并结合模型可用性探测，决定是否仅走规则抽取。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from src.core.llm_mode import normalize_llm_mode
from src.core.model_availability import detect_model_readiness


@dataclass(frozen=True)
class LlmModeResolution:
    requested: str
    normalized: str
    effective: str
    readiness: Dict[str, Any]
    fallback_rule_only: bool


def resolve_llm_mode_with_readiness(
    llm_mode: str,
    model_type: Optional[str],
    *,
    quiet: bool = False,
    logger: Optional[Any] = None,
) -> LlmModeResolution:
    """
    统一 LLM 模式规范化与可用性降级逻辑（CLI/API 共用）。
    """
    requested = llm_mode
    normalized = normalize_llm_mode(llm_mode)
    readiness = detect_model_readiness(model_type, check_ollama=True)
    fallback_rule_only = normalized != "off" and not bool(readiness.get("ready"))
    effective = "off" if (normalized == "off" or fallback_rule_only) else normalized
    if fallback_rule_only and not quiet and logger is not None:
        logger.warning(
            "模型不可用，自动降级为纯规则抽取：model=%s, reason=%s",
            readiness.get("model_type"),
            readiness.get("reason"),
        )
    return LlmModeResolution(
        requested=requested,
        normalized=normalized,
        effective=effective,
        readiness=readiness,
        fallback_rule_only=fallback_rule_only,
    )
