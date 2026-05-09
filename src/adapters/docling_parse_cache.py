"""
Docling 解析结果磁盘缓存（diskcache）。

命中时跳过 converter.convert，显著加速重复文件。
缓存键：绝对路径 + 文件大小/mtime + enable_ocr + 算法版本号。
不缓存 DataFrame 对象，命中后由 tables[].data 重建。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 与解析逻辑/字段结构变更时递增，使旧缓存失效
_CACHE_VERSION = 5


def _enabled() -> bool:
    """默认开启解析缓存；重复文件跳过 convert，提高跑通率与速度。"""
    return True


def _cache_dir() -> Path:
    p = Path.cwd() / ".cache" / "docling_parse"
    return p


def _disk_cache():
    try:
        from diskcache import Cache
    except ImportError:
        return None
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning("Docling 缓存目录不可用 %s: %s", d, e)
        return None
    try:
        return Cache(str(d))
    except Exception as e:
        logger.warning("Docling diskcache 初始化失败: %s", e)
        return None


def _make_key(path: Path, enable_ocr: bool) -> Optional[str]:
    try:
        st = path.stat()
        mtime = getattr(st, "st_mtime_ns", None)
        if mtime is None:
            mtime = int(st.st_mtime * 1_000_000_000)
        resolved = str(path.resolve())
        ocr = "1" if enable_ocr else "0"
        return f"v{_CACHE_VERSION}:{resolved}:{st.st_size}:{mtime}:{ocr}"
    except OSError:
        return None


def _strip_for_storage(result: Dict[str, Any]) -> Dict[str, Any]:
    """去掉不可稳定序列化/可由 tables 重建的字段。"""
    out = {k: v for k, v in result.items() if k != "tables_dataframes"}
    return out


def _rebuild_dataframes(payload: Dict[str, Any]) -> None:
    """原地填充 tables_dataframes（与 parse 中「仅追加非空 df」一致）。"""
    try:
        import pandas as pd
    except ImportError:
        payload["tables_dataframes"] = []
        return

    dfs = []
    for t in payload.get("tables") or []:
        raw = t.get("data")
        if not raw:
            continue
        try:
            dfs.append(pd.DataFrame(raw))
        except Exception:
            pass
    payload["tables_dataframes"] = dfs


def get_cached_parse(path: Path, enable_ocr: bool, parser_type: str) -> Optional[Dict[str, Any]]:
    """命中则返回完整 parse 结构（含 path/file_name 已更新）；未启用或失败返回 None。"""
    if not _enabled():
        return None
    c = _disk_cache()
    if c is None:
        return None
    key = _make_key(path, enable_ocr)
    if not key:
        return None
    try:
        raw = c.get(key)
    except Exception as e:
        logger.debug("Docling 缓存读取失败: %s", e)
        return None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    payload = dict(raw)
    payload["path"] = str(path)
    payload["file_name"] = path.name
    payload["parser_type"] = parser_type
    payload["type"] = parser_type
    _rebuild_dataframes(payload)
    logger.info("Docling 解析缓存命中: %s", path.name)
    return payload


def save_cached_parse(path: Path, enable_ocr: bool, result: Dict[str, Any]) -> None:
    if not _enabled():
        return
    if result.get("error"):
        return
    c = _disk_cache()
    if c is None:
        return
    key = _make_key(path, enable_ocr)
    if not key:
        return
    try:
        c.set(key, _strip_for_storage(result))
    except Exception as e:
        logger.debug("Docling 缓存写入失败: %s", e)
