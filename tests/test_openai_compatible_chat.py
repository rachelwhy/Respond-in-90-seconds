"""openai_compatible_chat：base_url 规范化。"""

from src.adapters.openai_compatible_chat import normalize_chat_base_url


def test_normalize_qwen_appends_compatible_mode():
    assert "compatible-mode/v1" in normalize_chat_base_url("qwen", "https://dashscope.aliyuncs.com")


def test_normalize_openai_appends_v1():
    assert normalize_chat_base_url("openai", "http://localhost:1234").endswith("/v1")


def test_normalize_zhipu_keeps_v4():
    u = normalize_chat_base_url("zhipu", "https://open.bigmodel.cn/api/paas/v4")
    assert u.endswith("/v4")
