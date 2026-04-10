"""
直接抽取服务 — 无需创建后台任务，同步返回结果

职责边界：
- 接收模板路径 + 输入目录，调用核心算法，返回 {"records": [...]} 格式
- 不写数据库，不管文件存储，由调用方（api_server.py）处理临时目录
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.core.profile import generate_profile_from_template, generate_profile_from_document
from src.core.reader import collect_input_bundle, try_internal_structured_extract
from src.core.postprocess import process_by_profile, validate_required_fields
from src.core.extractor import UniversalExtractor

logger = logging.getLogger(__name__)


def direct_extract(
    template_path: str,
    input_dir: str,
    model_type: Optional[str] = None,
    instruction: Optional[str] = None,
    llm_mode: str = 'full',
    enable_unit_aware: bool = True,
    work_dir: Optional[Path] = None,
    total_timeout: int = 110,
    max_chunks: int = 50,
    quiet: bool = False,
) -> Dict[str, Any]:
    """同步执行文档信息提取

    Args:
        template_path: 模板文件路径（.xlsx / .docx / .json）
        input_dir: 输入文档目录
        model_type: 模型类型（ollama / deepseek / openai），None 则用配置
        instruction: 补充抽取指令（可选）
        llm_mode: LLM抽取模式，可选值：'full'（始终全文抽取，默认）、'supplement'（仅补充缺失字段）
        enable_unit_aware: 是否启用单位感知（预留，暂不影响主流程）
        work_dir: 可选的工作空间目录（持久化场景由调用方提供，None 则无输出落盘）
        total_timeout: 总超时时间（秒），默认110秒
        max_chunks: 最大语义分块数量，默认50
        quiet: 安静模式，禁用控制台输出，默认False

    Returns:
        {
            "records": List[dict],        # 按模板字段对齐的记录数组
            "metadata": dict,             # 处理元数据
        }
    """
    try:
        # 0. 模型可用性检查
        from src.adapters.model_client import call_model
        try:
            call_model("测试连接", timeout=10)
        except Exception as e:
            logger.error(f"模型不可用: {e}")
            return {
                "records": [],
                "metadata": {
                    "error": '模型不可用，请在网页端"模型配置"页面配置可用的AI模型（Ollama本地模型或DeepSeek/OpenAI云API）',
                    "model_error": str(e),
                },
            }

        # 1. 加载文档（先加载，因为无模板时需要文档内容来生成profile）
        bundle = collect_input_bundle(input_dir)

        # 2. 生成 profile（支持三种模式自动判断）
        profile = _load_profile(template_path, instruction, bundle.get("all_text", ""))
        doc_type = profile.get("_doc_type", "")

        if not quiet:
            if doc_type:
                logger.info(f"文档自动分析结果: {doc_type}, 字段数: {len(profile.get('fields', []))}")

        # 3. 先尝试结构化表格提取（Docling DataFrame）
        records = try_internal_structured_extract(profile, bundle)

        # 4. 优先尝试 langextract（云 API 或大模型时更快更精确）
        import time
        text = bundle.get("all_text", "")
        langextract_used = False
        try:
            from src.adapters.langextract_adapter import extract_with_langextract
            text_chunks = [{"text": text}] if text else []
            lx_records = extract_with_langextract(
                text_chunks, profile,
                time_budget=total_timeout,
                quiet=quiet,
            )
            if lx_records is not None and len(lx_records) > 0:
                if not quiet:
                    logger.info(f"langextract 提取成功: {len(lx_records)} 条记录")
                records = (records or []) + lx_records
                langextract_used = True
        except Exception as e:
            logger.debug(f"langextract 不可用: {e}")

        # 5. LLM抽取（langextract 未成功时回退到 prompt 方案）
        if not langextract_used:
            config = {
                "llm_mode": llm_mode,
                "total_timeout": total_timeout,
                "max_chunks": max_chunks,
                "quiet": quiet,
                "total_deadline": time.time() + total_timeout if total_timeout else None
            }
            if model_type:
                config["model_type"] = model_type
            extractor = UniversalExtractor(config=config)
            result = extractor.extract(text, profile)
            if records:
                records = records + result.records
            else:
                records = result.records

        records = records or []

        # 6. 后处理（字段标准化）
        if records and profile.get("fields"):
            records = process_by_profile(records, profile)

        return {
            "records": records,
            "metadata": {
                "file_count": bundle.get("file_count", 0),
                "record_count": len(records),
                "template_mode": profile.get("template_mode", "unknown"),
                "task_mode": profile.get("task_mode", "unknown"),
                "doc_type": profile.get("_doc_type", "template_based"),
                "profile_auto_generated": bool(profile.get("_doc_type")),
            },
        }

    except Exception as e:
        logger.error(f"直接抽取失败: {e}", exc_info=True)
        return {
            "records": [],
            "metadata": {"error": str(e)},
        }


def _load_profile(template_path: str, instruction: Optional[str], document_text: str = "") -> dict:
    """从模板文件、用户指令或文档内容自动生成 profile

    三种模式自动判断：
    1. 有模板文件 → 严格按模板表头生成 profile（规则优先，LLM辅助）
    2. 无模板但有指令 → LLM 根据指令生成 profile
    3. 无模板无指令 → 自动分析文档内容，智能推断最优字段结构
    """
    # 模式1：有模板文件
    if template_path and template_path.strip():
        path = Path(template_path)

        if path.suffix.lower() == ".json":
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

        if path.exists():
            profile = generate_profile_from_template(
                template_path=template_path,
                use_llm=bool(instruction),
                mode="auto",
                user_description=instruction,
            )
            if profile and profile.get("fields"):
                return profile

    # 模式2：无模板但有用户指令
    if instruction and instruction.strip():
        # 使用指令 + 文档样本来生成 profile
        from src.core.profile import generate_profile_smart
        profile = generate_profile_smart(
            template_path="",
            instruction=instruction,
            document_sample=document_text[:3000] if document_text else "",
        )
        if profile and profile.get("fields"):
            return profile

    # 模式3：无模板无指令 → 全自动文档分析
    if document_text and document_text.strip():
        logger.info("无模板无指令，启动文档自动分析...")
        profile = generate_profile_from_document(document_text)
        if profile and profile.get("fields"):
            return profile

    # 兜底
    from src.core.profile import _default_profile
    return _default_profile(template_path)
