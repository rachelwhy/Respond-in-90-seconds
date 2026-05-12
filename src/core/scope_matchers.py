from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from src.core.alias import build_reverse_alias_map, load_alias_map, resolve_field_name
from src.core.scope_models import ConstraintClause, ConstraintSet, FieldConstraint

_YMD_RE = re.compile(r"(\d{4})[\/\-年](\d{1,2})[\/\-月](\d{1,2})")
_DATETIME_HINT_SYNONYMS = (
    "日期",
    "时间",
    "时刻",
    "监测时间",
    "统计日期",
    "date",
    "time",
)
_FIELD_HINT_CANONICALS: Dict[str, Tuple[str, ...]] = {
    "city": ("城市", "市", "地区", "行政区划"),
    "monitor_time": ("监测时间", "时间", "日期"),
    "date": ("日期", "时间", "监测时间"),
}


@lru_cache(maxsize=1)
def _cached_alias_reverse_map() -> Dict[str, str]:
    try:
        alias_map = load_alias_map()
        return build_reverse_alias_map(alias_map)
    except Exception:
        return {}


def coerce_text_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if value is None:
        return []
    s = str(value).strip()
    return [s] if s else []


def parse_ymd_like(value: Any) -> Optional[date]:
    text = str(value or "").strip()
    if not text:
        return None
    m = _YMD_RE.search(text)
    if not m:
        return None
    try:
        y, mm, dd = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return date(y, mm, dd)
    except Exception:
        return None


def parse_date_window_from_constraint(c: FieldConstraint) -> Optional[Tuple[date, date]]:
    if c.op != "date_between_ymd":
        return None
    raw = c.value if isinstance(c.value, dict) else {}
    start = parse_ymd_like(raw.get("start"))
    end = parse_ymd_like(raw.get("end"))
    if not start or not end:
        return None
    return (start, end) if start <= end else (end, start)


def text_matches_constraint(text: str, c: FieldConstraint) -> bool:
    txt = str(text or "")
    if not txt:
        return False
    if c.op == "contains_any":
        tokens = coerce_text_list(c.value)
        if any(token in txt for token in tokens):
            return True
        token_dates = {d for d in (parse_ymd_like(t) for t in tokens) if d is not None}
        if token_dates:
            for d in extract_dates_from_text(txt):
                if d in token_dates:
                    return True
        return False
    if c.op == "date_between_ymd":
        window = parse_date_window_from_constraint(c)
        if not window:
            return False
        for d in extract_dates_from_text(txt):
            if window[0] <= d <= window[1]:
                return True
    return False


def text_matches_clause(text: str, clause: ConstraintClause) -> bool:
    if not clause.constraints:
        return False
    return all(text_matches_constraint(text, c) for c in clause.constraints)


def text_matches_any_clause(text: str, constraint_set: ConstraintSet) -> bool:
    return any(text_matches_clause(text, clause) for clause in constraint_set.clauses)


def extract_dates_from_text(text: str) -> List[date]:
    out: List[date] = []
    for m in _YMD_RE.finditer(str(text or "")):
        try:
            out.append(date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
        except Exception:
            continue
    return out


def row_text(row: Dict[str, Any]) -> str:
    parts: List[str] = []
    for k, v in row.items():
        kk = str(k).strip()
        vv = "" if v is None else str(v).strip()
        if not kk and not vv:
            continue
        parts.append(f"{kk}={vv}" if kk else vv)
    return " | ".join(parts)


def _normalize_hint_candidates(field_hint: str) -> List[str]:
    hint = str(field_hint or "").strip()
    if not hint:
        return []
    candidates = [hint]
    candidates.extend(_FIELD_HINT_CANONICALS.get(hint, ()))
    reverse_alias = _cached_alias_reverse_map()
    if hint in reverse_alias:
        candidates.append(reverse_alias[hint])
    out: List[str] = []
    for c in candidates:
        s = str(c).strip()
        if s and s not in out:
            out.append(s)
    return out


def resolve_candidate_columns(columns: Sequence[str], field_hint: str) -> List[str]:
    """
    基于 field_hint、字段别名与时间字段启发，找到最可能的列名候选。
    """
    cols = [str(c).strip() for c in columns if str(c).strip()]
    if not cols:
        return []
    hint_candidates = _normalize_hint_candidates(field_hint)
    if not hint_candidates:
        return []
    alias_map = load_alias_map()

    matched: List[str] = []
    # pass1: 直接命中（列名或列名 canonical）
    for col in cols:
        canon = resolve_field_name(col, alias_map)
        if col in hint_candidates or canon in hint_candidates:
            if col not in matched:
                matched.append(col)

    # pass2: 时间类字段做子串保守匹配
    if not matched and any(h in hint_candidates for h in _DATETIME_HINT_SYNONYMS):
        for col in cols:
            c = col.lower()
            if any(h.lower() in c for h in _DATETIME_HINT_SYNONYMS):
                if col not in matched:
                    matched.append(col)
    return matched


def row_matches_constraint(
    row: Dict[str, Any],
    constraint: FieldConstraint,
    candidate_columns: Optional[Iterable[str]] = None,
) -> bool:
    """
    行级执行器：列候选上优先类型化比较，未决时用整行文本匹配。
    """
    cols = [c for c in (candidate_columns or []) if c in row]
    if constraint.op == "contains_any":
        tokens = coerce_text_list(constraint.value)
        if not tokens:
            return False
        if cols:
            token_dates = {d for d in (parse_ymd_like(t) for t in tokens) if d is not None}
            for col in cols:
                cell = str(row.get(col) or "")
                if any(t in cell for t in tokens):
                    return True
                if token_dates:
                    cell_date = parse_ymd_like(cell)
                    if cell_date in token_dates:
                        return True
            return False
        return any(t in row_text(row) for t in tokens)

    if constraint.op == "date_between_ymd":
        window = parse_date_window_from_constraint(constraint)
        if not window:
            return False
        if cols:
            for col in cols:
                d = parse_ymd_like(row.get(col))
                if d and window[0] <= d <= window[1]:
                    return True
            return False
        for d in extract_dates_from_text(row_text(row)):
            if window[0] <= d <= window[1]:
                return True
        return False

    return False
