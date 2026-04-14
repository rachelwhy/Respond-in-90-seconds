
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src.api.qna_service import answer_question
from src.api.direct_extractor import direct_extract
from src.core.llm_mode import normalize_llm_mode
# 任务系统（/api/tasks/*）默认关闭：生产由后端负责任务/异步
from src.config import (
    ENABLE_TASKS,
    PERSIST_UPLOADS,
    UPLOAD_RETENTION_HOURS,
    TEMP_RETENTION_HOURS,
)
if ENABLE_TASKS:
    from src.api.task_manager import task_manager
# 复杂度评估器已删除，使用简单估算

def _get_storage_root() -> Path:
    root = Path("storage/uploads")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_temp_storage_dir() -> Path:
    d = _get_storage_root() / "temp"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_debug_enabled() -> bool:
    v = os.environ.get("A23_DEBUG", "")
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


def _sanitize_output_files_for_client(output_files: Dict[str, Any]) -> Dict[str, Any]:
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


def _cleanup_old_uploads():
    """后台清理线程：删除过期上传目录与临时文件"""
    while True:
        try:
            # 清理超过保留时长的上传任务目录（排除 temp 目录）
            storage_root = _get_storage_root()
            dir_cutoff = time.time() - max(1, int(UPLOAD_RETENTION_HOURS)) * 3600
            for child in storage_root.iterdir():
                if child.is_dir():
                    if child.name == "temp":
                        continue
                    try:
                        mtime = child.stat().st_mtime
                        if mtime < dir_cutoff:
                            shutil.rmtree(child, ignore_errors=True)
                    except Exception:
                        pass

            # 按临时文件保留时长清理 storage/uploads/temp 下的文件
            temp_dir = _get_temp_storage_dir()
            file_cutoff = time.time() - max(1, int(TEMP_RETENTION_HOURS)) * 3600
            if temp_dir.exists():
                for file_path in temp_dir.iterdir():
                    if file_path.is_file():
                        try:
                            mtime = file_path.stat().st_mtime
                            if mtime < file_cutoff:
                                file_path.unlink(missing_ok=True)
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(3600)  # 每小时检查一次


if PERSIST_UPLOADS:
    _cleanup_thread = threading.Thread(target=_cleanup_old_uploads, daemon=True)
    _cleanup_thread.start()


async def _estimate_document_complexity(files: List[UploadFile], template_file: Optional[UploadFile] = None, task_mode: str = "full") -> Dict[str, Any]:
    """估算文档处理复杂度和处理时间（基于内容分析）

    Args:
        files: 输入文件列表
        template_file: 模板文件（可选）
        task_mode: 处理模式（full/off；supplement 会兼容映射为 full）

    Returns:
        包含估算信息的字典，包含详细的复杂度指标
    """
    import tempfile
    import os
    from pathlib import Path

    task_mode = normalize_llm_mode(task_mode)

    # 保存上传文件到临时目录
    temp_files = []
    document_paths = []
    estimator_mode = os.environ.get("A23_COMPLEXITY_ESTIMATOR", "fast").strip().lower()

    def _fallback_estimate() -> Dict[str, Any]:
        """简单估算逻辑（始终返回 dict）"""
        total_size_bytes = 0
        total_text_estimate = 0  # 仅做粗略“文本量”估计（不是 token）

        for file in files:
            file_size = 0
            if hasattr(file, 'size'):
                file_size = int(file.size or 0)
            else:
                try:
                    current_pos = file.file.tell()
                    file.file.seek(0, 2)
                    file_size = int(file.file.tell() or 0)
                    file.file.seek(current_pos)
                except Exception:
                    file_size = 0

            total_size_bytes += file_size

            filename = file.filename or ''
            if any(filename.lower().endswith(ext) for ext in ['.txt', '.md', '.json', '.csv']):
                total_text_estimate += file_size
            elif any(filename.lower().endswith(ext) for ext in ['.xlsx', '.xls', '.docx', '.doc']):
                total_text_estimate += file_size * 3
            elif filename.lower().endswith('.pdf'):
                total_text_estimate += file_size * 2
            else:
                total_text_estimate += file_size

        if template_file:
            template_size = 0
            if hasattr(template_file, 'size'):
                template_size = int(template_file.size or 0)
            else:
                try:
                    current_pos = template_file.file.tell()
                    template_file.file.seek(0, 2)
                    template_size = int(template_file.file.tell() or 0)
                    template_file.seek(current_pos)
                except Exception:
                    template_size = 0
            total_size_bytes += template_size

        total_size_mb = total_size_bytes / (1024 * 1024)
        estimated_chunks = max(1, total_text_estimate // 3000)
        base_time = 2.0
        per_chunk_time = 3.0
        estimated_processing_time = base_time + (estimated_chunks * per_chunk_time)

        # 在默认估算模式下，仅根据 estimated_chunks 进行 direct/async 分流。
        # 文件大小保留为观测指标，不参与强制分流判定。
        max_chunks_threshold = 30
        recommendation = "async" if estimated_chunks > max_chunks_threshold else "direct"
        if estimated_chunks <= 10:
            complexity_level = "low"
        elif estimated_chunks <= max_chunks_threshold:
            complexity_level = "medium"
        else:
            complexity_level = "high"

        return {
            "total_size_bytes": total_size_bytes,
            "total_size_mb": round(total_size_mb, 2),
            "total_text_length_estimate": total_text_estimate,
            "estimated_chunks": estimated_chunks,
            "estimated_processing_time_seconds": round(estimated_processing_time, 1),
            "complexity_level": complexity_level,
            "recommendation": recommendation,
            "max_chunks_threshold": max_chunks_threshold,
            "max_size_mb_threshold": 5.0,
            "text_complexity_score": 0,
            "structure_complexity_score": 0,
            "extraction_complexity_score": 0,
            "overall_score": 0,
            "estimated_pages": 0,
            "field_count": 0,
            "estimated_output_tokens": 0,
            "exceeds_direct_threshold": estimated_chunks > max_chunks_threshold,
            "exceeds_timeout_threshold": estimated_processing_time > 30,
            "estimator": "fast",
        }

    try:
        # 保存输入文件
        for file in files:
            # 创建临时文件
            suffix = Path(file.filename or "unknown").suffix
            with tempfile.NamedTemporaryFile(mode='wb', suffix=suffix, delete=False) as tmp:
                content = await file.read()
                tmp.write(content)
                tmp_path = tmp.name
                temp_files.append(tmp_path)
                document_paths.append(tmp_path)
                # 重置文件指针，以便后续读取
                await file.seek(0)

        # 保存模板文件（如果有）
        template_path = None
        if template_file:
            suffix = Path(template_file.filename or "template").suffix
            with tempfile.NamedTemporaryFile(mode='wb', suffix=suffix, delete=False) as tmp:
                content = await template_file.read()
                tmp.write(content)
                template_path = tmp.name
                temp_files.append(template_path)
                # 重置文件指针
                await template_file.seek(0)

        # 默认使用快速估算；仅在显式深度模式下执行 Docling 估算。
        logger = logging.getLogger(__name__)
        logger.info("复杂度估算模式: %s", estimator_mode or "fast")

        # fast（默认）直接返回；docling / accurate 才进入深度估算
        if estimator_mode not in ("docling", "accurate", "deep"):
            return _fallback_estimate()

        # 可选深度估算：对可解析格式使用 Docling 实际分块/文本长度（可能较慢）
        try:
            from src.adapters.docling_adapter import DoclingParser, DOCLING_AVAILABLE as _DOCLING_AVAILABLE
        except Exception:
            _DOCLING_AVAILABLE = False
            DoclingParser = None  # type: ignore

        if _DOCLING_AVAILABLE and DoclingParser is not None and document_paths:
            try:
                total_size_bytes = 0
                for tf in temp_files:
                    try:
                        total_size_bytes += int(os.path.getsize(tf) or 0)
                    except Exception:
                        pass
                if template_path:
                    try:
                        total_size_bytes += int(os.path.getsize(template_path) or 0)
                    except Exception:
                        pass
                total_size_mb = total_size_bytes / (1024 * 1024)

                # 控制深度估算范围，避免估算阶段占用过长时间
                docling_paths = []
                for pth in document_paths:
                    suffix = (Path(pth).suffix or "").lower()
                    if suffix in (".docx", ".pdf", ".pptx", ".xlsx", ".xls"):
                        docling_paths.append(pth)
                if total_size_mb > 3.0 or len(docling_paths) > 2:
                    logger.info(
                        "深度估算跳过（size=%.2fMB, files=%s），回退 fast 估算",
                        total_size_mb,
                        len(docling_paths),
                    )
                    return _fallback_estimate()

                parser = DoclingParser(enable_ocr=False)
                total_chars = 0
                total_chunks = 0
                total_tables = 0
                total_pages = 0

                for pth in docling_paths:
                    suffix = (Path(pth).suffix or "").lower()
                    # 仅对支持格式执行 Docling 复杂度估算
                    if suffix not in (".docx", ".pdf", ".pptx", ".xlsx", ".xls"):
                        continue
                    parsed = parser.parse(pth)
                    if parsed.get("error"):
                        continue
                    total_chars += len(parsed.get("text") or "")
                    total_chunks += len(parsed.get("chunks") or [])
                    total_tables += len(parsed.get("tables") or [])
                    total_pages += int(parsed.get("pages") or 0)

                estimated_chunks = max(1, total_chunks) if total_chunks > 0 else max(1, total_chars // 1500)
                # 用“块数 + 模式”估算时间：这里不追求绝对准确，只用于 direct/async 分流
                base_time = 2.0
                per_chunk_time = 3.0 if task_mode != "off" else 0.5
                estimated_processing_time = base_time + (estimated_chunks * per_chunk_time)

                max_chunks_threshold = 30
                max_size_mb_threshold = 5.0
                # 分流简化：深度估算同样只按 chunks 判断 direct/async。
                exceeds_direct = estimated_chunks > max_chunks_threshold

                # recommendation 基于阈值（与 direct 端点逻辑一致）
                recommendation = "async" if exceeds_direct else "direct"

                complexity_level = "low"
                if exceeds_direct:
                    complexity_level = "high"
                elif estimated_chunks > 10:
                    complexity_level = "medium"

                return {
                    "total_size_bytes": total_size_bytes,
                    "total_size_mb": round(total_size_mb, 2),
                    "total_text_length_estimate": total_chars,
                    "estimated_chunks": int(estimated_chunks),
                    "estimated_processing_time_seconds": round(estimated_processing_time, 1),
                    "complexity_level": complexity_level,
                    "recommendation": recommendation,
                    "max_chunks_threshold": max_chunks_threshold,
                    "max_size_mb_threshold": max_size_mb_threshold,
                    "text_complexity_score": 0,
                    "structure_complexity_score": 0,
                    "extraction_complexity_score": 0,
                    "overall_score": 0,
                    "estimated_pages": total_pages,
                    "field_count": 0,
                    "estimated_output_tokens": 0,
                    "tables_count": total_tables,
                    "exceeds_direct_threshold": exceeds_direct,
                    "exceeds_timeout_threshold": estimated_processing_time > 30,
                    "estimator": "docling",
                }
            except Exception:
                # Docling 估算失败则回退启发式
                pass

        return _fallback_estimate()

    except Exception as e:
        # 如果估算失败，记录警告
        logger = logging.getLogger(__name__)
        logger.warning(f"复杂度估算失败: {e}")
        return _fallback_estimate()

    finally:
        # 清理临时文件
        for temp_file in temp_files:
            try:
                if os.path.exists(temp_file):
                    os.unlink(temp_file)
            except:
                pass


def _raise_if_async_recommended(complexity_info: Dict[str, Any]) -> None:
    """复杂度命中 async 时统一抛错。"""
    if complexity_info.get("recommendation") != "async":
        return
    reason = (
        f"超过阈值：estimated_chunks={complexity_info.get('estimated_chunks')}"
        f" (>{complexity_info.get('max_chunks_threshold')})"
    )
    raise HTTPException(
        status_code=400,
        detail={
            "message": "文档过大，建议使用异步任务接口",
            "detail": reason,
            "estimated_chunks": complexity_info.get("estimated_chunks"),
            "estimated_processing_time": f"{complexity_info.get('estimated_processing_time_seconds')}秒",
            "recommendation": "请使用异步任务接口: POST /api/tasks/create",
            "complexity_info": complexity_info,
        },
    )


def _adjust_timeout_by_complexity(total_timeout: int, complexity_info: Dict[str, Any], logger: logging.Logger) -> int:
    """当 total_timeout 为默认值时按复杂度自动调整。"""
    if total_timeout != 110:
        return total_timeout
    estimated_time = float(complexity_info.get("estimated_processing_time_seconds") or 0)
    adjusted_timeout = int(estimated_time * 1.5) + 10
    adjusted_timeout = min(adjusted_timeout, 300)
    adjusted_timeout = max(adjusted_timeout, 30)
    logger.info(f"智能超时调整: {total_timeout} -> {adjusted_timeout}秒 (基于预估{estimated_time}秒)")
    return adjusted_timeout


app = FastAPI(title='A23 AI Demo HTTP API', version='2.0.0')

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)


@app.get('/')
def root():
    return {
        'service': 'a23-ai-demo-http',
        'version': '2.0.0',
        'docs': '/docs',
        'health': '/api/health',
    }


@app.get('/api/health')
def health():
    return {'ok': True, 'service': 'a23-ai-demo-http', 'time': time.time()}


# 鉴权端点已移除 - 根据后端要求，AI端不需要鉴权


# ============================================================================
# Task management endpoints (protected)
# ============================================================================

@app.get('/api/tasks')
def list_tasks():
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    return {'tasks': task_manager.list_tasks()}


@app.post('/api/tasks/create')
async def create_task(
    template: UploadFile = File(None),  # 改为可选，支持llm模式
    input_files: List[UploadFile] = File(...),
    note: str = Form(default=''),
    model_type: str = Form(default=''),
    template_mode: str = Form(default='auto'),
    template_description: str = Form(default=''),
    llm_mode: str = Form(default='full'),
    total_timeout: int = Form(default=110),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    if not input_files:
        raise HTTPException(status_code=400, detail='至少需要上传一个输入文件')

    # 验证模板模式
    if template_mode not in ['file', 'llm', 'auto']:
        raise HTTPException(status_code=400, detail='template_mode必须是file、llm或auto')

    # 根据模板模式处理
    template_path = None
    template_name = None

    if template_mode in ['file', 'auto']:
        if not template:
            if template_mode == 'file':
                raise HTTPException(status_code=400, detail='file模式需要上传模板文件')
            # auto模式没有模板文件，检查是否有描述
            elif not template_description:
                # auto模式既无模板也无描述，使用默认
                template_name = 'default_template'
        else:
            # 有模板文件
            template_name = template.filename or 'template.bin'

    elif template_mode == 'llm':
        if not template_description:
            raise HTTPException(status_code=400, detail='llm模式需要提供template_description')
        template_name = 'llm_generated'

    info = task_manager.create_task_workspace(
        template_name=template_name or 'template.bin',
        input_files=[f.filename or 'unknown' for f in input_files],
    )
    template_dir = info.task_dir / 'uploads' / 'template'
    input_dir = info.task_dir / 'uploads' / 'input'

    # 保存模板文件（如果有）
    if template and template_name:
        template_path = template_dir / template_name
        with template_path.open('wb') as f:
            shutil.copyfileobj(template.file, f)

    saved_inputs = []
    for up in input_files:
        name = up.filename or f'input_{len(saved_inputs)+1}.bin'
        p = input_dir / name
        with p.open('wb') as f:
            shutil.copyfileobj(up.file, f)
        saved_inputs.append(name)

    task_manager.update_status(info.task_id, 'queued')
    llm_mode = normalize_llm_mode(llm_mode)

    meta_extra = {
        'note': note,
        'saved_inputs': saved_inputs,
        'model_type': model_type,
        'template_mode': template_mode,
        'template_description': template_description,
        'llm_mode': llm_mode,
        'total_timeout': total_timeout,
        'max_chunks': max_chunks,
        'quiet': quiet
    }
    (info.task_dir / 'request_meta.json').write_text(json.dumps(meta_extra, ensure_ascii=False, indent=2), encoding='utf-8')
    task_manager.start_task(info.task_id, template_path=template_path, input_dir=input_dir)

    return {
        'task_id': info.task_id,
        'status': 'queued',
        'template_name': template.filename if template else template_name,
        'input_files': saved_inputs,
        'template_mode': template_mode,
        'status_url': f'/api/tasks/{info.task_id}',
        'events_url': f'/api/tasks/{info.task_id}/events',
        'stream_url': f'/api/tasks/{info.task_id}/stream',
        'result_url': f'/api/tasks/{info.task_id}/result',
    }


@app.get('/api/tasks/{task_id}')
def get_task(
    task_id: str,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    output_files = _sanitize_output_files_for_client(task_manager.get_output_files(task_id))
    return {
        'task': info.to_dict(),
        'output_files': output_files,
    }


@app.get('/api/tasks/{task_id}/events')
def get_events(
    task_id: str,
    limit: int = 200,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    return {
        'task_id': task_id,
        'status': info.status,
        'lines': task_manager.read_log(task_id, limit=limit),
    }


@app.get('/api/tasks/{task_id}/log')
def get_task_log(
    task_id: str,
    tail: int = 100,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    """获取任务专属日志，支持 ?tail=N 参数返回最后 N 行"""
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    lines = task_manager.read_log(task_id, limit=tail)
    return {
        'task_id': task_id,
        'status': info.status,
        'tail': tail,
        'line_count': len(lines),
        'lines': lines,
    }


@app.get('/api/tasks/{task_id}/stream')
async def stream_events(
    task_id: str,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')

    async def event_generator():
        sent = 0
        while True:
            current = task_manager.get_task(task_id)
            lines = task_manager.read_log(task_id, limit=10000)
            new_lines = lines[sent:]
            for line in new_lines:
                payload = {'type': 'log', 'message': line}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            sent = len(lines)
            if current and current.status in {'succeeded', 'failed'}:
                payload = {
                    'type': 'status',
                    'status': current.status,
                    'output_files': _sanitize_output_files_for_client(task_manager.get_output_files(task_id)),
                }
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get('/api/tasks/{task_id}/result')
def get_result(
    task_id: str,
    include_report: bool = False,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    output_files = task_manager.get_output_files(task_id)
    report_bundle_path = output_files.get('report_bundle')
    report_bundle = None
    if include_report and _is_debug_enabled() and report_bundle_path and os.path.exists(report_bundle_path):
        report_bundle = json.loads(Path(report_bundle_path).read_text(encoding='utf-8'))
    output_files = _sanitize_output_files_for_client(output_files)
    return {
        'task_id': task_id,
        'status': info.status,
        'output_files': output_files,
        'report_bundle': report_bundle,
    }


@app.get('/api/tasks/{task_id}/download/{kind}')
def download_result(
    task_id: str,
    kind: str,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    if kind == "report_bundle" and not _is_debug_enabled():
        raise HTTPException(status_code=404, detail='调试产物已禁用下载')
    output_files = task_manager.get_output_files(task_id)
    path = output_files.get(kind)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f'找不到输出文件：{kind}')
    return FileResponse(path=path, filename=Path(path).name)


@app.delete('/api/tasks/{task_id}')
def delete_task(
    task_id: str,
):
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    shutil.rmtree(info.task_dir, ignore_errors=True)
    return JSONResponse({'ok': True, 'deleted_task_id': task_id})


@app.post('/api/tasks/{task_id}/export-complete')
def acknowledge_task_export(
    task_id: str,
    cleanup: bool = True,
):
    """后端确认任务结果已导出；可选立即清理任务目录。"""
    if not ENABLE_TASKS:
        raise HTTPException(status_code=404, detail="tasks接口已禁用（A23_ENABLE_TASKS=false）")
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')

    if cleanup:
        task_manager.delete_task(task_id)
        return JSONResponse({'ok': True, 'task_id': task_id, 'cleaned': True})

    return JSONResponse({'ok': True, 'task_id': task_id, 'cleaned': False})


@app.post('/api/qna/ask')
async def qna_ask(
    question: str = Form(...),
    files: List[UploadFile] = File(...),
    session_id: Optional[str] = Form(default=None),
    top_k: int = Form(default=5),
):
    if not files:
        raise HTTPException(status_code=400, detail='QnA 至少需要上传一个文件')
    payload_files = []
    for f in files:
        payload_files.append((f.filename or 'unknown.txt', await f.read()))
    result = answer_question(question=question, files=payload_files, session_id=session_id, top_k=top_k)
    return result


# ============================================================================
# Model management endpoints (支持网页端动态切换模型)
# ============================================================================

@app.get('/api/models')
def get_available_models():
    """获取可用的模型列表和当前配置"""
    from src.config import (
        MODEL_TYPE, OLLAMA_URL, OLLAMA_MODEL, OPENAI_BASE_URL, OPENAI_MODEL,
        DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_API_KEY
    )

    available_models = [
        {
            "type": "ollama",
            "display_name": "Ollama (本地)",
            "url": OLLAMA_URL,
            "model": OLLAMA_MODEL,
            "is_available": True,  # 默认认为可用，实际可用性需要测试
        },
        {
            "type": "openai",
            "display_name": "OpenAI兼容API",
            "url": OPENAI_BASE_URL,
            "model": OPENAI_MODEL,
            "is_available": True,
        },
        {
            "type": "qwen",
            "display_name": "Qwen (兼容OpenAI)",
            "url": OPENAI_BASE_URL,  # 通常使用相同的API
            "model": OPENAI_MODEL,
            "is_available": True,
        },
        {
            "type": "deepseek",
            "display_name": "DeepSeek API",
            "url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "is_available": bool(DEEPSEEK_API_KEY),  # 有API密钥才认为可用
        }
    ]

    return {
        "current_model_type": MODEL_TYPE,
        "available_models": available_models,
        "config_source": "environment_variables",
    }


@app.post('/api/models/test-connection')
async def test_model_connection(
    model_type: str = Form(...),
    url: Optional[str] = Form(default=None),
    api_key: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
):
    """测试指定模型的连接性"""
    import requests

    test_config = {}
    if url:
        test_config["url"] = url
    if api_key:
        test_config["api_key"] = api_key
    if model:
        test_config["model"] = model

    try:
        # 根据模型类型进行连接测试
        if model_type == "ollama":
            test_url = url or "http://127.0.0.1:11434/api/generate"
            payload = {
                "model": model or "qwen2.5:7b",
                "prompt": "test",
                "stream": False
            }
            resp = requests.post(test_url, json=payload, timeout=10)
            resp.raise_for_status()
            return {"success": True, "message": "Ollama连接成功"}

        elif model_type in ["openai", "qwen"]:
            test_url = (url or "http://localhost:8000/v1") + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model or "Qwen/Qwen2.5-7B-Instruct",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10
            }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            return {"success": True, "message": "OpenAI兼容API连接成功"}

        elif model_type == "deepseek":
            test_url = (url or "https://api.deepseek.com") + "/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key or 'test'}"
            }
            payload = {
                "model": model or "deepseek-chat",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10
            }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=10)
            # DeepSeek会在缺少有效API密钥时返回401
            if resp.status_code == 401:
                return {"success": False, "message": "API密钥无效或缺失"}
            resp.raise_for_status()
            return {"success": True, "message": "DeepSeek API连接成功"}

        else:
            return {"success": False, "message": f"不支持的模型类型: {model_type}"}

    except Exception as e:
        return {"success": False, "message": f"连接测试失败: {str(e)}"}


@app.get('/api/config/runtime')
def get_runtime_config():
    """获取当前运行时配置（从 src/config.py 读取）"""
    from src.config import (
        MODEL_TYPE, OLLAMA_MODEL, OPENAI_MODEL, DEEPSEEK_MODEL,
        TEMPERATURE, MAX_TOKENS, EXTRACTION_TIMEOUT, MAX_RETRIES,
    )
    return {
        "success": True,
        "config": {
            "model_type": MODEL_TYPE,
            "ollama_model": OLLAMA_MODEL,
            "openai_model": OPENAI_MODEL,
            "deepseek_model": DEEPSEEK_MODEL,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "extraction_timeout": EXTRACTION_TIMEOUT,
            "max_retries": MAX_RETRIES,
        },
    }


@app.post('/api/config/runtime')
def update_runtime_config(
    config_updates: str = Form(...),
):
    """运行时配置更新（仅返回确认，实际变量由环境变量控制）"""
    try:
        updates = json.loads(config_updates)
        return {"success": True, "config": updates, "message": "配置已接收（重启生效）"}
    except json.JSONDecodeError:
        return {"success": False, "message": "配置数据必须是有效的JSON"}


@app.post('/api/models/switch')
def switch_model(
    model_type: str = Form(...),
    url: Optional[str] = Form(default=None),
    base_url: Optional[str] = Form(default=None),
    api_key: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    temperature: Optional[float] = Form(default=None),
    max_tokens: Optional[int] = Form(default=None),
):
    """记录模型切换请求（实际切换通过环境变量实现，重启生效）"""
    return {
        "success": True,
        "message": f"模型切换请求已记录（model_type={model_type}），请通过环境变量 A23_MODEL_TYPE 生效",
        "requested": {"model_type": model_type, "model": model, "url": url or base_url},
    }


@app.post('/api/extract/pre-analyze')
async def pre_analyze_documents(
    template: UploadFile = File(None),
    input_files: List[UploadFile] = File(...),
    task_mode: str = Form("full"),
):
    """预分析文档复杂度，预估处理时间和建议

    在正式处理前，用户可上传文档进行复杂度分析，获取：
    1. 预估处理时间和资源需求
    2. 处理建议（直接处理 vs 异步任务）
    3. 推荐配置参数（超时时间、分块数等）

    Args:
        template: 模板文件（可选）
        input_files: 输入文件列表

    Returns:
        包含预分析结果的字典
    """
    if not input_files:
        raise HTTPException(status_code=400, detail='至少需要上传一个输入文件')

    # 估算文档复杂度
    complexity_info = _estimate_document_complexity(input_files, template, task_mode)

    # 添加详细建议
    recommendation = complexity_info["recommendation"]
    if recommendation == "direct":
        complexity_info["suggestion"] = {
            "use_endpoint": "POST /api/extract/direct",
            "recommended_timeout": int(complexity_info["estimated_processing_time_seconds"] * 1.5 + 10),
            "recommended_max_chunks": min(complexity_info["estimated_chunks"] + 5, 50),
            "reason": f"文档大小适中 ({complexity_info['total_size_mb']}MB)，预估处理时间{complexity_info['estimated_processing_time_seconds']}秒"
        }
    else:
        complexity_info["suggestion"] = {
            "use_endpoint": "POST /api/tasks/create",
            "recommended_timeout": 300,  # 异步任务默认5分钟
            "reason": f"文档较大 ({complexity_info['total_size_mb']}MB)，预估处理时间{complexity_info['estimated_processing_time_seconds']}秒，建议使用异步任务避免超时"
        }

    # 添加处理流程图
    complexity_info["processing_flow"] = {
        "step1": "文档解析 → 语义分块",
        "step2": f"模型抽取 (预估 {complexity_info['estimated_chunks']} 个分块)",
        "step3": "后处理与字段归一化",
        "step4": "结果合并与输出",
        "total_steps": 4
    }

    # 添加分层超时建议
    complexity_info["timeout_layers"] = {
        "document_parsing": max(10, complexity_info["estimated_processing_time_seconds"] * 0.1),
        "model_extraction": max(20, complexity_info["estimated_processing_time_seconds"] * 0.6),
        "post_processing": max(5, complexity_info["estimated_processing_time_seconds"] * 0.1),
        "total_timeout": int(complexity_info["estimated_processing_time_seconds"] * 1.5 + 10)
    }

    return JSONResponse(complexity_info)


@app.post('/api/extract/direct')
async def extract_direct(
    template: UploadFile = File(...),
    input_files: List[UploadFile] = File(...),
    model_type: str = Form(default=''),
    instruction: str = Form(default=''),
    llm_mode: str = Form(default='full'),
    enable_unit_aware: bool = Form(default=True),
    total_timeout: int = Form(default=110),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    """直接抽取API端点，无需创建任务，直接返回抽取结果。文件保存到持久化目录。"""
    if not input_files:
        raise HTTPException(status_code=400, detail='至少需要上传一个输入文件')

    llm_mode = normalize_llm_mode(llm_mode)

    # 1. 智能路由：估算文档复杂度
    complexity_info = await _estimate_document_complexity(input_files, template, llm_mode)

    # 记录复杂度信息
    logger = logging.getLogger(__name__)
    logger.info(f"文档复杂度分析: {complexity_info}")

    # 超过 chunks 阈值时建议走异步接口；否则按复杂度自动调整超时
    _raise_if_async_recommended(complexity_info)
    total_timeout = _adjust_timeout_by_complexity(total_timeout, complexity_info, logger)

    import tempfile
    # 为每个请求创建唯一工作目录（可选持久化）
    task_id = uuid.uuid4().hex
    storage_root = _get_storage_root() if PERSIST_UPLOADS else None
    work_dir = (storage_root / task_id) if storage_root else Path(tempfile.mkdtemp(prefix="a23_direct_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    # 保存模板文件
    template_path = work_dir / (template.filename or 'template.bin')
    with template_path.open('wb') as f:
        shutil.copyfileobj(template.file, f)

    # 保存输入文件
    input_dir = work_dir / 'inputs'
    input_dir.mkdir(parents=True, exist_ok=True)
    for i, up in enumerate(input_files):
        name = up.filename or f'input_{i}.bin'
        p = input_dir / name
        with p.open('wb') as f:
            shutil.copyfileobj(up.file, f)

    # 创建输出目录
    output_dir = work_dir / 'output'
    output_dir.mkdir(parents=True, exist_ok=True)

    # 调用直接抽取函数
    try:
        result = direct_extract(
            template_path=str(template_path),
            input_dir=str(input_dir),
            model_type=model_type if model_type.strip() else None,
            instruction=instruction if instruction.strip() else None,
            llm_mode=llm_mode,
            enable_unit_aware=enable_unit_aware,
            work_dir=work_dir,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )
        result["task_id"] = task_id
        result["output_dir"] = str(output_dir)

        # 添加智能路由和复杂度信息
        result["routing_info"] = {
            "complexity_analysis": complexity_info,
            "actual_timeout_used": total_timeout,
            "timeout_adjusted": total_timeout != 110,  # 是否调整了超时
            "routing_decision": "direct",  # 已通过检查，使用直接处理
            "suggestion": "文档大小适中，使用直接处理接口",
            "estimated_vs_actual": {
                "estimated_time": complexity_info["estimated_processing_time_seconds"],
                "actual_time": None,  # 可在后续添加实际处理时间
            }
        }

        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'抽取失败: {str(e)}')


@app.post('/api/extract/no-template')
async def extract_without_template(
    input_files: List[UploadFile] = File(...),
    instruction: str = Form(default=''),
    model_type: str = Form(default=''),
    llm_mode: str = Form(default='full'),
    enable_unit_aware: bool = Form(default=True),
    total_timeout: int = Form(default=110),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    """无模板抽取 — 自动分析文档结构并提取

    三种自动模式：
    - 有 instruction → 按指令生成 profile 并提取
    - 无 instruction 但文档可结构化 → AI 自动分析最优字段结构
    - 文档杂乱无结构 → 提取摘要信息用于 QA 入库
    """
    import tempfile
    import shutil

    if not input_files:
        raise HTTPException(status_code=400, detail='至少需要上传一个输入文件')

    llm_mode = normalize_llm_mode(llm_mode)

    # 1. 智能路由：估算文档复杂度
    # 无模板文件，只检查输入文件
    complexity_info = await _estimate_document_complexity(input_files, None, llm_mode)

    # 记录复杂度信息
    logger = logging.getLogger(__name__)
    logger.info(f"无模板抽取 - 文档复杂度分析: {complexity_info}")

    # 超过 chunks 阈值时建议走异步接口；否则按复杂度自动调整超时
    _raise_if_async_recommended(complexity_info)
    total_timeout = _adjust_timeout_by_complexity(total_timeout, complexity_info, logger)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        input_dir = tmp_path / 'inputs'
        input_dir.mkdir(parents=True, exist_ok=True)
        for up in input_files:
            name = up.filename or f'input_{len(input_files)}.bin'
            p = input_dir / name
            with p.open('wb') as f:
                shutil.copyfileobj(up.file, f)

        try:
            from src.api.direct_extractor import direct_extract
            # instruction 为空时，direct_extract 会自动分析文档内容生成 profile
            # 创建工作目录用于生成输出文件
            work_dir = tmp_path / 'output'
            work_dir.mkdir(parents=True, exist_ok=True)
            result = direct_extract(
                template_path='',       # 无模板
                input_dir=str(input_dir),
                model_type=model_type if model_type.strip() else None,
                instruction=instruction if instruction.strip() else None,
                llm_mode=llm_mode,
                enable_unit_aware=enable_unit_aware,
                work_dir=work_dir,      # 传递工作目录
                total_timeout=total_timeout,
                max_chunks=max_chunks,
                quiet=quiet,
            )
            result['metadata']['template_generated'] = True

            # 添加智能路由和复杂度信息
            result["routing_info"] = {
                "complexity_analysis": complexity_info,
                "actual_timeout_used": total_timeout,
                "timeout_adjusted": total_timeout != 110,  # 是否调整了超时
                "routing_decision": "direct",  # 已通过检查，使用直接处理
                "suggestion": "文档大小适中，使用直接处理接口",
                "estimated_vs_actual": {
                    "estimated_time": complexity_info["estimated_processing_time_seconds"],
                    "actual_time": None,  # 可在后续添加实际处理时间
                }
            }

            # 只要 direct_extract 产生输出文件，即持久化并返回下载地址。
            # 持久化判断不依赖 doc_type，确保后端可稳定获取文件。
            output_file = result.get('output_file')
            if output_file:
                output_path = Path(output_file)
                if output_path.exists() and output_path.is_file():
                    logger = logging.getLogger(__name__)
                    try:
                        file_ext = output_path.suffix.lower()
                        safe_filename = f"{uuid.uuid4().hex}{file_ext}"
                        temp_storage_dir = _get_temp_storage_dir()
                        target_path = temp_storage_dir / safe_filename
                        shutil.move(str(output_path), str(target_path))

                        result['download_url'] = f'/api/download/temp/{safe_filename}'
                        result['output_file'] = str(target_path)
                        result.setdefault('metadata', {})
                        result['metadata']['persisted_output'] = True
                        logger.info("无模板输出已持久化: %s", safe_filename)
                    except Exception as e:
                        logger.warning("无模板输出持久化失败: %s", e)

            # 始终返回JSON响应
            return JSONResponse(result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'抽取失败: {str(e)}')


@app.get('/api/download/temp/{filename}')
def download_temp_file(filename: str):
    """下载临时生成的文件

    用于 /api/extract/no-template 接口生成的结构化文档文件下载。
    文件保存在 storage/uploads/temp/ 目录中，超过1小时自动清理。
    """
    # 安全性检查：防止路径遍历攻击
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail='无效的文件名')

    file_path = _get_temp_storage_dir() / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail='文件不存在或已过期')

    # 根据文件扩展名设置Content-Type
    content_type = 'application/octet-stream'
    if filename.lower().endswith('.xlsx'):
        content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    elif filename.lower().endswith('.docx'):
        content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    elif filename.lower().endswith('.doc'):
        content_type = 'application/msword'

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type=content_type
    )


@app.post('/api/download/temp/{filename}/export-complete')
def acknowledge_temp_export(filename: str):
    """后端确认临时导出文件已接收，触发立即删除。"""
    if '..' in filename or '/' in filename or '\\' in filename:
        raise HTTPException(status_code=400, detail='无效的文件名')

    file_path = _get_temp_storage_dir() / filename
    if not file_path.exists():
        return JSONResponse({'ok': True, 'filename': filename, 'deleted': False})

    try:
        file_path.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'清理失败: {e}')
    return JSONResponse({'ok': True, 'filename': filename, 'deleted': True})


# ─────────────────────────────────────────────
# M1：文档智能操作接口
# ─────────────────────────────────────────────

@app.post('/api/document/operate')
async def operate_document_endpoint(
    file: UploadFile = File(...),
    instruction: str = Form(...),
    backup: bool = Form(default=True),
):
    """文档智能操作接口（M1）

    将自然语言指令转化为文档编辑操作（格式调整、内容编辑、行筛选、数据提取等）。

    支持指令示例：
      - "将第三列加粗"
      - "将所有数字居中对齐"
      - "删除金额小于1000的行"
      - "提取城市为北京的所有记录"
      - "将标题字体改为16号"
      - "把'旧内容'替换为'新内容'"

    Form-data:
      file        : file    待操作的 Excel 或 Word 文件
      instruction : string  自然语言操作指令
      backup      : bool    是否保存备份（默认 true）

    Response:
      {
        "status": "ok" | "error",
        "operation": "操作类型",
        "affected": 12,
        "output_file": "/api/document/operate/result/<task_id>",
        "records": [...],      // extract_data 时返回
        "backup_available": true,
        "command": {...}       // 解析后的结构化指令
      }
    """
    import tempfile
    work_dir = Path(tempfile.mkdtemp(prefix='a23_operate_'))

    try:
        # 保存上传文件
        doc_path = work_dir / file.filename
        doc_path.write_bytes(await file.read())

        suffix = doc_path.suffix.lower()
        original_stem = Path(file.filename).stem
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

        # extract_data 直接返回数据，无需文件下载
        if result.get("operation") == "extract_data" or result.get("records") is not None:
            return JSONResponse({
                "status": "ok",
                "operation": "extract_data",
                "records": result.get("records", []),
                "count": result.get("count", len(result.get("records", []))),
                "command": result.get("command", {}),
            })

        # 其他操作返回修改后的文件
        if not out_path.exists():
            raise HTTPException(status_code=500, detail="操作未生成输出文件")

        from fastapi.responses import FileResponse
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


# ─────────────────────────────────────────────
# 数据入库接口（对接后端 MySQL）
# ─────────────────────────────────────────────

@app.post('/api/ingest')
async def ingest_files(
    files: List[UploadFile] = File(...),
    task_id: Optional[str] = Form(default=None),
    template_name: str = Form(default=''),
):
    """直接上传文件并入库（无需先建任务）。

    - 易于结构化的文件（xlsx/csv/有表格的PDF）→ a23_structured_records
    - 纯文本/Markdown/扫描件等 → a23_raw_documents（暂存）

    Returns:
        {
          "task_id": str,
          "total_files": int,
          "structured_count": int,
          "unstructured_count": int,
          "total_rows": int,
          "errors": [...],
          "details": [...]
        }
    """
    import tempfile
    import uuid as _uuid

    if not files:
        raise HTTPException(status_code=400, detail='至少需要上传一个文件')

    tid = task_id or _uuid.uuid4().hex[:12]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        for up in files:
            name = up.filename or 'unknown.bin'
            (tmp_path / name).write_bytes(await up.read())

        from src.core.reader import collect_input_bundle
        from src.core.db_ingest import ingest_bundle

        bundle = collect_input_bundle(str(tmp_path))
        result = ingest_bundle(task_id=tid, bundle=bundle, template_name=template_name)

    return result


@app.post('/api/tasks/{task_id}/ingest')
def ingest_task_result(
    task_id: str,
    template_name: str = Form(default=''),
):
    """将已完成任务的抽取结果推送入库。

    任务须处于 succeeded 状态，result_url 中须有 report_bundle。
    """
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    if info.status != 'succeeded':
        raise HTTPException(status_code=400, detail=f'任务尚未完成，当前状态: {info.status}')

    output_files = task_manager.get_output_files(task_id)
    report_bundle_path = output_files.get('report_bundle')
    extraction_result = None
    if report_bundle_path and os.path.exists(report_bundle_path):
        try:
            bundle_data = json.loads(Path(report_bundle_path).read_text(encoding='utf-8'))
            # report_bundle 里的 debug_result 含原始 records
            extraction_result = bundle_data.get('debug_result') or {}
        except Exception:
            pass

    input_dir = info.task_dir / 'uploads' / 'input'
    if not input_dir.exists():
        raise HTTPException(status_code=404, detail='任务输入目录不存在')

    from src.core.reader import collect_input_bundle
    from src.core.db_ingest import ingest_bundle

    bundle = collect_input_bundle(str(input_dir))
    result = ingest_bundle(
        task_id=task_id,
        bundle=bundle,
        extraction_result=extraction_result,
        template_name=template_name or (info.template_name if hasattr(info, 'template_name') else ''),
    )
    return result


@app.get('/api/ingest/{task_id}/records')
def get_ingest_records(task_id: str, limit: int = 200):
    """查询某 task_id 已入库的结构化记录"""
    from src.adapters.mysql_adapter import get_mysql_adapter
    adapter = get_mysql_adapter()
    if not adapter.is_available():
        raise HTTPException(status_code=503, detail='MySQL 不可用')
    return {
        'task_id': task_id,
        'structured': adapter.query_structured(task_id, limit=limit),
        'raw': adapter.query_raw(task_id, limit=min(limit, 50)),
    }




@app.get('/api/db/health')
def db_health():
    """检查 MySQL 连接状态"""
    from src.adapters.mysql_adapter import get_mysql_adapter
    from src.config import MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE
    adapter = get_mysql_adapter()
    ok = adapter.is_available()
    return {
        'mysql_available': ok,
        'host': MYSQL_HOST,
        'port': MYSQL_PORT,
        'database': MYSQL_DATABASE,
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
