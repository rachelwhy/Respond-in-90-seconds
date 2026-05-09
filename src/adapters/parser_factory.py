from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple
from zipfile import BadZipFile, ZipFile, is_zipfile

from src.adapters.docling_adapter import DoclingParser
from src.adapters.text_parser import TextParser

# Docling 支持的版式与二进制文档
DOCLING_SUPPORTED_SUFFIXES = {
    ".doc",
    ".docx",
    ".pdf",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".rtf",
    ".html",
    ".htm",
    ".epub",
    ".odt",
    ".ods",
    ".odp",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
}

# 明确文本类
TEXT_PARSER_SUFFIXES = {
    ".txt",
    ".md",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".xml",
    ".yaml",
    ".yml",
    ".log",
    ".ini",
    ".cfg",
    ".conf",
}

SUPPORTED_SUFFIXES = TEXT_PARSER_SUFFIXES | DOCLING_SUPPORTED_SUFFIXES

ZIP_DOC_SUFFIXES = {".docx", ".xlsx", ".xlsm", ".pptx", ".epub", ".odt", ".ods", ".odp"}
OLE_SUFFIXES = {".doc", ".xls", ".ppt"}
PDF_SUFFIXES = {".pdf"}
RTF_SUFFIXES = {".rtf"}
HTML_SUFFIXES = {".html", ".htm"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}

_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


def _read_head(path: Path, limit: int = 8192) -> bytes:
    try:
        return path.read_bytes()[:limit]
    except Exception:
        return b""


def _is_probably_text(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False
    printable = sum(1 for b in data if b in (9, 10, 13) or 32 <= b <= 126 or b >= 160)
    return (printable / max(1, len(data))) >= 0.7


def _is_html_text(data: bytes) -> bool:
    if not data:
        return False
    s = data.decode("utf-8", errors="ignore").lower()
    return "<html" in s or "<!doctype html" in s


def _sniff_kind(path: Path) -> str:
    data = _read_head(path)
    low = data.lower()

    if low.startswith(b"%pdf-"):
        return "pdf"
    if data.startswith(_OLE_MAGIC):
        return "ole"
    if low.startswith(b"{\\rtf"):
        return "rtf"
    if low.startswith((b"\x89png\r\n\x1a\n",)):
        return "image"
    if low.startswith((b"\xff\xd8\xff",)):
        return "image"
    if low.startswith((b"bm",)):
        return "image"
    if low.startswith((b"ii*\x00", b"mm\x00*")):
        return "image"
    if is_zipfile(path):
        return "zip"
    if _is_probably_text(data):
        if _is_html_text(data):
            return "html"
        return "text"
    return "binary_unknown"


def _is_docling_compatible_zip(path: Path) -> bool:
    try:
        with ZipFile(path, "r") as zf:
            names = [n.lower() for n in zf.namelist()[:2000]]
    except (BadZipFile, OSError):
        return False
    if "[content_types].xml" in names:
        return True
    prefixes = ("word/", "xl/", "ppt/", "meta-inf/", "mimetype")
    return any(n.startswith(prefixes) for n in names)


def resolve_parser(path: Path) -> Tuple[Optional[object], str]:
    """
    精准分发（内容签名优先，后缀作约束）：
    - 返回 (parser, reason)
    """
    p = Path(path)
    ext = p.suffix.lower()
    kind = _sniff_kind(p)

    if ext in TEXT_PARSER_SUFFIXES:
        if kind in {"text", "html", "rtf"}:
            return TextParser(), "text_suffix_and_text_signature"
        return None, f"text_suffix_but_signature_{kind}"

    if ext in ZIP_DOC_SUFFIXES:
        if kind != "zip":
            return None, f"zip_doc_suffix_but_signature_{kind}"
        if not _is_docling_compatible_zip(p):
            return None, "zip_container_not_doc_compatible"
        return DoclingParser(), "zip_doc_suffix_and_doc_compatible_zip"

    if ext in OLE_SUFFIXES:
        if kind != "ole":
            return None, f"ole_suffix_but_signature_{kind}"
        return DoclingParser(), "ole_suffix_and_ole_signature"

    if ext in PDF_SUFFIXES:
        if kind != "pdf":
            return None, f"pdf_suffix_but_signature_{kind}"
        return DoclingParser(), "pdf_suffix_and_pdf_signature"

    if ext in RTF_SUFFIXES:
        if kind != "rtf":
            return None, f"rtf_suffix_but_signature_{kind}"
        return DoclingParser(), "rtf_suffix_and_rtf_signature"

    if ext in HTML_SUFFIXES:
        if kind not in {"html", "text"}:
            return None, f"html_suffix_but_signature_{kind}"
        return DoclingParser(), "html_suffix_and_text_signature"

    if ext in IMAGE_SUFFIXES:
        if kind != "image":
            return None, f"image_suffix_but_signature_{kind}"
        return DoclingParser(), "image_suffix_and_image_signature"

    # 无后缀或未知后缀：纯内容签名分发
    if kind in {"text", "html", "rtf"}:
        return TextParser(), f"unknown_suffix_signature_{kind}"
    if kind == "pdf":
        return DoclingParser(), "unknown_suffix_signature_pdf"
    if kind == "ole":
        return DoclingParser(), "unknown_suffix_signature_ole"
    if kind == "image":
        return DoclingParser(), "unknown_suffix_signature_image"
    if kind == "zip" and _is_docling_compatible_zip(p):
        return DoclingParser(), "unknown_suffix_signature_doc_compatible_zip"
    return None, f"no_parser_for_signature_{kind}"


def get_parser(path, parser_type: str = None):
    """
    兼容旧接口：仅返回 parser；`parser_type` 保留参数位。
    """
    _ = parser_type
    parser, _reason = resolve_parser(Path(path))
    return parser
