"""collect_semantic_chunks_from_bundle 与 bundle 结构约定。"""

from src.core.reader import collect_semantic_chunks_from_bundle


def test_flatten_chunks_in_order():
    bundle = {
        "documents": [
            {"chunks": [{"type": "text", "text": "a"}]},
            {"chunks": [{"type": "table", "text": "t"}, {"type": "text", "text": "b"}]},
        ]
    }
    out = collect_semantic_chunks_from_bundle(bundle)
    assert len(out) == 3
    assert out[0]["text"] == "a"
    assert out[1]["type"] == "table"


def test_empty_documents():
    assert collect_semantic_chunks_from_bundle({}) == []
    assert collect_semantic_chunks_from_bundle({"documents": []}) == []
