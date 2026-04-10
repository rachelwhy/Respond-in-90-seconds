"""
分块合并器测试
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.chunk_merger import ChunkMerger, MergeConfig, smart_merge_records


class TestChunkMerger:
    """分块合并器测试类"""

    def setup_method(self):
        """测试初始化"""
        self.config = MergeConfig(
            similarity_threshold=0.8,
            min_text_length=5,
            enable_debug=True
        )
        self.merger = ChunkMerger(self.config)

    def test_initialization(self):
        """测试初始化"""
        assert self.merger.config == self.config
        assert self.merger._cache == {}

    def test_merge_records_empty(self):
        """测试空记录合并"""
        records = []
        result = self.merger.merge_records(records)
        assert result == []

    def test_merge_records_single(self):
        """测试单条记录合并"""
        records = [
            {"name": "张三", "age": "30", "city": "北京"}
        ]
        result = self.merger.merge_records(records)
        assert len(result) == 1
        assert result[0]["name"] == "张三"

    def test_merge_by_key_fields_basic(self):
        """测试基于关键字段的合并"""
        records = [
            {"id": "1", "name": "张三", "age": "30"},
            {"id": "1", "name": "张三", "age": "31"},  # 相同ID，年龄不同
            {"id": "2", "name": "李四", "age": "25"}
        ]

        key_fields = ["id"]
        result = self.merger._merge_by_key_fields(records, key_fields)

        # 应该合并为2条记录
        assert len(result) == 2

        # 第一条记录应该使用更完整的信息
        record1 = [r for r in result if r.get("id") == "1"][0]
        assert record1["name"] == "张三"
        # 年龄应该保留（根据合并策略）

    def test_detect_key_fields(self):
        """测试关键字段检测"""
        records = [
            {"id": "001", "name": "张三", "description": "这是一个很长的描述文本"},
            {"id": "002", "name": "李四", "description": "另一个描述"},
            {"id": "003", "name": "王五", "description": "第三个描述"}
        ]

        key_fields = self.merger.detect_key_fields(records)

        # id字段应该被检测为关键字段
        assert "id" in key_fields
        # 描述字段不应该被检测（文本太长）
        assert "description" not in key_fields

    def test_record_to_text(self):
        """测试记录转文本"""
        record = {
            "name": "张三",
            "age": "30",
            "city": "北京",
            "_internal": "内部字段"  # 应该被忽略
        }

        text = self.merger._record_to_text(record)
        assert "name:张三" in text
        assert "age:30" in text
        assert "city:北京" in text
        assert "_internal" not in text  # 内部字段应该被排除

    def test_merge_single_record_strategies(self):
        """测试单条记录合并策略"""
        target = {"name": "张三", "age": ""}
        source = {"name": "张三", "age": "30"}

        # 测试 non_empty_wins 策略
        self.merger.config.merge_strategy = "non_empty_wins"
        self.merger._merge_single_record(target, source)
        assert target["age"] == "30"  # 应该用非空值填充

        # 测试 latest_wins 策略
        target = {"name": "张三", "age": "29"}
        self.merger.config.merge_strategy = "latest_wins"
        self.merger._merge_single_record(target, source)
        assert target["age"] == "30"  # 应该用新值替换

        # 测试 longer_wins 策略
        target = {"name": "张三", "description": "短描述"}
        source = {"name": "张三", "description": "这是一个更长的描述文本"}
        self.merger.config.merge_strategy = "longer_wins"
        self.merger._merge_single_record(target, source)
        assert target["description"] == "这是一个更长的描述文本"

    @patch('core.chunk_merger.RAPIDFUZZ_AVAILABLE', False)
    def test_merge_by_similarity_fallback(self):
        """测试相似度合并回退"""
        records = [
            {"text": "这是一个测试", "category": "A"},
            {"text": "这是另一个测试", "category": "A"}
        ]

        # 当rapidfuzz不可用时应该直接返回
        result = self.merger._merge_by_similarity(records, 0.8)
        assert result == records

    def test_merge_chunk_results(self):
        """测试分块结果合并"""
        chunk_results = [
            {"records": [{"id": "1", "name": "张三"}]},
            {"records": [{"id": "2", "name": "李四"}]},
            {"records": [{"id": "1", "name": "张三", "age": "30"}]}  # 重复ID
        ]

        result = self.merger.merge_chunk_results(chunk_results)

        assert "records" in result
        assert "metadata" in result
        assert len(result["records"]) == 2  # 应该合并为2条

    def test_smart_merge_records_function(self):
        """测试智能合并函数（向后兼容）"""
        records = [
            {"id": "1", "name": "张三"},
            {"id": "1", "name": "张三", "age": "30"}
        ]

        result = smart_merge_records(records, ["id"])
        assert len(result) == 1
        assert "age" in result[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])