import pdfplumber
import pandas as pd
from docx import Document
from io import BytesIO

class UniversalLoader:
    """全格式解析器：支持文本与表格的结构化提取"""

    @staticmethod
    def to_markdown_table(table_data):
        """将原始表格列表转换为 Markdown 格式字符串"""
        if not table_data or not table_data[0]: return ""
        # 清理空值
        clean_table = [["" if c is None else str(c).replace("\n", " ") for c in row] for row in table_data]
        header = "| " + " | ".join(clean_table[0]) + " |"
        separator = "| " + " | ".join(["---"] * len(clean_table[0])) + " |"
        rows = ["| " + " | ".join(row) + " |" for row in clean_table[1:]]
        return "\n" + "\n".join([header, separator] + rows) + "\n"

    def load_pdf(self, path):
        """解析 PDF：文本 + 自动识别表格"""
        content = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                # 提取文字
                text = page.extract_text()
                if text: content.append(text)
                # 提取表格并转换
                tables = page.find_tables()
                for table in tables:
                    content.append(self.to_markdown_table(table.extract()))
        return "\n\n".join(content)

    def load_docx(self, path):
        """解析 Word：遍历段落与表格对象"""
        doc = Document(path)
        content = []
        for child in doc.element.body:
            if child.tag.endswith('p'): # 段落
                content.append(child.text)
            elif child.tag.endswith('tbl'): # 表格
                # 使用 docx 内部逻辑提取表格
                from docx.table import Table
                table = Table(child, doc)
                data = [[cell.text for cell in row.cells] for row in table.rows]
                content.append(self.to_markdown_table(data))
        return "\n\n".join(content)

    def load_excel(self, path):
        """解析 Excel：将工作表转为文本流"""
        df = pd.read_excel(path)
        return f"Excel数据内容：\n{df.to_markdown(index=False)}"

loader = UniversalLoader()