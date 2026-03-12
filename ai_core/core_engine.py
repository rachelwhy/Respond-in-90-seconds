from typing import List, Dict, Any, Optional
from .llm_client import llm_client
from .rag_engine import rag_engine
from .extraction_engine import extraction_engine
import json
import uuid
import pandas as pd
from .document_loaders import document_loader
import os

class UniversalProcessor:
    """通用抽取引擎：协调RAG检索和字段抽取"""

    def __init__(self):
        pass

    def process(self,
                file_path: str,
                instruction: str = "",
                template: Optional[List[str]] = None,
                output_format: str = "list",
                field_top_k: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """
        处理文档
        :param file_path: 文件路径
        :param instruction: 用户指令（字符串）
        :param template: 模板字段列表（可选）
        :param output_format: 输出格式，list 返回 fields 列表，dict 返回字段名到值的字典
        :param field_top_k: 为特定字段指定的检索数量，如 {"合同金额": 5, "签订日期": 3}
        """
        filename = os.path.basename(file_path)
        doc_info = document_loader.load(file_path, filename)

        if "error" in doc_info:
            return {
                "task_id": str(uuid.uuid4())[:8],
                "file_name": filename,
                "fields": [],
                "error": doc_info["error"]
            }

        file_type = doc_info.get("type", "unknown")

        if file_type == "excel":
            result = self._process_excel(doc_info, instruction, filename, template)
        elif file_type in ["word", "markdown"]:
            fields = self._process_structured_document(doc_info, instruction, filename, file_type, field_top_k)
            result = self._build_result(filename, file_type, fields, output_format)
        else:
            fields = self._process_text(doc_info, instruction, filename, field_top_k)
            result = self._build_result(filename, file_type, fields, output_format)

        result["task_id"] = str(uuid.uuid4())[:8]
        return result

    def _build_result(self, filename: str, file_type: str, fields: List[Dict], output_format: str) -> Dict:
        """根据 output_format 组装返回结果"""
        result = {
            "file_name": filename,
            "file_type": file_type,
        }

        if output_format == "list":
            result["fields"] = fields
            result["total_fields"] = len(fields)
        elif output_format == "dict":
            # 转换为字段名到值的字典，并标记成功状态
            field_dict = {}
            for f in fields:
                name = f["name"]
                value = f["value"]
                # 简单判断：非空且不是None视为成功
                success = value is not None and str(value).strip() != ""
                field_dict[name] = {
                    "value": value,
                    "success": success
                }
            result["data"] = field_dict
        else:
            raise ValueError(f"不支持的 output_format: {output_format}")

        return result

    def _process_excel(self, doc_info: Dict, instruction: str, filename: str,
                       template: Optional[List[str]]) -> Dict[str, Any]:
        df = doc_info.get("dataframe")
        if df is None:
            return {
                "task_id": str(uuid.uuid4())[:8],
                "file_name": filename,
                "fields": [],
                "error": "DataFrame 不存在"
            }

        excel_type = doc_info.get("excel_type", "data_table")
        rows = doc_info.get("rows", 0)
        columns = df.columns.tolist()
        all_rows = df.to_dict(orient='records')
        for row in all_rows:
            for key, value in row.items():
                if pd.isna(value):
                    row[key] = None

        return {
            "task_id": str(uuid.uuid4())[:8],
            "file_name": filename,
            "file_type": "excel",
            "excel_type": excel_type,
            "row_count": rows,
            "column_count": len(columns),
            "columns": columns,
            "data": all_rows,
            "summary": {"row_count": rows, "column_count": len(columns)}
        }

    def _process_structured_document(self, doc_info: Dict, instruction: str, filename: str,
                                      doc_type: str, field_top_k: Optional[Dict[str, int]] = None) -> List[Dict]:
        """处理结构化文档（Word/Markdown）"""
        paragraphs = doc_info.get("paragraphs", [])
        tables_md = doc_info.get("tables", [])
        lists = doc_info.get("lists", [])
        titles = doc_info.get("titles", [])
        full_text = "\n".join(paragraphs)

        # 调用RAG检索证据
        if field_top_k:
            # 按字段检索：从 field_top_k 的键中获取字段名列表
            field_names = list(field_top_k.keys())
            evidence = rag_engine.retrieve_evidence(
                document_text=full_text,
                instruction=field_names,
                filename=filename,
                global_top_k=3,
                field_top_k=field_top_k
            )
        else:
            # 全局检索
            evidence = rag_engine.retrieve_evidence(
                document_text=full_text,
                instruction=instruction,
                filename=filename,
                global_top_k=3
            )

        # 调用抽取引擎提取字段
        fields = extraction_engine.extract_fields(
            evidence=evidence,
            instruction=instruction,
            filename=filename,
            tables=tables_md,
            lists=lists,
            titles=titles
        )

        return fields

    def _process_text(self, doc_info: Dict, instruction: str, filename: str,
                      field_top_k: Optional[Dict[str, int]] = None) -> List[Dict]:
        """处理纯文本文件"""
        text = doc_info.get("text", "")
        if not text:
            return []

        # 调用RAG检索证据
        if field_top_k:
            field_names = list(field_top_k.keys())
            evidence = rag_engine.retrieve_evidence(
                document_text=text,
                instruction=field_names,
                filename=filename,
                global_top_k=3,
                field_top_k=field_top_k
            )
        else:
            evidence = rag_engine.retrieve_evidence(
                document_text=text,
                instruction=instruction,
                filename=filename,
                global_top_k=3
            )

        # 调用抽取引擎提取字段
        fields = extraction_engine.extract_fields(
            evidence=evidence,
            instruction=instruction,
            filename=filename
        )

        return fields


core_engine = UniversalProcessor()