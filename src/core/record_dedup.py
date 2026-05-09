"""统一记录去重工具：候选键管理 + 去重执行。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.core.knowledge_data import load_json_array

# 兜底候选复合键：从 ``src/knowledge/record_dedup_key_fallbacks.json`` 加载（列表的列表），
# 默认可为空；仅人工审核后写入知识库，不在逻辑中硬编码业务列名。
_FALLBACK_CACHE: Optional[List[Tuple[str, ...]]] = None


def _fallback_key_candidates() -> List[Tuple[str, ...]]:
    global _FALLBACK_CACHE
    if _FALLBACK_CACHE is not None:
        return _FALLBACK_CACHE
    raw = load_json_array("record_dedup_key_fallbacks.json")
    out: List[Tuple[str, ...]] = []
    for item in raw:
        if isinstance(item, list) and item:
            tup = tuple(str(x).strip() for x in item if str(x).strip())
            if tup:
                out.append(tup)
    _FALLBACK_CACHE = out
    return _FALLBACK_CACHE

_UNIT_RE = re.compile(
    r"\s*(亿元|万元|千元|百元|元|亿|万|千|百|%|％|‰|万人|千人|人|平方公里|km²|亿美元|万美元|美元)\s*$",
    re.I,
)


def _norm(v: Any) -> str:
    s = _UNIT_RE.sub("", str(v or "").strip())
    s = s.replace(",", "")
    s = "".join(s.split())
    return s.lower()


def _non_empty_field_names(records: Sequence[dict]) -> set:
    names = set()
    for rec in records:
        if not isinstance(rec, dict):
            continue
        for k, v in rec.items():
            if str(k).startswith("_"):
                continue
            if _norm(v):
                names.add(str(k))
    return names


def choose_dedup_fields(
    records: Sequence[dict],
    preferred_fields: Optional[Sequence[str]] = None,
    extra_candidates: Optional[Sequence[Sequence[str]]] = None,
) -> List[str]:
    """从优先字段与统一候选集中选择去重键。"""
    non_empty = _non_empty_field_names(records)
    if preferred_fields:
        pref = [str(x).strip() for x in preferred_fields if str(x).strip()]
        if pref and all(k in non_empty for k in pref):
            return pref

    candidates: List[Tuple[str, ...]] = list(_fallback_key_candidates())
    if extra_candidates:
        candidates = [tuple(str(x).strip() for x in c if str(x).strip()) for c in extra_candidates] + candidates

    for cand in candidates:
        if cand and all(k in non_empty for k in cand):
            return list(cand)

    # 最后兜底：尽量用出现频率高且语义字段感更强的列。
    scored: List[str] = sorted(non_empty, key=lambda k: (len(k), k))
    return scored[:3]


def dedup_records(
    records: Sequence[dict],
    preferred_fields: Optional[Sequence[str]] = None,
    extra_candidates: Optional[Sequence[Sequence[str]]] = None,
) -> Tuple[List[dict], int, List[str]]:
    """执行去重，返回 (去重后记录, 移除数量, 使用的键字段)。"""
    rows = [r for r in records if isinstance(r, dict)]
    if not rows:
        return list(rows), 0, []

    key_fields = choose_dedup_fields(rows, preferred_fields=preferred_fields, extra_candidates=extra_candidates)
    seen = set()
    out: List[dict] = []

    for rec in rows:
        if key_fields:
            key = tuple(_norm(rec.get(k, "")) for k in key_fields)
            if all(not x for x in key):
                # 键全空时退回全记录比对
                norm_map = {str(k): _norm(v) for k, v in rec.items() if not str(k).startswith("_")}
                key = (json.dumps(norm_map, sort_keys=True, ensure_ascii=False),)
        else:
            norm_map = {str(k): _norm(v) for k, v in rec.items() if not str(k).startswith("_")}
            key = (json.dumps(norm_map, sort_keys=True, ensure_ascii=False),)

        if key in seen:
            continue
        seen.add(key)
        out.append(rec)

    return out, len(rows) - len(out), key_fields

