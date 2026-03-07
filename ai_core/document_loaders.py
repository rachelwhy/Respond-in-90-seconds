import pandas as pd
from docx import Document
import os


class DocumentLoader:
    """全格式文档解析器"""

    @staticmethod
    def load_docx(path: str) -> str:
        """解析 Word 文档"""
        try:
            doc = Document(path)
            paragraphs = [para.text for para in doc.paragraphs if para.text]
            return "\n".join(paragraphs)
        except Exception as e:
            return f"Word解析失败: {str(e)}"

    @staticmethod
    def load_excel(path: str) -> str:
        """解析 Excel 文件"""
        try:
            df = pd.read_excel(path)
            if df.empty:
                return "Excel文件内容为空"
            return f"Excel数据内容：\n{df.to_markdown(index=False)}"
        except Exception as e:
            return f"Excel解析失败: {str(e)}"

    @staticmethod
    def load_txt(path: str) -> str:
        """解析文本文件"""
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"文本文件解析失败: {str(e)}"

    @staticmethod
    def load_md(path: str) -> str:
        """解析 Markdown 文件（同文本）"""
        return DocumentLoader.load_txt(path)

    def load(self, path: str, filename: str) -> str:
        """统一加载接口，根据文件扩展名自动选择解析器"""
        ext = os.path.splitext(filename)[1].lower()

        if ext in ['.docx', '.doc']:
            return self.load_docx(path)
        elif ext in ['.xlsx', '.xls']:
            return self.load_excel(path)
        elif ext in ['.txt']:
            return self.load_txt(path)
        elif ext in ['.md']:
            return self.load_md(path)
        else:
            return f"不支持的文件类型: {ext}"


document_loader = DocumentLoader()