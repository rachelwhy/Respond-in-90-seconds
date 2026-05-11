import pytest

from src.core.instruction_filters import compile_instruction_constraints
from src.core.scope_resolution import resolve_semantic_chunks_with_meta


def test_compile_instruction_constraints_monitor_city_time():
    instruction = (
        "表1：\n"
        "监测时间：2024-06-01 08:00\n"
        "城市：德州市\n\n"
        "表2：\n"
        "监测时间：2024-06-01 09:00\n"
        "城市：潍坊市\n"
    )
    out = compile_instruction_constraints(instruction)
    assert out is not None
    assert out.predicate == "monitor_city_time_blocks"
    assert len(out.clauses) == 2
    first_ops = {c.op for c in out.clauses[0].constraints}
    assert "contains_any" in first_ops


def test_compile_instruction_constraints_date_range_when_no_monitor_blocks():
    instruction = "仅统计 2024-01-01 到 2024-01-31 的记录"
    out = compile_instruction_constraints(instruction)
    assert out is not None
    assert out.predicate == "date_range"
    assert out.clauses[0].constraints[0].op == "date_between_ymd"


def test_resolve_semantic_chunks_by_constraint_clause():
    bundle = {
        "documents": [
            {
                "chunks": [
                    {"type": "text", "text": "这是标题段"},
                    {"type": "text", "text": "城市=德州市 | 监测时间=2024-06-01 08:00 | PM2.5=40"},
                    {"type": "text", "text": "与命中块相邻的补充说明"},
                    {"type": "text", "text": "城市=济南市 | 监测时间=2024-06-02 08:00"},
                ]
            }
        ]
    }
    profile = {"instruction": "表1：\n监测时间：2024-06-01 08:00\n城市：德州"}
    chunks, meta = resolve_semantic_chunks_with_meta(bundle, profile)
    assert len(chunks) >= 2
    joined = "\n".join(c.get("text", "") for c in chunks)
    assert "德州市" in joined
    assert meta["mode"] in {"constraint_clauses", "table_row_filter"}


def test_resolve_semantic_chunks_by_date_range_constraint():
    bundle = {
        "documents": [
            {
                "chunks": [
                    {"type": "text", "text": "记录日期 2024-01-05，值 10"},
                    {"type": "text", "text": "记录日期 2024-02-05，值 20"},
                ]
            }
        ]
    }
    profile = {"instruction": "请提取 2024-01-01 到 2024-01-31 的数据"}
    chunks, meta = resolve_semantic_chunks_with_meta(bundle, profile)
    assert len(chunks) >= 1
    assert "2024-01-05" in "\n".join(c.get("text", "") for c in chunks)
    assert meta["mode"] == "date_window"


def test_table_row_filter_uses_alias_columns_and_typed_date_match():
    pd = pytest.importorskip("pandas")
    df = pd.DataFrame(
        [
            {"地区": "德州市", "监测日期": "2024/06/01 08:00", "数值": 10},
            {"地区": "潍坊市", "监测日期": "2024/06/01 09:00", "数值": 20},
        ]
    )
    bundle = {"documents": [{"chunks": [{"type": "text", "text": "备用块"}], "tables_dataframes": [df]}]}
    profile = {"instruction": "表1：\n监测时间：2024-06-01 08:00\n城市：德州"}
    chunks, meta = resolve_semantic_chunks_with_meta(bundle, profile)
    assert meta["mode"] == "table_row_filter"
    assert len(chunks) == 1
    assert "德州市" in chunks[0]["text"]
    table_meta = (meta.get("table_match_meta") or [{}])[0]
    assert table_meta.get("matched_rows_via_columns", 0) >= 1
