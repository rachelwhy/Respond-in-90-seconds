from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from src.core.extraction_routing import is_word_multi_parallel_enabled
from src.core.required_field_retry import evaluate_and_retry_required_fields
from src.core.slicing_orchestrator import (
    get_profile_max_llm_context_chars,
    prepare_llm_context_and_chunks,
    run_extract_with_slicing,
)
from src.core.word_multi_segments import build_word_multi_table_segments


def run_model_extraction_path(
    *,
    extraction_service: Any,
    profile: Dict[str, Any],
    loaded_bundle: Dict[str, Any],
    context_for_llm: str,
    llm_context_route: str,
    effective_llm_mode: str,
    slice_size: int,
    overlap: int,
    quiet: bool,
    max_chunks: int,
    total_start: float,
    total_timeout: int,
    source_text_for_order: str,
    logger: logging.Logger,
) -> Tuple[Dict[str, Any], Dict[str, Any], str, List[Any], Dict[str, Any]]:
    """
    统一模型抽取主路径：
    - 上下文裁剪与 scope 选块
    - 切片抽取
    - 必填字段缺失重试
    """
    logger.info("使用模型智能抽取模式")
    step_start = time.perf_counter()

    elapsed_before_extraction = time.perf_counter() - total_start
    dynamic_time_budget = max(40, int(total_timeout) - int(elapsed_before_extraction))
    logger.info("动态切片时间预算: %ss（已用 %.1fs）", dynamic_time_budget, elapsed_before_extraction)

    max_llm_input_chars = get_profile_max_llm_context_chars(profile)
    context_for_llm, all_semantic_chunks, routing_bundle, scope_meta = prepare_llm_context_and_chunks(
        bundle=loaded_bundle,
        profile=profile,
        context_text=context_for_llm,
        llm_context_route=llm_context_route,
        max_context_chars=max_llm_input_chars,
        logger=logger,
    )

    word_table_segments = None
    if is_word_multi_parallel_enabled(profile):
        word_table_segments = build_word_multi_table_segments(
            profile, context_for_llm, loaded_bundle.get("documents", [])
        )

    extracted_raw, model_output, slicing_metadata = run_extract_with_slicing(
        extraction_service=extraction_service,
        profile=profile,
        context_text=context_for_llm,
        use_model=(effective_llm_mode != "off"),
        slice_size=slice_size,
        overlap=overlap,
        show_progress=not quiet,
        time_budget=dynamic_time_budget,
        chunks=all_semantic_chunks,
        max_chunks=max_chunks,
        word_table_segments=word_table_segments,
        routing_bundle=routing_bundle,
    )

    logger.info("切片抽取完成")
    logger.info("切片模式: %s", slicing_metadata.get("slicing_enabled", False))
    if slicing_metadata.get("slicing_enabled"):
        logger.info("切片数量: %s", slicing_metadata.get("slice_count", 1))

    retry_start = time.perf_counter()
    extracted_raw, missing_before_retry, retried_fields = evaluate_and_retry_required_fields(
        extracted_raw=extracted_raw,
        profile=profile,
        context_for_retry=context_for_llm,
        source_text_for_order=source_text_for_order,
    )
    retry_elapsed = round(time.perf_counter() - retry_start, 3)
    if missing_before_retry:
        logger.warning("首次抽取后关键字段缺失：%s", missing_before_retry)
        if retried_fields:
            logger.info("已触发补抽并补回内容：%s", retried_fields)

    runtime_updates: Dict[str, Any] = {
        "build_prompt_seconds": round(time.perf_counter() - step_start, 3),
        "model_inference_seconds": 0.0,
        "retry_inference_seconds": retry_elapsed if missing_before_retry else 0.0,
    }
    if scope_meta is not None:
        runtime_updates["scope_resolution"] = scope_meta
    runtime_updates["slicing_metadata"] = slicing_metadata

    return extracted_raw, model_output, context_for_llm, retried_fields, runtime_updates
