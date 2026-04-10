"""
输出格式化器测试
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.output_formatter import OutputFormatter, FormatConfig, OutputFormat, format_output_adaptive


class TestOutputFormatter:
    """输出格式化器测试类"""

    def setup_method(self):
        """测试初始化"""
        self.config = FormatConfig(
            selection_strategy="rule_based",
            default_format=OutputFormat.TABLE,
            enable_debug=True
        )
        self.formatter = OutputFormatter(self.config)

    def test_initialization(self):
        """测试初始化"""
        assert self.formatter.config == self.config

    def test_format_output_empty(self):
        """测试空记录格式化"""
        records = []
        result = self.formatter.format_output(records)

        assert result["format"] == OutputFormat.TABLE.value
        assert result["data"] == []
        assert result["metadata"]["record_count"] == 0

    def test_format_output_single_record(self):
        """测试单条记录格式化"""
        records = [
            {"name": "张三", "age": "30", "city": "北京"}
        ]

        result = self.formatter.format_output(records)

        assert result["format"] in [f.value for f in OutputFormat]
        assert "data" in result
        assert result["metadata"]["record_count"] == 1

    def test_analyze_structure_features(self):
        """测试文档结构特征分析"""
        # 测试表格结构
        structure = {
            "type": "table_document",
            "sections": ["表1", "表2"],
            "paragraphs": ["段落1"],
            "max_depth": 2
        }

        features = self.formatter._analyze_structure_features(structure)

        assert features["has_tables"] == True
        assert features["structure_type"] == "tabular"
        assert features["has_headings"] == True
        assert features["has_paragraphs"] == True
        assert features["complexity_score"] > 0

    def test_analyze_result_features(self):
        """测试抽取结果特征分析"""
        records = [
            {"id": "1", "name": "张三", "age": "30"},
            {"id": "2", "name": "李四", "age": "25"},
            {"id": "3", "name": "王五", "age": "35"}
        ]

        features = self.formatter._analyze_result_features(records)

        assert features["record_count"] == 3
        assert features["field_count"] == 3
        assert "id" in features["all_fields"]
        assert features["is_tabular"] == True
        assert not features["has_nested"]

    def test_analyze_result_features_nested(self):
        """测试嵌套结构特征分析"""
        records = [
            {
                "id": "1",
                "name": "张三",
                "skills": ["Python", "Java"],
                "address": {"city": "北京", "street": "长安街"}
            }
        ]

        features = self.formatter._analyze_result_features(records)

        assert features["has_nested"] == True
        assert features["has_arrays"] == True
        assert features["field_types"]["skills"] == "array"
        assert features["field_types"]["address"] == "object"

    def test_infer_field_type(self):
        """测试字段类型推断"""
        # 测试整数
        assert self.formatter._infer_field_type(["123", "456"]) == "integer"

        # 测试浮点数
        assert self.formatter._infer_field_type(["12.34", "56.78"]) == "float"

        # 测试百分比
        assert self.formatter._infer_field_type(["15%", "20%"]) == "percentage"

        # 测试日期
        assert self.formatter._infer_field_type(["2023-01-01", "2023-12-31"]) == "date"

        # 测试布尔值
        assert self.formatter._infer_field_type(["true", "false"]) == "boolean"

        # 测试文本
        assert self.formatter._infer_field_type(["张三", "李四"]) == "text"

    def test_select_format_by_rules_tabular(self):
        """测试规则选择表格格式"""
        structure_features = {"has_tables": True, "complexity_score": 0.3}
        result_features = {
            "is_tabular": True,
            "record_count": 10,
            "has_nested": False
        }

        format = self.formatter._select_format_by_rules(structure_features, result_features)

        assert format in [OutputFormat.TABLE, OutputFormat.CSV]

    def test_select_format_by_rules_nested(self):
        """测试规则选择嵌套格式"""
        structure_features = {"complexity_score": 0.7, "max_depth": 3}
        result_features = {
            "is_tabular": False,
            "has_nested": True,
            "record_count": 5
        }

        format = self.formatter._select_format_by_rules(structure_features, result_features)

        assert format in [OutputFormat.TREE, OutputFormat.JSON]

    def test_convert_to_table(self):
        """测试转换为表格"""
        records = [
            {"id": "1", "name": "张三", "age": "30"},
            {"id": "2", "name": "李四", "age": "25"}
        ]

        profile = {
            "fields": [
                {"name": "id"},
                {"name": "name"},
                {"name": "age"}
            ]
        }

        table = self.formatter._convert_to_table(records, profile)

        # 检查表头
        assert table[0] == ["id", "name", "age"]

        # 检查数据行
        assert len(table) == 3  # 表头 + 2行数据
        assert table[1][0] == "1"
        assert table[1][1] == "张三"

    def test_convert_to_tree(self):
        """测试转换为树形结构"""
        records = [
            {"department": "技术部", "name": "张三", "role": "工程师"},
            {"department": "技术部", "name": "李四", "role": "架构师"},
            {"department": "市场部", "name": "王五", "role": "经理"}
        ]

        tree = self.formatter._convert_to_tree(records, None)

        assert "group_by" in tree
        assert "tree" in tree
        assert "技术部" in tree["tree"]
        assert "市场部" in tree["tree"]

    def test_convert_to_json(self):
        """测试转换为JSON格式"""
        records = [
            {"id": "1", "name": "张三"},
            {"id": "2", "name": "李四"}
        ]

        json_data = self.formatter._convert_to_json(records, None)

        assert json_data == records  # JSON格式应保持原样

    def test_convert_to_markdown(self):
        """测试转换为Markdown"""
        records = [
            {"name": "张三", "age": "30"},
            {"name": "李四", "age": "25"}
        ]

        markdown = self.formatter._convert_to_markdown(records, None)

        assert "| name | age |" in markdown
        assert "| 张三 | 30 |" in markdown
        assert "| 李四 | 25 |" in markdown

    def test_convert_to_csv(self):
        """测试转换为CSV"""
        records = [
            {"name": "张三", "age": "30"},
            {"name": "李四", "age": "25"}
        ]

        csv_text = self.formatter._convert_to_csv(records, None)

        assert "name,age" in csv_text
        assert "张三,30" in csv_text
        assert "李四,25" in csv_text

    @patch('core.output_formatter.pd')
    def test_convert_to_excel(self, mock_pd):
        """测试转换为Excel（模拟pandas）"""
        # 模拟pandas
        mock_df = Mock()
        mock_buffer = Mock()
        mock_buffer.getvalue.return_value = b"excel_data"
        mock_pd.DataFrame.return_value = mock_df

        records = [{"name": "张三", "age": "30"}]

        with patch('core.output_formatter.io.BytesIO', return_value=mock_buffer):
            excel_data = self.formatter._convert_to_excel(records, None)

            mock_pd.DataFrame.assert_called_once_with(records)
            mock_df.to_excel.assert_called_once_with(mock_buffer, index=False)
            assert excel_data == b"excel_data"

    def test_convert_to_html(self):
        """测试转换为HTML"""
        records = [
            {"name": "张三", "age": "30"},
            {"name": "李四", "age": "25"}
        ]

        html = self.formatter._convert_to_html(records, None)

        assert "<table" in html
        assert "<th>name</th>" in html
        assert "<td>张三</td>" in html

    def test_explain_format_selection(self):
        """测试格式选择解释"""
        structure_features = {"has_tables": True, "complexity_score": 0.3}
        result_features = {
            "is_tabular": True,
            "record_count": 10,
            "has_nested": False
        }

        explanation = self.formatter._explain_format_selection(
            OutputFormat.TABLE,
            structure_features,
            result_features,
            None
        )

        assert "数据规整" in explanation
        assert "字段一致性" in explanation

    @patch('core.output_formatter.OutputFormatter._select_format_by_model')
    def test_model_based_selection(self, mock_model_select):
        """测试模型选择策略"""
        mock_model_select.return_value = OutputFormat.TREE

        self.formatter.config.selection_strategy = "model_based"

        records = [{"name": "张三", "age": "30"}]
        model_client = Mock()

        result = self.formatter.format_output(records, model_client=model_client)

        mock_model_select.assert_called_once()
        assert result["format"] == OutputFormat.TREE.value

    def test_format_output_adaptive_function(self):
        """测试自适应格式化快捷函数"""
        records = [{"name": "张三", "age": "30"}]

        with patch('core.output_formatter.OutputFormatter') as mock_class:
            mock_instance = Mock()
            mock_class.return_value = mock_instance
            mock_instance.format_output.return_value = {"format": "table", "data": []}

            result = format_output_adaptive(records)

            mock_class.assert_called_once()
            mock_instance.format_output.assert_called_once_with(records, None, None, None)

    def test_calculate_result_complexity(self):
        """测试结果复杂度计算"""
        records = [
            {"id": "1", "name": "张三" * 10, "description": "描述" * 20},
            {"id": "2", "name": "李四" * 5, "description": "另一个描述" * 10}
        ]

        complexity = self.formatter._calculate_result_complexity(records)

        assert 0 <= complexity <= 1.0

    def test_field_consistency_calculation(self):
        """测试字段一致性计算"""
        records = [
            {"id": "1", "name": "张三", "age": "30"},
            {"id": "2", "name": "李四"},  # 缺少age字段
            {"id": "3", "name": "王五", "age": "35"}
        ]

        features = self.formatter._analyze_result_features(records)

        # id和name字段应该完全一致
        assert features["field_consistency"]["id"] == 1.0
        assert features["field_consistency"]["name"] == 1.0
        # age字段一致性应该是2/3
        assert features["field_consistency"]["age"] == pytest.approx(2/3, 0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])