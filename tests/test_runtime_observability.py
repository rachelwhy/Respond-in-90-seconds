from src.core.runtime_observability import (
    merge_runtime_updates,
    finalize_runtime_metrics,
    compact_runtime_for_api,
)


def test_merge_runtime_updates():
    runtime = {"a": 1}
    merge_runtime_updates(runtime, {"b": 2})
    assert runtime["a"] == 1
    assert runtime["b"] == 2


def test_finalize_runtime_metrics(monkeypatch):
    monkeypatch.setattr("src.core.runtime_observability.time.perf_counter", lambda: 13.2)
    runtime = {"model_inference_seconds": 1.1, "retry_inference_seconds": 0.4}
    finalize_runtime_metrics(runtime, total_start=10.0, target_limit_seconds=5)
    assert runtime["model_inference_total_seconds"] == 1.5
    assert runtime["total_seconds"] == 3.2
    assert runtime["within_limit_seconds"] is True
    assert runtime["limit_seconds"] == 5


def test_compact_runtime_for_api():
    compacted = compact_runtime_for_api(
        {
            "read_documents_seconds": 0.2,
            "internal_structured_extract_seconds": 0.1,
            "unknown_key": 99,
            "slicing_metadata": {"slice_count": 2},
        }
    )
    assert "unknown_key" not in compacted
    assert compacted["read_documents_seconds"] == 0.2
    assert compacted["slicing_metadata"]["slice_count"] == 2
