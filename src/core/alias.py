import functools
import json
import logging
import os
from pathlib import Path

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

logger = logging.getLogger(__name__)

DEFAULT_ALIAS_PATH = "src/knowledge/field_aliases.json"


try:
    from src.config import FUZZY_THRESHOLD as _DEFAULT_FUZZY_THRESHOLD
except Exception:
    try:
        _DEFAULT_FUZZY_THRESHOLD = int(
            os.environ.get("A23_FUZZY_THRESHOLD", os.environ.get("FUZZY_THRESHOLD", "60"))
        )
    except Exception:
        _DEFAULT_FUZZY_THRESHOLD = 60


@functools.lru_cache(maxsize=4)
def load_alias_map(alias_path: str = DEFAULT_ALIAS_PATH) -> dict:
    # 默认路径：知识源加载；失败时读打包的别名 JSON 文件。
    if alias_path == DEFAULT_ALIAS_PATH:
        try:
            from src.knowledge.loader import load_knowledge_base

            kb_dir = Path(alias_path).parent
            kb = load_knowledge_base(kb_dir)
            logger.debug("字段别名已加载，模糊匹配阈值: %s", _DEFAULT_FUZZY_THRESHOLD)
            return kb.field_aliases
        except Exception as e:
            logger.warning("通过知识源加载字段别名失败: %s", e)
            logger.info("知识源不可用，从文件加载别名映射")

    if not os.path.exists(alias_path):
        return {}

    with open(alias_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return {}

    logger.debug("字段别名已从文件加载，模糊匹配阈值: %s", _DEFAULT_FUZZY_THRESHOLD)
    return data


def build_reverse_alias_map(alias_map: dict) -> dict:
    reverse_map = {}

    for canonical_name, aliases in alias_map.items():
        canonical_name = str(canonical_name).strip()
        reverse_map[canonical_name] = canonical_name

        if isinstance(aliases, list):
            for alias in aliases:
                alias = str(alias).strip()
                if alias:
                    reverse_map[alias] = canonical_name

    return reverse_map


def resolve_field_name(field_name: str, alias_map: dict, fuzzy_threshold: int = None) -> str:
    """将字段名解析为规范字段名（直接匹配 → 模糊匹配）。"""
    if fuzzy_threshold is None:
        fuzzy_threshold = _DEFAULT_FUZZY_THRESHOLD

    raw = str(field_name).strip()
    if not raw:
        return raw

    reverse_map = build_reverse_alias_map(alias_map)

    # 1) 直接命中
    if raw in reverse_map:
        return reverse_map[raw]

    # 2) 模糊匹配（字符级）
    if fuzz is not None and reverse_map:
        best_name = raw
        best_score = -1

        for candidate_alias, canonical_name in reverse_map.items():
            score = fuzz.ratio(raw, candidate_alias)
            if score > best_score:
                best_score = score
                best_name = canonical_name

        if best_score >= fuzzy_threshold:
            return best_name

    return raw


def resolve_field_names(field_names: list[str], alias_path: str = DEFAULT_ALIAS_PATH, fuzzy_threshold: int = None) -> list[str]:
    alias_map = load_alias_map(alias_path)
    return [resolve_field_name(name, alias_map, fuzzy_threshold=fuzzy_threshold) for name in field_names]


def resolve_column(col_name: str, alias_path: str = DEFAULT_ALIAS_PATH, fuzzy_threshold: int = None) -> str:
    """列名解析的便捷入口（供 extractor.py 使用）。"""
    alias_map = load_alias_map(alias_path)
    return resolve_field_name(col_name, alias_map, fuzzy_threshold=fuzzy_threshold)
