import argparse
import json
import logging
import os
import time
from pathlib import Path

# 加载环境变量
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("[INFO] 已从.env文件加载环境变量")
except ImportError:
    print("[WARN] dotenv未安装，将使用系统环境变量")

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

def get_rag_service():
    """简化版：获取RAG服务（占位）"""
    return None

# 文件服务 - 简化版本
def ensure_parent_dir(filepath: str):
    """确保文件父目录存在"""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

def normalize_input_path(path: str) -> str:
    """规范化输入路径"""
    import os
    return os.path.abspath(os.path.expanduser(path))

def get_file_service():
    """获取文件服务（占位）"""
    return None

def ensure_output_dir_empty(output_dir: str):
    """确保输出目录为空或可以覆盖"""
    import os
    import shutil
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)
    os.makedirs(output_dir, exist_ok=True)

def get_output_file_paths(output_dir: str, basename: str):
    """获取输出文件路径"""
    import os
    return {
        "excel": os.path.join(output_dir, f"{basename}.xlsx"),
        "word": os.path.join(output_dir, f"{basename}.docx"),
        "json": os.path.join(output_dir, f"{basename}.json"),
    }

# 输出服务 - 简化版本
def summarize_for_console(records: list, profile: dict) -> str:
    """简化版：为控制台输出总结"""
    return f"提取了 {len(records)} 条记录"

def get_output_service():
    """获取输出服务（占位）"""
    return None

def format_runtime_summary(runtime: dict) -> str:
    """简化版：格式化运行时总结"""
    import json
    return json.dumps(runtime, ensure_ascii=False, indent=2)

from src.core.profile import generate_profile_from_template, generate_profile_smart, generate_profile_from_document
from src.config import TARGET_LIMIT_SECONDS
from src.config import PERSIST_PROFILES
from src.core.reader import collect_input_bundle, try_internal_structured_extract
from src.adapters.model_client import call_model
from src.core.postprocess import (
    build_debug_result,
    build_run_summary,
    process_by_profile,
    retry_missing_required_fields,
    validate_required_fields,
)
from src.core.writers import fill_excel_table, fill_excel_vertical, fill_word_table, create_excel_from_records


def _is_debug_enabled() -> bool:
    v = os.environ.get("A23_DEBUG")
    if v is None:
        return False
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


_DEBUG = _is_debug_enabled()
_logger = logging.getLogger(__name__)


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


def merge_records_by_key(records: list, key_fields: list = None) -> list:
    """基于关键字段的记录融合去重（智能增强版）。

    增强功能：
    1. 自动检测关键字段（当未指定时）
    2. 基于关键字段的合并优先
    3. 基于内容相似度的二次合并（当 rapidfuzz 可用时）
    4. 保留原文顺序，清理内部标记字段

    相同键的记录进行字段级合并：新记录的非空值覆盖旧记录的空值。
    所有关键字段均为空的记录保留并打上 _unkeyed=True 标记。
    """
    # 使用抽取服务的合并函数
    extraction_service = get_extraction_service()
    return extraction_service.merge_records_by_key(records, key_fields)






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
    parser.add_argument('--llm-mode', type=str, default='full', choices=['full', 'supplement', 'off'],
                       help='LLM抽取模式：full=始终全文抽取（默认），supplement=仅补充缺失字段，off=仅规则/结构化抽取（不调用模型）')
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
        print('[INFO] 未提供模板文件或描述，系统将在文档加载后自动分析最优字段结构')

    # 模型可用性检查（llm-mode=off 时跳过）
    if args.llm_mode != "off":
        print('[INFO] 检查模型可用性...')
        try:
            test_prompt = "请回复一个简单的JSON：{\"status\": \"ok\"}"
            _ = call_model(test_prompt)
            print('[INFO] 模型可用性检查通过')
        except Exception as e:
            print(f'[ERROR] 模型不可用: {e}')
            print('[ERROR] 当前模式需要可用的AI模型才能运行。请配置以下任一模型：')
            print('  1. Ollama 本地模型: 启动 ollama serve 并拉取模型 (ollama pull qwen2.5:7b)')
            print('  2. DeepSeek API: 在 .env 中设置 A23_MODEL_TYPE=deepseek 和 A23_DEEPSEEK_API_KEY')
            print('  3. OpenAI 兼容 API: 在 .env 中设置 A23_MODEL_TYPE=openai 和相关配置')
            print('  网页端可在"模型配置"页面中切换和测试模型连接。')
            return


    # 标准化输入路径
    print(f'[INFO] 原始输入路径: {args.input_dir}')
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
        print(f'[INFO] 标准化后输入目录: {normalized_input_dir}')
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

        # 判断是否为通用模板（无模板场景）
        is_generic_template = template_path and Path(template_path).name in ('generic_template.xlsx', 'generic_template.docx')
        # 判断是否为Word模板
        is_word_template = template_path and template_path.lower().endswith(('.doc', '.docx'))
        # 判断是否完全无模板
        is_no_template = not template_path

        # 通用模板或Word模板使用智能LLM profile生成（其余用规则）
        # 注意：通用模板和无模板场景需要文档内容辅助，先做初始规则profile，文档加载后再升级
        if is_no_template:
            # 完全无模板：生成占位profile，文档加载后用 generate_profile_from_document 升级
            print('[INFO] 无模板模式：先生成占位profile，文档加载后自动分析字段结构')
            if args.template_description:
                # 有描述指令：用 LLM 生成
                profile = generate_profile_smart(
                    template_path="",
                    instruction=args.template_description,
                    document_sample=""
                )
            else:
                profile = {
                    "report_name": "auto_generated",
                    "template_path": "",
                    "instruction": "从文档中提取关键结构化信息",
                    "task_mode": "table_records",
                    "template_mode": "generic",
                    "fields": [{"name": "名称", "type": "text"}, {"name": "数值", "type": "number"}],
                }
        elif is_word_template:
            print('[INFO] Word模板：使用智能LLM分析生成profile（包含多表格识别）')
            instruction_for_profile = args.instruction.strip() if args.instruction else ''
            profile = generate_profile_smart(
                template_path=template_path,
                instruction=instruction_for_profile,
                document_sample=""  # Word模板结构已足够，不需要文档样本
            )
        elif is_generic_template:
            # 通用模板：先用规则生成占位profile，文档加载后升级
            print('[INFO] 通用模板：先生成占位profile，文档加载后升级为文档专项profile')
            profile = generate_profile_from_template(
                template_path=template_path,
                use_llm=args.use_profile_llm,
                mode=args.template_mode,
                user_description=args.template_description
            )
        else:
            # 真实Excel模板：规则模式足够准确且快速
            profile = generate_profile_from_template(
                template_path=template_path,
                use_llm=args.use_profile_llm,
                mode=args.template_mode,
                user_description=args.template_description
            )

        # 如果提供了自定义指令，覆盖profile中的instruction（Word模板已在generate_profile_smart中使用）
        if args.instruction and args.instruction.strip() and not is_word_template:
            profile['instruction'] = args.instruction.strip()
            print(f"[INFO] 使用自定义指令：{args.instruction[:100]}..." if len(args.instruction) > 100 else f"[INFO] 使用自定义指令：{args.instruction}")

        # Word模板：强制修正 template_mode（防止 LLM 误设为 excel_table）
        if is_word_template:
            if profile.get('template_mode') == 'excel_table':
                print("[WARN] Word模板被误识别为 excel_table，已强制修正为 word_table")
                profile['template_mode'] = 'word_table'
            # Word表格表头行是0（Excel习惯是1）
            profile['header_row'] = 0
            profile['start_row'] = 1
            profile['enable_multi_template'] = profile.get('template_mode') == 'word_multi_table'
            profile['use_ai_allocation'] = False
        else:
            profile['enable_multi_template'] = False
            profile['use_ai_allocation'] = False

        if PERSIST_PROFILES and profile_path:
            with open(profile_path, 'w', encoding='utf-8') as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
        runtime['generate_profile_seconds'] = round(time.perf_counter() - step_start, 3)

        print('=== 自动生成的 profile ===')
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        if PERSIST_PROFILES and profile_path:
            print(f'\n已保存 profile：{profile_path}')

        step_start = time.perf_counter()
        loaded_bundle = collect_input_bundle(args.input_dir) if os.path.exists(args.input_dir) else {'documents': [], 'all_text': '', 'warnings': []}
        all_text = loaded_bundle.get('all_text', '')
        runtime['read_documents_seconds'] = round(time.perf_counter() - step_start, 3)
        runtime['parsed_document_count'] = len(loaded_bundle.get('documents', []))
        runtime['parsed_warning_count'] = len(loaded_bundle.get('warnings', []))

        if loaded_bundle.get('warnings'):
            print('\n=== 文档解析警告（前10条）===')
            for item in loaded_bundle['warnings'][:10]:
                print('-', item)

        if all_text.strip():
            print('\n=== 已读取文档内容（前800字符）===')
            print(all_text[:800], '\n')
        else:
            print('[INFO] 当前未读取到可拼接正文内容，将优先尝试结构化解析或 RAG 结果。')

        # ——— 通用模板：基于文档内容动态生成任务专属profile ———
        if is_generic_template and all_text.strip():
            print('[INFO] 通用模板：基于文档内容和指令生成任务专属profile...')
            doc_sample = all_text[:3000]
            instruction_for_profile = args.instruction.strip() if args.instruction else profile.get('instruction', '智能提取文档中所有关键结构化数据')
            try:
                profile = generate_profile_smart(
                    template_path=template_path,
                    instruction=instruction_for_profile,
                    document_sample=doc_sample
                )
                # 通用模板专用：模板模式固定为excel_table（输出到新建Excel）
                profile['template_mode'] = 'excel_table'
                profile['header_row'] = profile.get('header_row', 1)
                profile['start_row'] = profile.get('start_row', 2)
                profile['enable_multi_template'] = False
                profile['use_ai_allocation'] = False
                print(f'[INFO] 文档专属profile生成完成，字段数: {len(profile.get("fields", []))}')
                if PERSIST_PROFILES and profile_path:
                    with open(profile_path, 'w', encoding='utf-8') as f:
                        json.dump(profile, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f'[WARN] 文档专属profile生成失败，使用原始profile: {e}')

        # ——— 无模板模式：基于文档内容自动分析最优字段结构 ———
        if is_no_template and all_text.strip():
            print('[INFO] 无模板模式：基于文档内容自动分析字段结构...')
            try:
                from src.core.profile import generate_profile_from_document
                auto_profile = generate_profile_from_document(all_text)
                if auto_profile and auto_profile.get("fields"):
                    doc_type = auto_profile.get("_doc_type", "unknown")
                    profile = auto_profile
                    profile['template_mode'] = 'generic'
                    profile['header_row'] = profile.get('header_row', 1)
                    profile['start_row'] = profile.get('start_row', 2)
                    profile['enable_multi_template'] = False
                    profile['use_ai_allocation'] = False
                    print(f'[INFO] 文档自动分析完成: type={doc_type}, 字段数={len(profile.get("fields", []))}')
                    # 如果有用户指令，覆盖 instruction
                    if args.instruction and args.instruction.strip():
                        profile['instruction'] = args.instruction.strip()
                    if PERSIST_PROFILES and profile_path:
                        with open(profile_path, 'w', encoding='utf-8') as f:
                            json.dump(profile, f, ensure_ascii=False, indent=2)
                    print('=== 自动分析 profile ===')
                    print(json.dumps(profile, ensure_ascii=False, indent=2))
            except Exception as e:
                print(f'[WARN] 文档自动分析失败，使用占位profile: {e}')

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

        # 检查是否有结构化结果
        if args.prefer_rag_structured and structured_rag_result:
            print('[INFO] 检测到 RAG 已提供结构化结果。')
            if args.force_model:
                print('[INFO] --force-model 参数启用，即使有结构化结果也调用模型补充')
            else:
                print('[INFO] 优先直接使用 RAG 结构化结果')
                extracted_raw = structured_rag_result
                internal_route_used = 'rag_structured'
                skip_model = True
        else:
            # 先尝试内部结构化抽取（对于Excel等结构化文档）
            step_start = time.perf_counter()
            internal_structured = try_internal_structured_extract(profile, loaded_bundle)
            runtime['internal_structured_extract_seconds'] = round(time.perf_counter() - step_start, 3)
            if internal_structured:
                print(f"[INFO] 已命中内部结构化抽取通道：{internal_structured.get('_internal_route', 'internal_structured')}")
                if args.force_model:
                    print('[INFO] --force-model 参数启用，即使有结构化结果也调用模型补充')
                else:
                    extracted_raw = internal_structured
                    internal_route_used = internal_structured.get('_internal_route', 'internal_structured')
                    skip_model = True
            else:
                print('[INFO] 内部结构化抽取未命中，使用智能抽取策略')

        # 如果没有抽取到结果，或者需要强制调用模型，则进行智能抽取
        if extracted_raw is None or args.force_model:
            # 准备LLM上下文
            retrieved_context = format_retrieved_chunks(retrieved_chunks, top_k=50) if retrieved_chunks else ''
            if retrieved_context.strip():
                context_for_llm = retrieved_context
                internal_route_used = 'rag_chunks'
                print('\n=== 已启用 RAG 片段优先模式 ===')
            else:
                context_for_llm = all_text
                internal_route_used = 'full_text'
                if args.rag_json.strip():
                    print('[WARN] RAG JSON 中未拿到有效片段，自动退回全文抽取模式。')

            if not context_for_llm.strip():
                raise ValueError('既没有有效原文，也没有可用的 RAG 片段或结构化记录，无法继续抽取。')

            # 使用模型抽取（支持切片模式）
            print('[INFO] 使用模型智能抽取模式')
            step_start = time.perf_counter()

            # 动态切片时间预算：扣除profile生成等前置耗时
            elapsed_before_extraction = time.perf_counter() - total_start
            total_timeout = getattr(args, 'total_timeout', 110)
            dynamic_time_budget = max(40, total_timeout - int(elapsed_before_extraction))
            print(f'[INFO] 动态切片时间预算: {dynamic_time_budget}s（已用 {elapsed_before_extraction:.1f}s）')

            # 限制输入长度，防止超时
            MAX_LLM_INPUT_CHARS = 24000
            if len(context_for_llm) > MAX_LLM_INPUT_CHARS:
                print(f'[INFO] 文本长度 {len(context_for_llm)} 字符，截断至 {MAX_LLM_INPUT_CHARS} 字符以控制耗时')
                context_for_llm = context_for_llm[:MAX_LLM_INPUT_CHARS]

            # 汇总所有文档的语义分块（按阅读顺序合并）
            all_semantic_chunks = []
            for doc in loaded_bundle.get("documents", []):
                all_semantic_chunks.extend(doc.get("chunks", []))

            # 使用切片感知抽取（优先语义分块，回退字符切片）
            extracted_raw, model_output, slicing_metadata = extraction_service.extract_with_slicing(
                text=context_for_llm,
                profile=profile,
                use_model=(args.llm_mode != "off"),
                slice_size=args.slice_size,
                overlap=args.overlap,
                show_progress=not args.quiet,
                time_budget=dynamic_time_budget,
                chunks=all_semantic_chunks if all_semantic_chunks else None,
                max_chunks=args.max_chunks,
            )

            runtime['build_prompt_seconds'] = round(time.perf_counter() - step_start, 3)
            runtime['model_inference_seconds'] = 0.0  # 时间已在extract_with_slicing内部统计

            print('=== 切片抽取完成 ===')
            print(f'[INFO] 切片模式: {slicing_metadata.get("slicing_enabled", False)}')
            if slicing_metadata.get("slicing_enabled"):
                print(f'[INFO] 切片数量: {slicing_metadata.get("slice_count", 1)}')

            print('=== 模型抽取结果 ===')
            print(json.dumps(summarize_for_console(model_output, profile), ensure_ascii=False, indent=2))

            temp_final_data = process_by_profile(extracted_raw, profile)
            missing_before_retry = validate_required_fields(temp_final_data, profile)
            runtime['retry_inference_seconds'] = 0.0

            if missing_before_retry:
                print(f'[WARN] 首次抽取后关键字段缺失：{missing_before_retry}')
                retry_start = time.perf_counter()
                extracted_raw, retried_fields = retry_missing_required_fields(context_for_llm, profile, extracted_raw, missing_before_retry)
                runtime['retry_inference_seconds'] = round(time.perf_counter() - retry_start, 3)
                if retried_fields:
                    print(f'[INFO] 已触发补抽并补回内容：{retried_fields}')

        runtime['model_inference_total_seconds'] = round(runtime.get('model_inference_seconds', 0.0) + runtime.get('retry_inference_seconds', 0.0), 3)

        step_start = time.perf_counter()
        final_data = process_by_profile(extracted_raw, profile)
        missing_required_fields = validate_required_fields(final_data, profile)
        runtime['rule_processing_seconds'] = round(time.perf_counter() - step_start, 3)

        if missing_required_fields:
            print(f'[WARN] 最终结果仍缺失关键字段：{missing_required_fields}')
        print('\n=== 最终格式化结果 ===')
        print(json.dumps(summarize_for_console(final_data, profile), ensure_ascii=False, indent=2))

        debug_result = build_debug_result(extracted_raw, profile)
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
        template_mode = profile.get('template_mode', 'vertical')

        # 如果没有模板路径，动态创建Excel输出
        if not template_path or is_no_template:
            print("[INFO] 无模板：动态创建Excel输出")
            records = final_data.get('records', []) if isinstance(final_data, dict) else []
            if not records and isinstance(final_data, dict):
                non_meta = {k: v for k, v in final_data.items() if not k.startswith('_')}
                if non_meta:
                    records = [non_meta]
            if records:
                create_excel_from_records(output_xlsx, records)
                print(f"[INFO] 动态Excel已生成: {output_xlsx}，共 {len(records)} 条记录")
            else:
                print("[INFO] 无有效记录，跳过Excel输出")
            runtime['write_template_seconds'] = round(time.perf_counter() - step_start, 3)
        else:
            # ——— 通用模板：动态创建Excel（字段由文档内容决定，不受模板列限制）———
            if is_generic_template:
                print("[INFO] 通用模板：动态创建任务专属Excel（不受模板列限制）")
                records = final_data.get('records', []) if isinstance(final_data, dict) else []
                if not records and isinstance(final_data, dict):
                    # 单记录模式回退
                    non_meta = {k: v for k, v in final_data.items() if not k.startswith('_')}
                    if non_meta:
                        records = [non_meta]
                create_excel_from_records(output_xlsx, records)
                print(f"[INFO] 动态Excel已生成: {output_xlsx}，共 {len(records)} 条记录")

            # ——— Word多表格模式：按城市/分组将记录分发到各表格 ———
            elif template_mode == 'word_multi_table':
                table_specs = profile.get('table_specs', [])
                records = final_data.get('records', []) if isinstance(final_data, dict) else (final_data if isinstance(final_data, list) else [])
                print(f"[INFO] Word多表格模式：共 {len(records)} 条记录，{len(table_specs)} 个表格")

                table_groups = []
                for spec in table_specs:
                    filter_field = spec.get('filter_field', '')
                    filter_value = spec.get('filter_value', '')
                    table_idx = int(spec.get('table_index', 0))
                    if filter_field and filter_value:
                        group_records = [r for r in records if filter_value in str(r.get(filter_field, ''))]
                    else:
                        group_records = records
                    print(f"  表格{table_idx+1}（{filter_value}）: {len(group_records)} 条记录")
                    table_groups.append({'table_index': table_idx, 'records': group_records})

                # 构建带_table_groups的payload
                fill_payload = {'records': records, '_table_groups': table_groups}
                fill_word_table(
                    template_path=template_path, output_path=output_docx,
                    records=fill_payload,
                    header_row=profile.get('header_row', 0),
                    start_row=profile.get('start_row', 1)
                )

            elif template_mode == 'vertical':
                fill_excel_vertical(template_path, output_xlsx, final_data)
            elif template_mode == 'excel_table':
                fill_excel_table(template_path=template_path, output_path=output_xlsx, records=final_data, header_row=profile.get('header_row', 1), start_row=profile.get('start_row', 2))
            elif template_mode == 'word_table':
                fill_word_table(template_path=template_path, output_path=output_docx, records=final_data, table_index=profile.get('table_index', 0), header_row=profile.get('header_row', 0), start_row=profile.get('start_row', 1))
            else:
                print(f"[WARN] 未知template_mode: {template_mode}，尝试按excel_table处理")
                fill_excel_table(template_path=template_path, output_path=output_xlsx, records=final_data, header_row=1, start_row=2)
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
                    'result_docx': output_docx if template_mode == 'word_table' else '',
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

        print('\n=== 运行耗时统计 ===')
        for k, v in runtime.items():
            print(f'{k}: {v}')
        print(f'\n已生成：{output_json}')
        if template_mode in ['vertical', 'excel_table']:
            print(f'已生成：{output_xlsx}')
        if template_mode == 'word_table':
            print(f'已生成：{output_docx}')
        print(f'已生成：{output_report_bundle_json}')
        if runtime['within_limit_seconds']:
            print(f'[OK] 总耗时在 {TARGET_LIMIT_SECONDS} 秒以内')
        else:
            print(f'[WARN] 总耗时超过 {TARGET_LIMIT_SECONDS} 秒，需要继续优化')

    except FileNotFoundError as e:
        print(f'[ERROR][file_error] {e}')
        raise
    except json.JSONDecodeError as e:
        print(f'[ERROR][json_error] JSON 解析失败：{e}')
        raise
    except ValueError as e:
        print(f'[ERROR][value_error] {e}')
        raise
    except Exception as e:
        print(f'[ERROR][unknown_error] {e}')
        raise


if __name__ == '__main__':
    main()