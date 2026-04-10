"""
并行处理器测试
"""

import pytest
import sys
import os
import time
from unittest.mock import Mock, patch, MagicMock

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.parallel_processor import ParallelProcessor, ParallelConfig, process_chunks_in_parallel


class TestParallelProcessor:
    """并行处理器测试类"""

    def setup_method(self):
        """测试初始化"""
        self.config = ParallelConfig(
            max_workers=2,
            timeout_per_task=1.0,
            overall_timeout=5.0,
            enable_fallback=True
        )
        self.processor = ParallelProcessor(self.config)

    def test_initialization(self):
        """测试初始化"""
        assert self.processor.config == self.config
        assert self.processor._task_stats["total_tasks"] == 0
        assert self.processor._executor is None

    def test_process_chunks_empty(self):
        """测试空分块处理"""
        chunks = []
        process_func = Mock()

        results, stats = self.processor.process_chunks(chunks, process_func)

        assert results == []
        assert stats["total_tasks"] == 0

    def test_is_suitable_for_parallel(self):
        """测试并行适用性判断"""
        # 单分块不适合并行
        chunks = [{"text": "测试", "type": "text"}]
        assert not self.processor._is_suitable_for_parallel(chunks)

        # 多分块适合并行
        chunks = [
            {"text": "测试1", "type": "text"},
            {"text": "测试2", "type": "text"},
            {"text": "测试3", "type": "text"}
        ]
        assert self.processor._is_suitable_for_parallel(chunks)

        # 分块太大不适合并行
        chunks = [
            {"text": "x" * 6000, "type": "text"},
            {"text": "y" * 6000, "type": "text"}
        ]
        assert not self.processor._is_suitable_for_parallel(chunks)

    def test_prepare_tasks(self):
        """测试任务准备"""
        chunks = [
            {"text": "测试1", "type": "text"},
            {"text": "测试2", "type": "text"}
        ]
        process_func = Mock()
        context = {"source": "test"}

        tasks = self.processor._prepare_tasks(chunks, process_func, context)

        assert len(tasks) == 2
        assert tasks[0]["index"] == 0
        assert tasks[0]["chunk"] == chunks[0]
        assert tasks[0]["process_func"] == process_func
        assert tasks[0]["context"] == context
        assert "task_id" in tasks[0]

    def test_process_single_task_success(self):
        """测试单任务处理成功"""
        task = {
            "index": 0,
            "chunk": {"text": "测试", "type": "text"},
            "process_func": lambda x: {"result": "success", "records": [x]},
            "context": {},
            "task_id": "test_001"
        }

        result = self.processor._process_single_task(task)

        assert "records" in result
        assert result["_chunk_metadata"]["index"] == 0
        assert result["_chunk_metadata"]["task_id"] == "test_001"

    def test_process_single_task_exception(self):
        """测试单任务处理异常"""
        def failing_func(x):
            raise ValueError("测试异常")

        task = {
            "index": 0,
            "chunk": {"text": "测试", "type": "text"},
            "process_func": failing_func,
            "context": {},
            "task_id": "test_001"
        }

        result = self.processor._process_single_task(task)

        assert "error" in result
        assert result["records"] == []
        assert "测试异常" in result["error"]

    def test_sequential_fallback(self):
        """测试串行回退"""
        chunks = [
            {"text": "测试1", "type": "text"},
            {"text": "测试2", "type": "text"}
        ]

        def process_func(chunk):
            return {"result": chunk["text"], "records": [chunk]}

        results, stats = self.processor._sequential_fallback(chunks, process_func, {})

        assert len(results) == 2
        assert stats["total_tasks"] == 2
        assert stats["completed_tasks"] == 2

    @patch('concurrent.futures.ThreadPoolExecutor')
    def test_execute_parallel_success(self, mock_executor_class):
        """测试并行执行成功"""
        # 模拟线程池
        mock_executor = Mock()
        mock_future1 = Mock()
        mock_future2 = Mock()

        # 设置future结果
        mock_future1.result.return_value = {"records": [{"id": "1"}]}
        mock_future2.result.return_value = {"records": [{"id": "2"}]}

        mock_future1.exception.return_value = None
        mock_future2.exception.return_value = None

        # 模拟as_completed
        mock_executor.__enter__.return_value.submit.side_effect = [mock_future1, mock_future2]
        mock_executor_class.return_value = mock_executor

        # 模拟as_completed迭代器
        as_completed_results = [mock_future1, mock_future2]

        with patch('concurrent.futures.as_completed', return_value=as_completed_results):
            # 准备任务
            tasks = [
                {
                    "index": 0,
                    "chunk": {"text": "测试1", "type": "text"},
                    "process_func": Mock(),
                    "context": {},
                    "task_id": "test_001"
                },
                {
                    "index": 1,
                    "chunk": {"text": "测试2", "type": "text"},
                    "process_func": Mock(),
                    "context": {},
                    "task_id": "test_002"
                }
            ]

            # 这里需要绕过实际的线程池执行
            with patch.object(self.processor, '_process_single_task') as mock_process:
                mock_process.side_effect = [
                    {"records": [{"id": "1"}]},
                    {"records": [{"id": "2"}]}
                ]

                results = self.processor._execute_parallel(tasks)

                assert len(results) == 2
                assert self.processor._task_stats["completed_tasks"] == 2

    def test_get_processing_stats(self):
        """测试获取处理统计"""
        self.processor._task_stats = {
            "total_tasks": 10,
            "completed_tasks": 8,
            "failed_tasks": 1,
            "timeout_tasks": 1
        }

        stats = self.processor.get_processing_stats()
        assert stats["total_tasks"] == 10
        assert stats["completed_tasks"] == 8

    def test_reset_stats(self):
        """测试重置统计"""
        self.processor._task_stats = {
            "total_tasks": 10,
            "completed_tasks": 8,
            "failed_tasks": 1,
            "timeout_tasks": 1
        }

        self.processor.reset_stats()

        assert self.processor._task_stats["total_tasks"] == 0
        assert self.processor._task_stats["completed_tasks"] == 0

    def test_should_use_parallel_function(self):
        """测试should_use_parallel函数"""
        from core.parallel_processor import should_use_parallel

        # 分块数量不足
        chunks = [{"text": "测试", "type": "text"}]
        assert not should_use_parallel(chunks, min_chunks=3)

        # 分块数量足够
        chunks = [
            {"text": "测试1", "type": "text"},
            {"text": "测试2", "type": "text"},
            {"text": "测试3", "type": "text"}
        ]
        assert should_use_parallel(chunks, min_chunks=3)

    def test_process_chunks_in_parallel_function(self):
        """测试并行处理快捷函数"""
        chunks = [
            {"text": "测试1", "type": "text"},
            {"text": "测试2", "type": "text"}
        ]

        def process_func(chunk):
            return {"result": chunk["text"], "records": [chunk]}

        # 由于实际并行测试复杂，这里只测试函数调用
        # 在实际测试中，可以mock相关组件
        with patch('core.parallel_processor.ParallelProcessor') as mock_class:
            mock_instance = Mock()
            mock_class.return_value = mock_instance
            mock_instance.process_chunks.return_value = ([], {})

            results, stats = process_chunks_in_parallel(chunks, process_func, max_workers=2)

            mock_class.assert_called_once()
            mock_instance.process_chunks.assert_called_once_with(chunks, process_func, None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])