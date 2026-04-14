from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Sequence, Tuple


_DATE_RANGE_RE = re.compile(
    r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*(?:到|至|~|～|-|—|–)\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})"
)
_DATE_VALUE_RE = re.compile(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})")
_DATE_FIELD_CANDIDATES = (
    "日期",
    "统计日期",
    "监测时间",
    "时间",
    "date",
    "Date",
    "DATE",
)


def _parse_ymd(text: str) -> Optional[date]:
    s = str(text or "").strip().replace("/", "-")
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def parse_date_range_from_instruction(instruction: str) -> Optional[Tuple[date, date]]:
    text = str(instruction or "").strip()
    if not text:
        return None
    m = _DATE_RANGE_RE.search(text)
    if not m:
        return None
    start = _parse_ymd(m.group(1))
    end = _parse_ymd(m.group(2))
    if not start or not end:
        return None
    if start <= end:
        return start, end
    return end, start


def _parse_date_in_value(value: Any) -> Optional[date]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    m = _DATE_VALUE_RE.search(text)
    if not m:
        return None
    return _parse_ymd(m.group(1))


def _choose_date_field(records: Sequence[Dict[str, Any]]) -> Optional[str]:
    if not records:
        return None

    # Prefer known semantic date field names.
    keys: List[str] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        for k in row.keys():
            kk = str(k)
            if kk not in keys:
                keys.append(kk)
    for cand in _DATE_FIELD_CANDIDATES:
        if cand in keys:
            return cand

    # Fallback: first field that can parse as date in any row.
    for key in keys:
        for row in records:
            if _parse_date_in_value(row.get(key)) is not None:
                return key
    return None


def filter_records_by_instruction_date_range(payload: Any, instruction: str) -> Tuple[Any, int, Optional[str]]:
    """
    Apply instruction date-range filtering to payload records.
    Returns (new_payload, removed_count, used_date_field).
    """
    date_range = parse_date_range_from_instruction(instruction)
    if not date_range:
        return payload, 0, None
    start, end = date_range

    records: List[Dict[str, Any]]
    if isinstance(payload, dict):
        raw = payload.get("records")
        records = list(raw) if isinstance(raw, list) else []
    elif isinstance(payload, list):
        records = [x for x in payload if isinstance(x, dict)]
    else:
        return payload, 0, None

    if not records:
        return payload, 0, None

    date_field = _choose_date_field(records)
    if not date_field:
        return payload, 0, None

    filtered: List[Dict[str, Any]] = []
    for row in records:
        d = _parse_date_in_value(row.get(date_field))
        if d is None:
            continue
        if start <= d <= end:
            filtered.append(row)

    removed = len(records) - len(filtered)
    if isinstance(payload, dict):
        out = dict(payload)
        out["records"] = filtered
        return out, removed, date_field
    return filtered, removed, date_field
