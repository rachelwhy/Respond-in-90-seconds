from src.core.profile_resolver import resolve_profile


def test_resolve_profile_prefers_json_template(tmp_path):
    template = tmp_path / "profile.json"
    template.write_text('{"fields":[{"name":"A","type":"text"}]}', encoding="utf-8")

    profile = resolve_profile(
        template_path=str(template),
        instruction="ignored",
        document_text="ignored",
    )

    assert profile["fields"][0]["name"] == "A"


def test_resolve_profile_instruction_mode(monkeypatch):
    def _fake_generate_profile_smart(*, template_path, instruction, document_sample):
        assert template_path == ""
        assert instruction == "extract key fields"
        assert document_sample == "doc sample"
        return {"fields": [{"name": "X", "type": "text"}], "instruction": instruction}

    def _fake_apply_hints(profile, instruction):
        assert instruction == "extract key fields"
        profile = dict(profile)
        profile["_hint_applied"] = True
        return profile

    monkeypatch.setattr("src.core.profile_resolver.generate_profile_smart", _fake_generate_profile_smart)
    monkeypatch.setattr("src.core.profile_resolver.apply_instruction_runtime_hints", _fake_apply_hints)

    profile = resolve_profile(
        template_path="",
        instruction="extract key fields",
        document_text="doc sample",
    )

    assert profile["_hint_applied"] is True
    assert profile["fields"][0]["name"] == "X"


def test_resolve_profile_document_fallback(monkeypatch):
    def _fake_generate_profile_from_document(text):
        assert text == "plain content"
        return {"fields": [{"name": "Y", "type": "text"}]}

    monkeypatch.setattr(
        "src.core.profile_resolver.generate_profile_from_document",
        _fake_generate_profile_from_document,
    )
    monkeypatch.setattr(
        "src.core.profile_resolver.apply_instruction_runtime_hints",
        lambda profile, _instruction: profile,
    )

    profile = resolve_profile(
        template_path="",
        instruction="",
        document_text="plain content",
    )

    assert profile["fields"][0]["name"] == "Y"
