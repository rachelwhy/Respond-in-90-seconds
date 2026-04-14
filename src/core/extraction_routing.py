"""
抽取路由（单一事实来源）

**职责边界（仅判断，不执行）**

- 根据 profile、bundle、chunks、环境变量做 **处理方式决策** 与 **pipeline_routing 摘要**。
- **不** 调用模型、LangExtract、HTTP；**不** 读写磁盘；**不** 修改 records / _table_groups（合并见执行层）。

从「输入文件能力 → 模板类型 → 拟采用的处理链」生成 **pipeline_routing**，供 meta 观测与排障。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── 稳定标识（勿随意改字符串，下游可依赖）──────────────────────────────────
ROUTING_SCHEMA_VERSION = 1

TRACK_WORD_MULTI_PARALLEL = "word_multi_parallel"
TRACK_SEMANTIC_SLICING = "semantic_slicing_stack"
TRACK_CHAR_OR_DIRECT = "char_slice_or_direct"
TRACK_MODEL_DISABLED = "model_disabled"

_TABULAR = frozenset({".xlsx", ".xls", ".xlsm", ".csv", ".ods"})
_TEXT = frozenset({".txt"})
_OFFICE = frozenset({".docx", ".doc", ".pdf", ".pptx", ".ppt", ".html", ".htm", ".epub"})


def _norm_profile(profile: Any) -> Dict[str, Any]:
    return profile if isinstance(profile, dict) else {}


def is_word_multi_parallel_enabled(profile: Any) -> bool:
    """是否走「多表 Word 并行 LLM」路径（纯策略，与 CoreExtractionService 内原逻辑一致）。

    依据：环境 ``A23_WORD_MULTI_PARALLEL``、``template_mode``、``word_multi_parallel``。
    """
    prof = _norm_profile(profile)
    v = os.environ.get("A23_WORD_MULTI_PARALLEL", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if prof.get("template_mode") != "word_multi_table":
        return False
    # 同标头多表优先走“统一抽取+后分表”，避免各表在同质上下文中并行抽取造成串表。
    if table_specs_homogeneous_columns(prof):
        return False
    return bool(prof.get("word_multi_parallel", True))


def _safe_text_chunks(chunks: Any, max_chunks: int) -> List[Dict[str, Any]]:
    if not chunks or not isinstance(chunks, list):
        return []
    out: List[Dict[str, Any]] = []
    for c in chunks:
        if not isinstance(c, dict):
            continue
        if c.get("type") == "table":
            continue
        out.append(c)
    if max_chunks and len(out) > max_chunks:
        out = out[: max(0, int(max_chunks))]
    return out


def _field_signature_for_spec(spec: dict) -> Optional[Tuple[str, ...]]:
    from src.core.alias import resolve_field_names

    if not isinstance(spec, dict):
        return None
    raw = spec.get("field_names") or []
    if raw:
        try:
            resolved = resolve_field_names(list(raw))
            return tuple(sorted(resolved))
        except Exception:
            return None
    tp = spec.get("table_profile") or {}
    fields = tp.get("fields") or []
    names = [str(f.get("name", "")).strip() for f in fields if isinstance(f, dict) and f.get("name")]
    if not names:
        return None
    return tuple(sorted(names))


def table_specs_homogeneous_columns(profile: Dict[str, Any]) -> bool:
    specs = profile.get("table_specs") or []
    if not isinstance(specs, list) or len(specs) < 1:
        return False
    sigs: List[Tuple[str, ...]] = []
    for s in specs:
        if not isinstance(s, dict):
            return False
        sig = _field_signature_for_spec(s)
        if not sig:
            return False
        sigs.append(sig)
    return len(set(sigs)) == 1


def decide_word_multi_langextract_prefill(
    profile: Dict[str, Any],
    chunks: Any,
    max_chunks: int,
) -> Tuple[bool, str]:
    """多表 Word 并行之后是否再跑 LangExtract 补缺（与通用分块 LangExtract 无关）。"""
    prof = _norm_profile(profile)
    text_chunks = _safe_text_chunks(chunks, max_chunks)

    env = os.environ.get("A23_WORD_MULTI_LANGEXTRACT", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False, "env_off"
    if env in ("1", "true", "yes", "on"):
        if not text_chunks:
            return False, "env_on_no_text_chunks"
        return True, "env_on"
    if prof.get("template_mode") != "word_multi_table":
        return False, "auto_not_word_multi"
    if not text_chunks:
        return False, "auto_no_text_chunks"
    if not table_specs_homogeneous_columns(prof):
        return False, "auto_heterogeneous_tables"
    return True, "auto_homogeneous_tables"


def _input_kind_from_suffixes(suffixes: Tuple[str, ...]) -> str:
    if not suffixes:
        return "unknown"
    s = set(suffixes)
    if len(s) == 1:
        x = next(iter(s))
        if x in _TABULAR:
            return "tabular"
        if x in _TEXT:
            return "text"
        if x in _OFFICE:
            return "office"
    if s <= _TABULAR:
        return "tabular"
    if s <= _TEXT:
        return "text"
    if s & _TABULAR and s & _OFFICE:
        return "mixed"
    if s & _OFFICE or s & _TEXT:
        return "mixed"
    return "mixed"


def summarize_input_side(
    routing_bundle: Optional[Dict[str, Any]],
    chunks: Any,
    max_chunks: int,
) -> Dict[str, Any]:
    """输入侧能力摘要（防御性：缺字段不抛）。"""
    suffixes: List[str] = []
    has_structured_tables = False
    bundle_has_doc_chunks = False

    docs = (routing_bundle or {}).get("documents")
    if isinstance(docs, list):
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            p = doc.get("path")
            if p:
                try:
                    suffixes.append(Path(str(p)).suffix.lower())
                except Exception:
                    pass
            dfs = doc.get("tables_dataframes")
            tr = doc.get("tables")
            if (isinstance(dfs, list) and len(dfs) > 0) or (isinstance(tr, list) and len(tr) > 0):
                has_structured_tables = True
            ch = doc.get("chunks")
            if isinstance(ch, list) and len(ch) > 0:
                bundle_has_doc_chunks = True

    uniq = tuple(sorted(set(suffixes)))
    text_chunks = _safe_text_chunks(chunks, max_chunks)
    has_text_chunks = bool(text_chunks)
    return {
        "suffixes": list(uniq),
        "input_kind": _input_kind_from_suffixes(uniq),
        "has_text_chunks": has_text_chunks,
        "has_bundle_document_chunks": bundle_has_doc_chunks,
        "has_structured_tables": has_structured_tables,
    }


def post_internal_merge_enabled(template_mode: str) -> bool:
    if template_mode != "word_multi_table":
        return False
    return os.environ.get("A23_WORD_MULTI_MERGE_INTERNAL", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def build_pipeline_routing_meta(
    profile: Any,
    routing_bundle: Optional[Dict[str, Any]],
    chunks: Any,
    max_chunks: int,
    *,
    parallel_word_multi_enabled: Optional[bool] = None,
    use_model: bool = True,
) -> Dict[str, Any]:
    """
    生成写入 meta['pipeline_routing'] 的字典（可 JSON 序列化）。

    parallel_word_multi_enabled: 默认 ``None`` 时由 ``is_word_multi_parallel_enabled(profile)`` 计算。
    """
    prof = _norm_profile(profile)
    pwm = parallel_word_multi_enabled
    if pwm is None:
        pwm = is_word_multi_parallel_enabled(prof)
    tm = str(prof.get("template_mode") or "unknown")
    mc = int(max_chunks) if max_chunks else 0

    inp = summarize_input_side(routing_bundle, chunks, mc)
    lx_ok, lx_reason = decide_word_multi_langextract_prefill(prof, chunks, mc)
    internal_post = post_internal_merge_enabled(tm)

    if not use_model:
        primary = TRACK_MODEL_DISABLED
        stages = ["skip_llm"]
    elif pwm:
        primary = TRACK_WORD_MULTI_PARALLEL
        stages = ["parse_inputs", "parallel_word_table_llm"]
        if lx_ok:
            stages.append("word_multi_langextract_prefill_merge")
        else:
            stages.append(f"skip_word_multi_lx_prefill:{lx_reason}")
    else:
        if inp["has_text_chunks"] or _safe_text_chunks(chunks, mc):
            primary = TRACK_SEMANTIC_SLICING
            stages = ["parse_inputs", "semantic_chunks", "langextract_or_prompt"]
        else:
            primary = TRACK_CHAR_OR_DIRECT
            stages = ["parse_inputs", "char_slice_or_direct_prompt"]

    if use_model and primary not in (TRACK_MODEL_DISABLED,):
        stages.append("process_by_profile")
        if internal_post and tm == "word_multi_table":
            stages.append("internal_structured_merge")
        stages.append("write_template")

    return {
        "schema_version": ROUTING_SCHEMA_VERSION,
        "template_mode": tm,
        "primary_track": primary,
        "use_model": bool(use_model),
        "parallel_word_tables": bool(pwm),
        "word_multi_langextract_prefill": bool(lx_ok),
        "langextract_prefill_reason": lx_reason,
        "post_internal_table_merge": bool(internal_post),
        "input": inp,
        "stages": stages,
    }