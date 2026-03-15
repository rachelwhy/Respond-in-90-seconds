"""
列名读取模块：从模板文件中读取列名，用于指导数据抽取
只读取列名，不处理Excel写入
"""

from typing import List
import pandas as pd
import os


class ColumnReader:
    """
    列名读取器
    职责：从模板Excel文件中读取列名，用于指导LLM抽取数据
    不处理任何数据写入操作
    """

    def __init__(self, debug: bool = False):
        self.debug = debug

    def read(self, template_path: str) -> List[str]:
        """
        从模板文件读取列名
        参数：
            template_path: 模板Excel文件路径
        返回：
            列名列表
        异常：
            FileNotFoundError: 文件不存在
            ValueError: 文件不是有效的Excel
        """
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"模板文件不存在: {template_path}")

        try:
            df = pd.read_excel(template_path)
            columns = df.columns.tolist()

            if self.debug:
                print(f"📋 从模板读取列名: {columns}")

            return columns

        except Exception as e:
            raise ValueError(f"读取模板失败: {str(e)}")


# 全局单例
columnreader = ColumnReader(debug=False)