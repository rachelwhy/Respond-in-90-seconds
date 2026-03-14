"""
检索模块：基于RAG的文档检索
"""

import numpy as np
from typing import List, Dict, Any, Optional, Union
import requests
import re
import jieba
from .llm import llm_client

# 初始化jieba
jieba.initialize()


class Retriever:
    """
    检索器：文档切片、向量检索、证据召回
    """

    def __init__(self, use_embedding: bool = True,
                 embedding_model: str = "nomic-embed-text"):
        self.use_embedding = use_embedding
        self.embedding_model = embedding_model
        self.embedding_cache = {}
        self._expand_cache = {}

    def retrieve(self,
                 document_text: str,
                 instruction: Union[str, List[str]],
                 filename: str,
                 top_k: int = 3,
                 field_top_k: Optional[Dict[str, int]] = None) -> Union[List[Dict], Dict[str, List[Dict]]]:
        """
        检索与指令最相关的证据片段
        参数：
            document_text: 文档全文
            instruction: 用户指令（字符串）或字段名列表
            filename: 文件名
            top_k: 全局默认检索数量
            field_top_k: 为特定字段指定的检索数量
        返回：
            如果 instruction 是字符串：List[Dict] 证据列表
            如果 instruction 是列表：Dict[str, List[Dict]] 每个字段对应的证据列表
        """
        # 1. 切片
        chunks = self._chunk_text(document_text)
        chunk_dicts = [{
            "text": c["text"],
            "start_line": c["start_line"],
            "end_line": c["end_line"]
        } for c in chunks]

        # 2. 处理不同类型指令
        if isinstance(instruction, str):
            query_keys = self._extract_keywords(instruction)
            retrieved = self._retrieve_with_scores(query_keys, chunk_dicts, top_k)
            return self._to_evidence(retrieved, filename)

        elif isinstance(instruction, list):
            result = {}
            field_top_k = field_top_k or {}
            for field_name in instruction:
                query_keys = self._extract_keywords(field_name)
                k = field_top_k.get(field_name, top_k)
                retrieved = self._retrieve_with_scores(query_keys, chunk_dicts, k)
                result[field_name] = self._to_evidence(retrieved, filename)
            return result

        else:
            raise ValueError("instruction 必须是字符串或列表")

    def _chunk_text(self, text: str, size: int = 300, overlap: int = 60) -> List[Dict]:
        """滑动窗口切片"""
        chunks = []
        lines = text.splitlines(keepends=True)
        offsets = []
        current = 0
        for line in lines:
            offsets.append(current)
            current += len(line)

        for i in range(0, len(text), size - overlap):
            end = min(i + size, len(text))
            chunk_text = text[i:end]

            # 计算行号
            start_line = 1
            for idx, offset in enumerate(offsets):
                if offset >= i:
                    start_line = idx + 1
                    break
            end_line = 1
            for idx, offset in enumerate(offsets):
                if offset >= end:
                    end_line = idx
                    break
                end_line = idx + 1

            chunks.append({
                "text": chunk_text,
                "start": i,
                "end": end,
                "start_line": start_line,
                "end_line": end_line
            })
        return chunks

    def _extract_keywords(self, text: str) -> List[str]:
        """从文本中提取关键词"""
        words = jieba.lcut(text)
        return [w for w in words if len(w) > 1] or [text]

    def _retrieve_with_scores(self, query_keys: List[str],
                               chunks: List[Dict],
                               top_k: int = 3) -> List[Dict]:
        """混合检索"""
        if not query_keys:
            return chunks[:top_k]

        # 查询扩展
        expanded = self._expand_query(query_keys)

        # 关键词得分
        scores = []
        for chunk in chunks:
            text = chunk["text"].lower()
            score = sum(1 for k in expanded if k.lower() in text)
            scores.append(score)

        # 取候选
        indices = np.argsort(scores)[-2*top_k:][::-1]
        candidates = [(chunks[i], scores[i]) for i in indices]

        if not self.use_embedding:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return [{
                "text": c[0]["text"],
                "score": c[1] / max(len(expanded), 1),
                "start_line": c[0]["start_line"],
                "end_line": c[0]["end_line"]
            } for c in candidates[:top_k]]

        # 向量重排
        query_emb = self._get_embedding(" ".join(expanded))
        if query_emb is None:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return [{
                "text": c[0]["text"],
                "score": c[1] / max(len(expanded), 1),
                "start_line": c[0]["start_line"],
                "end_line": c[0]["end_line"]
            } for c in candidates[:top_k]]

        # 计算向量相似度
        combined = []
        for chunk, kw_score in candidates:
            emb = self._get_embedding(chunk["text"])
            if emb:
                sim = self._cosine_similarity(query_emb, emb)
                score = 0.2 * (kw_score / max(len(expanded), 1)) + 0.8 * sim
                combined.append((chunk, score))

        combined.sort(key=lambda x: x[1], reverse=True)
        return [{
            "text": c[0]["text"],
            "score": float(c[1]),
            "start_line": c[0]["start_line"],
            "end_line": c[0]["end_line"]
        } for c in combined[:top_k]]

    def _expand_query(self, query_keys: List[str]) -> List[str]:
        """LLM查询扩展"""
        cache_key = tuple(sorted(query_keys))
        if cache_key in self._expand_cache:
            return self._expand_cache[cache_key]

        prompt = f"""
为以下关键词生成语义相关的扩展词（每个3-5个）：
{', '.join(query_keys)}

输出JSON格式：{{"expanded": ["词1", "词2"]}}
"""
        try:
            result = llm_client.request(prompt, is_json=True)
            if result and "expanded" in result:
                all_terms = list(set(query_keys + result["expanded"]))
                self._expand_cache[cache_key] = all_terms
                return all_terms
        except:
            pass

        self._expand_cache[cache_key] = query_keys
        return query_keys

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """获取文本向量"""
        if text in self.embedding_cache:
            return self.embedding_cache[text]
        try:
            resp = requests.post(
                "http://localhost:11434/api/embeddings",
                json={"model": self.embedding_model, "prompt": text},
                timeout=10
            )
            if resp.status_code == 200:
                emb = resp.json()["embedding"]
                self.embedding_cache[text] = emb
                return emb
        except:
            return None

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """余弦相似度"""
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def _to_evidence(self, retrieved: List[Dict], filename: str) -> List[Dict]:
        """转换为证据格式"""
        return [{
            "file": filename,
            "page": None,
            "start_line": item["start_line"],
            "end_line": item["end_line"],
            "text": item["text"],
            "score": item["score"]
        } for item in retrieved]


# 全局单例
retriever = Retriever()