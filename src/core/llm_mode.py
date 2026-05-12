"""LLM 模式规范化：主线仅保留 full/off，supplement 兼容映射到 full。"""

from __future__ import annotations

from typing import Optional


def normalize_llm_mode(mode: Optional[str]) -> str:
    """将外部 ``llm_mode`` 规范为 ``full`` 或 ``off``；空值与未知值视为 ``full``，``supplement`` 映射为 ``full``。"""
    raw = (mode or "").strip().lower()
    if raw == "off":
        return "off"
    # supplement 已收敛到 full，避免独立策略分叉
    if raw in ("supplement", "full", ""):
        return "full"
    return "full"

