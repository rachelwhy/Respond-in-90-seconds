"""
Docling 文档解析器 - 唯一的文档解析入口

高级功能：
- 布局分析与阅读顺序还原
- 表格直接提取为 pandas DataFrame
- 语义分块（按标题/段落/表格边界）
- 可选 OCR 支持（扫描件）
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
import traceback

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        EasyOcrOptions,
        TesseractOcrOptions,
    )
    DOCLING_AVAILABLE = True
except ImportError:
    DOCLING_AVAILABLE = False

from .base import BaseParser

logger = logging.getLogger(__name__)

# OCR 开关（通过环境变量控制）
import os
ENABLE_OCR = os.environ.get("A23_ENABLE_OCR", "false").lower() == "true"


def _build_converter(enable_ocr: bool = False):
    """构建 Docling DocumentConverter，支持 OCR 可选"""
    if not DOCLING_AVAILABLE:
        return None
    try:
        if enable_ocr:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = True
            pipeline_options.ocr_options = EasyOcrOptions(lang=["ch_sim", "en"])
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            return DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )
        else:
            pipeline_options = PdfPipelineOptions()
            pipeline_options.do_ocr = False
            pipeline_options.do_table_structure = True
            pipeline_options.table_structure_options.do_cell_matching = True
            return DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
            )
    except Exception:
        # 降级：使用默认配置
        try:
            return DocumentConverter()
        except Exception:
            return None


class DoclingParser(BaseParser):
    """Docling 文档解析器 — 所有非纯文本格式的唯一解析入口"""

    parser_type = "docling"

    def __init__(self, enable_ocr: bool = ENABLE_OCR):
        super().__init__()
        self.enable_ocr = enable_ocr
        if not DOCLING_AVAILABLE:
            logger.warning("Docling 库不可用，解析器以降级模式运行")

    def parse(self, path) -> Dict[str, Any]:
        """解析文档，返回统一结构

        Returns:
            {
                "parser_type": "docling",
                "text": str,                       # 按阅读顺序拼接的全文
                "paragraphs": List[str],           # 段落列表
                "tables": List[dict],              # 表格（含 markdown / dataframe）
                "tables_dataframes": List[pd.DataFrame],  # 直接可用的 DataFrame
                "chunks": List[dict],              # 语义分块（≤1500字）
                "pages": int,
                "warnings": List[str],
                "error": str | None,
            }
        """
        path = Path(path)
        result = {
            "parser_type": self.parser_type,
            "type": self.parser_type,
            "path": str(path),
            "file_name": path.name,
            "text": "",
            "paragraphs": [],
            "tables": [],
            "tables_dataframes": [],
            "chunks": [],
            "pages": 0,
            "warnings": [],
            "error": None,
        }

        if not path.exists():
            result["error"] = f"文件不存在: {path}"
            return result

        if not DOCLING_AVAILABLE:
            result["warnings"].append("Docling 库不可用")
            return result

        try:
            converter = _build_converter(enable_ocr=self.enable_ocr)
            if converter is None:
                result["error"] = "无法创建 Docling 转换器"
                return result

            logger.info(f"Docling 解析: {path}")
            conversion_result = converter.convert(str(path))
            doc = conversion_result.document

            if doc is None:
                result["warnings"].append("Docling 返回空文档")
                return result

            # ── 1. 按阅读顺序提取文本 ──────────────────────────────────────
            text_parts = []
            paragraphs = []
            chunks = []
            current_chunk_parts = []
            current_chunk_len = 0
            CHUNK_MAX = 1500

            def _flush_chunk(label: str = "text"):
                if current_chunk_parts:
                    chunk_text = "\n".join(current_chunk_parts).strip()
                    if chunk_text:
                        chunks.append({"type": label, "text": chunk_text})

            try:
                for item, _ in doc.iterate_items():
                    item_type = type(item).__name__
                    item_text = ""

                    if hasattr(item, "text") and item.text:
                        item_text = item.text.strip()
                    elif item_type in ("FormulaItem", "EquationItem") and hasattr(item, "export_to_latex"):
                        try:
                            item_text = item.export_to_latex().strip()
                        except Exception:
                            pass
                    if not item_text and hasattr(item, "export_to_markdown"):
                        try:
                            item_text = item.export_to_markdown().strip()
                        except Exception:
                            pass

                    if not item_text:
                        continue

                    text_parts.append(item_text)

                    # 段落/标题 → 合并进当前 chunk
                    if item_type in ("TextItem", "SectionHeaderItem", "ParagraphItem"):
                        paragraphs.append(item_text)
                        if current_chunk_len + len(item_text) > CHUNK_MAX:
                            _flush_chunk("text")
                            current_chunk_parts = [item_text]
                            current_chunk_len = len(item_text)
                        else:
                            current_chunk_parts.append(item_text)
                            current_chunk_len += len(item_text)

                    # 表格 → 独立 chunk（类型标记为 table）
                    elif item_type == "TableItem":
                        _flush_chunk("text")
                        current_chunk_parts = []
                        current_chunk_len = 0
                        chunks.append({"type": "table", "text": item_text})

                    # 公式 → 独立 chunk（类型标记为 formula，保留 LaTeX）
                    elif item_type in ("FormulaItem", "EquationItem"):
                        _flush_chunk("text")
                        current_chunk_parts = []
                        current_chunk_len = 0
                        chunks.append({"type": "formula", "text": item_text})

                    # 代码块 → 独立 chunk（类型标记为 code）
                    elif item_type in ("CodeItem", "ListingItem"):
                        _flush_chunk("text")
                        current_chunk_parts = []
                        current_chunk_len = 0
                        chunks.append({"type": "code", "text": item_text})

                _flush_chunk("text")

            except Exception as e:
                # iterate_items 不可用时降级
                logger.debug(f"iterate_items 不可用，降级提取: {e}")
                try:
                    md_text = doc.export_to_markdown()
                    text_parts = [md_text]
                    paragraphs = [p for p in md_text.split("\n") if p.strip()]
                    # 简单分块
                    for i in range(0, len(md_text), CHUNK_MAX):
                        chunks.append({"type": "text", "text": md_text[i:i + CHUNK_MAX]})
                except Exception:
                    if hasattr(doc, "text") and doc.text:
                        text_parts = [doc.text]
                        paragraphs = [p for p in doc.text.split("\n") if p.strip()]

            result["text"] = "\n\n".join(text_parts)
            result["paragraphs"] = paragraphs
            result["chunks"] = chunks

            # ── 2. 表格提取（DataFrame + Markdown）─────────────────────────
            tables_raw = []
            dfs = []

            try:
                if hasattr(doc, "tables") and doc.tables:
                    for i, table in enumerate(doc.tables):
                        df, markdown, raw_data = self._extract_table(table, i)
                        tables_raw.append({
                            "index": i,
                            "data": raw_data,
                            "markdown": markdown,
                            "row_count": len(raw_data),
                            "column_count": len(raw_data[0]) if raw_data else 0,
                        })
                        if df is not None:
                            dfs.append(df)
            except Exception as e:
                logger.warning(f"表格提取失败: {e}")
                result["warnings"].append(f"表格提取部分失败: {e}")

            result["tables"] = tables_raw
            result["tables_dataframes"] = dfs

            # ── 3. 页面数 ─────────────────────────────────────────────────
            try:
                if hasattr(doc, "pages") and doc.pages:
                    result["pages"] = len(doc.pages)
            except Exception:
                pass

            logger.info(
                f"Docling 解析完成: {path.name} | "
                f"文本 {len(result['text'])} 字 | "
                f"表格 {len(dfs)} 个 | "
                f"块 {len(chunks)} 个"
            )

        except Exception as e:
            msg = f"Docling 解析失败: {e}"
            result["error"] = msg
            result["warnings"].append(msg)
            logger.error(f"{msg} | 文件: {path}")
            logger.debug(traceback.format_exc())

        return result

    def get_text_in_reading_order(self, path) -> str:
        """按阅读顺序返回全文（用于双栏/复杂布局文档）"""
        result = self.parse(path)
        return result.get("text", "")

    def _extract_table(self, table, index: int):
        """提取单个表格，返回 (DataFrame, markdown_str, list_of_dicts)"""
        raw_data = []
        df = None
        markdown = ""

        try:
            # 方式1：export_to_dataframe（Docling ≥ 2.x，原生处理合并单元格）
            if PANDAS_AVAILABLE and hasattr(table, "export_to_dataframe"):
                try:
                    df = table.export_to_dataframe()
                    df.attrs["_is_merged_handled"] = True
                    raw_data = df.to_dict(orient="records")
                    markdown = df.to_markdown(index=False) if hasattr(df, "to_markdown") else ""
                    return df, markdown, raw_data
                except Exception:
                    pass

            # 方式2：export_to_markdown
            if hasattr(table, "export_to_markdown"):
                try:
                    markdown = table.export_to_markdown()
                    raw_data = self._markdown_to_dicts(markdown)
                    if PANDAS_AVAILABLE and raw_data:
                        df = pd.DataFrame(raw_data)
                    return df, markdown, raw_data
                except Exception:
                    pass

            # 方式3：data 属性（旧版 Docling），增加合并单元格展开处理
            if hasattr(table, "data") and table.data:
                table_data = table.data
                if hasattr(table_data, "grid") and table_data.grid:
                    grid = table_data.grid
                    if grid:
                        expanded = self._expand_merged_cells(grid)
                        if expanded and len(expanded) > 1:
                            headers = expanded[0]
                            for row in expanded[1:]:
                                row_dict = {}
                                for j, val in enumerate(row):
                                    col_name = headers[j] if j < len(headers) else f"col_{j}"
                                    row_dict[col_name] = val
                                if any(v for v in row_dict.values()):
                                    raw_data.append(row_dict)
                        else:
                            # 回退：无合并信息时按原逻辑处理
                            headers = [cell.text if hasattr(cell, "text") else str(cell) for cell in grid[0]]
                            for row in grid[1:]:
                                row_dict = {}
                                for j, cell in enumerate(row):
                                    col_name = headers[j] if j < len(headers) else f"col_{j}"
                                    row_dict[col_name] = cell.text if hasattr(cell, "text") else str(cell)
                                if row_dict:
                                    raw_data.append(row_dict)
                    if PANDAS_AVAILABLE and raw_data:
                        df = pd.DataFrame(raw_data)
                        df.attrs["_is_merged_handled"] = True
                    markdown = self._dicts_to_markdown(raw_data)

        except Exception as e:
            logger.warning(f"表格 {index} 提取失败: {e}")

        return df, markdown, raw_data

    def _expand_merged_cells(self, grid) -> List[List[str]]:
        """展开合并单元格，返回行优先的二维字符串列表（每行一个列表）。

        Docling 的 TableCell 可能包含 row_span / col_span 属性。
        对合并单元格，将其文本值复制到所有被合并的子位置。
        """
        if not grid:
            return []

        num_rows = len(grid)
        # 估算最大列数
        num_cols = max((len(row) for row in grid), default=0)
        if num_cols == 0:
            return []

        # 预分配二维列表
        expanded: List[List[str]] = [[""] * num_cols for _ in range(num_rows)]
        # 标记哪些位置已被合并填充
        filled = [[False] * num_cols for _ in range(num_rows)]

        for r, row in enumerate(grid):
            col_cursor = 0  # 当前逻辑列位置
            for cell in row:
                # 找下一个未填充的列
                while col_cursor < num_cols and filled[r][col_cursor]:
                    col_cursor += 1
                if col_cursor >= num_cols:
                    break

                text = ""
                if hasattr(cell, "text") and cell.text:
                    text = cell.text.strip()
                elif hasattr(cell, "content") and cell.content:
                    text = str(cell.content).strip()

                row_span = getattr(cell, "row_span", 1) or 1
                col_span = getattr(cell, "col_span", 1) or 1

                # 确保不越界
                row_end = min(r + row_span, num_rows)
                col_end = min(col_cursor + col_span, num_cols)

                for rr in range(r, row_end):
                    for cc in range(col_cursor, col_end):
                        if not filled[rr][cc]:
                            expanded[rr][cc] = text
                            filled[rr][cc] = True

                col_cursor = col_end

        return expanded

    def _markdown_to_dicts(self, markdown: str) -> List[Dict[str, Any]]:
        """将 Markdown 表格解析为字典列表"""
        lines = [l.strip() for l in markdown.strip().split("\n") if l.strip()]
        if len(lines) < 2:
            return []

        # 找表头行（第一个含 | 的行）
        header_line = None
        data_lines = []
        for i, line in enumerate(lines):
            if "|" not in line:
                continue
            if header_line is None:
                header_line = line
            elif all(c in "|-: " for c in line):
                continue  # 分隔行
            else:
                data_lines.append(line)

        if header_line is None:
            return []

        headers = [h.strip() for h in header_line.split("|") if h.strip()]
        rows = []
        for line in data_lines:
            cells = [c.strip() for c in line.split("|")]
            cells = [c for c in cells if c != ""]  # 去掉首尾空列
            row = {}
            for j, h in enumerate(headers):
                row[h] = cells[j] if j < len(cells) else ""
            rows.append(row)
        return rows

    def _dicts_to_markdown(self, data: List[Dict]) -> str:
        """字典列表转 Markdown 表格"""
        if not data:
            return ""
        cols = list(data[0].keys())
        lines = ["| " + " | ".join(cols) + " |",
                 "| " + " | ".join(["---"] * len(cols)) + " |"]
        for row in data:
            vals = [str(row.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols]
            lines.append("| " + " | ".join(vals) + " |")
        return "\n".join(lines)
