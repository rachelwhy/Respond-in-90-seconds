"""
多表 Word：当并行 LLM 结果为空或指标多为空时，用 Docling/Excel 表格直读结果补全各 _table_groups。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, List

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


def _infer_group_filter(spec: Dict[str, Any]) -> tuple[str, str]:
    """从 table spec 推断分组条件，优先使用显式配置，其次从表上方说明提取城市。"""
    ff = (spec.get("filter_field") or "").strip()
    fv = (spec.get("filter_value") or "").strip()
    if ff and fv:
        return ff, fv

    text = "\n".join(
        str(spec.get(k, "") or "")
        for k in ("instruction_above", "description", "builtin_prompt")
    )
    m = re.search(r"([\u4e00-\u9fa5]{2,10}市)", text)
    if m:
        return "城市", m.group(1)
    return "", ""


def _apply_fixed_values(records: List[dict], fixed_values: Dict[str, Any]) -> List[dict]:
    if not fixed_values:
        return records
    out: List[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rr = dict(rec)
        for k, v in fixed_values.items():
            if str(v).strip():
                rr[k] = v
        out.append(rr)
    return out


def merge_internal_structured_into_word_multi_groups(
    final_data: Dict[str, Any],
    profile: Dict[str, Any],
    bundle: Dict[str, Any],
) -> Dict[str, Any]:
    """若为多表 Word 且存在 _table_groups，尝试用 try_internal_structured_extract 补全各表记录。"""
    if profile.get("template_mode") != "word_multi_table":
        return final_data
    if os.environ.get("A23_WORD_MULTI_MERGE_INTERNAL", "1").strip().lower() in ("0", "false", "no"):
        return final_data

    groups = final_data.get("_table_groups")
    if not isinstance(groups, list) or not groups:
        return final_data

    from src.core.reader import try_internal_structured_extract

    try:
        inc = try_internal_structured_extract(profile, bundle)
    except Exception as e:
        logger.info("word_multi_internal_merge: 内部表抽取跳过: %s", e)
        return final_data

    if not isinstance(inc, dict) or not inc.get("records"):
        return final_data

    src_records: List[dict] = [r for r in inc["records"] if isinstance(r, dict)]
    if not src_records:
        return final_data

    specs = profile.get("table_specs") or []
    new_groups: List[Dict[str, Any]] = []

    for g in groups:
        if not isinstance(g, dict):
            new_groups.append(g)
            continue
        tid = int(g.get("table_index", 0))
        spec = next((s for s in specs if isinstance(s, dict) and int(s.get("table_index", -1)) == tid), {}) or {}
        ff, fv = _infer_group_filter(spec)
        fixed_values = dict(spec.get("fixed_values") or {})
        cur = list(g.get("records") or [])

        matched: List[dict] = []
        if ff and fv:
            matched = [r for r in src_records if fv in str(r.get(ff, ""))]

        if not matched:
            # 无匹配时给最小占位，避免整表空白；仅填固定约束字段。
            if (not cur) and fixed_values:
                gg = dict(g)
                gg["records"] = [dict(fixed_values)]
                new_groups.append(gg)
            else:
                new_groups.append(g)
            continue

        # LLM 无行、或每行有效字段≤1（通常只有城市）→ 用内部表数据替换
        need_replace = (not cur) or all(_row_non_empty_count(row) <= 1 for row in cur if isinstance(row, dict))
        if need_replace:
            gg = dict(g)
            gg["records"] = _apply_fixed_values(matched, fixed_values)
            new_groups.append(gg)
        else:
            gg = dict(g)
            gg["records"] = _apply_fixed_values(cur, fixed_values)
            new_groups.append(gg)

    out = dict(final_data)
    out["_table_groups"] = new_groups

    flat: List[dict] = []
    for gg in new_groups:
        for r in gg.get("records") or []:
            if isinstance(r, dict):
                flat.append(r)
    out["records"] = flat
    logger.info("word_multi_internal_merge: 合并后总记录 %s 条", len(flat))
    return out
