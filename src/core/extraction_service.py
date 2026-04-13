"""
核心抽取服务 - 从main.py提取的核心业务逻辑

职责：
1. 提供统一的文档抽取接口
2. 包含智能提示构建、分块抽取、记录合并等核心逻辑
3. 支持配置化参数，与CLI解耦
4. 为API服务和CLI提供统一的抽取能力

设计原则：
1. 单一职责：专注于抽取逻辑，不处理CLI参数或文件I/O
2. 可配置：所有参数可通过构造函数或方法参数配置
3. 可测试：易于单元测试和集成测试
4. 向后兼容：保持与现有main.py相同的接口和行为
"""

import json
import logging
import time
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path
from src.core.interfaces import IExtractionService

# 导入必要的模块
try:
    from src.config import get_config
    _config = None  # 不再使用ConfigManager，改为直接使用get_config()
except ImportError:
    # 向后兼容
    _config = None

logger = logging.getLogger(__name__)


class CoreExtractionService(IExtractionService):
    """核心抽取服务，封装从main.py提取的核心业务逻辑"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化抽取服务

        Args:
            config: 配置字典，用于覆盖默认配置
        """
        self.config = config or {}
        self._initialize_components()

    def _initialize_components(self):
        """初始化内部组件"""
        # 这里可以初始化FieldNormalizer、ModelRegistry等
        pass

    # ===== 从main.py提取的核心函数 =====

    def build_smart_prompt(self, text: str, profile: dict) -> str:
        """根据profile和文本构建抽取prompt（从main.py复制）"""
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

    def extract_with_slicing(self, text: str, profile: dict, use_model: bool = True, slice_size: int = 2000, overlap: int = 100, show_progress: bool = True, time_budget: int = 110, chunks: list = None, max_chunks: int = 50, logger=None):
        """使用切片模式进行抽取。优先使用 Docling 语义分块（chunks），回退到字符切片。
        从main.py复制的函数实现

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
        import time

        def _log(msg: str):
            if logger:
                logger.info(msg)
            else:
                # 上线默认不向 stdout 打印；统一走 logging
                logging.getLogger(__name__).info(msg)

        # llm-mode=off：严禁触发任何模型调用（包括 langextract）
        if not use_model:
            return {}, {}, {"slicing_enabled": False, "slice_count": 0, "mode": "model_disabled"}

        TIME_BUDGET_SECONDS = time_budget
        start_time = time.perf_counter()

        # 分层超时控制：为不同处理阶段分配时间预算
        # 基于用户建议：HTTP层 → CoreExtractionService层 → langextract层 → OpenAI client层
        # 这里实现CoreExtractionService层的超时控制
        LAYER_TIMEOUT_ALLOCATION = {
            "document_preprocessing": 0.05,  # 5% 文档预处理
            "langextract_extraction": 0.30,  # 30% langextract处理
            "model_chunk_processing": 0.60,  # 60% 模型分块处理
            "post_processing": 0.05,         # 5% 后处理
        }

        # 计算各阶段时间预算
        layer_time_budgets = {}
        for layer, ratio in LAYER_TIMEOUT_ALLOCATION.items():
            layer_time_budgets[layer] = TIME_BUDGET_SECONDS * ratio

        def check_timeout(layer_name: str, start_time: float) -> bool:
            """检查指定层是否超时"""
            elapsed = time.perf_counter() - start_time
            budget = layer_time_budgets.get(layer_name, TIME_BUDGET_SECONDS)
            if elapsed > budget:
                _log(f'[TIMEOUT] {layer_name}层超时: {elapsed:.1f}s > {budget:.1f}s预算')
                return True
            return False

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
                # 检查是否还有时间进行langextract处理
                elapsed_before_lx = time.perf_counter() - start_time
                remaining_total_time = max(1, TIME_BUDGET_SECONDS - elapsed_before_lx)

                # 计算langextract可用的时间预算（使用分配的比例，但不能超过剩余总时间）
                lx_time_budget = min(
                    layer_time_budgets["langextract_extraction"],
                    remaining_total_time * 0.8  # 保留20%给后续处理
                )

                if lx_time_budget < 5:  # 最少5秒
                    _log(f'[INFO] 剩余时间不足进行langextract处理: {lx_time_budget:.1f}s，跳过')
                    raise TimeoutError("时间预算不足，跳过langextract")

                _log(f'[INFO] 准备调用langextract，分配时间预算: {lx_time_budget:.1f}s (总剩余: {remaining_total_time:.1f}s)')

                from src.adapters.langextract_adapter import extract_with_langextract
                lx_start_time = time.perf_counter()
                lx_records = extract_with_langextract(
                    text_chunks, profile,
                    time_budget=lx_time_budget,
                    quiet=not show_progress,
                )
                lx_elapsed = time.perf_counter() - lx_start_time
                _log(f'[INFO] langextract处理完成，耗时: {lx_elapsed:.1f}s')

                if lx_records is not None and len(lx_records) > 0:
                    _log(f'[INFO] langextract 提取成功: {len(lx_records)} 条记录')
                    merged = {"records": lx_records}
                    return merged, merged, {
                        "slicing_enabled": False, "slice_count": total_chunks,
                        "mode": "langextract", "chunk_count": total_chunks,
                        "layer_timeouts": {
                            "langextract_seconds": lx_elapsed,
                            "remaining_budget_seconds": TIME_BUDGET_SECONDS - (time.perf_counter() - start_time)
                        }
                    }
                elif lx_records is not None:
                    _log('[INFO] langextract 返回空结果，回退到 prompt 方案')
            except TimeoutError:
                _log('[WARN] langextract 因时间不足跳过，回退到 prompt 方案')
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
                    prompt = self.build_smart_prompt(combined_text, profile)
                    from src.adapters.model_client import call_model
                    # 计算模型调用的截止时间
                    elapsed = time.perf_counter() - start_time
                    remaining_time = max(1, TIME_BUDGET_SECONDS - elapsed)
                    total_deadline = time.time() + remaining_time
                    raw = call_model(prompt, total_deadline=total_deadline)
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

            # 计算模型分块处理的可用时间预算
            elapsed_before_model = time.perf_counter() - start_time
            remaining_total_time = max(1, TIME_BUDGET_SECONDS - elapsed_before_model)

            # 模型处理阶段的时间预算（使用分配的比例，但不能超过剩余总时间）
            model_time_budget = min(
                layer_time_budgets["model_chunk_processing"],
                remaining_total_time * 0.9  # 保留10%给后处理
            )

            if model_time_budget < 10:  # 最少10秒
                _log(f'[WARN] 模型处理时间预算不足: {model_time_budget:.1f}s，跳过剩余分块')
                return {}, {}, {"slicing_enabled": True, "slice_count": 0, "mode": "timeout_skip_all"}

            _log(f'[INFO] 模型分块处理预算: {model_time_budget:.1f}s (总剩余: {remaining_total_time:.1f}s)')

            all_model_outputs = []
            model_processing_start = time.perf_counter()

            for i, chunk in enumerate(text_chunks):
                # 检查模型处理阶段是否超时
                elapsed_in_model_phase = time.perf_counter() - model_processing_start
                if elapsed_in_model_phase > model_time_budget:
                    _log(f'[TIMEOUT] 模型处理阶段超时: {elapsed_in_model_phase:.1f}s > {model_time_budget:.1f}s预算，跳过剩余 {total_chunks - i} 个块')
                    break

                # 也检查总时间预算（双重保险）
                elapsed_total = time.perf_counter() - start_time
                if elapsed_total > TIME_BUDGET_SECONDS:
                    _log(f'[WARN] 总抽取时间已达 {elapsed_total:.1f}s，跳过剩余 {total_chunks - i} 个块')
                    break
                chunk_text = chunk.get("text", "")
                if not chunk_text.strip():
                    continue
                if show_progress:
                    _log(f'[进度] 处理语义块 {i+1}/{total_chunks} ({len(chunk_text)} 字符)...')
                if use_model:
                    try:
                        prompt = self.build_smart_prompt(chunk_text, profile)
                        from src.adapters.model_client import call_model
                        # 计算模型调用的截止时间
                        elapsed_in_model_phase = time.perf_counter() - model_processing_start
                        remaining_model_time = max(1, model_time_budget - elapsed_in_model_phase)
                        total_deadline = time.time() + remaining_model_time
                        raw = call_model(prompt, total_deadline=total_deadline)
                        elapsed_after = time.perf_counter() - model_processing_start
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
                all_records = self.merge_records_by_key(all_records, key_fields)

            merged_model_output = {"records": all_records} if all_records else {}
            return merged_model_output, merged_model_output, {
                "slicing_enabled": True, "slice_count": total_chunks,
                "mode": "semantic_chunks", "chunk_count": total_chunks,
            }

        # ── 回退：字符切片模式 ─────────────────────────────────────────────────

        # 计算字符切片模式可用的时间预算
        elapsed_before_char_slicing = time.perf_counter() - start_time
        remaining_total_time = max(1, TIME_BUDGET_SECONDS - elapsed_before_char_slicing)

        # 字符切片模式的时间预算（使用模型处理阶段的比例）
        char_slice_time_budget = min(
            layer_time_budgets["model_chunk_processing"],  # 使用相同的预算分配
            remaining_total_time * 0.9  # 保留10%给后处理
        )

        if char_slice_time_budget < 10:  # 最少10秒
            _log(f'[WARN] 字符切片处理时间预算不足: {char_slice_time_budget:.1f}s，跳过处理')
            return {}, {}, {"slicing_enabled": False, "slice_count": 0, "mode": "char_slice_timeout_skip"}

        _log(f'[INFO] 字符切片模式预算: {char_slice_time_budget:.1f}s (总剩余: {remaining_total_time:.1f}s)')

        SLICE_THRESHOLD = 2000
        MAX_CHUNK_SIZE = slice_size
        OVERLAP_SIZE = overlap

        if len(text) <= SLICE_THRESHOLD:
            if use_model:
                prompt = self.build_smart_prompt(text, profile)
                from src.adapters.model_client import call_model
                # 计算模型调用的截止时间
                elapsed = time.perf_counter() - start_time
                remaining_time = max(1, TIME_BUDGET_SECONDS - elapsed)
                total_deadline = time.time() + remaining_time
                raw = call_model(prompt, total_deadline=total_deadline)
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
        char_slice_start_time = time.perf_counter()

        for i, segment in enumerate(char_chunks):
            # 检查字符切片阶段是否超时
            elapsed_in_char_slice = time.perf_counter() - char_slice_start_time
            if elapsed_in_char_slice > char_slice_time_budget:
                _log(f'[TIMEOUT] 字符切片阶段超时: {elapsed_in_char_slice:.1f}s > {char_slice_time_budget:.1f}s预算，跳过剩余 {total_segments - i} 个片段')
                break

            # 也检查总时间预算（双重保险）
            elapsed_total = time.perf_counter() - start_time
            if elapsed_total > TIME_BUDGET_SECONDS:
                _log(f'[WARN] 总抽取时间已达 {elapsed_total:.1f}s，跳过剩余 {total_segments - i} 个片段')
                break
            segment_text = segment["text"]
            if show_progress:
                _log(f'[进度] 处理第 {i+1}/{total_segments} 个片段 ({len(segment_text)} 字符)...')

            if use_model:
                try:
                    prompt = self.build_smart_prompt(segment_text, profile)
                    from src.adapters.model_client import call_model
                    # 计算模型调用的截止时间
                    elapsed_in_char_slice = time.perf_counter() - char_slice_start_time
                    remaining_char_slice_time = max(1, char_slice_time_budget - elapsed_in_char_slice)
                    total_deadline = time.time() + remaining_char_slice_time
                    raw = call_model(prompt, total_deadline=total_deadline)
                    elapsed_after = time.perf_counter() - char_slice_start_time
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

    def merge_records_by_key(self, records: List[Dict], key_fields: Optional[List[str]] = None) -> List[Dict]:
        """基于关键字段的记录融合去重（智能增强版）。

        增强功能：
        1. 自动检测关键字段（当未指定时）
        2. 基于关键字段的合并优先
        3. 基于内容相似度的二次合并（当 rapidfuzz 可用时）
        4. 保留原文顺序，清理内部标记字段

        相同键的记录进行字段级合并：新记录的非空值覆盖旧记录的空值。
        所有关键字段均为空的记录保留并打上 _unkeyed=True 标记。

        从main.py复制的函数实现
        """
        from src.core.chunk_merger import smart_merge_records
        # 使用去重配置获取阈值
        try:
            from src.core.deduplication_config import get_similarity_threshold
            threshold = get_similarity_threshold("record_merger")
        except ImportError:
            # 如果去重配置模块不可用，使用默认值0.98（保持向后兼容）
            threshold = 0.98

        # 使用智能合并函数（向后兼容）
        # 相似度阈值从配置获取，仅合并几乎完全相同的记录
        # 避免把不同实体（如不同城市）因为相同字段结构而误合并
        return smart_merge_records(records, key_fields, similarity_threshold=threshold)

    # ===== 统一抽取接口 =====

    def extract_from_text(self, text: str, profile: dict,
                          llm_mode: str = "full",
                          slice_size: int = 2000,
                          overlap: int = 100,
                          max_chunks: int = 50,
                          time_budget: int = 110,
                          quiet: bool = False) -> Dict[str, Any]:
        """从文本中提取结构化信息

        Args:
            text: 输入文本
            profile: 抽取配置文件
            llm_mode: 抽取模式，可选 "full"（全文抽取）、"supplement"（仅补充缺失字段）、"off"（仅规则抽取）
            slice_size: 字符切片大小（仅在无语义分块时使用）
            overlap: 字符切片重叠大小（仅在无语义分块时使用）
            max_chunks: 最大处理分块数
            time_budget: 时间预算（秒）
            quiet: 安静模式，禁用进度输出

        Returns:
            抽取结果字典，包含records、metadata等信息
        """
        # 根据llm_mode决定是否使用模型
        use_model = llm_mode != "off"

        # 调用extract_with_slicing（兼容现有逻辑）
        extracted_raw, model_output, slicing_metadata = self.extract_with_slicing(
            text=text,
            profile=profile,
            use_model=use_model,
            slice_size=slice_size,
            overlap=overlap,
            show_progress=not quiet,
            time_budget=time_budget,
            chunks=None,  # 由调用方提供chunks
            max_chunks=max_chunks,
            logger=None if quiet else None  # TODO: 支持logger
        )

        # 整合结果
        result = {
            "records": extracted_raw.get("records", []),
            "metadata": {
                "llm_mode": llm_mode,
                "slicing_metadata": slicing_metadata,
                "use_model": use_model,
            },
            "extracted_raw": extracted_raw,
            "model_output": model_output,
        }

        # TODO: 实现supplement模式（仅补充缺失字段）
        # 当前版本将supplement视为full，后续需要实现规则预抽取+AI补充的逻辑

        return result

    def extract_from_document(self, document_path: str, profile: dict, **kwargs) -> Dict[str, Any]:
        """从文档文件中提取结构化信息

        Args:
            document_path: 文档文件路径
            profile: 抽取配置文件
            **kwargs: 传递给extract_from_text的参数

        Returns:
            抽取结果字典
        """
        # TODO: 集成文档读取逻辑，使用ParserService
        # 临时实现：读取文本文件内容
        try:
            with open(document_path, 'r', encoding='utf-8') as f:
                text = f.read()
            return self.extract_from_text(text, profile, **kwargs)
        except UnicodeDecodeError:
            # 如果是二进制文件（如Word、Excel），需要解析器
            # 暂时抛出异常，等待ParserService实现
            raise NotImplementedError(f"文档解析尚未实现，无法处理文件: {document_path}")

    # ===== 工具方法 =====

    def _get_config(self, key: str, default: Any = None) -> Any:
        """获取配置值 - 简化版本，不使用ConfigManager"""
        # 首先检查实例配置
        if key in self.config:
            return self.config[key]

        # 尝试从src.config获取
        try:
            import src.config as config_module
            # 检查config模块是否有该属性
            if hasattr(config_module, key):
                return getattr(config_module, key)
            # 或者使用get_config函数如果存在
            if hasattr(config_module, 'get_config'):
                return config_module.get_config(key, default)
        except ImportError:
            pass

        # 最后尝试环境变量
        import os
        env_key = f"A23_{key.upper()}"
        if env_key in os.environ:
            return os.environ[env_key]

        # 返回默认值
        return default

    def _load_field_aliases(self) -> Dict[str, List[str]]:
        """加载字段别名映射"""
        try:
            from src.core.alias import load_alias_map
            return load_alias_map()
        except ImportError:
            return {}


# 全局默认实例（用于向后兼容）
_default_service = None

def get_extraction_service(config: Optional[Dict[str, Any]] = None) -> CoreExtractionService:
    """获取抽取服务实例（单例模式，用于向后兼容）"""
    global _default_service
    if _default_service is None or config is not None:
        _default_service = CoreExtractionService(config)
    return _default_service

def reset_extraction_service(config: Optional[Dict[str, Any]] = None):
    """重置抽取服务实例（主要用于测试）"""
    global _default_service
    _default_service = None if config is None else CoreExtractionService(config)


# 注册到服务注册表（用于接口模式）
try:
    from src.core.interfaces import register_extraction_service
    register_extraction_service("default", CoreExtractionService)
    register_extraction_service("core", CoreExtractionService)
except ImportError:
    # 如果interfaces模块不可用，跳过注册
    pass