"""解析问答句向量路径：默认离线、不隐式访问 HuggingFace Hub。"""


def _write_minimal_sentence_transformer_bundle(d):
    """本地快照最低集合：与 ``sentence-transformers`` / 预下载脚本产出对齐。"""
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "model.safetensors").write_bytes(b"x")
    (d / "tokenizer.json").write_text("{}", encoding="utf-8")


def test_resolve_uses_default_local_when_bundle_exists(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config._repository_root", lambda: tmp_path)
    local = tmp_path / "models" / "qna_embedding"
    _write_minimal_sentence_transformer_bundle(local)
    monkeypatch.delenv("A23_QNA_SENTENCE_TRANSFORMER", raising=False)

    from src.config import resolve_qna_sentence_transformer_model

    assert resolve_qna_sentence_transformer_model() == str(local.resolve())


def test_resolve_empty_without_env_and_without_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config._repository_root", lambda: tmp_path)
    monkeypatch.delenv("A23_QNA_SENTENCE_TRANSFORMER", raising=False)

    from src.config import resolve_qna_sentence_transformer_model

    assert resolve_qna_sentence_transformer_model() == ""


def test_resolve_empty_when_bundle_only_config(monkeypatch, tmp_path):
    """仅有 config.json、缺少权重/分词器时不视为可用快照。"""
    monkeypatch.setattr("src.config._repository_root", lambda: tmp_path)
    local = tmp_path / "models" / "qna_embedding"
    local.mkdir(parents=True)
    (local / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("A23_QNA_SENTENCE_TRANSFORMER", raising=False)

    from src.config import resolve_qna_sentence_transformer_model

    assert resolve_qna_sentence_transformer_model() == ""


def test_resolve_explicit_hub_id(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config._repository_root", lambda: tmp_path)
    monkeypatch.setenv("A23_QNA_SENTENCE_TRANSFORMER", "paraphrase-multilingual-MiniLM-L12-v2")

    from src.config import resolve_qna_sentence_transformer_model

    assert resolve_qna_sentence_transformer_model() == "paraphrase-multilingual-MiniLM-L12-v2"


def test_resolve_relative_env_path_to_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr("src.config._repository_root", lambda: tmp_path)
    local = tmp_path / "models" / "qna_embedding"
    _write_minimal_sentence_transformer_bundle(local)
    monkeypatch.setenv("A23_QNA_SENTENCE_TRANSFORMER", "models/qna_embedding")

    from src.config import resolve_qna_sentence_transformer_model

    assert resolve_qna_sentence_transformer_model() == str(local.resolve())
