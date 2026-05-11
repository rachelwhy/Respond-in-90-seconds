"""问答生成所用 model_type：默认 DeepSeek，与抽取 MODEL_TYPE 独立。"""


def test_resolve_qna_chat_defaults_to_config(monkeypatch):
    monkeypatch.setattr("src.config.QNA_MODEL_TYPE", "deepseek")
    from src.config import resolve_qna_chat_model_type

    assert resolve_qna_chat_model_type(None) == "deepseek"


def test_resolve_qna_chat_request_overrides(monkeypatch):
    monkeypatch.setattr("src.config.QNA_MODEL_TYPE", "deepseek")
    from src.config import resolve_qna_chat_model_type

    assert resolve_qna_chat_model_type("ollama") == "ollama"


def test_resolve_qna_chat_env_via_constant(monkeypatch):
    monkeypatch.setattr("src.config.QNA_MODEL_TYPE", "ollama")
    from src.config import resolve_qna_chat_model_type

    assert resolve_qna_chat_model_type(None) == "ollama"
