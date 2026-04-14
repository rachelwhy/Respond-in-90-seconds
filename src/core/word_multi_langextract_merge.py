"""
【独立分支 · word_multi_langextract_prefill】

仅负责合并逻辑。路由决策见 ``src.core.extraction_routing``；
``meta.pipeline_routing`` 由 ``extract_with_slicing`` 统一写入。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from src.core.extraction_routing import (
    decide_word_multi_langextract_prefill as word_multi_langextract_prefill_should_run,
    table_specs_homogeneous_columns,
)

logger = logging.getLogger(__name__)


def _row_non_empty_count(rec: dict) -> int:
    if not isinstance(rec, dict):
        return 0
    n = 0
    for k, v in rec.items():
        if str(k).startswith("_"):
            continue
        if v is None:
            continue
        if str(v).strip():
            n += 1
    return n


def _field_names_from_spec(spec: dict) -> List[str]:
    tp = spec.get("table_profile") or {}
    fields = tp.get("fields") or []
    out: List[str] = []
    for f in fields:
        if isinstance(f, dict) and f.get("name"):
            out.append(str(f["name"]))
    return out


def _pool_non_empty_values(matched: List[dict], field_names: List[str]) -> Dict[str, Any]:
    """多行合并为单行池：同一字段优先保留已有非空。"""
    pool: Dict[str, Any] = {}
    for r in matched:
        if not isinstance(r, dict):
            continue
        for fn in field_names:
            v = r.get(fn)
            if v is None or not str(v).strip():
                continue
            if fn not in pool or not str(pool.get(fn, "")).strip():
                pool[fn] = v
    return pool


def _fill_row_from_pool(row: dict, pool: Dict[str, Any], field_names: List[str]) -> dict:
    out = dict(row) if isinstance(row, dict) else {}
    for fn in field_names:
        cur = out.get(fn)
        if cur is not None and str(cur).strip():
            continue
        if fn in pool:
            pv = pool[fn]
            if pv is not None and str(pv).strip():
                out[fn] = pv
    return out


def merge_langextract_into_word_multi_groups(
    final_data: Dict[str, Any],
    profile: Dict[str, Any],
    lx_records: List[dict],
) -> Dict[str, Any]:
    """【word_multi_langextract_prefill 专用】将 LangExtract 扁平 records 按 filter 并入 _table_groups。"""
    if profile.get("template_mode") != "word_multi_table":
        return final_data
    if not isinstance(final_data, dict):
        return final_data
    groups = final_data.get("_table_groups")
    if not isinstance(groups, list) or not groups:
        return final_data
    if not lx_records:
        return final_data

    src = [r for r in lx_records if isinstance(r, dict)]
    if not src:
        return final_data

    specs = profile.get("table_specs") or []
    new_groups: List[Dict[str, Any]] = []

    for g in groups:
        if not isinstance(g, dict):
            new_groups.append(g)
            continue
        tid = int(g.get("table_index", 0))
        spec = next(
            (s for s in specs if isinstance(s, dict) and int(s.get("table_index", -1)) == tid),
            {},
        ) or {}
        ff = (spec.get("filter_field") or "").strip()
        fv = (spec.get("filter_value") or "").strip()
        field_names = _field_names_from_spec(spec)
        cur = list(g.get("records") or [])

        matched: List[dict] = []
        if ff and fv:
            matched = [r for r in src if fv in str(r.get(ff, ""))]
        elif not ff:
            matched = list(src)

        if not matched:
            new_groups.append(g)
            continue

        if not field_names:
            seen = set()
            for r in matched:
                if not isinstance(r, dict):
                    continue
                for k in r:
                    if str(k).startswith("_"):
                        continue
                    seen.add(k)
            field_names = sorted(seen)

        need_replace = (not cur) or all(
            _row_non_empty_count(row) <= 1 for row in cur if isinstance(row, dict)
        )
        if need_replace:
            gg = dict(g)
            gg["records"] = matched
            new_groups.append(gg)
            continue

        pool = _pool_non_empty_values(matched, field_names)
        new_recs = [_fill_row_from_pool(row, pool, field_names) for row in cur if isinstance(row, dict)]
        gg = dict(g)
        gg["records"] = new_recs
        new_groups.append(gg)

    out = dict(final_data)
    out["_table_groups"] = new_groups
    flat: List[dict] = []
    for gg in new_groups:
        for r in gg.get("records") or []:
            if isinstance(r, dict):
                flat.append(r)
    out["records"] = flat
    logger.info("word_multi_langextract_merge: 合并后总记录 %s 条", len(flat))
    return out
