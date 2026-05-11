"""问答检索：混合打分与类型加权。"""


def test_hybrid_uses_bm25_when_vectors_disabled(monkeypatch):
    monkeypatch.setenv("A23_QNA_HYBRID_ALPHA", "0.5")
    from src.api import qna_retrieval as qr

    monkeypatch.setattr(qr, "_scores_embedding_st", lambda *a, **k: None)
    monkeypatch.setattr(qr, "_scores_embedding_ollama", lambda *a, **k: None)
    monkeypatch.setattr(qr, "_scores_bm25_okapi", lambda ch, q: [0.1, 0.9])

    chunks = [
        {"text": "low", "type": "text"},
        {"text": "high", "type": "text"},
    ]
    out = qr.hybrid_retrieve_chunks(chunks, "query", top_k=1, session_dir=None)
    assert len(out) == 1
    assert out[0]["text"] == "high"
    assert "bm25" in out[0]["method"]


def test_apply_chunk_type_boost_breaks_tie():
    """同分时段落低于表格（产品偏好结构化命中）。"""
    from src.api.qna_retrieval import apply_chunk_type_boost

    chunks = [{"type": "text"}, {"type": "table"}]
    boosted = apply_chunk_type_boost([0.5, 0.5], chunks)
    assert boosted[1] > boosted[0]


def test_empty_chunks():
    from src.api.qna_retrieval import hybrid_retrieve_chunks

    assert hybrid_retrieve_chunks([], "q", 3) == []
