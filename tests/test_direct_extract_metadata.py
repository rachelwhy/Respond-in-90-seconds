"""direct_extract 返回结构（含 metadata.profile）契约测试。"""
from unittest.mock import patch

from src.core.llm_runtime import LlmModeResolution


def test_direct_extract_success_includes_profile_in_metadata():
    from src.api import direct_extractor

    prof = {
        "fields": [{"name": "a"}],
        "template_mode": "excel_table",
        "task_mode": "table_records",
    }
    bundle = {"all_text": "hello", "documents": [], "file_count": 1, "warnings": []}

    with patch.object(direct_extractor, "collect_input_bundle", return_value=bundle):
        with patch.object(direct_extractor, "resolve_profile", return_value=prof):
            with patch.object(
                direct_extractor,
                "resolve_llm_mode_with_readiness",
                return_value=LlmModeResolution(
                    requested="off",
                    normalized="off",
                    effective="off",
                    readiness={"ready": False},
                    fallback_rule_only=False,
                ),
            ):
                with patch.object(direct_extractor, "try_internal_structured_extract", return_value=None):
                    with patch.object(
                        direct_extractor,
                        "run_model_extraction_path",
                        return_value=({"records": [{"a": "1"}]}, {}, "", [], {}),
                    ):
                        with patch.object(
                            direct_extractor,
                            "process_by_profile",
                            return_value={"records": [{"a": "1"}]},
                        ):
                            with patch.object(
                                direct_extractor,
                                "merge_internal_structured_when_model_insufficient",
                                side_effect=lambda **kw: kw["final_data"],
                            ):
                                with patch.object(
                                    direct_extractor,
                                    "reconcile_word_multi_results",
                                    side_effect=lambda **kw: kw["final_data"],
                                ):
                                    with patch.object(
                                        direct_extractor,
                                        "records_from_final_data",
                                        return_value=[{"a": "1"}],
                                    ):
                                        with patch.object(
                                            direct_extractor,
                                            "write_template_outputs_api",
                                            return_value=(None, []),
                                        ):
                                            with patch.object(
                                                direct_extractor, "finalize_runtime_metrics"
                                            ):
                                                out = direct_extractor.direct_extract(
                                                    template_path="x.xlsx",
                                                    input_dir="dummy",
                                                    llm_mode="off",
                                                    work_dir=None,
                                                    quiet=True,
                                                )
    assert "metadata" in out
    assert out["metadata"].get("profile") == prof
    assert isinstance(out.get("records"), list)
