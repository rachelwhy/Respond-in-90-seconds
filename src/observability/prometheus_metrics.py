"""Prometheus 指标封装：未安装 ``prometheus_client`` 时为 no-op，主流程不依赖指标可用性。"""

from __future__ import annotations

import time
from typing import Any, Callable

try:
    from prometheus_client import Counter, Histogram

    _DOCLING_CACHE = Counter(
        "a23_docling_cache_requests_total",
        "Docling 解析路径：缓存命中或未命中次数",
        ("result",),
    )
    _DOCLING_CONVERT = Histogram(
        "a23_docling_convert_seconds",
        "Docling converter.convert 耗时（仅未命中缓存时）",
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0),
    )

    def inc_docling_cache_hit() -> None:
        _DOCLING_CACHE.labels(result="hit").inc()

    def inc_docling_cache_miss() -> None:
        _DOCLING_CACHE.labels(result="miss").inc()

    def observe_docling_convert(duration_s: float) -> None:
        _DOCLING_CONVERT.observe(max(0.0, duration_s))

except ImportError:

    def inc_docling_cache_hit() -> None:
        pass

    def inc_docling_cache_miss() -> None:
        pass

    def observe_docling_convert(duration_s: float) -> None:
        pass


def timed_convert(call: Callable[[], Any]) -> Any:
    """计时并上报 convert 耗时。"""
    t0 = time.perf_counter()
    try:
        return call()
    finally:
        observe_docling_convert(time.perf_counter() - t0)
