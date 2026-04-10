import argparse
import json
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

from src.core.profile import generate_profile_from_template, generate_profile_smart, generate_profile_from_document
from src.config import TARGET_LIMIT_SECONDS
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
from src.core.chunk_merger import smart_merge_records


def preprocess_retrieved_chunks(chunks: list) -> list:
    """规范化 RAG 检索片段格式"""
    result = []
    for c in chunks:
        if isinstance(c, str):
            result.append({"text": c, "score": 1.0, "source": ""})
        elif isinstance(c, dict):
            result.append({"text": c.get("text", str(c)), "score": c.get("score", 1.0), "source": c.get("source", "")})
    return result


def format_retrieved_chunks(chunks: list, top_k: int = 50) -> str:
    """将检索片段格式化为 LLM 上下文字符串"""
    return "\n\n".join(c.get("text", "") for c in chunks[:top_k] if isinstance(c, dict))


def attach_field_evidence(extracted_raw: dict, retrieved_chunks: list) -> dict:
    """为每个字段附加检索来源（RAG 场景）"""
    if not retrieved_chunks or not isinstance(extracted_raw, dict):
        return {}
    evidence = {}
    records = extracted_raw.get("records", [extracted_raw])
    if records:
        first = records[0] if isinstance(records[0], dict) else {}
        for field_name in first:
            for chunk in retrieved_chunks:
                text = chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
                if str(first.get(field_name, "")) in text:
                    evidence[field_name] = {"source": chunk.get("source", ""), "excerpt": text[:200]}
                    break
    return evidence


def load_rag_json(rag_json_path: str) -> dict:
    with open(rag_json_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_retrieved_chunks_from_rag_json(rag_data: dict) -> list[dict]:
    if not isinstance(rag_data, dict):
        return []
    if isinstance(rag_data.get('retrieved_chunks'), list):
        return rag_data['retrieved_chunks']
    result = rag_data.get('result', {})
    if isinstance(result, dict) and isinstance(result.get('retrieved_chunks'), list):
        return result['retrieved_chunks']
    return []


def extract_structured_result_from_rag_json(rag_data: dict):
    if not isinstance(rag_data, dict):
        return None
    result = rag_data.get('result')
    return result if isinstance(result, dict) else None


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
    # 使用智能合并函数（向后兼容）
    # 相似度阈值设为0.98：仅合并几乎完全相同的记录
    # 避免把不同实体（如不同城市）因为相同字段结构而误合并
    return smart_merge_records(records, key_fields, similarity_threshold=0.98)


def ensure_parent_dir(path_str: str):
    parent = os.path.dirname(path_str)
    if parent:
        os.makedirs(parent, exist_ok=True)


def normalize_input_path(path: str) -> str:
    """标准化输入路径：如果路径是文件，创建临时目录并复制文件；如果是目录，直接返回

    Args:
        path: 输入路径（文件或目录）

    Returns:
        目录路径（确保是目录）
    """
    import shutil
    import tempfile

    if not os.path.exists(path):
        raise FileNotFoundError(f'路径不存在: {path}')

    if os.path.isfile(path):
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix='a23_input_')
        print(f'[INFO] 输入为文件，创建临时目录: {temp_dir}')

        # 复制文件到临时目录
        file_name = os.path.basename(path)
        dest_path = os.path.join(temp_dir, file_name)
        shutil.copy2(path, dest_path)
        print(f'[INFO] 文件已复制到临时目录: {dest_path}')

        return temp_dir
    elif os.path.isdir(path):
        return path
    else:
        raise ValueError(f'路径既不是文件也不是目录: {path}')


def summarize_for_console(data):
    import copy
    if isinstance(data, dict):
        data = copy.deepcopy(data)
        # 处理顶层records
        if isinstance(data.get('records'), list):
            records = data['records']
            if len(records) > 20:
                preview = records[:5]
                data = {**data, 'records_preview': preview, 'records_count': len(records), 'records': f'<omitted {len(records)} records on console>'}

        # 处理_table_groups中的records
        if isinstance(data.get('_table_groups'), list):
            for group in data['_table_groups']:
                if isinstance(group.get('records'), list):
                    records = group['records']
                    if len(records) > 20:
                        preview = records[:5]
                        group['records_preview'] = preview
                        group['records_count'] = len(records)
                        group['records'] = f'<omitted {len(records)} records on console>'

    return data



def build_smart_prompt(text: str, profile: dict) -> str:
    """根据profile和文本构建抽取prompt"""
    instruction = profile.get("instruction", "请根据字段要求，从文档中提取信息。")
    fields = profile.get("fields", [])
    task_mode = profile.get("task_mode", "single_record")
    template_mode = profile.get("template_mode", "")

    field_names = [item['name'] for item in fields if isinstance(item, dict)]

    # 加载字段别名映射
    field_aliases_info = {}
    try:
        from src.core.alias import load_alias_map
        alias_map = load_alias_map()
        for field in fields:
            if not isinstance(field, dict):
                continue
            fn = field['name']
            aliases = []
            if fn in alias_map:
                raw = alias_map[fn]
                aliases = raw if isinstance(raw, list) else [raw]
            for canonical, alias_list in alias_map.items():
                if isinstance(alias_list, list) and fn in alias_list:
                    aliases.append(canonical)
                elif alias_list == fn:
                    aliases.append(canonical)
            aliases = list(set(a for a in aliases if a and a != fn))
            if aliases:
                field_aliases_info[fn] = aliases
    except Exception:
        pass

    # ——— 多表格Word模式 ———
    if template_mode == "word_multi_table":
        table_specs = profile.get("table_specs", [])
        required_groups = [(s.get('filter_field', ''), s.get('filter_value', '')) for s in table_specs]
        tables_info = ""
        if table_specs:
            tables_info = "\n模板中的表格分配规则：\n" + "\n".join([
                f"  表格{s.get('table_index', i)+1}：{s.get('filter_field', '字段')}={s.get('filter_value', '?')}（{s.get('description', '')}）"
                for i, s in enumerate(table_specs)
            ])
        required_groups_str = ""
        if required_groups:
            required_groups_str = "\n\n必须包含的分组（每个分组至少要有一条记录）：\n" + "\n".join([
                f"  - {fv}（用于填写 {ff} 字段）" for ff, fv in required_groups
            ]) + "\n若文档中某分组数据缺失，仍需在records中为该分组添加记录，城市字段填入分组名，其余字段留空字符串。"

        field_descs = [f'{fn}（别名：{", ".join(field_aliases_info[fn])}）' if fn in field_aliases_info else fn for fn in field_names]
        example_records = [{fn: f"示例{fn}{i+1}" for fn in field_names} for i in range(3)]
        return f"""你是一个严格的信息抽取助手。请从文档中提取所有分组记录并按JSON格式输出。

用户指令：{instruction}
{tables_info}{required_groups_str}

必须提取的字段（字段名必须精确匹配）：
{json.dumps(field_descs, ensure_ascii=False, indent=2)}

重要要求：
1. 模板有多个表格，每个表格对应不同的分组（如不同城市）
2. 请提取文档中所有分组的所有记录，不要遗漏任何一组
3. 每条记录都必须包含所有指定字段，缺失字段用空字符串""
4. 字段值直接从文档获取，保持原始格式
5. 若文档中找不到某分组数据，仍需为该分组输出记录（分组名填入对应字段，其余留空）

输出格式示例：
{json.dumps({"records": example_records}, ensure_ascii=False, indent=2)}

文档内容：
{text}

只输出JSON："""

    # ——— 多记录表格模式 ———
    if task_mode == "table_records":
        field_descs = [f'{fn}（别名：{", ".join(field_aliases_info[fn])}）' if fn in field_aliases_info else fn for fn in field_names]
        example_records = [{fn: f"示例{fn}{i+1}" for fn in field_names} for i in range(3)]
        estimated_count = max(1, len(text) // 200)
        return f"""你是一个严格的信息抽取助手，必须完全按照要求的格式输出。

用户指令：{instruction}

必须提取的字段（字段名必须精确匹配，括号内是可能出现的别名）：
{json.dumps(field_descs, ensure_ascii=False, indent=2)}

【重要约束——必须遵守】
1. 你必须提取文档中所有符合条件的记录，不能只输出前几条示例。
2. 如果文档中有表格，请逐行处理每一行（从表头后的第一行开始，直到最后一行）。
3. 如果文档中有编号列表（如 1. ... 2. ...），也请逐条提取。
4. 输出 records 数组的长度应当等于文档中的实际记录条数，宁可多输出，也不要遗漏。
5. 文档字符数约为 {len(text)} 字，预估记录数约为 {estimated_count} 条，请参考该数量。
6. 每条记录应包含所有指定字段，找不到的字段使用空字符串""。
7. 字段值应直接从文档中获取，保持原始格式。

输出格式（必须包含"records"键）：
{json.dumps({"records": example_records}, ensure_ascii=False, indent=2)}

文档内容：
{text}

现在开始抽取，只输出JSON："""

    # ——— 单记录模式 ———
    field_descs = [f'{fn}（别名：{", ".join(field_aliases_info[fn])}）' if fn in field_aliases_info else fn for fn in field_names]
    example_json = {fn: "示例值" for fn in field_names}
    return f"""你是一个严格的信息抽取助手，必须完全按照要求的格式输出。

用户指令：{instruction}

必须提取的字段（字段名必须精确匹配，括号内是可能出现的别名）：
{json.dumps(field_descs, ensure_ascii=False, indent=2)}

输出要求：
1. 只输出一个JSON对象，包含上述所有字段
2. JSON键名必须与字段名完全一致
3. 找不到字段内容时使用空字符串""
4. 不要添加任何额外字段

输出格式示例：
{json.dumps(example_json, ensure_ascii=False, indent=2)}

文档内容：
{text}

现在开始抽取，只输出JSON："""


def extract_with_slicing(text: str, profile: dict, use_model: bool = True, slice_size: int = 2000, overlap: int = 100, show_progress: bool = True, time_budget: int = 110, chunks: list = None, max_chunks: int = 50, logger=None):
    """使用切片模式进行抽取。优先使用 Docling 语义分块（chunks），回退到字符切片。

    Args:
        text: 完整文档文本
        profile: 模板配置
        use_model: 是否使用模型抽取
        slice_size: 字符切片大小（仅在无 chunks 时使用）
        overlap: 字符切片重叠大小（仅在无 chunks 时使用）
        show_progress: 是否显示进度信息
        time_budget: 最大允许耗时（秒）
        chunks: Docling 语义分块列表（每个元素含 type 和 text 字段）
        max_chunks: 最多处理的 chunk 数量
        logger: 可选的 Python logger 实例

    Returns:
        extracted_raw: 抽取结果字典
        model_output: 模型输出字典
        slicing_metadata: 切片处理的元数据
    """
    import json

    def _log(msg: str):
        if logger:
            logger.info(msg)
        else:
            print(msg)

    TIME_BUDGET_SECONDS = time_budget

    # ── 优先使用 Docling 语义分块 ──────────────────────────────────────────
    if chunks:
        # 过滤掉表格类型的 chunk（表格已通过直读路径处理）
        text_chunks = [c for c in chunks if c.get("type") != "table"]
        # 限制处理数量
        if len(text_chunks) > max_chunks:
            _log(f'[INFO] 语义分块数 {len(text_chunks)} 超过 max_chunks={max_chunks}，截断处理')
            text_chunks = text_chunks[:max_chunks]

        if not text_chunks:
            # 所有 chunk 均为表格，无文本需处理
            return {}, {}, {"slicing_enabled": False, "slice_count": 0, "mode": "chunks_skipped_all_tables"}

        total_chunks = len(text_chunks)

        # ── 优先尝试 langextract（自动结构化提取） ──
        try:
            from src.adapters.langextract_adapter import extract_with_langextract
            lx_records = extract_with_langextract(
                text_chunks, profile,
                time_budget=TIME_BUDGET_SECONDS,
                quiet=not show_progress,
            )
            if lx_records is not None and len(lx_records) > 0:
                _log(f'[INFO] langextract 提取成功: {len(lx_records)} 条记录')
                merged = {"records": lx_records}
                return merged, merged, {
                    "slicing_enabled": False, "slice_count": total_chunks,
                    "mode": "langextract", "chunk_count": total_chunks,
                }
            elif lx_records is not None:
                _log('[INFO] langextract 返回空结果，回退到 prompt 方案')
        except Exception as e:
            _log(f'[WARN] langextract 不可用: {e}，使用 prompt 方案')

        # ── 回退：手动分块 + prompt + call_model ──

        # 基于总字符数决定是否合并：4000字符以下合并处理，以上逐块处理
        total_chars = sum(len(c.get("text", "")) for c in text_chunks)
        combine_threshold = 4000  # 约1000 token，7B模型的安全区间

        if total_chars <= combine_threshold:
            # 文本量小：拼接后整体处理
            combined_text = "\n\n".join(c.get("text", "") for c in text_chunks)
            _log(f'[INFO] 语义块总量 {total_chars} 字符 ≤ {combine_threshold}，合并为单次请求')
            if use_model:
                prompt = build_smart_prompt(combined_text, profile)
                raw = call_model(prompt)
                if isinstance(raw, dict) and "records" in raw:
                    model_output = raw
                elif isinstance(raw, dict):
                    model_output = {"records": [raw]}
                else:
                    model_output = {"records": []}
            else:
                model_output = {}
            extracted_raw = model_output
            return extracted_raw, model_output, {
                "slicing_enabled": False, "slice_count": 1, "mode": "chunks_combined",
                "chunk_count": total_chunks
            }

        # 文本量大：逐块处理，每块独立提取后合并
        _log(f'[INFO] 语义分块模式：共 {total_chunks} 个文本块，{total_chars} 字符，逐块处理')
        all_model_outputs = []
        slice_start_time = time.perf_counter()

        for i, chunk in enumerate(text_chunks):
            elapsed = time.perf_counter() - slice_start_time
            if elapsed > TIME_BUDGET_SECONDS:
                _log(f'[WARN] 抽取时间已达 {elapsed:.1f}s，跳过剩余 {total_chunks - i} 个块')
                break
            chunk_text = chunk.get("text", "")
            if not chunk_text.strip():
                continue
            if show_progress:
                _log(f'[进度] 处理语义块 {i+1}/{total_chunks} ({len(chunk_text)} 字符)...')
            if use_model:
                try:
                    prompt = build_smart_prompt(chunk_text, profile)
                    raw = call_model(prompt)
                    elapsed_after = time.perf_counter() - slice_start_time
                    _log(f'[INFO] 块 {i+1} 模型调用完成 (累计 {elapsed_after:.1f}s)')
                    if isinstance(raw, dict) and "records" in raw:
                        seg_output = raw
                    elif isinstance(raw, dict):
                        seg_output = {"records": [raw]}
                    else:
                        seg_output = {"records": []}
                    all_model_outputs.append(seg_output)
                except TimeoutError:
                    _log(f'[WARN] 块 {i+1} 超时，返回已收集结果')
                    break
                except Exception as e:
                    _log(f'[WARN] 块 {i+1} 抽取失败: {e}')
                    all_model_outputs.append({"records": []})

        all_records = []
        field_names = [f['name'] for f in profile.get('fields', []) if isinstance(f, dict)]
        for out in all_model_outputs:
            if isinstance(out, dict) and "records" in out:
                chunk_recs = out["records"]
            elif isinstance(out, dict) and out:
                chunk_recs = [out]
            else:
                chunk_recs = []
            # 展平嵌套JSON（LLM可能返回 {"城市A": {...}, "城市B": {...}} 而非 records 数组）
            if chunk_recs and field_names:
                from src.core.postprocess import _flatten_nested_records
                chunk_recs = _flatten_nested_records(chunk_recs, field_names)
            all_records.extend(chunk_recs)

        # 关键字段去重（从 profile 读取 dedup_key_fields）
        key_fields = profile.get("dedup_key_fields") or None
        if all_records:
            all_records = merge_records_by_key(all_records, key_fields)

        merged_model_output = {"records": all_records} if all_records else {}
        return merged_model_output, merged_model_output, {
            "slicing_enabled": True, "slice_count": total_chunks,
            "mode": "semantic_chunks", "chunk_count": total_chunks,
        }

    # ── 回退：字符切片模式 ─────────────────────────────────────────────────
    SLICE_THRESHOLD = 2000
    MAX_CHUNK_SIZE = slice_size
    OVERLAP_SIZE = overlap

    if len(text) <= SLICE_THRESHOLD:
        if use_model:
            prompt = build_smart_prompt(text, profile)
            raw = call_model(prompt)
            if isinstance(raw, dict) and "records" in raw:
                model_output = raw
            elif isinstance(raw, dict):
                model_output = {"records": [raw]}
            else:
                model_output = {"records": []}
        else:
            model_output = {}
        extracted_raw = model_output
        return extracted_raw, model_output, {"slicing_enabled": False, "slice_count": 1, "mode": "direct"}

    # 需要切片
    _log(f'[INFO] 文档内容过长 ({len(text)} 字符)，启用字符切片模式')
    _log(f'[INFO] 切片配置: 阈值={SLICE_THRESHOLD}, 分块大小={MAX_CHUNK_SIZE}, 重叠={OVERLAP_SIZE}')

    # 生成字符切片
    char_chunks = []
    start = 0
    while start < len(text):
        end = min(start + MAX_CHUNK_SIZE, len(text))
        char_chunks.append({"text": text[start:end], "metadata": {"start": start, "end": end}})
        if end >= len(text):
            break
        start = end - OVERLAP_SIZE

    _log(f'[INFO] 文档已切分为 {len(char_chunks)} 个片段')

    all_model_outputs = []
    total_segments = len(char_chunks)
    slice_start_time = time.perf_counter()

    for i, segment in enumerate(char_chunks):
        elapsed = time.perf_counter() - slice_start_time
        if elapsed > TIME_BUDGET_SECONDS:
            _log(f'[WARN] 抽取时间已达 {elapsed:.1f}s，跳过剩余 {total_segments - i} 个片段（已覆盖前 {i} 个）')
            break
        segment_text = segment["text"]
        if show_progress:
            _log(f'[进度] 处理第 {i+1}/{total_segments} 个片段 ({len(segment_text)} 字符)...')

        if use_model:
            try:
                prompt = build_smart_prompt(segment_text, profile)
                raw = call_model(prompt)
                elapsed_after = time.perf_counter() - slice_start_time
                _log(f'[INFO] 片段 {i+1} 模型调用完成 (累计 {elapsed_after:.1f}s)')
                if isinstance(raw, dict) and "records" in raw:
                    seg_output = raw
                elif isinstance(raw, dict):
                    seg_output = {"records": [raw]}
                else:
                    seg_output = {"records": []}
                all_model_outputs.append(seg_output)
                _log(f'[INFO] 片段 {i+1} 抽取完成，获取 {len(seg_output.get("records", []))} 条记录')
            except TimeoutError:
                _log(f'[WARN] 片段 {i+1} 超时，返回已收集结果')
                break
            except Exception as e:
                _log(f'[WARN] 片段 {i+1} 抽取失败: {e}')
                all_model_outputs.append({"records": []})

    all_records = []
    field_names = [f['name'] for f in profile.get('fields', []) if isinstance(f, dict)]
    for model_out in all_model_outputs:
        if isinstance(model_out, dict) and "records" in model_out:
            chunk_recs = model_out["records"]
        elif isinstance(model_out, dict) and model_out:
            chunk_recs = [model_out]
        else:
            chunk_recs = []
        # 展平嵌套JSON
        if chunk_recs and field_names:
            from src.core.postprocess import _flatten_nested_records
            chunk_recs = _flatten_nested_records(chunk_recs, field_names)
        all_records.extend(chunk_recs)

    merged_model_output = {"records": all_records} if all_records else {}
    extracted_raw = merged_model_output

    slicing_metadata = {
        "slicing_enabled": True,
        "slice_threshold": SLICE_THRESHOLD,
        "slice_count": len(char_chunks),
        "max_chunk_size": MAX_CHUNK_SIZE,
        "overlap_size": OVERLAP_SIZE,
        "mode": "char_slice",
    }

    return extracted_raw, merged_model_output, slicing_metadata


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
    parser.add_argument('--llm-mode', type=str, default='full', choices=['full', 'supplement'],
                       help='LLM抽取模式：full=始终全文抽取（默认），supplement=仅补充缺失字段')
    parser.add_argument('--total-timeout', type=int, default=180,
                       help='整体抽取最大允许时间（秒，默认180）')
    parser.add_argument('--output-basename', type=str, default='',
                       help='输出文件basename（默认为空，使用输入文件名）')

    args = parser.parse_args()

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

    # 模型可用性检查（必须有可用模型才能运行）
    print('[INFO] 检查模型可用性...')
    try:
        test_prompt = "请回复一个简单的JSON：{\"status\": \"ok\"}"
        test_result = call_model(test_prompt)
        print('[INFO] 模型可用性检查通过')
    except Exception as e:
        print(f'[ERROR] 模型不可用: {e}')
        print('[ERROR] 系统需要可用的AI模型才能运行。请配置以下任一模型：')
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

    # 确定profile保存路径
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

        with open(profile_path, 'w', encoding='utf-8') as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
        runtime['generate_profile_seconds'] = round(time.perf_counter() - step_start, 3)

        print('=== 自动生成的 profile ===')
        print(json.dumps(profile, ensure_ascii=False, indent=2))
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
            extracted_raw, model_output, slicing_metadata = extract_with_slicing(
                text=context_for_llm,
                profile=profile,
                use_model=True,
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
            print(json.dumps(summarize_for_console(model_output), ensure_ascii=False, indent=2))

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
        print(json.dumps(summarize_for_console(final_data), ensure_ascii=False, indent=2))

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
                'profile_path': profile_path,
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