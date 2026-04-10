
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src.api.qna_service import answer_question
from src.api.task_manager import task_manager
from src.api.direct_extractor import direct_extract

# 持久化上传文件目录（requests 结束后文件不会丢失）
STORAGE_ROOT = Path("storage/uploads")
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def _cleanup_old_uploads():
    """后台清理线程：删除超过 24 小时的上传目录"""
    while True:
        try:
            cutoff = time.time() - 86400  # 24 小时
            for child in STORAGE_ROOT.iterdir():
                if child.is_dir():
                    try:
                        mtime = child.stat().st_mtime
                        if mtime < cutoff:
                            shutil.rmtree(child, ignore_errors=True)
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(3600)  # 每小时检查一次


_cleanup_thread = threading.Thread(target=_cleanup_old_uploads, daemon=True)
_cleanup_thread.start()


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
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    return {
        'task': info.to_dict(),
        'output_files': task_manager.get_output_files(task_id),
    }


@app.get('/api/tasks/{task_id}/events')
def get_events(
    task_id: str,
    limit: int = 200,
):
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
                payload = {'type': 'status', 'status': current.status, 'output_files': task_manager.get_output_files(task_id)}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            await asyncio.sleep(1.0)

    return StreamingResponse(event_generator(), media_type='text/event-stream')


@app.get('/api/tasks/{task_id}/result')
def get_result(
    task_id: str,
):
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    output_files = task_manager.get_output_files(task_id)
    report_bundle_path = output_files.get('report_bundle')
    report_bundle = None
    if report_bundle_path and os.path.exists(report_bundle_path):
        report_bundle = json.loads(Path(report_bundle_path).read_text(encoding='utf-8'))
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
    output_files = task_manager.get_output_files(task_id)
    path = output_files.get(kind)
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f'找不到输出文件：{kind}')
    return FileResponse(path=path, filename=Path(path).name)


@app.delete('/api/tasks/{task_id}')
def delete_task(
    task_id: str,
):
    info = task_manager.get_task(task_id)
    if not info:
        raise HTTPException(status_code=404, detail='task_id 不存在')
    shutil.rmtree(info.task_dir, ignore_errors=True)
    return JSONResponse({'ok': True, 'deleted_task_id': task_id})


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

    # 为每个请求创建唯一持久化目录
    task_id = uuid.uuid4().hex
    work_dir = STORAGE_ROOT / task_id
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
            result = direct_extract(
                template_path='',       # 无模板
                input_dir=str(input_dir),
                model_type=model_type if model_type.strip() else None,
                instruction=instruction if instruction.strip() else None,
                llm_mode=llm_mode,
                enable_unit_aware=enable_unit_aware,
                total_timeout=total_timeout,
                max_chunks=max_chunks,
                quiet=quiet,
            )
            result['metadata']['template_generated'] = True
            return JSONResponse(result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f'抽取失败: {str(e)}')


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


