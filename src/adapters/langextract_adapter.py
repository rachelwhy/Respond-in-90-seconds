"""
langextract 适配器 — 将 Google langextract 集成到 A23 提取流水线

支持模型后端：
- Ollama 本地模型（qwen2.5:7b/14b 等）
- DeepSeek API（deepseek-chat）
- OpenAI API（gpt-4o 等）
- Qwen API（通义千问，OpenAI 兼容接口）

策略：
- 云 API (DeepSeek/OpenAI/Qwen) → 使用 langextract（结构化输出更精确）
- 本地 Ollama 7B → 跳过 langextract，回退到 prompt 方案（更高效）
- 本地 Ollama 14B+ → 使用 langextract（模型够强，prompt 开销可接受）
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 懒加载标记
_langextract_ready = None  # None=未检查, True=可用, False=不可用


def _check_langextract():
    """检查 langextract 是否可用"""
    global _langextract_ready
    if _langextract_ready is not None:
        return _langextract_ready
    try:
        import langextract  # noqa: F401
        from langextract.data import ExampleData, Extraction  # noqa: F401
        _langextract_ready = True
        logger.info("langextract 可用")
    except ImportError:
        _langextract_ready = False
        logger.info("langextract 未安装，将使用 prompt 方案")
    return _langextract_ready


def _get_model_size_hint(model_name: str) -> int:
    """从模型名称推测参数量（单位：B），用于判断是否适合 langextract"""
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
    model_type = model_type.lower()

    if model_type == "ollama":
        from langextract.providers.ollama import OllamaLanguageModel
        model_name = os.environ.get("A23_OLLAMA_MODEL", "qwen2.5:7b")
        model_url = os.environ.get("A23_OLLAMA_URL", "http://127.0.0.1:11434")
        size = _get_model_size_hint(model_name)
        model = OllamaLanguageModel(model_id=model_name, model_url=model_url)
        return model, model_name, False, size

    if model_type == "deepseek":
        from langextract.providers.openai import OpenAILanguageModel
        model_name = os.environ.get("A23_DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.environ.get("A23_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        api_key = os.environ.get("A23_DEEPSEEK_API_KEY", "")
        # DeepSeek 使用 OpenAI 兼容接口
        # base_url 需要以 /v1 结尾（OpenAI SDK 要求）
        if not base_url.rstrip("/").endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        model = OpenAILanguageModel(
            model_id=model_name,
            api_key=api_key,
            base_url=base_url,
        )
        return model, model_name, True, 70  # DeepSeek-V3 约 70B+

    if model_type in ("openai",):
        from langextract.providers.openai import OpenAILanguageModel
        model_name = os.environ.get("A23_OPENAI_MODEL", "gpt-4o")
        base_url = os.environ.get("A23_OPENAI_BASE_URL", "")
        api_key = os.environ.get("A23_OPENAI_API_KEY", "")
        kwargs = {"model_id": model_name}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        model = OpenAILanguageModel(**kwargs)
        return model, model_name, True, 100  # GPT-4o 级别

    if model_type == "qwen":
        from langextract.providers.openai import OpenAILanguageModel
        model_name = os.environ.get("A23_OPENAI_MODEL", "qwen-plus")
        base_url = os.environ.get("A23_OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
        api_key = os.environ.get("A23_OPENAI_API_KEY", "")
        # 通义千问使用 OpenAI 兼容接口
        model = OpenAILanguageModel(
            model_id=model_name,
            api_key=api_key,
            base_url=base_url,
        )
        return model, model_name, True, 70  # Qwen-Plus 约 70B+

    # 未知类型，尝试 Ollama
    from langextract.providers.ollama import OllamaLanguageModel
    model_name = os.environ.get("A23_OLLAMA_MODEL", "qwen2.5:7b")
    model_url = os.environ.get("A23_OLLAMA_URL", "http://127.0.0.1:11434")
    model = OllamaLanguageModel(model_id=model_name, model_url=model_url)
    return model, model_name, False, _get_model_size_hint(model_name)


def _build_example_from_profile(profile: dict) -> Any:
    """从 profile 自动生成 langextract ExampleData"""
    from langextract.data import ExampleData, Extraction

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

    return ExampleData(
        text=example_text,
        extractions=[
            Extraction(
                extraction_class="record",
                extraction_text=attributes.get(first_field, "示例"),
                attributes=attributes,
            )
        ],
    )


def extract_with_langextract(
    text_chunks: List[Dict],
    profile: dict,
    time_budget: float = None,
    quiet: bool = False,
) -> Optional[List[Dict]]:
    """使用 langextract 从文本块列表中提取结构化记录

    自适应策略：
    - 云 API → 使用 langextract
    - 本地 14B+ → 使用 langextract
    - 本地 7B 及以下 → 返回 None（由调用方使用 prompt 方案）

    Returns:
        成功时返回 List[Dict]，失败时返回 None（调用方应回退）
    """
    if not _check_langextract():
        return None

    try:
        import langextract as lx

        # 1. 获取模型类型
        model_type = os.environ.get("A23_MODEL_TYPE", "ollama").lower()

        # 2. 创建模型实例
        model_instance, model_id, is_cloud, model_size = _create_langextract_model(model_type)

        # 本地小模型跳过 langextract
        if not is_cloud and model_size < 14:
            if not quiet:
                print(f"[INFO] 本地 {model_size}B 模型，使用 prompt 方案（更高效）")
            return None

        # 3. 生成 example
        example = _build_example_from_profile(profile)
        if example is None:
            logger.warning("无法从 profile 生成 langextract 示例")
            return None

        # 4. 构造提取描述
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

        # 5. 合并文本块
        text_parts = []
        for chunk in text_chunks:
            t = chunk.get("text", "")
            if t.strip():
                text_parts.append(t)
        full_text = "\n\n".join(text_parts)

        if not full_text.strip():
            return []

        # 6. 构造调用参数 — 直接传入 model 实例，绕过 model_id 路由
        extract_kwargs = {
            "text_or_documents": full_text,
            "prompt_description": prompt_desc,
            "examples": [example],
            "model": model_instance,  # 直接传模型实例，避免 provider 路由问题
            "show_progress": not quiet,
            "temperature": 0.0,  # 确定性输出，减少遗漏
            "max_char_buffer": 4000 if is_cloud else 2000,  # 云API用更大chunk减少边界丢失
            "extraction_passes": 2 if is_cloud else 1,  # 云API双轮提取提高召回
            "context_window_chars": 500 if is_cloud else 200,  # 跨chunk上下文，防止边界实体丢失
            "batch_length": 15 if is_cloud else 10,  # 云API批量更大
        }

        if not quiet:
            backend = "cloud" if is_cloud else "local"
            print(f"[INFO] langextract 提取开始: model={model_id} ({backend}), 文本长度={len(full_text)}")

        # 7. 调用
        result = lx.extract(**extract_kwargs)

        # 8. 转换结果
        records = _convert_result_to_records(result, field_names)

        if not quiet:
            print(f"[INFO] langextract 提取完成: {len(records)} 条记录")

        return records

    except Exception as e:
        logger.warning(f"langextract 提取失败，将回退到 prompt 方案: {e}")
        if not quiet:
            print(f"[WARN] langextract 失败: {e}，回退到 prompt 方案")
        return None


def _convert_result_to_records(
    result,
    field_names: List[str],
) -> List[Dict]:
    """将 langextract AnnotatedDocument 转换为记录列表"""
    from langextract.data import AnnotatedDocument

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
            records.append(record)

    return records
