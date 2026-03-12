import numpy as np
from typing import List, Dict, Any, Optional, Union
import requests
from .llm_client import llm_client
import re
import jieba

# 全局加载jieba，避免重复加载词典
jieba.initialize()


class RAGEngine:
    """RAG 引擎：文档切片、向量检索、证据召回"""

    def __init__(self, use_embedding: bool = True, embedding_model: str = "nomic-embed-text"):
        self.use_embedding = use_embedding
        self.embedding_model = embedding_model
        self.embedding_cache = {}
        self._expand_cache = {}

    def _llm_expand_query(self, query_keys: List[str]) -> List[str]:
        """用 LLM 动态生成扩展查询词"""
        if not query_keys:
            return []
        cache_key = tuple(sorted(query_keys))
        if cache_key in self._expand_cache:
            return self._expand_cache[cache_key]

        prompt = f"""
你是一个专业的查询扩展助手。请为以下查询关键词生成语义相关的扩展词。

原始查询词：{', '.join(query_keys)}

任务要求：
1. 理解每个词的语义和可能的文档表达方式
2. 为每个原始词生成 3-5 个同义词、近义词或相关术语
3. 考虑专业术语、中文语境下的常见替换、文档中可能出现的不同表述
4. 保持语义一致性，不要偏离原意

输出格式：
{{
  "expanded": ["词1", "词2", "词3", ...]
}}

开始生成：
"""
        try:
            result = llm_client.request(prompt, is_json=True)
            if result and "expanded" in result:
                all_terms = list(set(query_keys + result["expanded"]))
                print(f"🔍 查询扩展: {query_keys} -> {all_terms}")
                self._expand_cache[cache_key] = all_terms
                return all_terms
        except Exception as e:
            print(f"LLM查询扩展失败: {e}")

        self._expand_cache[cache_key] = query_keys
        return query_keys

    def sliding_window_chunk(self, text: str, size: int = 300, overlap: int = 60) -> List[Dict[str, Any]]:
        """返回带位置信息的切片列表（片段大小300，重叠60）"""
        chunks = []
        lines = text.splitlines(keepends=True)
        line_offsets = []
        current_offset = 0
        for line in lines:
            line_offsets.append(current_offset)
            current_offset += len(line)

        for i in range(0, len(text), size - overlap):
            end = min(i + size, len(text))
            chunk_text = text[i:end]

            start_line = 1
            for idx, offset in enumerate(line_offsets):
                if offset >= i:
                    start_line = idx + 1
                    break
            end_line = 1
            for idx, offset in enumerate(line_offsets):
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

    def _get_embedding(self, text: str) -> Optional[List[float]]:
        """调用 Ollama 嵌入接口获取向量"""
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
        except Exception as e:
            print(f"嵌入获取失败: {e}")
        return None

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        a = np.array(a)
        b = np.array(b)
        return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))

    def retrieve_with_scores(self,
                             query_keys: List[str],
                             chunks: List[Dict[str, Any]],
                             top_k: int = 3,
                             keyword_weight: float = 0.2,
                             vector_weight: float = 0.8) -> List[Dict[str, Any]]:
        """
        混合检索 + LLM 查询扩展
        返回带分数和位置的片段列表
        """
        if not query_keys:
            return [{"text": c["text"], "score": 1.0, "start_line": c["start_line"], "end_line": c["end_line"]}
                    for c in chunks[:top_k]]

        # 1. LLM 动态扩展查询词
        expanded_keys = self._llm_expand_query(query_keys)

        # 2. 计算关键词得分
        keyword_scores = []
        for chunk in chunks:
            text = chunk["text"].lower()
            score = sum(1 for key in expanded_keys if key.lower() in text)
            keyword_scores.append(score)

        candidate_indices = np.argsort(keyword_scores)[-2 * top_k:][::-1]
        candidates = [(chunks[i], keyword_scores[i]) for i in candidate_indices]

        if not self.use_embedding:
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = []
            for i, (chunk, score) in enumerate(candidates[:top_k]):
                result.append({
                    "text": chunk["text"],
                    "score": score / max(len(expanded_keys), 1),
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "source_index": i
                })
            return result

        # 3. 向量精排
        query_text = " ".join(expanded_keys)
        query_emb = self._get_embedding(query_text)
        if query_emb is None:
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = []
            for i, (chunk, score) in enumerate(candidates[:top_k]):
                result.append({
                    "text": chunk["text"],
                    "score": score / max(len(expanded_keys), 1),
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "source_index": i
                })
            return result

        chunk_embs = []
        valid_indices = []
        for idx, (chunk, _) in enumerate(candidates):
            emb = self._get_embedding(chunk["text"])
            if emb is not None:
                chunk_embs.append(emb)
                valid_indices.append(idx)

        if not chunk_embs:
            candidates.sort(key=lambda x: x[1], reverse=True)
            result = []
            for i, (chunk, score) in enumerate(candidates[:top_k]):
                result.append({
                    "text": chunk["text"],
                    "score": score / max(len(expanded_keys), 1),
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "source_index": i
                })
            return result

        similarities = [self._cosine_similarity(query_emb, emb) for emb in chunk_embs]

        combined = []
        for idx, sim in zip(valid_indices, similarities):
            chunk, kw_score = candidates[idx]
            kw_norm = kw_score / max(len(expanded_keys), 1)
            combined_score = keyword_weight * kw_norm + vector_weight * sim
            combined.append((chunk, combined_score))

        combined.sort(key=lambda x: x[1], reverse=True)

        result = []
        for i, (chunk, score) in enumerate(combined[:top_k]):
            result.append({
                "text": chunk["text"],
                "score": float(score),
                "start_line": chunk["start_line"],
                "end_line": chunk["end_line"],
                "source_index": i
            })
        return result

    # ========== 对外接口（供抽取模块调用） ==========
    def retrieve_evidence(self,
                          document_text: str,
                          instruction: Union[str, List[str]],
                          filename: str,
                          global_top_k: int = 3,
                          field_top_k: Optional[Dict[str, int]] = None) -> Union[List[Dict], Dict[str, List[Dict]]]:
        """
        检索与指令最相关的证据片段。
        如果传 instruction 字符串，按全局检索返回一个列表；
        如果传字段名列表，则为每个字段分别检索，返回字典 {字段名: [证据片段]}。

        参数：
            document_text: 文档全文
            instruction: 用户指令（字符串）或字段名列表
            filename: 文件名
            global_top_k: 全局默认检索数量（当 instruction 为字符串时生效，或作为字段的默认值）
            field_top_k: 为特定字段指定的检索数量，格式如 {"合同金额": 5, "签订日期": 3}
        返回：
            如果 instruction 是字符串：List[Dict] 证据列表
            如果 instruction 是列表：Dict[str, List[Dict]] 每个字段对应的证据列表
        """
        # 1. 对全文进行切片
        chunks = self.sliding_window_chunk(document_text, size=300, overlap=60)
        chunk_dicts = [{"text": c["text"], "start_line": c["start_line"], "end_line": c["end_line"]} for c in chunks]

        # 2. 处理两种调用方式
        if isinstance(instruction, str):
            # 全局检索（兼容旧调用）
            words = jieba.lcut(instruction)
            query_keys = [w for w in words if len(w) > 1]
            if not query_keys:
                query_keys = [instruction]
            retrieved = self.retrieve_with_scores(query_keys, chunk_dicts, top_k=global_top_k)
            return self._to_evidence_list(retrieved, filename)

        elif isinstance(instruction, list):
            # 按字段分别检索
            result = {}
            field_top_k = field_top_k or {}
            for field_name in instruction:
                # 对每个字段名进行分词（字段名本身就是关键词）
                words = jieba.lcut(field_name)
                query_keys = [w for w in words if len(w) > 1]
                if not query_keys:
                    query_keys = [field_name]
                top_k = field_top_k.get(field_name, global_top_k)
                retrieved = self.retrieve_with_scores(query_keys, chunk_dicts, top_k=top_k)
                result[field_name] = self._to_evidence_list(retrieved, filename)
            return result
        else:
            raise ValueError("instruction 必须是字符串或列表")

    def _to_evidence_list(self, retrieved: List[Dict], filename: str) -> List[Dict]:
        """将检索结果转换为标准证据格式"""
        evidence = []
        for item in retrieved:
            evidence.append({
                "file": filename,
                "page": None,
                "start_line": item["start_line"],
                "end_line": item["end_line"],
                "text": item["text"],
                "score": item["score"]
            })
        return evidence


rag_engine = RAGEngine(use_embedding=True)