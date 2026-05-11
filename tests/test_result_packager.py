from src.core.result_packager import (
    build_api_metadata,
    build_api_result,
    build_cli_report_bundle,
    build_generated_outputs,
)


def test_build_generated_outputs_respects_template_mode():
    out = build_generated_outputs(
        template_mode="word_table",
        output_json="a.json",
        output_xlsx="a.xlsx",
        output_docx="a.docx",
    )
    assert out["result_json"] == "a.json"
    assert out["result_xlsx"] == ""
    assert out["result_docx"] == "a.docx"


def test_build_api_metadata_includes_optional_sections():
    meta = build_api_metadata(
        file_count=2,
        records=[{"a": 1}],
        profile={"task_mode": "table_records", "template_mode": "word_multi_table", "_doc_type": "x"},
        doc_type="invoice",
        runtime_updates={
            "scope_resolution": {"mode": "table_row_filter"},
            "slicing_metadata": {"word_multi_parallel": True},
            "internal_structured_extract_seconds": 0.1,
        },
        llm_mode_requested="full",
        llm_mode_normalized="full",
        llm_mode_effective="full",
        readiness={"ready": True, "reason": "ok"},
        internal_route_used="internal_structured",
        retried_fields=["name"],
        final_data={"_table_groups": [{"table_index": 0, "records": []}]},
        model_output={"records": [{"a": 1}, {"a": 2}]},
        output_files=["a.xlsx"],
    )
    assert meta["file_count"] == 2
    assert meta["record_count"] == 1
    assert meta["word_multi_parallel"] is True
    assert meta["output_file_count"] == 1
    assert meta["model_output_preview"]["record_count"] == 2
    assert meta["scope_resolution"]["mode"] == "table_row_filter"
    assert meta["runtime_metrics"]["internal_structured_extract_seconds"] == 0.1


def test_build_api_result_sets_primary_output_file():
    res = build_api_result(
        records=[{"x": 1}],
        metadata={"ok": True},
        output_files=["a.xlsx", "b.xlsx"],
    )
    assert res["output_file"] == "a.xlsx"
    assert len(res["output_files"]) == 2


def test_build_cli_report_bundle(monkeypatch):
    monkeypatch.setattr(
        "src.core.result_packager.build_debug_result",
        lambda _data, _profile: {"debug": True},
    )
    monkeypatch.setattr(
        "src.core.result_packager.build_run_summary",
        lambda **_kwargs: {"summary": "ok"},
    )
    bundle = build_cli_report_bundle(
        final_data={"records": [{"a": 1}]},
        extracted_raw={"records": [{"a": 1}]},
        profile={"report_name": "r1", "template_path": "t.xlsx", "task_mode": "table_records"},
        runtime={"total_seconds": 1.2},
        missing_required_fields=[],
        retried_fields=["a"],
        input_text="hello",
        profile_path="p.json",
        template_mode="excel_table",
        output_json="out.json",
        output_xlsx="out.xlsx",
        output_docx="out.docx",
        rag_json_path="rag.json",
        retrieved_chunks=[{"text": "x"}],
        prefer_rag_structured=True,
        structured_rag_result={"records": []},
        internal_route_used="internal_structured",
        persist_profiles=True,
        field_evidence={"a": "evidence"},
    )
    assert bundle["meta"]["profile_name"] == "r1"
    assert bundle["meta"]["generated_outputs"]["result_xlsx"] == "out.xlsx"
    assert bundle["retrieval"]["internal_route_used"] == "internal_structured"
    assert bundle["field_evidence"]["a"] == "evidence"
