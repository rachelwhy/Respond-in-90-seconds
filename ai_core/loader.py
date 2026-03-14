import pandas as pd
from docx import Document
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Tuple, Optional, Dict, Any, List


class DocumentLoader:
    """全格式文档解析器，针对不同文件类型优化"""

    @staticmethod
    def load_docx(path: str) -> Dict[str, Any]:
        """
        解析 Word 文档，返回结构化信息
        返回: {
            "type": "word",
            "paragraphs": [...],
            "tables": [markdown_table, ...],
            "lists": [...],
            "titles": [...],
            "has_footnotes": bool,
            "text": "完整文本（备用）"
        }
        """
        result = {
            "type": "word",
            "paragraphs": [],
            "tables": [],
            "lists": [],
            "titles": [],
            "has_footnotes": False,
            "text": ""
        }

        try:
            doc = Document(path)
            # 段落
            for para in doc.paragraphs:
                if para.text.strip():
                    result["paragraphs"].append(para.text)
                    # 简单识别标题（可以改进）
                    if para.style.name.startswith('Heading'):
                        result["titles"].append((para.style.name, para.text))

            # 表格
            for table in doc.tables:
                table_md = []
                for i, row in enumerate(table.rows):
                    row_cells = [cell.text.strip() for cell in row.cells]
                    if i == 0:
                        table_md.append("| " + " | ".join(row_cells) + " |")
                        table_md.append("|" + " --- |" * len(row_cells))
                    else:
                        table_md.append("| " + " | ".join(row_cells) + " |")
                result["tables"].append("\n".join(table_md))

            # 简单列表识别：以项目符号开头的段落
            for para in doc.paragraphs:
                if para.text.strip() and (para.style.name.startswith('List') or para.text.startswith(('•', '-', '*'))):
                    result["lists"].append(para.text)

            result["text"] = "\n".join(result["paragraphs"])
            return result, None

        except Exception as e:
            # 备用解析
            try:
                with zipfile.ZipFile(path, 'r') as zip_ref:
                    xml_content = zip_ref.read('word/document.xml')
                    text = re.sub(r'<[^>]+>', ' ', xml_content.decode('utf-8', errors='ignore'))
                    text = re.sub(r'\s+', ' ', text).strip()
                    result["text"] = text
                    sentences = re.split(r'[。！？]', text)
                    result["paragraphs"] = [s.strip() + "。" for s in sentences if len(s.strip()) > 10]
                    return result, None
            except:
                return None, f"Word解析失败: {str(e)}"

    @staticmethod
    def load_md(path: str) -> Dict[str, Any]:
        """
        增强版 Markdown 解析，提取结构化信息
        返回: {
            "type": "markdown",
            "titles": [(level, title), ...],
            "tables": [markdown_table, ...],
            "lists": [list_item, ...],
            "paragraphs": [paragraph, ...],
            "text": "完整文本"
        }
        """
        result = {
            "type": "markdown",
            "titles": [],
            "tables": [],
            "lists": [],
            "paragraphs": [],
            "text": ""
        }

        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            lines = content.split('\n')
            in_code_block = False
            current_paragraph = []

            for line in lines:
                # 跳过代码块
                if line.startswith('```'):
                    in_code_block = not in_code_block
                    continue
                if in_code_block:
                    continue

                # 标题
                title_match = re.match(r'^(#{1,6})\s+(.+)$', line)
                if title_match:
                    level = len(title_match.group(1))
                    title = title_match.group(2)
                    result["titles"].append((level, title))
                    # 标题也作为段落的一种，但通常不加入段落列表
                    continue

                # 表格行
                if re.match(r'^\|.+\|$', line):
                    # 收集表格行，但不立即解析，后续可以合并成表格
                    # 简单处理：将表格行存入一个临时列表，但为了简化，我们将整个表格作为一个字符串存入tables
                    # 这里不处理，后面统一提取表格
                    pass

                # 列表项
                list_match = re.match(r'^[\*\-\+]\s+(.+)$', line) or re.match(r'^\d+\.\s+(.+)$', line)
                if list_match:
                    result["lists"].append(list_match.group(1))
                    continue

                # 普通段落
                if line.strip():
                    current_paragraph.append(line.strip())
                else:
                    if current_paragraph:
                        result["paragraphs"].append(" ".join(current_paragraph))
                        current_paragraph = []

            if current_paragraph:
                result["paragraphs"].append(" ".join(current_paragraph))

            # 提取表格（简单正则）
            tables = re.findall(r'(\|.+\|\r?\n\|[-|\s]+\|\r?\n(?:\|.+\|\r?\n)+)', content)
            result["tables"] = [t.strip() for t in tables]

            # 构建完整文本
            result["text"] = content

            return result, None

        except Exception as e:
            return None, f"Markdown解析失败: {str(e)}"

    @staticmethod
    def load_txt(path: str) -> Dict[str, Any]:
        """
        解析文本文件，判断类型，并提取段落
        返回: {
            "type": "report" 或 "log" 或 "unknown",
            "paragraphs": [...],
            "text": "完整文本"
        }
        """
        result = {
            "type": "unknown",
            "paragraphs": [],
            "text": ""
        }

        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()

            lines = content.split('\n')
            # 简单分段
            paragraphs = []
            current = []
            for line in lines:
                if line.strip():
                    current.append(line.strip())
                else:
                    if current:
                        paragraphs.append(" ".join(current))
                        current = []
            if current:
                paragraphs.append(" ".join(current))

            result["paragraphs"] = paragraphs
            result["text"] = content

            # 类型判断
            sample_text = ' '.join(lines[:20])
            log_patterns = [
                r'\d{4}-\d{2}-\d{2}', r'\d{2}:\d{2}:\d{2}', r'\[.*?\]', r'INFO|WARN|ERROR|DEBUG'
            ]
            for pattern in log_patterns:
                if re.search(pattern, sample_text):
                    result["type"] = "log"
                    break
            if result["type"] == "unknown" and re.search(r'第[一二三四五六七八九十]+章|[一二三四]、', sample_text):
                result["type"] = "report"

            return result, None

        except Exception as e:
            return None, f"文本文件解析失败: {str(e)}"

    @staticmethod
    def load_excel_as_df(path: str) -> Tuple[Optional[pd.DataFrame], Optional[str]]:
        try:
            df = pd.read_excel(path)
            return df, None
        except Exception as e:
            return None, str(e)

    @staticmethod
    def analyze_excel(df: pd.DataFrame) -> Dict[str, Any]:
        rows, cols = df.shape
        result = {
            "rows": rows,
            "cols": cols,
            "type": "data_table" if rows > 20 else "key_table",
            "has_numeric": any(df[col].dtype in ['int64', 'float64'] for col in df.columns),
            "has_date": any('date' in str(col).lower() or '时间' in str(col) for col in df.columns),
            "null_ratio": df.isnull().sum().sum() / (rows * cols) if rows * cols > 0 else 0
        }
        return result

    def load(self, path: str, filename: str) -> Dict[str, Any]:
        """
        统一加载接口，返回结构化信息
        """
        ext = os.path.splitext(filename)[1].lower()
        result = {"filename": filename, "path": path, "ext": ext}

        if ext in ['.docx', '.doc']:
            data, err = self.load_docx(path)
            if err:
                result["error"] = err
            else:
                result.update(data)

        elif ext in ['.xlsx', '.xls']:
            df, err = self.load_excel_as_df(path)
            if err:
                result["error"] = err
            else:
                result["type"] = "excel"
                result["analysis"] = self.analyze_excel(df)
                result["rows"] = result["analysis"]["rows"]
                result["cols"] = result["analysis"]["cols"]
                result["excel_type"] = result["analysis"]["type"]
                result["dataframe"] = df

        elif ext in ['.md']:
            data, err = self.load_md(path)
            if err:
                result["error"] = err
            else:
                result.update(data)

        elif ext in ['.txt']:
            data, err = self.load_txt(path)
            if err:
                result["error"] = err
            else:
                result.update(data)

        else:
            result["error"] = f"不支持的文件类型: {ext}"

        return result


document_loader = DocumentLoader()