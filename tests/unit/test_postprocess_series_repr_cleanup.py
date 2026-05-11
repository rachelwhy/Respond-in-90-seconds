from src.core.postprocess import process_single_record, process_table_records


def test_process_table_records_cleans_series_repr_string_values():
    profile = {
        "task_mode": "table_records",
        "fields": [
            {"name": "城市", "type": "text"},
            {"name": "站点名称", "type": "text"},
        ],
    }
    extracted_raw = {
        "records": [
            {
                "城市": "德州市",
                "站点名称": "站点名称    371400052\n站点名称         儿童乐园\nName: 152, dtype: object",
            }
        ]
    }

    out = process_table_records(extracted_raw, profile)
    assert out["records"][0]["站点名称"] == "儿童乐园"


def test_process_table_records_keeps_normal_station_name_untouched():
    profile = {
        "task_mode": "table_records",
        "fields": [{"name": "站点名称", "type": "text"}],
    }
    extracted_raw = {"records": [{"站点名称": "潍城区政府(开发区中学)"}]}
    out = process_table_records(extracted_raw, profile)
    assert out["records"][0]["站点名称"] == "潍城区政府(开发区中学)"


def test_process_table_records_does_not_clean_non_series_multiline_text():
    profile = {
        "task_mode": "table_records",
        "fields": [{"name": "站点名称", "type": "text"}],
    }
    extracted_raw = {
        "records": [
            {
                "站点名称": "说明: Name: 这个是普通文本\n备注: 不应被处理\n没有 dtype 标记"
            }
        ]
    }
    out = process_table_records(extracted_raw, profile)
    assert out["records"][0]["站点名称"] == "说明: Name: 这个是普通文本\n备注: 不应被处理\n没有 dtype 标记"


def test_process_table_records_strips_units_for_numeric_fields():
    profile = {
        "task_mode": "table_records",
        "fields": [
            {"name": "病例数", "type": "number"},
            {"name": "每日检测数", "type": "number"},
        ],
    }
    extracted_raw = {"records": [{"病例数": "64 例", "每日检测数": "12.6 万份"}]}
    out = process_table_records(extracted_raw, profile)
    assert out["records"][0]["病例数"] == "64"
    assert out["records"][0]["每日检测数"] == "12.6"


def test_process_single_record_strips_units_for_numeric_fields():
    profile = {
        "task_mode": "single_record",
        "fields": [{"name": "病例数", "type": "number"}],
    }
    extracted_raw = {"病例数": "2 例"}
    out = process_single_record(extracted_raw, profile)
    assert out["病例数"] == "2"


def test_process_table_records_infers_numeric_like_text_field_and_strips_units():
    profile = {
        "task_mode": "table_records",
        "fields": [{"name": "病例数", "type": "text"}],
    }
    extracted_raw = {
        "records": [
            {"病例数": "1 例"},
            {"病例数": "0 例"},
            {"病例数": "57 例"},
            {"病例数": ""},
        ]
    }
    out = process_table_records(extracted_raw, profile)
    assert out["records"][0]["病例数"] == "1"
    assert out["records"][1]["病例数"] == "0"
    assert out["records"][2]["病例数"] == "57"
