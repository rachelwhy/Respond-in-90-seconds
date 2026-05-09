from __future__ import annotations

from typing import Any, Dict, List, Tuple

from src.core.postprocess import process_by_profile, retry_missing_required_fields, validate_required_fields


def with_source_text(extracted_raw: Any, source_text: str) -> dict:
    """为后处理注入 `_source_text`，保障按原文顺序稳定排序。"""
    if isinstance(extracted_raw, dict):
        payload = dict(extracted_raw)
    elif isinstance(extracted_raw, list):
        payload = {"records": list(extracted_raw)}
    else:
        payload = {"records": []}
    payload["_source_text"] = source_text or ""
    return payload


def evaluate_and_retry_required_fields(
    *,
    extracted_raw: Dict[str, Any],
    profile: Dict[str, Any],
    context_for_retry: str,
    source_text_for_order: str,
) -> Tuple[Dict[str, Any], List[Any], List[Any]]:
    """
    执行“缺字段校验 + 二次补抽”并返回：
    - 更新后的 extracted_raw
    - 首轮缺失字段/项
    - 成功补回字段/补齐日志
    """
    temp_final_data = process_by_profile(with_source_text(extracted_raw, source_text_for_order), profile)
    missing_before_retry = validate_required_fields(temp_final_data, profile)
    retried_fields: List[Any] = []
    if missing_before_retry:
        extracted_raw, retried_fields = retry_missing_required_fields(
            context_for_retry,
            profile,
            extracted_raw,
            missing_before_retry,
        )
    return extracted_raw, list(missing_before_retry or []), list(retried_fields or [])
