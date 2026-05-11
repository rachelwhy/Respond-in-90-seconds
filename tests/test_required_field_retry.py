from src.core.required_field_retry import evaluate_and_retry_required_fields, with_source_text


def test_with_source_text_wraps_list_payload():
    payload = with_source_text([{"a": 1}], "abc")
    assert payload["records"][0]["a"] == 1
    assert payload["_source_text"] == "abc"


def test_evaluate_and_retry_required_fields_no_missing(monkeypatch):
    monkeypatch.setattr(
        "src.core.required_field_retry.process_by_profile",
        lambda data, _profile: data,
    )
    monkeypatch.setattr(
        "src.core.required_field_retry.validate_required_fields",
        lambda _final_data, _profile: [],
    )

    out, missing, retried = evaluate_and_retry_required_fields(
        extracted_raw={"records": [{"name": "ok"}]},
        profile={"fields": [{"name": "name", "required": True}]},
        context_for_retry="ctx",
        source_text_for_order="src",
    )

    assert out["records"][0]["name"] == "ok"
    assert missing == []
    assert retried == []


def test_evaluate_and_retry_required_fields_with_missing(monkeypatch):
    monkeypatch.setattr(
        "src.core.required_field_retry.process_by_profile",
        lambda data, _profile: data,
    )
    monkeypatch.setattr(
        "src.core.required_field_retry.validate_required_fields",
        lambda _final_data, _profile: ["company"],
    )

    def _fake_retry(ctx, _profile, extracted, missing):
        assert ctx == "ctx"
        assert missing == ["company"]
        out = dict(extracted)
        out["company"] = "Acme"
        return out, ["company"]

    monkeypatch.setattr("src.core.required_field_retry.retry_missing_required_fields", _fake_retry)

    out, missing, retried = evaluate_and_retry_required_fields(
        extracted_raw={"records": [{}]},
        profile={"fields": [{"name": "company", "required": True}]},
        context_for_retry="ctx",
        source_text_for_order="src",
    )

    assert out["company"] == "Acme"
    assert missing == ["company"]
    assert retried == ["company"]
