import src.core.chunk_merger as cm


def test_no_similarity_merge_when_explicit_key_fields(monkeypatch):
    merger = cm.ChunkMerger()

    called = {"n": 0}

    def _fake_merge_by_similarity(self, records, threshold):
        called["n"] += 1
        return [records[0]]

    monkeypatch.setattr(cm, "RAPIDFUZZ_AVAILABLE", True, raising=False)
    monkeypatch.setattr(cm.ChunkMerger, "_merge_by_similarity", _fake_merge_by_similarity, raising=True)

    records = [
        {"城市": "南阳", "GDP": "100"},
        {"城市": "南昌", "GDP": "100"},
    ]

    out = merger.merge_records(records, key_fields=["城市"], similarity_threshold=0.5)
    assert called["n"] == 0
    assert len(out) == 2

