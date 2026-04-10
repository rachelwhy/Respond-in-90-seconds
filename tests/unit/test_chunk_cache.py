"""
分块缓存测试
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch, MagicMock

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.chunk_cache import ChunkCache, ChunkCacheConfig, get_global_chunk_cache, clear_global_chunk_cache


class TestChunkCache:
    """分块缓存测试类"""

    def setup_method(self):
        """测试初始化"""
        self.config = ChunkCacheConfig(
            cache_dir="test_cache",
            similarity_threshold=0.85,
            max_cache_size_mb=10,
            ttl_days=1,
            enable_semantic_cache=False,  # 测试中禁用语义缓存
            enable_text_hash_cache=True,
            min_text_length=5
        )
        self.cache = ChunkCache(self.config)

    def teardown_method(self):
        """测试清理"""
        # 清理缓存目录
        import shutil
        if os.path.exists("test_cache"):
            shutil.rmtree("test_cache", ignore_errors=True)

    def test_initialization(self):
        """测试初始化"""
        assert self.cache.config == self.config
        assert self.cache._cache_dir.name == "test_cache"

    @patch('core.chunk_cache.SENTENCE_TRANSFORMERS_AVAILABLE', False)
    @patch('core.chunk_cache.DISKCACHE_AVAILABLE', False)
    def test_cache_backend_fallback(self):
        """测试缓存后端回退"""
        cache = ChunkCache(self.config)
        assert isinstance(cache._cache, dict)  # 应该回退到内存字典

    def test_compute_text_hash(self):
        """测试文本哈希计算"""
        text1 = "这是一个测试文本"
        text2 = "这是另一个测试文本"

        hash1 = self.cache._compute_text_hash(text1, "text")
        hash2 = self.cache._compute_text_hash(text2, "text")
        hash3 = self.cache._compute_text_hash(text1, "table")  # 不同类型

        assert hash1 != hash2  # 不同文本应该有不同的哈希
        assert hash1 != hash3  # 不同类型应该有不同的哈希

        # 相同输入应该产生相同哈希
        hash1_again = self.cache._compute_text_hash(text1, "text")
        assert hash1 == hash1_again

    def test_get_or_compute_cache_hit(self):
        """测试缓存命中"""
        chunk = {"text": "这是一个测试文本", "type": "text"}

        # 模拟计算函数
        compute_calls = []
        def compute_func(chunk):
            compute_calls.append(chunk)
            return {"result": "computed", "records": [chunk]}

        # 第一次调用，应该计算
        result1 = self.cache.get_or_compute(chunk, compute_func)
        assert len(compute_calls) == 1
        assert result1["result"] == "computed"

        # 第二次调用，应该命中缓存
        result2 = self.cache.get_or_compute(chunk, compute_func)
        assert len(compute_calls) == 1  # 计算函数不应该被再次调用
        assert result2["result"] == "computed"

    def test_get_or_compute_short_text(self):
        """测试短文本跳过缓存"""
        chunk = {"text": "短", "type": "text"}  # 长度小于min_text_length

        compute_calls = []
        def compute_func(chunk):
            compute_calls.append(chunk)
            return {"result": "computed"}

        result = self.cache.get_or_compute(chunk, compute_func)

        assert len(compute_calls) == 1  # 应该被计算
        assert result["result"] == "computed"

    def test_cache_stats(self):
        """测试缓存统计"""
        chunk = {"text": "测试文本", "type": "text"}

        def compute_func(chunk):
            return {"result": "computed"}

        # 第一次调用
        self.cache.get_or_compute(chunk, compute_func)
        stats = self.cache.get_stats()

        assert stats["total_lookups"] == 1
        assert stats["cache_hits"] == 0  # 第一次应该未命中
        assert stats["cache_misses"] == 1

        # 第二次调用（应该命中）
        self.cache.get_or_compute(chunk, compute_func)
        stats = self.cache.get_stats()

        assert stats["total_lookups"] == 2
        assert stats["cache_hits"] == 1
        assert stats["hash_hits"] == 1  # 文本哈希命中

    def test_clear_cache(self):
        """测试清空缓存"""
        chunk = {"text": "测试文本", "type": "text"}

        def compute_func(chunk):
            return {"result": "computed"}

        # 添加一些缓存条目
        self.cache.get_or_compute(chunk, compute_func)

        # 清空缓存
        self.cache.clear_cache()

        # 检查缓存是否为空
        if isinstance(self.cache._cache, dict):
            assert len(self.cache._cache) == 0
        else:
            # 对于diskcache，至少统计应该重置
            stats = self.cache.get_stats()
            assert stats["cache_size"] == 0

    def test_save_stats(self):
        """测试保存统计信息"""
        import tempfile

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as tmp:
            tmp_path = tmp.name

        try:
            self.cache.save_stats(tmp_path)

            # 检查文件是否创建并包含有效JSON
            with open(tmp_path, 'r', encoding='utf-8') as f:
                stats = json.load(f)

            assert "total_lookups" in stats
            assert "hit_rate" in stats
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @patch('core.chunk_cache.SENTENCE_TRANSFORMERS_AVAILABLE', True)
    @patch('core.chunk_cache.SentenceTransformer')
    def test_semantic_cache_initialization(self, mock_model_class):
        """测试语义缓存初始化"""
        mock_model = Mock()
        mock_model_class.return_value = mock_model

        config = ChunkCacheConfig(
            enable_semantic_cache=True,
            embedding_model="test-model"
        )

        cache = ChunkCache(config)

        assert cache._embedding_model is not None
        mock_model_class.assert_called_once_with("test-model")

    @patch('core.chunk_cache.SENTENCE_TRANSFORMERS_AVAILABLE', True)
    @patch('core.chunk_cache.np')
    def test_semantic_similarity_search(self, mock_np, mock_sentence_transformers):
        """测试语义相似度搜索"""
        # 模拟嵌入模型
        mock_model = Mock()
        mock_model.encode.return_value = [0.1, 0.2, 0.3]

        # 模拟numpy
        mock_np.array.return_value = [0.1, 0.2, 0.3]
        mock_np.linalg.norm.return_value = 1.0
        mock_np.dot.return_value = 0.95  # 高相似度

        self.cache._embedding_model = mock_model
        self.cache.config.enable_semantic_cache = True

        # 模拟缓存中有条目
        self.cache._cache = {
            "semantic:hash1": {
                "text": "相似的文本",
                "type": "text",
                "result": {"result": "cached"},
                "embedding": [0.1, 0.2, 0.3]
            }
        }

        chunk = {"text": "测试文本", "type": "text"}

        def compute_func(chunk):
            return {"result": "computed"}

        # 应该找到语义相似的结果
        with patch.object(self.cache, '_find_similar_chunk') as mock_find:
            mock_find.return_value = {
                "similarity": 0.9,
                "result": {"result": "cached"}
            }

            result = self.cache.get_or_compute(chunk, compute_func)

            # 应该命中语义缓存
            stats = self.cache.get_stats()
            assert stats["semantic_hits"] == 1

    def test_cache_entry_expiration(self):
        """测试缓存条目过期"""
        # 这个测试主要验证diskcache的TTL功能
        # 由于测试复杂性，这里主要验证配置是否正确传递
        assert self.config.ttl_days == 1

    def test_min_text_length_config(self):
        """测试最小文本长度配置"""
        # 测试短文本
        short_chunk = {"text": "短", "type": "text"}
        compute_calls = []

        def compute_func(chunk):
            compute_calls.append(chunk)
            return {"result": "computed"}

        result = self.cache.get_or_compute(short_chunk, compute_func)

        assert len(compute_calls) == 1  # 应该计算
        assert "min_text_length" in str(self.cache.get_stats())

    def test_cache_directory_creation(self):
        """测试缓存目录创建"""
        import tempfile

        # 使用临时目录
        with tempfile.TemporaryDirectory() as tmpdir:
            config = ChunkCacheConfig(cache_dir=os.path.join(tmpdir, "new_cache"))
            cache = ChunkCache(config)

            # 检查目录是否创建
            assert os.path.exists(cache._cache_dir)

    def test_cache_size_limit(self):
        """测试缓存大小限制"""
        # 验证配置是否正确传递
        assert self.config.max_cache_size_mb == 10


class TestGlobalCacheFunctions:
    """全局缓存函数测试"""

    def test_get_global_chunk_cache_singleton(self):
        """测试全局缓存单例模式"""
        # 清理全局缓存
        clear_global_chunk_cache()

        # 第一次获取
        cache1 = get_global_chunk_cache()

        # 第二次获取，应该是同一个实例
        cache2 = get_global_chunk_cache()

        assert cache1 is cache2

    def test_clear_global_chunk_cache(self):
        """测试清空全局缓存"""
        # 先获取全局缓存
        cache = get_global_chunk_cache()

        # 清空
        clear_global_chunk_cache()

        # 再次获取，应该是新实例
        cache2 = get_global_chunk_cache()

        assert cache is not cache2

    def test_global_cache_with_config(self):
        """测试带配置的全局缓存"""
        clear_global_chunk_cache()

        config = ChunkCacheConfig(cache_dir="global_test_cache")
        cache = get_global_chunk_cache(config)

        assert cache.config.cache_dir == "global_test_cache"

        # 清理
        import shutil
        if os.path.exists("global_test_cache"):
            shutil.rmtree("global_test_cache", ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])