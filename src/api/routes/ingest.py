from __future__ import annotations

import json
import os
import tempfile
import uuid as _uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.storage_utils import safe_upload_name
from src.config import ENABLE_TASKS

if ENABLE_TASKS:
    from src.api.task_manager import task_manager


router = APIRouter()


@router.post("/api/ingest")
async def ingest_files(
    files: List[UploadFile] = File(...),
    task_id: Optional[str] = Form(default=None),
    template_name: str = Form(default=""),
):
    """直接上传文件并入库（无需先建任务）。"""
    if not files:
        raise HTTPException(status_code=400, detail="至少需要上传一个文件")

    tid = task_id or _uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for up in files:
            name = safe_upload_name(up.filename, "unknown.bin")
            (tmp_path / name).write_bytes(await up.read())

        from src.core.reader import collect_input_bundle
        from src.core.db_ingest import ingest_bundle

        bundle = collect_input_bundle(str(tmp_path))
        result = ingest_bundle(task_id=tid, bundle=bundle, template_name=template_name)
    return result


@router.post("/api/tasks/{task_id}/ingest")
def ingest_task_result(
    task_id: str,
    template_name: str = Form(default=""),
):
    """将已完成任务的抽取结果推送入库。"""
    if not ENABLE_TASKS:
        raise HTTPException(
            status_code=404,
            detail="依赖任务的入库路由不可用（A23_ENABLE_TASKS=false）。请使用 POST /api/extract/direct 或 POST /api/ingest，详见 HTTP_API_USAGE.md。",
        )
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="task_id 不存在")
    if info.status != "succeeded":
        raise HTTPException(status_code=400, detail=f"任务尚未完成，当前状态: {info.status}")

    output_files = task_manager.get_output_files(task_id)
    report_bundle_path = output_files.get("report_bundle")
    extraction_result = None
    if report_bundle_path and os.path.exists(report_bundle_path):
        try:
            bundle_data = json.loads(Path(report_bundle_path).read_text(encoding="utf-8"))
            extraction_result = bundle_data.get("debug_result") or {}
        except Exception:
            pass

    input_dir = info.task_dir / "uploads" / "input"
    if not input_dir.exists():
        raise HTTPException(status_code=404, detail="任务输入目录不存在")

    from src.core.reader import collect_input_bundle
    from src.core.db_ingest import ingest_bundle

    bundle = collect_input_bundle(str(input_dir))
    result = ingest_bundle(
        task_id=task_id,
        bundle=bundle,
        extraction_result=extraction_result,
        template_name=template_name or (info.template_name if hasattr(info, "template_name") else ""),
    )
    return result


@router.get("/api/ingest/{task_id}/records")
@router.get("/api/ingest/{task_id}/record")
def get_ingest_records(task_id: str, limit: int = 200):
    """查询某 task_id 已入库的结构化记录"""
    from src.adapters.mysql_adapter import get_mysql_adapter

    adapter = get_mysql_adapter()
    if not adapter.is_available():
        raise HTTPException(status_code=503, detail="MySQL 不可用")
    return {
        "task_id": task_id,
        "structured": adapter.query_structured(task_id, limit=limit),
        "raw": adapter.query_raw(task_id, limit=min(limit, 50)),
    }


@router.get("/api/db/health")
def db_health():
    """检查 MySQL 连接状态"""
    from src.adapters.mysql_adapter import get_mysql_adapter
    from src.config import MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE

    adapter = get_mysql_adapter()
    ok = adapter.is_available()
    return {
        "mysql_available": ok,
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DATABASE,
    }
