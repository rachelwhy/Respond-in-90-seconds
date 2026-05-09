from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from src.core.extraction_routing import table_specs_homogeneous_columns
from src.core.reader import collect_semantic_chunks_from_bundle
from src.core.scope_resolution import prepare_main_llm_inputs


def get_profile_max_llm_context_chars(profile: Dict[str, Any]) -> int:
    """
    统一上下文窗口策略：默认 24k，同标头多表放宽到 80k。
    """
    if profile.get("template_mode") == "word_multi_table" and table_specs_homogeneous_columns(profile):
        return 80000
    return 24000


def prepare_llm_context_and_chunks(
    *,
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
    context_text: str,
    llm_context_route: str,
    max_context_chars: int,
    logger: Optional[logging.Logger] = None,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any], Optional[Dict[str, Any]]]:
    """
    统一准备 extract_with_slicing 的上下文、语义块和 routing_bundle。
    """
    routing_bundle = bundle
    if llm_context_route == "rag_chunks":
        if len(context_text) > max_context_chars:
            if logger is not None:
                logger.info("文本长度 %s 字符，截断至 %s 字符以控制耗时", len(context_text), max_context_chars)
            context_text = context_text[:max_context_chars]
        return context_text, collect_semantic_chunks_from_bundle(bundle), routing_bundle, None

    prep = prepare_main_llm_inputs(bundle, profile, max_context_chars=max_context_chars)
    routing_bundle = {**bundle, "scope_resolution": prep.scope_meta}
    return prep.context_text, prep.semantic_chunks, routing_bundle, dict(prep.scope_meta)


def run_extract_with_slicing(
    *,
    extraction_service: Any,
    profile: Dict[str, Any],
    context_text: str,
    use_model: bool,
    slice_size: Optional[int],
    overlap: int,
    show_progress: bool,
    time_budget: int,
    chunks: Optional[List[Dict[str, Any]]],
    max_chunks: int,
    word_table_segments: Optional[List[str]] = None,
    routing_bundle: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
):
    """
    统一执行 extract_with_slicing，避免 CLI/API 多处复制参数编排。
    """
    return extraction_service.extract_with_slicing(
        text=context_text,
        profile=profile,
        use_model=use_model,
        slice_size=slice_size,
        overlap=overlap,
        show_progress=show_progress,
        time_budget=time_budget,
        chunks=chunks if chunks else None,
        max_chunks=max_chunks,
        logger=logger,
        word_table_segments=word_table_segments,
        routing_bundle=routing_bundle,
    )
