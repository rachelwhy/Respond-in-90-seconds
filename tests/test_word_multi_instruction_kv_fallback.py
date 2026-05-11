"""多表 Word：用户指令与模板表上方说明（instruction_above）的通用 filter 补全。"""
from src.core.profile import apply_word_multi_instruction_constraints


def _base_profile(table_specs: list) -> dict:
    return {
        "template_mode": "word_multi_table",
        "task_mode": "table_records",
        "table_specs": table_specs,
    }


def test_instruction_above_fills_filter_when_no_table_blocks():
    """无「表N：」时，从各表 instruction_above 按「列名：值」语法补 filter。"""
    profile = _base_profile(
        [
            {
                "table_index": 0,
                "field_names": ["维度", "数值"],
                "instruction_above": "维度：类型甲",
            },
            {
                "table_index": 1,
                "field_names": ["维度", "数值"],
                "instruction_above": "维度：类型乙",
            },
        ]
    )
    out = apply_word_multi_instruction_constraints(profile, "")
    specs = out["table_specs"]
    assert specs[0].get("filter_field") == "维度"
    assert specs[0].get("filter_value") == "类型甲"
    assert specs[1].get("filter_field") == "维度"
    assert specs[1].get("filter_value") == "类型乙"


def test_user_table_blocks_take_priority_over_instruction_above():
    profile = _base_profile(
        [
            {
                "table_index": 0,
                "field_names": ["键", "量"],
                "instruction_above": "键：上方占位",
            },
            {
                "table_index": 1,
                "field_names": ["键", "量"],
                "instruction_above": "键：仅表二用",
            },
        ]
    )
    instruction = "表1：\n键：显式覆盖\n"
    out = apply_word_multi_instruction_constraints(profile, instruction)
    specs = out["table_specs"]
    assert specs[0].get("filter_value") == "显式覆盖"
    assert specs[1].get("filter_value") == "仅表二用"


def test_last_resolved_kv_wins_as_filter_per_table():
    profile = _base_profile(
        [
            {
                "table_index": 0,
                "field_names": ["列A", "列B"],
                "instruction_above": "列A：先写\n列B：后写",
            },
        ]
    )
    out = apply_word_multi_instruction_constraints(profile, "")
    assert out["table_specs"][0].get("filter_field") == "列B"
    assert out["table_specs"][0].get("filter_value") == "后写"
    fixed = out["table_specs"][0].get("fixed_values") or {}
    assert fixed.get("列A") == "先写"
    assert fixed.get("列B") == "后写"
