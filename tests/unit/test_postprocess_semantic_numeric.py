from src.core.postprocess import _semantic_numeric_resolve


def test_semantic_numeric_resolve_direct_negation_to_zero() -> None:
    got = _semantic_numeric_resolve(
        raw_value="无新增",
        field_name="字段A",
        field_type="number",
        aliases=[],
        record={},
        fields=[],
        source_text="",
    )
    assert got == "0"


def test_semantic_numeric_resolve_direct_total_with_subitems() -> None:
    got = _semantic_numeric_resolve(
        raw_value="新增 68 例，其中本土 64 例、输入 4 例",
        field_name="字段A",
        field_type="number",
        aliases=[],
        record={},
        fields=[],
        source_text="",
    )
    assert got == "68"


def test_semantic_numeric_resolve_from_local_context() -> None:
    fields = [{"name": "主体", "type": "text"}, {"name": "指标值", "type": "number"}]
    record = {"主体": "北京", "指标值": ""}
    source = "...... 北京 当日新增指标值为 12 ，其中A 9，B 3 ......"
    got = _semantic_numeric_resolve(
        raw_value="",
        field_name="指标值",
        field_type="number",
        aliases=[],
        record=record,
        fields=fields,
        source_text=source,
    )
    assert got == "12"


def test_semantic_numeric_resolve_negation_with_field_context_to_zero() -> None:
    got = _semantic_numeric_resolve(
        raw_value="未报告新增病例",
        field_name="病例数",
        field_type="number",
        aliases=["新增病例"],
        record={},
        fields=[],
        source_text="",
    )
    assert got == "0"


def test_semantic_numeric_resolve_do_not_force_zero_without_context() -> None:
    got = _semantic_numeric_resolve(
        raw_value="暂无数据",
        field_name="病例数",
        field_type="number",
        aliases=["新增病例"],
        record={},
        fields=[],
        source_text="",
    )
    assert got == "暂无数据"


def test_semantic_numeric_resolve_from_snippet_negation_evidence_to_zero() -> None:
    fields = [{"name": "国家/地区", "type": "text"}, {"name": "病例数", "type": "number"}]
    record = {"国家/地区": "安徽省", "病例数": ""}
    source = "...... 安徽省 7月27日 未报告新增病例，继续开展常态化监测 ......"
    got = _semantic_numeric_resolve(
        raw_value="",
        field_name="病例数",
        field_type="number",
        aliases=["新增病例"],
        record=record,
        fields=fields,
        source_text=source,
    )
    assert got == "0"
