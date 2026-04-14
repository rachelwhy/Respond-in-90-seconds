"""
直接抽取服务 — 无需创建后台任务，同步返回结果

职责边界：
- 接收模板路径 + 输入目录，调用核心算法，返回 {"records": [...]} 格式
- 不写数据库，不管文件存储，由调用方（api_server.py）处理临时目录
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.profile import (
    generate_profile_from_template,
    generate_profile_from_document,
    apply_instruction_runtime_hints,
    apply_word_multi_instruction_constraints,
)
from src.core.reader import collect_input_bundle, collect_semantic_chunks_from_bundle, try_internal_structured_extract
from src.core.postprocess import process_by_profile, validate_required_fields
from src.core.extractor import UniversalExtractor
from src.core.writers import create_excel_from_records, fill_excel_table, fill_excel_vertical, fill_word_table
from src.core.deadline_context import create_deadline_context
from src.core.llm_mode import normalize_llm_mode
from src.core.model_availability import detect_model_readiness

logger = logging.getLogger(__name__)


@dataclass
class ExtractFlowState:
    records_list: List[Any]
    text: str
    effective_llm_mode: str
    requested_llm_mode: str
    llm_mode_normalized: str
    readiness: Dict[str, object]
    langextract_used: bool = False
    parallel_extracted: bool = False
    processed_bundle: Optional[Dict[str, Any]] = None


def _resolve_llm_mode_with_fallback(
    llm_mode: str,
    model_type: Optional[str],
    quiet: bool,
) -> tuple[str, str, str, Dict[str, object]]:
    requested_llm_mode = llm_mode
    llm_mode_norm = normalize_llm_mode(llm_mode)
    readiness = detect_model_readiness(model_type, check_ollama=True)
    fallback_rule_only = llm_mode_norm != "off" and not bool(readiness.get("ready"))
    effective_llm_mode = "off" if (llm_mode_norm == "off" or fallback_rule_only) else llm_mode_norm
    if fallback_rule_only and not quiet:
        logger.warning(
            "模型不可用，自动降级为纯规则抽取：model=%s, reason=%s",
            readiness.get("model_type"),
            readiness.get("reason"),
        )
    return requested_llm_mode, llm_mode_norm, effective_llm_mode, readiness


def _run_word_multi_parallel_stage(
    profile: Dict[str, Any],
    bundle: Dict[str, Any],
    state: ExtractFlowState,
    *,
    total_timeout: int,
    max_chunks: int,
    quiet: bool,
) -> None:
    if state.effective_llm_mode == "off":
        return
    from src.core.extraction_service import get_extraction_service
    from src.core.extraction_routing import is_word_multi_parallel_enabled
    from src.core.word_multi_segments import build_word_multi_table_segments

    if not is_word_multi_parallel_enabled(profile):
        return

    ctx = state.text or ""
    if len(ctx) > 24000:
        ctx = ctx[:24000]
        if not quiet:
            logger.info("direct_extract: 正文截断至 24000 字符")

    segs = build_word_multi_table_segments(profile, ctx, bundle.get("documents") or [])
    svc = get_extraction_service()
    _chunks = collect_semantic_chunks_from_bundle(bundle)
    chunks_arg = _chunks if _chunks else None
    extracted_raw, _mo, meta = svc.extract_with_slicing(
        text=ctx,
        profile=profile,
        use_model=True,
        show_progress=not quiet,
        time_budget=total_timeout,
        chunks=chunks_arg,
        max_chunks=max_chunks,
        logger=logger if not quiet else None,
        word_table_segments=segs,
        routing_bundle=bundle,
    )
    extracted_for_post = dict(extracted_raw)
    extracted_for_post["_source_text"] = state.text
    processed_bundle = process_by_profile(extracted_for_post, profile)
    if (
        profile.get("template_mode") == "word_multi_table"
        and isinstance(processed_bundle, dict)
        and processed_bundle.get("_table_groups")
    ):
        from src.core.word_multi_internal_merge import merge_internal_structured_into_word_multi_groups

        processed_bundle = merge_internal_structured_into_word_multi_groups(
            processed_bundle, profile, bundle
        )
    state.records_list = list(processed_bundle.get("records") or [])
    state.parallel_extracted = True
    state.langextract_used = True
    state.processed_bundle = processed_bundle
    if not quiet:
        logger.info("direct_extract: word_multi_parallel 完成 meta=%s", meta)


def _run_langextract_stage(
    profile: Dict[str, Any],
    bundle: Dict[str, Any],
    state: ExtractFlowState,
    *,
    total_timeout: int,
    max_chunks: int,
    quiet: bool,
) -> None:
    if state.langextract_used or state.effective_llm_mode == "off":
        return
    try:
        from src.adapters.langextract_adapter import extract_with_langextract

        chunks = ensure_chunks(bundle, quiet=quiet)
        original_count = len(chunks)

        total_chars = sum(len(chunk.get("text", "")) for chunk in chunks)
        if total_chars > 20000:
            target_size = 8000
        elif total_chars > 10000:
            target_size = 6000
        else:
            target_size = 4000

        if max_chunks and original_count > max_chunks:
            chunks = chunks[:max_chunks]
            if not quiet:
                logger.info(f"限制分块数: {original_count} -> {len(chunks)} (max_chunks={max_chunks})")

        text_chunks = merge_chunks(chunks, target_size=target_size)
        if not quiet:
            logger.info(
                "分块合并: %s -> %s 块, 目标大小=%s字符, 总字符=%s",
                original_count,
                len(text_chunks),
                target_size,
                total_chars,
            )
        logger.info("准备调用 langextract，块数: %s", len(text_chunks))
        lx_records = extract_with_langextract(
            text_chunks,
            profile,
            time_budget=total_timeout,
            quiet=quiet,
        )
        if lx_records is not None and len(lx_records) > 0:
            if not quiet:
                logger.info("langextract 提取成功: %s 条记录", len(lx_records))
            state.records_list = (state.records_list or []) + list(lx_records)
            state.langextract_used = True
    except Exception as e:
        logger.info(f"langextract 不可用: {e}")
        import traceback
        logger.info(f"异常详情: {traceback.format_exc()}")


def _run_prompt_llm_stage(
    profile: Dict[str, Any],
    state: ExtractFlowState,
    *,
    model_type: Optional[str],
    total_timeout: int,
    max_chunks: int,
    quiet: bool,
) -> None:
    if state.langextract_used or state.effective_llm_mode == "off":
        return
    deadline_ctx = create_deadline_context(
        name=f"direct_extract_{int(time.time())}",
        timeout_seconds=total_timeout if total_timeout > 0 else None,
    )
    config = {
        "llm_mode": state.llm_mode_normalized,
        "total_timeout": total_timeout,
        "max_chunks": max_chunks,
        "quiet": quiet,
        "total_deadline": time.time() + total_timeout if total_timeout else None,
        "deadline_context": deadline_ctx,
    }
    if model_type:
        config["model_type"] = model_type
    extractor = UniversalExtractor(config=config)
    result = extractor.extract(state.text, profile)
    if state.records_list:
        state.records_list = state.records_list + list(result.records)
    else:
        state.records_list = list(result.records)


def merge_chunks(chunks: List[Dict], target_size: int = 6000) -> List[Dict]:
    """将小分块合并为指定大小的分块

    Args:
        chunks: 原始分块列表，每个分块是 dict，包含 "text" 和可选的 "type"
        target_size: 目标分块大小（字符数），默认 6000

    Returns:
        合并后的分块列表
    """
    if not chunks:
        return []

    merged = []
    current_text = []
    current_len = 0
    current_type = "merged"  # 合并后的类型

    for chunk in chunks:
        chunk_text = chunk.get("text", "")
        chunk_len = len(chunk_text)
        chunk_type = chunk.get("type", "text")

        # 如果当前批次已有内容且加上当前块会超过目标大小，则保存当前批次
        if current_len > 0 and current_len + chunk_len > target_size:
            merged.append({"text": "\n".join(current_text), "type": current_type})
            current_text = [chunk_text]
            current_len = chunk_len
            current_type = chunk_type
        else:
            current_text.append(chunk_text)
            current_len += chunk_len
            # 保持第一个块的类型作为合并块的类型
            if len(current_text) == 1:
                current_type = chunk_type

    # 处理最后一批
    if current_text:
        merged.append({"text": "\n".join(current_text), "type": current_type})

    return merged


def ensure_chunks(bundle: dict, quiet: bool = False) -> list:
    """确保 bundle 中有 chunks，如果没有则自动生成

    Args:
        bundle: 文档bundle字典
        quiet: 安静模式，禁用日志输出

    Returns:
        分块列表，格式: [{"type": "text", "text": "块内容"}, ...]
    """
    # 保持 API 默认安静：不要用 print 直出（网页端/SSE 会被污染）

    # 1. 优先使用已有的 chunks
    chunks = bundle.get("chunks", [])
    if chunks and isinstance(chunks, list) and len(chunks) > 0:
        if not quiet:
            logger.info(f"使用已有的语义分块，共 {len(chunks)} 块")
        return chunks

    # 1.5 从所有文档中收集 chunks
    all_chunks = []
    documents = bundle.get("documents", [])
    for doc in documents:
        if isinstance(doc, dict) and "chunks" in doc and isinstance(doc["chunks"], list):
            all_chunks.extend(doc["chunks"])

    if all_chunks:
        if not quiet:
            logger.info(f"从文档收集语义分块，共 {len(all_chunks)} 块")
        return all_chunks

    # 2. 尝试从 paragraphs 生成
    paragraphs = bundle.get("paragraphs", [])
    if paragraphs and isinstance(paragraphs, list) and len(paragraphs) > 0:
        chunks = []
        current_chunk = []
        current_len = 0
        CHUNK_MAX = 1500

        for para in paragraphs:
            para_len = len(para)
            if current_len + para_len > CHUNK_MAX and current_chunk:
                chunks.append({"type": "text", "text": "\n".join(current_chunk)})
                current_chunk = [para]
                current_len = para_len
            else:
                current_chunk.append(para)
                current_len += para_len

        if current_chunk:
            chunks.append({"type": "text", "text": "\n".join(current_chunk)})

        if not quiet:
            logger.info(f"从 paragraphs 生成分块，共 {len(chunks)} 块")
        return chunks

    # 3. 最后回退：从 all_text 按段落切分
    text = bundle.get("all_text", "")
    if text.strip():
        # 按换行符切分段落
        paras = [p.strip() for p in text.split("\n") if p.strip()]
        chunks = []
        current_chunk = []
        current_len = 0
        CHUNK_MAX = 1500

        for para in paras:
            para_len = len(para)
            if current_len + para_len > CHUNK_MAX and current_chunk:
                chunks.append({"type": "text", "text": "\n".join(current_chunk)})
                current_chunk = [para]
                current_len = para_len
            else:
                current_chunk.append(para)
                current_len += para_len

        if current_chunk:
            chunks.append({"type": "text", "text": "\n".join(current_chunk)})

        if not quiet:
            logger.info(f"从 all_text 生成分块，共 {len(chunks)} 块")
        return chunks

    # 4. 最终回退：一个块包含全部文本
    if not quiet:
        logger.warning("无法生成分块，使用整段文本作为单一块")
    return [{"type": "text", "text": text}] if text else []


def direct_extract(
    template_path: str,
    input_dir: str,
    model_type: Optional[str] = None,
    instruction: Optional[str] = None,
    llm_mode: str = 'full',
    enable_unit_aware: bool = True,
    work_dir: Optional[Path] = None,
    total_timeout: int = 110,
    max_chunks: int = 50,
    quiet: bool = False,
) -> Dict[str, Any]:
    """同步执行文档信息提取

    Args:
        template_path: 模板文件路径（.xlsx / .docx / .json）
        input_dir: 输入文档目录
        model_type: 模型类型（ollama / deepseek / openai），None 则用配置
        instruction: 补充抽取指令（可选）
        llm_mode: LLM抽取模式，可选值：'full'（默认）、'off'（纯规则）。'supplement' 会兼容映射为 'full'
        enable_unit_aware: 是否启用单位感知（预留，暂不影响主流程）
        work_dir: 可选的工作空间目录（持久化场景由调用方提供，None 则无输出落盘）
        total_timeout: 总超时时间（秒），默认110秒
        max_chunks: 最大语义分块数量，默认50
        quiet: 安静模式，禁用控制台输出，默认False

    Returns:
        {
            "records": List[dict],        # 按模板字段对齐的记录数组
            "metadata": dict,             # 处理元数据
        }
    """
    try:
        # 1. 加载文档（先加载，因为无模板时需要文档内容来生成profile）
        bundle = collect_input_bundle(input_dir)
        if not quiet:
            logger.info(
                "direct_extract: file_count=%s, all_text_len=%s",
                bundle.get("file_count", 0),
                len(bundle.get("all_text", "") or ""),
            )

        # 2. 生成 profile（支持三种模式自动判断）
        profile = _load_profile(template_path, instruction, bundle.get("all_text", ""))
        doc_type = profile.get("_doc_type", "")

        if not quiet:
            if doc_type:
                logger.info(f"文档自动分析结果: {doc_type}, 字段数: {len(profile.get('fields', []))}")

        # 3. 先尝试结构化表格提取（Docling DataFrame）
        internal_raw = try_internal_structured_extract(profile, bundle)
        records_list: List = []
        if isinstance(internal_raw, dict):
            records_list = list(internal_raw.get("records") or [])
        elif isinstance(internal_raw, list):
            records_list = internal_raw

        text = bundle.get("all_text", "")
        requested_llm_mode, llm_mode_norm, effective_llm_mode, readiness = _resolve_llm_mode_with_fallback(
            llm_mode, model_type, quiet
        )
        state = ExtractFlowState(
            records_list=list(records_list or []),
            text=text,
            effective_llm_mode=effective_llm_mode,
            requested_llm_mode=requested_llm_mode,
            llm_mode_normalized=llm_mode_norm,
            readiness=readiness,
        )

        # 4. 主线编排：并行多表 -> langextract -> prompt（按需短路）
        _run_word_multi_parallel_stage(
            profile,
            bundle,
            state,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )
        _run_langextract_stage(
            profile,
            bundle,
            state,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )
        _run_prompt_llm_stage(
            profile,
            state,
            model_type=model_type,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )

        records = state.records_list or []

        # 6. 后处理（字段标准化）；并行抽取已在 3b 中 process_by_profile
        if records and not state.parallel_extracted:
            try:
                # 传入 Docling 阅读顺序全文，用于“按原文顺序”稳定排序
                processed = process_by_profile({"records": records, "_source_text": text}, profile)
                records = processed.get("records", records)
                if not quiet:
                    logger.info("后处理完成: %s 条记录", len(records))
            except Exception as e:
                logger.warning(f"后处理失败，使用原始记录: {e}")
        elif state.parallel_extracted and state.processed_bundle is not None:
            records = list(state.processed_bundle.get("records") or records)
            if not quiet:
                logger.info("后处理已在 word_multi_parallel 路径完成: %s 条记录", len(records))

        output_file = None
        # 输出文件：有模板优先按模板写回；无模板则回退为动态 Excel。
        doc_type = profile.get("_doc_type", "")
        if work_dir is not None and records and isinstance(records, list) and len(records) > 0:
            try:
                work_dir.mkdir(parents=True, exist_ok=True)
                template_mode = profile.get("template_mode", "excel_table")
                ts = int(time.time())
                template_suffix = (Path(template_path).suffix or "").lower() if template_path else ""

                if template_path and Path(template_path).exists() and template_suffix in (".xlsx", ".xls", ".xlsm"):
                    output_path = work_dir / f"extracted_{ts}.xlsx"
                    if template_mode == "vertical":
                        vertical_data = records[0] if records and isinstance(records[0], dict) else {}
                        fill_excel_vertical(str(template_path), str(output_path), vertical_data)
                    else:
                        fill_excel_table(
                            template_path=str(template_path),
                            output_path=str(output_path),
                            records=records,
                            header_row=int(profile.get("header_row", 1)),
                            start_row=int(profile.get("start_row", 2)),
                        )
                    output_file = str(output_path)
                    logger.info("按 Excel 模板写回成功: %s", output_file)

                elif template_path and Path(template_path).exists() and template_suffix in (".docx", ".doc"):
                    output_path = work_dir / f"extracted_{ts}.docx"
                    fill_payload: Dict[str, Any]
                    if (
                        profile.get("template_mode") == "word_multi_table"
                        and state.processed_bundle
                        and isinstance(state.processed_bundle, dict)
                        and state.processed_bundle.get("_table_groups")
                    ):
                        fill_payload = {
                            "records": records,
                            "_table_groups": state.processed_bundle.get("_table_groups"),
                        }
                    else:
                        fill_payload = {"records": records}
                    fill_word_table(
                        template_path=str(template_path),
                        output_path=str(output_path),
                        records=fill_payload,
                        table_index=int(profile.get("table_index", 0)),
                        header_row=int(profile.get("header_row", 0)),
                        start_row=int(profile.get("start_row", 1)),
                    )
                    output_file = str(output_path)
                    logger.info("按 Word 模板写回成功: %s", output_file)

                else:
                    # 无模板或模板不可写回：回退动态 Excel
                    output_path = work_dir / f"extracted_{ts}.xlsx"
                    create_excel_from_records(str(output_path), records)
                    output_file = str(output_path)
                    logger.info("模板不可写回，回退动态 Excel: %s", output_file)
            except Exception as e:
                logger.warning(f"生成输出文件失败: {e}")
        elif work_dir is not None:
            logger.info("无有效记录，跳过文件生成")

        # 调试信息（可选）
        if not quiet and records and len(records) > 0:
            logger.info(f"提取完成: {len(records)} 条记录")

        meta: Dict[str, Any] = {
            "file_count": bundle.get("file_count", 0),
            "record_count": len(records),
            "template_mode": profile.get("template_mode", "unknown"),
            "task_mode": profile.get("task_mode", "unknown"),
            "doc_type": doc_type,
            "profile_auto_generated": bool(profile.get("_doc_type")),
            "word_multi_parallel": state.parallel_extracted,
            "llm_mode_requested": state.requested_llm_mode,
            "llm_mode_normalized": state.llm_mode_normalized,
            "llm_mode_effective": state.effective_llm_mode,
            "model_ready": bool(state.readiness.get("ready")),
            "model_ready_reason": str(state.readiness.get("reason") or ""),
        }
        if (
            state.parallel_extracted
            and state.processed_bundle is not None
            and state.processed_bundle.get("_table_groups")
        ):
            meta["_table_groups"] = state.processed_bundle.get("_table_groups")

        return {
            "records": records,
            "metadata": meta,
            "output_file": output_file,
        }

    except Exception as e:
        logger.error(f"直接抽取失败: {e}", exc_info=True)
        return {
            "records": [],
            "metadata": {"error": str(e)},
        }


def _load_profile(template_path: str, instruction: Optional[str], document_text: str = "") -> dict:
    """从模板文件、用户指令或文档内容自动生成 profile

    三种模式自动判断：
    1. 有模板文件 → 严格按模板表头生成 profile（规则优先，LLM辅助）
    2. 无模板但有指令 → LLM 根据指令生成 profile
    3. 无模板无指令 → 自动分析文档内容，智能推断最优字段结构
    """
    # 模式1：有模板文件
    if template_path and template_path.strip():
        path = Path(template_path)

        if path.suffix.lower() == ".json":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if path.exists():
            profile = generate_profile_from_template(
                template_path=template_path,
                use_llm=bool(instruction),
                mode="auto",
                user_description=instruction,
            )
            if profile and profile.get("fields"):
                profile = apply_instruction_runtime_hints(profile, instruction or "")
                if profile.get("template_mode") == "word_multi_table":
                    profile = apply_word_multi_instruction_constraints(profile, profile.get("instruction", ""))
                return profile

    # 模式2：无模板但有用户指令
    if instruction and instruction.strip():
        # 使用指令 + 文档样本来生成 profile
        from src.core.profile import generate_profile_smart
        profile = generate_profile_smart(
            template_path="",
            instruction=instruction,
            document_sample=document_text[:3000] if document_text else "",
        )
        if profile and profile.get("fields"):
            return apply_instruction_runtime_hints(profile, instruction or "")

    # 模式3：无模板无指令 → 全自动文档分析
    if document_text and document_text.strip():
        logger.info("无模板无指令，启动文档自动分析...")
        profile = generate_profile_from_document(document_text)
        if profile and profile.get("fields"):
            return apply_instruction_runtime_hints(profile, instruction or "")

    # 兜底
    from src.core.profile import _default_profile
    return _default_profile(template_path)
