"""
AI核心模块
提供文档理解、信息抽取、智能问答的全套能力
"""

from .core import UniversalProcessor, processor
from .retriever import Retriever, retriever
from .extractor import Extractor, extractor
from .processor import FieldProcessor, field_processor
from .llm import LLMClient, llm_client
from .loader import DocumentLoader, document_loader
from .qa import QAEngine, qa_engine
from .config import load_profile, validate_profile
from .utils import Timer, timer
from .exceptions import *

__version__ = "1.0.0"
__all__ = [
    # 核心处理器
    'UniversalProcessor',
    'processor',

    # 检索模块
    'Retriever',
    'retriever',

    # 抽取模块
    'Extractor',
    'extractor',

    # 字段处理器
    'FieldProcessor',
    'field_processor',

    # LLM客户端
    'LLMClient',
    'llm_client',

    # 文档加载器
    'DocumentLoader',
    'document_loader',

    # 问答引擎
    'QAEngine',
    'qa_engine',

    # 配置工具
    'load_profile',
    'validate_profile',

    # 工具
    'Timer',
    'timer',

    # 异常
    'AICoreError',
    'DocumentLoadError',
    'ProfileError',
    'LLMError',
    'RetrievalError',
    'ExtractionError',
]