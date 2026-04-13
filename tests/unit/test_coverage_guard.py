from src.core.coverage_guard import (
    CoverageSpec,
    compute_missing_keys,
    derive_coverage_spec_from_bundle,
    derive_coverage_spec,
)


def test_derive_spec_from_docling_table_data_dict_rows():
    bundle = {
        "documents": [
            {
                "tables": [
                    {
                        "data": [
                            {"名称": "KeyA", "值": "1"},
                            {"名称": "KeyB", "值": "2"},
                            {"名称": "KeyB", "值": "2"},  # duplicate
                            {"名称": "", "值": "x"},
                        ]
                    }
                ]
            }
        ]
    }
    profile = {
        "task_mode": "table_records",
        "fields": [{"name": "名称"}, {"name": "值"}],
        "dedup_key_fields": ["名称"],
    }
    spec = derive_coverage_spec_from_bundle(bundle, profile)
    assert spec is not None
    assert spec.key_field == "名称"
    assert spec.expected_keys == ["KeyA", "KeyB"]


def test_compute_missing_keys():
    spec = CoverageSpec(key_field="名称", expected_keys=["A", "B", "C"])
    records = [{"名称": "A"}, {"名称": "C"}]
    missing = compute_missing_keys(spec, records)
    assert missing == ["B"]


def test_derive_spec_from_text_fallback_markdown_table():
    bundle = {"documents": [{"tables": []}]}
    profile = {"task_mode": "table_records", "fields": [{"name": "名称"}], "dedup_key_fields": ["名称"]}
    md = """
| 名称 | 值 |
| --- | --- |
| KeyA | 1 |
| KeyB | 2 |
"""
    spec = derive_coverage_spec(bundle, profile, source_text=md)
    assert spec is not None
    # Heuristic should extract first-cell keys; header row may be included depending on input.
    assert "KeyA" in spec.expected_keys
    assert "KeyB" in spec.expected_keys


def test_derive_spec_from_text_fallback_field_marker_split():
    # Simulate prose-like rows: "Key 字段A ... 字段B ..."
    bundle = {"documents": [{"tables": []}]}
    profile = {
        "task_mode": "table_records",
        "fields": [{"name": "名称"}, {"name": "金额"}, {"name": "日期"}],
        "dedup_key_fields": ["名称"],
    }
    txt = "Alpha 金额 10 日期 2025-01-01\nBeta 金额 20 日期 2025-01-02\n"
    spec = derive_coverage_spec(bundle, profile, source_text=txt)
    assert spec is not None
    assert "Alpha" in spec.expected_keys
    assert "Beta" in spec.expected_keys

