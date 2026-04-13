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
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.profile import generate_profile_from_template, generate_profile_from_document
from src.core.reader import collect_input_bundle, try_internal_structured_extract
from src.core.postprocess import process_by_profile, validate_required_fields
from src.core.extractor import UniversalExtractor
from src.core.writers import create_excel_from_records
from src.core.deadline_context import create_deadline_context

logger = logging.getLogger(__name__)


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
        llm_mode: LLM抽取模式，可选值：'full'（始终全文抽取，默认）、'supplement'（仅补充缺失字段）
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
        # 0. 模型可用性检查（已跳过，因为 Ollama 返回非 JSON 导致误判）
        pass
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
        records = try_internal_structured_extract(profile, bundle)

        # 4. 优先尝试 langextract（云 API 或大模型时更快更精确）
        import time
        text = bundle.get("all_text", "")
        langextract_used = False
        try:
            from src.adapters.langextract_adapter import extract_with_langextract
            # 确保有 chunks（健壮的兜底逻辑）
            chunks = ensure_chunks(bundle, quiet=quiet)

            # 应用分块合并优化
            original_count = len(chunks)

            # 根据文档长度动态调整目标分块大小
            total_chars = sum(len(chunk.get("text", "")) for chunk in chunks)
            if total_chars > 20000:  # 长文档
                target_size = 8000
            elif total_chars > 10000:  # 中长文档
                target_size = 6000
            else:  # 短文档
                target_size = 4000

            # 限制最大分块数（如果设置了 max_chunks）
            if max_chunks and original_count > max_chunks:
                # 保持文档阅读顺序：直接截断前 N 个分块
                # 说明：此前“按长度挑选长分块”会打乱顺序，且可能跳过文档尾部导致记录顺序/覆盖不稳定。
                chunks = chunks[:max_chunks]
                if not quiet:
                    logger.info(f"限制分块数: {original_count} -> {len(chunks)} (max_chunks={max_chunks})")

            # 合并小分块
            merged_chunks = merge_chunks(chunks, target_size=target_size)

            text_chunks = merged_chunks
            if not quiet:
                logger.info(f"分块合并: {original_count} -> {len(text_chunks)} 块, 目标大小={target_size}字符, 总字符={total_chars}")

            logger.info(f"准备调用 langextract，块数: {len(text_chunks)}")
            lx_records = extract_with_langextract(
                text_chunks, profile,
                time_budget=total_timeout,
                quiet=quiet,
            )
            if lx_records is not None and len(lx_records) > 0:
                if not quiet:
                    logger.info(f"langextract 提取成功: {len(lx_records)} 条记录")
                records = (records or []) + lx_records
                langextract_used = True
        except Exception as e:
            logger.info(f"langextract 不可用: {e}")
            import traceback
            logger.info(f"异常详情: {traceback.format_exc()}")

        # 5. LLM抽取（langextract 未成功时回退到 prompt 方案）
        if not langextract_used:
            # 创建deadline上下文
            deadline_ctx = create_deadline_context(
                name=f"direct_extract_{int(time.time())}",
                timeout_seconds=total_timeout if total_timeout > 0 else None
            )

            config = {
                "llm_mode": llm_mode,
                "total_timeout": total_timeout,
                "max_chunks": max_chunks,
                "quiet": quiet,
                "total_deadline": time.time() + total_timeout if total_timeout else None,
                "deadline_context": deadline_ctx
            }
            if model_type:
                config["model_type"] = model_type
            extractor = UniversalExtractor(config=config)
            result = extractor.extract(text, profile)
            if records:
                records = records + result.records
            else:
                records = result.records

        records = records or []

        # 6. 后处理（字段标准化）- 重新启用
        if records:
            try:
                from src.core.postprocess import process_by_profile
                # 传入 Docling 阅读顺序全文，用于“按原文顺序”稳定排序
                processed = process_by_profile({"records": records, "_source_text": text}, profile)
                records = processed.get("records", records)
                if not quiet:
                    logger.info("后处理完成: %s 条记录", len(records))
            except Exception as e:
                logger.warning(f"后处理失败，使用原始记录: {e}")

        output_file = None
        # 智能文件输出：如果是结构化类型且 work_dir 存在，则生成表格文件
        doc_type = profile.get("_doc_type", "")
        if doc_type in ("structured_table", "structured_single") and work_dir is not None:
            if records and isinstance(records, list) and len(records) > 0:
                try:
                    # 确保 work_dir 存在
                    work_dir.mkdir(parents=True, exist_ok=True)
                    # 根据输入文件类型决定输出格式（默认Excel）
                    # 获取第一个输入文件的扩展名（如果有）
                    first_file = None
                    documents = bundle.get("documents", [])
                    if documents and isinstance(documents, list) and len(documents) > 0:
                        doc = documents[0]
                        if isinstance(doc, dict) and "path" in doc:
                            first_file = Path(doc["path"])
                    # 决定输出格式（暂时只支持Excel，Word输出需要模板）
                    # 生成Excel文件
                    output_filename = f"extracted_{int(time.time())}.xlsx"
                    output_path = work_dir / output_filename
                    create_excel_from_records(str(output_path), records)
                    output_file = str(output_path)
                    logger.info(f"自动生成表格文件: {output_file}")
                except Exception as e:
                    logger.warning(f"生成输出文件失败: {e}")
            else:
                logger.info("无有效记录，跳过文件生成")

        # 调试信息（可选）
        if not quiet and records and len(records) > 0:
            logger.info(f"提取完成: {len(records)} 条记录")

        return {
            "records": records,
            "metadata": {
                "file_count": bundle.get("file_count", 0),
                "record_count": len(records),
                "template_mode": profile.get("template_mode", "unknown"),
                "task_mode": profile.get("task_mode", "unknown"),
                "doc_type": doc_type,
                "profile_auto_generated": bool(profile.get("_doc_type")),
            },
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
            return profile

    # 模式3：无模板无指令 → 全自动文档分析
    if document_text and document_text.strip():
        logger.info("无模板无指令，启动文档自动分析...")
        profile = generate_profile_from_document(document_text)
        if profile and profile.get("fields"):
            return profile

    # 兜底
    from src.core.profile import _default_profile
    return _default_profile(template_path)
