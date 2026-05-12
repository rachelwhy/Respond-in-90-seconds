from src.core import model_availability as ma


def test_detect_deepseek_missing_key(monkeypatch):
    import src.config as cfg

    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "")
    ma._READY_CACHE.clear()
    out = ma.detect_model_readiness("deepseek", check_ollama=False)
    assert out["ready"] is False
    assert out["reason"] == "missing_deepseek_api_key"


def test_detect_deepseek_with_key(monkeypatch):
    import src.config as cfg

    monkeypatch.setattr(cfg, "DEEPSEEK_API_KEY", "sk-ok")
    ma._READY_CACHE.clear()
    out = ma.detect_model_readiness("deepseek", check_ollama=False)
    assert out["ready"] is True
    assert out["reason"] == "ok"


def test_detect_ollama_unreachable(monkeypatch):
    def _boom(*_args, **_kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(ma.requests, "get", _boom)
    ma._READY_CACHE.clear()
    out = ma.detect_model_readiness("ollama", check_ollama=True, timeout_seconds=0.01)
    assert out["ready"] is False
    assert out["reason"] == "ollama_unreachable"


def test_detect_unsupported_model():
    ma._READY_CACHE.clear()
    out = ma.detect_model_readiness("unknown_model")
    assert out["ready"] is False
    assert str(out["reason"]).startswith("unsupported_model_type:")
