from pathlib import Path

from src.adapters.text_parser import TextParser
from src.adapters.docling_adapter import DoclingParser

# Docling 支持的所有文件格式（唯一的文档解析入口）
DOCLING_SUPPORTED_SUFFIXES = {
    '.doc', '.docx', '.pdf', '.ppt', '.pptx',
    '.xls', '.xlsx', '.xlsm', '.rtf',
    '.html', '.htm', '.epub', '.odt', '.ods', '.odp',
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif',
}

SUPPORTED_SUFFIXES = {'.txt'} | DOCLING_SUPPORTED_SUFFIXES


def get_parser(path, parser_type: str = None):
    """获取适合该文件的解析器

    Docling 是所有文档格式的唯一解析入口。
    纯文本文件 (.txt) 使用轻量级 TextParser。
    """
    path = Path(path)
    ext = path.suffix.lower()

    if ext == '.txt':
        return TextParser()

    if ext in DOCLING_SUPPORTED_SUFFIXES:
        return DoclingParser()

    return None
