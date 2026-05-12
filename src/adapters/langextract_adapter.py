"""将 langextract 接入切片抽取流水线：批量结构化抽取、并行度与后端探测。

云 API 与较强本地模型启用 langextract；轻量本地模型改用直接提示以降低延迟与资源占用。
"""

from __future__ import annotations

import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import Any, Dict, List, Optional
from src.core.debug_flags import is_debug_enabled
from src.config import config_manager

_model_registry = None

logger = logging.getLogger(__name__)

def _dprint(msg: str):
    if is_debug_enabled():
        logger.debug(msg)

# 优先使用本地修改版的 langextract
third_party_path = os.path.join(os.path.dirname(__file__), '..', '..', 'third_party')

# 配置获取辅助函数
def _get_config(key: str, default: Any = None) -> Any:
    """统一配置读取（经 src.config）。"""
    return config_manager.get(key, default)

def _get_config_int(key: str, default: int = 0) -> int:
    """获取整数配置值"""
    return config_manager.get_int(key, default)

def _get_config_float(key: str, default: float = 0.0) -> float:
    """获取浮点数配置值"""
    return config_manager.get_float(key, default)

def _get_config_bool(key: str, default: bool = False) -> bool:
    """获取布尔值配置值"""
    return config_manager.get_bool(key, default)
if os.path.exists(third_party_path):
    sys.path.insert(0, third_party_path)
    logger.debug(f"添加第三方库路径: {third_party_path}")

# 懒加载标记
_langextract_ready = None  # None=未检查, True=可用, False=不可用



def _check_langextract():
    """检查 langextract 是否可用"""
    global _langextract_ready
    if _langextract_ready is not None:
        return _langextract_ready
    try:
        _dprint("尝试导入 langextract ...")
        import langextract  # noqa: F401
        from langextract.data import ExampleData, Extraction  # noqa: F401
        _langextract_ready = True
        logger.info("langextract 可用")
        _dprint("langextract 导入成功")
    except ImportError as e:
        _langextract_ready = False
        logger.info("langextract 未安装，将使用 prompt 方案")
        _dprint(f"langextract ImportError: {e}")
    return _langextract_ready


def _get_model_size_hint(model_name: str) -> int:
    """从模型名称推测参数量（单位：B），用于判断是否适合 langextract。

    若存在 ModelRegistry 则取其登记值；否则用语义正则与已知前缀表推断。
    """
    if _model_registry is not None:
        try:
            size = _model_registry.get_model_size(model_name)
            return size
        except Exception as e:
            logger.debug(f"ModelRegistry获取模型大小失败 {model_name}: {e}")

    m = re.search(r'(\d+)[bB]', model_name)
    if m:
        return int(m.group(1))
    # 常见模型的参数量映射
    known = {
        "qwen2.5": 7, "qwen2": 7, "llama3": 8, "gemma2": 9,
        "mistral": 7, "phi3": 3, "codellama": 7,
    }
    for prefix, size in known.items():
        if prefix in model_name.lower():
            return size
    return 7  # 默认假设 7B


def _create_langextract_model(model_type: str):
    """根据模型类型创建 langextract 模型实例

    Returns:
        (model_instance, model_id, is_cloud, model_size_b)
    """
    from src.adapters.openai_compatible_chat import normalize_chat_base_url
    from src.adapters.provider_env import default_chat_provider_dict, is_langextract_openai_compatible

    model_type = model_type.lower()
    _dprint(f"_create_langextract_model: model_type='{model_type}'")

    if is_langextract_openai_compatible(model_type):
        from langextract.providers.openai import OpenAILanguageModel

        provider_workers = 1
        cfg = default_chat_provider_dict(model_type)
        api_key = str(cfg.get("api_key") or "").strip()
        raw_base = str(cfg.get("base_url") or "").strip()
        base_url = normalize_chat_base_url(model_type, raw_base)
        model_name = str(cfg.get("model") or "").strip()
        model_size = (
            _get_model_size_hint(model_name)
            if _model_registry is None
            else _model_registry.get_model_size(model_name)
        )

        _dprint(
            "create OpenAI-compatible model: "
            f"model_name={model_name}, base_url={base_url}, "
            f"provider_workers={provider_workers}, "
            f"api_key_prefix={(api_key[:5] if api_key else 'None')}"
        )

        model = OpenAILanguageModel(
            model_id=model_name,
            api_key=api_key,
            base_url=base_url,
            max_workers=provider_workers,
        )
        return model, model_name, True, model_size

    if model_type == "ollama":
        from langextract.providers.ollama import OllamaLanguageModel
        model_name = _get_config("OLLAMA_MODEL", "qwen2.5:7b").strip()
        model_url = _get_config("OLLAMA_URL", "http://127.0.0.1:11434").strip()
        size = _get_model_size_hint(model_name)
        _dprint(f"create Ollama model: model_name={model_name}, model_url={model_url}, size={size}B")
        model = OllamaLanguageModel(model_id=model_name, model_url=model_url)
        return model, model_name, False, size

    raise ValueError(f"langextract 不支持的 MODEL_TYPE: {model_type}")


def _build_example_from_profile(profile: dict) -> Any:
    """从 profile 生成 langextract ExampleData，优先使用真实示例"""
    _dprint(f"_build_example_from_profile: keys={list(profile.keys())}")
    if "_example" in profile:
        _dprint(f"_build_example_from_profile: has _example")
    if "_example_text" in profile:
        _dprint(f"_build_example_from_profile: _example_text_len={len(profile['_example_text'])}")
    from langextract.data import ExampleData, Extraction

    # 使用规则化示例生成，避免在缺少稳定真实样本时引入不确定性。
    fields = profile.get("fields", [])
    if not fields:
        return None

    example_parts = []
    attributes = {}
    for f in fields:
        if not isinstance(f, dict):
            continue
        name = f.get("name", "")
        unit = f.get("unit", "")
        ftype = f.get("type", "text")

        if ftype in ("number", "money", "percentage", "area", "speed", "weight") or unit:
            if ftype == "percentage":
                example_val = "15.3%"
                text_val = "15.3%"
            elif ftype == "money":
                example_val = "12345.67"
                text_val = f"12,345.67 {unit}" if unit else "12,345.67 元"
            else:
                example_val = "12345.67"
                text_val = f"12,345.67 {unit}" if unit else "12,345.67"
        elif ftype == "date":
            example_val = "2025-01-01"
            text_val = "2025年1月1日"
        else:
            example_val = f"示例{name}"
            text_val = f"示例{name}"

        example_parts.append(f"{name}为{text_val}")
        attributes[name] = example_val

    example_text = "，".join(example_parts) + "。"
    first_field = fields[0].get("name", "数据")
    extraction_text = attributes.get(first_field, "示例")
    _dprint(f"_build_example_from_profile: rule example extraction_text_type={type(extraction_text)}")

    return ExampleData(
        text=example_text,
        extractions=[
            Extraction(
                extraction_class="record",
                extraction_text=extraction_text,
                attributes=attributes,
            )
        ],
    )


def _build_resolver_params(is_cloud: bool) -> Dict[str, Any]:
    """
    构建 langextract resolver 对齐参数。
    - 云模型：适度提升对齐阈值，抑制宽松匹配导致的语义上卷。
    - 本地模型：保持更保守的默认。
    """
    threshold = 0.88 if is_cloud else 0.82
    return {
        "enable_fuzzy_alignment": True,
        "fuzzy_alignment_threshold": threshold,
        "accept_match_lesser": False,
        "suppress_parse_errors": True,
    }


def _optimize_chunks(text_chunks: List[Dict], is_cloud: bool, quiet: bool = False) -> List[Dict]:
    """优化分块策略，避免双重分块

    规则：
    1. 保持分块类型一致（不合并不同类型）
    2. 根据模型类型设置目标大小（云API: 3500，本地: 1500）
    3. 按顺序合并小分块，直到接近目标大小

    Returns:
        优化后的分块列表
    """
    if not text_chunks:
        return []

    target_size = 3500 if is_cloud else 1500

    # 按类型分组
    chunks_by_type = {}
    for chunk in text_chunks:
        chunk_type = chunk.get("type", "text")
        if chunk_type not in chunks_by_type:
            chunks_by_type[chunk_type] = []
        chunks_by_type[chunk_type].append(chunk)

    optimized = []

    for chunk_type, chunks in chunks_by_type.items():
        current_batch = []
        current_length = 0

        for chunk in chunks:
            text = chunk.get("text", "")
            text_len = len(text)

            # 如果当前批次为空，或者合并后仍小于目标大小，则加入批次
            if current_length + text_len <= target_size:
                current_batch.append(chunk)
                current_length += text_len
            else:
                # 当前批次已满，合并并创建新批次
                if current_batch:
                    merged_text = "\n\n".join([c.get("text", "") for c in current_batch])
                    optimized.append({"type": chunk_type, "text": merged_text})

                # 开始新批次
                current_batch = [chunk]
                current_length = text_len

        # 处理最后一批
        if current_batch:
            merged_text = "\n\n".join([c.get("text", "") for c in current_batch])
            optimized.append({"type": chunk_type, "text": merged_text})

    if not quiet:
        original_count = len(text_chunks)
        optimized_count = len(optimized)
        if optimized_count < original_count:
            logger.info("分块优化: %s → %s 块 (目标大小: %s)", original_count, optimized_count, target_size)

    return optimized


def _extract_with_langextract_direct(
    text_chunks: List[Dict],
    profile: dict,
    model_instance,
    model_id: str,
    is_cloud: bool,
    quiet: bool = False,
) -> Optional[List[Dict]]:
    """原始 langextract 直接提取逻辑（不包含策略选择）"""
    try:
        import langextract as lx

        if not quiet:
            logger.debug(
                "_extract_with_langextract_direct 开始: 输入块数=%s, model=%s",
                len(text_chunks),
                model_id,
            )

        # 1. 生成 example
        example = _build_example_from_profile(profile)
        if example is None:
            logger.warning("无法从 profile 生成 langextract 示例")
            return None

        # 2. 构造提取描述
        fields = profile.get("fields", [])
        field_names = [f["name"] for f in fields if isinstance(f, dict)]
        instruction = profile.get("instruction", "提取结构化信息")
        task_mode = profile.get("task_mode", "single_record")

        if task_mode == "table_records":
            prompt_desc = (
                f"{instruction}\n"
                f"请提取文本中每一个独立实体/条目的以下字段：{', '.join(field_names)}。\n"
                f"每个实体提取为一条独立记录，必须提取全部记录，不能遗漏。"
            )
        else:
            prompt_desc = f"{instruction}\n提取字段：{', '.join(field_names)}"

        # 3. 优化分块，避免双重分块
        optimized_chunks = _optimize_chunks(text_chunks, is_cloud, quiet)

        # 4. 合并文本块
        text_parts = []
        chunk_types = {}
        for chunk in optimized_chunks:
            t = chunk.get("text", "")
            if not t.strip():
                continue
            text_parts.append(t)
            # 统计块类型
            chunk_type = chunk.get("type", "text")
            chunk_types[chunk_type] = chunk_types.get(chunk_type, 0) + 1
        full_text = "\n\n".join(text_parts)

        if not quiet:
            type_info = ", ".join([f"{k}:{v}" for k, v in chunk_types.items()])
            logger.info(
                "合并 %s 个文本块，总长度 %s 字符，类型分布: %s",
                len(text_parts),
                len(full_text),
                type_info,
            )

        if not full_text.strip():
            return []

        # 5. 构造调用参数
        # 关键策略：
        # - 强制关闭进度条，避免多线程 / 非交互环境阻塞
        # - extraction_passes 先固定 1，先追求稳定
        # - batch_length 收缩，减少内部复杂度
        extract_kwargs = {
            "text_or_documents": full_text,
            "prompt_description": prompt_desc,
            "examples": [example],
            "model": model_instance,
            "show_progress": False,
            "temperature": 0.0,
            "fence_output": True,
            "use_schema_constraints": False,
            "max_char_buffer": 3000 if is_cloud else 1800,
            "extraction_passes": 1,
            "context_window_chars": 300 if is_cloud else 150,
            "batch_length": 5 if is_cloud else 4,
            "resolver_params": _build_resolver_params(is_cloud),
        }

        if not quiet:
            backend = "cloud" if is_cloud else "local"
            logger.info(
                "langextract 提取开始: model=%s (%s), 文本长度=%s, show_progress=%s, extraction_passes=%s, batch_length=%s",
                model_id,
                backend,
                len(full_text),
                extract_kwargs["show_progress"],
                extract_kwargs["extraction_passes"],
                extract_kwargs["batch_length"],
            )

        # 6. 调用
        try:
            result = lx.extract(**extract_kwargs)
        except Exception as e:
            import traceback
            logger.error("langextract 提取异常: %s", e)
            logger.debug("extract_kwargs keys: %s", list(extract_kwargs.keys()))
            logger.debug("example type: %s", type(example))
            if hasattr(example, "extractions"):
                logger.debug("example extractions: %s", example.extractions)
            logger.debug("异常堆栈: %s", traceback.format_exc())
            return None

        # 7. 转换结果
        records = _convert_result_to_records(result, field_names)

        if not quiet:
            logger.info("langextract 提取完成: %s 条记录", len(records))

        return records

    except Exception as e:
        logger.warning(f"langextract 直接提取失败: {e}")
        import traceback
        logger.warning(f"异常堆栈: {traceback.format_exc()}")
        return None


def _align_records_to_fields(records: List[Dict], field_names: List[str]) -> List[Dict]:
    """将记录对齐到指定字段名，确保字段顺序和完整性"""
    aligned = []
    for record in records:
        if not isinstance(record, dict):
            continue
        aligned_record = {}
        for field in field_names:
            # 尝试多种匹配方式
            value = ""
            # 1. 精确匹配
            if field in record:
                value = record[field]
            else:
                # 2. 模糊匹配：字段名包含关系
                for key, val in record.items():
                    if field in key or key in field:
                        value = val
                        break
            aligned_record[field] = value if value is not None else ""
        aligned.append(aligned_record)
    return aligned


def extract_with_langextract(
    text_chunks: List[Dict],
    profile: dict,
    time_budget: Optional[float] = None,
    quiet: bool = False,
) -> Optional[List[Dict]]:
    """使用自适应策略从文本块列表中提取结构化记录。

    稳定性约定：不改写进程级环境变量；云 API 场景外层并行、provider 内 ``max_workers=1``；
    batch API 策略未启用时与 ``parallel`` 一致。

    Returns:
        成功时为记录列表；失败、跳过或小模型短路时为 ``None``（上层改用 prompt 路径）。
    """
    _dprint(f"extract_with_langextract called: chunks={len(text_chunks)}, quiet={quiet}")
    if not _check_langextract():
        return None

    try:
        # 1. 获取模型
        model_type = _get_config("MODEL_TYPE", "deepseek").strip().lower()
        _dprint(f"extract_with_langextract: model_type='{model_type}'")
        model_instance, model_id, is_cloud, model_size = _create_langextract_model(model_type)
        _dprint(f"extract_with_langextract: model_id={model_id}, is_cloud={is_cloud}, model_size={model_size}B")

        # 2. 本地小模型跳过 langextract，由上层使用 prompt 路径
        if not is_cloud and model_size < 14:
            if not quiet:
                logger.info("本地 %sB 模型，使用 prompt 方案（更高效）", model_size)
            _dprint("skip langextract for small local model")
            return None

        # 3. 选择策略
        strategy_config = get_optimal_strategy(text_chunks, profile)
        strategy = strategy_config["strategy"]
        max_workers = strategy_config["max_workers"]
        chunk_count = strategy_config["chunk_count"]

        if not quiet:
            logger.info("策略选择: %s, 块数: %s, 并发: %s", strategy, chunk_count, max_workers)

        # 4. 执行
        records = None

        if strategy == "single":
            records = _extract_with_langextract_direct(
                text_chunks, profile, model_instance, model_id, is_cloud, quiet
            )
        elif strategy == "parallel":
            records = extract_with_langextract_parallel(
                text_chunks, profile, max_workers, quiet, time_budget=time_budget
            )
        else:
            # batch 策略未启用，与 parallel 共用实现
            records = extract_with_langextract_parallel(
                text_chunks, profile, max_workers, quiet, time_budget=time_budget
            )

        # 5. 结果处理
        if records:
            deduped = deduplicate_records(records)
            if len(deduped) < len(records):
                if not quiet:
                    logger.info("去重: %s -> %s 条记录", len(records), len(deduped))
                records = deduped

            fields = profile.get("fields", [])
            field_names = [f["name"] for f in fields if isinstance(f, dict)]
            if field_names and records:
                records = _align_records_to_fields(records, field_names)

        return records

    except Exception as e:
        logger.warning("自适应提取失败，上层将改用 prompt 路径: %s", e)
        return None


def deduplicate_records(records: List[Dict]) -> List[Dict]:
    """基于关键字段去重

    Args:
        records: 记录列表

    Returns:
        去重后的记录列表
    """
    if not records:
        return []

    seen = set()
    unique = []
    for record in records:
        if not isinstance(record, dict):
            continue
        # 使用字段值的组合作为去重键
        key = tuple(sorted((k, str(v)) for k, v in record.items())) if record else None
        if key and key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def extract_with_langextract_parallel(
    text_chunks: List[Dict],
    profile: dict,
    max_workers: int = 2,
    quiet: bool = False,
    time_budget: Optional[float] = None,
) -> Optional[List[Dict]]:
    """并行版本的多分块提取

    关键改动：
    - 不再在线程里递归调用 extract_with_langextract()
    - 每个线程独立创建模型实例，避免共享 client 的线程安全问题
    - 外层并行，内层 provider 并行固定为 1
    """
    model_type = _get_config("MODEL_TYPE", "deepseek").strip().lower()

    # 单块时直接走最小闭环
    if len(text_chunks) <= 1:
        model_instance, model_id, is_cloud, _ = _create_langextract_model(model_type)
        return _extract_with_langextract_direct(
            text_chunks, profile, model_instance, model_id, is_cloud, quiet=quiet
        )

    if not quiet:
        logger.info("启动并行提取: %s 块, 并发数=%s", len(text_chunks), max_workers)

    results = []
    failed_chunks = []
    lock = threading.Lock()
    start_time = time.time()

    def process_chunk(idx: int, chunk: dict):
        try:
            # 每个线程各自创建模型实例
            model_instance, model_id, is_cloud, _ = _create_langextract_model(model_type)
            chunk_result = _extract_with_langextract_direct(
                [chunk], profile, model_instance, model_id, is_cloud, quiet=True
            )

            if chunk_result:
                with lock:
                    results.extend(chunk_result)
                if not quiet:
                    logger.info("并行块 %s/%s 完成，提取 %s 条", idx + 1, len(text_chunks), len(chunk_result))
            else:
                with lock:
                    failed_chunks.append(idx)

        except Exception as e:
            with lock:
                failed_chunks.append(idx)
            if not quiet:
                logger.warning("并行块 %s 失败: %s", idx + 1, e)

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(process_chunk, i, chunk)
                for i, chunk in enumerate(text_chunks)
            ]
            if time_budget is not None and float(time_budget) > 0:
                done, not_done = wait(futures, timeout=float(time_budget))
                if not_done and not quiet:
                    logger.warning(
                        "LangExtract 并行在 %.1fs 内未全部完成，取消剩余 %s 个任务",
                        float(time_budget),
                        len(not_done),
                    )
                for f in not_done:
                    f.cancel()
                for f in futures:
                    if f.done():
                        try:
                            f.result()
                        except Exception:
                            pass
            else:
                for future in as_completed(futures):
                    future.result()

        merged_results = deduplicate_records(results)

        elapsed = time.time() - start_time
        if not quiet:
            success_count = len(text_chunks) - len(failed_chunks)
            logger.info(
                "并行完成: %s/%s 块成功，共 %s 条记录，耗时 %.1f 秒",
                success_count,
                len(text_chunks),
                len(merged_results),
                elapsed,
            )

        if merged_results:
            return merged_results

        # 并行无结果时串行再试一次
        if not quiet:
            logger.warning("并行结果为空，改用串行 langextract")
        model_instance, model_id, is_cloud, _ = _create_langextract_model(model_type)
        return _extract_with_langextract_direct(
            text_chunks, profile, model_instance, model_id, is_cloud, quiet=quiet
        )

    except Exception as e:
        if not quiet:
            logger.warning("并行提取失败，改用串行 langextract: %s", e)
        model_instance, model_id, is_cloud, _ = _create_langextract_model(model_type)
        return _extract_with_langextract_direct(
            text_chunks, profile, model_instance, model_id, is_cloud, quiet=quiet
        )


def extract_with_batch_api(
    text_chunks: List[Dict],
    profile: dict,
    quiet: bool = False,
) -> Optional[List[Dict]]:
    """云 API 批量多块的实验性保留接口；策略未启用 batch 时不会进入此函数。

    生产稳定路径为 parallel。

    Args:
        text_chunks: 文本块列表
        profile: 抽取配置文件
        quiet: 安静模式

    Returns:
        提取的记录列表；不适用或失败时为 ``None``（由上层选择 parallel / prompt）。
    """
    # 仅当使用云 API 且块数 > 1 时启用
    from src.adapters.provider_env import is_langextract_openai_compatible

    model_type = _get_config("MODEL_TYPE", "deepseek").strip().lower()
    is_cloud = is_langextract_openai_compatible(model_type)

    if not is_cloud or len(text_chunks) <= 1:
        return None
        logger.info("尝试批量请求: %s 块", len(text_chunks))

    # 构建批量提示词
    batch_prompts = []
    fields = profile.get("fields", [])
    field_names = [f["name"] for f in fields if isinstance(f, dict)]
    field_desc = ", ".join(field_names)

    for i, chunk in enumerate(text_chunks):
        chunk_text = chunk.get("text", "")
        prompt = f"""请从以下文本块中提取结构化信息，按JSON格式输出。

文本块 {i+1}:
{chunk_text}

字段要求: {field_desc}
只输出JSON，不要其他内容。"""
        batch_prompts.append(prompt)

    # 合并为一次请求
    combined_prompt = "\n\n---\n\n".join(batch_prompts)

    try:
        from src.adapters.model_client import call_model
        result = call_model(combined_prompt)

        # 解析批量响应 - 简单实现，假设返回JSON数组
        # 注意：这需要根据实际API响应调整
        import json
        try:
            # 尝试解析为JSON
            parsed = json.loads(result)
            if isinstance(parsed, list):
                return parsed
            elif isinstance(parsed, dict) and "records" in parsed:
                return parsed["records"]
            else:
                logger.warning(f"批量API返回格式不支持: {type(parsed)}")
                return None
        except json.JSONDecodeError:
            # 可能返回了非JSON格式，尝试提取JSON部分
            import re
            json_match = re.search(r'\[.*\]|\{.*\}', result, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    if isinstance(parsed, list):
                        return parsed
                    elif isinstance(parsed, dict) and "records" in parsed:
                        return parsed["records"]
                except Exception:
                    logger.debug("批量 JSON 补救解析失败", exc_info=True)
            logger.warning(f"批量API返回无法解析为JSON")
            return None

    except Exception as e:
        logger.warning("批量请求失败，批量路径放弃: %s", e)
        return None


def get_optimal_strategy(text_chunks: List[Dict], profile: dict) -> dict:
    """根据条件选择最优处理策略

    算法侧并行度使用固定保守上限；进程/网关级并发由部署与后端负责。
    """
    from src.adapters.provider_env import is_langextract_openai_compatible

    total_chars = sum(len(chunk.get("text", "")) for chunk in text_chunks)
    chunk_count = len(text_chunks)
    model_type = _get_config("MODEL_TYPE", "deepseek").strip().lower()
    is_cloud = is_langextract_openai_compatible(model_type)
    _cloud_parallel_cap = 2

    if is_cloud:
        if chunk_count <= 1:
            strategy = "single"
            max_workers = 1
        else:
            strategy = "parallel"
            max_workers = min(_cloud_parallel_cap, chunk_count)
    else:
        if chunk_count <= 1:
            strategy = "single"
            max_workers = 1
        else:
            strategy = "parallel"
            max_workers = min(1, chunk_count)

    return {
        "strategy": strategy,
        "max_workers": max_workers,
        "chunk_count": chunk_count,
        "total_chars": total_chars,
        "is_cloud": is_cloud,
    }


def _convert_result_to_records(
    result,
    field_names: List[str],
) -> List[Dict]:
    """将 langextract AnnotatedDocument 转换为记录列表"""
    from langextract.data import AnnotatedDocument

    _dprint(f"_convert_result_to_records: expected_fields={field_names}")

    records = []

    if isinstance(result, AnnotatedDocument):
        docs = [result]
    elif isinstance(result, list):
        docs = result
    else:
        return []

    for doc in docs:
        if not hasattr(doc, "extractions"):
            continue
        for ext in doc.extractions:
            attrs = ext.attributes or {}
            anchor_text = str(getattr(ext, "extraction_text", "") or "").strip()
            
            _dprint(f"_convert_result_to_records: attrs_keys={list(attrs.keys())}")
            
            record = {}
            for fname in field_names:
                if fname in attrs:
                    record[fname] = str(attrs[fname]) if attrs[fname] is not None else ""
                else:
                    # 模糊匹配（langextract 可能用了略不同的字段名）
                    matched = False
                    for ak, av in attrs.items():
                        if fname in ak or ak in fname:
                            record[fname] = str(av) if av is not None else ""
                            matched = True
                            break
                    if not matched:
                        record[fname] = ""
            if anchor_text:
                record["_anchor_text"] = anchor_text
            records.append(record)

    return _apply_anchor_backfill_records(records, field_names)


def _is_usable_anchor_text(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    # 避免把整句/长片段当作实体名写回字段
    if len(s) > 40:
        return False
    if any(ch in s for ch in ("\n", "。", "；", ";", "，", ",")):
        return False
    return True


def _apply_anchor_backfill_records(records: List[Dict], field_names: List[str]) -> List[Dict]:
    """
    当首要标识字段被模型过度概括（大量记录同值）时，用 extraction_text 回填更细粒度实体名。
    该策略不依赖任何领域词典，仅依据“记录锚点多样性 vs 字段单一值”判断。
    """
    rows = [dict(r) for r in records if isinstance(r, dict)]
    if not rows or not field_names:
        return rows

    target_field = str(field_names[0]).strip()
    if not target_field:
        return rows

    anchors = [str(r.get("_anchor_text", "")).strip() for r in rows]
    usable_anchors = [a for a in anchors if _is_usable_anchor_text(a)]
    distinct_anchors = {a for a in usable_anchors if a}
    if len(distinct_anchors) < 3:
        for r in rows:
            r.pop("_anchor_text", None)
        return rows

    target_values = [str(r.get(target_field, "")).strip() for r in rows]
    non_empty_values = [v for v in target_values if v]
    unique_values = set(non_empty_values)

    # 目标字段几乎单值，而锚点明显多样，判定为“语义上卷”。
    if len(unique_values) > 1:
        for r in rows:
            r.pop("_anchor_text", None)
        return rows

    changed = 0
    for r in rows:
        anchor = str(r.get("_anchor_text", "")).strip()
        if not _is_usable_anchor_text(anchor):
            r.pop("_anchor_text", None)
            continue
        cur = str(r.get(target_field, "")).strip()
        if (not cur) or (cur in unique_values):
            r[target_field] = anchor
            changed += 1
        r.pop("_anchor_text", None)

    if changed and not is_debug_enabled():
        logger.info("langextract: 锚点回填已应用，字段=%s，回填记录=%s", target_field, changed)
    return rows
