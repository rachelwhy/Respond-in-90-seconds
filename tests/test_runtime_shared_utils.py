from src.api.storage_utils import safe_upload_name, sanitize_output_files_for_client
from src.core.llm_runtime import resolve_llm_mode_with_readiness


def test_safe_upload_name_blocks_path_traversal():
    assert safe_upload_name("../a.txt", "fallback.bin") == "fallback.bin"
    assert safe_upload_name("..\\a.txt", "fallback.bin") == "fallback.bin"
    assert safe_upload_name("normal.txt", "fallback.bin") == "normal.txt"


def test_sanitize_output_files_for_client_removes_report_bundle():
    data = {
        "result": "x",
        "report_bundle": "secret",
        "by_input": {
            "a": {"ok": 1, "report_bundle": "hidden"},
            "b": {"ok": 2},
        },
    }
    out = sanitize_output_files_for_client(data)
    assert "report_bundle" not in out
    assert "report_bundle" not in out["by_input"]["a"]
    assert out["by_input"]["a"]["ok"] == 1


def test_resolve_llm_mode_with_readiness_fallback(monkeypatch):
    from src.core import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "detect_model_readiness",
        lambda model_type, check_ollama=True: {"ready": False, "model_type": model_type, "reason": "unavailable"},
    )
    out = resolve_llm_mode_with_readiness("full", "deepseek", quiet=True)
    assert out.normalized == "full"
    assert out.effective == "off"
    assert out.fallback_rule_only is True


def test_resolve_llm_mode_with_readiness_off_stays_off(monkeypatch):
    from src.core import llm_runtime

    monkeypatch.setattr(
        llm_runtime,
        "detect_model_readiness",
        lambda model_type, check_ollama=True: {"ready": True, "model_type": model_type, "reason": "ok"},
    )
    out = resolve_llm_mode_with_readiness("off", "deepseek", quiet=True)
    assert out.normalized == "off"
    assert out.effective == "off"
    assert out.fallback_rule_only is False
