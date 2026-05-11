"""writers 默认输出文件名规则回归。"""

from src.core.writers import build_default_extracted_filename, sanitize_template_stem_for_output


def test_sanitize_stem():
    assert sanitize_template_stem_for_output("  foo/bar  ") == "foo_bar"
    assert sanitize_template_stem_for_output("") == "output"


def test_build_default_extracted_filename():
    n = build_default_extracted_filename(r"C:\t\模板A.xlsx", ".xlsx", ts=1700000000)
    assert n == "模板A_Doc90_1700000000.xlsx"
    n2 = build_default_extracted_filename(None, "xlsx", ts=1)
    assert n2 == "output_Doc90_1.xlsx"
