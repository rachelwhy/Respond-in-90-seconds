"""
内部结构化覆盖度规格（coverage）：从 bundle 或原文推断期望键集合（仅测试辅助）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class CoverageSpec:
    key_field: str
    expected_keys: List[str]


def _profile_key_field(profile: Dict[str, Any]) -> Optional[str]:
    dkf = profile.get("dedup_key_fields")
    if isinstance(dkf, list) and dkf:
        s = str(dkf[0]).strip()
        if s:
            return s
    fields = profile.get("fields") or []
    if isinstance(fields, list) and fields:
        f0 = fields[0]
        if isinstance(f0, dict):
            n = str(f0.get("name", "")).strip()
            if n:
                return n
    return None


def derive_coverage_spec_from_bundle(bundle: Dict[str, Any], profile: Dict[str, Any]) -> Optional[CoverageSpec]:
    key_field = _profile_key_field(profile) or "名称"
    expected: List[str] = []
    seen: set = set()
    for doc in bundle.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        for tbl in doc.get("tables") or []:
            if not isinstance(tbl, dict):
                continue
            for row in tbl.get("data") or []:
                if not isinstance(row, dict):
                    continue
                v = str(row.get(key_field, "")).strip()
                if v and v not in seen:
                    seen.add(v)
                    expected.append(v)
    if not expected:
        return None
    return CoverageSpec(key_field=key_field, expected_keys=expected)


def compute_missing_keys(spec: CoverageSpec, records: Sequence[Dict[str, Any]]) -> List[str]:
    got = set()
    for r in records:
        if not isinstance(r, dict):
            continue
        v = str(r.get(spec.key_field, "")).strip()
        if v:
            got.add(v)
    return [k for k in spec.expected_keys if k not in got]


def _markdown_first_column_keys(text: str) -> List[str]:
    keys: List[str] = []
    seen: set = set()
    past_sep = False
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if not cells:
            continue
        if any("---" in (c or "") for c in cells):
            past_sep = True
            continue
        if not past_sep:
            continue
        k = cells[0]
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def _prose_first_token_keys(text: str) -> List[str]:
    keys: List[str] = []
    seen: set = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        tok = ln.split(None, 1)[0].strip()
        if tok and tok not in seen:
            seen.add(tok)
            keys.append(tok)
    return keys


def derive_coverage_spec(
    bundle: Dict[str, Any],
    profile: Dict[str, Any],
    *,
    source_text: Optional[str] = None,
) -> Optional[CoverageSpec]:
    key_field = _profile_key_field(profile) or "名称"
    if source_text and str(source_text).strip():
        st = str(source_text).strip()
        keys: List[str] = []
        if "|" in st and "---" in st:
            keys = _markdown_first_column_keys(st)
        else:
            keys = _prose_first_token_keys(st)
        if keys:
            return CoverageSpec(key_field=key_field, expected_keys=keys)
    return derive_coverage_spec_from_bundle(bundle, profile)
