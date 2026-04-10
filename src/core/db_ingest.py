"""
数据入库调度器

职责：
  1. 判断文档是否易于结构化（表格 / 抽取结果 / 结构化文件）
  2. 结构化 → 入 a23_structured_records
  3. 非结构化 → 入 a23_raw_documents（暂存，后期处理）

分类规则（按优先级）：
  STRUCTURED  - 有 Docling 表格 DataFrame / 已有抽取 records / 纯电子表格（xlsx/csv）
  UNSTRUCTURED - 纯文本、Word 纯文叙述、扫描 PDF、Markdown 等
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认认定为结构化的文件后缀
_STRUCTURED_EXTENSIONS = {".xlsx", ".xls", ".csv"}
# 即使有文本也优先按非结构化存储的后缀
_UNSTRUCTURED_EXTENSIONS = {".txt", ".md", ".rst"}

# 最少需要多少行有效记录才判为"可结构化"
_MIN_STRUCTURED_ROWS = 1


# ── 分类 ─────────────────────────────────────────────────────────────────────

def classify_document(
    parse_result: Dict[str, Any],
    extraction_records: Optional[List[Dict]] = None,
) -> str:
    """判断文档是否可结构化入库。

    Returns:
        "structured" 或 "unstructured"
    """
    # 1. 已经有抽取结果中的 records → 结构化
    if extraction_records and len(extraction_records) >= _MIN_STRUCTURED_ROWS:
        return "structured"

    # 2. Docling 提取到了表格 DataFrame → 结构化
    dfs = parse_result.get("tables_dataframes", [])
    if dfs and any(not df.empty for df in dfs):
        return "structured"

    # 3. tables_raw 有数据行 → 结构化
    tables_raw = parse_result.get("tables", [])
    if any(t.get("row_count", 0) >= _MIN_STRUCTURED_ROWS for t in tables_raw):
        return "structured"

    # 4. 文件后缀判断
    suffix = Path(parse_result.get("path", "")).suffix.lower()
    if suffix in _STRUCTURED_EXTENSIONS:
        return "structured"

    # 5. 其余归为非结构化
    return "unstructured"


# ── 单文档入库 ────────────────────────────────────────────────────────────────

def ingest_document(
    task_id: str,
    parse_result: Dict[str, Any],
    extraction_records: Optional[List[Dict]] = None,
    template_name: str = "",
) -> Dict[str, Any]:
    """将单个文档的解析/抽取结果入库。

    Returns:
        {
            "source_file": str,
            "category": "structured" | "unstructured",
            "rows_inserted": int,
            "error": str | None,
        }
    """
    from src.adapters.mysql_adapter import get_mysql_adapter

    source_file = Path(parse_result.get("path", "unknown")).name
    file_type = Path(parse_result.get("path", "")).suffix.lower().lstrip(".")
    category = classify_document(parse_result, extraction_records)

    result: Dict[str, Any] = {
        "source_file": source_file,
        "category": category,
        "rows_inserted": 0,
        "error": None,
    }

    adapter = get_mysql_adapter()
    if not adapter.is_available():
        result["error"] = "MySQL 不可用（未安装 pymysql 或连接失败）"
        logger.warning(f"跳过入库 [{source_file}]: {result['error']}")
        return result

    try:
        adapter.ensure_tables()
    except Exception as e:
        result["error"] = f"建表失败: {e}"
        return result

    try:
        if category == "structured":
            records = _build_structured_records(parse_result, extraction_records)
            n = adapter.insert_structured_records(
                task_id=task_id,
                source_file=source_file,
                records=records,
                template_name=template_name,
            )
            result["rows_inserted"] = n
        else:
            content = parse_result.get("text", "")
            metadata = {
                "pages": parse_result.get("pages", 0),
                "char_count": len(content),
                "parser_type": parse_result.get("parser_type", ""),
                "warnings": parse_result.get("warnings", [])[:5],
            }
            adapter.insert_raw_document(
                task_id=task_id,
                source_file=source_file,
                content=content,
                file_type=file_type,
                metadata=metadata,
            )
            result["rows_inserted"] = 1

    except Exception as e:
        result["error"] = str(e)
        logger.error(f"入库失败 [{source_file}]: {e}")

    return result


# ── 批量入库（整个 bundle）────────────────────────────────────────────────────

def ingest_bundle(
    task_id: str,
    bundle: Dict[str, Any],
    extraction_result: Optional[Dict[str, Any]] = None,
    template_name: str = "",
) -> Dict[str, Any]:
    """将 collect_input_bundle 的结果批量入库。

    Args:
        task_id:          任务 ID
        bundle:           collect_input_bundle() 的返回值
        extraction_result: process_by_profile() 的返回值（可选）
        template_name:    模板名（用于标注来源）

    Returns:
        {
            "task_id": str,
            "total_files": int,
            "structured_count": int,
            "unstructured_count": int,
            "total_rows": int,
            "errors": [...],
            "details": [...],
        }
    """
    documents = bundle.get("documents", [])
    # 抽取 records：支持 {"records": [...]} 或直接 list
    global_records: List[Dict] = []
    if extraction_result:
        if isinstance(extraction_result, dict):
            global_records = extraction_result.get("records", [])
            if not global_records and any(
                k not in ("_meta", "metadata", "records") for k in extraction_result
            ):
                # 单记录模式
                global_records = [
                    {k: v for k, v in extraction_result.items() if not k.startswith("_")}
                ]
        elif isinstance(extraction_result, list):
            global_records = extraction_result

    summary = {
        "task_id": task_id,
        "total_files": len(documents),
        "structured_count": 0,
        "unstructured_count": 0,
        "total_rows": 0,
        "errors": [],
        "details": [],
    }

    for doc in documents:
        # 尝试将全局 records 分配给该文档（简单策略：所有文档共享同一批 records）
        doc_records = global_records if global_records else None
        detail = ingest_document(
            task_id=task_id,
            parse_result=doc,
            extraction_records=doc_records,
            template_name=template_name,
        )
        summary["details"].append(detail)
        summary["total_rows"] += detail["rows_inserted"]
        if detail["category"] == "structured":
            summary["structured_count"] += 1
        else:
            summary["unstructured_count"] += 1
        if detail["error"]:
            summary["errors"].append(f"{detail['source_file']}: {detail['error']}")

    return summary


# ── 内部辅助 ──────────────────────────────────────────────────────────────────

def _build_structured_records(
    parse_result: Dict[str, Any],
    extraction_records: Optional[List[Dict]],
) -> List[Dict]:
    """按优先级构造最终 records 列表"""
    # 1. 优先使用抽取结果（最准确）
    if extraction_records:
        return extraction_records

    # 2. 从 Docling DataFrame 直接转换
    dfs = parse_result.get("tables_dataframes", [])
    if dfs:
        records = []
        for df in dfs:
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                rec = {
                    str(col): ("" if str(val) == "nan" else str(val).strip())
                    for col, val in row.items()
                }
                if any(v for v in rec.values()):
                    records.append(rec)
        if records:
            return records

    # 3. 从 tables_raw 的 data 列表转换
    for table in parse_result.get("tables", []):
        data = table.get("data", [])
        if data:
            return [r for r in data if isinstance(r, dict) and any(v for v in r.values())]

    return []
