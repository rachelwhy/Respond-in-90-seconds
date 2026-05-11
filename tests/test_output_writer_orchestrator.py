from pathlib import Path

from src.core.output_writer_orchestrator import write_template_outputs_api, write_template_outputs_cli


def test_write_template_outputs_cli_no_template(monkeypatch, tmp_path):
    called = {"count": 0}

    def _fake_create_excel(path, records):
        called["count"] += 1
        assert str(path).endswith(".xlsx")
        assert len(records) == 1

    monkeypatch.setattr("src.core.output_writer_orchestrator.create_excel_from_records", _fake_create_excel)

    mode = write_template_outputs_cli(
        template_path="",
        is_no_template=True,
        is_generic_template=False,
        final_data={"records": [{"a": 1}]},
        profile={"template_mode": "generic"},
        output_xlsx=str(tmp_path / "out.xlsx"),
        output_docx=str(tmp_path / "out.docx"),
        logger=__import__("logging").getLogger(__name__),
    )
    assert mode == "generic"
    assert called["count"] == 1


def test_write_template_outputs_api_skip_when_no_records(tmp_path):
    output_file, output_files = write_template_outputs_api(
        template_path="",
        work_dir=tmp_path,
        records=[],
        profile={},
        final_data={},
        logger=__import__("logging").getLogger(__name__),
    )
    assert output_file is None
    assert output_files == []


def test_write_template_outputs_api_dynamic_excel(monkeypatch, tmp_path):
    touched = {"count": 0}

    def _fake_create_excel(path, records):
        touched["count"] += 1
        assert str(path).endswith(".xlsx")
        assert len(records) == 1

    monkeypatch.setattr("src.core.output_writer_orchestrator.create_excel_from_records", _fake_create_excel)

    output_file, output_files = write_template_outputs_api(
        template_path="missing-template.xlsx",
        work_dir=Path(tmp_path),
        records=[{"name": "a"}],
        profile={"template_mode": "excel_table"},
        final_data={"records": [{"name": "a"}]},
        logger=__import__("logging").getLogger(__name__),
    )
    assert touched["count"] == 1
    assert output_file is not None
    assert len(output_files) == 1
