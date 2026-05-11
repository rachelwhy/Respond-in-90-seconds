"""word_multi_langextract_merge 补缺合并逻辑单元测试。"""

import pytest

from src.core.word_multi_langextract_merge import (
    merge_langextract_into_word_multi_groups,
    table_specs_homogeneous_columns,
    word_multi_langextract_prefill_should_run,
)


@pytest.fixture
def profile_one_table():
    return {
        "template_mode": "word_multi_table",
        "table_specs": [
            {
                "table_index": 0,
                "filter_field": "城市",
                "filter_value": "潍坊市",
                "table_profile": {
                    "fields": [{"name": "城市"}, {"name": "PM2.5"}, {"name": "PM10"}],
                },
            },
        ],
    }


def test_fill_empty_fields_from_langextract(profile_one_table):
    final_data = {
        "_table_groups": [
            {"table_index": 0, "records": [{"城市": "潍坊市", "PM2.5": "", "PM10": ""}]},
        ],
        "records": [{"城市": "潍坊市", "PM2.5": "", "PM10": ""}],
    }
    lx = [{"城市": "潍坊市", "PM2.5": "45", "PM10": "80"}]
    out = merge_langextract_into_word_multi_groups(final_data, profile_one_table, lx)
    row = out["_table_groups"][0]["records"][0]
    assert row["PM2.5"] == "45"
    assert row["PM10"] == "80"


def test_does_not_overwrite_nonempty(profile_one_table):
    final_data = {
        "_table_groups": [
            {"table_index": 0, "records": [{"城市": "潍坊市", "PM2.5": "10", "PM10": ""}]},
        ],
        "records": [{"城市": "潍坊市", "PM2.5": "10", "PM10": ""}],
    }
    lx = [{"城市": "潍坊市", "PM2.5": "99", "PM10": "77"}]
    out = merge_langextract_into_word_multi_groups(final_data, profile_one_table, lx)
    row = out["_table_groups"][0]["records"][0]
    assert row["PM2.5"] == "10"
    assert row["PM10"] == "77"


def test_replace_when_weak_rows(profile_one_table):
    final_data = {
        "_table_groups": [{"table_index": 0, "records": [{"城市": "潍坊市"}]}],
        "records": [{"城市": "潍坊市"}],
    }
    lx = [{"城市": "潍坊市", "PM2.5": "1", "PM10": "2"}]
    out = merge_langextract_into_word_multi_groups(final_data, profile_one_table, lx)
    assert len(out["_table_groups"][0]["records"]) >= 1
    assert out["_table_groups"][0]["records"][0].get("PM2.5") == "1"


def test_non_word_multi_noop():
    final_data = {"_table_groups": [{"table_index": 0, "records": []}]}
    profile = {"template_mode": "excel_table"}
    out = merge_langextract_into_word_multi_groups(final_data, profile, [{"a": 1}])
    assert out == final_data


def test_homogeneous_specs():
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["城市", "PM2.5"]},
            {"table_index": 1, "field_names": ["PM2.5", "城市"]},
        ],
    }
    assert table_specs_homogeneous_columns(profile) is True


def test_heterogeneous_specs():
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["城市", "PM2.5"]},
            {"table_index": 1, "field_names": ["城市", "SO2"]},
        ],
    }
    assert table_specs_homogeneous_columns(profile) is False


def test_prefill_should_run_auto_homogeneous(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_LANGEXTRACT", raising=False)
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["a"]},
            {"table_index": 1, "field_names": ["a"]},
        ],
    }
    chunks = [{"type": "text", "text": "x"}]
    ok, reason = word_multi_langextract_prefill_should_run(profile, chunks, 50)
    assert ok is True
    assert reason == "auto_homogeneous_tables"


def test_prefill_auto_skips_heterogeneous(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_LANGEXTRACT", raising=False)
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["a"]},
            {"table_index": 1, "field_names": ["b"]},
        ],
    }
    chunks = [{"type": "text", "text": "x"}]
    ok, reason = word_multi_langextract_prefill_should_run(profile, chunks, 50)
    assert ok is False
    assert reason == "auto_heterogeneous_tables"


def test_prefill_skips_when_specs_empty():
    """路由仅依据同质列与 text chunks；空 table_specs 视为非同构。"""
    profile = {"template_mode": "word_multi_table", "table_specs": []}
    chunks = [{"type": "text", "text": "x"}]
    ok, reason = word_multi_langextract_prefill_should_run(profile, chunks, 50)
    assert ok is False
    assert reason == "auto_heterogeneous_tables"
