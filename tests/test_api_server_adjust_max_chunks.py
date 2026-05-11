"""抽取路由：复杂度驱动的超时调整（与 HTTP 主链路一致，函数位于 routes.extract）。"""
import logging

from src.api.routes.extract import _adjust_timeout_by_complexity
from src.config import EXTRACTION_TIMEOUT


def test_adjust_timeout_respects_user_non_default():
    logger = logging.getLogger("test")
    info = {"estimated_processing_time_seconds": 999}
    assert _adjust_timeout_by_complexity(80, info, logger) == 80


def test_adjust_timeout_when_default_uses_complexity_and_caps():
    logger = logging.getLogger("test")
    info = {"estimated_processing_time_seconds": 100.0}
    out = _adjust_timeout_by_complexity(EXTRACTION_TIMEOUT, info, logger)
    assert out == min(int(100 * 1.5) + 10, 300)


def test_adjust_timeout_when_default_clamps_minimum():
    logger = logging.getLogger("test")
    info = {"estimated_processing_time_seconds": 1.0}
    out = _adjust_timeout_by_complexity(EXTRACTION_TIMEOUT, info, logger)
    assert out >= 30
