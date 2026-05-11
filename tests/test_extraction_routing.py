"""extraction_routing：输入/模板摘要与 pipeline_routing 结构。"""

import pytest

from src.core.extraction_routing import (
    TRACK_WORD_MULTI_PARALLEL,
    build_pipeline_routing_meta,
    decide_word_multi_langextract_prefill,
    is_word_multi_parallel_enabled,
    summarize_input_side,
)


def test_summarize_input_side_empty():
    s = summarize_input_side(None, None, 50)
    assert s["input_kind"] == "unknown"
    assert s["has_text_chunks"] is False


def test_build_routing_meta_word_multi_parallel():
    profile = {
        "template_mode": "word_multi_table",
        "word_multi_parallel": True,
        "table_specs": [
            {"table_index": 0, "field_names": ["a"]},
            {"table_index": 1, "field_names": ["a"]},
        ],
    }
    bundle = {"documents": [{"path": "/x/data.xlsx", "chunks": [{"type": "text", "text": "z"}]}]}
    chunks = [{"type": "text", "text": "hello"}]
    m = build_pipeline_routing_meta(
        profile,
        bundle,
        chunks,
        50,
        parallel_word_multi_enabled=True,
        use_model=True,
    )
    assert m["primary_track"] == TRACK_WORD_MULTI_PARALLEL
    assert m["schema_version"] == 1
    assert "pipeline" not in m
    assert m["input"]["suffixes"] == [".xlsx"]
    assert m["parallel_word_tables"] is True


def test_build_routing_meta_model_disabled():
    m = build_pipeline_routing_meta(
        {"template_mode": "excel_table"},
        None,
        None,
        0,
        parallel_word_multi_enabled=False,
        use_model=False,
    )
    assert m["primary_track"] == "model_disabled"


def test_decide_prefill_robust_none_profile():
    ok, reason = decide_word_multi_langextract_prefill(None, None, 50)
    assert ok is False
    assert reason == "auto_not_word_multi"


def test_is_word_multi_parallel_env_off(monkeypatch):
    monkeypatch.setenv("A23_WORD_MULTI_PARALLEL", "0")
    p = {"template_mode": "word_multi_table", "word_multi_parallel": True}
    assert is_word_multi_parallel_enabled(p) is False


def test_is_word_multi_parallel_wrong_template(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_PARALLEL", raising=False)
    p = {"template_mode": "excel_table", "word_multi_parallel": True}
    assert is_word_multi_parallel_enabled(p) is False


def test_is_word_multi_parallel_profile_false(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_PARALLEL", raising=False)
    p = {"template_mode": "word_multi_table", "word_multi_parallel": False}
    assert is_word_multi_parallel_enabled(p) is False


def test_is_word_multi_parallel_default_true(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_PARALLEL", raising=False)
    p = {"template_mode": "word_multi_table"}
    assert is_word_multi_parallel_enabled(p) is True


def test_is_word_multi_parallel_homogeneous_tables_auto_off(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_PARALLEL", raising=False)
    p = {
        "template_mode": "word_multi_table",
        "table_specs": [
            {"table_index": 0, "field_names": ["城市", "站点名称"]},
            {"table_index": 1, "field_names": ["城市", "站点名称"]},
        ],
    }
    assert is_word_multi_parallel_enabled(p) is False


def test_build_routing_meta_parallel_none_uses_profile(monkeypatch):
    monkeypatch.delenv("A23_WORD_MULTI_PARALLEL", raising=False)
    profile = {
        "template_mode": "word_multi_table",
        "word_multi_parallel": True,
        "table_specs": [
            {"table_index": 0, "field_names": ["a"]},
            {"table_index": 1, "field_names": ["a"]},
        ],
    }
    m = build_pipeline_routing_meta(
        profile,
        None,
        [{"type": "text", "text": "x"}],
        50,
        parallel_word_multi_enabled=None,
        use_model=True,
    )
    assert m["primary_track"] != TRACK_WORD_MULTI_PARALLEL
    assert m["parallel_word_tables"] is False
