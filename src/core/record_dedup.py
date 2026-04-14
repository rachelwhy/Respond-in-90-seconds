"""统一记录去重工具：候选键管理 + 去重执行。"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple


# 统一兜底候选键（从强约束到弱约束），可覆盖多行业表格记录。
FALLBACK_KEY_CANDIDATES: List[Tuple[str, ...]] = [
    ("省", "市", "区", "站点名称", "监测时间"),
    ("城市", "区", "站点名称", "监测时间"),
    ("城市", "区", "站点名称"),
    ("城市", "站点名称"),
    ("地区", "区", "站点名称"),
    ("省", "市", "区", "名称"),
    ("省", "市", "名称"),
    ("城市", "名称"),
    ("地区", "名称"),
    ("单位", "名称"),
    ("公司", "名称"),
    ("项目", "名称"),
    ("id",),
    ("ID",),
    ("编号",),
    ("编码",),
    ("code",),
    ("city", "district", "site_name", "monitor_time"),
    ("city", "district", "site_name"),
    ("city", "site_name"),
    ("province", "city", "name"),
    ("name", "id"),
    ("name",),
]

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

    candidates: List[Tuple[str, ...]] = list(FALLBACK_KEY_CANDIDATES)
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

