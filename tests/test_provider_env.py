"""provider_env：与 src.config 动态绑定。"""

import src.config as cfg
from src.adapters.provider_env import default_chat_provider_dict, is_chat_openai_compatible


def test_default_dict_reads_live_config(monkeypatch):
    monkeypatch.setattr(cfg, "MOONSHOT_API_KEY", "sk-test")
    d = default_chat_provider_dict("moonshot")
    assert d["api_key"] == "sk-test"
    assert is_chat_openai_compatible("moonshot") is True
    assert is_chat_openai_compatible("ollama") is False
