from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from src.api.storage_utils import get_temp_storage_dir, safe_upload_name


router = APIRouter()


@router.get("/api/download/temp/{filename}")
def download_temp_file(filename: str):
    """下载临时生成的文件。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    file_path = get_temp_storage_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")

    content_type = "application/octet-stream"
    if filename.lower().endswith(".xlsx"):
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif filename.lower().endswith(".docx"):
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif filename.lower().endswith(".doc"):
        content_type = "application/msword"

    return FileResponse(path=str(file_path), filename=filename, media_type=content_type)


@router.post("/api/download/temp/{filename}/export-complete")
def acknowledge_temp_export(filename: str):
    """后端确认临时导出文件已接收，触发立即删除。"""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="无效的文件名")

    file_path = get_temp_storage_dir() / filename
    if not file_path.exists():
        return JSONResponse({"ok": True, "filename": filename, "deleted": False})

    try:
        file_path.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"清理失败: {e}")
    return JSONResponse({"ok": True, "filename": filename, "deleted": True})


@router.post("/api/document/operate")
async def operate_document_endpoint(
    file: UploadFile = File(...),
    instruction: str = Form(...),
    backup: bool = Form(default=True),
):
    """文档智能操作接口。"""
    work_dir = Path(tempfile.mkdtemp(prefix="a23_operate_"))
    try:
        safe_name = safe_upload_name(file.filename, "document.bin")
        doc_path = work_dir / safe_name
        doc_path.write_bytes(await file.read())

        suffix = doc_path.suffix.lower()
        original_stem = Path(safe_name).stem
        out_path = work_dir / f"{original_stem}_result{suffix}"

        from src.core.doc_operator import operate_document

        result = operate_document(
            instruction=instruction,
            document_path=str(doc_path),
            output_path=str(out_path),
            backup=backup,
        )
        if result.get("status") == "error":
            raise HTTPException(status_code=422, detail=result.get("message", "操作失败"))

        if result.get("operation") == "extract_data" or result.get("records") is not None:
            return JSONResponse(
                {
                    "status": "ok",
                    "operation": "extract_data",
                    "records": result.get("records", []),
                    "count": result.get("count", len(result.get("records", []))),
                    "command": result.get("command", {}),
                }
            )

        if not out_path.exists():
            raise HTTPException(status_code=500, detail="操作未生成输出文件")

        return FileResponse(
            path=str(out_path),
            filename=f"{original_stem}_result{suffix}",
            media_type="application/octet-stream",
            headers={
                "X-Operation": result.get("operation", ""),
                "X-Affected": str(result.get("affected", 0)),
                "X-Backup-Available": str(result.get("backup_path") is not None).lower(),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文档操作失败: {str(e)}")
