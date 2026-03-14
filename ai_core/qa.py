"""
问答引擎模块：基于RAG的智能问答
从你的代码迁移：qa_engine.py 核心功能
"""

from typing import List, Dict, Any, Optional, Tuple
from .llm import llm_client
from .retriever import retriever
from . import prompts
import re


class QAEngine:
    """
    智能问答引擎：完全独立，只做问答
    基于RAG检索增强生成
    """

    def __init__(self, confidence_threshold: float = 0.7):
        """
        初始化问答引擎
        :param confidence_threshold: 置信度阈值，低于此值认为不可靠
        """
        self.confidence_threshold = confidence_threshold

    def answer(self,
               question: str,
               documents: List[str],
               doc_sources: Optional[List[str]] = None,
               top_k: int = 3) -> Dict[str, Any]:
        """
        回答问题
        :param question: 用户问题
        :param documents: 文档列表（每个文档是字符串）
        :param doc_sources: 文档来源标记（如文件名）
        :param top_k: 检索的文档片段数
        :return: {
            "data": {
                "answer": "答案内容",
                "evidence": "支撑答案的原文片段",
                "sources": ["来源1", "来源2"]
            },
            "confidence": 0.95,
            "needs_human_review": false
        }
        """
        # 1. 检索相关片段
        retrieved_chunks, chunk_sources, chunk_scores = self._retrieve_chunks(
            question, documents, doc_sources, top_k
        )

        if not retrieved_chunks:
            return {
                "data": {
                    "answer": "没有找到相关信息",
                    "evidence": "",
                    "sources": []
                },
                "confidence": 0.0,
                "needs_human_review": False
            }

        # 2. 构建带来源的上下文
        context = self._build_context(retrieved_chunks, chunk_sources)

        # 3. 生成答案并评估置信度
        return self._generate_answer(question, context, chunk_sources, chunk_scores)

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
            # 复用retriever的切片功能
            chunks = retriever._chunk_text(doc, size=800, overlap=150)
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
        基于关键词匹配的简单实现
        """
        # 提取问题关键词
        keywords = re.findall(r'\w+', question.lower())
        keywords = [k for k in keywords if len(k) > 1]  # 过滤单字符

        if not keywords:
            return 0.5

        chunk_lower = chunk.lower()

        # 计算关键词匹配数量
        matches = sum(1 for k in keywords if k in chunk_lower)

        # 基础得分
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

    def _generate_answer(self,
                         question: str,
                         context: str,
                         sources: List[str],
                         scores: List[float]) -> Dict[str, Any]:
        """
        生成答案并评估置信度
        """
        prompt = f"""
你是一个智能问答助手，需要基于提供的文档片段回答问题。

文档片段：
{context}

问题：{question}

要求：
1. 如果文档片段中包含答案，请给出准确回答，注意紧抓文件主题，严谨推理
2. 如果文档片段中没有足够信息，请明确说明，并归纳和问题强相关的信息，给用户适宜引导
3. 引用支撑答案的原文片段作为证据
4. 评估答案的可靠性（0-1之间）

输出格式：
{{
  "answer": "你的回答",
  "evidence": "支撑答案的关键证据片段",
  "confidence": 0.95
}}

现在开始回答：
"""

        result = llm_client.request(prompt, is_json=True)

        # 处理返回值
        if not isinstance(result, dict):
            answer = str(result) if result else "无法生成答案"
            confidence = 0.5
            evidence = ""
        else:
            answer = result.get("answer", "")
            confidence = result.get("confidence", 0.5)
            evidence = result.get("evidence", "")

        # 确保置信度在0-1之间
        if not isinstance(confidence, (int, float)):
            confidence = 0.5
        confidence = max(0.0, min(1.0, float(confidence)))

        # 构建来源信息
        source_list = []
        for i, (src, score) in enumerate(zip(sources, scores)):
            source_list.append(f"{src} (相关度: {score:.2f})")

        return {
            "data": {
                "answer": answer,
                "evidence": evidence,
                "sources": source_list
            },
            "confidence": confidence,
            "needs_human_review": confidence < self.confidence_threshold
        }


# 全局单例
qa_engine = QAEngine(confidence_threshold=0.7)