import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger.info("已从.env文件加载环境变量")
except ImportError:
    logger.warning("dotenv未安装，将使用系统环境变量")

# 导入核心服务
from src.core.extraction_service import get_extraction_service

# RAG服务 - 简化占位实现
def load_rag_json(filepath: str) -> dict:
    """简化版：加载RAG JSON文件"""
    import json
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_retrieved_chunks_from_rag_json(rag_data: dict) -> list:
    """简化版：从RAG数据提取检索块"""
    return rag_data.get("retrieved_chunks", [])

def preprocess_retrieved_chunks(chunks: list) -> list:
    """简化版：预处理检索块"""
    return chunks

def extract_structured_result_from_rag_json(rag_data: dict) -> dict:
    """简化版：从RAG数据提取结构化结果"""
    return rag_data.get("structured_result", {})

# 文件服务 - 简化版本
def ensure_parent_dir(filepath: str):
    """确保文件父目录存在"""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

def normalize_input_path(path: str) -> str:
    """规范化输入路径"""
    import os
    return os.path.abspath(os.path.expanduser(path))

# 输出服务 - 简化版本
def summarize_for_console(records: list, profile: dict) -> str:
    """简化版：为控制台输出总结"""
    return f"提取了 {len(records)} 条记录"

from src.core.profile import (
    generate_profile_from_template,
    generate_profile_smart,
    generate_profile_from_document,
    apply_word_multi_instruction_constraints,
    apply_instruction_runtime_hints,
)
from src.core.word_multi_segments import build_word_multi_table_segments
from src.core.record_dedup import dedup_records
from src.config import TARGET_LIMIT_SECONDS
from src.config import PERSIST_PROFILES
from src.core.llm_mode import normalize_llm_mode
from src.core.model_availability import detect_model_readiness
from src.core.extraction_routing import is_word_multi_parallel_enabled, table_specs_homogeneous_columns
from src.core.reader import collect_input_bundle, collect_semantic_chunks_from_bundle, try_internal_structured_extract
from src.core.postprocess import (
    build_debug_result,
    build_run_summary,
    process_by_profile,
    retry_missing_required_fields,
    validate_required_fields,
)
from src.core.writers import fill_excel_table, fill_excel_vertical, fill_word_table, create_excel_from_records


# 函数简化实现（不再依赖复杂服务模块）

def format_retrieved_chunks(chunks: list, top_k: int = 50) -> str:
    """将 RAG 检索片段格式化为可用于 LLM 的上下文文本。

    兼容 chunk 为 str / dict 的常见形态；仅做最小格式化与截断。
    """
    if not chunks:
        return ""
    lines = []
    for i, ch in enumerate(chunks[: max(1, int(top_k or 50))]):
        if isinstance(ch, str):
            text = ch
        elif isinstance(ch, dict):
            text = ch.get("text") or ch.get("content") or ch.get("chunk") or ""
        else:
            text = str(ch)
        text = str(text).strip()
        if not text:
            continue
        lines.append(f"[chunk {i+1}]\n{text}")
    return "\n\n".join(lines).strip()


def attach_field_evidence(extracted_raw: dict, retrieved_chunks: list, max_evidence_chars: int = 500) -> dict:
    """为单记录字段附加检索证据（轻量实现）。

    目标：避免 main.py 运行期因缺失函数崩溃；证据为启发式匹配，找不到则空字符串。
    """
    if not isinstance(extracted_raw, dict):
        return {}
    values = {k: v for k, v in extracted_raw.items() if not str(k).startswith("_")}
    evidence = {}
    if not retrieved_chunks:
        return evidence

    # 统一 chunk 文本
    chunk_texts = []
    for ch in retrieved_chunks:
        if isinstance(ch, str):
            t = ch
        elif isinstance(ch, dict):
            t = ch.get("text") or ch.get("content") or ch.get("chunk") or ""
        else:
            t = str(ch)
        t = str(t).strip()
        if t:
            chunk_texts.append(t)

    for field, raw_val in values.items():
        v = "" if raw_val is None else str(raw_val).strip()
        if not v:
            evidence[field] = ""
            continue
        hit = ""
        for t in chunk_texts:
            if v in t:
                hit = t
                break
        if hit:
            # 截断证据，保留命中附近的片段
            pos = hit.find(v)
            if pos >= 0:
                start = max(0, pos - max_evidence_chars // 2)
                end = min(len(hit), pos + len(v) + max_evidence_chars // 2)
                snippet = hit[start:end].strip()
            else:
                snippet = hit[:max_evidence_chars].strip()
            evidence[field] = snippet
        else:
            evidence[field] = ""

    return evidence


def _prepare_llm_context(args, retrieved_chunks: list, all_text: str) -> tuple[str, str]:
    """准备模型抽取上下文，并返回内部路由标记。"""
    retrieved_context = format_retrieved_chunks(retrieved_chunks, top_k=50) if retrieved_chunks else ''
    if retrieved_context.strip():
        logger.info("已启用 RAG 片段优先模式")
        return retrieved_context, 'rag_chunks'

    if args.rag_json.strip():
        logger.warning("RAG JSON 中未拿到有效片段，自动退回全文抽取模式。")
    context_for_llm = all_text
    if not context_for_llm.strip():
        raise ValueError('既没有有效原文，也没有可用的 RAG 片段或结构化记录，无法继续抽取。')
    return context_for_llm, 'full_text'


def _with_source_text(extracted_raw: Any, source_text: str) -> dict:
    """为后处理注入 _source_text，保障按原文顺序稳定排序。"""
    if isinstance(extracted_raw, dict):
        payload = dict(extracted_raw)
    elif isinstance(extracted_raw, list):
        payload = {"records": list(extracted_raw)}
    else:
        payload = {"records": []}
    payload["_source_text"] = source_text or ""
    return payload


def _run_model_extraction_path(
    extraction_service,
    profile: dict,
    loaded_bundle: dict,
    context_for_llm: str,
    effective_llm_mode: str,
    args,
    total_start: float,
    runtime: dict,
    source_text_for_order: str,
) -> tuple[dict, dict, str, list]:
    """执行模型抽取主路径（保持现有行为）。"""
    logger.info("使用模型智能抽取模式")
    step_start = time.perf_counter()

    elapsed_before_extraction = time.perf_counter() - total_start
    total_timeout = getattr(args, 'total_timeout', 110)
    dynamic_time_budget = max(40, total_timeout - int(elapsed_before_extraction))
    logger.info("动态切片时间预算: %ss（已用 %.1fs）", dynamic_time_budget, elapsed_before_extraction)

    max_llm_input_chars = 24000
    if profile.get("template_mode") == "word_multi_table" and table_specs_homogeneous_columns(profile):
        # 同标头多表：统一抽取需要更多上下文，避免只命中文档前段导致漏表。
        max_llm_input_chars = 80000
    if len(context_for_llm) > max_llm_input_chars:
        logger.info("文本长度 %s 字符，截断至 %s 字符以控制耗时", len(context_for_llm), max_llm_input_chars)
        context_for_llm = context_for_llm[:max_llm_input_chars]

    all_semantic_chunks = collect_semantic_chunks_from_bundle(loaded_bundle)

    word_table_segments = None
    if is_word_multi_parallel_enabled(profile):
        word_table_segments = build_word_multi_table_segments(
            profile, context_for_llm, loaded_bundle.get("documents", [])
        )

    extracted_raw, model_output, slicing_metadata = extraction_service.extract_with_slicing(
        text=context_for_llm,
        profile=profile,
        use_model=(effective_llm_mode != "off"),
        slice_size=args.slice_size,
        overlap=args.overlap,
        show_progress=not args.quiet,
        time_budget=dynamic_time_budget,
        chunks=all_semantic_chunks if all_semantic_chunks else None,
        max_chunks=args.max_chunks,
        word_table_segments=word_table_segments,
        routing_bundle=loaded_bundle,
    )

    runtime['build_prompt_seconds'] = round(time.perf_counter() - step_start, 3)
    runtime['model_inference_seconds'] = 0.0

    logger.info("切片抽取完成")
    logger.info("切片模式: %s", slicing_metadata.get("slicing_enabled", False))
    if slicing_metadata.get("slicing_enabled"):
        logger.info("切片数量: %s", slicing_metadata.get("slice_count", 1))

    logger.info("模型抽取结果: %s", summarize_for_console(model_output, profile))

    temp_final_data = process_by_profile(_with_source_text(extracted_raw, source_text_for_order), profile)
    missing_before_retry = validate_required_fields(temp_final_data, profile)
    runtime['retry_inference_seconds'] = 0.0
    retried_fields = []
    if missing_before_retry:
        logger.warning("首次抽取后关键字段缺失：%s", missing_before_retry)
        retry_start = time.perf_counter()
        extracted_raw, retried_fields = retry_missing_required_fields(
            context_for_llm, profile, extracted_raw, missing_before_retry
        )
        runtime['retry_inference_seconds'] = round(time.perf_counter() - retry_start, 3)
        if retried_fields:
            logger.info("已触发补抽并补回内容：%s", retried_fields)

    return extracted_raw, model_output, context_for_llm, retried_fields


def _records_from_final_data(final_data: Any) -> list:
    """从 final_data 中提取 records，兼容单记录字典回退。"""
    records = final_data.get('records', []) if isinstance(final_data, dict) else []
    if not records and isinstance(final_data, dict):
        non_meta = {k: v for k, v in final_data.items() if not k.startswith('_')}
        if non_meta:
            records = [non_meta]
    return records


def _build_word_multi_groups(records: list, final_data: Any, profile: dict) -> list:
    """构建 word_multi_table 填表分组（优先使用 _table_groups）。"""
    def _apply_fixed_values(rows: list, fixed: dict) -> list:
        if not fixed:
            return rows
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rr = dict(row)
            for k, v in fixed.items():
                if str(v).strip():
                    rr[k] = v
            out.append(rr)
        return out

    def _dedup_group_records(rows: list, spec: dict) -> list:
        if not rows:
            return rows
        if not isinstance(spec, dict):
            spec = {}

        dedup_fields = spec.get("dedup_key_fields")
        deduped, _, _ = dedup_records(rows, preferred_fields=dedup_fields if isinstance(dedup_fields, list) else None)
        return deduped

    def _cap_rows_by_template_capacity(rows: list, spec: dict) -> list:
        if not isinstance(spec, dict):
            return rows
        cap = spec.get("max_rows")
        try:
            cap_n = int(cap)
        except Exception:
            return rows
        if cap_n <= 0:
            return rows
        return list(rows)[:cap_n]

    table_specs = profile.get('table_specs', [])
    pre_groups = final_data.get('_table_groups') if isinstance(final_data, dict) else None
    if isinstance(pre_groups, list) and pre_groups:
        spec_by_index = {
            int(s.get('table_index', i)): s
            for i, s in enumerate(table_specs) if isinstance(s, dict)
        }
        table_groups = [
            {'table_index': int(g.get('table_index', 0)), 'records': g.get('records', [])}
            for g in pre_groups
        ]
        fixed_by_index = {
            int(s.get('table_index', i)): dict(s.get('fixed_values') or {})
            for i, s in enumerate(table_specs) if isinstance(s, dict)
        }
        for g in table_groups:
            tid = int(g.get('table_index', 0))
            rows = _apply_fixed_values(g.get('records', []), fixed_by_index.get(tid, {}))
            g['records'] = _dedup_group_records(rows, spec_by_index.get(tid, {}))
            g['records'] = _cap_rows_by_template_capacity(g['records'], spec_by_index.get(tid, {}))
        logger.info("使用并行抽取生成的 _table_groups（%s 组）填表", len(table_groups))
        return table_groups

    table_groups = []
    for spec in table_specs:
        filter_field = spec.get('filter_field', '')
        filter_value = spec.get('filter_value', '')
        fixed_values = dict(spec.get('fixed_values') or {})
        table_idx = int(spec.get('table_index', 0))
        if filter_field and filter_value:
            group_records = [r for r in records if filter_value in str(r.get(filter_field, ''))]
        else:
            # 未配置显式分组时，避免把同一批记录复制到所有表。
            group_records = records if table_idx == 0 else []
        group_records = _apply_fixed_values(group_records, fixed_values)
        group_records = _dedup_group_records(group_records, spec)
        group_records = _cap_rows_by_template_capacity(group_records, spec)
        logger.info("表格%s（%s）: %s 条记录", table_idx + 1, filter_value, len(group_records))
        table_groups.append({'table_index': table_idx, 'records': group_records})
    return table_groups


def _write_template_outputs(
    *,
    template_path: str,
    is_no_template: bool,
    is_generic_template: bool,
    final_data: Any,
    profile: dict,
    output_xlsx: str,
    output_docx: str,
) -> str:
    """按模板模式写出结果文件，返回最终 template_mode。"""
    template_mode = profile.get('template_mode', 'vertical')

    if not template_path or is_no_template:
        logger.info("无模板：动态创建Excel输出")
        records = _records_from_final_data(final_data)
        if records:
            create_excel_from_records(output_xlsx, records)
            logger.info("动态Excel已生成: %s，共 %s 条记录", output_xlsx, len(records))
        else:
            logger.info("无有效记录，跳过Excel输出")
        return template_mode

    if is_generic_template:
        logger.info("通用模板：动态创建任务专属Excel（不受模板列限制）")
        records = _records_from_final_data(final_data)
        create_excel_from_records(output_xlsx, records)
        logger.info("动态Excel已生成: %s，共 %s 条记录", output_xlsx, len(records))
        return template_mode

    if template_mode == 'word_multi_table':
        records = final_data.get('records', []) if isinstance(final_data, dict) else (final_data if isinstance(final_data, list) else [])
        logger.info("Word多表格模式：共 %s 条记录，%s 个表格", len(records), len(profile.get('table_specs', [])))
        table_groups = _build_word_multi_groups(records, final_data, profile)
        fill_payload = {'records': records, '_table_groups': table_groups}
        fill_word_table(
            template_path=template_path, output_path=output_docx,
            records=fill_payload,
            header_row=profile.get('header_row', 0),
            start_row=profile.get('start_row', 1)
        )
        return template_mode

    if template_mode == 'vertical':
        fill_excel_vertical(template_path, output_xlsx, final_data)
    elif template_mode == 'excel_table':
        fill_excel_table(
            template_path=template_path, output_path=output_xlsx, records=final_data,
            header_row=profile.get('header_row', 1), start_row=profile.get('start_row', 2)
        )
    elif template_mode == 'word_table':
        fill_word_table(
            template_path=template_path, output_path=output_docx, records=final_data,
            table_index=profile.get('table_index', 0),
            header_row=profile.get('header_row', 0),
            start_row=profile.get('start_row', 1)
        )
    else:
        logger.warning("未知template_mode: %s，尝试按excel_table处理", template_mode)
        fill_excel_table(template_path=template_path, output_path=output_xlsx, records=final_data, header_row=1, start_row=2)
    return template_mode


def _build_initial_profile(
    *,
    args: argparse.Namespace,
    template_path: str,
    is_no_template: bool,
    is_word_template: bool,
    is_generic_template: bool,
) -> dict:
    """构建初始 profile（不依赖输入文档正文）。"""
    if is_no_template:
        logger.info("无模板模式：先生成占位profile，文档加载后自动分析字段结构")
        if args.template_description:
            return generate_profile_smart(
                template_path="",
                instruction=args.template_description,
                document_sample=""
            )
        return {
            "report_name": "auto_generated",
            "template_path": "",
            "instruction": "从文档中提取关键结构化信息",
            "task_mode": "table_records",
            "template_mode": "generic",
            "fields": [{"name": "名称", "type": "text"}, {"name": "数值", "type": "number"}],
        }

    if is_word_template:
        logger.info("Word模板：优先使用规则识别生成profile（保留多表结构）")
        return generate_profile_from_template(
            template_path=template_path,
            use_llm=False,
            mode='file',
            user_description=args.template_description
        )

    if is_generic_template:
        logger.info("通用模板：先生成占位profile，文档加载后升级为文档专项profile")

    return generate_profile_from_template(
        template_path=template_path,
        use_llm=args.use_profile_llm,
        mode=args.template_mode,
        user_description=args.template_description
    )


def _apply_profile_runtime_settings(
    *,
    profile: dict,
    args: argparse.Namespace,
    is_word_template: bool,
) -> dict:
    """注入指令约束、word 模板修正与运行时标记。"""
    profile = apply_instruction_runtime_hints(profile, args.instruction)
    if args.instruction and args.instruction.strip():
        logger.info(
            "使用自定义指令：%s",
            f"{args.instruction[:100]}..." if len(args.instruction) > 100 else args.instruction,
        )

    if profile.get('template_mode') == 'word_multi_table':
        profile = apply_word_multi_instruction_constraints(profile, profile.get('instruction', ''))

    if is_word_template:
        if profile.get('template_mode') == 'excel_table':
            logger.warning("Word模板被误识别为 excel_table，已强制修正为 word_table")
            profile['template_mode'] = 'word_table'
        profile['header_row'] = 0
        profile['start_row'] = 1
        profile['enable_multi_template'] = profile.get('template_mode') == 'word_multi_table'
        profile['use_ai_allocation'] = False
    else:
        profile['enable_multi_template'] = False
        profile['use_ai_allocation'] = False

    return profile


def _upgrade_profile_with_document(
    *,
    profile: dict,
    args: argparse.Namespace,
    template_path: str,
    is_generic_template: bool,
    is_no_template: bool,
    all_text: str,
    profile_path: str,
) -> dict:
    """根据文档正文升级 profile（通用模板/无模板）。"""
    if is_generic_template and all_text.strip():
        logger.info("通用模板：基于文档内容和指令生成任务专属profile...")
        doc_sample = all_text[:3000]
        instruction_for_profile = args.instruction.strip() if args.instruction else profile.get('instruction', '智能提取文档中所有关键结构化数据')
        try:
            profile = generate_profile_smart(
                template_path=template_path,
                instruction=instruction_for_profile,
                document_sample=doc_sample
            )
            profile['template_mode'] = 'excel_table'
            profile['header_row'] = profile.get('header_row', 1)
            profile['start_row'] = profile.get('start_row', 2)
            profile['enable_multi_template'] = False
            profile['use_ai_allocation'] = False
            logger.info("文档专属profile生成完成，字段数: %s", len(profile.get("fields", [])))
            if PERSIST_PROFILES and profile_path:
                with open(profile_path, 'w', encoding='utf-8') as f:
                    json.dump(profile, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("文档专属profile生成失败，使用原始profile: %s", e)

    if is_no_template and all_text.strip():
        logger.info("无模板模式：基于文档内容自动分析字段结构...")
        try:
            auto_profile = generate_profile_from_document(all_text)
            if auto_profile and auto_profile.get("fields"):
                doc_type = auto_profile.get("_doc_type", "unknown")
                profile = auto_profile
                profile['template_mode'] = 'generic'
                profile['header_row'] = profile.get('header_row', 1)
                profile['start_row'] = profile.get('start_row', 2)
                profile['enable_multi_template'] = False
                profile['use_ai_allocation'] = False
                logger.info("文档自动分析完成: type=%s, 字段数=%s", doc_type, len(profile.get("fields", [])))
                if args.instruction and args.instruction.strip():
                    profile['instruction'] = args.instruction.strip()
                if PERSIST_PROFILES and profile_path:
                    with open(profile_path, 'w', encoding='utf-8') as f:
                        json.dump(profile, f, ensure_ascii=False, indent=2)
                logger.debug("自动分析 profile: %s", json.dumps(profile, ensure_ascii=False))
        except Exception as e:
            logger.warning("文档自动分析失败，使用占位profile: %s", e)

    return profile


def _maybe_fallback_to_internal_structured(
    *,
    final_data: Any,
    internal_structured: Any,
    effective_llm_mode: str,
    all_text: str,
    profile: dict,
) -> Any:
    """full 模式下的统一回退判断（行为保持不变）。"""
    if (
        effective_llm_mode == 'full'
        and not _records_from_final_data(final_data)
        and isinstance(internal_structured, dict)
        and internal_structured.get('records')
    ):
        logger.info("模型结果为空，回退到内部结构化结果")
        return process_by_profile(_with_source_text(internal_structured, all_text), profile)

    if (
        effective_llm_mode == 'full'
        and isinstance(internal_structured, dict)
        and isinstance(internal_structured.get('records'), list)
    ):
        model_rows = _records_from_final_data(final_data)
        internal_rows = internal_structured.get('records') or []
        if len(model_rows) <= 1 and len(internal_rows) > max(20, len(model_rows) * 10):
            logger.info("模型结果过少，回退到内部结构化结果")
            return process_by_profile(_with_source_text(internal_structured, all_text), profile)
    return final_data






def main():
    parser = argparse.ArgumentParser(description='A23 AI Demo - 智能完整抽取版')
    parser.add_argument('--template', required=False, default='', help='模板路径（file模式必需，llm模式可选）')
    parser.add_argument('--profile-output', default='', help='自动生成 profile 保存路径')
    parser.add_argument('--use-profile-llm', action='store_true', help='生成 profile 时启用本地模型增强字段推断')
    parser.add_argument('--input-dir', default='data/in', help='原始文档目录')
    parser.add_argument('--rag-json', default='', help='RAG 中间 JSON 路径')
    parser.add_argument('--prefer-rag-structured', action='store_true', help='若 RAG JSON 已含结构化结果，优先直接使用')
    parser.add_argument('--output-dir', default='output', help='输出目录')
    parser.add_argument('--overwrite-output', action='store_true', help='允许覆盖已有输出目录')
    parser.add_argument('--force-model', action='store_true', help='强制调用模型，即使内部结构化结果存在也调用模型（用于补充遗漏字段）')
    parser.add_argument('--instruction', type=str, default='', help='自定义抽取指令，将覆盖自动生成的instruction')
    parser.add_argument('--model-type', type=str, default='',
                       help='可选模型类型（deepseek/openai/qwen/ollama），为空时使用环境变量配置')

    # 双模式模板理解参数
    parser.add_argument('--template-mode', type=str, default='auto', choices=['file', 'llm', 'auto'],
                       help='模板理解模式: file(文件解析), llm(自然语言描述), auto(自动选择)')
    parser.add_argument('--template-description', type=str, default='',
                       help='自然语言模板描述（当使用llm模式时），如"提取城市、GDP、人口"')

    # 分块参数（新：使用语义分块；--slice-size/--overlap 已废弃，保留向后兼容）
    parser.add_argument('--quiet', action='store_true',
                       help='安静模式，禁用控制台进度输出')
    parser.add_argument('--max-chunks', type=int, default=50,
                       help='最多处理的语义块数量（默认50）')
    parser.add_argument('--slice-size', type=int, default=3000,
                       help='[兼容参数] 字符切片大小，仅在无语义分块时使用')
    parser.add_argument('--overlap', type=int, default=200,
                       help='[兼容参数] 字符切片重叠大小，仅在无语义分块时使用')
    parser.add_argument('--llm-mode', type=str, default='full',
                       help='LLM抽取模式：full=默认模型抽取，off=仅规则/结构化抽取（supplement 会兼容映射到 full）')
    parser.add_argument('--total-timeout', type=int, default=180,
                       help='整体抽取最大允许时间（秒，默认180）')
    parser.add_argument('--output-basename', type=str, default='',
                       help='输出文件basename（默认为空，使用输入文件名）')

    args = parser.parse_args()

    # 获取抽取服务实例
    extraction_service = get_extraction_service()

    template_path = args.template.strip() if args.template else None

    # 根据模板模式检查文件
    if args.template_mode == 'file':
        if not template_path:
            raise ValueError('file模式需要提供--template参数')
        if not os.path.exists(template_path):
            raise FileNotFoundError(f'找不到模板文件：{template_path}')
    elif args.template_mode == 'llm':
        if not args.template_description:
            raise ValueError('llm模式需要提供--template-description参数')
        # LLM模式可以没有模板文件
    elif args.template_mode == 'auto':
        # 自动模式：有模板文件用文件，有描述用描述，都没有用默认
        if template_path and not os.path.exists(template_path):
            raise FileNotFoundError(f'找不到模板文件：{template_path}')

    if not template_path and not args.template_description:
        logger.info("未提供模板文件或描述，系统将在文档加载后自动分析最优字段结构")

    # 模式规范化：主线仅保留 full/off（supplement 兼容映射为 full）。
    requested_llm_mode = args.llm_mode
    normalized_llm_mode = normalize_llm_mode(requested_llm_mode)

    # 模型可用性检查：不可用时自动降级为纯规则，避免用户本地无模型/无key时直接失败。
    effective_llm_mode = normalized_llm_mode
    if normalized_llm_mode != "off":
        logger.info("检查模型可用性...")
        readiness = detect_model_readiness(args.model_type if args.model_type else None, check_ollama=True)
        if bool(readiness.get("ready")):
            logger.info("模型可用性检查通过")
        else:
            effective_llm_mode = "off"
            logger.warning(
                "模型不可用，自动降级为纯规则抽取: model=%s reason=%s",
                readiness.get("model_type"),
                readiness.get("reason"),
            )
    if requested_llm_mode != normalized_llm_mode:
        logger.info("llm_mode 已规范化: %s -> %s", requested_llm_mode, normalized_llm_mode)


    # 标准化输入路径
    logger.info("原始输入路径: %s", args.input_dir)
    # 记录原始输入名（文件名stem或目录名），用于输出文件命名
    _raw_input_path = Path(args.input_dir)
    if _raw_input_path.is_file():
        input_base_name = _raw_input_path.stem
    else:
        # 目录：取目录名；若目录下只有一个文件也可取文件名
        _files = [f for f in _raw_input_path.iterdir() if f.is_file()] if _raw_input_path.exists() else []
        input_base_name = _files[0].stem if len(_files) == 1 else _raw_input_path.name

    normalized_input_dir = normalize_input_path(args.input_dir)
    if normalized_input_dir != args.input_dir:
        logger.info("标准化后输入目录: %s", normalized_input_dir)
        args.input_dir = normalized_input_dir

    if os.path.exists(args.output_dir) and os.listdir(args.output_dir) and not args.overwrite_output:
        raise ValueError(f'输出目录非空：{args.output_dir}。请使用 --overwrite-output 参数覆盖，或选择其他目录。')
    os.makedirs(args.output_dir, exist_ok=True)

    # 确定profile保存路径（默认不落盘；调试时用 A23_PERSIST_PROFILES=true 开启）
    profile_path = ""
    if PERSIST_PROFILES:
        if args.profile_output.strip():
            profile_path = args.profile_output.strip()
        elif template_path:
            profile_path = f"profiles/{Path(template_path).stem}_auto.json"
        else:
            profile_path = os.path.join(args.output_dir, "llm_profile_auto.json")
        ensure_parent_dir(profile_path)

    # 输出文件命名：优先使用--output-basename，否则基于输入文件名
    base_name = args.output_basename.strip() if args.output_basename else input_base_name

    output_json = os.path.join(args.output_dir, f'{base_name}_result.json')
    output_xlsx = os.path.join(args.output_dir, f'{base_name}_result.xlsx')
    output_docx = os.path.join(args.output_dir, f'{base_name}_result.docx')
    output_report_bundle_json = os.path.join(args.output_dir, f'{base_name}_result_report.json')

    runtime = {}
    total_start = time.perf_counter()
    retried_fields = []

    try:
        step_start = time.perf_counter()

        is_generic_template = template_path and Path(template_path).name in ('generic_template.xlsx', 'generic_template.docx')
        is_word_template = template_path and template_path.lower().endswith(('.doc', '.docx'))
        is_no_template = not template_path

        profile = _build_initial_profile(
            args=args,
            template_path=template_path,
            is_no_template=is_no_template,
            is_word_template=is_word_template,
            is_generic_template=is_generic_template,
        )
        profile = _apply_profile_runtime_settings(
            profile=profile,
            args=args,
            is_word_template=is_word_template,
        )

        if PERSIST_PROFILES and profile_path:
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        runtime['generate_profile_seconds'] = round(time.perf_counter() - step_start, 3)

        logger.info("自动生成的 profile 已就绪")
        logger.debug("profile detail: %s", json.dumps(profile, ensure_ascii=False))
        if PERSIST_PROFILES and profile_path:
            logger.info("已保存 profile：%s", profile_path)

        step_start = time.perf_counter()
        loaded_bundle = collect_input_bundle(args.input_dir) if os.path.exists(args.input_dir) else {'documents': [], 'all_text': '', 'warnings': []}
        all_text = loaded_bundle.get('all_text', '')
        runtime['read_documents_seconds'] = round(time.perf_counter() - step_start, 3)
        runtime['parsed_document_count'] = len(loaded_bundle.get('documents', []))
        runtime['parsed_warning_count'] = len(loaded_bundle.get('warnings', []))

        if loaded_bundle.get('warnings'):
            logger.warning("文档解析警告（前10条）:")
            for item in loaded_bundle['warnings'][:10]:
                logger.warning("- %s", item)

        if all_text.strip():
            logger.info("已读取文档内容（前800字符）")
            logger.debug("%s", all_text[:800])
        else:
            logger.info("当前未读取到可拼接正文内容，将优先尝试结构化解析或 RAG 结果。")

        profile = _upgrade_profile_with_document(
            profile=profile,
            args=args,
            template_path=template_path,
            is_generic_template=is_generic_template,
            is_no_template=is_no_template,
            all_text=all_text,
            profile_path=profile_path,
        )

        step_start = time.perf_counter()
        rag_data, retrieved_chunks, structured_rag_result = {}, [], None
        if args.rag_json.strip():
            if not os.path.exists(args.rag_json):
                raise FileNotFoundError(f'找不到 RAG JSON 文件：{args.rag_json}')
            rag_data = load_rag_json(args.rag_json)
            retrieved_chunks = preprocess_retrieved_chunks(extract_retrieved_chunks_from_rag_json(rag_data))
            structured_rag_result = extract_structured_result_from_rag_json(rag_data)
        runtime['load_rag_json_seconds'] = round(time.perf_counter() - step_start, 3)

        extracted_raw = None
        context_for_llm = ''
        internal_route_used = ''
        internal_structured = None

        # 检查是否有结构化结果
        if args.prefer_rag_structured and structured_rag_result:
            logger.info("检测到 RAG 已提供结构化结果。")
            if args.force_model:
                logger.info("--force-model 参数启用，即使有结构化结果也调用模型补充")
            else:
                logger.info("优先直接使用 RAG 结构化结果")
                extracted_raw = structured_rag_result
                internal_route_used = 'rag_structured'
        else:
            # 先尝试内部结构化抽取（对于Excel等结构化文档）
            step_start = time.perf_counter()
            internal_structured = try_internal_structured_extract(profile, loaded_bundle)
            runtime['internal_structured_extract_seconds'] = round(time.perf_counter() - step_start, 3)
            if internal_structured:
                logger.info("已命中内部结构化抽取通道：%s", internal_structured.get('_internal_route', 'internal_structured'))
                should_force_model = args.force_model or effective_llm_mode == 'full'
                if should_force_model:
                    if args.force_model:
                        logger.info("--force-model 参数启用，即使有结构化结果也调用模型补充")
                    else:
                        logger.info("llm_mode=full：即使有结构化结果也继续模型抽取，避免大表直接写回导致超时")
                else:
                    extracted_raw = internal_structured
                    internal_route_used = internal_structured.get('_internal_route', 'internal_structured')
            else:
                logger.info("内部结构化抽取未命中，使用智能抽取策略")

        # 如果没有抽取到结果，或者需要强制调用模型，则进行智能抽取
        if extracted_raw is None or args.force_model or effective_llm_mode == 'full':
            context_for_llm, internal_route_used = _prepare_llm_context(args, retrieved_chunks, all_text)
            extracted_raw, model_output, context_for_llm, retried_fields = _run_model_extraction_path(
                extraction_service=extraction_service,
                profile=profile,
                loaded_bundle=loaded_bundle,
                context_for_llm=context_for_llm,
                effective_llm_mode=effective_llm_mode,
                args=args,
                total_start=total_start,
                runtime=runtime,
                source_text_for_order=all_text,
            )

        runtime['model_inference_total_seconds'] = round(runtime.get('model_inference_seconds', 0.0) + runtime.get('retry_inference_seconds', 0.0), 3)

        step_start = time.perf_counter()
        final_data = process_by_profile(_with_source_text(extracted_raw, all_text), profile)
        final_data = _maybe_fallback_to_internal_structured(
            final_data=final_data,
            internal_structured=internal_structured,
            effective_llm_mode=effective_llm_mode,
            all_text=all_text,
            profile=profile,
        )
        if profile.get("template_mode") == "word_multi_table":
            homogeneous_multi = table_specs_homogeneous_columns(profile)
            current_records = _records_from_final_data(final_data)
            # 同标头多表若统一抽取结果过少，回退到内部结构化结果再做统一分表。
            if homogeneous_multi and len(current_records) <= 1:
                internal_structured = try_internal_structured_extract(profile, loaded_bundle)
                if isinstance(internal_structured, dict) and internal_structured.get("records"):
                    logger.info("同标头多表统一抽取结果偏少，回退到内部结构化结果后再分表")
                    final_data = process_by_profile(_with_source_text(internal_structured, all_text), profile)

            if (
                isinstance(final_data, dict)
                and final_data.get("_table_groups")
                and not homogeneous_multi
            ):
                from src.core.word_multi_internal_merge import merge_internal_structured_into_word_multi_groups
                final_data = merge_internal_structured_into_word_multi_groups(final_data, profile, loaded_bundle)
        missing_required_fields = validate_required_fields(final_data, profile)
        runtime['rule_processing_seconds'] = round(time.perf_counter() - step_start, 3)

        if missing_required_fields:
            logger.warning("最终结果仍缺失关键字段：%s", missing_required_fields)
        logger.info("最终格式化结果：%s", summarize_for_console(final_data, profile))

        debug_result = build_debug_result(final_data if isinstance(final_data, dict) else extracted_raw, profile)
        field_evidence = attach_field_evidence(extracted_raw, retrieved_chunks) if profile.get('task_mode') == 'single_record' and retrieved_chunks else {}

        retrieval_info = {
            'rag_json_provided': bool(args.rag_json.strip()),
            'rag_json_path': args.rag_json,
            'chunks_count': len(retrieved_chunks),
            'chunks_preview': retrieved_chunks[:3] if retrieved_chunks else [],
            'used_structured_rag_result': bool(args.prefer_rag_structured and structured_rag_result),
            'internal_route_used': internal_route_used,
        }

        step_start = time.perf_counter()
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        runtime['write_json_seconds'] = round(time.perf_counter() - step_start, 3)

        step_start = time.perf_counter()
        template_mode = _write_template_outputs(
            template_path=template_path,
            is_no_template=is_no_template,
            is_generic_template=is_generic_template,
            final_data=final_data,
            profile=profile,
            output_xlsx=output_xlsx,
            output_docx=output_docx,
        )
        runtime['write_template_seconds'] = round(time.perf_counter() - step_start, 3)

        total_seconds = round(time.perf_counter() - total_start, 3)
        runtime['total_seconds'] = total_seconds
        runtime['within_limit_seconds'] = total_seconds <= TARGET_LIMIT_SECONDS
        runtime['limit_seconds'] = TARGET_LIMIT_SECONDS

        run_summary = build_run_summary(profile=profile, runtime=runtime, missing_fields=missing_required_fields, retried_fields=retried_fields, input_text=all_text)
        report_bundle = {
            'meta': {
                'report_type': 'integrated_output_bundle',
                'profile_path': profile_path if (PERSIST_PROFILES and profile_path) else "",
                'profile_name': profile.get('report_name', ''),
                'template_path': profile.get('template_path', ''),
                'task_mode': profile.get('task_mode', 'single_record'),
                'template_mode': template_mode,
                'input_char_count': len(all_text),
                'generated_outputs': {
                    'result_json': output_json,
                    'result_xlsx': output_xlsx if template_mode in ['vertical', 'excel_table'] else '',
                    'result_docx': output_docx if template_mode in ('word_table', 'word_multi_table') else '',
                },
            },
            'run_summary': run_summary,
            'runtime_metrics': runtime,
            'debug_result': debug_result,
            'retrieval': retrieval_info,
            'field_evidence': field_evidence,
        }
        with open(output_report_bundle_json, 'w', encoding='utf-8') as f:
            json.dump(report_bundle, f, ensure_ascii=False, indent=2)

        logger.info("运行耗时统计")
        for k, v in runtime.items():
            logger.info("%s: %s", k, v)
        logger.info("已生成：%s", output_json)
        if template_mode in ['vertical', 'excel_table']:
            logger.info("已生成：%s", output_xlsx)
        if template_mode in ('word_table', 'word_multi_table'):
            logger.info("已生成：%s", output_docx)
        logger.info("已生成：%s", output_report_bundle_json)
        if runtime['within_limit_seconds']:
            logger.info("总耗时在 %s 秒以内", TARGET_LIMIT_SECONDS)
        else:
            logger.warning("总耗时超过 %s 秒，需要继续优化", TARGET_LIMIT_SECONDS)

    except FileNotFoundError as e:
        logger.error("[file_error] %s", e)
        raise
    except json.JSONDecodeError as e:
        logger.error("[json_error] JSON 解析失败：%s", e)
        raise
    except ValueError as e:
        logger.error("[value_error] %s", e)
        raise
    except Exception as e:
        logger.error("[unknown_error] %s", e)
        raise


if __name__ == '__main__':
    main()