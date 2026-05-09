"""CoreExtractionService.extract_from_document：统一解析链与错误语义。"""

import pytest


def test_extract_from_document_file_not_found():
    from src.core.extraction_service import CoreExtractionService

    svc = CoreExtractionService()
    with pytest.raises(FileNotFoundError):
        svc.extract_from_document("/nonexistent/path/no_such_file.txt", {"fields": []})


def test_extract_from_document_no_parser_raises(monkeypatch, tmp_path):
    from src.core.extraction_service import CoreExtractionService

    p = tmp_path / "weird.bin"
    p.write_bytes(b"\x00\x01")

    monkeypatch.setattr("src.adapters.parser_factory.get_parser", lambda path: None)

    svc = CoreExtractionService()
    with pytest.raises(ValueError, match="不支持"):
        svc.extract_from_document(str(p), {"fields": []})


def test_extract_from_document_uses_parser_text(monkeypatch, tmp_path):
    from src.core.extraction_service import CoreExtractionService

    p = tmp_path / "sample.txt"
    p.write_text("ignored body", encoding="utf-8")

    class FakeParser:
        def parse(self, path):
            return {"text": "resolved text", "error": None}

    monkeypatch.setattr("src.adapters.parser_factory.get_parser", lambda path: FakeParser())

    captured = {}

    def fake_extract_from_text(text, profile, **kwargs):
        captured["text"] = text
        return {"records": [], "metadata": {}, "extracted_raw": {}, "model_output": {}}

    svc = CoreExtractionService()
    monkeypatch.setattr(svc, "extract_from_text", fake_extract_from_text)

    svc.extract_from_document(str(p), {"fields": []}, llm_mode="off")

    assert captured.get("text") == "resolved text"
