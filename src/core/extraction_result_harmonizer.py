from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.core.extraction_routing import table_specs_homogeneous_columns
from src.core.postprocess import process_by_profile
from src.core.reader import try_internal_structured_extract
from src.core.required_field_retry import with_source_text


def records_from_final_data(final_data: Any) -> list:
    records = final_data.get("records", []) if isinstance(final_data, dict) else []
    if not records and isinstance(final_data, dict):
        non_meta = {k: v for k, v in final_data.items() if not k.startswith("_")}
        if non_meta:
            records = [non_meta]
    return records


def merge_internal_structured_when_model_insufficient(
    *,
    final_data: Any,
    internal_structured: Any,
    effective_llm_mode: str,
    all_text: str,
    profile: Dict[str, Any],
    logger: logging.Logger,
) -> Any:
    if (
        effective_llm_mode == "full"
        and not records_from_final_data(final_data)
        and isinstance(internal_structured, dict)
        and internal_structured.get("records")
    ):
        logger.info("模型结果为空，改用内部结构化结果")
        return process_by_profile(with_source_text(internal_structured, all_text), profile)

    if (
        effective_llm_mode == "full"
        and isinstance(internal_structured, dict)
        and isinstance(internal_structured.get("records"), list)
    ):
        model_rows = records_from_final_data(final_data)
        internal_rows = internal_structured.get("records") or []
        if len(model_rows) <= 1 and len(internal_rows) > max(20, len(model_rows) * 10):
            logger.info("模型结果过少，改用内部结构化结果")
            return process_by_profile(with_source_text(internal_structured, all_text), profile)
    return final_data


def reconcile_word_multi_results(
    *,
    final_data: Any,
    profile: Dict[str, Any],
    loaded_bundle: Dict[str, Any],
    all_text: str,
    logger: logging.Logger,
    internal_structured: Optional[Any] = None,
) -> Any:
    if profile.get("template_mode") != "word_multi_table":
        return final_data

    homogeneous_multi = table_specs_homogeneous_columns(profile)
    current_records = records_from_final_data(final_data)
    current_internal = internal_structured
    if homogeneous_multi and len(current_records) <= 1:
        current_internal = try_internal_structured_extract(profile, loaded_bundle)
        if isinstance(current_internal, dict) and current_internal.get("records"):
            logger.info("同标头多表统一抽取结果偏少，改用内部结构化结果分表")
            final_data = process_by_profile(with_source_text(current_internal, all_text), profile)

    if isinstance(final_data, dict) and final_data.get("_table_groups") and not homogeneous_multi:
        from src.core.word_multi_internal_merge import merge_internal_structured_into_word_multi_groups

        final_data = merge_internal_structured_into_word_multi_groups(final_data, profile, loaded_bundle)
    return final_data
