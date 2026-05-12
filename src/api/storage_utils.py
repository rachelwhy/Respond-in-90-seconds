"""上传目录、临时文件命名与安全清理；后台清理线程由 ``api_server`` 启动。"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Dict, Optional


def get_storage_root() -> Path:
    root = Path("storage/uploads")
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_temp_storage_dir() -> Path:
    d = get_storage_root() / "temp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def safe_upload_name(filename: Optional[str], fallback: str) -> str:
    """归一化上传文件名，避免路径穿越。"""
    raw = str(filename or "").strip()
    if raw and (any(sep in raw for sep in ("/", "\\")) or ".." in raw):
        return fallback
    name = Path(raw).name if raw else fallback
    if not name or name in (".", ".."):
        return fallback
    if any(sep in name for sep in ("/", "\\")) or ".." in name:
        return fallback
    return name


def sanitize_output_files_for_client(output_files: Dict[str, Any]) -> Dict[str, Any]:
    """对外返回的输出文件裁剪：始终隐藏 report_bundle。"""
    if not isinstance(output_files, dict):
        return {}

    sanitized = dict(output_files)
    sanitized.pop("report_bundle", None)

    by_input = sanitized.get("by_input")
    if isinstance(by_input, dict):
        cleaned_by_input: Dict[str, Any] = {}
        for key, item in by_input.items():
            if isinstance(item, dict):
                obj = dict(item)
                obj.pop("report_bundle", None)
                cleaned_by_input[key] = obj
            else:
                cleaned_by_input[key] = item
        sanitized["by_input"] = cleaned_by_input

    return sanitized


def cleanup_old_uploads_once(
    *,
    upload_retention_hours: int,
    temp_retention_hours: int,
) -> None:
    """执行一次过期目录/文件清理。"""
    storage_root = get_storage_root()
    dir_cutoff = time.time() - max(1, int(upload_retention_hours)) * 3600
    for child in storage_root.iterdir():
        if child.is_dir():
            if child.name == "temp":
                continue
            try:
                mtime = child.stat().st_mtime
                if mtime < dir_cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except Exception:
                continue

    temp_dir = get_temp_storage_dir()
    file_cutoff = time.time() - max(1, int(temp_retention_hours)) * 3600
    if temp_dir.exists():
        for file_path in temp_dir.iterdir():
            if not file_path.is_file():
                continue
            try:
                mtime = file_path.stat().st_mtime
                if mtime < file_cutoff:
                    file_path.unlink(missing_ok=True)
            except Exception:
                continue


def cleanup_old_uploads_loop(
    *,
    upload_retention_hours: int,
    temp_retention_hours: int,
    sleep_seconds: int = 3600,
) -> None:
    """后台清理线程循环：删除过期上传目录与临时文件。"""
    while True:
        try:
            cleanup_old_uploads_once(
                upload_retention_hours=upload_retention_hours,
                temp_retention_hours=temp_retention_hours,
            )
        except Exception:
            pass
        time.sleep(max(1, int(sleep_seconds)))
