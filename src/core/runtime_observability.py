from __future__ import annotations

import time
from typing import Any, Dict, Optional


def merge_runtime_updates(runtime: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """将阶段更新写入 runtime（浅合并）。"""
    if not isinstance(updates, dict):
        return runtime
    runtime.update(updates)
    return runtime


def finalize_runtime_metrics(
    runtime: Dict[str, Any],
    *,
    total_start: float,
    target_limit_seconds: int,
) -> Dict[str, Any]:
    """
    统一收口 runtime 指标：
    - model_inference_total_seconds
    - total_seconds / within_limit_seconds / limit_seconds
    """
    runtime["model_inference_total_seconds"] = round(
        float(runtime.get("model_inference_seconds", 0.0) or 0.0)
        + float(runtime.get("retry_inference_seconds", 0.0) or 0.0),
        3,
    )
    total_seconds = round(time.perf_counter() - float(total_start), 3)
    runtime["total_seconds"] = total_seconds
    runtime["within_limit_seconds"] = bool(total_seconds <= float(target_limit_seconds))
    runtime["limit_seconds"] = int(target_limit_seconds)
    return runtime


def compact_runtime_for_api(runtime: Dict[str, Any]) -> Dict[str, Any]:
    """
    API metadata 使用的 runtime 摘要；保留关键耗时与路由元信息。
    """
    if not isinstance(runtime, dict):
        return {}
    keys = [
        "read_documents_seconds",
        "internal_structured_extract_seconds",
        "build_prompt_seconds",
        "model_inference_seconds",
        "retry_inference_seconds",
        "model_inference_total_seconds",
        "total_seconds",
        "within_limit_seconds",
        "limit_seconds",
        "scope_resolution",
        "slicing_metadata",
    ]
    out: Dict[str, Any] = {}
    for k in keys:
        if k in runtime:
            out[k] = runtime[k]
    return out
