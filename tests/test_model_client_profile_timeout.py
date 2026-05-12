"""model_client：call_model 将 timeout 透传至各后端 HTTP 单次超时。"""

from unittest.mock import MagicMock, patch


def test_call_model_dispatches_to_deepseek_backend():
    from src.adapters import model_client

    with patch.object(model_client, "_call_remote_chat", return_value={"r": 1}) as m:
        out = model_client.call_model("prompt", model_type="deepseek")
    assert out == {"r": 1}
    m.assert_called_once()
    assert m.call_args.kwargs.get("request_timeout") is None


def test_call_model_timeout_propagates_to_deepseek():
    from src.adapters import model_client

    with patch.object(model_client, "_call_remote_chat", return_value={}) as m:
        model_client.call_model("x", model_type="deepseek", timeout=33)
    assert m.call_args.kwargs.get("request_timeout") == 33
    assert m.call_args.kwargs.get("plain_text") is False


def test_call_model_plain_text_passed_to_deepseek():
    from src.adapters import model_client

    with patch.object(model_client, "_call_remote_chat", return_value={"answer": "ok"}) as m:
        out = model_client.call_model("p", model_type="deepseek", plain_text=True)
    assert out == {"answer": "ok"}
    assert m.call_args.kwargs.get("plain_text") is True


def test_call_deepseek_uses_explicit_timeout_on_http():
    from src.adapters import model_client

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
    mock_resp.raise_for_status = lambda: None
    mock_post = MagicMock(return_value=mock_resp)
    mock_sess = MagicMock()
    mock_sess.post = mock_post

    with patch("src.adapters.openai_compatible_chat.attempt_litellm_parsed_json", return_value=None):
        with patch("src.adapters.openai_compatible_chat.get_shared_session", return_value=mock_sess):
            model_client._call_deepseek("p", request_timeout=22)
    _, kwargs = mock_post.call_args
    assert kwargs.get("timeout") == 22
