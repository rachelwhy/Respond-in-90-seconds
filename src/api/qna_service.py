"""
文档问答服务（M1 - Q&A 部分）

职责边界：
- 接收问题 + 文件列表，返回基于文档内容的回答
- 实现分块检索：将文档切块 → 向量余弦相似度（主）/ BM25关键词（备）→ 取 top_k 喂给 LLM
- 若 LangChain + Chroma 可用，使用 ConversationalRetrievalChain 提升对话体验（降级安全）
- 会话文件暂存 storage/sessions/
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from src.core.reader import collect_all_text, collect_input_bundle

SESSIONS_ROOT = Path("storage/sessions")
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)

# ── LangChain 可选集成 ──────────────────────────────────────────────────────
# 若 langchain/chromadb 已安装，使用 ConversationalRetrievalChain
# 若未安装，自动降级到内置手写 RAG 逻辑（无任何功能损失）
_LANGCHAIN_AVAILABLE = False
try:
    from langchain.chains import ConversationalRetrievalChain
    from langchain_community.vectorstores import Chroma
    from langchain_community.embeddings import OllamaEmbeddings
    from langchain.schema import Document as LCDocument
    from langchain.memory import ConversationBufferMemory
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    pass


def _chunk_key(chunk: dict) -> str:
    """生成 chunk 的缓存键（用 text hash，兼容无 start 偏移的语义块）"""
    return hashlib.md5(chunk["text"].encode("utf-8", errors="replace")).hexdigest()[:16]


def _collect_semantic_chunks(documents: list) -> List[dict]:
    """从 Docling 解析结果提取语义块（表格/段落边界对齐）

    每个 chunk 携带 source_file 字段，直接用于来源标注，无需偏移量反推。
    """
    all_chunks = []
    for doc in documents:
        fname = Path(doc.get("path", "unknown")).name
        doc_chunks = doc.get("chunks", [])
        if not doc_chunks:
            continue
        for chunk in doc_chunks:
            text = chunk.get("text", "").strip()
            if not text:
                continue
            all_chunks.append({
                "text": text,
                "type": chunk.get("type", "text"),   # "text" 或 "table"
                "source_file": fname,
                "start": -1,  # 语义块不需要字符偏移
                "end": -1,
            })
    return all_chunks


# ─────────────────────────────────────────────
# 文本分块（字符级，fallback）
# ─────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[dict]:
    """将文本切分为带来源位置信息的块"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        # 尝试在句号/换行处截断，避免切断句子
        if end < len(text):
            for sep in ('。', '\n', '；', '.', ';'):
                pos = text.rfind(sep, start + chunk_size // 2, end)
                if pos != -1:
                    end = pos + 1
                    break
        chunks.append({
            "text": text[start:end],
            "start": start,
            "end": end,
        })
        if end >= len(text):
            break
        start = end - overlap
    return chunks


# ─────────────────────────────────────────────
# 关键词检索评分（BM25 fallback）
# ─────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """简单中英文分词：按字符分割中文词，按空格分割英文词"""
    tokens = re.findall(r'[a-zA-Z0-9]+', text.lower())
    cn_chars = re.findall(r'[\u4e00-\u9fff]+', text)
    for seg in cn_chars:
        tokens.extend(list(seg))
        for i in range(len(seg) - 1):
            tokens.append(seg[i:i+2])
    return tokens


def _score_chunk_bm25(chunk_text: str, query_tokens: List[str]) -> float:
    """BM25 简化版评分：命中词频 / 块长度归一化"""
    if not query_tokens:
        return 0.0
    ct = chunk_text.lower()
    hits = sum(1 for t in query_tokens if t in ct)
    import math
    length_penalty = 1.0 / (1.0 + math.log(len(chunk_text) + 1) * 0.05)
    return (hits / len(query_tokens)) * (1.0 + length_penalty)


# ─────────────────────────────────────────────
# 向量检索（主路径）
# ─────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    return dot / (norm_a * norm_b + 1e-9)


def _load_embedding_cache(session_dir: Path) -> dict:
    cache_file = session_dir / "embedding_cache.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_embedding_cache(session_dir: Path, cache: dict):
    (session_dir / "embedding_cache.json").write_text(
        json.dumps(cache, ensure_ascii=False),
        encoding="utf-8",
    )


def retrieve_chunks(
    text: str,
    question: str,
    top_k: int = 5,
    chunk_size: int = 500,
    overlap: int = 80,
    session_dir: Optional[Path] = None,
) -> List[dict]:
    """将文本字符分块，按问题相关度返回 top_k 块（无 Docling 语义块时的 fallback）。"""
    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return []
    return _retrieve_from_chunks(chunks, question, top_k=top_k, session_dir=session_dir)


def _retrieve_from_chunks(
    chunks: List[dict],
    question: str,
    top_k: int = 5,
    session_dir: Optional[Path] = None,
) -> List[dict]:
    """对任意 chunks 列表评分并返回 top_k。

    优先向量余弦相似度；不可用时退回 BM25。
    适用于 Docling 语义块和字符分块两种来源。
    """
    if not chunks:
        return []

    # ── 尝试向量检索 ──────────────────────────────────────────
    try:
        from src.adapters.model_client import call_embedding

        cache: dict = _load_embedding_cache(session_dir) if session_dir else {}
        q_vec = call_embedding(question)

        scored = []
        cache_updated = False
        for c in chunks:
            key = _chunk_key(c)
            if key in cache:
                c_vec = cache[key]
            else:
                c_vec = call_embedding(c["text"])
                cache[key] = c_vec
                cache_updated = True
            score = _cosine_similarity(q_vec, c_vec)
            scored.append({**c, "score": score, "method": "embedding"})

        if session_dir and cache_updated:
            _save_embedding_cache(session_dir, cache)

        scored = _apply_type_boost(scored)
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("向量检索不可用，退回 BM25: %s", e)

    # ── BM25 fallback ──────────────────────────────────────────
    query_tokens = _tokenize(question)
    scored = [{**c, "score": _score_chunk_bm25(c["text"], query_tokens), "method": "bm25"} for c in chunks]
    scored = _apply_type_boost(scored)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


_CHUNK_TYPE_BOOST = {"table": 1.2, "formula": 1.1, "code": 1.05}


def _apply_type_boost(scored: List[dict]) -> List[dict]:
    """对表格、公式、代码块的分数乘以加权系数"""
    for c in scored:
        boost = _CHUNK_TYPE_BOOST.get(c.get("type", "text"), 1.0)
        if boost != 1.0:
            c["score"] = c["score"] * boost
    return scored


# ─────────────────────────────────────────────
# 多轮会话历史管理
# ─────────────────────────────────────────────

def _load_history(session_dir: Path) -> List[dict]:
    hist_file = session_dir / "history.json"
    if hist_file.exists():
        try:
            return json.loads(hist_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_history(session_dir: Path, history: List[dict]):
    (session_dir / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ─────────────────────────────────────────────
# 来源文件定位
# ─────────────────────────────────────────────

def _find_source_file(char_offset: int, files: List[Tuple[str, bytes]], input_dir: Path) -> str:
    """根据字符偏移量估算来源文件"""
    try:
        from src.adapters.parser_factory import get_parser
        cumulative = 0
        for name, _ in files:
            fpath = input_dir / name
            if not fpath.exists():
                continue
            parser = get_parser(fpath)
            if parser is None:
                continue
            try:
                result = parser.parse(fpath)
                file_text = result.get("text", "") if isinstance(result, dict) else ""
                if cumulative <= char_offset < cumulative + len(file_text):
                    return name
                cumulative += len(file_text) + 2
            except Exception:
                pass
    except Exception:
        pass
    return files[0][0] if files else "unknown"


def _answer_with_langchain(
    question: str,
    semantic_chunks: List[dict],
    session_id: str,
    work_dir: Path,
    top_k: int = 5,
) -> Optional[str]:
    """使用 LangChain ConversationalRetrievalChain 进行问答。

    返回回答字符串，若失败则返回 None（调用方降级到手写 RAG）。
    """
    if not _LANGCHAIN_AVAILABLE:
        return None
    try:
        from langchain_ollama import OllamaLLM
        from src.config import OLLAMA_URL, MODEL_NAME

        # 将语义块转换为 LangChain Document 对象
        lc_docs = [
            LCDocument(
                page_content=c["text"],
                metadata={"source": c.get("source_file", ""), "type": c.get("type", "text")}
            )
            for c in semantic_chunks if c.get("text")
        ]
        if not lc_docs:
            return None

        # 构建向量库（存到 session 目录以复用）
        chroma_dir = str(work_dir / "chroma_db")
        embeddings = OllamaEmbeddings(model="nomic-embed-text", base_url=OLLAMA_URL.rsplit("/api", 1)[0])
        vectorstore = Chroma.from_documents(lc_docs, embeddings, persist_directory=chroma_dir)

        # LLM（使用 LangChain 封装的 Ollama）
        llm = OllamaLLM(model=MODEL_NAME, base_url=OLLAMA_URL.rsplit("/api", 1)[0])

        # 对话历史（无状态复用；用 ConversationBufferMemory 维护）
        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        history = _load_history(work_dir)
        for h in history[-4:]:
            memory.save_context({"input": h["q"]}, {"output": h["a"]})

        chain = ConversationalRetrievalChain.from_llm(
            llm=llm,
            retriever=vectorstore.as_retriever(search_kwargs={"k": top_k}),
            memory=memory,
        )
        result = chain.invoke({"question": question})
        return result.get("answer") or str(result)
    except Exception:
        return None


# ─────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────

def answer_question(
    question: str,
    files: List[Tuple[str, bytes]],
    session_id: Optional[str] = None,
    top_k: int = 5,
) -> dict:
    """回答用户关于文档的问题（支持多轮对话 + 分块检索）

    Args:
        question:   用户问题
        files:      [(filename, content_bytes), ...]
        session_id: 会话 ID（可选，用于多轮对话）
        top_k:      检索段落数量（1-20）

    Returns:
        {"answer": str, "session_id": str, "sources": [...]}
    """
    qna_id = session_id or uuid.uuid4().hex[:12]
    work_dir = SESSIONS_ROOT / qna_id
    input_dir = work_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传文件
    for name, content in files:
        (input_dir / name).write_bytes(content)

    # 解析所有文档（保留语义块等结构化信息）
    bundle = collect_input_bundle(str(input_dir))
    all_text = bundle.get("all_text", "")

    if not all_text.strip():
        return {
            "answer": "无法从上传文件中提取文本内容",
            "session_id": qna_id,
            "sources": [],
        }

    # 优先使用 Docling 语义块；若无则回退到字符分块
    top_k_clamped = min(max(top_k, 1), 20)
    semantic_chunks = _collect_semantic_chunks(bundle.get("documents", []))

    # ── LangChain 路径（若可用）──────────────────────────────────────────
    if _LANGCHAIN_AVAILABLE and semantic_chunks:
        lc_answer = _answer_with_langchain(
            question=question,
            semantic_chunks=semantic_chunks,
            session_id=qna_id,
            work_dir=work_dir,
            top_k=top_k_clamped,
        )
        if lc_answer is not None:
            # 保存历史
            history = _load_history(work_dir)
            history.append({"q": question, "a": lc_answer, "t": int(time.time())})
            _save_history(work_dir, history)
            return {
                "answer": lc_answer,
                "session_id": qna_id,
                "sources": [],  # LangChain 路径暂不返回细粒度来源
                "method": "langchain",
            }

    # ── 手写 RAG 路径（降级）──────────────────────────────────────────────
    if semantic_chunks:
        # 已有语义块 → 直接检索，跳过字符分块
        relevant_chunks = _retrieve_from_chunks(semantic_chunks, question, top_k=top_k_clamped, session_dir=work_dir)
    else:
        relevant_chunks = retrieve_chunks(all_text, question, top_k=top_k_clamped, session_dir=work_dir)

    # 构建检索上下文和来源列表
    context_parts = []
    sources = []
    for i, chunk in enumerate(relevant_chunks, start=1):
        context_parts.append(f"[片段{i}]\n{chunk['text']}")
        # 语义块直接携带 source_file；字符块用偏移量反推
        if chunk.get("source_file"):
            src_file = chunk["source_file"]
        else:
            src_file = _find_source_file(chunk["start"], files, input_dir)
        sources.append({
            "file": src_file,
            "excerpt": chunk["text"][:200].strip(),
            "score": round(chunk["score"], 3),
            "method": chunk.get("method", "bm25"),
            "chunk_type": chunk.get("type", "text"),
        })
    context = "\n\n".join(context_parts)

    # 加入对话历史（最近 4 轮）
    history = _load_history(work_dir)
    history_text = ""
    if history:
        recent = history[-4:]
        history_text = "\n".join([f"用户：{h['q']}\n助手：{h['a']}" for h in recent]) + "\n\n"

    # 调用 LLM
    try:
        from src.adapters.model_client import call_model
        prompt = (
            f"{history_text}"
            "请根据以下文档片段回答问题。若文档中没有相关信息，请明确说明'文档中未找到相关信息'。\n\n"
            f"问题：{question}\n\n"
            f"文档片段：\n{context}\n\n"
            f"请给出准确、简洁的回答："
        )
        answer = call_model(prompt)
        if isinstance(answer, dict):
            answer = answer.get("answer") or str(answer)
    except Exception as e:
        answer = f"模型调用失败: {e}"

    # 保存历史
    history.append({
        "q": question,
        "a": answer if isinstance(answer, str) else str(answer),
        "t": int(time.time()),
    })
    _save_history(work_dir, history)

    return {
        "answer": answer,
        "session_id": qna_id,
        "sources": sources,
    }
