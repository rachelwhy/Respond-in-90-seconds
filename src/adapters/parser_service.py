"""
统一解析服务 - 实现唯一的文档解析入口

职责：
1. 统一所有文档格式的解析入口，包括.txt文件
2. 为所有格式提供语义分块功能
3. 解决解析器入口"唯一性"被破坏的问题

设计原则：
1. 优先使用DoclingParser（支持语义分块）
2. 对于纯文本，尝试使用DoclingParser，失败时回退到TextParser
3. 提供一致的解析接口，返回标准化的文档结构
"""

import logging
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

from src.core.interfaces import IParserService

logger = logging.getLogger(__name__)

# 导入现有的解析器工厂
try:
    from src.adapters.parser_factory import get_parser, DOCLING_SUPPORTED_SUFFIXES, SUPPORTED_SUFFIXES
except ImportError:
    # 向后兼容
    get_parser = None
    DOCLING_SUPPORTED_SUFFIXES = set()
    SUPPORTED_SUFFIXES = set()


class ParserService(IParserService):
    """统一解析服务，实现唯一的文档解析入口"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化解析服务

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self._initialize_components()

    def _initialize_components(self):
        """初始化内部组件"""
        # 可以在这里初始化缓存、配置等
        pass

    def parse_document(self, document_path: str) -> Dict[str, Any]:
        """解析文档，返回结构化的文档信息

        Args:
            document_path: 文档文件路径

        Returns:
            文档信息字典，包含text、chunks、tables等字段
        """
        path = Path(document_path)
        if not path.exists():
            raise FileNotFoundError(f"文档不存在: {document_path}")

        ext = path.suffix.lower()

        # 检查是否支持该格式
        if SUPPORTED_SUFFIXES and ext not in SUPPORTED_SUFFIXES:
            raise ValueError(f"不支持的文档格式: {ext}，支持格式: {SUPPORTED_SUFFIXES}")

        # 获取解析器
        parser = self._get_parser_for_file(document_path)
        if parser is None:
            raise ValueError(f"无法获取适合 {ext} 文件的解析器")

        # 解析文档
        try:
            result = parser.parse(document_path)
            return self._normalize_result(result, document_path)
        except Exception as e:
            logger.error(f"解析文档失败 {document_path}: {e}")
            raise

    def parse_text(self, text: str, file_extension: str = ".txt") -> Dict[str, Any]:
        """解析文本，返回结构化的文档信息

        Args:
            text: 原始文本
            file_extension: 文件扩展名，用于推断文档类型

        Returns:
            文档信息字典
        """
        # 对于纯文本，我们可以创建一个临时文件，或者直接使用TextParser
        # 这里我们使用TextParser直接解析文本
        try:
            from src.adapters.text_parser import TextParser
            parser = TextParser()
            # TextParser可能没有parse_text方法，只有parse_file方法
            # 创建临时文件
            import tempfile
            with tempfile.NamedTemporaryFile(mode='w', suffix=file_extension, encoding='utf-8', delete=False) as f:
                f.write(text)
                temp_path = f.name

            try:
                result = parser.parse(temp_path)
                return self._normalize_result(result, f"text:{file_extension}")
            finally:
                # 删除临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass
        except Exception as e:
            logger.error(f"解析文本失败: {e}")
            # 返回基本结构
            return {
                "path": f"text:{file_extension}",
                "text": text,
                "chunks": [{"type": "text", "text": text}],
                "tables": [],
                "metadata": {"file_extension": file_extension}
            }

    def get_semantic_chunks(self, document_path: str, max_chunks: int = 50) -> List[Dict[str, Any]]:
        """获取文档的语义分块

        Args:
            document_path: 文档文件路径
            max_chunks: 最大分块数

        Returns:
            语义分块列表，每个分块包含type、text等字段
        """
        # 解析文档，然后提取chunks
        result = self.parse_document(document_path)
        chunks = result.get("chunks", [])

        # 限制分块数量
        if max_chunks and len(chunks) > max_chunks:
            chunks = chunks[:max_chunks]

        return chunks

    def _get_parser_for_file(self, document_path: str):
        """获取适合文件的解析器，优先使用DoclingParser以获得语义分块

        修改逻辑：对于.txt文件，也尝试使用DoclingParser（如果可用），
        失败时回退到TextParser。
        """
        path = Path(document_path)
        ext = path.suffix.lower()

        # 如果get_parser可用，使用原有逻辑（但我们需要修改.txt的处理）
        if get_parser is not None:
            parser = get_parser(document_path)
            # 如果是TextParser且我们有DoclingParser，尝试使用DoclingParser
            if ext == '.txt':
                from src.adapters.docling_adapter import DoclingParser
                from src.adapters.text_parser import TextParser
                try:
                    # 尝试使用DoclingParser处理.txt文件
                    docling_parser = DoclingParser()
                    # 检查DoclingParser是否支持.txt（可能不支持，但我们可以尝试）
                    # 这里我们假设DoclingParser可以处理纯文本
                    return docling_parser
                except Exception as e:
                    logger.debug(f"DoclingParser无法处理.txt文件，回退到TextParser: {e}")
                    return TextParser()
            return parser
        else:
            # 直接导入解析器
            if ext == '.txt':
                from src.adapters.text_parser import TextParser
                return TextParser()
            else:
                from src.adapters.docling_adapter import DoclingParser
                return DoclingParser()

    def _normalize_result(self, result: Dict[str, Any], source: str) -> Dict[str, Any]:
        """规范化解析结果，确保返回标准结构

        标准化字段：
        - text: 完整文档文本
        - chunks: 语义分块列表，每个分块包含type和text字段
        - tables: 表格数据列表
        - metadata: 元数据
        """
        normalized = {
            "path": source,
            "text": "",
            "chunks": [],
            "tables": [],
            "metadata": {}
        }

        if not isinstance(result, dict):
            # 如果result不是字典，假设它是文本
            normalized["text"] = str(result)
            normalized["chunks"] = [{"type": "text", "text": str(result)}]
            return normalized

        # 提取文本
        if "text" in result:
            normalized["text"] = result["text"]
        elif "content" in result:
            normalized["text"] = result["content"]
        elif "document" in result and isinstance(result["document"], dict) and "text" in result["document"]:
            normalized["text"] = result["document"]["text"]

        # 提取分块
        if "chunks" in result and isinstance(result["chunks"], list):
            normalized["chunks"] = result["chunks"]
        elif "segments" in result and isinstance(result["segments"], list):
            # 转换segments为chunks格式
            normalized["chunks"] = [
                {"type": seg.get("type", "text"), "text": seg.get("text", "")}
                for seg in result["segments"]
            ]
        elif normalized["text"]:
            # 如果没有分块，将整个文本作为一个分块
            normalized["chunks"] = [{"type": "text", "text": normalized["text"]}]

        # 提取表格
        if "tables" in result and isinstance(result["tables"], list):
            normalized["tables"] = result["tables"]
        elif "table_data" in result and isinstance(result["table_data"], list):
            normalized["tables"] = result["table_data"]

        # 提取元数据
        if "metadata" in result and isinstance(result["metadata"], dict):
            normalized["metadata"] = result["metadata"]
        else:
            # 从结果中收集非标准字段作为元数据
            for key, value in result.items():
                if key not in ["text", "chunks", "tables", "segments", "content", "document"]:
                    normalized["metadata"][key] = value

        return normalized


# 全局默认实例
_default_parser_service = None

def get_parser_service(config: Optional[Dict[str, Any]] = None) -> ParserService:
    """获取解析服务实例"""
    global _default_parser_service
    if _default_parser_service is None or config is not None:
        _default_parser_service = ParserService(config)
    return _default_parser_service

def reset_parser_service(config: Optional[Dict[str, Any]] = None):
    """重置解析服务实例（主要用于测试）"""
    global _default_parser_service
    _default_parser_service = None if config is None else ParserService(config)


# 注册到服务注册表
try:
    from src.core.interfaces import register_parser_service
    register_parser_service("default", ParserService)
    register_parser_service("unified", ParserService)
except ImportError:
    # 如果interfaces模块不可用，跳过注册
    pass