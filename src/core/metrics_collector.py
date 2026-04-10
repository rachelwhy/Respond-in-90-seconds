"""
性能监控指标收集 — 收集、分析和报告系统性能指标

核心功能：
1. 收集关键性能指标（抽取成功率、处理时长、错误率等）
2. 实时监控和警报
3. 生成性能报告和分析
4. 支持历史数据查询和趋势分析

使用场景：
- 监控系统运行状态
- 识别性能瓶颈
- 容量规划和优化
- 故障诊断和根因分析

依赖：
- prometheus-client（可选，用于暴露指标）
- 时间序列数据库（可选，用于长期存储）
"""

import time
import json
import logging
import threading
import statistics
from typing import Dict, List, Any, Optional, Union, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from collections import defaultdict, deque
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """指标类型枚举"""
    COUNTER = "counter"      # 计数器（只增不减）
    GAUGE = "gauge"          # 测量值（可增可减）
    HISTOGRAM = "histogram"  # 直方图（分布统计）
    SUMMARY = "summary"      # 摘要（分位数统计）


@dataclass
class MetricConfig:
    """指标配置"""
    name: str
    type: MetricType
    description: str = ""
    labels: List[str] = field(default_factory=list)  # 标签列表
    buckets: List[float] = field(default_factory=lambda: [0.1, 0.5, 1.0, 2.0, 5.0, 10.0])  # 直方图分桶
    quantiles: List[float] = field(default_factory=lambda: [0.5, 0.9, 0.95, 0.99])  # 摘要分位数
    retention_days: int = 30  # 数据保留天数


@dataclass
class MetricValue:
    """指标值"""
    timestamp: datetime
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class MetricsCollector:
    """性能监控指标收集器

    收集和处理系统性能指标，支持实时监控和历史分析。
    """

    def __init__(self, config_path: Optional[str] = None):
        self._metrics: Dict[str, MetricConfig] = {}
        self._data: Dict[str, List[MetricValue]] = defaultdict(list)
        self._locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._start_time = datetime.now()

        # 默认指标配置
        self._setup_default_metrics()

        # 加载自定义配置
        if config_path:
            self._load_config(config_path)

        # 启动清理线程
        self._cleanup_thread = threading.Thread(target=self._cleanup_old_data, daemon=True)
        self._cleanup_thread.start()

        logger.info(f"性能监控指标收集器已启动")

    def _setup_default_metrics(self):
        """设置默认指标"""
        default_metrics = [
            MetricConfig(
                name="document_processing_total",
                type=MetricType.COUNTER,
                description="文档处理总数",
                labels=["model_type", "template_mode", "status"]
            ),
            MetricConfig(
                name="document_processing_duration_seconds",
                type=MetricType.HISTOGRAM,
                description="文档处理时长（秒）",
                labels=["model_type", "template_mode"]
            ),
            MetricConfig(
                name="chunk_processing_total",
                type=MetricType.COUNTER,
                description="分块处理总数",
                labels=["chunk_type", "status"]
            ),
            MetricConfig(
                name="chunk_processing_duration_seconds",
                type=MetricType.HISTOGRAM,
                description="分块处理时长（秒）"
            ),
            MetricConfig(
                name="model_calls_total",
                type=MetricType.COUNTER,
                description="模型调用总数",
                labels=["model_type", "status"]
            ),
            MetricConfig(
                name="model_call_duration_seconds",
                type=MetricType.HISTOGRAM,
                description="模型调用时长（秒）",
                labels=["model_type"]
            ),
            MetricConfig(
                name="cache_hits_total",
                type=MetricType.COUNTER,
                description="缓存命中总数",
                labels=["cache_type"]
            ),
            MetricConfig(
                name="cache_misses_total",
                type=MetricType.COUNTER,
                description="缓存未命中总数",
                labels=["cache_type"]
            ),
            MetricConfig(
                name="extraction_success_rate",
                type=MetricType.GAUGE,
                description="抽取成功率",
                labels=["template_type"]
            ),
            MetricConfig(
                name="records_extracted_total",
                type=MetricType.COUNTER,
                description="抽取记录总数",
                labels=["template_type"]
            ),
            MetricConfig(
                name="error_rate",
                type=MetricType.GAUGE,
                description="错误率",
                labels=["error_type"]
            ),
            MetricConfig(
                name="system_memory_usage_percent",
                type=MetricType.GAUGE,
                description="系统内存使用率"
            ),
            MetricConfig(
                name="system_cpu_usage_percent",
                type=MetricType.GAUGE,
                description="系统CPU使用率"
            ),
            MetricConfig(
                name="active_tasks",
                type=MetricType.GAUGE,
                description="活跃任务数"
            ),
            MetricConfig(
                name="queue_length",
                type=MetricType.GAUGE,
                description="队列长度"
            )
        ]

        for metric in default_metrics:
            self.register_metric(metric)

    def _load_config(self, config_path: str):
        """加载配置"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            for metric_data in config_data.get("metrics", []):
                metric = MetricConfig(
                    name=metric_data["name"],
                    type=MetricType(metric_data["type"]),
                    description=metric_data.get("description", ""),
                    labels=metric_data.get("labels", []),
                    buckets=metric_data.get("buckets", [0.1, 0.5, 1.0, 2.0, 5.0, 10.0]),
                    quantiles=metric_data.get("quantiles", [0.5, 0.9, 0.95, 0.99]),
                    retention_days=metric_data.get("retention_days", 30)
                )
                self.register_metric(metric)

            logger.info(f"从 {config_path} 加载了 {len(config_data.get('metrics', []))} 个指标配置")

        except Exception as e:
            logger.warning(f"加载指标配置失败: {e}")

    def register_metric(self, metric: MetricConfig):
        """注册指标"""
        self._metrics[metric.name] = metric
        logger.debug(f"注册指标: {metric.name} ({metric.type.value})")

    def record(self, name: str, value: float, labels: Optional[Dict[str, str]] = None,
               metadata: Optional[Dict[str, Any]] = None):
        """记录指标值"""
        if name not in self._metrics:
            logger.warning(f"未注册的指标: {name}")
            return

        metric_config = self._metrics[name]
        labels = labels or {}
        metadata = metadata or {}

        # 验证标签
        for label in metric_config.labels:
            if label not in labels:
                logger.warning(f"指标 {name} 缺少标签: {label}")

        metric_value = MetricValue(
            timestamp=datetime.now(),
            value=value,
            labels=labels.copy(),
            metadata=metadata.copy()
        )

        with self._locks[name]:
            self._data[name].append(metric_value)

        logger.debug(f"记录指标 {name}={value} labels={labels}")

    def increment(self, name: str, value: float = 1.0, labels: Optional[Dict[str, str]] = None,
                  metadata: Optional[Dict[str, Any]] = None):
        """增加计数器"""
        if name not in self._metrics:
            logger.warning(f"未注册的指标: {name}")
            return

        metric_config = self._metrics[name]
        if metric_config.type != MetricType.COUNTER:
            logger.warning(f"指标 {name} 不是计数器类型")
            return

        self.record(name, value, labels, metadata)

    def observe(self, name: str, value: float, labels: Optional[Dict[str, str]] = None,
                metadata: Optional[Dict[str, Any]] = None):
        """观察测量值（直方图/摘要）"""
        if name not in self._metrics:
            logger.warning(f"未注册的指标: {name}")
            return

        metric_config = self._metrics[name]
        if metric_config.type not in [MetricType.HISTOGRAM, MetricType.SUMMARY]:
            logger.warning(f"指标 {name} 不是直方图或摘要类型")
            return

        self.record(name, value, labels, metadata)

    def set_gauge(self, name: str, value: float, labels: Optional[Dict[str, str]] = None,
                  metadata: Optional[Dict[str, Any]] = None):
        """设置测量值（仪表）"""
        if name not in self._metrics:
            logger.warning(f"未注册的指标: {name}")
            return

        metric_config = self._metrics[name]
        if metric_config.type != MetricType.GAUGE:
            logger.warning(f"指标 {name} 不是仪表类型")
            return

        self.record(name, value, labels, metadata)

    def get_metric_data(self, name: str, start_time: Optional[datetime] = None,
                        end_time: Optional[datetime] = None,
                        label_filters: Optional[Dict[str, str]] = None) -> List[MetricValue]:
        """获取指标数据"""
        if name not in self._data:
            return []

        with self._locks[name]:
            data = self._data[name].copy()

        # 时间过滤
        if start_time:
            data = [d for d in data if d.timestamp >= start_time]
        if end_time:
            data = [d for d in data if d.timestamp <= end_time]

        # 标签过滤
        if label_filters:
            filtered_data = []
            for d in data:
                match = True
                for key, value in label_filters.items():
                    if d.labels.get(key) != value:
                        match = False
                        break
                if match:
                    filtered_data.append(d)
            data = filtered_data

        return data

    def get_metric_summary(self, name: str, start_time: Optional[datetime] = None,
                           end_time: Optional[datetime] = None,
                           label_filters: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """获取指标摘要统计"""
        data = self.get_metric_data(name, start_time, end_time, label_filters)
        if not data:
            return {}

        values = [d.value for d in data]

        summary = {
            "count": len(values),
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
            "mean": statistics.mean(values) if values else 0,
            "median": statistics.median(values) if values else 0,
            "stddev": statistics.stdev(values) if len(values) > 1 else 0,
            "latest_value": data[-1].value if data else 0,
            "latest_timestamp": data[-1].timestamp.isoformat() if data else None
        }

        # 添加分位数（如果是指标类型支持）
        metric_config = self._metrics.get(name)
        if metric_config and metric_config.type in [MetricType.HISTOGRAM, MetricType.SUMMARY]:
            if len(values) >= 5:  # 至少有5个数据点
                for q in metric_config.quantiles:
                    try:
                        summary[f"p{q*100:.0f}"] = statistics.quantiles(values, n=100)[int(q*100)-1]
                    except (IndexError, ValueError):
                        pass

        return summary

    def get_all_metrics_summary(self, start_time: Optional[datetime] = None,
                                end_time: Optional[datetime] = None) -> Dict[str, Any]:
        """获取所有指标摘要"""
        summary = {}
        for name in self._metrics.keys():
            metric_summary = self.get_metric_summary(name, start_time, end_time)
            if metric_summary:
                summary[name] = metric_summary

        # 添加系统信息
        summary["system"] = {
            "uptime_seconds": (datetime.now() - self._start_time).total_seconds(),
            "total_metrics": len(self._metrics),
            "total_data_points": sum(len(data) for data in self._data.values()),
            "collector_start_time": self._start_time.isoformat()
        }

        return summary

    def export_prometheus_format(self) -> str:
        """导出Prometheus格式指标"""
        lines = []

        for name, metric_config in self._metrics.items():
            data = self.get_metric_data(name)

            # 根据指标类型导出
            if metric_config.type == MetricType.COUNTER:
                # 计数器：累计值
                total_value = sum(d.value for d in data)
                label_str = self._format_labels({})
                lines.append(f"# HELP {name} {metric_config.description}")
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{label_str} {total_value}")

            elif metric_config.type == MetricType.GAUGE:
                # 仪表：最新值
                latest_value = data[-1].value if data else 0
                label_str = self._format_labels({})
                lines.append(f"# HELP {name} {metric_config.description}")
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{name}{label_str} {latest_value}")

            elif metric_config.type == MetricType.HISTOGRAM:
                # 直方图：需要特殊处理
                pass  # 简化实现

        return "\n".join(lines)

    def _format_labels(self, labels: Dict[str, str]) -> str:
        """格式化标签为Prometheus格式"""
        if not labels:
            return ""

        label_parts = [f'{k}="{v}"' for k, v in labels.items()]
        return "{" + ",".join(label_parts) + "}"

    def _cleanup_old_data(self):
        """清理旧数据"""
        while True:
            try:
                time.sleep(3600)  # 每小时清理一次

                cutoff_time = datetime.now() - timedelta(days=30)  # 默认保留30天
                removed_count = 0

                for name in list(self._data.keys()):
                    with self._locks[name]:
                        original_count = len(self._data[name])
                        self._data[name] = [
                            d for d in self._data[name]
                            if d.timestamp >= cutoff_time
                        ]
                        removed_count += original_count - len(self._data[name])

                if removed_count > 0:
                    logger.debug(f"清理了 {removed_count} 个旧数据点")

            except Exception as e:
                logger.warning(f"数据清理失败: {e}")

    def save_to_file(self, filepath: str, start_time: Optional[datetime] = None,
                     end_time: Optional[datetime] = None):
        """保存指标数据到文件"""
        try:
            export_data = {
                "metadata": {
                    "export_time": datetime.now().isoformat(),
                    "time_range": {
                        "start": start_time.isoformat() if start_time else None,
                        "end": end_time.isoformat() if end_time else None
                    }
                },
                "metrics": {}
            }

            for name in self._metrics.keys():
                data = self.get_metric_data(name, start_time, end_time)
                if data:
                    export_data["metrics"][name] = {
                        "config": asdict(self._metrics[name]),
                        "data": [
                            {
                                "timestamp": d.timestamp.isoformat(),
                                "value": d.value,
                                "labels": d.labels,
                                "metadata": d.metadata
                            }
                            for d in data
                        ]
                    }

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2, default=str)

            logger.info(f"指标数据已保存到: {filepath}")

        except Exception as e:
            logger.warning(f"保存指标数据失败: {e}")

    def record_document_processing(self, duration: float, model_type: str,
                                   template_mode: str, status: str, records_count: int = 0,
                                   error: Optional[str] = None):
        """记录文档处理指标"""
        # 文档处理总数
        self.increment("document_processing_total", labels={
            "model_type": model_type,
            "template_mode": template_mode,
            "status": status
        })

        # 处理时长
        if status == "success":
            self.observe("document_processing_duration_seconds", duration, labels={
                "model_type": model_type,
                "template_mode": template_mode
            })

        # 抽取记录数
        if records_count > 0:
            self.increment("records_extracted_total", value=records_count, labels={
                "template_mode": template_mode
            })

        # 错误率
        if error:
            self.increment("error_rate", labels={"error_type": error})

    def record_chunk_processing(self, duration: float, chunk_type: str, status: str):
        """记录分块处理指标"""
        self.increment("chunk_processing_total", labels={
            "chunk_type": chunk_type,
            "status": status
        })

        if status == "success":
            self.observe("chunk_processing_duration_seconds", duration)

    def record_model_call(self, duration: float, model_type: str, status: str, tokens_used: int = 0):
        """记录模型调用指标"""
        self.increment("model_calls_total", labels={
            "model_type": model_type,
            "status": status
        })

        if status == "success":
            self.observe("model_call_duration_seconds", duration, labels={
                "model_type": model_type
            })

        # 可以添加token使用量指标
        if tokens_used > 0:
            self.observe("model_tokens_used", tokens_used, labels={
                "model_type": model_type
            })

    def record_cache_operation(self, cache_type: str, hit: bool):
        """记录缓存操作指标"""
        if hit:
            self.increment("cache_hits_total", labels={"cache_type": cache_type})
        else:
            self.increment("cache_misses_total", labels={"cache_type": cache_type})

    def calculate_success_rate(self, metric_name: str, success_label: str = "success",
                               time_window_hours: int = 24) -> float:
        """计算成功率"""
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=time_window_hours)

        # 获取成功和总数
        success_data = self.get_metric_data(metric_name, start_time, end_time, {"status": success_label})
        total_data = self.get_metric_data(metric_name, start_time, end_time)

        success_count = sum(d.value for d in success_data)
        total_count = sum(d.value for d in total_data)

        if total_count == 0:
            return 0.0

        return success_count / total_count

    def generate_performance_report(self) -> Dict[str, Any]:
        """生成性能报告"""
        report = {
            "timestamp": datetime.now().isoformat(),
            "time_range": {
                "last_hour": (datetime.now() - timedelta(hours=1)).isoformat(),
                "last_day": (datetime.now() - timedelta(days=1)).isoformat(),
                "last_week": (datetime.now() - timedelta(days=7)).isoformat()
            },
            "summary": self.get_all_metrics_summary(),
            "success_rates": {},
            "recommendations": []
        }

        # 计算关键成功率
        report["success_rates"]["document_processing"] = self.calculate_success_rate(
            "document_processing_total", "success", 24
        )
        report["success_rates"]["model_calls"] = self.calculate_success_rate(
            "model_calls_total", "success", 24
        )

        # 生成建议
        doc_duration_summary = self.get_metric_summary("document_processing_duration_seconds")
        if doc_duration_summary.get("p95", 0) > 30:  # 95分位超过30秒
            report["recommendations"].append({
                "type": "performance",
                "message": "文档处理时间较长，建议检查模型调用或分块策略",
                "metric": "document_processing_duration_seconds",
                "value": doc_duration_summary["p95"]
            })

        cache_hit_rate = self.calculate_success_rate("cache_hits_total", time_window_hours=24)
        if cache_hit_rate < 0.3:  # 缓存命中率低于30%
            report["recommendations"].append({
                "type": "optimization",
                "message": "缓存命中率较低，建议调整缓存策略",
                "metric": "cache_hit_rate",
                "value": cache_hit_rate
            })

        return report


# 全局指标收集器实例
_global_metrics_collector: Optional[MetricsCollector] = None


def get_global_metrics_collector(config_path: Optional[str] = None) -> MetricsCollector:
    """获取全局指标收集器实例（单例模式）"""
    global _global_metrics_collector
    if _global_metrics_collector is None:
        _global_metrics_collector = MetricsCollector(config_path)
    return _global_metrics_collector


def record_document_processing_metric(duration: float, model_type: str, template_mode: str,
                                      status: str, records_count: int = 0, error: Optional[str] = None):
    """记录文档处理指标（快捷函数）"""
    collector = get_global_metrics_collector()
    collector.record_document_processing(duration, model_type, template_mode, status, records_count, error)


def record_model_call_metric(duration: float, model_type: str, status: str, tokens_used: int = 0):
    """记录模型调用指标（快捷函数）"""
    collector = get_global_metrics_collector()
    collector.record_model_call(duration, model_type, status, tokens_used)


def get_performance_report() -> Dict[str, Any]:
    """获取性能报告（快捷函数）"""
    collector = get_global_metrics_collector()
    return collector.generate_performance_report()