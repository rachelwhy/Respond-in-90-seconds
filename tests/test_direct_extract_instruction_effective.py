"""direct_extract 指令解析：表单优先、侧文件 用户要求.txt。"""
import tempfile
from pathlib import Path

from src.api.direct_extractor import effective_instruction_for_extract


def test_effective_instruction_prefers_explicit():
    assert effective_instruction_for_extract("/no/such/dir", "  hello  ") == "hello"


def test_effective_instruction_reads_sidecar_when_empty():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "用户要求.txt").write_text("sidecar\nline2\n", encoding="utf-8")
        assert effective_instruction_for_extract(str(d), None) == "sidecar\nline2"
        assert effective_instruction_for_extract(str(d), "") == "sidecar\nline2"


def test_effective_instruction_none_when_missing():
    with tempfile.TemporaryDirectory() as td:
        assert effective_instruction_for_extract(str(Path(td)), None) is None
