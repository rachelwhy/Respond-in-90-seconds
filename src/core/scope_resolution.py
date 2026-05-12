"""
非 LLM 的读入范围收敛：把 instruction 编译为通用约束，再用统一执行器筛选语义块。

- 不修改 extract_with_slicing / model_client；由入口层传入已选块与已拼接 context。
- RAG 片段模式（正文并非 all_text）不在此模块处理，由调用方跳过本模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Tuple

from src.core.instruction_filters import (
    compile_instruction_constraints,
)
from src.core.reader import collect_semantic_chunks_from_bundle
from src.core.scope_matchers import (
    extract_dates_from_text,
    parse_date_window_from_constraint,
    resolve_candidate_columns,
    row_matches_constraint,
    row_text,
    text_matches_any_clause,
)
from src.core.scope_models import ConstraintSet

try:
    import pandas as pd  # type: ignore

    _PANDAS_AVAILABLE = True
except Exception:
    pd = None  # type: ignore
    _PANDAS_AVAILABLE = False

# 日期区间路径里：版式块不参与「正文中日期命中」判断，但始终保留。
_STRUCTURAL_TYPES = frozenset({"table", "formula", "code"})
# 命中块两侧各扩一块，降低截断在边界上的漏抽。
_NEIGHBOR_RADIUS = 1


def _chunk_type(ch: Dict[str, Any]) -> str:
    return str(ch.get("type") or "text").strip().lower()


def _dates_in_window(text: str, start: date, end: date) -> bool:
    for d in extract_dates_from_text(text):
        if start <= d <= end:
            return True
    return False


def _collect_dataframes(bundle: Dict[str, Any]) -> List["pd.DataFrame"]:
    """从 bundle.documents 收集 Docling 表格 DataFrame（若可用）。"""
    if not _PANDAS_AVAILABLE:
        return []
    out: List["pd.DataFrame"] = []
    docs = bundle.get("documents") or []
    if not isinstance(docs, list):
        return []
    for d in docs:
        if not isinstance(d, dict):
            continue
        dfs = d.get("tables_dataframes")
        if isinstance(dfs, list):
            for df in dfs:
                if df is None:
                    continue
                # 防御性：仅接收 DataFrame-like
                if hasattr(df, "to_dict") and hasattr(df, "columns"):
                    out.append(df)
    return out


def _filter_dataframe_rows_by_constraint_set(
    df: "pd.DataFrame",
    constraint_set: ConstraintSet,
    *,
    max_rows: int = 60,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    通用表格初筛：不要求固定列名；把行转文本后按 constraint_set 执行匹配。
    返回被选中的行 dict（最多 max_rows）与匹配元数据。
    """
    try:
        rows = df.to_dict(orient="records")
    except Exception:
        return [], {"row_count": 0, "match_mode": "invalid_dataframe"}

    columns = [str(c) for c in getattr(df, "columns", [])]
    hint_candidates: Dict[str, List[str]] = {}
    for clause in constraint_set.clauses:
        for c in clause.constraints:
            if c.field_hint in hint_candidates:
                continue
            hint_candidates[c.field_hint] = resolve_candidate_columns(columns, c.field_hint)

    selected: List[Dict[str, Any]] = []
    matched_by_columns = 0
    matched_by_fallback_text = 0
    for r in rows:
        if not isinstance(r, dict):
            continue
        row_hit = False
        via_columns = True
        for clause in constraint_set.clauses:
            clause_hit = True
            clause_via_columns = True
            for c in clause.constraints:
                cols = hint_candidates.get(c.field_hint, [])
                if not cols:
                    clause_via_columns = False
                if not row_matches_constraint(r, c, candidate_columns=cols):
                    clause_hit = False
                    break
            if clause_hit:
                row_hit = True
                via_columns = clause_via_columns
                break

        if not row_hit and text_matches_any_clause(row_text(r), constraint_set):
            row_hit = True
            via_columns = False

        if row_hit:
            selected.append(r)
            if via_columns:
                matched_by_columns += 1
            else:
                matched_by_fallback_text += 1
            if len(selected) >= max_rows:
                break
    return selected, {
        "row_count": len(rows),
        "match_mode": "typed_columns_with_text_fallback",
        "field_hint_candidates": {k: v[:6] for k, v in hint_candidates.items()},
        "matched_rows_via_columns": matched_by_columns,
        "matched_rows_via_row_text": matched_by_fallback_text,
    }


def _scoped_table_chunks_for_constraint_set(
    bundle: Dict[str, Any],
    constraint_set: ConstraintSet,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """优先对 DataFrame 做行级裁剪；命中则返回极小的 text chunks，避免 LLM 读入全文。"""
    dfs = _collect_dataframes(bundle)
    meta: Dict[str, Any] = {
        "mode": "table_row_filter",
        "predicate": f"instruction_{constraint_set.predicate}",
        "constraint_compiler": constraint_set.compiler,
        "clause_count": len(constraint_set.clauses),
        "dataframe_count": len(dfs),
    }
    if constraint_set.metadata:
        meta["constraints_meta"] = dict(constraint_set.metadata)
    if not dfs:
        meta["mode"] = "table_row_filter_degraded"
        meta["reason"] = "no_tables_dataframes"
        meta["warnings"] = ["指令约束可解析，但未解析到可用表格结构，改用语义块筛选"]
        return [], meta

    chunks: List[Dict[str, Any]] = []
    total_rows = 0
    table_match_meta: List[Dict[str, Any]] = []
    for ti, df in enumerate(dfs, start=1):
        rows, match_meta = _filter_dataframe_rows_by_constraint_set(df, constraint_set)
        table_match_meta.append({"table_index": ti, **match_meta})
        if not rows:
            continue
        total_rows += len(rows)
        try:
            slim = pd.DataFrame(rows)  # type: ignore[misc]
            md = slim.to_markdown(index=False)
        except Exception:
            md = "\n".join(row_text(r) for r in rows)
        chunks.append(
            {
                "type": "text",
                "text": f"[scoped_table:{ti}] {len(rows)} rows\n{md}",
            }
        )

    if not chunks:
        meta["mode"] = "table_row_filter_degraded"
        meta["reason"] = "no_rows_match_constraints"
        meta["warnings"] = ["表格行级初筛未命中任何行，改用语义块筛选"]
        meta["table_match_meta"] = table_match_meta
        return [], meta

    meta["selected_chunk_count"] = len(chunks)
    meta["selected_row_count"] = total_rows
    meta["table_match_meta"] = table_match_meta
    return chunks, meta


def _select_by_constraint_set(
    chunks: List[Dict[str, Any]],
    constraint_set: ConstraintSet,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按通用约束选块；支持 OR(clauses) + AND(constraints)。"""
    n = len(chunks)
    meta: Dict[str, Any] = {
        "mode": "constraint_clauses",
        "predicate": f"instruction_{constraint_set.predicate}",
        "constraint_compiler": constraint_set.compiler,
        "clause_count": len(constraint_set.clauses),
    }
    if constraint_set.metadata:
        meta["constraints_meta"] = dict(constraint_set.metadata)
    hit: set[int] = set()
    for i, ch in enumerate(chunks):
        txt = str(ch.get("text") or "")
        if text_matches_any_clause(txt, constraint_set):
            hit.add(i)

    if not hit:
        meta["mode"] = "constraint_clauses_degraded"
        meta["reason"] = "no_chunk_matches_constraints"
        meta["warnings"] = ["语义块筛选未命中约束，保留全部语义块"]
        return list(chunks), meta

    expanded: set[int] = set(hit)
    for i in list(hit):
        for j in range(i - _NEIGHBOR_RADIUS, i + _NEIGHBOR_RADIUS + 1):
            if 0 <= j < n:
                expanded.add(j)

    ordered = sorted(expanded)
    out = [chunks[i] for i in ordered]
    meta["selected_chunk_count"] = len(out)
    meta["base_chunk_count"] = n
    return out, meta


def _select_by_date_window(
    chunks: List[Dict[str, Any]],
    start: date,
    end: date,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """按指令日期窗口选块；无法满足时退回全量并带 warnings。"""
    n = len(chunks)
    meta: Dict[str, Any] = {
        "mode": "date_window",
        "predicate": "instruction_date_range",
        "window": [start.isoformat(), end.isoformat()],
    }
    in_window_indices: set[int] = set()
    structural: set[int] = set()
    any_text_date = False

    for i, ch in enumerate(chunks):
        typ = _chunk_type(ch)
        txt = str(ch.get("text") or "")
        if typ in _STRUCTURAL_TYPES:
            structural.add(i)
            in_window_indices.add(i)
            continue
        dates = extract_dates_from_text(txt)
        if dates:
            any_text_date = True
        if _dates_in_window(txt, start, end):
            in_window_indices.add(i)

    if not any_text_date:
        meta["mode"] = "date_window_degraded"
        meta["reason"] = "no_parseable_ymd_in_text_chunks"
        meta["warnings"] = ["指令含日期区间，但正文块中未解析到日期，保留全部语义块"]
        return list(chunks), meta

    text_hits = {i for i in in_window_indices if i not in structural}
    if not text_hits:
        meta["mode"] = "date_window_degraded"
        meta["reason"] = "no_text_chunk_in_date_window"
        meta["warnings"] = ["指令日期区间内无正文块命中，保留全部语义块"]
        return list(chunks), meta

    expanded: set[int] = set(in_window_indices)
    for i in list(text_hits):
        for j in range(i - _NEIGHBOR_RADIUS, i + _NEIGHBOR_RADIUS + 1):
            if 0 <= j < n:
                expanded.add(j)

    ordered = sorted(expanded)
    out = [chunks[i] for i in ordered]
    meta["selected_chunk_count"] = len(out)
    meta["base_chunk_count"] = n
    return out, meta


def resolve_semantic_chunks_with_meta(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    单一事实来源：根据 bundle 与 profile.instruction 得到语义块列表与 scope 元数据。
    无日期谓词或降级时块列表与 ``collect_semantic_chunks_from_bundle`` 一致。
    """
    base = collect_semantic_chunks_from_bundle(bundle)
    instr = str((profile or {}).get("instruction") or "").strip()
    if not base:
        return [], {"mode": "passthrough", "reason": "no_semantic_chunks"}
    if not instr:
        return base, {"mode": "passthrough", "reason": "no_instruction", "base_chunk_count": len(base)}

    constraint_set = compile_instruction_constraints(instr)
    if constraint_set is None:
        return base, {"mode": "passthrough", "reason": "no_scope_predicate", "base_chunk_count": len(base)}

    if constraint_set.predicate == "monitor_city_time_blocks":
        # 1) 表格优先：先行级裁剪（更稳、更省 token）
        scoped_table_chunks, scoped_table_meta = _scoped_table_chunks_for_constraint_set(bundle, constraint_set)
        if scoped_table_chunks:
            scoped_table_meta.setdefault("base_chunk_count", len(base))
            return scoped_table_chunks, scoped_table_meta

        # 2) 无表或表未命中：在语义块上做通用约束匹配
        selected, meta = _select_by_constraint_set(base, constraint_set)
        if meta.get("mode") == "constraint_clauses_degraded":
            return base, meta
        meta.setdefault("base_chunk_count", len(base))
        return selected, meta

    if constraint_set.predicate == "date_range":
        if not constraint_set.clauses or not constraint_set.clauses[0].constraints:
            return base, {"mode": "passthrough", "reason": "invalid_date_constraint", "base_chunk_count": len(base)}
        dr = parse_date_window_from_constraint(constraint_set.clauses[0].constraints[0])
        if dr is None:
            return base, {"mode": "passthrough", "reason": "invalid_date_constraint", "base_chunk_count": len(base)}
        selected, meta = _select_by_date_window(base, dr[0], dr[1])
        meta["constraint_compiler"] = constraint_set.compiler
        if constraint_set.metadata:
            meta["constraints_meta"] = dict(constraint_set.metadata)
        if meta.get("mode") == "date_window_degraded":
            return base, meta
        meta.setdefault("base_chunk_count", len(base))
        return selected, meta

    return base, {"mode": "passthrough", "reason": "unsupported_constraint_predicate", "base_chunk_count": len(base)}


def select_semantic_chunks_for_profile(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """供 ``ensure_chunks`` 使用：仅返回块列表。"""
    chunks, _ = resolve_semantic_chunks_with_meta(bundle, profile)
    return chunks


@dataclass
class MainLLMInputs:
    """CLI main 路径：进入 extract_with_slicing 的字符串与块列表。"""

    context_text: str
    semantic_chunks: List[Dict[str, Any]]
    scope_meta: Dict[str, Any] = field(default_factory=dict)


def _join_chunks_text(chunks: List[Dict[str, Any]], max_chars: int) -> str:
    joined = "\n\n".join(str(c.get("text") or "").strip() for c in chunks if c.get("text"))
    if len(joined) <= max_chars:
        return joined
    return joined[:max_chars]


def prepare_main_llm_inputs(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    max_context_chars: int,
) -> MainLLMInputs:
    """
    为 main 的「全文抽取」路径准备 context 与语义块（非 RAG 片段模式）。

    - 无可用语义块：退回 ``all_text`` 前缀截断（与历史行为一致）。
    - 无可用谓词：拼接全部语义块正文再截断；块列表为全集。
    - 有监测时间+城市块或日期区间：先经 ``resolve_semantic_chunks_with_meta`` 选块，再拼接截断。
    """
    chunks, meta = resolve_semantic_chunks_with_meta(bundle, profile)
    if not chunks:
        raw = str(bundle.get("all_text") or "")[:max_context_chars]
        meta_out = dict(meta)
        meta_out["truncated_chars"] = max_context_chars
        return MainLLMInputs(context_text=raw, semantic_chunks=[], scope_meta=meta_out)

    return MainLLMInputs(
        context_text=_join_chunks_text(chunks, max_context_chars),
        semantic_chunks=chunks,
        scope_meta=dict(meta),
    )
