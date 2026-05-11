import asyncio
import io

from starlette.datastructures import UploadFile

from src.api.complexity_estimator import estimate_document_complexity


def _make_upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(content), filename=filename)


def test_estimate_document_complexity_fast_mode(monkeypatch):
    monkeypatch.setenv("A23_COMPLEXITY_ESTIMATOR", "fast")
    files = [
        _make_upload("a.txt", b"hello world"),
        _make_upload("b.pdf", b"binary"),
    ]
    out = asyncio.run(estimate_document_complexity(files, None, "full"))
    assert out["estimator"] == "fast"
    assert out["estimated_chunks"] >= 1
    assert out["recommendation"] == "direct"
