"""
并行切片处理器 — 加速长文档处理

核心功能：
1. 并行处理独立的语义分块
2. 保持处理结果的原始顺序
3. 支持超时控制和优雅降级
4. 线程安全的模型调用

使用场景：
- 长文档（>10页）包含多个独立语义块
- 分块间无强依赖关系（如表格、段落）
- 需要加速处理的批量任务

注意：
- 模型调用可能有并发限制（如 Ollama 单实例）
- 建议控制最大并发数（默认 2-4）
- 当并行失败时自动回退到串行处理
"""

import concurrent.futures
import logging
import time
from typing import List, Dict, Any, Optional, Callable, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


@dataclass
class ParallelConfig:
    """并行处理配置"""
    max_workers: int = 4  # 最大并发线程数
    timeout_per_task: Optional[float] = 30.0  # 单任务超时（秒）
    overall_timeout: Optional[float] = 120.0  # 总超时（秒）
    enable_fallback: bool = True  # 并行失败时回退到串行
    preserve_order: bool = True  # 保持原始顺序
    # 模型调用限制（避免压垮本地模型）
    max_concurrent_model_calls: int = 2  # 最大并发模型调用数
    model_call_delay: float = 0.1  # 模型调用间的最小延迟（秒）


class ParallelProcessor:
    """并行切片处理器

    将独立的语义分块分配到线程池并行处理，加速长文档抽取。
    """

    def __init__(self, config: Optional[ParallelConfig] = None):
        self.config = config or ParallelConfig()
        self._executor = None
        self._task_stats = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "timeout_tasks": 0,
        }

    def process_chunks(
        self,
        chunks: List[Dict[str, Any]],
        process_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        chunk_context: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """并行处理分块

        Args:
            chunks: 分块列表，每个分块至少包含 {"text": str, "type": str}
            process_func: 处理函数，接收分块字典，返回处理结果
            chunk_context: 可选，传递给每个分块的上下文信息

        Returns:
            (results, stats): 处理结果列表（按原始顺序）和处理统计信息
        """
        if not chunks:
            return [], self._task_stats

        # 检查是否适合并行处理
        if not self._is_suitable_for_parallel(chunks):
            logger.info("分块不适合并行处理，回退到串行模式")
            return self._sequential_fallback(chunks, process_func, chunk_context)

        # 准备任务
        tasks = self._prepare_tasks(chunks, process_func, chunk_context)

        # 执行并行处理
        try:
            results = self._execute_parallel(tasks)
            return results, self._task_stats
        except Exception as e:
            logger.warning(f"并行处理失败: {e}")
            if self.config.enable_fallback:
                logger.info("回退到串行处理模式")
                return self._sequential_fallback(chunks, process_func, chunk_context)
            else:
                raise

    def _is_suitable_for_parallel(self, chunks: List[Dict[str, Any]]) -> bool:
        """检查分块是否适合并行处理"""
        if len(chunks) <= 1:
            logger.debug("分块数量 <= 1，不适合并行")
            return False

        # 检查分块类型：表格和标题可能不适合并行
        table_chunks = [c for c in chunks if c.get("type") == "table"]
        if len(table_chunks) > 0:
            logger.debug(f"包含 {len(table_chunks)} 个表格分块，可能不适合并行")
            # 表格通常已通过直读处理，不会进入LLM路径
            pass

        # 检查分块大小分布
        text_lengths = [len(c.get("text", "")) for c in chunks]
        avg_length = sum(text_lengths) / len(text_lengths) if text_lengths else 0
        if avg_length > 5000:  # 分块太大，并行收益有限
            logger.debug(f"分块平均长度 {avg_length:.0f} 字符，可能不适合并行")
            return False

        # 基本检查通过
        return True

    def _prepare_tasks(
        self,
        chunks: List[Dict[str, Any]],
        process_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        chunk_context: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """准备并行任务"""
        tasks = []
        for i, chunk in enumerate(chunks):
            task = {
                "index": i,
                "chunk": chunk,
                "process_func": process_func,
                "context": chunk_context or {},
                "task_id": f"chunk_{i}_{hash(str(chunk)) % 10000:04d}"
            }
            tasks.append(task)
        return tasks

    def _execute_parallel(self, tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """执行并行处理"""
        total_tasks = len(tasks)
        self._task_stats["total_tasks"] = total_tasks
        logger.info(f"开始并行处理 {total_tasks} 个分块，最大并发数: {self.config.max_workers}")

        start_time = time.time()
        results = [None] * total_tasks  # 预分配结果列表

        # 使用 ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # 提交所有任务
            future_to_index = {}
            for task in tasks:
                future = executor.submit(self._process_single_task, task)
                future_to_index[future] = task["index"]

                # 控制模型调用并发数（简单延迟）
                if self.config.model_call_delay > 0:
                    time.sleep(self.config.model_call_delay)

            # 收集结果（按完成顺序）
            completed = 0
            for future in as_completed(future_to_index):
                if self.config.overall_timeout:
                    elapsed = time.time() - start_time
                    if elapsed > self.config.overall_timeout:
                        logger.warning(f"总处理时间超时 ({elapsed:.1f}s > {self.config.overall_timeout}s)")
                        break

                index = future_to_index[future]
                try:
                    result = future.result(timeout=self.config.timeout_per_task)
                    results[index] = result
                    self._task_stats["completed_tasks"] += 1
                    completed += 1
                except concurrent.futures.TimeoutError:
                    logger.warning(f"分块 {index} 处理超时")
                    results[index] = {"error": "timeout", "records": []}
                    self._task_stats["timeout_tasks"] += 1
                except Exception as e:
                    logger.warning(f"分块 {index} 处理失败: {e}")
                    results[index] = {"error": str(e), "records": []}
                    self._task_stats["failed_tasks"] += 1

                # 进度日志
                if completed % max(1, total_tasks // 10) == 0:  # 每10%记录一次
                    logger.info(f"并行处理进度: {completed}/{total_tasks} ({completed/total_tasks*100:.0f}%)")

        # 清理 None 结果（超时跳过的任务）
        final_results = []
        for i, res in enumerate(results):
            if res is None:
                logger.debug(f"分块 {i} 未完成，使用空结果")
                final_results.append({"records": [], "metadata": {"skipped": True}})
            else:
                final_results.append(res)

        total_time = time.time() - start_time
        logger.info(f"并行处理完成: {self._task_stats['completed_tasks']}/{total_tasks} 成功, "
                   f"耗时 {total_time:.1f}s")

        return final_results

    def _process_single_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """处理单个分块任务"""
        chunk = task["chunk"]
        process_func = task["process_func"]
        context = task["context"]

        try:
            # 添加上下文信息到分块
            chunk_with_context = dict(chunk)
            chunk_with_context.update(context)
            chunk_with_context["_chunk_index"] = task["index"]

            # 执行处理函数
            result = process_func(chunk_with_context)

            # 确保结果格式
            if not isinstance(result, dict):
                result = {"records": [result] if result else []}
            elif "records" not in result:
                result = {"records": [result]}

            # 添加分块元数据
            result["_chunk_metadata"] = {
                "index": task["index"],
                "type": chunk.get("type", "unknown"),
                "length": len(chunk.get("text", "")),
                "task_id": task["task_id"]
            }

            return result

        except Exception as e:
            logger.error(f"分块 {task['index']} 处理异常: {e}")
            return {
                "records": [],
                "error": str(e),
                "_chunk_metadata": {
                    "index": task["index"],
                    "error": str(e),
                    "task_id": task["task_id"]
                }
            }

    def _sequential_fallback(
        self,
        chunks: List[Dict[str, Any]],
        process_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        chunk_context: Optional[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """串行处理回退"""
        logger.info("使用串行处理模式")
        results = []
        stats = {"total_tasks": len(chunks), "completed_tasks": 0, "failed_tasks": 0, "timeout_tasks": 0}

        for i, chunk in enumerate(chunks):
            try:
                chunk_with_context = dict(chunk)
                if chunk_context:
                    chunk_with_context.update(chunk_context)
                chunk_with_context["_chunk_index"] = i

                result = process_func(chunk_with_context)
                if not isinstance(result, dict):
                    result = {"records": [result] if result else []}
                elif "records" not in result:
                    result = {"records": [result]}

                results.append(result)
                stats["completed_tasks"] += 1

                # 进度日志
                if (i + 1) % max(1, len(chunks) // 10) == 0:
                    logger.info(f"串行处理进度: {i+1}/{len(chunks)}")

            except Exception as e:
                logger.warning(f"串行处理分块 {i} 失败: {e}")
                results.append({"records": [], "error": str(e)})
                stats["failed_tasks"] += 1

        return results, stats

    def get_processing_stats(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        return dict(self._task_stats)

    def reset_stats(self):
        """重置统计信息"""
        self._task_stats = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "timeout_tasks": 0,
        }


# 工具函数：判断是否启用并行处理
def should_use_parallel(chunks: List[Dict[str, Any]], min_chunks: int = 3) -> bool:
    """判断是否应该使用并行处理"""
    if len(chunks) < min_chunks:
        return False

    # 检查配置（通过配置管理器）
    from src.core.config_manager import get_config_manager
    _config = get_config_manager()
    if _config.get_bool("DISABLE_PARALLEL", False):
        return False

    # 检查分块类型
    text_chunks = [c for c in chunks if c.get("type") != "table"]
    if len(text_chunks) <= 1:
        return False

    return True


# 快捷函数：并行处理分块
def process_chunks_in_parallel(
    chunks: List[Dict[str, Any]],
    process_func: Callable[[Dict[str, Any]], Dict[str, Any]],
    max_workers: int = 4,
    chunk_context: Optional[Dict[str, Any]] = None
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """并行处理分块的快捷函数"""
    config = ParallelConfig(max_workers=max_workers)
    processor = ParallelProcessor(config)
    return processor.process_chunks(chunks, process_func, chunk_context)