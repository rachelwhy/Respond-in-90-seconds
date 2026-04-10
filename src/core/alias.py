import functools
import json
import logging
import os
from pathlib import Path

try:
    from rapidfuzz import fuzz
except Exception:
    fuzz = None

# 语义匹配：懒加载 sentence-transformers
_semantic_model = None
_semantic_ready = None  # None=未检查, True=可用, False=不可用


def _get_semantic_model():
    """懒加载 sentence-transformers 模型（全局单例）"""
    global _semantic_model, _semantic_ready
    if _semantic_ready is not None:
        return _semantic_model
    try:
        from sentence_transformers import SentenceTransformer
        _semantic_model = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
        _semantic_ready = True
        logger.info("语义匹配模型已加载: paraphrase-multilingual-MiniLM-L12-v2")
    except Exception as e:
        _semantic_ready = False
        logger.warning(f"语义匹配模型加载失败，回退到字符匹配: {e}")
    return _semantic_model


logger = logging.getLogger(__name__)

DEFAULT_ALIAS_PATH = "src/knowledge/field_aliases.json"

# 默认模糊匹配阈值：优先读取环境变量，回退到 60
_DEFAULT_FUZZY_THRESHOLD = int(os.environ.get("A23_FUZZY_THRESHOLD", "60"))

# 语义匹配阈值（余弦相似度 0~1）
_SEMANTIC_THRESHOLD = float(os.environ.get("A23_SEMANTIC_THRESHOLD", "0.55"))


@functools.lru_cache(maxsize=4)
def load_alias_map(alias_path: str = DEFAULT_ALIAS_PATH) -> dict:
    # 如果使用默认路径，通过知识源加载（支持文件/数据库切换）
    if alias_path == DEFAULT_ALIAS_PATH:
        try:
            from src.knowledge.loader import load_knowledge_base
            # 使用默认的知识库目录
            kb_dir = Path(alias_path).parent
            kb = load_knowledge_base(kb_dir)
            logger.debug(f"字段别名已加载，当前模糊匹配阈值: {_DEFAULT_FUZZY_THRESHOLD}")
            return kb.field_aliases
        except Exception as e:
            print(f"[WARN] 通过知识源加载字段别名失败: {e}")
            print(f"[INFO] 回退到直接文件加载")

    # 回退到直接文件加载（兼容自定义路径）
    if not os.path.exists(alias_path):
        return {}

    with open(alias_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return {}

    logger.debug(f"字段别名已从文件加载，当前模糊匹配阈值: {_DEFAULT_FUZZY_THRESHOLD}")
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


def _semantic_match(field_name: str, candidates: dict, threshold: float = None) -> str:
    """使用 sentence-transformers 语义相似度匹配字段名。

    Args:
        field_name: 待匹配的字段名
        candidates: {候选名: 规范名} 的映射
        threshold: 余弦相似度阈值（0~1），默认使用环境变量

    Returns:
        匹配到的规范字段名，或原始字段名（未匹配时）
    """
    model = _get_semantic_model()
    if model is None:
        return field_name

    if threshold is None:
        threshold = _SEMANTIC_THRESHOLD

    try:
        candidate_list = list(candidates.keys())
        # 批量编码：[query] + [all candidates]
        embeddings = model.encode([field_name] + candidate_list, normalize_embeddings=True)
        query_emb = embeddings[0]
        candidate_embs = embeddings[1:]

        # 计算余弦相似度（已归一化，点积即可）
        best_score = -1.0
        best_canonical = field_name
        for i, cand_emb in enumerate(candidate_embs):
            score = float(query_emb @ cand_emb)
            if score > best_score:
                best_score = score
                best_canonical = candidates[candidate_list[i]]

        if best_score >= threshold:
            logger.debug(f"语义匹配: '{field_name}' → '{best_canonical}' (score={best_score:.3f})")
            return best_canonical

    except Exception as e:
        logger.warning(f"语义匹配异常: {e}")

    return field_name


def resolve_field_name(field_name: str, alias_map: dict, fuzzy_threshold: int = None) -> str:
    """将字段名解析为规范字段名（直接匹配 → 模糊匹配 → 语义匹配）。

    三级匹配策略：
    1. 直接匹配：字段名完全等于某个别名
    2. 模糊匹配：rapidfuzz 字符级相似度 ≥ 阈值
    3. 语义匹配：sentence-transformers 语义相似度 ≥ 阈值（自动处理同义词）
    """
    if fuzzy_threshold is None:
        fuzzy_threshold = _DEFAULT_FUZZY_THRESHOLD

    raw = str(field_name).strip()
    if not raw:
        return raw

    reverse_map = build_reverse_alias_map(alias_map)

    # 1. 直接命中
    if raw in reverse_map:
        return reverse_map[raw]

    # 2. 模糊匹配（字符级）
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

    # 3. 语义匹配（向量级，自动处理同义词/缩写/带单位变体）
    result = _semantic_match(raw, reverse_map)
    if result != raw:
        return result

    return raw


def resolve_field_names(field_names: list[str], alias_path: str = DEFAULT_ALIAS_PATH, fuzzy_threshold: int = None) -> list[str]:
    alias_map = load_alias_map(alias_path)
    resolved = []

    for name in field_names:
        resolved.append(resolve_field_name(name, alias_map, fuzzy_threshold=fuzzy_threshold))

    return resolved


def resolve_column(col_name: str, alias_path: str = DEFAULT_ALIAS_PATH, fuzzy_threshold: int = None) -> str:
    """列名解析的便捷入口（供 extractor.py 使用）"""
    alias_map = load_alias_map(alias_path)
    return resolve_field_name(col_name, alias_map, fuzzy_threshold=fuzzy_threshold)
