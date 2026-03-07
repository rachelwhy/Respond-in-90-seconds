from typing import List


class RAGEngine:
    """通用 RAG 引擎：实现非结构化文本的语义路由"""

    @staticmethod
    def sliding_window_chunk(text: str, size: int = 1000, overlap: int = 200) -> List[str]:
        """滑动窗口切片，确保语义不被物理截断"""
        chunks = []
        for i in range(0, len(text), size - overlap):
            chunks.append(text[i:i + size])
        return chunks

    @staticmethod
    def retrieve(query_keys: List[str], chunks: List[str], top_k: int = 3) -> str:
        """基于语义探针的上下文召回"""
        if not query_keys:
            # 没有查询关键词时返回开头几个片段
            return "\n---\n".join(chunks[:top_k])

        scored_chunks = []
        for chunk in chunks:
            # 统计目标字段在分片中的提及频率
            score = sum(1 for key in query_keys if str(key).lower() in chunk.lower())
            scored_chunks.append((score, chunk))

        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        return "\n---\n".join([c[1] for c in scored_chunks[:top_k]])


rag_engine = RAGEngine()