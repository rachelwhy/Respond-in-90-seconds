"""_optimize_chunks：按类型分组后合并同类型小块（与当前 langextract_adapter 实现一致）。"""
from src.adapters.langextract_adapter import _optimize_chunks


def test_optimize_chunks_groups_by_type_and_merges_text_under_cap():
    chunks = [
        {"type": "text", "text": "城市A GDP 1"},
        {"type": "table", "text": "| 城市 | GDP |"},
        {"type": "text", "text": "城市B GDP 2"},
    ]
    out = _optimize_chunks(chunks, is_cloud=True, quiet=True)
    assert len(out) == 2
    types = [c["type"] for c in out]
    assert "text" in types and "table" in types
    text_chunk = next(c for c in out if c["type"] == "text")
    assert "城市A" in text_chunk["text"] and "城市B" in text_chunk["text"]
