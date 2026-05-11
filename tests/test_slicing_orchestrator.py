from src.core.slicing_orchestrator import get_profile_max_llm_context_chars


def test_default_context_window_for_non_word_multi():
    profile = {"template_mode": "excel_table"}
    assert get_profile_max_llm_context_chars(profile) == 24000


def test_homogeneous_word_multi_uses_large_context_window():
    profile = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["城市", "站点名称"]},
            {"table_index": 1, "field_names": ["城市", "站点名称"]},
        ],
    }
    assert get_profile_max_llm_context_chars(profile) == 80000
