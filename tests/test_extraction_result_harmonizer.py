from src.core.extraction_result_harmonizer import (
    records_from_final_data,
    merge_internal_structured_when_model_insufficient,
)


def test_records_from_final_data_with_records():
    rows = records_from_final_data({"records": [{"a": 1}]})
    assert rows == [{"a": 1}]


def test_records_from_final_data_single_record_object():
    rows = records_from_final_data({"name": "x", "_meta": "m"})
    assert rows == [{"name": "x"}]


def test_merge_internal_structured_when_model_empty(monkeypatch):
    def _echo(payload, _profile):
        return {"records": list(payload.get("records") or [])}

    monkeypatch.setattr("src.core.extraction_result_harmonizer.process_by_profile", _echo)
    merged = merge_internal_structured_when_model_insufficient(
        final_data={},
        internal_structured={"records": [{"city": "A"}]},
        effective_llm_mode="full",
        all_text="source",
        profile={"task_mode": "table_records"},
        logger=__import__("logging").getLogger(__name__),
    )
    assert merged["records"][0]["city"] == "A"
