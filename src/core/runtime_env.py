"""进程级启动：可选加载 ``.env``，并在导入重型库前设置 OMP/MKL 线程环境。"""

from __future__ import annotations

import os
from typing import Any, Optional


def load_dotenv_if_available(*, logger: Optional[Any] = None, log_loaded: bool = False) -> bool:
    """
    尝试加载 .env；未安装 python-dotenv 时保持静默兼容。
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
        if log_loaded and logger is not None:
            logger.info("已从 .env 文件加载环境变量")
        return True
    except ImportError:
        if logger is not None and log_loaded:
            logger.warning("dotenv 未安装，将使用系统环境变量")
        return False


def bootstrap_runtime_env() -> None:
    """
    在导入 docling/torch 前设置 OMP/MKL：
    未显式指定时按 CPU 核数给保守默认值。
    """
    n = (os.environ.get("A23_OMP_NUM_THREADS") or os.environ.get("OMP_NUM_THREADS") or "").strip()
    if n:
        os.environ["OMP_NUM_THREADS"] = n
        os.environ.setdefault("MKL_NUM_THREADS", n)
        return
    cpu = os.cpu_count() or 4
    auto = min(8, max(1, cpu))
    os.environ.setdefault("OMP_NUM_THREADS", str(auto))
    os.environ.setdefault("MKL_NUM_THREADS", str(auto))


def initialize_runtime_env(*, logger: Optional[Any] = None, log_dotenv_loaded: bool = False) -> None:
    load_dotenv_if_available(logger=logger, log_loaded=log_dotenv_loaded)
    bootstrap_runtime_env()
