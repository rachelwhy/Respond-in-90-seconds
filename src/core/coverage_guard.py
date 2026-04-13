from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List, Optional, Set


@dataclass(frozen=True)
class CoverageSpec:
    """A generic coverage specification for table-like multi-record tasks."""

    key_field: str
    expected_keys: List[str]


def _norm(s: Any) -> str:
    if s is None:
        return ""
    return str(s).strip()


def _extract_expected_keys_from_table_data(
    rows: Any,
    key_field: str,
    *,
    max_keys: int = 2000,
) -> List[str]:
    """
    Extract expected keys from a Docling table representation.

    Supports:
    - list[dict]: typical for df.to_dict(orient="records")
    - list[list]: grid-like (first row header)
    - list[str]/other: ignored
    """
    key_field = _norm(key_field)
    if not rows or max_keys <= 0:
        return []

    expected: List[str] = []
    seen: Set[str] = set()

    # Case 1: list of dict rows
    if isinstance(rows, list) and rows and isinstance(rows[0], dict):
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Prefer explicit key_field if present; else fall back to first column value
            if key_field and key_field in row:
                v = _norm(row.get(key_field))
            else:
                first_key = next(iter(row.keys()), "")
                v = _norm(row.get(first_key)) if first_key else ""
            if not v:
                continue
            if v not in seen:
                seen.add(v)
                expected.append(v)
                if len(expected) >= max_keys:
                    break
        return expected

    # Case 2: grid with header row
    if isinstance(rows, list) and rows and isinstance(rows[0], list):
        header = [_norm(x) for x in rows[0]]  # type: ignore[misc]
        col_idx = 0
        if key_field and key_field in header:
            col_idx = header.index(key_field)
        for r in rows[1:]:
            if not isinstance(r, list) or col_idx >= len(r):
                continue
            v = _norm(r[col_idx])
            if not v:
                continue
            if v not in seen:
                seen.add(v)
                expected.append(v)
                if len(expected) >= max_keys:
                    break
        return expected

    return []


_RE_MD_TABLE_ROW = re.compile(r"^\s*\|\s*[^|]+\s*\|")
_RE_NUMBERED_ITEM = re.compile(r"^\s*(\d{1,4})[.)、]\s*(.+?)\s*$")
_RE_MULTI_SPACE_SPLIT = re.compile(r"\s{2,}|\t+")


def _extract_expected_keys_from_text(
    text: str,
    *,
    max_keys: int,
    split_markers: Optional[List[str]] = None,
) -> List[str]:
    """
    Generic heuristic extraction of "row keys" from reading-order text.

    Works for many list-like sources:
    - markdown tables: take first cell of each row
    - numbered lists: take item text (or first token thereof)
    - multi-space / tab separated lines: take first column
    """
    if not text or max_keys <= 0:
        return []

    expected: List[str] = []
    seen: Set[str] = set()

    markers = [m for m in (split_markers or []) if _norm(m)]
    # Prefer longer markers first to avoid partial matches
    markers.sort(key=len, reverse=True)

    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    for ln in lines:
        key = ""

        # Markdown table row: | a | b |
        if _RE_MD_TABLE_ROW.match(ln):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if cells:
                key = cells[0]

        # Numbered item: 1. xxx
        if not key:
            m = _RE_NUMBERED_ITEM.match(ln)
            if m:
                key = m.group(2).strip()

        # Multi-column text: use first column
        if not key:
            parts = [p.strip() for p in _RE_MULTI_SPACE_SPLIT.split(ln) if p.strip()]
            if len(parts) >= 2:
                key = parts[0]

        # Field-marker split: for lines like "Key 字段A ... 字段B ..."
        # Use profile field names (excluding key_field) as split markers.
        if not key and markers:
            hit_pos = None
            for m in markers:
                p = ln.find(m)
                if p > 0:  # must have some prefix
                    hit_pos = p if hit_pos is None else min(hit_pos, p)
            if hit_pos is not None and hit_pos > 0:
                key = ln[:hit_pos].strip("：:，,;； \t")

        # Final cleanup: keep it short-ish and non-empty
        key = _norm(key)
        if not key:
            continue
        if len(key) > 60:
            # Likely a sentence; skip
            continue

        if key not in seen:
            seen.add(key)
            expected.append(key)
            if len(expected) >= max_keys:
                break

    return expected


def derive_coverage_spec(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    source_text: Optional[str] = None,
    max_keys: int = 2000,
) -> Optional[CoverageSpec]:
    """
    Derive expected keys from document structure, with fallback to reading-order text.
    """
    spec = derive_coverage_spec_from_bundle(bundle, profile, max_keys=max_keys)
    if spec:
        return spec

    if not isinstance(profile, dict):
        return None
    if profile.get("task_mode") != "table_records":
        return None

    fields = profile.get("fields") or []
    field_names = [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]
    if not field_names:
        return None

    key_fields = profile.get("dedup_key_fields") or profile.get("key_fields") or []
    key_field = _norm(key_fields[0] if isinstance(key_fields, list) and key_fields else field_names[0])
    if not key_field:
        return None

    # Use other field names as split markers to infer keys from prose-like lines
    split_markers = [fn for fn in field_names if fn and fn != key_field]
    expected_keys = _extract_expected_keys_from_text(
        source_text or "",
        max_keys=max_keys,
        split_markers=split_markers,
    )
    if not expected_keys:
        return None

    return CoverageSpec(key_field=key_field, expected_keys=expected_keys)


def derive_coverage_spec_from_bundle(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    max_keys: int = 2000,
) -> Optional[CoverageSpec]:
    """
    Derive an expected-key set from the *document itself* (Docling tables),
    so we can detect "missing rows" in a generic way.
    """
    if not isinstance(bundle, dict) or not isinstance(profile, dict):
        return None

    task_mode = profile.get("task_mode")
    if task_mode != "table_records":
        return None

    fields = profile.get("fields") or []
    field_names = [f.get("name") for f in fields if isinstance(f, dict) and f.get("name")]
    if not field_names:
        return None

    key_fields = profile.get("dedup_key_fields") or profile.get("key_fields") or []
    key_field = _norm(key_fields[0] if isinstance(key_fields, list) and key_fields else field_names[0])
    if not key_field:
        return None

    expected_keys: List[str] = []
    seen: Set[str] = set()

    for doc in bundle.get("documents") or []:
        if not isinstance(doc, dict):
            continue

        # Prefer tables raw "data" (dict rows) which we can parse without pandas
        tables = doc.get("tables") or []
        if isinstance(tables, list):
            for t in tables:
                if not isinstance(t, dict):
                    continue
                rows = t.get("data")
                keys = _extract_expected_keys_from_table_data(rows, key_field, max_keys=max_keys)
                for k in keys:
                    if k not in seen:
                        seen.add(k)
                        expected_keys.append(k)
                        if len(expected_keys) >= max_keys:
                            break
                if len(expected_keys) >= max_keys:
                    break
        if len(expected_keys) >= max_keys:
            break

    if not expected_keys:
        return None

    return CoverageSpec(key_field=key_field, expected_keys=expected_keys)


def compute_missing_keys(
    spec: CoverageSpec,
    records: List[Dict[str, Any]],
    *,
    max_missing: int = 200,
) -> List[str]:
    """
    Compute which expected keys are not covered by output records.
    """
    key_field = spec.key_field
    out: Set[str] = set()
    for r in records or []:
        if not isinstance(r, dict):
            continue
        v = _norm(r.get(key_field))
        if v:
            out.add(v)

    missing: List[str] = []
    for k in spec.expected_keys:
        if k and k not in out:
            missing.append(k)
            if len(missing) >= max_missing:
                break
    return missing


def build_snippets_for_keys(text: str, keys: List[str], *, window: int = 350, max_total_chars: int = 12000) -> str:
    """
    Build a compact evidence text by extracting local windows around each missing key.
    """
    if not text or not keys:
        return ""
    s = str(text)
    parts: List[str] = []
    total = 0
    for k in keys:
        kk = _norm(k)
        if not kk:
            continue
        pos = s.find(kk)
        if pos < 0:
            continue
        start = max(0, pos - window)
        end = min(len(s), pos + len(kk) + window)
        snippet = s[start:end].strip()
        if not snippet:
            continue
        block = f"[KEY={kk}]\n{snippet}\n"
        if total + len(block) > max_total_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts).strip()

