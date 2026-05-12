"""远端 LLM 请求前的可配置正则脱敏：占位符仅存于进程内，返回结果中按映射还原。

仅在 ``REDACT_REMOTE_PROMPTS`` 开启且当前 ``MODEL_TYPE`` 为 OpenAI Chat 兼容远端（见 ``provider_env.is_chat_openai_compatible``）时由 ``call_model`` 启用。
规则文件默认 ``src/knowledge/redaction_patterns.json``，可通过 ``REDACTION_PATTERNS_CONFIG`` 覆盖。
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def should_apply_remote_redaction(model_type: str, *, enabled_flag: bool) -> bool:
    """是否对即将发往远端的 prompt 做脱敏（本地 Ollama 及未知类型不做）。"""
    if not enabled_flag:
        return False
    from src.adapters.provider_env import is_chat_openai_compatible

    return is_chat_openai_compatible(model_type or "")


@lru_cache(maxsize=8)
def _load_compiled_patterns(config_path: str) -> List[Tuple[re.Pattern[str], str, int]]:
    """返回 [(compiled, pattern_id, priority), ...]，按 priority 降序。"""

    path = Path(config_path)
    if not path.is_file():
        logger.warning("脱敏规则文件不存在，跳过: %s", path)
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("脱敏规则 JSON 读取失败，跳过: %s", e)
        return []

    patterns = raw.get("patterns")
    if not isinstance(patterns, list):
        return []

    out: List[Tuple[re.Pattern[str], str, int]] = []
    for entry in patterns:
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled", True) is False:
            continue
        rid = str(entry.get("id") or "").strip() or "anon"
        expr = str(entry.get("regex") or "").strip()
        if not expr:
            continue
        flags = 0
        for f in entry.get("flags") or []:
            if str(f).upper() == "IGNORECASE":
                flags |= re.IGNORECASE
        try:
            compiled = re.compile(expr, flags)
        except re.error as e:
            logger.warning("脱敏正则无效 id=%s: %s", rid, e)
            continue
        priority = int(entry.get("priority") or 0)
        out.append((compiled, rid, priority))

    out.sort(key=lambda x: (-x[2], x[1]))
    return out


def redact_prompt(
    text: str,
    *,
    config_path: str,
) -> Tuple[str, Dict[str, str]]:
    """按知识库正则依次替换为占位符 ``⟦A23Xn⟧``，返回新文本与 ``占位符 -> 原文`` 映射。"""

    if not text:
        return text, {}

    compiled = _load_compiled_patterns(config_path)
    if not compiled:
        return text, {}

    mapping: Dict[str, str] = {}
    counter = 0
    out = text

    for cre, _rid, _pri in compiled:
        def _repl(m: re.Match[str]) -> str:
            nonlocal counter
            ph = f"⟦A23X{counter}⟧"
            counter += 1
            mapping[ph] = m.group(0)
            return ph

        out = cre.sub(_repl, out)

    return out, mapping


def restore_model_output(obj: Any, mapping: Dict[str, str]) -> Any:
    """在模型返回的结构中，将所有占位符还原为原文（长键先替换，避免子串误伤）。"""

    if not mapping:
        return obj

    keys_sorted = sorted(mapping.keys(), key=len, reverse=True)

    def _restore_str(s: str) -> str:
        t = s
        for k in keys_sorted:
            if k in t:
                t = t.replace(k, mapping[k])
        return t

    if isinstance(obj, str):
        return _restore_str(obj)
    if isinstance(obj, dict):
        return {k: restore_model_output(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [restore_model_output(v, mapping) for v in obj]
    return obj
