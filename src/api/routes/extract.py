"""HTTP：同步抽取与复杂度预估（multipart 上传模板与输入）。"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.api.complexity_estimator import estimate_document_complexity
from src.api.direct_extractor import direct_extract
from src.api.storage_utils import get_storage_root, get_temp_storage_dir, safe_upload_name
from src.config import EXTRACTION_TIMEOUT, PERSIST_UPLOADS
from src.core.llm_mode import normalize_llm_mode


router = APIRouter()


async def _merge_instruction_form_and_file(
    instruction: str,
    instruction_file: Optional[UploadFile],
    logger: logging.Logger,
) -> Optional[str]:
    """合并表单 ``instruction`` 与可选上传的指令文件正文（UTF-8，非法字节替换）。"""
    eff = str(instruction or "").strip()
    if instruction_file is None:
        return eff or None
    fn = str(getattr(instruction_file, "filename", "") or "").strip()
    if not fn:
        return eff or None
    try:
        raw = await instruction_file.read()
        extra = raw.decode("utf-8", errors="replace").strip()
    except Exception as e:
        logger.warning("读取 instruction_file 失败：%s", e)
        return eff or None
    if not extra:
        return eff or None
    if eff:
        return f"{eff}\n\n{extra}".strip()
    return extra


def _adjust_timeout_by_complexity(total_timeout: int, complexity_info: Dict[str, Any], logger: logging.Logger) -> int:
    """当 total_timeout 为默认值时按复杂度自动调整。"""
    if total_timeout != EXTRACTION_TIMEOUT:
        return total_timeout
    estimated_time = float(complexity_info.get("estimated_processing_time_seconds") or 0)
    adjusted_timeout = int(estimated_time * 1.5) + 10
    adjusted_timeout = min(adjusted_timeout, 300)
    adjusted_timeout = max(adjusted_timeout, 30)
    logger.info("智能超时调整: %s -> %s秒 (基于预估%s秒)", total_timeout, adjusted_timeout, estimated_time)
    return adjusted_timeout


@router.post("/api/extract/pre-analyze")
async def pre_analyze_documents(
    template: UploadFile = File(None),
    input_files: List[UploadFile] = File(...),
    task_mode: str = Form("full"),
):
    if not input_files:
        raise HTTPException(status_code=400, detail="至少需要上传一个输入文件")

    complexity_info = await estimate_document_complexity(input_files, template, task_mode)
    complexity_info["suggestion"] = {
        "use_endpoint": "POST /api/extract/direct",
        "recommended_timeout": int(complexity_info["estimated_processing_time_seconds"] * 1.5 + 10),
        "recommended_max_chunks": min(complexity_info["estimated_chunks"] + 5, 50),
        "reason": (
            f"预估约 {complexity_info['estimated_chunks']} 个语义分块，"
            f"处理时间约 {complexity_info['estimated_processing_time_seconds']} 秒（仅供参考，可调 total_timeout）"
        ),
    }
    complexity_info["processing_flow"] = {
        "step1": "文档解析 -> 语义分块",
        "step2": f"模型抽取 (预估 {complexity_info['estimated_chunks']} 个分块)",
        "step3": "后处理与字段归一化",
        "step4": "结果合并与输出",
        "total_steps": 4,
    }
    complexity_info["timeout_layers"] = {
        "document_parsing": max(10, complexity_info["estimated_processing_time_seconds"] * 0.1),
        "model_extraction": max(20, complexity_info["estimated_processing_time_seconds"] * 0.6),
        "post_processing": max(5, complexity_info["estimated_processing_time_seconds"] * 0.1),
        "total_timeout": int(complexity_info["estimated_processing_time_seconds"] * 1.5 + 10),
    }
    return JSONResponse(complexity_info)


@router.post("/api/extract/direct")
async def extract_direct_endpoint(
    template: UploadFile = File(...),
    input_files: List[UploadFile] = File(...),
    model_type: str = Form(default=""),
    instruction: str = Form(default=""),
    instruction_file: Optional[UploadFile] = File(None),
    llm_mode: str = Form(default="full"),
    enable_unit_aware: bool = Form(default=True),
    total_timeout: int = Form(default=EXTRACTION_TIMEOUT),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    if not input_files:
        raise HTTPException(status_code=400, detail="至少需要上传一个输入文件")

    llm_mode = normalize_llm_mode(llm_mode)
    complexity_info = await estimate_document_complexity(input_files, template, llm_mode)
    logger = logging.getLogger(__name__)
    logger.info("文档复杂度分析: %s", complexity_info)
    timeout_before_adjust = total_timeout
    total_timeout = _adjust_timeout_by_complexity(total_timeout, complexity_info, logger)
    timeout_adjusted = total_timeout != timeout_before_adjust

    task_id = uuid.uuid4().hex
    storage_root = get_storage_root() if PERSIST_UPLOADS else None
    work_dir = (storage_root / task_id) if storage_root else Path(tempfile.mkdtemp(prefix="a23_direct_"))
    work_dir.mkdir(parents=True, exist_ok=True)

    template_name = safe_upload_name(template.filename, "template.bin")
    template_path = work_dir / template_name
    with template_path.open("wb") as f:
        shutil.copyfileobj(template.file, f)

    input_dir = work_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    for i, up in enumerate(input_files):
        name = safe_upload_name(up.filename, f"input_{i}.bin")
        p = input_dir / name
        with p.open("wb") as f:
            shutil.copyfileobj(up.file, f)

    output_dir = work_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        merged_instruction = await _merge_instruction_form_and_file(instruction, instruction_file, logger)
        result = direct_extract(
            template_path=str(template_path),
            input_dir=str(input_dir),
            model_type=model_type if model_type.strip() else None,
            instruction=merged_instruction,
            llm_mode=llm_mode,
            enable_unit_aware=enable_unit_aware,
            work_dir=work_dir,
            total_timeout=total_timeout,
            max_chunks=max_chunks,
            quiet=quiet,
        )
        result["task_id"] = task_id
        result["output_dir"] = str(output_dir)
        result["routing_info"] = {
            "complexity_analysis": complexity_info,
            "actual_timeout_used": total_timeout,
            "timeout_adjusted": timeout_adjusted,
            "routing_decision": "direct",
            "suggestion": "同步抽取（total_timeout 已按复杂度粗调，上游可自行排队）",
            "estimated_vs_actual": {
                "estimated_time": complexity_info["estimated_processing_time_seconds"],
                "actual_time": None,
            },
        }
        return JSONResponse(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"抽取失败: {str(e)}")


@router.post("/api/extract/no-template")
async def extract_without_template(
    input_files: List[UploadFile] = File(...),
    instruction: str = Form(default=""),
    instruction_file: Optional[UploadFile] = File(None),
    model_type: str = Form(default=""),
    llm_mode: str = Form(default="full"),
    enable_unit_aware: bool = Form(default=True),
    total_timeout: int = Form(default=EXTRACTION_TIMEOUT),
    max_chunks: int = Form(default=50),
    quiet: bool = Form(default=False),
):
    if not input_files:
        raise HTTPException(status_code=400, detail="至少需要上传一个输入文件")

    llm_mode = normalize_llm_mode(llm_mode)
    complexity_info = await estimate_document_complexity(input_files, None, llm_mode)
    logger = logging.getLogger(__name__)
    logger.info("无模板抽取 - 文档复杂度分析: %s", complexity_info)
    timeout_before_adjust = total_timeout
    total_timeout = _adjust_timeout_by_complexity(total_timeout, complexity_info, logger)
    timeout_adjusted = total_timeout != timeout_before_adjust

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        input_dir = tmp_path / "inputs"
        input_dir.mkdir(parents=True, exist_ok=True)
        for i, up in enumerate(input_files):
            name = safe_upload_name(up.filename, f"input_{i}.bin")
            p = input_dir / name
            with p.open("wb") as f:
                shutil.copyfileobj(up.file, f)

        try:
            work_dir = tmp_path / "output"
            work_dir.mkdir(parents=True, exist_ok=True)
            merged_instruction = await _merge_instruction_form_and_file(instruction, instruction_file, logger)
            result = direct_extract(
                template_path="",
                input_dir=str(input_dir),
                model_type=model_type if model_type.strip() else None,
                instruction=merged_instruction,
                llm_mode=llm_mode,
                enable_unit_aware=enable_unit_aware,
                work_dir=work_dir,
                total_timeout=total_timeout,
                max_chunks=max_chunks,
                quiet=quiet,
            )
            result["metadata"]["template_generated"] = True
            result["routing_info"] = {
                "complexity_analysis": complexity_info,
                "actual_timeout_used": total_timeout,
                "timeout_adjusted": timeout_adjusted,
                "routing_decision": "direct",
                "suggestion": "同步抽取（total_timeout 已按复杂度粗调，上游可自行排队）",
                "estimated_vs_actual": {
                    "estimated_time": complexity_info["estimated_processing_time_seconds"],
                    "actual_time": None,
                },
            }

            output_file = result.get("output_file")
            if output_file:
                output_path = Path(output_file)
                if output_path.exists() and output_path.is_file():
                    try:
                        file_ext = output_path.suffix.lower()
                        safe_filename = f"{uuid.uuid4().hex}{file_ext}"
                        temp_storage_dir = get_temp_storage_dir()
                        target_path = temp_storage_dir / safe_filename
                        shutil.move(str(output_path), str(target_path))
                        result["download_url"] = f"/api/download/temp/{safe_filename}"
                        result["output_file"] = str(target_path)
                        result.setdefault("metadata", {})
                        result["metadata"]["persisted_output"] = True
                        logger.info("无模板输出已持久化: %s", safe_filename)
                    except Exception as e:
                        logger.warning("无模板输出持久化失败: %s", e)

            return JSONResponse(result)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"抽取失败: {str(e)}")
