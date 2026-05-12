"""命令行与批处理入口：解析参数、生成 profile、加载输入并走抽取与模板写回。

与 ``api_server`` 的 HTTP 路径并存；不落业务库，输出目录由 ``--output-dir`` 等参数指定。
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from src.core.runtime_env import initialize_runtime_env

initialize_runtime_env(logger=logger, log_dotenv_loaded=True)

from src.config import EXTRACTION_TIMEOUT
from src.core.extraction_service import get_extraction_service

def load_rag_json(filepath: str) -> dict:
    """加载 ``--rag-json`` 指向的中间 JSON 文件。"""
    import json
    with open(filepath, 'r', encoding='utf-8') as f:
        return json.load(f)

def extract_retrieved_chunks_from_rag_json(rag_data: dict) -> list:
    """取 RAG 中间结果中的 ``retrieved_chunks`` 列表。"""
    return rag_data.get("retrieved_chunks", [])

def preprocess_retrieved_chunks(chunks: list) -> list:
    """检索块预处理钩子；当前为恒等映射。"""
    return chunks

def extract_structured_result_from_rag_json(rag_data: dict) -> dict:
    """取 RAG 中间结果中的 ``structured_result`` 对象。"""
    return rag_data.get("structured_result", {})

def ensure_parent_dir(filepath: str):
    """若缺失则创建目标文件所在目录。"""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)


def _persist_profile_to_disk(profile_path: str, profile: dict) -> None:
    """将 profile 写入磁盘；是否调用由 ``PERSIST_PROFILES`` 决定。"""
    ensure_parent_dir(profile_path)
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)

def normalize_input_path(path: str) -> str:
    """展开用户目录并转为绝对路径。"""
    import os
    return os.path.abspath(os.path.expanduser(path))

def summarize_for_console(records: list, profile: dict) -> str:
    """控制台输出的记录条数摘要文案。"""
    return f"提取了 {len(records)} 条记录"

from src.core.profile import (
    generate_profile_from_template,
    generate_profile_smart,
    apply_word_multi_instruction_constraints,
    effective_instruction_text,
    apply_instruction_runtime_hints,
)
from src.core.profile_resolver import resolve_profile
from src.config import TARGET_LIMIT_SECONDS
from src.config import PERSIST_PROFILES
from src.config import WRITE_RESULT_REPORT_BUNDLE
from src.core.llm_runtime import resolve_llm_mode_with_readiness
from src.core.reader import collect_input_bundle, try_internal_structured_extract
from src.core.postprocess import (
    process_by_profile,
    validate_required_fields,
)
from src.core.required_field_retry import with_source_text
from src.core.model_extraction_orchestrator import run_model_extraction_path
from src.core.extraction_result_harmonizer import (
    merge_internal_structured_when_model_insufficient,
    reconcile_word_multi_results,
)
from src.core.result_packager import build_cli_report_bundle
from src.core.output_writer_orchestrator import write_template_outputs_cli
from src.core.runtime_observability import merge_runtime_updates, finalize_runtime_metrics


def format_retrieved_chunks(chunks: list, top_k: int = 50) -> str:
    """将检索片段格式化为 LLM 上下文字符串；支持 str 与 dict，按 top_k 截断。"""
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


def _run_model_extraction_path(
    extraction_service,
    profile: dict,
    loaded_bundle: dict,
    context_for_llm: str,
    llm_context_route: str,
    effective_llm_mode: str,
    args,
    total_start: float,
    runtime: dict,
    source_text_for_order: str,
) -> tuple[dict, dict, str, list]:
    """执行模型抽取主路径（由共享 orchestrator 实现）。"""
    extracted_raw, model_output, context_for_llm, retried_fields, runtime_updates = run_model_extraction_path(
        extraction_service=extraction_service,
        profile=profile,
        loaded_bundle=loaded_bundle,
        context_for_llm=context_for_llm,
        llm_context_route=llm_context_route,
        effective_llm_mode=effective_llm_mode,
        slice_size=int(getattr(args, "slice_size", 3000)),
        overlap=int(getattr(args, "overlap", 200)),
        quiet=bool(getattr(args, "quiet", False)),
        max_chunks=int(getattr(args, "max_chunks", 50)),
        total_start=total_start,
        total_timeout=int(getattr(args, "total_timeout", EXTRACTION_TIMEOUT)),
        source_text_for_order=source_text_for_order,
        logger=logger,
    )
    merge_runtime_updates(runtime, runtime_updates)
    logger.info("模型抽取结果: %s", summarize_for_console(model_output, profile))
    return extracted_raw, model_output, context_for_llm, retried_fields


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
        logger.info("无模板模式：使用初始 profile，文档加载后解析字段结构")
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
        logger.info("通用模板：使用初始 profile，文档加载后生成任务专属 profile")

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
        profile = apply_word_multi_instruction_constraints(
            profile,
            effective_instruction_text(args.instruction, profile),
        )

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
                _persist_profile_to_disk(profile_path, profile)
        except Exception as e:
            logger.warning("文档专属profile生成失败，使用原始profile: %s", e)

    if is_no_template and all_text.strip():
        logger.info("无模板模式：基于文档内容自动分析字段结构...")
        try:
            auto_profile = resolve_profile(
                template_path="",
                instruction="",
                document_text=all_text,
                logger=logger,
            )
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
                    _persist_profile_to_disk(profile_path, profile)
                logger.debug("自动分析 profile: %s", json.dumps(profile, ensure_ascii=False))
        except Exception as e:
            logger.warning("文档自动分析失败，保留初始 profile: %s", e)

    return profile


def _maybe_load_instruction_sidecar(args: argparse.Namespace) -> None:
    """未传 ``--instruction`` 时，从输入目录或输入文件同目录读取 ``用户要求.txt``。

    多表 Word（``word_multi_table``）依赖其中的「表1：」「表2：」块生成 ``filter_field`` /
    ``filter_value``；缺失时全部记录会写入第一张表。
    """
    if str(args.instruction or "").strip():
        return
    inp = Path(args.input_dir)
    candidates = []
    if inp.is_dir():
        candidates.append(inp / "用户要求.txt")
    elif inp.is_file():
        candidates.append(inp.parent / "用户要求.txt")
    for p in candidates:
        try:
            if p.is_file():
                args.instruction = p.read_text(encoding="utf-8")
                logger.info("已从侧文件读取抽取指令（等价于 --instruction）：%s", p.resolve())
                return
        except OSError as e:
            logger.warning("读取侧指令文件失败 %s：%s", p, e)


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
    parser.add_argument(
        '--instruction',
        type=str,
        default='',
        help='自定义抽取指令；多表 Word 需含「表1：」等分表说明。未传时若输入目录存在「用户要求.txt」则自动读取',
    )
    parser.add_argument('--model-type', type=str, default='',
                       help='可选模型类型（deepseek/openai/qwen/ollama），为空时使用环境变量配置')

    # 双模式模板理解参数
    parser.add_argument('--template-mode', type=str, default='auto', choices=['file', 'llm', 'auto'],
                       help='模板理解模式: file(文件解析), llm(自然语言描述), auto(自动选择)')
    parser.add_argument('--template-description', type=str, default='',
                       help='自然语言模板描述（llm 模式必填）；说明任务目标与字段侧重即可')

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
    parser.add_argument('--total-timeout', type=int, default=EXTRACTION_TIMEOUT,
                       help='整体抽取最大允许时间（秒，默认与 EXTRACTION_TIMEOUT 一致）')
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
    llm_resolution = resolve_llm_mode_with_readiness(
        args.llm_mode,
        args.model_type if args.model_type else None,
        quiet=False,
        logger=logger,
    )
    requested_llm_mode = llm_resolution.requested
    normalized_llm_mode = llm_resolution.normalized
    effective_llm_mode = llm_resolution.effective
    if normalized_llm_mode != "off":
        logger.info("检查模型可用性...")
        if bool(llm_resolution.readiness.get("ready")):
            logger.info("模型可用性检查通过")
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

    _maybe_load_instruction_sidecar(args)

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
            _persist_profile_to_disk(profile_path, profile)
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

        if args.prefer_rag_structured and structured_rag_result:
            logger.info("检测到 RAG 已提供结构化结果。")
            if args.force_model:
                logger.info("--force-model 参数启用，即使有结构化结果也调用模型补充")
            else:
                logger.info("优先直接使用 RAG 结构化结果")
                extracted_raw = structured_rag_result
                internal_route_used = 'rag_structured'
        else:
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

        if extracted_raw is None or args.force_model or effective_llm_mode == 'full':
            context_for_llm, llm_context_route = _prepare_llm_context(args, retrieved_chunks, all_text)
            extracted_raw, model_output, context_for_llm, retried_fields = _run_model_extraction_path(
                extraction_service=extraction_service,
                profile=profile,
                loaded_bundle=loaded_bundle,
                context_for_llm=context_for_llm,
                llm_context_route=llm_context_route,
                effective_llm_mode=effective_llm_mode,
                args=args,
                total_start=total_start,
                runtime=runtime,
                source_text_for_order=all_text,
            )

        step_start = time.perf_counter()
        final_data = process_by_profile(with_source_text(extracted_raw, all_text), profile)
        final_data = merge_internal_structured_when_model_insufficient(
            final_data=final_data,
            internal_structured=internal_structured,
            effective_llm_mode=effective_llm_mode,
            all_text=all_text,
            profile=profile,
            logger=logger,
        )
        final_data = reconcile_word_multi_results(
            final_data=final_data,
            profile=profile,
            loaded_bundle=loaded_bundle,
            all_text=all_text,
            logger=logger,
            internal_structured=internal_structured,
        )
        missing_required_fields = validate_required_fields(final_data, profile)
        runtime['rule_processing_seconds'] = round(time.perf_counter() - step_start, 3)

        if missing_required_fields:
            logger.warning("最终结果仍缺失关键字段：%s", missing_required_fields)
        logger.info("最终格式化结果：%s", summarize_for_console(final_data, profile))

        step_start = time.perf_counter()
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump(final_data, f, ensure_ascii=False, indent=2)
        runtime['write_json_seconds'] = round(time.perf_counter() - step_start, 3)

        step_start = time.perf_counter()
        template_mode = write_template_outputs_cli(
            template_path=template_path,
            is_no_template=is_no_template,
            is_generic_template=is_generic_template,
            final_data=final_data,
            profile=profile,
            output_xlsx=output_xlsx,
            output_docx=output_docx,
            logger=logger,
        )
        runtime['write_template_seconds'] = round(time.perf_counter() - step_start, 3)

        finalize_runtime_metrics(
            runtime,
            total_start=total_start,
            target_limit_seconds=TARGET_LIMIT_SECONDS,
        )

        if WRITE_RESULT_REPORT_BUNDLE:
            field_evidence = attach_field_evidence(extracted_raw, retrieved_chunks) if profile.get('task_mode') == 'single_record' and retrieved_chunks else {}
            report_bundle = build_cli_report_bundle(
                final_data=final_data,
                extracted_raw=extracted_raw,
                profile=profile,
                runtime=runtime,
                missing_required_fields=missing_required_fields,
                retried_fields=retried_fields,
                input_text=all_text,
                profile_path=profile_path,
                template_mode=template_mode,
                output_json=output_json,
                output_xlsx=output_xlsx,
                output_docx=output_docx,
                rag_json_path=args.rag_json,
                retrieved_chunks=retrieved_chunks,
                prefer_rag_structured=bool(args.prefer_rag_structured),
                structured_rag_result=structured_rag_result,
                internal_route_used=internal_route_used,
                persist_profiles=bool(PERSIST_PROFILES),
                field_evidence=field_evidence,
            )
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
        if WRITE_RESULT_REPORT_BUNDLE:
            logger.info("已生成：%s", output_report_bundle_json)
        else:
            logger.info("未写出 %s（默认关闭；调试可设 A23_WRITE_RESULT_REPORT_BUNDLE=true）", os.path.basename(output_report_bundle_json))
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