from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List


_NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_DEFAULT_NEGATION_CUES = (
    "无",
    "未见",
    "未发现",
    "未报告",
    "未检出",
    "没有",
    "并无",
    "暂无",
    "尚无",
    "缺失",
    "缺省",
    "none",
    "no ",
    "not ",
    "not found",
    "not reported",
    "not detected",
    "not available",
)
_DEFAULT_ZERO_UNITS = ("例", "起", "人", "次", "件", "项", "%", "万", "亿", "千")
_DEFAULT_ZERO_CONTEXT_HINTS = ("新增", "发生", "报告", "检出", "发现", "增长", "变化", "cases?", "incidents?")
_DEFAULT_DATE_CHARS = ("年", "月", "日", "/")
_DEFAULT_SNIPPET_WINDOW = 520
_DEFAULT_KEYWORD_CONTEXT_BEFORE = 24
_DEFAULT_KEYWORD_CONTEXT_AFTER = 40


def _load_interpreter_config() -> Dict[str, Any]:
    rules_path = Path(__file__).parent.parent / "knowledge" / "field_normalization_rules.json"
    try:
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = data.get("field_interpreter", {})
        if isinstance(cfg, dict):
            return cfg
    except Exception:
        pass
    return {}


_INTERPRETER_CONFIG = _load_interpreter_config()
_NEGATION_CUES = tuple(_INTERPRETER_CONFIG.get("negation_cues") or _DEFAULT_NEGATION_CUES)
_ZERO_UNITS = tuple(_INTERPRETER_CONFIG.get("zero_units") or _DEFAULT_ZERO_UNITS)
_ZERO_CONTEXT_HINTS = tuple(_INTERPRETER_CONFIG.get("zero_context_hints") or _DEFAULT_ZERO_CONTEXT_HINTS)
_DATE_CHARS = tuple(_INTERPRETER_CONFIG.get("date_context_chars") or _DEFAULT_DATE_CHARS)
_SNIPPET_WINDOW = int(_INTERPRETER_CONFIG.get("snippet_window") or _DEFAULT_SNIPPET_WINDOW)
_KEYWORD_CONTEXT_BEFORE = int(
    _INTERPRETER_CONFIG.get("keyword_context_before") or _DEFAULT_KEYWORD_CONTEXT_BEFORE
)
_KEYWORD_CONTEXT_AFTER = int(
    _INTERPRETER_CONFIG.get("keyword_context_after") or _DEFAULT_KEYWORD_CONTEXT_AFTER
)
_ZERO_TOKEN_RE = re.compile(
    rf"(?:^|[^\d])0+(?:\.0+)?(?:\s*(?:{'|'.join(re.escape(u) for u in _ZERO_UNITS)}))?(?:$|[^\d])"
)
_ZERO_CONTEXT_HINT_RE = re.compile(r"(?:%s)" % "|".join(_ZERO_CONTEXT_HINTS), re.I)


def _format_numeric_token(token: str) -> str:
    try:
        val = float(str(token))
        if val.is_integer():
            return str(int(val))
        return str(val).rstrip("0").rstrip(".")
    except Exception:
        return str(token).strip()


def _has_explicit_zero_evidence(text: str, keywords: List[str] | None = None) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    if _ZERO_TOKEN_RE.search(s):
        return True

    # Negation/absence evidence must be paired with field context
    # (keyword hit or common quantitative context word).
    has_negation = any(cue in s.lower() for cue in _NEGATION_CUES)
    if not has_negation:
        return False

    kws = [str(x).strip() for x in (keywords or []) if str(x).strip()]
    if kws and any(k in s for k in kws):
        return True
    return bool(_ZERO_CONTEXT_HINT_RE.search(s))


def _parse_semantic_numeric(text: str, *, keywords: List[str] | None = None) -> str:
    s = str(text or "").strip()
    if not s:
        return ""

    neg_zero_evidence = _has_explicit_zero_evidence(s, keywords)
    matches = list(_NUM_RE.finditer(s))
    nums = [m.group(0) for m in matches]
    if not nums and neg_zero_evidence:
        return "0"
    if not nums:
        return ""

    if neg_zero_evidence:
        filtered_nums: List[str] = []
        for m in matches:
            st, ed = m.span()
            near = s[max(0, st - 2): min(len(s), ed + 2)]
            # 在明确“无/未报告”等否定证据语境下，排除日期数字（如 7月27日）
            if any(ch in near for ch in _DATE_CHARS):
                continue
            filtered_nums.append(m.group(0))
        if not filtered_nums:
            return "0"
        nums = filtered_nums

    # Prefer an explicit total; otherwise combine sub-items when obvious.
    if "其中" in s:
        pre, post = s.split("其中", 1)
        pre_nums = _NUM_RE.findall(pre)
        if pre_nums:
            return _format_numeric_token(pre_nums[0])
        post_nums = _NUM_RE.findall(post)
        if len(post_nums) >= 2:
            try:
                return _format_numeric_token(sum(float(x) for x in post_nums))
            except Exception:
                pass

    return _format_numeric_token(nums[0])


def _derive_field_keywords(field_name: str, aliases: List[str]) -> List[str]:
    raw = [str(field_name or "").strip()] + [str(x).strip() for x in aliases if str(x).strip()]
    stop_suffix = ("数量", "总量", "人数", "数", "值", "率", "比", "情况")
    out: List[str] = []
    seen = set()
    for item in raw:
        if not item:
            continue
        candidates = [item]
        for suf in stop_suffix:
            if item.endswith(suf) and len(item) > len(suf) + 1:
                candidates.append(item[: -len(suf)])
        for c in candidates:
            c = c.strip()
            if len(c) < 2:
                continue
            if c not in seen:
                out.append(c)
                seen.add(c)
    return out


def _select_record_anchor(record: Dict[str, Any], fields: List[dict]) -> str:
    text_field_names = [f.get("name") for f in fields if isinstance(f, dict) and f.get("type") == "text"]
    candidates = text_field_names + [k for k, v in record.items() if isinstance(v, str)]
    for key in candidates:
        if not key:
            continue
        v = str(record.get(key, "")).strip()
        if 1 < len(v) <= 20 and not _NUM_RE.search(v):
            return v
    return ""


def _extract_local_snippet(source_text: str, anchor: str, window: int = _SNIPPET_WINDOW) -> str:
    text = str(source_text or "")
    a = str(anchor or "").strip()
    if not text or not a:
        return ""
    pos = text.find(a)
    if pos < 0:
        return ""
    start = max(0, pos - window)
    end = min(len(text), pos + len(a) + window)
    return text[start:end]


@dataclass
class FieldInterpreter:
    """Generic evidence-driven field value interpreter."""

    def resolve(
        self,
        *,
        raw_value: Any,
        field_name: str,
        field_type: str,
        aliases: List[str],
        record: Dict[str, Any],
        fields: List[dict],
        source_text: str,
    ) -> Any:
        fname = str(field_name or "").strip().lower()
        ftype = str(field_type or "").strip().lower()

        if any(x in fname for x in ("日期", "时间", "date", "time")):
            return raw_value
        if ftype not in ("number", "money", "percentage"):
            return raw_value

        current = str(raw_value or "").strip()
        keywords = _derive_field_keywords(field_name, aliases)
        parsed_direct = _parse_semantic_numeric(current, keywords=keywords)
        if parsed_direct:
            return parsed_direct

        anchor = _select_record_anchor(record, fields)
        snippet = _extract_local_snippet(source_text, anchor)
        if not snippet:
            return raw_value

        if not keywords:
            return raw_value

        for kw in keywords:
            idx = snippet.find(kw)
            if idx < 0:
                continue
            seg = snippet[
                max(0, idx - _KEYWORD_CONTEXT_BEFORE):
                min(len(snippet), idx + max(48, len(kw) + _KEYWORD_CONTEXT_AFTER))
            ]
            parsed = _parse_semantic_numeric(seg, keywords=keywords)
            if parsed:
                return parsed
        if _has_explicit_zero_evidence(snippet, keywords=keywords):
            return "0"
        return raw_value

