from pathlib import Path
from zipfile import ZipFile

from src.adapters.parser_factory import resolve_parser


def test_resolve_parser_unknown_text_suffix(tmp_path):
    p = tmp_path / "sample.custom"
    p.write_text("hello world\nthis is plain text", encoding="utf-8")
    parser, reason = resolve_parser(p)
    assert parser is not None
    assert parser.parser_type == "text"
    assert reason.startswith("unknown_suffix_signature_")


def test_resolve_parser_pdf_suffix_signature_mismatch(tmp_path):
    p = tmp_path / "bad.pdf"
    p.write_text("not a pdf", encoding="utf-8")
    parser, reason = resolve_parser(p)
    assert parser is None
    assert "pdf_suffix_but_signature_" in reason


def test_resolve_parser_unknown_suffix_doc_compatible_zip(tmp_path):
    p = tmp_path / "noext"
    with ZipFile(p, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("word/document.xml", "<w:document></w:document>")
    parser, reason = resolve_parser(p)
    assert parser is not None
    assert parser.parser_type == "docling"
    assert reason == "unknown_suffix_signature_doc_compatible_zip"
