from src.core.instruction_filters import (
    filter_records_by_instruction_date_range,
    parse_date_range_from_instruction,
)


def test_parse_date_range_from_instruction_cn_separator() -> None:
    got = parse_date_range_from_instruction("请提取日期从2020/7/1到2020/8/31的数据")
    assert got is not None
    assert str(got[0]) == "2020-07-01"
    assert str(got[1]) == "2020-08-31"


def test_filter_records_by_instruction_date_range_with_date_field() -> None:
    payload = {
        "records": [
            {"国家/地区": "A", "日期": "2020-06-30"},
            {"国家/地区": "A", "日期": "2020-07-01"},
            {"国家/地区": "A", "日期": "2020-08-15 00:00:00"},
            {"国家/地区": "A", "日期": "2020-09-01"},
        ]
    }
    out, removed, field = filter_records_by_instruction_date_range(
        payload,
        "提取2020/7/1到2020/8/31的数据",
    )

    assert field == "日期"
    assert removed == 2
    assert len(out["records"]) == 2
    assert out["records"][0]["日期"] == "2020-07-01"


def test_filter_records_by_instruction_date_range_no_date_field_keeps_payload() -> None:
    payload = {"records": [{"国家/地区": "中国", "病例数": "1"}]}
    out, removed, field = filter_records_by_instruction_date_range(
        payload,
        "提取2020/7/1到2020/8/31的数据",
    )
    assert out == payload
    assert removed == 0
    assert field is None
