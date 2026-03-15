"""
工具函数模块：提供计时器、缓存、日志等通用工具
"""

import time
import hashlib
import json
from contextlib import contextmanager
from typing import Dict, Any, Optional, List
from collections import OrderedDict
from functools import wraps
import os


# ==================== 计时器 ====================

class Timer:
    """
    性能计时器
    支持多阶段计时、汇总统计
    """

    def __init__(self):
        self.timings = {}
        self.current = {}
        self._start_time = None
        self._last_time = None

    def start(self):
        """开始总计时"""
        self._start_time = time.time()
        self._last_time = self._start_time
        return self

    @contextmanager
    def measure(self, name: str):
        """
        测量代码块执行时间
        用法：
        with timer.measure("阶段1"):
            do_something()
        """
        start = time.time()
        try:
            yield
        finally:
            duration = time.time() - start
            if name in self.timings:
                self.timings[name].append(duration)
            else:
                self.timings[name] = [duration]

    def lap(self, name: str):
        """
        记录时间点（用于顺序计时）
        """
        now = time.time()
        if self._last_time is None:
            self._last_time = now
            self._start_time = now

        duration = now - self._last_time
        if name in self.timings:
            self.timings[name].append(duration)
        else:
            self.timings[name] = [duration]

        self._last_time = now
        return duration

    def get_summary(self) -> Dict[str, float]:
        """
        获取计时汇总（平均值、总时间等）
        """
        summary = {}
        total_time = 0

        for name, durations in self.timings.items():
            total = sum(durations)
            avg = total / len(durations) if durations else 0
            summary[f"{name}_total"] = round(total, 3)
            summary[f"{name}_avg"] = round(avg, 3)
            summary[f"{name}_count"] = len(durations)
            total_time += total

        summary["total_time"] = round(total_time, 3)
        if self._start_time:
            summary["wall_time"] = round(time.time() - self._start_time, 3)

        return summary

    def reset(self):
        """重置计时器"""
        self.timings.clear()
        self.current.clear()
        self._start_time = None
        self._last_time = None

    def __str__(self) -> str:
        """字符串表示"""
        summary = self.get_summary()
        parts = [f"{k}: {v}s" for k, v in summary.items()]
        return " | ".join(parts)


# 全局计时器实例
timer = Timer()


# ==================== LRU缓存 ====================

class LRUCache(OrderedDict):
    """
    LRU缓存，自动淘汰最久未使用的项
    """

    def __init__(self, maxsize: int = 128):
        super().__init__()
        self.maxsize = maxsize

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            oldest = next(iter(self))
            del self[oldest]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default


# ==================== 缓存装饰器 ====================

def cached(maxsize: int = 128, ttl: Optional[int] = None):
    """
    函数结果缓存装饰器
    :param maxsize: 最大缓存数量
    :param ttl: 过期时间（秒），None表示永不过期
    """

    def decorator(func):
        cache = LRUCache(maxsize)
        timestamps = {}

        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            key_parts = [str(arg) for arg in args]
            key_parts.extend(f"{k}:{v}" for k, v in sorted(kwargs.items()))
            key = hashlib.md5("|".join(key_parts).encode()).hexdigest()

            # 检查过期
            if ttl and key in timestamps:
                if time.time() - timestamps[key] > ttl:
                    del cache[key]
                    del timestamps[key]

            # 返回缓存或执行函数
            if key in cache:
                return cache[key]

            result = func(*args, **kwargs)
            cache[key] = result
            timestamps[key] = time.time()
            return result

        return wrapper

    return decorator


# ==================== 文本处理工具 ====================

def truncate(text: str, max_length: int = 100, ellipsis: str = "...") -> str:
    """
    截断文本到指定长度
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - len(ellipsis)] + ellipsis


def clean_text(text: str) -> str:
    """
    清洗文本：去除多余空白、特殊字符
    """
    if not text:
        return ""

    # 替换多个空白为单个空格
    import re
    text = re.sub(r'\s+', ' ', text)
    # 去除首尾空白
    text = text.strip()
    return text


def extract_numbers(text: str) -> List[float]:
    """
    从文本中提取所有数字
    """
    import re
    return [float(x) for x in re.findall(r'-?\d+\.?\d*', text)]


# ==================== 文件工具 ====================

def ensure_dir(path: str):
    """
    确保目录存在，如果不存在则创建
    """
    os.makedirs(path, exist_ok=True)


def safe_filename(filename: str) -> str:
    """
    生成安全的文件名（去除非法字符）
    """
    import re
    # Windows/Linux非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    # 控制字符
    filename = re.sub(r'[\x00-\x1f\x7f]', '', filename)
    # 去除首尾空格和点
    filename = filename.strip('. ')
    return filename or "unnamed"


# ==================== JSON工具 ====================

class EnhancedJSONEncoder(json.JSONEncoder):
    """
    增强的JSON编码器，支持更多类型
    """

    def default(self, obj):
        if hasattr(obj, 'to_dict'):
            return obj.to_dict()
        if isinstance(obj, (set, tuple)):
            return list(obj)
        if isinstance(obj, (Decimal, float)):
            return float(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, (bytes, bytearray)):
            return obj.decode('utf-8', errors='ignore')
        return super().default(obj)


def safe_json_dumps(obj: Any, **kwargs) -> str:
    """
    安全的JSON序列化
    """
    try:
        return json.dumps(obj, cls=EnhancedJSONEncoder, ensure_ascii=False, **kwargs)
    except Exception as e:
        return json.dumps({"error": f"序列化失败: {str(e)}"})


# ==================== 性能监控装饰器 ====================

def monitor(func):
    """
    性能监控装饰器：记录函数执行时间
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start
            print(f"⏱️ {func.__name__} 执行时间: {duration:.3f}s")
            return result
        except Exception as e:
            duration = time.time() - start
            print(f"❌ {func.__name__} 失败 ({duration:.3f}s): {e}")
            raise

    return wrapper


# 导入需要的模块（放在最后避免循环依赖）
from decimal import Decimal
from datetime import datetime