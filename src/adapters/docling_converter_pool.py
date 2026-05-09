"""
Docling DocumentConverter 进程内单例与按次轮转。

表格结构固定采用高精度模式（TableFormerMode.ACCURATE），无需环境变量切换。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Optional

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False
    DocumentConverter = None  # type: ignore
    TableFormerMode = None  # type: ignore

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_shared_converters: Dict[bool, Any] = {}
_rotation_counts: Dict[bool, int] = {}


def _max_docs_before_rotate() -> int:
    """默认不轮转转换器（0），避免长驻任务中途换实例导致困惑；内存由部署侧 worker 策略保障。"""
    return 0


def create_document_converter(enable_ocr: bool = False) -> Optional[Any]:
    """新建 DocumentConverter（不经缓存）。供测试或特殊场景使用。"""
    if not DOCLING_AVAILABLE:
        return None
    try:
        if enable_ocr:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.ocr_options = EasyOcrOptions(lang=["ch_sim", "en"])
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            try:
                pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
            except Exception:
                logger.warning("当前 Docling 版本忽略 table_structure_options.mode，仍使用默认表格模型")
            return DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = True
        pipeline_options.table_structure_options.do_cell_matching = True
        try:
            pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
        except Exception:
            logger.warning("当前 Docling 版本忽略 table_structure_options.mode，仍使用默认表格模型")
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    except Exception:
        try:
            return DocumentConverter()
        except Exception:
            return None


def record_convert_for_rotation(enable_ocr: bool) -> None:
    """每次成功 convert 后调用；达到阈值则丢弃缓存实例，下次懒重建。"""
    max_docs = _max_docs_before_rotate()
    if max_docs <= 0:
        return
    with _lock:
        _rotation_counts[enable_ocr] = _rotation_counts.get(enable_ocr, 0) + 1
        if _rotation_counts[enable_ocr] >= max_docs:
            _shared_converters.pop(enable_ocr, None)
            _rotation_counts[enable_ocr] = 0
            logger.info(
                "Docling DocumentConverter 已按次数轮转 (enable_ocr=%s, max_docs=%s)",
                enable_ocr,
                max_docs,
            )


def get_shared_document_converter(enable_ocr: bool = False) -> Optional[Any]:
    """获取进程内共享的 DocumentConverter（懒创建、线程安全）。"""
    if not DOCLING_AVAILABLE:
        return None
    with _lock:
        if enable_ocr not in _shared_converters:
            conv = create_document_converter(enable_ocr)
            _shared_converters[enable_ocr] = conv
            if conv is not None:
                logger.info("Docling DocumentConverter 已创建并缓存 (enable_ocr=%s)", enable_ocr)
        return _shared_converters[enable_ocr]
