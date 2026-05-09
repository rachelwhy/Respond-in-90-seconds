"""
直接抽取服务 — 无需创建后台任务，同步返回结果

职责边界：
- 接收模板路径 + 输入目录，调用核心算法，返回 {"records": [...]} 格式
- 不写数据库，不管文件存储，由调用方（api_server.py）处理临时目录
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.profile_resolver import resolve_profile
from src.core.extraction_service import get_extraction_service
from src.core.llm_runtime import resolve_llm_mode_with_readiness
from src.core.model_extraction_orchestrator import run_model_extraction_path
from src.core.required_field_retry import with_source_text
from src.core.extraction_result_harmonizer import (
    records_from_final_data,
    merge_internal_structured_when_model_insufficient,
    reconcile_word_multi_results,
)
from src.core.result_packager import build_api_metadata, build_api_result
from src.core.output_writer_orchestrator import write_template_outputs_api
from src.core.runtime_observability import (
    merge_runtime_updates,
    finalize_runtime_metrics,
    compact_runtime_for_api,
)
from src.core.reader import collect_input_bundle, try_internal_structured_extract
from src.core.postprocess import process_by_profile
from src.config import EXTRACTION_TIMEOUT

logger = logging.getLogger(__name__)

_SIDECAR_INSTRUCTION_NAME = "用户要求.txt"


def effective_instruction_for_extract(
    input_dir: str,
    instruction: Optional[str],
) -> Optional[str]:
    """解析最终抽取指令：优先使用调用方传入的 ``instruction``；为空时读取 ``input_dir/用户要求.txt``。

    与 ``main.py`` 侧文件行为一致，便于 multipart 仅上传数据文件、不写长 Form 字段。
    """
    eff = str(instruction or "").strip()
    if eff:
        return eff
    base = Path(input_dir)
    if not base.is_dir():
        return None
    side = base / _SIDECAR_INSTRUCTION_NAME
    if not side.is_file():
        return None
    try:
        txt = side.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("读取侧指令文件失败 %s：%s", side, e)
        return None
    eff = str(txt or "").strip()
    return eff or None


def direct_extract(
    template_path: str,
    input_dir: str,
    model_type: Optional[str] = None,
    instruction: Optional[str] = None,
    llm_mode: str = 'full',
    enable_unit_aware: bool = True,
    work_dir: Optional[Path] = None,
    total_timeout: Optional[int] = None,
    max_chunks: int = 50,
    quiet: bool = False,
) -> Dict[str, Any]:
    """同步执行文档信息提取

    Args:
        template_path: 模板文件路径（.xlsx / .docx / .json）
        input_dir: 输入文档目录
        model_type: 模型类型（ollama / deepseek / openai），None 则用配置
        instruction: 补充抽取指令（可选）；若为空且 ``input_dir`` 下存在 ``用户要求.txt`` 则自动读取
        llm_mode: LLM抽取模式，可选值：'full'（默认）、'off'（纯规则）。'supplement' 会兼容映射为 'full'
        enable_unit_aware: 是否启用单位感知（预留，暂不影响主流程）
        work_dir: 可选的工作空间目录（持久化场景由调用方提供，None 则无输出落盘）
        total_timeout: 总超时时间（秒），默认与 config.EXTRACTION_TIMEOUT 一致
        max_chunks: 最大语义分块数量，默认50
        quiet: 安静模式，禁用控制台输出，默认False

    Returns:
        {
            "records": List[dict],        # 按模板字段对齐的记录数组
            "metadata": dict,             # 处理元数据
        }
    """
    try:
        total_start = time.perf_counter()
        if total_timeout is None:
            total_timeout = EXTRACTION_TIMEOUT
        runtime: Dict[str, Any] = {}

        step_start = time.perf_counter()
        bundle = collect_input_bundle(input_dir)
        runtime["read_documents_seconds"] = round(time.perf_counter() - step_start, 3)
        if not quiet:
            logger.info(
                "direct_extract: file_count=%s, all_text_len=%s",
                bundle.get("file_count", 0),
                len(bundle.get("all_text", "") or ""),
            )

        effective_instruction = effective_instruction_for_extract(input_dir, instruction)
        if effective_instruction and not str(instruction or "").strip() and not quiet:
            logger.info("抽取指令来自输入目录侧文件：%s", Path(input_dir) / _SIDECAR_INSTRUCTION_NAME)

        profile = resolve_profile(
            template_path=template_path,
            instruction=effective_instruction,
            document_text=bundle.get("all_text", ""),
            logger=logger,
        )
        doc_type = profile.get("_doc_type", "")
        if not quiet and doc_type:
            logger.info("文档自动分析结果: %s, 字段数: %s", doc_type, len(profile.get("fields", [])))

        llm_resolution = resolve_llm_mode_with_readiness(
            llm_mode,
            model_type,
            quiet=quiet,
            logger=logger,
        )
        requested_llm_mode = llm_resolution.requested
        llm_mode_norm = llm_resolution.normalized
        effective_llm_mode = llm_resolution.effective
        readiness = llm_resolution.readiness

        runtime_updates: Dict[str, Any] = {}
        step_start = time.perf_counter()
        internal_structured = try_internal_structured_extract(profile, bundle)
        runtime["internal_structured_extract_seconds"] = round(time.perf_counter() - step_start, 3)
        extracted_raw: Any = None
        text = bundle.get("all_text", "")
        internal_route_used = ""
        retried_fields: List[Any] = []
        model_output: Dict[str, Any] = {}

        if internal_structured:
            logger.info(
                "已命中内部结构化抽取通道：%s",
                internal_structured.get("_internal_route", "internal_structured"),
            )
            should_force_model = effective_llm_mode == "full"
            if should_force_model:
                logger.info("llm_mode=full：即使有结构化结果也继续模型抽取")
            else:
                extracted_raw = internal_structured
                internal_route_used = internal_structured.get("_internal_route", "internal_structured")
        else:
            logger.info("内部结构化抽取未命中，使用智能抽取策略")

        if extracted_raw is None or effective_llm_mode == "full":
            if not str(text or "").strip():
                raise ValueError("无可用正文，无法继续模型抽取。")
            extraction_service = get_extraction_service()
            extracted_raw, model_output, _context_for_llm, retried_fields, runtime_updates = run_model_extraction_path(
                extraction_service=extraction_service,
                profile=profile,
                loaded_bundle=bundle,
                context_for_llm=text,
                llm_context_route="full_text",
                effective_llm_mode=effective_llm_mode,
                slice_size=3000,
                overlap=200,
                quiet=quiet,
                max_chunks=max_chunks,
                total_start=total_start,
                total_timeout=total_timeout,
                source_text_for_order=text,
                logger=logger,
            )
            merge_runtime_updates(runtime, runtime_updates)

        final_data = process_by_profile(with_source_text(extracted_raw, text), profile)
        final_data = merge_internal_structured_when_model_insufficient(
            final_data=final_data,
            internal_structured=internal_structured,
            effective_llm_mode=effective_llm_mode,
            all_text=text,
            profile=profile,
            logger=logger,
        )
        final_data = reconcile_word_multi_results(
            final_data=final_data,
            profile=profile,
            loaded_bundle=bundle,
            all_text=text,
            logger=logger,
            internal_structured=internal_structured,
        )
        records = records_from_final_data(final_data)
        if not quiet:
            logger.info("后处理完成: %s 条记录", len(records))

        output_file = None
        output_files: List[str] = []
        doc_type = profile.get("_doc_type", "")
        output_file, output_files = write_template_outputs_api(
            template_path=template_path,
            work_dir=work_dir,
            records=records if isinstance(records, list) else [],
            profile=profile,
            final_data=final_data,
            logger=logger,
        )

        # 调试信息（可选）
        if not quiet and records and len(records) > 0:
            logger.info(f"提取完成: {len(records)} 条记录")

        finalize_runtime_metrics(
            runtime,
            total_start=total_start,
            target_limit_seconds=int(total_timeout),
        )

        meta = build_api_metadata(
            file_count=int(bundle.get("file_count", 0)),
            records=records,
            profile=profile,
            doc_type=doc_type,
            runtime_updates=compact_runtime_for_api(runtime),
            llm_mode_requested=requested_llm_mode,
            llm_mode_normalized=llm_mode_norm,
            llm_mode_effective=effective_llm_mode,
            readiness=readiness,
            internal_route_used=internal_route_used,
            retried_fields=retried_fields,
            final_data=final_data,
            model_output=model_output,
            output_files=output_files,
        )
        parse_warnings = bundle.get("warnings") or []
        if parse_warnings:
            meta["parse_warnings_count"] = len(parse_warnings)
            meta["parse_warnings_preview"] = list(parse_warnings[:5])
        return build_api_result(
            records=records,
            metadata=meta,
            output_file=output_file,
            output_files=output_files,
        )

    except Exception as e:
        logger.error(f"直接抽取失败: {e}", exc_info=True)
        return {
            "records": [],
            "metadata": {"error": str(e)},
        }


