"""问答会话契约：history_json 解析、持久化开关。"""

import pytest

from src.api.qna_service import (
    normalize_conversation_history,
    parse_conversation_history_json,
    parse_persist_session_form,
)


def test_parse_history_json_none_and_roundtrip():
    assert parse_conversation_history_json(None) is None
    assert parse_conversation_history_json("") is None
    assert parse_conversation_history_json("   ") is None


def test_parse_history_json_valid():
    raw = '[{"q":"你好","a":"您好","t":1}]'
    out = parse_conversation_history_json(raw)
    assert len(out) == 1
    assert out[0]["q"] == "你好"
    assert out[0]["a"] == "您好"
    assert out[0]["t"] == 1


def test_parse_history_invalid_raises():
    with pytest.raises(ValueError):
        parse_conversation_history_json("{")
    with pytest.raises(ValueError):
        parse_conversation_history_json('"x"')


def test_normalize_drops_empty_pairs():
    assert normalize_conversation_history([{"q": "", "a": ""}, {"q": "a", "a": "b"}]) == [{"q": "a", "a": "b"}]


def test_parse_persist_session_form():
    assert parse_persist_session_form(None) is None
    assert parse_persist_session_form("") is None
    assert parse_persist_session_form("true") is True
    assert parse_persist_session_form("FALSE") is False
    with pytest.raises(ValueError):
        parse_persist_session_form("maybe")
