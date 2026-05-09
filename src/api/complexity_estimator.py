from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import UploadFile

from src.core.llm_mode import normalize_llm_mode


async def estimate_document_complexity(
    files: List[UploadFile],
    template_file: Optional[UploadFile] = None,
    task_mode: str = "full",
) -> Dict[str, Any]:
    """估算文档处理复杂度和处理时间（基于内容分析）。"""
    task_mode = normalize_llm_mode(task_mode)
    temp_files: List[str] = []
    document_paths: List[str] = []
    estimator_mode = os.environ.get("A23_COMPLEXITY_ESTIMATOR", "fast").strip().lower()

    def _fallback_estimate() -> Dict[str, Any]:
        total_size_bytes = 0
        total_text_estimate = 0

        for file in files:
            file_size = 0
            if hasattr(file, "size"):
                file_size = int(file.size or 0)
            else:
                try:
                    current_pos = file.file.tell()
                    file.file.seek(0, 2)
                    file_size = int(file.file.tell() or 0)
                    file.file.seek(current_pos)
                except Exception:
                    file_size = 0

            total_size_bytes += file_size
            filename = file.filename or ""
            if any(filename.lower().endswith(ext) for ext in [".txt", ".md", ".json", ".csv"]):
                total_text_estimate += file_size
            elif any(filename.lower().endswith(ext) for ext in [".xlsx", ".xls", ".docx", ".doc"]):
                total_text_estimate += file_size * 3
            elif filename.lower().endswith(".pdf"):
                total_text_estimate += file_size * 2
            else:
                total_text_estimate += file_size

        if template_file:
            template_size = 0
            if hasattr(template_file, "size"):
                template_size = int(template_file.size or 0)
            else:
                try:
                    current_pos = template_file.file.tell()
                    template_file.file.seek(0, 2)
                    template_size = int(template_file.file.tell() or 0)
                    template_file.file.seek(current_pos)
                except Exception:
                    template_size = 0
            total_size_bytes += template_size

        total_size_mb = total_size_bytes / (1024 * 1024)
        estimated_chunks = max(1, total_text_estimate // 3000)
        estimated_processing_time = 2.0 + (estimated_chunks * 3.0)
        max_chunks_threshold = 30
        recommendation = "direct"
        if estimated_chunks <= 10:
            complexity_level = "low"
        elif estimated_chunks <= max_chunks_threshold:
            complexity_level = "medium"
        else:
            complexity_level = "high"

        return {
            "total_size_bytes": total_size_bytes,
            "total_size_mb": round(total_size_mb, 2),
            "total_text_length_estimate": total_text_estimate,
            "estimated_chunks": estimated_chunks,
            "estimated_processing_time_seconds": round(estimated_processing_time, 1),
            "complexity_level": complexity_level,
            "recommendation": recommendation,
            "max_chunks_threshold": max_chunks_threshold,
            "max_size_mb_threshold": 5.0,
            "text_complexity_score": 0,
            "structure_complexity_score": 0,
            "extraction_complexity_score": 0,
            "overall_score": 0,
            "estimated_pages": 0,
            "field_count": 0,
            "estimated_output_tokens": 0,
            "exceeds_direct_threshold": estimated_chunks > max_chunks_threshold,
            "exceeds_timeout_threshold": estimated_processing_time > 30,
            "estimator": "fast",
        }

    try:
        for file in files:
            suffix = Path(file.filename or "unknown").suffix
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name
                temp_files.append(tmp_path)
                document_paths.append(tmp_path)
                await file.seek(0)

        template_path = None
        if template_file:
            suffix = Path(template_file.filename or "template").suffix
            with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix, delete=False) as tmp:
                content = await template_file.read()
                tmp.write(content)
                template_path = tmp.name
                temp_files.append(template_path)
                await template_file.seek(0)

        logger = logging.getLogger(__name__)
        logger.info("复杂度估算模式: %s", estimator_mode or "fast")
        if estimator_mode not in ("docling", "accurate", "deep"):
            return _fallback_estimate()

        try:
            from src.adapters.docling_adapter import DoclingParser, DOCLING_AVAILABLE as _DOCLING_AVAILABLE
        except Exception:
            _DOCLING_AVAILABLE = False
            DoclingParser = None  # type: ignore

        if _DOCLING_AVAILABLE and DoclingParser is not None and document_paths:
            try:
                total_size_bytes = 0
                for tf in temp_files:
                    try:
                        total_size_bytes += int(os.path.getsize(tf) or 0)
                    except Exception:
                        pass
                if template_path:
                    try:
                        total_size_bytes += int(os.path.getsize(template_path) or 0)
                    except Exception:
                        pass
                total_size_mb = total_size_bytes / (1024 * 1024)

                docling_paths: List[str] = []
                for pth in document_paths:
                    suffix = (Path(pth).suffix or "").lower()
                    if suffix in (".docx", ".pdf", ".pptx", ".xlsx", ".xls"):
                        docling_paths.append(pth)
                if total_size_mb > 3.0 or len(docling_paths) > 2:
                    logger.info("深度估算跳过（size=%.2fMB, files=%s），回退 fast 估算", total_size_mb, len(docling_paths))
                    return _fallback_estimate()

                parser = DoclingParser(enable_ocr=False)
                total_chars = 0
                total_chunks = 0
                total_tables = 0
                total_pages = 0

                for pth in docling_paths:
                    suffix = (Path(pth).suffix or "").lower()
                    if suffix not in (".docx", ".pdf", ".pptx", ".xlsx", ".xls"):
                        continue
                    parsed = parser.parse(pth)
                    if parsed.get("error"):
                        continue
                    total_chars += len(parsed.get("text") or "")
                    total_chunks += len(parsed.get("chunks") or [])
                    total_tables += len(parsed.get("tables") or [])
                    total_pages += int(parsed.get("pages") or 0)

                estimated_chunks = max(1, total_chunks) if total_chunks > 0 else max(1, total_chars // 1500)
                per_chunk_time = 3.0 if task_mode != "off" else 0.5
                estimated_processing_time = 2.0 + (estimated_chunks * per_chunk_time)
                max_chunks_threshold = 30
                exceeds_direct = estimated_chunks > max_chunks_threshold
                complexity_level = "low"
                if exceeds_direct:
                    complexity_level = "high"
                elif estimated_chunks > 10:
                    complexity_level = "medium"

                return {
                    "total_size_bytes": total_size_bytes,
                    "total_size_mb": round(total_size_mb, 2),
                    "total_text_length_estimate": total_chars,
                    "estimated_chunks": int(estimated_chunks),
                    "estimated_processing_time_seconds": round(estimated_processing_time, 1),
                    "complexity_level": complexity_level,
                    "recommendation": "direct",
                    "max_chunks_threshold": max_chunks_threshold,
                    "max_size_mb_threshold": 5.0,
                    "text_complexity_score": 0,
                    "structure_complexity_score": 0,
                    "extraction_complexity_score": 0,
                    "overall_score": 0,
                    "estimated_pages": total_pages,
                    "field_count": 0,
                    "estimated_output_tokens": 0,
                    "tables_count": total_tables,
                    "exceeds_direct_threshold": exceeds_direct,
                    "exceeds_timeout_threshold": estimated_processing_time > 30,
                    "estimator": "docling",
                }
            except Exception:
                pass

        return _fallback_estimate()
    except Exception as e:
        logging.getLogger(__name__).warning("复杂度估算失败: %s", e)
        return _fallback_estimate()
    finally:
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except Exception:
                pass
