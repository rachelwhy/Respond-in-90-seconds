"""
分块缓存与索引 — 基于嵌入向量的语义缓存

核心功能：
1. 基于嵌入向量的语义相似度查找
2. 避免重复处理相似内容
3. 支持增量更新和持久化
4. 可配置的相似度阈值和缓存策略

使用场景：
- 长文档处理中的重复内容检测
- 跨文档的相似片段识别
- 问答系统中的上下文缓存
- 批量处理中的去重优化

依赖：
- sentence-transformers (可选，用于嵌入生成)
- diskcache (用于持久化缓存)
- numpy (用于向量计算)
"""

import json
import logging
import hashlib
import pickle
from typing import List, Dict, Any, Optional, Tuple, Union
from dataclasses import dataclass, field
from pathlib import Path
import numpy as np

logger = logging.getLogger(__name__)

# 尝试导入可选依赖
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers 未安装，将使用文本哈希作为回退")

try:
    import diskcache
    DISKCACHE_AVAILABLE = True
except ImportError:
    DISKCACHE_AVAILABLE = False
    logger.warning("diskcache 未安装，将使用内存缓存")


@dataclass
class ChunkCacheConfig:
    """分块缓存配置"""
    cache_dir: str = "storage/chunk_cache"  # 缓存目录
    embedding_model: str = "paraphrase-multilingual-MiniLM-L12-v2"  # 嵌入模型
    similarity_threshold: float = 0.85  # 相似度阈值
    max_cache_size_mb: int = 1024  # 最大缓存大小(MB)
    ttl_days: int = 30  # 缓存有效期(天)
    enable_semantic_cache: bool = True  # 启用语义缓存
    enable_text_hash_cache: bool = True  # 启用文本哈希缓存
    min_text_length: int = 20  # 最小文本长度（低于此值不缓存）


class ChunkCache:
    """基于嵌入向量的分块缓存器

    支持两种缓存策略：
    1. 语义缓存：基于嵌入向量的相似度查找
    2. 文本哈希缓存：基于文本内容的精确匹配
    """

    def __init__(self, config: Optional[ChunkCacheConfig] = None):
        if config is None:
            # 尝试从去重配置获取默认值
            try:
                from src.core.deduplication_config import get_similarity_threshold
                threshold = get_similarity_threshold("chunk_cache")
                config = ChunkCacheConfig(similarity_threshold=threshold)
            except ImportError:
                # 如果去重配置模块不可用，使用默认配置
                config = ChunkCacheConfig()

        self.config = config
        self._cache_dir = Path(self.config.cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # 初始化嵌入模型
        self._embedding_model = None
        if self.config.enable_semantic_cache and SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                self._embedding_model = SentenceTransformer(self.config.embedding_model)
                logger.info(f"嵌入模型已加载: {self.config.embedding_model}")
            except Exception as e:
                logger.warning(f"嵌入模型加载失败: {e}，语义缓存将不可用")
                self._embedding_model = None

        # 初始化缓存后端
        self._cache = None
        if DISKCACHE_AVAILABLE:
            try:
                self._cache = diskcache.Cache(
                    str(self._cache_dir),
                    size_limit=self.config.max_cache_size_mb * 1024 * 1024
                )
                logger.info(f"磁盘缓存已初始化: {self._cache_dir}")
            except Exception as e:
                logger.warning(f"磁盘缓存初始化失败: {e}，使用内存字典")
                self._cache = {}
        else:
            self._cache = {}

        # 统计信息
        self._stats = {
            "total_lookups": 0,
            "cache_hits": 0,
            "semantic_hits": 0,
            "hash_hits": 0,
            "cache_misses": 0,
            "embeddings_computed": 0
        }

    def get_or_compute(self, chunk: Dict[str, Any], compute_func: callable) -> Any:
        """获取缓存结果或计算新结果

        Args:
            chunk: 分块数据，至少包含 {"text": str, "type": str}
            compute_func: 计算函数，当缓存未命中时调用

        Returns:
            分块处理结果
        """
        self._stats["total_lookups"] += 1
        chunk_text = chunk.get("text", "")
        chunk_type = chunk.get("type", "text")

        # 检查文本长度
        if len(chunk_text) < self.config.min_text_length:
            logger.debug(f"文本长度不足 {self.config.min_text_length}，跳过缓存")
            return compute_func(chunk)

        # 1. 首先尝试文本哈希精确匹配
        if self.config.enable_text_hash_cache:
            hash_key = self._compute_text_hash(chunk_text, chunk_type)
            cached_result = self._get_from_cache(hash_key)
            if cached_result is not None:
                self._stats["cache_hits"] += 1
                self._stats["hash_hits"] += 1
                logger.debug(f"文本哈希缓存命中: {hash_key[:16]}...")
                return cached_result

        # 2. 尝试语义相似度匹配
        if self.config.enable_semantic_cache and self._embedding_model is not None:
            similar_result = self._find_similar_chunk(chunk_text, chunk_type)
            if similar_result is not None:
                self._stats["cache_hits"] += 1
                self._stats["semantic_hits"] += 1
                logger.debug(f"语义缓存命中: 相似度 {similar_result['similarity']:.3f}")
                return similar_result["result"]

        # 3. 缓存未命中，计算新结果
        self._stats["cache_misses"] += 1
        result = compute_func(chunk)

        # 4. 存储到缓存
        self._store_in_cache(chunk_text, chunk_type, result)

        return result

    def _compute_text_hash(self, text: str, chunk_type: str) -> str:
        """计算文本哈希键"""
        # 组合文本和类型进行哈希
        content = f"{chunk_type}:{text}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _get_from_cache(self, key: str) -> Optional[Any]:
        """从缓存获取值"""
        try:
            if isinstance(self._cache, dict):
                return self._cache.get(key)
            else:
                return self._cache.get(key)
        except Exception as e:
            logger.warning(f"缓存读取失败: {e}")
            return None

    def _store_in_cache(self, text: str, chunk_type: str, result: Any):
        """存储结果到缓存"""
        try:
            # 存储文本哈希缓存
            if self.config.enable_text_hash_cache:
                hash_key = self._compute_text_hash(text, chunk_type)
                if isinstance(self._cache, dict):
                    self._cache[hash_key] = result
                else:
                    self._cache.set(hash_key, result, expire=self.config.ttl_days * 24 * 3600)

            # 存储语义缓存（如果启用）
            if self.config.enable_semantic_cache and self._embedding_model is not None:
                self._store_semantic_cache(text, chunk_type, result)

        except Exception as e:
            logger.warning(f"缓存存储失败: {e}")

    def _store_semantic_cache(self, text: str, chunk_type: str, result: Any):
        """存储语义缓存（嵌入向量 + 文本）"""
        try:
            # 计算嵌入向量
            embedding = self._embedding_model.encode(text, convert_to_numpy=True)
            self._stats["embeddings_computed"] += 1

            # 存储嵌入向量和元数据
            cache_entry = {
                "text": text,
                "type": chunk_type,
                "result": result,
                "embedding": embedding.tolist(),  # 转换为列表以便JSON序列化
                "text_hash": self._compute_text_hash(text, chunk_type),
                "timestamp": np.datetime64('now').astype(str)
            }

            # 生成语义缓存键
            semantic_key = f"semantic:{hashlib.md5(text.encode()).hexdigest()}"

            if isinstance(self._cache, dict):
                self._cache[semantic_key] = cache_entry
            else:
                self._cache.set(semantic_key, cache_entry, expire=self.config.ttl_days * 24 * 3600)

        except Exception as e:
            logger.warning(f"语义缓存存储失败: {e}")

    def _find_similar_chunk(self, text: str, chunk_type: str) -> Optional[Dict[str, Any]]:
        """查找语义相似的分块"""
        if self._embedding_model is None:
            return None

        try:
            # 计算查询文本的嵌入向量
            query_embedding = self._embedding_model.encode(text, convert_to_numpy=True)
            self._stats["embeddings_computed"] += 1

            # 获取所有语义缓存条目
            semantic_entries = self._get_all_semantic_entries()

            if not semantic_entries:
                return None

            # 计算相似度
            best_match = None
            best_similarity = 0.0

            for key, entry in semantic_entries:
                if entry.get("type") != chunk_type:
                    continue  # 类型不匹配，跳过

                cached_embedding = np.array(entry["embedding"])
                similarity = self._cosine_similarity(query_embedding, cached_embedding)

                if similarity > best_similarity and similarity >= self.config.similarity_threshold:
                    best_similarity = similarity
                    best_match = {
                        "key": key,
                        "similarity": similarity,
                        "result": entry["result"],
                        "cached_text": entry["text"]
                    }

            return best_match

        except Exception as e:
            logger.warning(f"语义相似度查找失败: {e}")
            return None

    def _get_all_semantic_entries(self) -> List[Tuple[str, Dict]]:
        """获取所有语义缓存条目"""
        entries = []
        try:
            if isinstance(self._cache, dict):
                for key, value in self._cache.items():
                    if key.startswith("semantic:") and isinstance(value, dict):
                        entries.append((key, value))
            else:
                # diskcache 迭代
                for key in self._cache:
                    if key.startswith("semantic:"):
                        value = self._cache.get(key)
                        if value and isinstance(value, dict):
                            entries.append((key, value))
        except Exception as e:
            logger.warning(f"获取语义缓存条目失败: {e}")

        return entries

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    def clear_cache(self):
        """清空缓存"""
        try:
            if isinstance(self._cache, dict):
                self._cache.clear()
            else:
                self._cache.clear()
            logger.info("缓存已清空")
        except Exception as e:
            logger.warning(f"缓存清空失败: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息"""
        cache_size = 0
        if isinstance(self._cache, dict):
            cache_size = len(self._cache)
        elif DISKCACHE_AVAILABLE:
            cache_size = len(self._cache)

        stats = dict(self._stats)
        stats.update({
            "cache_size": cache_size,
            "embedding_model_available": self._embedding_model is not None,
            "semantic_cache_enabled": self.config.enable_semantic_cache and self._embedding_model is not None,
            "text_hash_cache_enabled": self.config.enable_text_hash_cache,
            "cache_dir": str(self._cache_dir)
        })

        # 计算命中率
        if stats["total_lookups"] > 0:
            stats["hit_rate"] = stats["cache_hits"] / stats["total_lookups"]
            stats["semantic_hit_rate"] = stats["semantic_hits"] / stats["total_lookups"] if stats["total_lookups"] > 0 else 0
            stats["hash_hit_rate"] = stats["hash_hits"] / stats["total_lookups"] if stats["total_lookups"] > 0 else 0
        else:
            stats["hit_rate"] = 0
            stats["semantic_hit_rate"] = 0
            stats["hash_hit_rate"] = 0

        return stats

    def save_stats(self, filepath: Optional[str] = None):
        """保存统计信息到文件"""
        if filepath is None:
            filepath = self._cache_dir / "cache_stats.json"

        stats = self.get_stats()
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(stats, f, ensure_ascii=False, indent=2)
            logger.info(f"缓存统计信息已保存到: {filepath}")
        except Exception as e:
            logger.warning(f"保存统计信息失败: {e}")

    def __del__(self):
        """析构函数，确保缓存正确关闭"""
        try:
            if not isinstance(self._cache, dict) and hasattr(self._cache, 'close'):
                self._cache.close()
        except:
            pass


# 全局缓存实例（可选）
_global_chunk_cache: Optional[ChunkCache] = None

def get_global_chunk_cache(config: Optional[ChunkCacheConfig] = None) -> ChunkCache:
    """获取全局分块缓存实例（单例模式）"""
    global _global_chunk_cache
    if _global_chunk_cache is None:
        _global_chunk_cache = ChunkCache(config)
    return _global_chunk_cache

def clear_global_chunk_cache():
    """清空全局分块缓存"""
    global _global_chunk_cache
    if _global_chunk_cache is not None:
        _global_chunk_cache.clear_cache()
        _global_chunk_cache = None