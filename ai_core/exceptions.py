"""
自定义异常模块
"""

class AICoreError(Exception):
    """AI核心基础异常"""
    pass


class DocumentLoadError(AICoreError):
    """文档加载异常"""
    pass


class ProfileError(AICoreError):
    """Profile配置异常"""
    pass


class LLMError(AICoreError):
    """LLM调用异常"""
    pass


class RetrievalError(AICoreError):
    """检索异常"""
    pass


class ExtractionError(AICoreError):
    """抽取异常"""
    pass