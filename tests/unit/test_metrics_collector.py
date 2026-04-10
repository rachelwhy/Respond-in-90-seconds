"""
性能监控指标收集器测试
"""

import pytest
import sys
import os
import json
import time
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.metrics_collector import (
    MetricsCollector,
    MetricConfig,
    MetricType,
    MetricValue,
    get_global_metrics_collector,
    record_document_processing_metric,
    get_performance_report
)


class TestMetricsCollector:
    """性能监控指标收集器测试类"""

    def setup_method(self):
        """测试初始化"""
        self.collector = MetricsCollector()

    def test_initialization(self):
        """测试初始化"""
        assert len(self.collector._metrics) > 0  # 应该有默认指标
        assert "document_processing_total" in self.collector._metrics

    def test_register_metric(self):
        """测试注册指标"""
        metric = MetricConfig(
            name="test_metric",
            type=MetricType.COUNTER,
            description="测试指标",
            labels=["label1", "label2"]
        )

        self.collector.register_metric(metric)

        assert "test_metric" in self.collector._metrics
        assert self.collector._metrics["test_metric"] == metric

    def test_record_counter(self):
        """测试记录计数器"""
        metric_name = "document_processing_total"
        self.collector.increment(metric_name, value=1, labels={
            "model_type": "ollama",
            "template_mode": "file",
            "status": "success"
        })

        data = self.collector.get_metric_data(metric_name)
        assert len(data) == 1
        assert data[0].value == 1
        assert data[0].labels["model_type"] == "ollama"

    def test_record_gauge(self):
        """测试记录仪表"""
        metric_name = "extraction_success_rate"
        self.collector.set_gauge(metric_name, value=0.95, labels={
            "template_type": "environment"
        })

        data = self.collector.get_metric_data(metric_name)
        assert len(data) == 1
        assert data[0].value == 0.95

    def test_record_histogram(self):
        """测试记录直方图"""
        metric_name = "document_processing_duration_seconds"
        self.collector.observe(metric_name, value=2.5, labels={
            "model_type": "deepseek",
            "template_mode": "llm"
        })

        data = self.collector.get_metric_data(metric_name)
        assert len(data) == 1
        assert data[0].value == 2.5

    def test_get_metric_summary(self):
        """测试获取指标摘要"""
        metric_name = "document_processing_duration_seconds"

        # 添加一些数据
        for value in [1.0, 2.0, 3.0, 4.0, 5.0]:
            self.collector.observe(metric_name, value)

        summary = self.collector.get_metric_summary(metric_name)

        assert summary["count"] == 5
        assert summary["min"] == 1.0
        assert summary["max"] == 5.0
        assert summary["mean"] == 3.0
        assert summary["median"] == 3.0

    def test_time_filtering(self):
        """测试时间过滤"""
        metric_name = "test_time_filter"
        metric = MetricConfig(name=metric_name, type=MetricType.COUNTER)
        self.collector.register_metric(metric)

        # 记录不同时间的数据
        now = datetime.now()
        hour_ago = now - timedelta(hours=1)
        day_ago = now - timedelta(days=1)

        # 模拟不同时间的数据
        with patch('core.metrics_collector.datetime') as mock_datetime:
            # 一天前的数据
            mock_datetime.now.return_value = day_ago
            self.collector.increment(metric_name, value=1)

            # 一小时前的数据
            mock_datetime.now.return_value = hour_ago
            self.collector.increment(metric_name, value=2)

            # 现在的数据
            mock_datetime.now.return_value = now
            self.collector.increment(metric_name, value=3)

        # 获取最近2小时的数据
        two_hours_ago = now - timedelta(hours=2)
        recent_data = self.collector.get_metric_data(metric_name, start_time=two_hours_ago)

        # 应该只有最近2小时的数据（2个数据点）
        assert len(recent_data) == 2
        values = [d.value for d in recent_data]
        assert set(values) == {2, 3}  # 应该包含2和3，不包含1

    def test_label_filtering(self):
        """测试标签过滤"""
        metric_name = "document_processing_total"

        # 记录不同标签的数据
        self.collector.increment(metric_name, labels={
            "model_type": "ollama",
            "status": "success"
        })
        self.collector.increment(metric_name, labels={
            "model_type": "deepseek",
            "status": "success"
        })
        self.collector.increment(metric_name, labels={
            "model_type": "ollama",
            "status": "failed"
        })

        # 过滤特定标签
        ollama_data = self.collector.get_metric_data(
            metric_name,
            label_filters={"model_type": "ollama"}
        )

        assert len(ollama_data) == 2  # 两个ollama数据点

        # 过滤多个标签
        ollama_success_data = self.collector.get_metric_data(
            metric_name,
            label_filters={"model_type": "ollama", "status": "success"}
        )

        assert len(ollama_success_data) == 1  # 一个ollama成功数据点

    def test_record_document_processing(self):
        """测试文档处理指标记录"""
        duration = 5.5
        model_type = "ollama"
        template_mode = "file"
        status = "success"
        records_count = 10

        self.collector.record_document_processing(
            duration, model_type, template_mode, status, records_count
        )

        # 检查计数器
        counter_data = self.collector.get_metric_data("document_processing_total")
        assert len(counter_data) == 1
        assert counter_data[0].labels["status"] == "success"

        # 检查时长直方图
        duration_data = self.collector.get_metric_data("document_processing_duration_seconds")
        assert len(duration_data) == 1
        assert duration_data[0].value == duration

        # 检查记录数
        records_data = self.collector.get_metric_data("records_extracted_total")
        assert len(records_data) == 1
        assert records_data[0].value == records_count

    def test_record_model_call(self):
        """测试模型调用指标记录"""
        duration = 2.3
        model_type = "deepseek"
        status = "success"
        tokens_used = 1500

        self.collector.record_model_call(duration, model_type, status, tokens_used)

        # 检查计数器
        counter_data = self.collector.get_metric_data("model_calls_total")
        assert len(counter_data) == 1
        assert counter_data[0].labels["model_type"] == model_type

        # 检查时长直方图
        duration_data = self.collector.get_metric_data("model_call_duration_seconds")
        assert len(duration_data) == 1
        assert duration_data[0].value == duration

    def test_calculate_success_rate(self):
        """测试成功率计算"""
        metric_name = "document_processing_total"

        # 记录成功和失败
        for _ in range(8):
            self.collector.increment(metric_name, labels={"status": "success"})
        for _ in range(2):
            self.collector.increment(metric_name, labels={"status": "failed"})

        success_rate = self.collector.calculate_success_rate(metric_name, "success", 24)

        assert success_rate == 0.8  # 8/(8+2) = 0.8

    def test_generate_performance_report(self):
        """测试生成性能报告"""
        # 添加一些测试数据
        for i in range(5):
            self.collector.record_document_processing(
                duration=i + 1,
                model_type="ollama",
                template_mode="file",
                status="success",
                records_count=i * 10
            )

        report = self.collector.generate_performance_report()

        assert "timestamp" in report
        assert "summary" in report
        assert "success_rates" in report
        assert "recommendations" in report

        # 检查报告结构
        assert "document_processing" in report["success_rates"]
        assert "system" in report["summary"]

    def test_save_to_file(self):
        """测试保存到文件"""
        import tempfile

        # 添加一些测试数据
        self.collector.increment("document_processing_total", labels={"status": "success"})

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            tmp_path = tmp.name

        try:
            # 保存数据
            self.collector.save_to_file(tmp_path)

            # 验证文件内容
            with open(tmp_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            assert "metadata" in data
            assert "metrics" in data
            assert "document_processing_total" in data["metrics"]

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.metrics_collector.MetricsCollector._cleanup_old_data')
    def test_cleanup_thread_start(self, mock_cleanup):
        """测试清理线程启动"""
        # 验证清理线程在初始化时启动
        assert self.collector._cleanup_thread.is_alive()
        assert self.collector._cleanup_thread.daemon

    def test_all_metrics_summary(self):
        """测试所有指标摘要"""
        # 添加一些数据到不同指标
        self.collector.increment("document_processing_total", labels={"status": "success"})
        self.collector.set_gauge("extraction_success_rate", 0.95)

        summary = self.collector.get_all_metrics_summary()

        assert "document_processing_total" in summary
        assert "extraction_success_rate" in summary
        assert "system" in summary
        assert "total_metrics" in summary["system"]

    def test_invalid_metric_operations(self):
        """测试无效指标操作"""
        # 测试记录未注册的指标
        self.collector.record("non_existent_metric", 1.0)
        # 应该记录警告但不崩溃

        # 测试错误类型的操作
        self.collector.increment("extraction_success_rate", 1.0)  # 仪表类型不能递增
        # 应该记录警告但不崩溃


class TestGlobalFunctions:
    """全局函数测试"""

    def test_get_global_metrics_collector_singleton(self):
        """测试全局指标收集器单例模式"""
        # 清理全局实例
        import core.metrics_collector
        core.metrics_collector._global_metrics_collector = None

        # 第一次获取
        collector1 = get_global_metrics_collector()

        # 第二次获取，应该是同一个实例
        collector2 = get_global_metrics_collector()

        assert collector1 is collector2

    def test_record_document_processing_metric_function(self):
        """测试记录文档处理指标快捷函数"""
        # 清理全局实例
        import core.metrics_collector
        core.metrics_collector._global_metrics_collector = None

        with patch('core.metrics_collector.get_global_metrics_collector') as mock_get:
            mock_collector = Mock()
            mock_get.return_value = mock_collector

            record_document_processing_metric(
                duration=5.0,
                model_type="ollama",
                template_mode="file",
                status="success",
                records_count=10
            )

            mock_collector.record_document_processing.assert_called_once_with(
                5.0, "ollama", "file", "success", 10, None
            )

    def test_get_performance_report_function(self):
        """测试获取性能报告快捷函数"""
        import core.metrics_collector
        core.metrics_collector._global_metrics_collector = None

        with patch('core.metrics_collector.get_global_metrics_collector') as mock_get:
            mock_collector = Mock()
            mock_collector.generate_performance_report.return_value = {"test": "report"}
            mock_get.return_value = mock_collector

            report = get_performance_report()

            mock_collector.generate_performance_report.assert_called_once()
            assert report == {"test": "report"}


class TestMetricConfigSerialization:
    """指标配置序列化测试"""

    def test_metric_config_to_dict(self):
        """测试MetricConfig转换为字典"""
        from dataclasses import asdict

        metric = MetricConfig(
            name="test_metric",
            type=MetricType.COUNTER,
            description="测试指标",
            labels=["label1", "label2"],
            buckets=[0.1, 0.5, 1.0],
            quantiles=[0.5, 0.9],
            retention_days=7
        )

        metric_dict = asdict(metric)

        assert metric_dict["name"] == "test_metric"
        assert metric_dict["type"] == "counter"
        assert metric_dict["description"] == "测试指标"
        assert metric_dict["labels"] == ["label1", "label2"]
        assert metric_dict["retention_days"] == 7

    def test_metric_value_serialization(self):
        """测试MetricValue序列化"""
        timestamp = datetime.now()
        metric_value = MetricValue(
            timestamp=timestamp,
            value=42.0,
            labels={"model": "test"},
            metadata={"source": "test"}
        )

        # 转换为字典（模拟JSON序列化）
        value_dict = {
            "timestamp": metric_value.timestamp.isoformat(),
            "value": metric_value.value,
            "labels": metric_value.labels,
            "metadata": metric_value.metadata
        }

        assert value_dict["value"] == 42.0
        assert value_dict["labels"]["model"] == "test"
        assert "timestamp" in value_dict


if __name__ == "__main__":
    pytest.main([__file__, "-v"])