"""组装 API/CLI 返回体：生成产物路径声明、运行摘要与调试包字段。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.core.postprocess import build_debug_result, build_run_summary


def build_generated_outputs(
    *,
    template_mode: str,
    output_json: str,
    output_xlsx: str,
    output_docx: str,
) -> Dict[str, str]:
    return {
        "result_json": output_json,
        "result_xlsx": output_xlsx if template_mode in ["vertical", "excel_table"] else "",
        "result_docx": output_docx if template_mode in ("word_table", "word_multi_table") else "",
    }


def build_cli_report_bundle(
    *,
    final_data: Any,
    extracted_raw: Any,
    profile: Dict[str, Any],
    runtime: Dict[str, Any],
    missing_required_fields: List[Any],
    retried_fields: List[Any],
    input_text: str,
    profile_path: str,
    template_mode: str,
    output_json: str,
    output_xlsx: str,
    output_docx: str,
    rag_json_path: str,
    retrieved_chunks: List[Any],
    prefer_rag_structured: bool,
    structured_rag_result: Optional[Any],
    internal_route_used: str,
    persist_profiles: bool,
    field_evidence: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    debug_result = build_debug_result(final_data if isinstance(final_data, dict) else extracted_raw, profile)
    run_summary = build_run_summary(
        profile=profile,
        runtime=runtime,
        missing_fields=missing_required_fields,
        retried_fields=retried_fields,
        input_text=input_text,
    )
    retrieval_info = {
        "rag_json_provided": bool((rag_json_path or "").strip()),
        "rag_json_path": rag_json_path,
        "chunks_count": len(retrieved_chunks),
        "chunks_preview": retrieved_chunks[:3] if retrieved_chunks else [],
        "used_structured_rag_result": bool(prefer_rag_structured and structured_rag_result),
        "internal_route_used": internal_route_used,
    }
    return {
        "meta": {
            "report_type": "integrated_output_bundle",
            "profile_path": profile_path if (persist_profiles and profile_path) else "",
            "profile_name": profile.get("report_name", ""),
            "template_path": profile.get("template_path", ""),
            "task_mode": profile.get("task_mode", "single_record"),
            "template_mode": template_mode,
            "input_char_count": len(input_text),
            "generated_outputs": build_generated_outputs(
                template_mode=template_mode,
                output_json=output_json,
                output_xlsx=output_xlsx,
                output_docx=output_docx,
            ),
        },
        "run_summary": run_summary,
        "runtime_metrics": runtime,
        "debug_result": debug_result,
        "retrieval": retrieval_info,
        "field_evidence": dict(field_evidence or {}),
    }


def build_api_metadata(
    *,
    file_count: int,
    records: List[Any],
    profile: Dict[str, Any],
    doc_type: str,
    runtime_updates: Optional[Dict[str, Any]],
    llm_mode_requested: str,
    llm_mode_normalized: str,
    llm_mode_effective: str,
    readiness: Dict[str, Any],
    internal_route_used: str,
    retried_fields: Optional[List[Any]],
    final_data: Any,
    model_output: Optional[Dict[str, Any]] = None,
    output_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    runtime_updates = runtime_updates or {}
    output_files = output_files or []
    meta: Dict[str, Any] = {
        "file_count": int(file_count or 0),
        "record_count": len(records or []),
        "template_mode": profile.get("template_mode", "unknown"),
        "task_mode": profile.get("task_mode", "unknown"),
        "doc_type": doc_type,
        "profile_auto_generated": bool(profile.get("_doc_type")),
        "word_multi_parallel": bool(
            isinstance(runtime_updates.get("slicing_metadata"), dict)
            and runtime_updates["slicing_metadata"].get("word_multi_parallel")
        ),
        "llm_mode_requested": llm_mode_requested,
        "llm_mode_normalized": llm_mode_normalized,
        "llm_mode_effective": llm_mode_effective,
        "model_ready": bool(readiness.get("ready")),
        "model_ready_reason": str(readiness.get("reason") or ""),
        "internal_route_used": internal_route_used,
        "retried_fields": list(retried_fields or []),
    }
    if runtime_updates.get("scope_resolution") is not None:
        meta["scope_resolution"] = runtime_updates.get("scope_resolution")
    if runtime_updates.get("slicing_metadata") is not None:
        meta["slicing_metadata"] = runtime_updates.get("slicing_metadata")
    if runtime_updates:
        meta["runtime_metrics"] = dict(runtime_updates)
    if output_files:
        meta["output_file_count"] = len(output_files)
    if isinstance(final_data, dict) and final_data.get("_table_groups"):
        meta["_table_groups"] = final_data.get("_table_groups")
    if model_output:
        model_records = model_output.get("records", []) if isinstance(model_output, dict) else []
        meta["model_output_preview"] = {"record_count": len(model_records)}
    meta["profile"] = profile
    return meta


def build_api_result(
    *,
    records: List[Any],
    metadata: Dict[str, Any],
    output_files: Optional[List[str]] = None,
    output_file: Optional[str] = None,
) -> Dict[str, Any]:
    output_files = list(output_files or [])
    if output_file is None and output_files:
        output_file = output_files[0]
    return {
        "records": list(records or []),
        "metadata": metadata,
        "output_file": output_file,
        "output_files": output_files,
    }
