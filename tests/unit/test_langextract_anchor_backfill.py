from src.adapters.langextract_adapter import _apply_anchor_backfill_records


def test_anchor_backfill_replaces_over_generalized_primary_field() -> None:
    rows = [
        {"国家/地区": "中国", "病例数": "1", "_anchor_text": "湖北省"},
        {"国家/地区": "中国", "病例数": "2", "_anchor_text": "广东省"},
        {"国家/地区": "中国", "病例数": "3", "_anchor_text": "河南省"},
    ]
    out = _apply_anchor_backfill_records(rows, ["国家/地区", "病例数"])
    assert [r["国家/地区"] for r in out] == ["湖北省", "广东省", "河南省"]


def test_anchor_backfill_noop_when_primary_field_already_diverse() -> None:
    rows = [
        {"国家/地区": "湖北省", "病例数": "1", "_anchor_text": "湖北省"},
        {"国家/地区": "广东省", "病例数": "2", "_anchor_text": "广东省"},
        {"国家/地区": "河南省", "病例数": "3", "_anchor_text": "河南省"},
    ]
    out = _apply_anchor_backfill_records(rows, ["国家/地区", "病例数"])
    assert [r["国家/地区"] for r in out] == ["湖北省", "广东省", "河南省"]
