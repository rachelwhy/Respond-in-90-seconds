"""
简化版截止时间上下文 - 用于超时控制

替代原复杂版本，提供基本功能：
1. 记录任务开始时间和超时时间
2. 检查是否已超时
3. 简单的上下文管理器
"""

import time
import logging
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class DeadlineContext:
    """简化版截止时间上下文"""

    def __init__(self, name: str, timeout_seconds: Optional[float] = None):
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.start_time = time.time()
        self.end_time = self.start_time + timeout_seconds if timeout_seconds else None
        self.is_expired_flag = False

        logger.debug(f"创建截止时间上下文: {name}, 超时={timeout_seconds}s")

    def is_expired(self) -> bool:
        """检查是否已超时"""
        if self.is_expired_flag:
            return True
        if self.end_time is None:
            return False
        expired = time.time() >= self.end_time
        if expired:
            self.is_expired_flag = True
            logger.debug(f"截止时间上下文已超时: {self.name}")
        return expired

    def remaining_time(self) -> Optional[float]:
        """返回剩余时间（秒），如果无超时则返回None"""
        if self.end_time is None:
            return None
        remaining = self.end_time - time.time()
        return max(0.0, remaining)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # 清理资源（如有需要）
        if exc_type is not None:
            logger.debug(f"截止时间上下文异常退出: {self.name}, 异常={exc_type}")
        else:
            logger.debug(f"截止时间上下文正常退出: {self.name}")
        return False  # 不抑制异常


def create_deadline_context(name: str, timeout_seconds: Optional[float] = None) -> DeadlineContext:
    """创建截止时间上下文（简化版）"""
    return DeadlineContext(name=name, timeout_seconds=timeout_seconds)


# 向后兼容：导出原模块可能导出的其他函数
__all__ = ["DeadlineContext", "create_deadline_context"]