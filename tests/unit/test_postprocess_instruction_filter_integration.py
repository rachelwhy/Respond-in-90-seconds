from src.core.postprocess import process_by_profile


def test_process_by_profile_applies_instruction_date_filter() -> None:
    profile = {
        "task_mode": "table_records",
        "instruction": "提取2020/7/1到2020/8/31的数据",
        "fields": [
            {"name": "国家/地区", "type": "text"},
            {"name": "日期", "type": "text"},
            {"name": "病例数", "type": "number"},
        ],
    }
    extracted = {
        "records": [
            {"国家/地区": "A", "日期": "2020-06-30", "病例数": "1"},
            {"国家/地区": "A", "日期": "2020-07-01", "病例数": "2"},
            {"国家/地区": "A", "日期": "2020-08-31", "病例数": "3"},
            {"国家/地区": "A", "日期": "2020-09-01", "病例数": "4"},
        ]
    }

    out = process_by_profile(extracted, profile)
    rows = out.get("records", [])
    assert len(rows) == 2
    assert rows[0]["日期"].startswith("2020-07-01")
    assert rows[1]["日期"].startswith("2020-08-31")
