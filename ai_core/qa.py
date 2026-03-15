"""
RAG问答引擎：基于检索增强生成的智能问答
支持多文档、证据溯源、会话记忆、流式输出
完全独立模块，可被interactive.py调用，也可单独使用
"""

from typing import List, Dict, Any, Optional, Tuple, Generator, Union
from .llm import llm_client
from .retriever import retriever
import re
import hashlib
import json
import time
import os
from functools import lru_cache


class QASession:
    """
    问答会话：管理对话历史
    每个会话独立，支持多轮对话
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or hashlib.md5(str(time.time()).encode()).hexdigest()[:8]
        self.history = []  # [(question, answer, evidence), ...]
        self.created_at = time.time()
        self.last_active = time.time()

    def add(self, question: str, answer: str, evidence: str = ""):
        """添加一轮对话到历史"""
        self.history.append({
            "question": question,
            "answer": answer,
            "evidence": evidence,
            "timestamp": time.time()
        })
        self.last_active = time.time()

    def get_context(self, max_rounds: int = 3) -> str:
        """获取最近的对话历史作为上下文"""
        if not self.history:
            return ""

        recent = self.history[-max_rounds:]
        context_parts = []
        for item in recent:
            context_parts.append(f"用户：{item['question']}")
            context_parts.append(f"助手：{item['answer']}")

        return "\n".join(context_parts)

    def clear(self):
        """清空历史"""
        self.history = []

    def to_dict(self) -> Dict:
        """序列化会话"""
        return {
            "session_id": self.session_id,
            "history": self.history,
            "created_at": self.created_at,
            "last_active": self.last_active
        }


class QAEngine:
    """
    智能问答引擎：基于RAG的问答系统
    支持：
    - 多文档问答
    - 证据溯源
    - 会话记忆
    - 结果缓存
    - 流式输出
    """

    def __init__(self, use_cache: bool = True):
        """
        初始化问答引擎
        :param use_cache: 是否使用缓存（相同问题和文档直接返回历史结果）
        """
        self.use_cache = use_cache
        self.sessions: Dict[str, QASession] = {}  # 管理多个会话
        self._cache = {}  # 结果缓存

    def answer(self,
               question: str,
               documents: Union[List[str], str],
               doc_sources: Optional[List[str]] = None,
               top_k: int = 3,
               session_id: Optional[str] = None,
               use_history: bool = True) -> Dict[str, Any]:
        """
        回答问题（标准接口）
        :param question: 用户问题
        :param documents: 文档列表（每个文档是字符串）或单个文档路径
        :param doc_sources: 文档来源标记（如文件名）
        :param top_k: 检索的文档片段数
        :param session_id: 会话ID（用于多轮对话）
        :param use_history: 是否使用对话历史
        :return: {
            "answer": "答案内容",
            "evidence": "支撑答案的原文片段",
            "sources": ["来源1 (相关度: 0.95)", ...],
            "session_id": "abc123",
            "cached": false
        }
        """
        # 处理单个文档路径
        if isinstance(documents, str):
            if documents.endswith(('.txt', '.md', '.docx')):
                from .loader import document_loader
                doc_info = document_loader.load(documents, os.path.basename(documents))
                if "error" not in doc_info:
                    if doc_info.get("type") in ["word", "markdown"]:
                        documents = ["\n".join(doc_info.get("paragraphs", []))]
                    else:
                        documents = [doc_info.get("text", "")]
                else:
                    documents = [""]
            else:
                documents = [documents]

        # 检查缓存
        cache_key = self._get_cache_key(question, documents, top_k)
        if self.use_cache and cache_key in self._cache:
            result = self._cache[cache_key].copy()
            result["cached"] = True
            return result

        # 获取或创建会话
        session = None
        if session_id and session_id in self.sessions:
            session = self.sessions[session_id]
        elif session_id:
            session = QASession(session_id)
            self.sessions[session_id] = session

        # 1. 检索相关片段
        retrieved_chunks, chunk_sources, chunk_scores = self._retrieve_chunks(
            question, documents, doc_sources, top_k
        )

        if not retrieved_chunks:
            result = {
                "answer": "没有找到相关信息",
                "evidence": "",
                "sources": []
            }
            if session_id:
                result["session_id"] = session_id
            return result

        # 2. 构建带来源和历史的上下文
        context = self._build_context(retrieved_chunks, chunk_sources)
        if session and use_history:
            history_context = session.get_context()
            if history_context:
                context = f"对话历史：\n{history_context}\n\n当前文档片段：\n{context}"

        # 3. 生成答案
        result = self._generate_answer(question, context, chunk_sources, chunk_scores)

        # 4. 保存到会话
        if session:
            session.add(question, result["answer"], result["evidence"])
            result["session_id"] = session.session_id

        # 5. 缓存结果
        if self.use_cache:
            self._cache[cache_key] = result.copy()
            result["cached"] = False

        return result

    def answer_stream(self,
                     question: str,
                     documents: List[str],
                     doc_sources: Optional[List[str]] = None,
                     top_k: int = 3,
                     session_id: Optional[str] = None) -> Generator[str, None, None]:
        """
        流式回答问题（边生成边返回）
        适用于需要实时显示的场景
        """
        # 1. 检索相关片段
        retrieved_chunks, chunk_sources, chunk_scores = self._retrieve_chunks(
            question, documents, doc_sources, top_k
        )

        if not retrieved_chunks:
            yield json.dumps({"type": "error", "content": "没有找到相关信息"})
            return

        # 2. 构建上下文
        context = self._build_context(retrieved_chunks, chunk_sources)

        # 3. 流式生成答案
        prompt = self._build_qa_prompt(question, context)

        full_answer = ""
        for chunk in llm_client.request_stream(prompt, is_json=False):
            full_answer += chunk
            yield json.dumps({"type": "chunk", "content": chunk})

        # 4. 最后返回完整结果
        result = {
            "type": "complete",
            "answer": full_answer,
            "evidence": retrieved_chunks[0] if retrieved_chunks else "",
            "sources": [f"{src} (相关度: {score:.2f})" for src, score in zip(chunk_sources, chunk_scores)]
        }
        yield json.dumps(result)

    def _retrieve_chunks(self,
                         question: str,
                         documents: List[str],
                         doc_sources: Optional[List[str]],
                         top_k: int) -> Tuple[List[str], List[str], List[float]]:
        """
        检索相关文档片段
        返回: (chunks, sources, scores)
        """
        all_chunks = []
        all_sources = []

        # 对每个文档切片
        for idx, doc in enumerate(documents):
            # 使用retriever的切片功能（复用现有模块）
            chunks = retriever._chunk_text(doc, size=500, overlap=100)
            source = doc_sources[idx] if doc_sources and idx < len(doc_sources) else f"文档{idx + 1}"

            for chunk in chunks:
                all_chunks.append(chunk["text"])
                all_sources.append(source)

        if not all_chunks:
            return [], [], []

        # 计算每个片段与问题的相关性
        chunk_scores = []
        for chunk in all_chunks:
            score = self._compute_relevance(question, chunk)
            chunk_scores.append(score)

        # 按得分排序，取top_k
        indexed_scores = list(enumerate(chunk_scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)

        top_indices = [idx for idx, _ in indexed_scores[:top_k]]

        retrieved_chunks = [all_chunks[idx] for idx in top_indices]
        retrieved_sources = [all_sources[idx] for idx in top_indices]
        retrieved_scores = [chunk_scores[idx] for idx in top_indices]

        return retrieved_chunks, retrieved_sources, retrieved_scores

    def _compute_relevance(self, question: str, chunk: str) -> float:
        """
        计算片段与问题的相关性得分
        简单关键词匹配
        """
        # 提取问题关键词
        keywords = re.findall(r'\w+', question.lower())
        keywords = [k for k in keywords if len(k) > 1]

        if not keywords:
            return 0.5

        chunk_lower = chunk.lower()

        # 计算关键词匹配数量
        matches = sum(1 for k in keywords if k in chunk_lower)
        base_score = matches / len(keywords)

        # 如果片段包含问题中的短语，加分
        for word in question.split():
            if len(word) > 2 and word.lower() in chunk_lower:
                base_score += 0.1

        return min(1.0, base_score)

    def _build_context(self, chunks: List[str], sources: List[str]) -> str:
        """构建带来源的上下文"""
        context_parts = []
        for i, (chunk, source) in enumerate(zip(chunks, sources)):
            context_parts.append(f"[片段{i + 1} 来源:{source}]\n{chunk}")

        return "\n\n".join(context_parts)

    def _build_qa_prompt(self, question: str, context: str) -> str:
        """构造问答prompt"""
        return f"""
你是一个智能问答助手，需要基于提供的文档片段回答问题。

文档片段：
{context}

问题：{question}

要求：
1. 如果文档片段中包含答案，请给出准确回答
2. 如果文档片段中没有足够信息，请明确说明
3. 引用支撑答案的原文片段作为证据

输出格式：
{{
  "answer": "你的回答",
  "evidence": "支撑答案的关键证据片段"
}}

现在开始回答：
"""

    def _generate_answer(self,
                         question: str,
                         context: str,
                         sources: List[str],
                         scores: List[float]) -> Dict[str, Any]:
        """
        生成答案
        """
        prompt = self._build_qa_prompt(question, context)

        result = llm_client.request(prompt, is_json=True)

        # 处理返回值
        if not isinstance(result, dict):
            answer = str(result) if result else "无法生成答案"
            evidence = ""
        else:
            answer = result.get("answer", "无法生成答案")
            evidence = result.get("evidence", "")

        # 构建来源信息
        source_list = []
        for i, (src, score) in enumerate(zip(sources, scores)):
            source_list.append(f"{src} (相关度: {score:.2f})")

        return {
            "answer": answer,
            "evidence": evidence,
            "sources": source_list
        }

    def _get_cache_key(self, question: str, documents: List[str], top_k: int) -> str:
        """生成缓存键"""
        content = question + "|||" + "|||".join(documents[:3]) + f"|||{top_k}"
        return hashlib.md5(content.encode()).hexdigest()

    def clear_cache(self):
        """清空缓存"""
        self._cache.clear()

    def get_session(self, session_id: str) -> Optional[QASession]:
        """获取会话"""
        return self.sessions.get(session_id)

    def create_session(self) -> str:
        """创建新会话"""
        session = QASession()
        self.sessions[session.session_id] = session
        return session.session_id

    def delete_session(self, session_id: str):
        """删除会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]

    def list_sessions(self) -> List[Dict]:
        """列出所有会话"""
        return [s.to_dict() for s in self.sessions.values()]


# 全局单例
qa_engine = QAEngine(use_cache=True)


# ==================== 便捷函数 ====================

def ask(question: str,
        documents: Union[List[str], str],
        doc_sources: Optional[List[str]] = None,
        session_id: Optional[str] = None) -> Dict:
    """
    快捷问答函数
    """
    return qa_engine.answer(question, documents, doc_sources, session_id=session_id)


def create_session() -> str:
    """创建新会话"""
    return qa_engine.create_session()


def chat(question: str,
         documents: Union[List[str], str],
         session_id: str) -> Dict:
    """
    多轮对话（自动使用历史）
    """
    return qa_engine.answer(question, documents, session_id=session_id, use_history=True)