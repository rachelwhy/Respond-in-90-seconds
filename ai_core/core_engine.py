from typing import List, Dict, Any, Optional
from .llm_client import llm_client
from .rag_engine import rag_engine
import re
import json


class UniversalProcessor:
    """通用处理单元：让模型自注意判断文档类型和输出格式"""

    def __init__(self, confidence_threshold: float = 0.7):
        self.confidence_threshold = confidence_threshold

    def process(self, text: str, instruction: str = "", fields: Optional[List[str]] = None,
                template: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        处理文档，提取信息 - 模型自主判断输出格式
        :param text: 文档文本内容
        :param instruction: 用户需求指令（可选）
        :param fields: 预留参数，保持兼容
        :param template: 可选，目标模板列名
        :return: 智能判断格式的结果
        """
        # 1. 获取相关上下文
        chunks = rag.sliding_window_chunk(text)
        context = rag.retrieve(fields or [], chunks, top_k=3)

        # 2. 让模型自注意：分析文档并决定输出格式
        result = self._self_attention_extract(context, instruction)

        # 3. 如果需要模板映射，进行处理
        if template and result.get("data"):
            result["data"] = self._map_to_template(result["data"], template)

        return result

    def _self_attention_extract(self, context: str, instruction: str = "") -> Dict[str, Any]:
        """
        自注意抽取：让模型自己分析文档结构，决定最佳输出格式
        返回统一的格式：{"data": {...}, "confidence": {...}, "needs_human_review": bool}
        """

        # 构建自注意提示词 - 引导模型思考，不过度指定
        prompt = f"""
你是一个智能文档理解助手。请分析以下文档，并基于你的理解提取重要信息。

用户需求（如果有）：{instruction}

文档内容：
{context}

【任务要求】
1. 首先，分析这份文档的性质（是什么类型的文档？包含什么类型的信息？）
2. 然后，根据文档本身的特点，决定最合适的组织和呈现方式
3. 最后，以你最擅长的方式提取和组织信息

【思考步骤】
- 文档类型是什么？（表格数据/统计指标/文本描述/混合型/其他）
- 文档中有哪些重要实体或概念？
- 这些信息之间有什么关系？
- 如何组织才能最好地保留原始信息结构？

【输出要求】
- 以JSON格式输出
- 包含字段 "data" 和 "confidence"
- data应该反映文档的自然结构，可以是：
  * 数组（多行记录）
  * 对象（键值对）
  * 嵌套结构（如果文档本身有层次）
  * 字符串（纯文本）
- confidence应该是每个重要字段的置信度，或整体置信度

请开始你的分析：
"""

        result = client.request(prompt)

        # 如果模型返回的不是字典，包装一下
        if not isinstance(result, dict):
            result = {"data": result, "confidence": {}}

        # 确保基本结构
        if "data" not in result:
            result = {"data": result, "confidence": {}}
        if "confidence" not in result:
            result["confidence"] = {}

        # 计算是否需要人工复核
        needs_review = self._evaluate_confidence(result["confidence"])

        return {
            "data": result["data"],
            "confidence": result["confidence"],
            "needs_human_review": needs_review
        }

    def _evaluate_confidence(self, confidence: Any) -> bool:
        """评估是否需要人工复核"""
        if not confidence:
            return True

        if isinstance(confidence, dict):
            # 如果有任何字段置信度低于阈值
            return any(v < self.confidence_threshold for v in confidence.values() if isinstance(v, (int, float)))
        elif isinstance(confidence, (int, float)):
            return confidence < self.confidence_threshold

        return False

    def _map_to_template(self, data: Any, template: List[str]) -> Any:
        """智能映射到模板，保持原结构尽可能不变"""

        # 如果是数组（多行记录）
        if isinstance(data, list):
            return [self._map_to_template(item, template) for item in data]

        # 如果是对象
        if isinstance(data, dict):
            mapped = {}
            # 保留所有原始字段
            for key, value in data.items():
                mapped[key] = value

            # 补充模板中缺失的字段
            for col in template:
                if col not in mapped:
                    # 尝试模糊匹配
                    found = False
                    for key in data.keys():
                        if col in key or key in col:
                            mapped[col] = data[key]
                            found = True
                            break
                    if not found:
                        mapped[col] = None
            return mapped

        # 其他类型原样返回
        return data

    def ask(self, question: str, documents: List[str], doc_sources: Optional[List[str]] = None) -> Dict[str, Any]:
        """
        问答接口 - 基于文档回答问题
        """
        # 1. 检索相关片段
        retrieved_chunks, chunk_sources, chunk_scores = self._retrieve_chunks(
            question, documents, doc_sources, top_k=3
        )

        if not retrieved_chunks:
            return {
                "data": {"answer": "没有找到相关信息"},
                "confidence": 0.0,
                "needs_human_review": False
            }

        # 2. 构建上下文
        context = self._build_context(retrieved_chunks, chunk_sources)

        # 3. 自注意问答
        prompt = f"""
请基于提供的文档片段，回答用户的问题。

文档片段：
{context}

用户问题：{question}

要求：
- 如果文档中包含答案，请给出准确回答
- 如果文档中没有足够信息，请明确说明
- 评估你的回答的可信度
- 用JSON格式输出：{{"answer": "你的回答", "confidence": 0.95}}

开始：
"""
        result = client.request(prompt)

        if not isinstance(result, dict):
            result = {"answer": str(result) if result else "无法回答", "confidence": 0.5}

        return {
            "data": result,
            "confidence": result.get("confidence", 0.5),
            "needs_human_review": result.get("confidence", 0) < self.confidence_threshold
        }

    def _retrieve_chunks(self, question: str, documents: List[str], doc_sources: Optional[List[str]], top_k: int):
        """检索相关文档片段"""
        all_chunks = []
        all_sources = []

        for idx, doc in enumerate(documents):
            chunks = rag.sliding_window_chunk(doc, size=800, overlap=150)
            source = doc_sources[idx] if doc_sources and idx < len(doc_sources) else f"文档{idx + 1}"

            for chunk in chunks:
                all_chunks.append(chunk)
                all_sources.append(source)

        if not all_chunks:
            return [], [], []

        # 计算相关性
        chunk_scores = []
        for chunk in all_chunks:
            score = self._compute_relevance(question, chunk)
            chunk_scores.append(score)

        # 排序
        indexed_scores = list(enumerate(chunk_scores))
        indexed_scores.sort(key=lambda x: x[1], reverse=True)
        top_indices = [idx for idx, _ in indexed_scores[:top_k]]

        return ([all_chunks[idx] for idx in top_indices],
                [all_sources[idx] for idx in top_indices],
                [chunk_scores[idx] for idx in top_indices])

    def _compute_relevance(self, question: str, chunk: str) -> float:
        """计算相关性"""
        keywords = re.findall(r'\w+', question.lower())
        keywords = [k for k in keywords if len(k) > 1]

        if not keywords:
            return 0.5

        chunk_lower = chunk.lower()
        matches = sum(1 for k in keywords if k in chunk_lower)
        return min(1.0, matches / len(keywords))

    def _build_context(self, chunks: List[str], sources: List[str]) -> str:
        """构建上下文"""
        context_parts = []
        for i, (chunk, source) in enumerate(zip(chunks, sources)):
            context_parts.append(f"[片段{i + 1} 来源:{source}]\n{chunk}")
        return "\n\n".join(context_parts)


# 全局单例
core_engine = UniversalProcessor(confidence_threshold=0.7)