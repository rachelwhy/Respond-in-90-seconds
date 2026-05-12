"""prompt_redaction：知识库正则脱敏与 call_model 集成。"""

from pathlib import Path
from unittest.mock import patch


def test_redact_and_restore_roundtrip():
    from src.core.prompt_redaction import redact_prompt, restore_model_output

    cfg = str(Path("src/knowledge/redaction_patterns.json"))
    text = "联系人 13812345678 完毕"
    redacted, mapping = redact_prompt(text, config_path=cfg)
    assert "13812345678" not in redacted
    assert mapping
    assert any("⟦A23X" in k for k in mapping)
    out = restore_model_output({"records": [{"tel": redacted}]}, mapping)
    assert "13812345678" in out["records"][0]["tel"]


def test_ollama_skips_redaction_in_call_model(monkeypatch):
    import src.config as cfg
    from src.adapters import model_client

    monkeypatch.setattr(cfg, "REDACT_REMOTE_PROMPTS", True)
    seen = {}

    def fake_ollama(prompt, **kwargs):
        seen["prompt"] = prompt
        return {"answer": "ok"}

    with patch.object(model_client, "_call_ollama", side_effect=fake_ollama):
        model_client.call_model("电话13812345678", model_type="ollama")
    assert "13812345678" in seen["prompt"]


def test_deepseek_redacts_when_enabled(monkeypatch):
    import src.config as cfg
    from src.adapters import model_client

    monkeypatch.setattr(cfg, "REDACT_REMOTE_PROMPTS", True)

    def fake_deepseek(prompt, **kwargs):
        assert "13812345678" not in prompt
        assert "⟦A23X" in prompt
        return {"records": [{"t": "⟦A23X0⟧"}]}

    with patch.object(model_client, "_call_remote_chat", side_effect=fake_deepseek):
        out = model_client.call_model("号码13812345678测试", model_type="deepseek")
    assert out["records"][0]["t"] == "13812345678"
