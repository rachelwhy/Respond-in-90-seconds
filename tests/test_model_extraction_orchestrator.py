from src.core.model_extraction_orchestrator import run_model_extraction_path


def test_run_model_extraction_path_returns_runtime_updates(monkeypatch):
    monkeypatch.setattr(
        "src.core.model_extraction_orchestrator.get_profile_max_llm_context_chars",
        lambda _profile: 24000,
    )
    monkeypatch.setattr(
        "src.core.model_extraction_orchestrator.prepare_llm_context_and_chunks",
        lambda **_kwargs: ("ctx", [{"text": "a"}], {"documents": []}, {"mode": "table_row_filter"}),
    )
    monkeypatch.setattr(
        "src.core.model_extraction_orchestrator.is_word_multi_parallel_enabled",
        lambda _profile: False,
    )
    monkeypatch.setattr(
        "src.core.model_extraction_orchestrator.run_extract_with_slicing",
        lambda **_kwargs: ({"records": [{"x": 1}]}, {"records": [{"x": 1}]}, {"slicing_enabled": True, "slice_count": 1}),
    )
    monkeypatch.setattr(
        "src.core.model_extraction_orchestrator.evaluate_and_retry_required_fields",
        lambda **kwargs: (kwargs["extracted_raw"], [], []),
    )

    extracted_raw, model_output, context_for_llm, retried_fields, runtime_updates = run_model_extraction_path(
        extraction_service=object(),
        profile={"task_mode": "table_records"},
        loaded_bundle={"documents": []},
        context_for_llm="input",
        llm_context_route="full_text",
        effective_llm_mode="full",
        slice_size=3000,
        overlap=200,
        quiet=True,
        max_chunks=20,
        total_start=0.0,
        total_timeout=120,
        source_text_for_order="input",
        logger=__import__("logging").getLogger(__name__),
    )

    assert extracted_raw["records"][0]["x"] == 1
    assert model_output["records"][0]["x"] == 1
    assert context_for_llm == "ctx"
    assert retried_fields == []
    assert runtime_updates["model_inference_seconds"] == 0.0
    assert runtime_updates["scope_resolution"]["mode"] == "table_row_filter"
