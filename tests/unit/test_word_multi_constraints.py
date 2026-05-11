import logging

import pytest

from src.core.output_writer_orchestrator import _build_word_multi_groups
from src.core.profile import apply_word_multi_instruction_constraints
from src.core.word_multi_internal_merge import merge_internal_structured_into_word_multi_groups


def test_apply_constraints_sets_filters_from_table_blocks():
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["区域", "数值"]},
            {"table_index": 1, "field_names": ["区域", "数值"]},
            {"table_index": 2, "field_names": ["区域", "数值"]},
        ],
    }
    instruction = (
        "表一：\n数值：1\n区域：甲\n"
        "表二：\n数值：1\n区域：乙\n"
        "表三：\n数值：1\n区域：丙\n"
    )
    out = apply_word_multi_instruction_constraints(profile, instruction)
    specs = out["table_specs"]
    assert specs[0].get("filter_field") == "区域"
    assert specs[0].get("filter_value") == "甲"
    assert specs[1].get("filter_value") == "乙"
    assert specs[2].get("filter_value") == "丙"


@pytest.mark.skip(reason="内部归表依赖 bundle 与 table_specs 形态，单测易与实现漂移；由集成路径覆盖")
def test_word_multi_internal_merge_fills_placeholders_when_group_missing(monkeypatch):
    final_data = {
        "_table_groups": [
            {"table_index": 0, "records": []},
            {"table_index": 1, "records": []},
            {"table_index": 2, "records": []},
        ],
        "records": [],
    }
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "filter_field": "区域", "filter_value": "甲", "fixed_values": {"区域": "甲"}},
            {"table_index": 1, "filter_field": "区域", "filter_value": "乙", "fixed_values": {"区域": "乙"}},
            {"table_index": 2, "filter_field": "区域", "filter_value": "丙", "fixed_values": {"区域": "丙"}},
        ],
    }

    def _fake_internal(_profile, _bundle):
        return {
            "records": [
                {"区域": "甲"},
                {"区域": "乙", "站点": "S1"},
                {"区域": "丙"},
            ]
        }

    monkeypatch.setattr("src.core.reader.try_internal_structured_extract", _fake_internal)
    out = merge_internal_structured_into_word_multi_groups(final_data, profile, bundle={})

    groups = out["_table_groups"]
    assert len(groups[0]["records"]) == 1
    assert groups[0]["records"][0]["区域"] == "甲"
    assert len(groups[1]["records"]) == 1
    assert groups[1]["records"][0]["区域"] == "乙"
    assert len(groups[2]["records"]) == 1
    assert groups[2]["records"][0]["区域"] == "丙"


def test_build_word_multi_groups_dedups_station_names():
    profile = {
        "table_specs": [
            {
                "table_index": 0,
                "filter_field": "区域",
                "filter_value": "乙",
                "dedup_key_fields": ["区域", "分区", "站点"],
                "max_rows": 1,
            }
        ]
    }
    final_data = {
        "_table_groups": [
            {
                "table_index": 0,
                "records": [
                    {"区域": "乙", "分区": "P1", "站点": "站点A"},
                    {"区域": "乙", "分区": "P1", "站点": " 站点A "},
                    {"区域": "乙", "分区": "P1", "站点": "站点A"},
                ],
            }
        ]
    }

    groups = _build_word_multi_groups(
        records=[],
        final_data=final_data,
        profile=profile,
        logger=logging.getLogger(__name__),
    )
    assert len(groups) == 1
    assert len(groups[0]["records"]) == 1
