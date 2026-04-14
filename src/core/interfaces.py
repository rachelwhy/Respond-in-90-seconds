"""
抽取系统统一接口定义

定义核心服务接口，支持多实现（语义分块、字符切片、langextract等），
便于依赖注入和测试。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class IExtractionService(ABC):
    """抽取服务统一接口"""

    @abstractmethod
    def extract_from_text(self, text: str, profile: dict,
                          llm_mode: str = "full",
                          slice_size: int = 2000,
                          overlap: int = 100,
                          max_chunks: int = 50,
                          time_budget: int = 110,
                          quiet: bool = False) -> Dict[str, Any]:
        """从文本中提取结构化信息

        Args:
            text: 输入文本
            profile: 抽取配置文件
            llm_mode: 抽取模式，可选 "full"（默认）/"off"（仅规则抽取），"supplement" 兼容映射为 "full"
            slice_size: 字符切片大小（仅在无语义分块时使用）
            overlap: 字符切片重叠大小（仅在无语义分块时使用）
            max_chunks: 最大处理分块数
            time_budget: 时间预算（秒）
            quiet: 安静模式，禁用进度输出

        Returns:
            抽取结果字典，包含records、metadata等信息
        """
        pass

    @abstractmethod
    def extract_from_document(self, document_path: str, profile: dict, **kwargs) -> Dict[str, Any]:
        """从文档文件中提取结构化信息

        Args:
            document_path: 文档文件路径
            profile: 抽取配置文件
            **kwargs: 传递给extract_from_text的参数

        Returns:
            抽取结果字典
        """
        pass

    @abstractmethod
    def build_smart_prompt(self, text: str, profile: dict) -> str:
        """根据profile和文本构建抽取prompt"""
        pass

    @abstractmethod
    def extract_with_slicing(self, text: str, profile: dict,
                             use_model: bool = True,
                             slice_size: int = 2000,
                             overlap: int = 100,
                             show_progress: bool = True,
                             time_budget: int = 110,
                             chunks: list = None,
                             max_chunks: int = 50,
                             logger=None,
                             word_table_segments: Optional[List[str]] = None,
                             routing_bundle: Optional[Dict[str, Any]] = None):
        """使用切片模式进行抽取。优先使用 Docling 语义分块（chunks），回退到字符切片。

        Args:
            text: 完整文档文本
            profile: 模板配置
            use_model: 是否使用模型抽取
            slice_size: 字符切片大小（仅在无 chunks 时使用）
            overlap: 字符切片重叠大小（仅在无 chunks 时使用）
            show_progress: 是否显示进度信息
            time_budget: 最大允许耗时（秒）
            chunks: Docling 语义分块列表（每个元素含 type 和 text 字段）
            max_chunks: 最多处理的 chunk 数量
            logger: 可选的 Python logger 实例
            word_table_segments: 多表 Word 时每表一段源文档上下文
            routing_bundle: collect_input_bundle 结果，用于 meta.pipeline_routing

        Returns:
            extracted_raw: 抽取结果字典
            model_output: 模型输出字典
            slicing_metadata: 切片处理的元数据
        """
        pass

    @abstractmethod
    def merge_records_by_key(self, records: List[Dict], key_fields: Optional[List[str]] = None) -> List[Dict]:
        """基于关键字段的记录融合去重"""
        pass


class IParserService(ABC):
    """解析服务统一接口"""

    @abstractmethod
    def parse_document(self, document_path: str) -> Dict[str, Any]:
        """解析文档，返回结构化的文档信息

        Args:
            document_path: 文档文件路径

        Returns:
            文档信息字典，包含text、chunks、tables等字段
        """
        pass

    @abstractmethod
    def parse_text(self, text: str, file_extension: str = ".txt") -> Dict[str, Any]:
        """解析文本，返回结构化的文档信息

        Args:
            text: 原始文本
            file_extension: 文件扩展名，用于推断文档类型

        Returns:
            文档信息字典
        """
        pass

    @abstractmethod
    def get_semantic_chunks(self, document_path: str, max_chunks: int = 50) -> List[Dict[str, Any]]:
        """获取文档的语义分块

        Args:
            document_path: 文档文件路径
            max_chunks: 最大分块数

        Returns:
            语义分块列表，每个分块包含type、text等字段
        """
        pass


# 工厂函数和注册表
_extraction_service_registry = {}
_parser_service_registry = {}


def register_extraction_service(name: str, service_class):
    """注册抽取服务实现"""
    _extraction_service_registry[name] = service_class


def get_extraction_service(name: str = "default", **kwargs) -> IExtractionService:
    """获取抽取服务实例"""
    if name not in _extraction_service_registry:
        raise ValueError(f"未注册的抽取服务: {name}")
    return _extraction_service_registry[name](**kwargs)


def register_parser_service(name: str, service_class):
    """注册解析服务实现"""
    _parser_service_registry[name] = service_class


def get_parser_service(name: str = "default", **kwargs) -> IParserService:
    """获取解析服务实例"""
    if name not in _parser_service_registry:
        raise ValueError(f"未注册的解析服务: {name}")
    return _parser_service_registry[name](**kwargs)