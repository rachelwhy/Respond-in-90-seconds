from __future__ import annotations

import re
from datetime import datetime, date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from src.core.scope_models import ConstraintClause, ConstraintSet, FieldConstraint


_DATE_RANGE_RE = re.compile(
    r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})\s*(?:到|至|~|～|-|—|–)\s*(\d{4}[/-]\d{1,2}[/-]\d{1,2})"
)
_DATE_VALUE_RE = re.compile(r"(\d{4}[/-]\d{1,2}[/-]\d{1,2})")
# 在记录中定位「日期/时刻」字段时的优先名信号（与别名字典互补；非穷举）
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


def parse_monitor_city_time_pairs(instruction: str) -> Optional[List[Tuple[str, str]]]:
    """
    解析指令中「表N … 监测时间：… 城市：…」多块结构（如环境监测类任务说明 txt）。

    返回若干 ``(城市, 监测时间原文)``；与具体业务地名无关，仅认标签 ``监测时间``、``城市`` 及 ``表+编号`` 分段。
    无命中则 ``None``（调用方走其它 scope 谓词）。
    """
    text = str(instruction or "")
    if not text.strip():
        return None
    segments = re.split(r"(?=^表\s*\d+\s*[：:])", text, flags=re.MULTILINE)
    pairs: List[Tuple[str, str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg or not re.match(r"表\s*\d+", seg, re.I):
            continue
        m_time = re.search(r"监测时间\s*[：:]\s*([^\n\r]+)", seg)
        m_city = re.search(r"城市\s*[：:]\s*([^\n\r]+)", seg)
        if not m_time or not m_city:
            continue
        city = m_city.group(1).strip()
        tim = m_time.group(1).strip()
        if city and tim:
            pairs.append((city, tim))
    return pairs if pairs else None


def _parse_monitor_city_time_blocks(instruction: str) -> List[Dict[str, str]]:
    """
    解析形如「表N: ... 监测时间: ... 城市: ...」的分段指令。
    返回标准化 block，供约束编译器复用。
    """
    text = str(instruction or "")
    if not text.strip():
        return []
    segments = re.split(r"(?=^表\s*\d+\s*[：:])", text, flags=re.MULTILINE)
    blocks: List[Dict[str, str]] = []
    for seg in segments:
        seg = seg.strip()
        if not seg or not re.match(r"表\s*\d+", seg, re.I):
            continue
        m_table = re.match(r"(表\s*\d+)\s*[：:]?", seg, re.I)
        m_time = re.search(r"监测时间\s*[：:]\s*([^\n\r]+)", seg)
        m_city = re.search(r"城市\s*[：:]\s*([^\n\r]+)", seg)
        if not m_time or not m_city:
            continue
        city = m_city.group(1).strip()
        tim = m_time.group(1).strip()
        if not city or not tim:
            continue
        blocks.append(
            {
                "table_hint": (m_table.group(1).strip() if m_table else ""),
                "city": city,
                "monitor_time": tim,
            }
        )
    return blocks


def _city_name_variants(city: str) -> List[str]:
    c = str(city or "").strip()
    if not c:
        return []
    out: List[str] = []
    candidates = [c]
    if c.endswith("市"):
        candidates.append(c[:-1])
    else:
        candidates.append(c + "市")
    for item in candidates:
        if item and item not in out:
            out.append(item)
    return out


def _monitor_time_fingerprints(value: str) -> List[str]:
    t = str(value or "").strip()
    if not t:
        return []
    out: List[str] = [t]
    if len(t) >= 10 and t[4] in "-/" and t[7] in "-/":
        normalized = t[:10].replace("/", "-")
        if normalized not in out:
            out.append(normalized)
    return out


def compile_monitor_city_time_constraints(instruction: str) -> Optional[ConstraintSet]:
    """
    将「表N + 城市 + 监测时间」指令编译为通用 FieldConstraints。
    """
    blocks = _parse_monitor_city_time_blocks(instruction)
    if not blocks:
        return None
    clauses: List[ConstraintClause] = []
    for block in blocks:
        city_variants = _city_name_variants(block["city"])
        time_tokens = _monitor_time_fingerprints(block["monitor_time"])
        if not city_variants or not time_tokens:
            continue
        clauses.append(
            ConstraintClause(
                constraints=[
                    FieldConstraint(
                        field_hint="city",
                        op="contains_any",
                        value=city_variants,
                        value_type="text_list",
                    ),
                    FieldConstraint(
                        field_hint="monitor_time",
                        op="contains_any",
                        value=time_tokens,
                        value_type="text_list",
                    ),
                ],
                scope_hint=block.get("table_hint") or None,
            )
        )
    if not clauses:
        return None
    return ConstraintSet(
        predicate="monitor_city_time_blocks",
        compiler="monitor_city_time_v1",
        clauses=clauses,
        priority=10,
        metadata={
            "block_count": len(clauses),
            "blocks_preview": [
                {
                    "city": b["city"][:32],
                    "monitor_time": b["monitor_time"][:32],
                    "table_hint": (b.get("table_hint") or "")[:16],
                }
                for b in blocks[:8]
            ],
        },
    )


def extract_ymd_dates_from_text(text: str) -> List[date]:
    """从正文中提取所有 `YYYY-MM-DD` 样式日期（用于范围锁定，非 LLM）。"""
    out: List[date] = []
    for m in _DATE_VALUE_RE.finditer(str(text or "")):
        d = _parse_ymd(m.group(1))
        if d is not None:
            out.append(d)
    return out


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


def compile_date_range_constraints(instruction: str) -> Optional[ConstraintSet]:
    """
    将指令中的日期区间编译为通用 FieldConstraints。
    """
    dr = parse_date_range_from_instruction(instruction)
    if not dr:
        return None
    start, end = dr
    return ConstraintSet(
        predicate="date_range",
        compiler="date_range_v1",
        clauses=[
            ConstraintClause(
                constraints=[
                    FieldConstraint(
                        field_hint="date",
                        op="date_between_ymd",
                        value={"start": start.isoformat(), "end": end.isoformat()},
                        value_type="date_window",
                    )
                ]
            )
        ],
        priority=20,
        metadata={"window": [start.isoformat(), end.isoformat()]},
    )


ConstraintCompiler = Callable[[str], Optional[ConstraintSet]]


def get_default_constraint_compilers() -> Tuple[ConstraintCompiler, ...]:
    """
    约束编译器注册点：按顺序尝试，先命中者生效。
    """
    return (
        compile_monitor_city_time_constraints,
        compile_date_range_constraints,
    )


def compile_instruction_constraints(
    instruction: str,
    compilers: Optional[Sequence[ConstraintCompiler]] = None,
) -> Optional[ConstraintSet]:
    """
    统一入口：按优先级返回首个可执行约束集。
    """
    text = str(instruction or "").strip()
    if not text:
        return None
    compiler_list = tuple(compilers) if compilers is not None else get_default_constraint_compilers()
    for compiler in compiler_list:
        out = compiler(text)
        if out is not None:
            return out
    return None


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
