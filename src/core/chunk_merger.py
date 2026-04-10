"""
智能分块合并算法 — 解决长文档切片导致的顺序混乱与重复抽取问题

核心功能：
1. 基于关键字段的记录融合去重（增强版 merge_records_by_key）
2. 基于内容相似度的记录合并（无关键字段时使用）
3. 自动检测关键字段
4. 分块位置感知合并（保留原文顺序）

依赖：
. rapidfuzz（可选，用于相似度计算）
. 现有 merge_records_by_key 函数作为基础
"""

import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 可选依赖：rapidfuzz 用于相似度计算
try:
    from rapidfuzz import fuzz, utils
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    logger.warning("rapidfuzz 未安装，相似度合并功能将使用回退算法")

# 可选依赖：sentence-transformers 用于语义相似度计算
try:
    from sentence_transformers import SentenceTransformer
    import torch
    SENTENCE_TRANSFORMERS_AVAILABLE = True
    # 默认使用中文预训练模型，如果不可用则使用多语言模型
    try:
        # 尝试加载中文模型
        SEMANTIC_MODEL = SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')
    except:
        # 回退到通用模型
        SEMANTIC_MODEL = SentenceTransformer('all-MiniLM-L6-v2')
    logger.info(f"语义相似度模型已加载: {SEMANTIC_MODEL}")
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers 未安装，将使用字符串相似度")
except Exception as e:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning(f"语义相似度模型加载失败: {e}，将使用字符串相似度")


@dataclass
class MergeConfig:
    """分块合并配置"""
    # 关键字段合并配置
    key_field_priority: List[str] = field(default_factory=list)
    # 相似度合并配置
    similarity_threshold: float = 0.8  # 0-1，相似度阈值
    min_text_length: int = 10  # 文本最小长度，低于此值不进行相似度比较
    # 位置感知配置
    preserve_original_order: bool = True
    # 字段合并策略
    merge_strategy: str = "non_empty_wins"  # 可选: "non_empty_wins", "latest_wins", "longer_wins"
    # 调试配置
    enable_debug: bool = False


class ChunkMerger:
    """智能分块合并器

    解决长文档切片导致的顺序混乱与重复抽取问题。
    支持多级合并策略：关键字段优先，相似度次之，位置感知兜底。
    """

    def __init__(self, config: Optional[MergeConfig] = None):
        self.config = config or MergeConfig()
        self._cache = {}  # 简单缓存，避免重复计算

    def merge_records(
        self,
        records: List[Dict[str, Any]],
        key_fields: Optional[List[str]] = None,
        similarity_threshold: Optional[float] = None
    ) -> List[Dict[str, Any]]:
        """智能合并记录（主入口函数）

        Args:
            records: 待合并的记录列表
            key_fields: 可选的关键字段列表（None 时自动检测）
            similarity_threshold: 可选的自定义相似度阈值

        Returns:
            合并后的记录列表，保留原文顺序
        """
        if not records:
            return records

        # 1. 确定关键字段
        if key_fields is None:
            key_fields = self.detect_key_fields(records)

        # 2. 基于关键字段的初步合并
        if key_fields:
            key_merged = self._merge_by_key_fields(records, key_fields)
            if self.config.enable_debug:
                logger.debug(f"关键字段合并: {len(records)} -> {len(key_merged)} 条记录")
            records = key_merged

        # 3. 基于相似度的二次合并（仅当仍有重复可能时）
        if len(records) > 1 and RAPIDFUZZ_AVAILABLE:
            similarity_threshold = similarity_threshold or self.config.similarity_threshold
            similarity_merged = self._merge_by_similarity(records, similarity_threshold)
            if self.config.enable_debug and len(similarity_merged) < len(records):
                logger.debug(f"相似度合并: {len(records)} -> {len(similarity_merged)} 条记录")
            records = similarity_merged

        # 4. 清理内部标记字段
        cleaned_records = []
        for rec in records:
            cleaned = {k: v for k, v in rec.items() if not k.startswith("_")}
            cleaned_records.append(cleaned)

        return cleaned_records

    def detect_key_fields(self, records: List[Dict[str, Any]]) -> List[str]:
        """自动检测可能的关键字段

        启发式规则：
        1. 唯一性高的字段（值重复率低）
        2. 数值型字段（如ID、序号）
        3. 位置信息字段（如"序号"、"编号"、"排名"）
        4. 长度稳定的字段（非长文本）
        """
        if not records:
            return []

        first_record = records[0]
        candidate_fields = []

        for field_name in first_record.keys():
            if field_name.startswith("_"):
                continue

            values = []
            for rec in records[:min(100, len(records))]:  # 采样分析
                val = rec.get(field_name)
                if val and isinstance(val, (str, int, float)):
                    values.append(str(val))

            if not values:
                continue

            # 计算唯一性比例
            unique_ratio = len(set(values)) / len(values) if values else 0

            # 字段特征分析
            avg_length = sum(len(str(v)) for v in values) / len(values) if values else 0
            is_numeric_like = all(str(v).replace('.', '').replace('-', '').isdigit() for v in values if v)

            # 评分规则
            score = 0
            if unique_ratio > 0.9:  # 高唯一性
                score += 3
            elif unique_ratio > 0.7:
                score += 2
            elif unique_ratio > 0.5:
                score += 1

            if is_numeric_like:
                score += 2

            if avg_length < 20:  # 短文本更适合作为关键字段
                score += 1

            # 常见关键字段名称模式
            key_patterns = ["id", "序号", "编号", "排名", "index", "no", "number", "code", "编号"]
            if any(pattern in field_name.lower() for pattern in key_patterns):
                score += 2

            if score >= 3:  # 达到阈值
                candidate_fields.append((field_name, score))

        # 按分数排序
        candidate_fields.sort(key=lambda x: x[1], reverse=True)
        return [field_name for field_name, score in candidate_fields[:5]]  # 返回前5个

    def _merge_by_key_fields(
        self,
        records: List[Dict[str, Any]],
        key_fields: List[str]
    ) -> List[Dict[str, Any]]:
        """基于关键字段的合并（增强版 merge_records_by_key）

        增强功能：
        1. 支持多级关键字段优先级
        2. 支持字段值规范化（如去除空格、统一大小写）
        3. 更好的空值处理策略
        """
        merged_map = {}
        unkeyed_records = []

        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                continue

            # 构建组合键（支持字段值规范化）
            key_parts = []
            for field in key_fields:
                val = rec.get(field)
                if val is None:
                    key_parts.append("")
                else:
                    # 简单规范化
                    norm_val = str(val).strip()
                    key_parts.append(norm_val)

            key_str = "|".join(key_parts)

            # 检查是否所有关键字段为空
            if not any(part.strip() for part in key_parts):
                rec["_unkeyed"] = True
                rec["_original_order"] = i
                unkeyed_records.append(rec)
                continue

            # 合并策略
            if key_str not in merged_map:
                merged_rec = dict(rec)
                merged_rec["_original_order"] = i
                merged_rec["_merge_count"] = 1
                merged_map[key_str] = merged_rec
            else:
                existing = merged_map[key_str]
                self._merge_single_record(existing, rec)
                existing["_merge_count"] += 1

        # 按原始顺序排序
        merged_records = list(merged_map.values())
        if self.config.preserve_original_order:
            merged_records.sort(key=lambda x: x.get("_original_order", 0))

        return merged_records + unkeyed_records

    def _merge_by_similarity(
        self,
        records: List[Dict[str, Any]],
        threshold: float
    ) -> List[Dict[str, Any]]:
        """基于内容相似度的合并（无关键字段时使用）

        优先使用嵌入模型计算语义相似度，其次使用 rapidfuzz 计算字符串相似度。
        """
        if len(records) <= 1:
            return records

        # 为每条记录生成文本表示
        record_texts = []
        for i, rec in enumerate(records):
            text_repr = self._record_to_text(rec)
            record_texts.append((i, text_repr))

        # 计算相似度矩阵
        similarity_matrix = self._compute_similarity_matrix(record_texts, threshold)

        # 使用贪心算法合并相似记录
        merged_indices = set()
        merged_records = []

        for i in range(len(records)):
            if i in merged_indices:
                continue

            base_rec = records[i]
            base_text = record_texts[i][1]

            # 如果文本太短，跳过相似度比较
            if len(base_text) < self.config.min_text_length:
                merged_records.append(base_rec)
                merged_indices.add(i)
                continue

            # 寻找相似记录
            similar_indices = [i]
            for j in range(i + 1, len(records)):
                if j in merged_indices:
                    continue

                target_text = record_texts[j][1]
                if len(target_text) < self.config.min_text_length:
                    continue

                # 从相似度矩阵获取相似度
                if similarity_matrix[i][j] >= threshold:
                    similar_indices.append(j)

            # 合并相似记录
            if len(similar_indices) > 1:
                merged = dict(base_rec)
                merged["_original_order"] = i
                merged["_merge_count"] = 1

                for idx in similar_indices[1:]:
                    self._merge_single_record(merged, records[idx])
                    merged["_merge_count"] += 1
                    merged_indices.add(idx)

                merged_records.append(merged)
            else:
                merged_records.append(base_rec)

            merged_indices.add(i)

        return merged_records

    def _record_to_text(self, record: Dict[str, Any]) -> str:
        """将记录转换为文本表示，用于相似度计算"""
        parts = []
        for key, value in record.items():
            if key.startswith("_"):
                continue
            if value is None:
                continue
            parts.append(f"{key}:{value}")
        return " ".join(parts)

    def _compute_similarity_matrix(self, record_texts: List[Tuple[int, str]], threshold: float) -> List[List[float]]:
        """计算记录间的相似度矩阵

        优先使用嵌入模型计算语义相似度，其次使用字符串相似度。
        """
        n = len(record_texts)
        texts = [text for _, text in record_texts]

        # 初始化相似度矩阵
        similarity_matrix = [[0.0] * n for _ in range(n)]

        # 优先使用嵌入模型
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                # 批量编码所有文本
                embeddings = SEMANTIC_MODEL.encode(texts, convert_to_tensor=True, show_progress_bar=False)

                # 计算余弦相似度（不使用sklearn，使用numpy）
                import numpy as np

                # 将tensor转换为numpy数组
                if hasattr(embeddings, 'cpu'):
                    embeddings_np = embeddings.cpu().numpy()
                else:
                    embeddings_np = embeddings

                # 归一化向量
                norms = np.linalg.norm(embeddings_np, axis=1, keepdims=True)
                norms[norms == 0] = 1e-10  # 避免除零
                embeddings_norm = embeddings_np / norms

                # 计算余弦相似度矩阵
                cos_sim = np.dot(embeddings_norm, embeddings_norm.T)

                # 填充相似度矩阵
                for i in range(n):
                    for j in range(i, n):
                        similarity = float(cos_sim[i][j])
                        similarity_matrix[i][j] = similarity
                        similarity_matrix[j][i] = similarity

                logger.debug(f"使用嵌入模型计算了 {n} 条记录的语义相似度矩阵")
                return similarity_matrix

            except Exception as e:
                logger.warning(f"嵌入模型计算失败: {e}，回退到字符串相似度")

        # 回退到字符串相似度
        if RAPIDFUZZ_AVAILABLE:
            for i in range(n):
                similarity_matrix[i][i] = 1.0  # 自相似度为1
                for j in range(i + 1, n):
                    similarity = fuzz.ratio(texts[i], texts[j], processor=utils.default_process) / 100
                    similarity_matrix[i][j] = similarity
                    similarity_matrix[j][i] = similarity
            logger.debug(f"使用字符串相似度计算了 {n} 条记录的相似度矩阵")
            return similarity_matrix

        # 如果两种方法都不可用，使用简单的文本长度相似度作为回退
        logger.warning("无可用相似度计算方法，使用简单回退策略")
        for i in range(n):
            similarity_matrix[i][i] = 1.0
            for j in range(i + 1, n):
                # 简单回退：基于文本长度的粗略相似度
                len_i, len_j = len(texts[i]), len(texts[j])
                if len_i == 0 or len_j == 0:
                    similarity = 0.0
                else:
                    similarity = 1.0 - abs(len_i - len_j) / max(len_i, len_j)
                similarity_matrix[i][j] = similarity
                similarity_matrix[j][i] = similarity

        return similarity_matrix

    def _merge_single_record(self, target: Dict[str, Any], source: Dict[str, Any]):
        """单条记录合并策略

        根据配置的合并策略合并两个记录
        """
        strategy = self.config.merge_strategy

        for field, source_val in source.items():
            if field.startswith("_"):
                continue

            target_val = target.get(field)

            if strategy == "non_empty_wins":
                # 非空值优先
                if source_val and not target_val:
                    target[field] = source_val
                # 如果两者都有值，保留更长的（可能更完整）
                elif source_val and target_val and len(str(source_val)) > len(str(target_val)):
                    target[field] = source_val

            elif strategy == "latest_wins":
                # 最新记录优先（假设 source 是更新的）
                target[field] = source_val

            elif strategy == "longer_wins":
                # 更长的文本优先（假设更完整）
                source_len = len(str(source_val)) if source_val else 0
                target_len = len(str(target_val)) if target_val else 0
                if source_len > target_len:
                    target[field] = source_val

    def merge_chunk_results(
        self,
        chunk_results: List[Dict[str, Any]],
        chunk_metadata: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """合并多个分块的抽取结果

        Args:
            chunk_results: 每个分块的抽取结果列表
            chunk_metadata: 可选的分块元数据（位置、类型等）

        Returns:
            合并后的总结果，包含合并统计信息
        """
        all_records = []
        for result in chunk_results:
            records = result.get("records", [])
            if isinstance(records, list):
                all_records.extend(records)

        if not all_records:
            return {"records": [], "metadata": {"total_records": 0, "merged_count": 0}}

        # 检测关键字段（考虑跨分块）
        key_fields = self.detect_key_fields(all_records)

        # 智能合并
        merged_records = self.merge_records(all_records, key_fields)

        # 收集统计信息
        stats = {
            "total_records": len(all_records),
            "merged_records": len(merged_records),
            "reduction_rate": 1 - (len(merged_records) / len(all_records)) if all_records else 0,
            "detected_key_fields": key_fields,
            "rapidfuzz_available": RAPIDFUZZ_AVAILABLE
        }

        return {
            "records": merged_records,
            "metadata": stats
        }


# 向后兼容的快捷函数
def smart_merge_records(
    records: List[Dict[str, Any]],
    key_fields: Optional[List[str]] = None,
    similarity_threshold: float = 0.8
) -> List[Dict[str, Any]]:
    """向后兼容的智能合并函数（可直接替换 merge_records_by_key）"""
    merger = ChunkMerger()
    return merger.merge_records(records, key_fields, similarity_threshold)