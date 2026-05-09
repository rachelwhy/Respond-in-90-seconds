"""
文档问答：LangChain ConversationalRetrievalChain + Chroma + HuggingFaceEmbeddings。

作为默认主路径（见 ``QNA_USE_LANGCHAIN``）；失败时由 ``qna_service`` 回退至混合检索。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _build_chat_model(model_type: Optional[str]):
    """按 ``MODEL_TYPE`` / 请求覆盖构造 LangChain Chat 模型（OpenAI 兼容优先 ``langchain_openai.ChatOpenAI``）。"""
    from langchain_community.chat_models import ChatOllama

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:  # 兼容旧环境；优先 pip install langchain-openai
        from langchain_community.chat_models import ChatOpenAI

    from src.config import (
        DEEPSEEK_API_KEY,
        DEEPSEEK_BASE_URL,
        DEEPSEEK_MODEL,
        OLLAMA_MODEL,
        OLLAMA_URL,
        OPENAI_API_KEY,
        OPENAI_BASE_URL,
        OPENAI_MODEL,
        QWEN_API_KEY,
        QWEN_BASE_URL,
        QWEN_MODEL,
        TEMPERATURE,
        resolve_qna_chat_model_type,
    )

    mt = resolve_qna_chat_model_type(model_type)
    temp = float(TEMPERATURE) if TEMPERATURE is not None else 0.3

    if mt == "ollama":
        base = OLLAMA_URL.rsplit("/api", 1)[0] if "/api" in OLLAMA_URL else OLLAMA_URL.replace("/api/generate", "").rstrip("/")
        return ChatOllama(model=OLLAMA_MODEL, base_url=base, temperature=temp)

    if mt == "deepseek":
        return ChatOpenAI(
            api_key=DEEPSEEK_API_KEY or "dummy",
            base_url=DEEPSEEK_BASE_URL.rstrip("/"),
            model=DEEPSEEK_MODEL,
            temperature=temp,
        )

    if mt == "qwen":
        return ChatOpenAI(
            api_key=QWEN_API_KEY or "dummy",
            base_url=QWEN_BASE_URL.rstrip("/"),
            model=QWEN_MODEL,
            temperature=temp,
        )

    if mt == "openai":
        return ChatOpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL.rstrip("/"),
            model=OPENAI_MODEL,
            temperature=temp,
        )

    return ChatOpenAI(
        api_key=DEEPSEEK_API_KEY or "dummy",
        base_url=DEEPSEEK_BASE_URL.rstrip("/"),
        model=DEEPSEEK_MODEL,
        temperature=temp,
    )


def run_langchain_qna(
    *,
    question: str,
    all_chunks: List[dict],
    work_dir: Path,
    history: List[dict],
    top_k: int,
    model_type: Optional[str],
    persist_session: bool,
) -> Optional[Tuple[str, List[dict]]]:
    """执行 ConversationalRetrievalChain；成功返回 ``(answer, sources)``，失败返回 ``None``。"""
    try:
        from langchain.chains import ConversationalRetrievalChain
        from langchain.memory import ConversationBufferMemory
        from langchain_community.embeddings import HuggingFaceEmbeddings
        from langchain_community.vectorstores import Chroma
        from langchain_core.documents import Document
    except ImportError as e:
        logger.warning("LangChain 组件未安装或版本不兼容，跳过 LangChain 问答: %s", e)
        return None

    from src.config import (
        qna_sentence_transformer_offline_guard,
        resolve_qna_sentence_transformer_model,
    )

    if not all_chunks:
        return None

    texts = [str(c.get("text", "")).strip() for c in all_chunks]
    if not any(texts):
        return None

    model_name = resolve_qna_sentence_transformer_model()
    if not model_name:
        logger.info(
            "LangChain 问答: 未配置句向量模型（见 models/qna_embedding 或 A23_QNA_SENTENCE_TRANSFORMER），"
            "跳过 LangChain 路径"
        )
        return None

    try:
        with qna_sentence_transformer_offline_guard(model_name):
            embeddings = HuggingFaceEmbeddings(model_name=model_name)
    except Exception as e:
        logger.warning("HuggingFaceEmbeddings 初始化失败: %s", e)
        return None

    docs: List[Any] = []
    for i, c in enumerate(all_chunks):
        t = str(c.get("text", "")).strip()
        if not t:
            continue
        docs.append(
            Document(
                page_content=t,
                metadata={
                    "source": str(c.get("source_file", "unknown")),
                    "type": str(c.get("type", "text")),
                    "i": i,
                },
            )
        )

    if not docs:
        return None

    chroma_dir = work_dir / "chroma_qna"
    try:
        if persist_session and chroma_dir.exists():
            shutil.rmtree(chroma_dir, ignore_errors=True)
        if persist_session:
            chroma_dir.mkdir(parents=True, exist_ok=True)
            vectorstore = Chroma.from_documents(
                docs,
                embeddings,
                persist_directory=str(chroma_dir),
            )
        else:
            vectorstore = Chroma.from_documents(docs, embeddings)
    except Exception as e:
        logger.warning("Chroma 索引构建失败: %s", e)
        return None

    try:
        llm = _build_chat_model(model_type)
    except Exception as e:
        logger.warning("LangChain Chat 模型构造失败: %s", e)
        return None

    memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True, output_key="answer")
    for h in history[-4:]:
        q = str(h.get("q", "")).strip()
        a = str(h.get("a", "")).strip()
        if q or a:
            memory.save_context({"question": q}, {"answer": a})

    try:
        chain = ConversationalRetrievalChain.from_llm(
            llm=llm,
            retriever=vectorstore.as_retriever(search_kwargs={"k": top_k}),
            memory=memory,
            return_source_documents=True,
        )
        result = chain.invoke({"question": question})
    except Exception as e:
        logger.warning("ConversationalRetrievalChain 执行失败: %s", e)
        return None

    answer = result.get("answer")
    if answer is None:
        answer = str(result)
    answer = str(answer).strip()
    if not answer:
        return None

    sources: List[dict] = []
    for doc in result.get("source_documents") or []:
        pc = getattr(doc, "page_content", "") or ""
        meta = getattr(doc, "metadata", {}) or {}
        sources.append(
            {
                "file": meta.get("source", "unknown"),
                "excerpt": pc[:200].strip(),
                "score": None,
                "method": "langchain",
                "chunk_type": meta.get("type", "text"),
            }
        )

    return answer, sources
