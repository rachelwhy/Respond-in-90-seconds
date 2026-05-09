"""
问答检索：BM25（rank-bm25）+ 向量相似度混合，向量优先 sentence-transformers，缺省回退 Ollama embedding。

与 ``qna_service`` 解耦，避免循环引用；缓存文件仍写入会话目录 ``embedding_cache.json``（与旧逻辑兼容）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_ST_MODEL: Any = None
_ST_LOAD_FAILED = False
_ST_NO_MODEL_LOGGED = False

_CHUNK_TYPE_BOOST = {"table": 1.2, "formula": 1.1, "code": 1.05}


def apply_chunk_type_boost(combined: List[float], chunks: List[dict]) -> List[float]:
    """对已与查询对齐的分数列表按块类型加权（长度与 ``chunks`` 一致）。"""
    out = list(combined)
    for i in range(len(out)):
        b = _CHUNK_TYPE_BOOST.get(str(chunks[i].get("type", "text")), 1.0)
        out[i] *= b
    return out


def _chunk_key(chunk: dict) -> str:
    return hashlib.md5(chunk["text"].encode("utf-8", errors="replace")).hexdigest()[:16]


def _tokenize_simple(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9]+", text.lower())
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(list(seg))
        for i in range(max(0, len(seg) - 1)):
            tokens.append(seg[i : i + 2])
    return [t for t in tokens if t]


def _tokenize_for_bm25(text: str) -> List[str]:
    try:
        import jieba

        return [t.strip() for t in jieba.lcut(text) if t.strip()]
    except ImportError:
        return _tokenize_simple(text)


def _get_sentence_transformer():
    """懒加载 sentence-transformers；失败则后续只用 BM25 + Ollama embed。"""
    global _ST_MODEL, _ST_LOAD_FAILED, _ST_NO_MODEL_LOGGED
    if _ST_LOAD_FAILED:
        return None
    if _ST_MODEL is not None:
        return _ST_MODEL
    try:
        from sentence_transformers import SentenceTransformer

        from src.config import (
            qna_sentence_transformer_offline_guard,
            resolve_qna_sentence_transformer_model,
        )

        model_name = resolve_qna_sentence_transformer_model()
        if not model_name:
            if not _ST_NO_MODEL_LOGGED:
                logger.info(
                    "问答检索: 未配置句向量（运行 scripts/download_qna_embedding_model.py 至 models/qna_embedding，"
                    "或设置 A23_QNA_SENTENCE_TRANSFORMER）；跳过 sentence-transformers，使用 BM25 / Ollama 向量"
                )
                _ST_NO_MODEL_LOGGED = True
            return None
        with qna_sentence_transformer_offline_guard(model_name):
            _ST_MODEL = SentenceTransformer(model_name)
        logger.info("问答检索: 已加载 sentence-transformers 模型 %s", model_name)
        return _ST_MODEL
    except Exception as e:
        _ST_LOAD_FAILED = True
        logger.warning("sentence-transformers 未加载（将使用 Ollama 向量或纯 BM25）: %s", e)
        return None


def _load_embed_cache(session_dir: Optional[Path]) -> dict:
    if not session_dir:
        return {}
    p = session_dir / "embedding_cache.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_embed_cache(session_dir: Optional[Path], cache: dict) -> None:
    if not session_dir:
        return
    (session_dir / "embedding_cache.json").write_text(
        json.dumps(cache, ensure_ascii=False),
        encoding="utf-8",
    )


def _scores_bm25_okapi(chunks: List[dict], question: str) -> Optional[List[float]]:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.warning("rank_bm25 未安装，跳过 BM25Okapi")
        return None
    corpus = [_tokenize_for_bm25(str(c.get("text", ""))) for c in chunks]
    if not corpus or all(not row for row in corpus):
        return None
    try:
        bm25 = BM25Okapi(corpus)
        q = _tokenize_for_bm25(question)
        if not q:
            q = _tokenize_simple(question)
        return list(bm25.get_scores(q))
    except Exception as e:
        logger.warning("BM25Okapi 评分失败: %s", e)
        return None


def _scores_embedding_st(chunks: List[dict], question: str) -> Optional[List[float]]:
    model = _get_sentence_transformer()
    if model is None:
        return None
    texts = [str(c.get("text", "")) for c in chunks]
    try:
        import numpy as np

        q = model.encode(question, normalize_embeddings=True)
        d = model.encode(texts, normalize_embeddings=True)
        sim = np.dot(d, q)
        return sim.tolist()
    except Exception as e:
        logger.warning("sentence-transformers 编码失败: %s", e)
        return None


def _cosine_safe(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb + 1e-9)


def _scores_embedding_ollama(
    chunks: List[dict],
    question: str,
    session_dir: Optional[Path],
) -> Optional[List[float]]:
    try:
        from src.adapters.model_client import call_embedding
    except ImportError:
        return None
    cache = _load_embed_cache(session_dir)
    updated = False
    try:
        q_vec = call_embedding(question)
    except Exception as e:
        logger.warning("Embedding API 不可用: %s", e)
        return None

    out: List[float] = []
    for c in chunks:
        key = _chunk_key(c)
        if key in cache:
            c_vec = cache[key]
        else:
            try:
                c_vec = call_embedding(c["text"])
            except Exception:
                return None
            cache[key] = c_vec
            updated = True
        out.append(_cosine_safe(q_vec, c_vec))

    if session_dir and updated:
        _save_embed_cache(session_dir, cache)
    return out


def _minmax(vals: List[float]) -> List[float]:
    if not vals:
        return vals
    mn, mx = min(vals), max(vals)
    if mx - mn < 1e-12:
        return [0.0] * len(vals)
    return [(v - mn) / (mx - mn) for v in vals]


def hybrid_retrieve_chunks(
    chunks: List[dict],
    question: str,
    top_k: int,
    *,
    session_dir: Optional[Path] = None,
) -> List[dict]:
    """混合检索：向量（ST 优先，否则 Ollama embed）+ BM25Okapi，分数加权融合。"""
    if not chunks:
        return []

    alpha = float(os.environ.get("A23_QNA_HYBRID_ALPHA", "0.65"))
    alpha = min(1.0, max(0.0, alpha))

    bm_scores = _scores_bm25_okapi(chunks, question)
    bm_ok = bm_scores is not None

    emb_scores = _scores_embedding_st(chunks, question)
    method_vec = "embedding_st"
    if emb_scores is None:
        emb_scores = _scores_embedding_ollama(chunks, question, session_dir)
        method_vec = "embedding_api"

    emb_ok = emb_scores is not None

    if not bm_ok:
        bm_scores = [0.0] * len(chunks)
    if not emb_ok:
        emb_scores = [0.0] * len(chunks)
        method_vec = "none"

    nb = _minmax(bm_scores) if bm_ok else [0.0] * len(chunks)
    ne = _minmax(emb_scores) if emb_ok else [0.0] * len(chunks)

    emb_dead = not emb_ok
    bm_dead = not bm_ok
    if emb_dead and bm_dead:
        return [{**chunks[i], "score": 0.0, "method": "none"} for i in range(min(top_k, len(chunks)))]

    if emb_dead:
        combined = nb
        tag = "bm25"
    elif bm_dead:
        combined = ne
        tag = method_vec
    else:
        combined = [alpha * ne[i] + (1.0 - alpha) * nb[i] for i in range(len(chunks))]
        tag = f"hybrid({method_vec}+bm25)"

    combined = apply_chunk_type_boost(combined, chunks)

    indexed = list(enumerate(combined))
    indexed.sort(key=lambda x: x[1], reverse=True)
    out: List[dict] = []
    for idx, sc in indexed[:top_k]:
        c = dict(chunks[idx])
        c["score"] = float(sc)
        c["method"] = tag
        out.append(c)
    return out
