"""
集成测试：测试多个模块的协同工作
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.chunk_merger import ChunkMerger, MergeConfig
from core.parallel_processor import ParallelProcessor, ParallelConfig
from core.output_formatter import OutputFormatter, OutputFormat
from core.nested_extractor import NestedExtractor
from core.chunk_cache import ChunkCache


class TestIntegration:
    """集成测试类"""

    def test_chunk_processing_pipeline(self, sample_text_chunks):
        """测试分块处理流水线：缓存 -> 并行处理 -> 合并"""
        # 1. 创建缓存
        cache_config = ChunkCacheConfig(
            cache_dir="test_integration_cache",
            enable_semantic_cache=False,
            enable_text_hash_cache=True
        )
        cache = ChunkCache(cache_config)

        # 2. 创建并行处理器
        parallel_config = ParallelConfig(max_workers=2, timeout_per_task=2.0)
        processor = ParallelProcessor(parallel_config)

        # 3. 创建合并器
        merge_config = MergeConfig(similarity_threshold=0.8)
        merger = ChunkMerger(merge_config)

        # 处理函数（模拟抽取）
        def process_chunk(chunk):
            # 模拟处理逻辑
            return {
                "records": [{
                    "text": chunk["text"],
                    "type": chunk["type"],
                    "length": len(chunk["text"])
                }]
            }

        # 使用缓存的处理函数
        def cached_process(chunk):
            return cache.get_or_compute(chunk, process_chunk)

        # 并行处理分块
        results, stats = processor.process_chunks(sample_text_chunks, cached_process)

        # 提取所有记录
        all_records = []
        for result in results:
            if "records" in result:
                all_records.extend(result["records"])

        # 合并记录
        merged_records = merger.merge_records(all_records)

        # 验证结果
        assert len(merged_records) <= len(all_records)  # 合并后记录数应减少或不变
        assert len(results) == len(sample_text_chunks)  # 结果数应与分块数相同

        # 清理
        import shutil
        if os.path.exists("test_integration_cache"):
            shutil.rmtree("test_integration_cache", ignore_errors=True)

    def test_nested_extraction_with_formatting(self, mock_model_client):
        """测试嵌套提取与格式化集成"""
        # 测试文本（包含嵌套结构）
        test_text = """
        公司组织架构:
          技术部:
            前端团队: 张三(组长), 李四
            后端团队: 王五(组长), 赵六
          市场部:
            营销团队: 钱七(组长), 孙八
        """

        # 1. 提取嵌套实体
        extractor = NestedExtractor()
        nested_result = extractor.extract_nested_entities(
            test_text,
            ["organization", "department", "team", "employee"],
            mock_model_client
        )

        # 2. 扁平化嵌套实体
        flat_records = extractor.flatten_nested_entities(nested_result)

        # 3. 格式化输出
        formatter = OutputFormatter()
        formatted = formatter.format_output(
            flat_records,
            document_structure={"type": "organization_chart", "complexity_score": 0.7}
        )

        # 验证结果
        assert "format" in formatted
        assert "data" in formatted
        assert "metadata" in formatted

        # 根据数据特征，应该选择合适格式
        # 嵌套数据可能适合tree或json格式
        assert formatted["format"] in ["tree", "json", "table"]

    def test_complete_document_processing_flow(self, sample_records, mock_model_client):
        """测试完整文档处理流程"""
        # 模拟场景：文档解析 -> 抽取 -> 去重 -> 格式化

        # 1. 模拟分块处理结果
        chunk_results = [
            {
                "records": [
                    {"id": "1", "name": "张三", "age": "30"},
                    {"id": "2", "name": "李四", "age": "25"}
                ]
            },
            {
                "records": [
                    {"id": "1", "name": "张三", "city": "北京"},  # 重复ID，不同信息
                    {"id": "3", "name": "王五", "age": "35"}
                ]
            }
        ]

        # 2. 合并分块结果（去重）
        merger = ChunkMerger()
        merged_result = merger.merge_chunk_results(chunk_results)

        # 3. 分析文档结构特征
        document_structure = {
            "type": "tabular",
            "has_tables": True,
            "complexity_score": 0.4,
            "record_count": len(merged_result["records"])
        }

        # 4. 格式化输出
        formatter = OutputFormatter()
        formatted_output = formatter.format_output(
            merged_result["records"],
            document_structure,
            profile={
                "fields": [
                    {"name": "id"},
                    {"name": "name"},
                    {"name": "age"},
                    {"name": "city"}
                ]
            },
            model_client=mock_model_client
        )

        # 验证
        assert len(merged_result["records"]) == 3  # 应该合并为3条唯一记录
        assert "张三" in str(merged_result["records"])  # 应该包含合并后的记录
        assert "北京" in str(merged_result["records"])  # 合并后应该包含城市信息

        # 格式化输出应该包含所有必要字段
        assert formatted_output["metadata"]["record_count"] == 3

    def test_cache_and_parallel_integration(self, sample_text_chunks):
        """测试缓存与并行处理的集成"""
        # 创建缓存
        cache = ChunkCache(ChunkCacheConfig(
            cache_dir="test_cache_parallel",
            enable_text_hash_cache=True
        ))

        # 模拟处理函数（记录调用次数）
        process_call_count = 0

        def counting_process(chunk):
            nonlocal process_call_count
            process_call_count += 1
            return {
                "records": [{
                    "processed": True,
                    "content": chunk["text"],
                    "call_count": process_call_count
                }]
            }

        # 使用缓存的处理函数
        def cached_process(chunk):
            return cache.get_or_compute(chunk, counting_process)

        # 创建并行处理器
        processor = ParallelProcessor(ParallelConfig(max_workers=2))

        # 第一次处理（应该全部计算）
        results1, stats1 = processor.process_chunks(sample_text_chunks, cached_process)
        first_call_count = process_call_count

        # 第二次处理（应该全部缓存命中）
        results2, stats2 = processor.process_chunks(sample_text_chunks, cached_process)

        # 验证
        assert first_call_count == len(sample_text_chunks)  # 第一次应该每个分块都计算
        assert process_call_count == first_call_count  # 第二次不应该增加调用次数

        # 缓存命中率应该提高
        cache_stats = cache.get_stats()
        assert cache_stats["hit_rate"] > 0

        # 清理
        import shutil
        if os.path.exists("test_cache_parallel"):
            shutil.rmtree("test_cache_parallel", ignore_errors=True)

    def test_error_handling_integration(self):
        """测试错误处理集成"""
        # 测试当某个组件失败时，整个流程的健壮性

        # 创建会失败的处理函数
        def failing_process(chunk):
            if "fail" in chunk.get("text", ""):
                raise ValueError("处理失败")
            return {"records": [{"success": True}]}

        # 创建处理器（启用回退）
        processor = ParallelProcessor(ParallelConfig(
            max_workers=1,
            enable_fallback=True,
            timeout_per_task=1.0
        ))

        chunks = [
            {"text": "正常分块1", "type": "text"},
            {"text": "这个会失败 fail", "type": "text"},  # 这个会失败
            {"text": "正常分块2", "type": "text"}
        ]

        # 处理分块（应该能处理部分失败）
        results, stats = processor.process_chunks(chunks, failing_process)

        # 验证：应该有一些成功和一些失败
        assert stats["completed_tasks"] >= 2  # 至少2个成功
        assert stats["failed_tasks"] >= 1  # 至少1个失败

        # 结果应该包含错误信息
        error_results = [r for r in results if "error" in r]
        assert len(error_results) >= 1

    def test_format_selection_based_on_data(self, sample_records, nested_records):
        """测试基于数据特征的格式选择"""
        formatter = OutputFormatter()

        # 测试1：表格数据应该选择表格格式
        table_data = sample_records
        table_result = formatter.format_output(table_data)

        # 表格数据通常适合table或csv格式
        assert table_result["format"] in ["table", "csv"]
        assert "适合二维表格" in table_result["metadata"]["format_reason"]

        # 测试2：嵌套数据应该选择树形或JSON格式
        nested_result = formatter.format_output(nested_records)

        # 嵌套数据通常适合tree或json格式
        assert nested_result["format"] in ["tree", "json"]
        assert "嵌套" in nested_result["metadata"]["format_reason"] or \
               "层次" in nested_result["metadata"]["format_reason"]

    def test_realistic_document_scenario(self, mock_model_client):
        """测试真实文档场景"""
        # 模拟一个真实文档处理场景
        document_text = """
        2024年第一季度报告

        1. 财务数据
          营业收入: 1,234万元
          净利润: 567万元
          增长率: 15.3%

        2. 部门绩效
          技术部:
            - 项目完成率: 95%
            - 团队规模: 50人
          市场部:
            - 客户增长: 120家
            - 团队规模: 30人

        3. 重点项目
          项目A: 预算500万，进度80%
          项目B: 预算300万，进度60%
        """

        # 使用嵌套提取器识别结构
        extractor = NestedExtractor()
        nested_result = extractor.extract_nested_entities(
            document_text,
            ["report", "financial_data", "department", "project"],
            mock_model_client
        )

        # 扁平化以便格式化
        flat_records = extractor.flatten_nested_entities(nested_result)

        # 分析文档结构
        document_structure = {
            "type": "report",
            "has_headings": True,
            "has_lists": True,
            "has_tables": False,
            "complexity_score": 0.7,
            "sections": ["财务数据", "部门绩效", "重点项目"]
        }

        # 选择输出格式
        formatter = OutputFormatter()
        output = formatter.format_output(
            flat_records,
            document_structure,
            model_client=mock_model_client
        )

        # 验证输出合理性
        assert output["format"] in ["tree", "json", "markdown", "table"]
        assert output["metadata"]["record_count"] > 0

        # 格式选择应该有合理的解释
        assert len(output["metadata"]["format_reason"]) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])