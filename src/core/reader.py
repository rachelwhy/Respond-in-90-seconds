"""
文档读取器 — 统一加载输入目录中的所有文档
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List


def collect_input_bundle(input_dir: str) -> Dict[str, Any]:
    """加载输入目录中所有支持格式的文档，返回统一结构

    Returns:
        {
            "all_text": str,           # 所有文档文本拼接
            "documents": List[dict],   # 每个文档的解析结果
            "file_count": int,
        }
    """
    from src.adapters.parser_factory import get_parser, SUPPORTED_SUFFIXES

    input_path = Path(input_dir)
    if not input_path.exists():
        return {"all_text": "", "documents": [], "file_count": 0}

    documents = []
    text_parts = []

    # 兼容：input_dir 既可以是目录也可以是单文件
    if input_path.is_file():
        files = [input_path]
    else:
        files = sorted(input_path.iterdir())

    for f in files:
        if not f.is_file():
            continue
        if f.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        parser = get_parser(f)
        if parser is None:
            continue
        try:
            result = parser.parse(f)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            documents.append(result if isinstance(result, dict) else {"text": text, "path": str(f)})
            if text:
                text_parts.append(f"【文件名】{f.name}\n{text}")
        except Exception as e:
            documents.append({"path": str(f), "error": str(e), "text": ""})

    return {
        "all_text": "\n\n".join(text_parts),
        "documents": documents,
        "file_count": len(documents),
    }


def collect_semantic_chunks_from_bundle(loaded_bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    """从 `collect_input_bundle` 的 bundle 里按文档顺序合并语义分块。

    **数据从哪来**

    1. 输入为目录或单文件路径 → `collect_input_bundle` 逐文件选解析器（见 `parser_factory`）。
    2. 非 ``.txt`` 走 **Docling**：`doc["chunks"]` 来自版式/Markdown 派生的分块，元素含 ``type``（如
       ``text`` / ``table`` / ``formula``）与 ``text``。
    3. ``.txt`` 走 **TextParser**：按段落拼块，``chunks`` 一般为 ``type=="text"``。

    **用在哪**

    - 传入 ``CoreExtractionService.extract_with_slicing(..., chunks=..., routing_bundle=bundle)``。
    - 与 ``src.core.extraction_routing.build_pipeline_routing_meta`` 配合得到 ``meta.pipeline_routing``。

    Returns:
        扁平列表；可能为空列表。
    """
    out: List[Dict[str, Any]] = []
    for doc in loaded_bundle.get("documents") or []:
        if isinstance(doc, dict):
            out.extend(doc.get("chunks") or [])
    return out


def collect_all_text(input_dir: str) -> str:
    """快捷方法：只返回合并后的文本"""
    return collect_input_bundle(input_dir).get("all_text", "")


def try_internal_structured_extract(profile: dict, loaded_bundle: Dict[str, Any]):
    """尝试从文档中直接提取结构化表格数据（Docling 表格 → records）

    Returns:
        List[dict] or None — 若成功提取则返回 records，否则返回 None
    """
    records = []
    template_fields = profile.get("fields", [])
    field_names = [f["name"] if isinstance(f, dict) else f for f in template_fields]

    # 多表 Word 场景：为“分组过滤条件”补充辅助字段（如监测时间），
    # 便于后续按约束精确分表；这些字段不要求必须写回模板。
    helper_names = set()
    if profile.get("template_mode") == "word_multi_table":
        for spec in profile.get("table_specs") or []:
            if not isinstance(spec, dict):
                continue
            ff = str(spec.get("filter_field", "")).strip()
            if ff:
                helper_names.add(ff)
            for k in (spec.get("fixed_values") or {}).keys():
                kk = str(k).strip()
                if kk:
                    helper_names.add(kk)
    for hn in helper_names:
        if hn not in field_names:
            template_fields.append({"name": hn, "type": "text"})
            field_names.append(hn)

    if not field_names:
        return None

    from src.core.extractor import UniversalExtractor
    extractor = UniversalExtractor()

    for doc in loaded_bundle.get("documents", []):
        dfs = doc.get("tables_dataframes", [])
        tables_raw = doc.get("tables", [])
        recs = extractor._tables_to_records(dfs, tables_raw, template_fields)
        records.extend(recs)

    return {"records": records, "_internal_route": "docling_table"} if records else None


def load_profile(profile_path: str) -> dict:
    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)
