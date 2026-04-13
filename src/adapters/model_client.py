import json
import re
import os
import requests
import threading
from typing import Optional, Dict, Any, List, Union
import logging

# 配置管理
try:
    from src.core.config_manager import get_config_manager
    _config = get_config_manager()
except ImportError:
    # 向后兼容
    _config = None

from src.config import (
    MODEL_TYPE, OLLAMA_URL, OLLAMA_MODEL, OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL,
    MODEL_NAME, EMBEDDING_URL, EMBEDDING_MODEL, DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY, DEEPSEEK_MODEL,
    QWEN_BASE_URL, QWEN_API_KEY, QWEN_MODEL,
    TEMPERATURE, MAX_TOKENS
)

# 尝试导入运行时配置（可选）
try:
    from src.runtime_config import get_model_config
    RUNTIME_CONFIG_AVAILABLE = True
except ImportError:
    RUNTIME_CONFIG_AVAILABLE = False
    def get_model_config(model_type=None):
        # 回退到环境变量配置
        mt = model_type or MODEL_TYPE
        if mt == "deepseek":
            return {
                "type": mt,
                "url": OLLAMA_URL,
                "base_url": DEEPSEEK_BASE_URL,
                "api_key": DEEPSEEK_API_KEY,
                "model": DEEPSEEK_MODEL,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
            }
        elif mt == "qwen":
            return {
                "type": mt,
                "url": OLLAMA_URL,
                "base_url": QWEN_BASE_URL,
                "api_key": QWEN_API_KEY,
                "model": QWEN_MODEL,
                "temperature": TEMPERATURE,
                "max_tokens": MAX_TOKENS,
            }
        return {
            "type": mt,
            "url": OLLAMA_URL,
            "base_url": OPENAI_BASE_URL,
            "api_key": OPENAI_API_KEY,
            "model": OPENAI_MODEL if mt == "openai" else OLLAMA_MODEL,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
        }


def call_model(prompt: str, model_type: Optional[str] = None, total_deadline: Optional[float] = None, timeout: Optional[int] = None, temperature: Optional[float] = None, deadline_context=None) -> dict:
    """调用模型生成内容，支持 Ollama、OpenAI 兼容 API（如 Qwen）

    支持运行时配置管理，可以通过runtime_config动态修改模型配置

    Args:
        total_deadline: Unix 时间戳（time.time()），超过该时间则抛出 TimeoutError
    """
    import time as _time

    # 处理deadline_context
    if deadline_context:
        try:
            deadline_context.check_deadline()
            # 使用deadline_context的剩余时间更新total_deadline
            remaining = deadline_context.time_remaining()
            if remaining is not None:
                # 设置更严格的deadline，考虑网络请求时间
                adjusted_deadline = _time.time() + remaining * 0.9  # 留出10%缓冲
                if total_deadline is None or adjusted_deadline < total_deadline:
                    total_deadline = adjusted_deadline
        except Exception as e:
            # 如果deadline_context已取消，抛出超时异常
            raise TimeoutError(f"call_model: deadline_context检查失败: {e}")

    if total_deadline and _time.time() > total_deadline:
        raise TimeoutError("call_model: 已超过总时间限制")

    model_type = model_type or MODEL_TYPE
    logging.getLogger(__name__).info("调用模型: %s, 提示文本长度: %s", model_type, len(prompt))

    try:
        if model_type == "ollama":
            result = _call_ollama(prompt, model_type=model_type, total_deadline=total_deadline, temperature=temperature)
        elif model_type == "openai":
            result = _call_openai(prompt, model_type=model_type, total_deadline=total_deadline, temperature=temperature)
        elif model_type == "qwen":
            result = _call_qwen(prompt, model_type=model_type, total_deadline=total_deadline, temperature=temperature)
        elif model_type == "deepseek":
            result = _call_deepseek(prompt, model_type=model_type, total_deadline=total_deadline, temperature=temperature)
        else:
            raise ValueError(f"不支持的模型类型: {model_type}")

        # 输出模型响应信息
        if result:
            result_str = json.dumps(result, ensure_ascii=False)
            logging.getLogger(__name__).info(
                "模型响应解析完成，结果类型: %s, 响应JSON长度: %s",
                type(result).__name__,
                len(result_str),
            )
        else:
            logging.getLogger(__name__).warning("模型返回空结果")

        return result
    except Exception as e:
        logging.getLogger(__name__).error("模型调用失败: %s", e)
        raise


def call_embedding(text: str) -> List[float]:
    """调用 Ollama embeddings API 获取文本向量表示，强制使用 UTF-8 编码

    Returns:
        float 向量列表；若服务不可用则抛出异常
    """
    import json

    # 确保 text 是有效的 UTF-8 字符串
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='replace')
    else:
        text = text.encode('utf-8', errors='replace').decode('utf-8')

    payload = {"model": EMBEDDING_MODEL, "prompt": text}

    # 手动序列化 JSON，确保使用 UTF-8（不依赖系统默认编码）
    json_str = json.dumps(payload, ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')

    resp = requests.post(EMBEDDING_URL, data=json_bytes, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    embedding = data.get("embedding") or data.get("embeddings")
    if not embedding:
        raise ValueError(f"嵌入服务返回异常: {data}")
    return embedding


def _call_ollama(prompt: str, model_type: Optional[str] = None, total_deadline: Optional[float] = None, temperature: Optional[float] = None) -> dict:
    """调用 Ollama 模型，强制使用 UTF-8 编码。

    不使用 format=json / 解析后二次请求：小模型在长表抽取时易被约束成更短输出，条数反而下降。
    """
    import time as _time
    import json
    config = get_model_config(model_type or "ollama")

    if isinstance(prompt, bytes):
        prompt = prompt.decode('utf-8', errors='replace')
    else:
        prompt = prompt.encode('utf-8', errors='replace').decode('utf-8')

    payload = {
        "model": config.get("model", MODEL_NAME),
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature if temperature is not None else config.get("temperature", TEMPERATURE),
            "num_predict": config.get("max_tokens", MAX_TOKENS),
        },
    }

    json_str = json.dumps(payload, ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')

    max_retries = 3
    last_exception = None

    for attempt in range(max_retries):
        if total_deadline and _time.time() > total_deadline:
            raise TimeoutError("Ollama: 已超过总时间限制")
        try:
            timeout = 120 + (attempt * 60)
            resp = requests.post(config.get("url", OLLAMA_URL), data=json_bytes, timeout=timeout)
            resp.raise_for_status()
            response_data = resp.json()
            result = (response_data.get("response") or "").strip()
            logging.getLogger(__name__).debug("Ollama原始响应长度: %s", len(result))
            parsed = _parse_model_response(result)
            logging.getLogger(__name__).debug("Ollama解析后结果类型: %s", type(parsed).__name__)
            return parsed

        except requests.exceptions.Timeout as e:
            logging.getLogger(__name__).warning(
                "Ollama调用超时，尝试 %s/%s，超时设置: %s秒",
                attempt + 1,
                max_retries,
                timeout,
            )
            last_exception = e
            if attempt < max_retries - 1:
                _time.sleep(2)
        except Exception as e:
            raise e

    raise Exception(f"Ollama调用失败，重试{max_retries}次后仍然超时: {last_exception}")


def _call_openai(prompt: str, model_type: Optional[str] = None, total_deadline: Optional[float] = None, temperature: Optional[float] = None) -> dict:
    """调用 OpenAI 兼容 API（如本地部署的 Qwen），强制使用 UTF-8 编码"""
    import time as _time
    import json
    # 获取运行时配置
    config = get_model_config(model_type or "openai")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
    }

    api_key = config.get("api_key", OPENAI_API_KEY)
    if api_key and api_key != "not-needed":
        headers["Authorization"] = f"Bearer {api_key}"

    # 确保 prompt 是有效的 UTF-8 字符串
    if isinstance(prompt, bytes):
        prompt = prompt.decode('utf-8', errors='replace')
    else:
        prompt = prompt.encode('utf-8', errors='replace').decode('utf-8')

    payload = {
        "model": config.get("model", OPENAI_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature if temperature is not None else config.get("temperature", TEMPERATURE),
        "max_tokens": config.get("max_tokens", MAX_TOKENS),
        "stream": False
    }

    # 手动序列化 JSON，确保使用 UTF-8（不依赖系统默认编码）
    json_str = json.dumps(payload, ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')

    max_retries = 3
    last_exception = None

    for attempt in range(max_retries):
        if total_deadline and _time.time() > total_deadline:
            raise TimeoutError("OpenAI: 已超过总时间限制")
        try:
            # 每次重试增加超时时间：120, 180, 240秒
            timeout = 120 + (attempt * 60)
            base_url = config.get("base_url", OPENAI_BASE_URL)
            # 使用 data 参数发送字节流，而非 json 参数
            resp = requests.post(f"{base_url}/chat/completions", data=json_bytes, headers=headers, timeout=timeout)
            resp.raise_for_status()
            response_data = resp.json()
            result = response_data["choices"][0]["message"]["content"].strip()
            logging.getLogger(__name__).debug("OpenAI原始响应长度: %s", len(result))

            parsed = _parse_model_response(result)
            logging.getLogger(__name__).debug("OpenAI解析后结果类型: %s", type(parsed).__name__)
            return parsed

        except requests.exceptions.Timeout as e:
            logging.getLogger(__name__).warning(
                "OpenAI API调用超时，尝试 %s/%s，超时设置: %s秒",
                attempt + 1,
                max_retries,
                timeout,
            )
            last_exception = e
            if attempt < max_retries - 1:
                _time.sleep(2)  # 等待2秒后重试
        except Exception as e:
            # 其他错误，不重试
            raise e

    # 所有重试都失败
    raise Exception(f"OpenAI API调用失败，重试{max_retries}次后仍然超时: {last_exception}")


def _call_qwen(prompt: str, model_type: Optional[str] = None, total_deadline: Optional[float] = None, temperature: Optional[float] = None) -> dict:
    """调用 Qwen 模型（兼容 OpenAI API）"""
    # Qwen 通常使用 OpenAI 兼容接口，所以调用 openai 方法
    return _call_openai(prompt, model_type or "qwen", total_deadline=total_deadline, temperature=temperature)


def _call_deepseek(prompt: str, model_type: Optional[str] = None, total_deadline: Optional[float] = None, temperature: Optional[float] = None) -> dict:
    """调用 DeepSeek API，强制使用 UTF-8 编码"""
    import time as _time
    import json

    config = get_model_config(model_type or "deepseek")

    headers = {
        "Content-Type": "application/json; charset=utf-8",
    }

    api_key = config.get("api_key", DEEPSEEK_API_KEY)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # 确保 prompt 是有效的 UTF-8 字符串
    if isinstance(prompt, bytes):
        prompt = prompt.decode('utf-8', errors='replace')
    else:
        prompt = prompt.encode('utf-8', errors='replace').decode('utf-8')

    payload = {
        "model": config.get("model", DEEPSEEK_MODEL),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature if temperature is not None else config.get("temperature", TEMPERATURE),
        "max_tokens": config.get("max_tokens", MAX_TOKENS),
        "stream": False
    }

    # 手动序列化 JSON，确保使用 UTF-8（不依赖系统默认编码）
    json_str = json.dumps(payload, ensure_ascii=False)
    json_bytes = json_str.encode('utf-8')

    max_retries = 3
    last_exception = None

    for attempt in range(max_retries):
        if total_deadline and _time.time() > total_deadline:
            raise TimeoutError("DeepSeek: 已超过总时间限制")
        try:
            timeout = 120 + (attempt * 60)
            base_url = config.get("base_url", DEEPSEEK_BASE_URL)
            # 使用 data 参数发送字节流，而非 json 参数
            resp = requests.post(
                f"{base_url}/chat/completions",
                data=json_bytes,  # 关键修改：使用 data 而不是 json
                headers=headers,
                timeout=timeout
            )
            resp.raise_for_status()
            response_data = resp.json()
            result = response_data["choices"][0]["message"]["content"].strip()
            logging.getLogger(__name__).debug("DeepSeek原始响应长度: %s", len(result))

            parsed = _parse_model_response(result)
            logging.getLogger(__name__).debug("DeepSeek解析后结果类型: %s", type(parsed).__name__)
            return parsed

        except requests.exceptions.Timeout as e:
            logging.getLogger(__name__).warning(
                "DeepSeek API调用超时，尝试 %s/%s",
                attempt + 1,
                max_retries,
            )
            last_exception = e
            if attempt < max_retries - 1:
                _time.sleep(2)
        except Exception as e:
            raise e

    raise Exception(f"DeepSeek API调用失败，重试{max_retries}次后仍然超时: {last_exception}")




def _fix_json_common_issues(json_str: str) -> str:
    """修复常见的JSON格式问题"""
    fixed = json_str

    # 1. 移除对象和数组中的尾随逗号
    fixed = re.sub(r',\s*}', '}', fixed)
    fixed = re.sub(r',\s*]', ']', fixed)

    # 2. 修复可能的多余反斜杠
    fixed = fixed.replace('\\"', '"')

    # 3. 移除控制字符（如换行符、制表符等）在字符串外部
    # 先保存字符串内容，然后移除外部的控制字符
    import json as json_module

    # 4. 修复未引用的字符串值（特别是包含单位的值）
    # 查找模式：键: 值, 其中值包含中文字符或单位
    # 例如: "脱贫人口务工就业规模": 3278万人,
    # 应该为: "脱贫人口务工就业规模": "3278万人",

    # 这个正则表达式匹配: "key": value, 其中value不以引号开头，但包含非数字字符
    def fix_unquoted_string(match):
        key_part = match.group(1)  # "key":
        value_part = match.group(2)  # 3278万人

        # 如果value看起来像数字+单位，或者包含中文，加引号
        if re.search(r'[^\d\s.,\-+eE]', value_part):
            # 包含非数字字符，需要加引号
            # 但要注意value可能包含逗号，需要特别处理
            value_fixed = value_part.strip()
            # 转义内部的双引号
            value_fixed = value_fixed.replace('"', '\\"')
            return f'{key_part} "{value_fixed}"'
        return match.group(0)

    # 匹配: "key": value, 或 "key": value} 或 "key": value\n
    pattern = r'("[^"]*"\s*:\s*)([^"{\[\d][^,}\]\n]*)'
    fixed = re.sub(pattern, fix_unquoted_string, fixed)

    # 5. 修复百分比和其他常见格式
    # 例如: 37.5% 应该为 "37.5%"
    fixed = re.sub(r':\s*(\d+(?:\.\d+)?%)(?=[,\s\]}])', r': "\1"', fixed)

    # 6. 修复数字加单位的情况
    # 例如: 3278万人 应该为 "3278万人"
    fixed = re.sub(r':\s*(\d+(?:\.\d+)?[^\s,}\]"\'%]+)(?=[,\s\]}])', r': "\1"', fixed)

    return fixed


def _fix_json_aggressive(json_str: str) -> str:
    """更激进的JSON修复，用于处理严重格式问题"""
    fixed = json_str

    # 1. 移除所有尾随逗号（包括多层嵌套）
    while True:
        new_fixed = re.sub(r',\s*(?=\s*[\]}])', '', fixed)
        if new_fixed == fixed:
            break
        fixed = new_fixed

    # 2. 修复未闭合的引号
    # 统计双引号数量，如果为奇数，在末尾添加一个
    quote_count = fixed.count('"')
    if quote_count % 2 == 1:
        fixed += '"'

    # 3. 修复常见的键值分隔符问题
    # 将 key: value 改为 "key": value
    fixed = re.sub(r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:', r'\1"\2":', fixed)

    # 4. 修复中文键名（未加引号）
    # 匹配模式: { 键: 或 , 键:
    chinese_key_pattern = r'([{,]\s*)([\u4e00-\u9fff][\u4e00-\u9fff\w]*)\s*:'
    fixed = re.sub(chinese_key_pattern, r'\1"\2":', fixed)

    # 5. 修复所有未加引号的字符串值（激进模式）
    # 匹配: "key": value 其中value不以引号开头
    def quote_all_unquoted_values(match):
        key = match.group(1)  # "key":
        value = match.group(2)  # 值

        # 如果值已经是数字、true、false、null，不处理
        if re.match(r'^\s*(true|false|null|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*$', value):
            return match.group(0)

        # 否则加引号
        # 转义内部的双引号
        value_escaped = value.replace('"', '\\"')
        return f'{key} "{value_escaped}"'

    # 匹配: "key": value
    pattern = r'("[^"]*"\s*:\s*)([^"{\[\d][^,}\]\n]*(?:[^,}\]\n][^,}\]\n]*)*)'
    fixed = re.sub(pattern, quote_all_unquoted_values, fixed)

    # 6. 修复截断的JSON（缺少闭合括号）
    # 统计 { 和 } 的数量
    open_braces = fixed.count('{')
    close_braces = fixed.count('}')
    if open_braces > close_braces:
        fixed += '}' * (open_braces - close_braces)

    # 统计 [ 和 ] 的数量
    open_brackets = fixed.count('[')
    close_brackets = fixed.count(']')
    if open_brackets > close_brackets:
        fixed += ']' * (open_brackets - close_brackets)

    # 7. 移除可能的多余文本
    # 如果以```json开头，移除
    if fixed.startswith('```json'):
        fixed = fixed[7:]
    if fixed.startswith('```'):
        fixed = fixed[3:]
    if fixed.endswith('```'):
        fixed = fixed[:-3]

    # 8. 确保JSON以 { 或 [ 开头
    fixed = fixed.strip()
    if not (fixed.startswith('{') or fixed.startswith('[')):
        # 尝试在文本中查找第一个 { 或 [
        first_brace = fixed.find('{')
        first_bracket = fixed.find('[')

        if first_brace >= 0 and (first_bracket < 0 or first_brace < first_bracket):
            fixed = fixed[first_brace:]
        elif first_bracket >= 0:
            fixed = fixed[first_bracket:]

    return fixed


def _validate_and_normalize_structured_output(parsed: dict) -> dict:
    """验证和标准化结构化输出

    参数：
    - parsed: 解析后的JSON对象

    返回：
    - 验证和标准化后的对象

    功能：
    1. 检查是否为单位感知提取的输出格式
    2. 标准化units数组的结构
    3. 确保必需的字段存在
    4. 修复常见的数据类型问题
    """
    if not isinstance(parsed, dict):
        return parsed

    # 检查是否为单位感知提取格式
    has_units = "units" in parsed and isinstance(parsed["units"], list)
    has_extraction_mode = "extraction_mode" in parsed

    if not has_units:
        # 不是单位感知格式，直接返回
        return parsed

    # 标准化units数组
    normalized_units = []
    for i, unit in enumerate(parsed["units"]):
        if not isinstance(unit, dict):
            # 如果不是字典，跳过或尝试转换
            continue

        normalized_unit = dict(unit)

        # 确保必需的字段存在
        if "unit_id" not in normalized_unit:
            normalized_unit["unit_id"] = f"unit_{i+1:03d}"

        if "unit_type" not in normalized_unit:
            normalized_unit["unit_type"] = "unknown"

        if "fields" not in normalized_unit:
            # 尝试从单位对象中提取字段
            # 假设除元字段外的其他键都是字段
            fields = {}
            meta_keys = {"unit_id", "unit_type", "confidence", "evidence", "metadata"}
            for key, value in normalized_unit.items():
                if key not in meta_keys:
                    fields[key] = value
            normalized_unit["fields"] = fields
        elif not isinstance(normalized_unit["fields"], dict):
            # 如果fields不是字典，尝试转换
            if isinstance(normalized_unit["fields"], list):
                # 可能是键值对列表
                field_dict = {}
                for item in normalized_unit["fields"]:
                    if isinstance(item, dict) and "key" in item and "value" in item:
                        field_dict[item["key"]] = item["value"]
                    elif isinstance(item, list) and len(item) == 2:
                        field_dict[str(item[0])] = str(item[1])
                normalized_unit["fields"] = field_dict
            else:
                normalized_unit["fields"] = {}

        # 确保confidence字段
        if "confidence" not in normalized_unit:
            normalized_unit["confidence"] = 0.8
        else:
            # 确保confidence是数值且在合理范围内
            try:
                conf = float(normalized_unit["confidence"])
                if conf < 0:
                    conf = 0.0
                elif conf > 1.0:
                    conf = 1.0
                normalized_unit["confidence"] = conf
            except (ValueError, TypeError):
                normalized_unit["confidence"] = 0.8

        # 确保evidence字段
        if "evidence" not in normalized_unit:
            normalized_unit["evidence"] = ""

        # 确保metadata字段
        if "metadata" not in normalized_unit:
            normalized_unit["metadata"] = {}
        elif not isinstance(normalized_unit["metadata"], dict):
            normalized_unit["metadata"] = {}

        normalized_units.append(normalized_unit)

    # 更新units数组
    parsed["units"] = normalized_units

    # 确保summary字段
    if "summary" not in parsed:
        parsed["summary"] = {}

    summary = parsed["summary"]
    if not isinstance(summary, dict):
        summary = {}
        parsed["summary"] = summary

    # 更新summary中的统计信息
    summary["unit_count"] = len(normalized_units)
    if "unit_types" not in summary:
        unit_types = list(set(u["unit_type"] for u in normalized_units if u["unit_type"]))
        summary["unit_types"] = unit_types

    # 确保extraction_mode
    if "extraction_mode" not in parsed:
        parsed["extraction_mode"] = "unit_aware"

    # 确保metadata
    if "metadata" not in parsed:
        parsed["metadata"] = {}
    elif not isinstance(parsed["metadata"], dict):
        parsed["metadata"] = {}

    return parsed


def _parse_model_response(result: str) -> dict:
    """解析模型响应，提取 JSON"""
    # 清理响应文本
    cleaned = result.strip()

    # 移除可能的Markdown代码块标记
    if cleaned.startswith("```"):
        # 移除开头的```json或```
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        # 移除结尾的```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    # 尝试1: 直接解析
    try:
        parsed = json.loads(cleaned)
        # 验证和标准化结构化输出
        return _validate_and_normalize_structured_output(parsed)
    except json.JSONDecodeError as e:
        first_error = e
        # 继续尝试其他方法

    # 尝试2: 从文本中提取JSON对象（优先提取最外层对象）
    # 查找所有可能的JSON对象
    json_candidates = []

    # 尝试匹配 {...} 或 [...]
    object_matches = list(re.finditer(r"\{[\s\S]*?\}(?=\s*(?:,|\]|}|$))", cleaned))
    array_matches = list(re.finditer(r"\[[\s\S]*?\](?=\s*(?:,|\]|}|$))", cleaned))

    # 收集所有候选
    for match in object_matches + array_matches:
        json_str = match.group(0)
        json_candidates.append(json_str)

    # 如果没有找到候选，尝试匹配整个文本作为一个对象
    if not json_candidates:
        full_match = re.search(r"\{[\s\S]*\}", cleaned)
        if full_match:
            json_candidates.append(full_match.group(0))

    # 尝试解析每个候选
    for json_str in json_candidates:
        try:
            parsed = json.loads(json_str)
            return _validate_and_normalize_structured_output(parsed)
        except json.JSONDecodeError as e2:
            # 尝试修复
            json_str_fixed = _fix_json_common_issues(json_str)
            try:
                parsed = json.loads(json_str_fixed)
                return _validate_and_normalize_structured_output(parsed)
            except json.JSONDecodeError as e3:
                # 尝试更激进的修复
                json_str_fixed2 = _fix_json_aggressive(json_str_fixed)
                try:
                    parsed = json.loads(json_str_fixed2)
                    return _validate_and_normalize_structured_output(parsed)
                except json.JSONDecodeError:
                    # 继续尝试下一个候选
                    continue

    # 尝试3: 如果所有候选都失败，尝试解析为JSON行（每行一个JSON对象）
    lines = cleaned.split('\n')
    json_lines = []
    for line in lines:
        line = line.strip()
        if line.startswith('{') and line.endswith('}'):
            json_lines.append(line)
        elif line.startswith('[') and line.endswith(']'):
            json_lines.append(line)

    if json_lines:
        # 尝试将多个JSON对象合并为一个数组
        combined_json = '[' + ','.join(json_lines) + ']'
        try:
            parsed = json.loads(combined_json)
            return _validate_and_normalize_structured_output(parsed)
        except json.JSONDecodeError:
            # 如果合并失败，尝试解析第一个有效的JSON行
            for json_line in json_lines:
                try:
                    parsed = json.loads(json_line)
                    return _validate_and_normalize_structured_output(parsed)
                except json.JSONDecodeError:
                    continue

    # 所有尝试都失败
    raise ValueError(f"无法解析模型输出为JSON，原始错误: {first_error}\n原始输出前500字符:\n{result[:500]}")


def call_ollama(prompt: str) -> dict:
    """向后兼容的旧接口"""
    return call_model(prompt, model_type="ollama")


class ModelClient:
    """模型客户端包装类，用于AI主题分析等需要类接口的场景"""

    def __init__(self, model_type: str = None):
        """初始化模型客户端

        Args:
            model_type: 模型类型，默认使用配置中的MODEL_TYPE
        """
        self.model_type = model_type

    def generate_text(self, prompt: str, temperature: float = 0.1, max_tokens: int = 500) -> str:
        """生成文本响应

        Args:
            prompt: 提示文本
            temperature: 温度参数
            max_tokens: 最大令牌数

        Returns:
            str: 模型生成的文本响应
        """
        # 调用现有的call_model函数
        result = call_model(prompt, model_type=self.model_type)

        # 从结果中提取文本响应
        # call_model返回字典（解析后的JSON），可能包含'response'字段或其他结构
        # 对于AI主题分析，模型应返回JSON对象，我们需要将其转换为字符串
        if isinstance(result, dict):
            # 检查是否有原始响应文本（某些模型返回格式）
            if 'response' in result and isinstance(result['response'], str):
                # 原始响应文本可能是JSON字符串或其他文本
                return result['response']
            elif 'text' in result and isinstance(result['text'], str):
                return result['text']
            elif 'content' in result and isinstance(result['content'], str):
                return result['content']
            elif 'result' in result and isinstance(result['result'], str):
                return result['result']
            else:
                # 将整个字典转换为JSON字符串
                return json.dumps(result, ensure_ascii=False, indent=None)
        else:
            # 如果不是字典，转换为字符串
            return str(result)


# ============================================================================
# 配置上下文管理器（用于临时修改配置）
# ============================================================================

class ConfigContext:
    """配置上下文管理器，用于临时修改配置（线程安全）"""

    def __init__(self, config_updates: Dict[str, Any]):
        """
        Args:
            config_updates: 要临时更新的配置字典，键为配置名（如"OLLAMA_URL"）
        """
        self.config_updates = config_updates
        self.original_values = {}
        self._config = _config

    def __enter__(self):
        """进入上下文，保存原始值并设置新值"""
        global _config
        if self._config is None:
            # 尝试动态导入ConfigManager（激进清理：不再直接使用os.environ）
            try:
                from src.core.config_manager import get_config_manager
                _config = get_config_manager()
                self._config = _config
            except ImportError:
                raise ImportError("ConfigManager不可用，且不允许直接使用os.environ。请确保src.core.config_manager已正确安装。")

        # 使用ConfigManager（线程安全）
        for key, value in self.config_updates.items():
            self.original_values[key] = self._config.get(key)
            self._config.set(key, value, notify=False)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，恢复原始值"""
        # 使用ConfigManager恢复配置
        for key, original_value in self.original_values.items():
            if original_value is not None:
                self._config.set(key, original_value, notify=False)
            else:
                # 如果没有原始值，删除该配置（恢复为默认值）
                pass  # ConfigManager不支持删除配置，设置为None
                self._config.set(key, None, notify=False)


# ============================================================================
# ModelGateway 类 - 多模型网关，支持优先级和故障转移
# ============================================================================

class ModelGateway:
    """多模型网关，支持按优先级尝试多个模型，失败时自动切换到下一个"""

    def __init__(self, models_config=None):
        """初始化模型网关

        Args:
            models_config: 模型配置列表，默认为从环境变量A23_MODELS加载
                          格式: [{"type": "ollama", "model": "qwen2.5:7b", "url": "...", "priority": 1}, ...]
        """
        from src.config import MODELS
        self.models = models_config or MODELS
        # 按优先级排序
        self.models.sort(key=lambda x: x.get("priority", 999))
        self.current_model_index = 0
        self.metrics = {
            "calls": {i: 0 for i in range(len(self.models))},
            "successes": {i: 0 for i in range(len(self.models))},
            "failures": {i: 0 for i in range(len(self.models))},
            "response_times": {i: [] for i in range(len(self.models))}
        }

    def call(self, prompt: str, max_retries_per_model: int = 3) -> dict:
        """调用模型处理提示文本

        Args:
            prompt: 提示文本
            max_retries_per_model: 每个模型的最大重试次数

        Returns:
            dict: 模型响应解析后的字典

        Raises:
            Exception: 所有模型都失败时抛出异常
        """
        import time
        from typing import Dict, Any

        for model_idx, model_config in enumerate(self.models):
            model_type = model_config.get("type", "ollama")
            model_name = model_config.get("model", "")

            logging.getLogger(__name__).debug(
                "ModelGateway: 尝试模型 %s/%s (type=%s, model=%s, priority=%s)",
                model_idx + 1,
                len(self.models),
                model_type,
                model_name,
                model_config.get("priority", 999),
            )

            self.metrics["calls"][model_idx] += 1
            start_time = time.time()

            try:
                # 调用现有call_model函数，但使用当前模型的配置
                # 需要临时修改配置或传递配置参数
                # 由于call_model使用全局配置，这里我们使用一个包装方法
                result = self._call_with_config(prompt, model_config, max_retries_per_model)

                elapsed = time.time() - start_time
                self.metrics["successes"][model_idx] += 1
                self.metrics["response_times"][model_idx].append(elapsed)

                logging.getLogger(__name__).info(
                    "ModelGateway: 模型 %s/%s 调用成功, 耗时 %.2fs",
                    model_type,
                    model_name,
                    elapsed,
                )
                self.current_model_index = model_idx  # 记录当前成功模型
                return result

            except Exception as e:
                elapsed = time.time() - start_time
                self.metrics["failures"][model_idx] += 1
                self.metrics["response_times"][model_idx].append(elapsed)

                logging.getLogger(__name__).warning(
                    "ModelGateway: 模型 %s/%s 调用失败: %s",
                    model_type,
                    model_name,
                    str(e),
                )
                # 继续尝试下一个模型
                continue

        # 所有模型都失败
        error_msg = f"ModelGateway: 所有 {len(self.models)} 个模型都调用失败"
        logging.getLogger(__name__).error("%s", error_msg)
        raise Exception(error_msg)

    def _call_with_config(self, prompt: str, model_config: Dict[str, Any], max_retries: int) -> dict:
        """使用特定模型配置调用模型"""
        model_type = model_config.get("type", "ollama")

        # 准备要临时更新的配置
        config_updates = {}

        if model_type == "ollama":
            # Ollama配置映射
            if "url" in model_config:
                config_updates["OLLAMA_URL"] = model_config["url"]
            if "model" in model_config:
                config_updates["OLLAMA_MODEL"] = model_config["model"]
                config_updates["MODEL_NAME"] = model_config["model"]

        elif model_type == "openai":
            # OpenAI配置映射
            if "api_key" in model_config:
                config_updates["OPENAI_API_KEY"] = model_config["api_key"]
            if "base_url" in model_config:
                config_updates["OPENAI_BASE_URL"] = model_config["base_url"]
            if "model" in model_config:
                config_updates["OPENAI_MODEL"] = model_config["model"]

        elif model_type == "deepseek":
            # DeepSeek配置映射
            if "api_key" in model_config:
                config_updates["DEEPSEEK_API_KEY"] = model_config["api_key"]
            if "base_url" in model_config:
                config_updates["DEEPSEEK_BASE_URL"] = model_config["base_url"]
            if "model" in model_config:
                config_updates["DEEPSEEK_MODEL"] = model_config["model"]

        elif model_type == "qwen":
            # Qwen配置映射
            if "api_key" in model_config:
                config_updates["QWEN_API_KEY"] = model_config["api_key"]
            if "base_url" in model_config:
                config_updates["QWEN_BASE_URL"] = model_config["base_url"]
            if "model" in model_config:
                config_updates["QWEN_MODEL"] = model_config["model"]

        else:
            raise ValueError(f"不支持的模型类型: {model_type}")

        # 使用配置上下文管理器临时更新配置
        with ConfigContext(config_updates):
            return call_model(prompt, model_type=model_type)

    def health_check(self) -> Dict[str, Any]:
        """检查所有配置的模型是否可用

        Returns:
            dict: 健康检查结果，包含每个模型的状态
        """
        import time
        results = {}

        for idx, model_config in enumerate(self.models):
            model_type = model_config.get("type", "ollama")
            model_name = model_config.get("model", "")

            # 简单的健康检查：发送一个很小的测试提示
            test_prompt = "Hello, please respond with 'OK'"
            start_time = time.time()

            try:
                # 设置短超时
                import requests
                import json
                if model_type == "ollama":
                    url = model_config.get("url", "http://127.0.0.1:11434/api/generate")
                    # 确保 test_prompt 是有效的 UTF-8 字符串
                    if isinstance(test_prompt, bytes):
                        test_prompt = test_prompt.decode('utf-8', errors='replace')
                    else:
                        test_prompt = test_prompt.encode('utf-8', errors='replace').decode('utf-8')

                    payload = {
                        "model": model_name,
                        "prompt": test_prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 10}
                    }
                    # 手动序列化 JSON，确保使用 UTF-8
                    json_str = json.dumps(payload, ensure_ascii=False)
                    json_bytes = json_str.encode('utf-8')
                    resp = requests.post(url, data=json_bytes, timeout=5)
                    resp.raise_for_status()
                    status = "healthy"
                else:
                    # 其他模型类型暂时标记为未知
                    status = "unknown"

                elapsed = time.time() - start_time
                results[f"model_{idx}_{model_type}"] = {
                    "status": status,
                    "response_time": elapsed,
                    "type": model_type,
                    "model": model_name
                }

            except Exception as e:
                results[f"model_{idx}_{model_type}"] = {
                    "status": "unhealthy",
                    "error": str(e),
                    "type": model_type,
                    "model": model_name
                }

        return results

    def get_metrics(self) -> Dict[str, Any]:
        """获取网关性能指标"""
        return {
            "models": [
                {
                    "config": config,
                    "calls": self.metrics["calls"][i],
                    "successes": self.metrics["successes"][i],
                    "failures": self.metrics["failures"][i],
                    "avg_response_time": (
                        sum(self.metrics["response_times"][i]) / len(self.metrics["response_times"][i])
                        if self.metrics["response_times"][i] else 0
                    )
                }
                for i, config in enumerate(self.models)
            ],
            "current_model_index": self.current_model_index,
            "total_calls": sum(self.metrics["calls"].values()),
            "total_successes": sum(self.metrics["successes"].values()),
            "total_failures": sum(self.metrics["failures"].values())
        }