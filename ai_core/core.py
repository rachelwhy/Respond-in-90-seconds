"""
核心协调器：整合所有模块，提供统一接口
从你的代码迁移：core_engine.py 核心功能
"""

from typing import List, Dict, Any, Optional, Union
import uuid
import os
import pandas as pd
from .loader import document_loader
from .retriever import retriever
from .extractor import extractor
from .config import load_profile, validate_profile
from .utils import timer


class UniversalProcessor:
    """
    通用处理器：协调文档加载、检索、抽取、处理
    提供完整的文档理解能力
    """

    def __init__(self):
        pass

    def process(self,
                file_path: str,
                instruction: str = "",
                profile_path: Optional[str] = None,
                output_format: str = "list",
                field_top_k: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
        """
        处理文档
        参数：
            file_path: 文件路径
            instruction: 用户指令
            profile_path: profile配置文件路径（可选）
            output_format: 输出格式（list/dict）
            field_top_k: 按字段指定检索数量
        返回：
            处理结果
        """
        filename = os.path.basename(file_path)
        result = {
            "task_id": str(uuid.uuid4())[:8],
            "file_name": filename
        }

        # 1. 加载文档
        with timer.measure("load_document"):
            doc_info = document_loader.load(file_path, filename)
            if "error" in doc_info:
                result["error"] = doc_info["error"]
                return result

        # 2. 加载profile（如果有）
        profile = None
        if profile_path:
            with timer.measure("load_profile"):
                profile = load_profile(profile_path)
                validate_profile(profile)

        # 3. 根据类型处理
        file_type = doc_info.get("type", "unknown")
        result["file_type"] = file_type

        if file_type == "excel":
            return self._process_excel(doc_info, filename)

        # 4. 获取全文
        if file_type in ["word", "markdown"]:
            full_text = "\n".join(doc_info.get("paragraphs", []))
        else:
            full_text = doc_info.get("text", "")

        if not full_text:
            result["fields"] = []
            result["total_fields"] = 0
            return result

        # 5. RAG检索
        with timer.measure("retrieve"):
            if field_top_k:
                field_names = list(field_top_k.keys())
                evidence = retriever.retrieve(
                    document_text=full_text,
                    instruction=field_names,
                    filename=filename,
                    top_k=3,
                    field_top_k=field_top_k
                )
            else:
                evidence = retriever.retrieve(
                    document_text=full_text,
                    instruction=instruction,
                    filename=filename,
                    top_k=3
                )

        # 6. 字段抽取
        with timer.measure("extract"):
            fields = extractor.extract(
                evidence=evidence,
                instruction=instruction,
                filename=filename,
                profile=profile,
                tables=doc_info.get("tables"),
                lists=doc_info.get("lists"),
                titles=doc_info.get("titles")
            )

        # 7. 组装结果
        if output_format == "list":
            # 确保所有分数都是3位小数
            for field in fields:
                if "retrieval_score" in field and field["retrieval_score"] is not None:
                    field["retrieval_score"] = round(field["retrieval_score"], 3)
            result["fields"] = fields
            result["total_fields"] = len(fields)
        elif output_format == "dict":
            data = {}
            for f in fields:
                name = f["name"]
                value = f["value"]
                success = value is not None and str(value).strip() != ""
                data[name] = {
                    "value": value,
                    "success": success
                }
            result["data"] = data
        else:
            raise ValueError(f"不支持的输出格式: {output_format}")

        # 8. 添加耗时
        result["timing"] = timer.get_summary()
        timer.reset()

        return result

    def _process_excel(self, doc_info: Dict, filename: str) -> Dict[str, Any]:
        """处理Excel文件"""
        df = doc_info.get("dataframe")
        if df is None:
            return {
                "task_id": str(uuid.uuid4())[:8],
                "file_name": filename,
                "file_type": "excel",
                "error": "DataFrame不存在"
            }

        all_rows = df.to_dict(orient='records')
        for row in all_rows:
            for k, v in row.items():
                if pd.isna(v):
                    row[k] = None

        return {
            "task_id": str(uuid.uuid4())[:8],
            "file_name": filename,
            "file_type": "excel",
            "data": all_rows,
            "row_count": len(all_rows),
            "column_count": len(df.columns),
            "columns": df.columns.tolist()
        }


# 全局单例
processor = UniversalProcessor()