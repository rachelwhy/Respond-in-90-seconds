"""
测试配置和fixture
"""

import pytest
import sys
import os
import tempfile
import json
from pathlib import Path

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../src'))


@pytest.fixture
def temp_dir():
    """临时目录fixture"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_records():
    """样本记录fixture"""
    return [
        {"id": "1", "name": "张三", "age": "30", "city": "北京"},
        {"id": "2", "name": "李四", "age": "25", "city": "上海"},
        {"id": "3", "name": "王五", "age": "35", "city": "广州"}
    ]


@pytest.fixture
def nested_records():
    """嵌套记录fixture"""
    return [
        {
            "department": "技术部",
            "teams": [
                {"name": "前端团队", "members": ["张三", "李四"]},
                {"name": "后端团队", "members": ["王五", "赵六"]}
            ]
        },
        {
            "department": "市场部",
            "teams": [
                {"name": "营销团队", "members": ["钱七", "孙八"]}
            ]
        }
    ]


@pytest.fixture
def sample_text_chunks():
    """样本文本分块fixture"""
    return [
        {"text": "这是第一个分块，包含一些测试内容。", "type": "paragraph"},
        {"text": "第二个分块有更多详细信息。", "type": "paragraph"},
        {"text": "第三个分块是表格数据。", "type": "table"}
    ]


@pytest.fixture
def mock_model_client():
    """模拟模型客户端fixture"""
    class MockModelClient:
        def call_model(self, messages, model_type="ollama", **kwargs):
            return {
                "content": '{"entities": [{"type": "test", "name": "测试实体"}], "relationships": []}',
                "model": model_type,
                "usage": {"total_tokens": 100}
            }

    return MockModelClient()


@pytest.fixture
def sample_document_structure():
    """样本文档结构fixture"""
    return {
        "type": "document",
        "sections": ["引言", "方法", "结果"],
        "paragraphs": ["第一段", "第二段"],
        "tables": ["表1", "表2"],
        "max_depth": 2,
        "complexity_score": 0.6
    }


@pytest.fixture
def field_normalization_rules():
    """字段归一化规则fixture"""
    return {
        "default": {
            "strip_whitespace": True,
            "remove_commas": False
        },
        "types": {
            "numeric": {
                "extract_number_regex": "\\d+(?:\\.\\d+)?",
                "remove_commas": True
            },
            "percentage": {
                "extract_number_regex": "\\d+(?:\\.\\d+)?",
                "output_format": "{value}%"
            },
            "money": {
                "extract_number_regex": "\\d+(?:\\.\\d+)?",
                "remove_commas": True,
                "unit_conversions": {"万": 10000, "亿": 100000000}
            }
        },
        "fields": {
            "GDP总量": {"type": "money", "unit_conversions": {"万亿": 1000000000000}},
            "增长率": {"type": "percentage"}
        }
    }


@pytest.fixture
def alias_map():
    """字段别名映射fixture"""
    return {
        "城市": ["城市名称", "城市名", "地点"],
        "GDP总量": ["GDP", "国内生产总值", "生产总值"],
        "PM2.5监测值": ["PM2.5", "细颗粒物浓度", "空气质量指数"]
    }


@pytest.fixture
def test_profile():
    """测试模板profile fixture"""
    return {
        "name": "环境数据模板",
        "fields": [
            {"name": "城市", "type": "text", "required": True},
            {"name": "PM2.5监测值", "type": "numeric", "required": True},
            {"name": "GDP总量", "type": "money", "required": False}
        ],
        "output_format": "table",
        "dedup_key_fields": ["城市"]
    }