"""多表约束路径不写入历史字段 ``constraint_text``（当前契约使用 filter / hints）。"""
from src.core.profile import apply_word_multi_instruction_constraints


def test_apply_word_multi_instruction_constraints_no_legacy_constraint_text():
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["列"], "instruction_above": ""},
        ],
    }
    out = apply_word_multi_instruction_constraints(profile, "")
    assert out["table_specs"][0].get("constraint_text") is None
