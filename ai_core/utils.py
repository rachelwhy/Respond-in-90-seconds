"""
工具函数模块：计时器、去重等通用功能
"""

import time
from contextlib import contextmanager
from typing import Dict, List, Any


class Timer:
    """
    计时器：分阶段统计耗时
    从魏的代码迁移
    """

    def __init__(self):
        self.times = {}

    @contextmanager
    def measure(self, name: str):
        """上下文管理器：自动记录耗时"""
        start = time.perf_counter()
        yield
        self.times[name] = round(time.perf_counter() - start, 3)

    def get_summary(self) -> Dict[str, float]:
        """获取统计结果"""
        return self.times

    def reset(self):
        """重置"""
        self.times = {}


def merge_fields(fields_list: List[List[Dict]]) -> List[Dict]:
    """
    合并多个字段列表，按name去重
    """
    seen = set()
    merged = []
    for fields in fields_list:
        for f in fields:
            key = f"{f['name']}_{json.dumps(f['value'], sort_keys=True)}"
            if key not in seen:
                seen.add(key)
                merged.append(f)
    return merged


# 全局单例
timer = Timer()