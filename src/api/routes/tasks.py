"""HTTP：异步任务创建、状态与产出下载 ``/api/tasks/*``（受 ``A23_ENABLE_TASKS`` 门控）。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src.api.storage_utils import safe_upload_name, sanitize_output_files_for_client
from src.config import ENABLE_TASKS, EXTRACTION_TIMEOUT
from src.core.debug_flags import is_debug_enabled
from src.core.llm_mode import normalize_llm_mode

if ENABLE_TASKS:
    from src.api.task_manager import task_manager


router = APIRouter()


def _require_tasks_enabled() -> None:
    if not ENABLE_TASKS:
        raise HTTPException(
            status_code=404,
            detail="算法端 /api/tasks/* 已禁用（A23_ENABLE_TASKS=false）。请使用 POST /api/extract/direct，详见 HTTP_API_USAGE.md。",
        )


def _require_task(task_id: str):
    _require_tasks_enabled()
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="task_id 不存在")
    return info


@router.get("/api/tasks")
def list_tasks():
    _require_tasks_enabled()
    return {"tasks": task_manager.list_tasks()}


@router.post("/api/tasks/create")
async def create_task(
    template: UploadFile = File(None),
    input_files: List[UploadFile] = File(...),
    note: str = Form(default=""),
    model_type: str = Form(default=""),
    template_mode: str = Form(default="auto"),
    template_description: str = Form(default=""),
    llm_mode: str = Form(default="full"),
    total_timeout: int = Form(default=EXTRACTION_TIMEOUT),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    _require_tasks_enabled()
    if not input_files:
        raise HTTPException(status_code=400, detail="至少需要上传一个输入文件")
    if template_mode not in ["file", "llm", "auto"]:
        raise HTTPException(status_code=400, detail="template_mode必须是file、llm或auto")

    template_path = None
    template_name = None
    if template_mode in ["file", "auto"]:
        if not template:
            if template_mode == "file":
                raise HTTPException(status_code=400, detail="file模式需要上传模板文件")
            elif not template_description:
                template_name = "default_template"
        else:
            template_name = template.filename or "template.bin"
    elif template_mode == "llm":
        if not template_description:
            raise HTTPException(status_code=400, detail="llm模式需要提供template_description")
        template_name = "llm_generated"

    info = task_manager.create_task_workspace(
        template_name=template_name or "template.bin",
        input_files=[f.filename or "unknown" for f in input_files],
    )
    template_dir = info.task_dir / "uploads" / "template"
    input_dir = info.task_dir / "uploads" / "input"

    if template and template_name:
        safe_template_name = safe_upload_name(template_name, "template.bin")
        template_path = template_dir / safe_template_name
        with template_path.open("wb") as f:
            shutil.copyfileobj(template.file, f)

    saved_inputs = []
    for idx, up in enumerate(input_files):
        name = safe_upload_name(up.filename, f"input_{idx+1}.bin")
        p = input_dir / name
        with p.open("wb") as f:
            shutil.copyfileobj(up.file, f)
        saved_inputs.append(name)

    task_manager.update_status(info.task_id, "queued")
    llm_mode = normalize_llm_mode(llm_mode)
    meta_extra = {
        "note": note,
        "saved_inputs": saved_inputs,
        "model_type": model_type,
        "template_mode": template_mode,
        "template_description": template_description,
        "llm_mode": llm_mode,
        "total_timeout": total_timeout,
        "max_chunks": max_chunks,
        "quiet": quiet,
    }
    (info.task_dir / "request_meta.json").write_text(json.dumps(meta_extra, ensure_ascii=False, indent=2), encoding="utf-8")
    task_manager.start_task(info.task_id, template_path=template_path, input_dir=input_dir)
    return {
        "task_id": info.task_id,
        "status": "queued",
        "template_name": template.filename if template else template_name,
        "input_files": saved_inputs,
        "template_mode": template_mode,
        "status_url": f"/api/tasks/{info.task_id}",
        "events_url": f"/api/tasks/{info.task_id}/events",
        "stream_url": f"/api/tasks/{info.task_id}/stream",
        "result_url": f"/api/tasks/{info.task_id}/result",
    }


@router.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    info = _require_task(task_id)
    output_files = sanitize_output_files_for_client(task_manager.get_output_files(task_id))
    return {"task": info.to_dict(), "output_files": output_files}


@router.get("/api/tasks/{task_id}/events")
def get_events(task_id: str, limit: int = 200):
    info = _require_task(task_id)
    return {"task_id": task_id, "status": info.status, "lines": task_manager.read_log(task_id, limit=limit)}


@router.get("/api/tasks/{task_id}/log")
def get_task_log(task_id: str, tail: int = 100):
    info = _require_task(task_id)
    lines = task_manager.read_log(task_id, limit=tail)
    return {"task_id": task_id, "status": info.status, "tail": tail, "line_count": len(lines), "lines": lines}


@router.get("/api/tasks/{task_id}/stream")
async def stream_events(task_id: str):
    _require_task(task_id)

    async def event_generator():
        sent = 0
        while True:
            current = task_manager.get_task(task_id)
            lines = task_manager.read_log(task_id, limit=10000)
            new_lines = lines[sent:]
            for line in new_lines:
                payload = {"type": "log", "message": line}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            sent = len(lines)
            if current and current.status in {"succeeded", "failed"}:
                payload = {
                    "type": "status",
                    "status": current.status,
                    "output_files": sanitize_output_files_for_client(task_manager.get_output_files(task_id)),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/api/tasks/{task_id}/result")
def get_result(task_id: str, include_report: bool = False):
    info = _require_task(task_id)
    output_files = task_manager.get_output_files(task_id)
    report_bundle_path = output_files.get("report_bundle")
    report_bundle = None
    if include_report and is_debug_enabled() and report_bundle_path and os.path.exists(report_bundle_path):
        report_bundle = json.loads(Path(report_bundle_path).read_text(encoding="utf-8"))
    output_files = sanitize_output_files_for_client(output_files)
    return {"task_id": task_id, "status": info.status, "output_files": output_files, "report_bundle": report_bundle}


@router.get("/api/tasks/{task_id}/download/{kind}")
def download_result(task_id: str, kind: str):
    _require_tasks_enabled()
    if kind == "report_bundle" and not is_debug_enabled():
        raise HTTPException(status_code=404, detail="调试产物已禁用下载")
    output_files = task_manager.get_output_files(task_id)
    path = output_files.get(kind)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"找不到输出文件：{kind}")
    return FileResponse(path=path, filename=Path(path).name)


@router.delete("/api/tasks/{task_id}")
def delete_task(task_id: str):
    _require_task(task_id)
    task_manager.delete_task(task_id)
    return JSONResponse({"ok": True, "deleted_task_id": task_id})


@router.post("/api/tasks/{task_id}/export-complete")
def acknowledge_task_export(task_id: str, cleanup: bool = True):
    _require_task(task_id)
    if cleanup:
        task_manager.delete_task(task_id)
        return JSONResponse({"ok": True, "task_id": task_id, "cleaned": True})
    return JSONResponse({"ok": True, "task_id": task_id, "cleaned": False})
