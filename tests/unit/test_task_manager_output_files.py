from pathlib import Path

from src.api.task_manager import _collect_output_files


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def test_collect_output_files_single_input_compat_keys(tmp_path: Path) -> None:
    out = tmp_path / "output"
    _touch(out / "sample_result.json")
    _touch(out / "sample_result.xlsx")
    _touch(out / "sample_result_report.json")

    got = _collect_output_files(out)

    assert got["result_json"].endswith("sample_result.json")
    assert got["result_xlsx"].endswith("sample_result.xlsx")
    assert got["report_bundle"].endswith("sample_result_report.json")
    assert got["multi_input"] is False
    assert "sample" in got["by_input"]


def test_collect_output_files_multi_input_lists_and_grouping(tmp_path: Path) -> None:
    out = tmp_path / "output"
    _touch(out / "a_result.json")
    _touch(out / "a_result.xlsx")
    _touch(out / "a_result_report.json")
    _touch(out / "b_result.json")
    _touch(out / "b_result.xlsx")
    _touch(out / "b_result_report.json")

    got = _collect_output_files(out)

    assert got["multi_input"] is True
    assert len(got["excel_files"]) == 2
    assert len(got["json_files"]) == 2
    assert len(got["report_bundle_files"]) == 2
    assert got["by_input"]["a"]["excel"].endswith("a_result.xlsx")
    assert got["by_input"]["b"]["json"].endswith("b_result.json")
