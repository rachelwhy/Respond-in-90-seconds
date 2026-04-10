import json
import re
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from src.adapters.model_client import call_model, call_ollama


def build_missing_fields_prompt(text, missing_fields, profile):
    """构建补充缺失字段的 prompt（内联，避免依赖 prompt_builder）"""
    names = "、".join(missing_fields)
    return (
        f"请从以下文本中补充提取这些缺失字段：{names}\n\n"
        f"文本：\n{text[:4000]}\n\n"
        f'以JSON格式返回：{{"records": [{{"字段名": "值"}}]}}'
    )


# =========================
# 5. 内部标准化
# =========================
def _build_annotation_re():
    """从 field_normalization_rules.json 动态加载标注关键字并编译正则"""
    try:
        rules_path = Path(__file__).parent.parent / "knowledge" / "field_normalization_rules.json"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = json.load(f)
        patterns = rules.get("annotation_patterns", [])
        if patterns:
            joined = "|".join(patterns)
            return re.compile(rf'[（(]\s*(?:{joined})\s*[）)]')
    except Exception:
        pass
    # 兜底：空模式（不做任何替换）
    return re.compile(r'(?!)')

_LLM_ANNOTATION_RE = _build_annotation_re()


def strip_llm_annotations(value: str) -> str:
    """清理 LLM 在字段值中附加的括号标注，如（修正值）、（预估）等"""
    if not value:
        return value
    return _LLM_ANNOTATION_RE.sub('', value).strip()


def normalize_text(value: str) -> str:
    if value is None:
        return ""
    return strip_llm_annotations(str(value).strip())


def clean_org_name(value: str) -> str:
    """
    从一句话里提取组织名称（公司/单位）
    关键：优先提取“和/与/跟/由/是”等连接词后面的组织名，避免把“我们和...”一起带进去
    """
    if value is None:
        return ""
    s = str(value).strip()

    # 1) 先用“连接词后面的组织名”模式（最有效）
    connector_patterns = [
        r'(?:我们|咱们|本次|这次|这回|今天|刚刚|刚才|后来)?(?:是|和|与|跟|同|由)\s*([^\s，。、“”"（）()]{2,60}?(?:有限公司|集团|研究院|中心|学院|大学))'
    ]
    for pat in connector_patterns:
        m = re.search(pat, s)
        if m:
            return m.group(1).strip()

    # 2) 再做兜底：从句子中找“最像公司名的后缀实体”，取“最后一个”更稳
    suffix = r'(?:信息技术有限公司|科技有限公司|数据服务有限公司|智能设备有限公司|网络科技有限公司|软件有限公司|有限公司|集团|研究院|中心|学院|大学)'
    m_all = re.findall(r'([^\s，。、“”"（）()]{2,60}?%s)' % suffix, s)
    if m_all:
        return m_all[-1].strip()

    return s


def normalize_phone(value: str) -> str:
    if value is None:
        return ""
    return re.sub(r"\D", "", str(value))


def normalize_date(value: str) -> str:
    if value is None:
        return ""
    s = str(value).strip()
    s = s.replace("年", "-").replace("月", "-").replace("日", "").replace("号", "")
    s = s.replace("/", "-")
    s = re.sub(r"\s+", "", s)

    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return f"{y}-{int(mo):02d}-{int(d):02d}"

    return s


def normalize_money(value: str) -> str:
    if value is None:
        return ""
    s = str(value).replace(",", "").strip()
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0) if m else ""


def normalize_internal(value: str, field_type: str, field_name: str = "") -> str:
    """字段值规范化（类型感知）。

    优先使用泛化 FieldNormalizer 规则；若无匹配规则，回退到硬编码逻辑。
    """
    # 尝试泛化规则框架
    if field_name or field_type:
        try:
            from src.core.field_normalizer import FieldNormalizer
            result = FieldNormalizer.get_instance().normalize(
                field_name=field_name or "",
                raw_value=str(value) if value is not None else "",
                field_type=field_type,
            )
            if result is not None:
                return result
        except Exception:
            pass

    # 回退到硬编码逻辑
    field_type = (field_type or "text").lower()

    if field_type == "phone":
        return normalize_phone(value)
    elif field_type == "date":
        return normalize_date(value)
    elif field_type == "money":
        return normalize_money(value)
    else:
        return normalize_text(value)


def fallback_extract_company_name(text: str) -> str:
    patterns = [
        r'([^\s，。、“”"（）()]{2,40}?信息技术有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?科技有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?数据服务有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?智能设备有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?网络科技有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?软件有限公司)',
        r'([^\s，。、“”"（）()]{2,40}?有限公司)',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return ""


def fallback_extract_project_title(text: str) -> str:
    # 1) 优先引号内容
    m = re.search(r'“([^”]{2,50})”', text)
    if m:
        return m.group(1).strip()

    # 2) “谈成的是XXX这个项目 / 签的是XXX项目”
    patterns = [
        r'谈成的是([^，。]{2,50})这个项目',
        r'签的是([^，。]{2,50})这个项目',
        r'对应的(?:是)?([^，。]{2,50})项目',
        r'做的(?:是)?([^，。]{2,50})项目',
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip().strip('“”"')
    return ""

def rule_fill_table_records(extracted_raw: dict, profile: dict):
    """
    表格任务优先规则补齐：
    - 不做大模型二次补抽
    - 先做行内默认补齐 / 相邻行继承 / 简单规则兜底
    """
    records = extracted_raw.get("records", [])
    if not isinstance(records, list):
        extracted_raw["records"] = []
        return extracted_raw, []

    field_items = profile.get("fields", [])
    field_names = [item["name"] for item in field_items]

    fill_log = []

    # 你可以按字段名分组，哪些字段适合继承上一行
    carry_forward_fields = {
        "城市", "城市名", "区", "区域", "省份"
    }

    previous_row = {}

    for row_idx, record in enumerate(records):
        if not isinstance(record, dict):
            continue

        # 1. 先把不存在的字段补成空字符串，避免后面 KeyError
        for name in field_names:
            if name not in record:
                record[name] = ""

        # 2. 对适合“沿用上一行”的字段，若为空则从上一行继承
        for name in carry_forward_fields:
            if name in record:
                current_value = str(record.get(name, "")).strip()
                prev_value = str(previous_row.get(name, "")).strip()
                if not current_value and prev_value:
                    record[name] = prev_value
                    fill_log.append({
                        "row_index": row_idx,
                        "field": name,
                        "strategy": "carry_forward",
                        "value": prev_value
                    })

        # 3. 记录当前行，供下一行继承
        previous_row = dict(record)

    extracted_raw["records"] = records
    return extracted_raw, fill_log

# =========================
# 6. 输出格式化
# =========================
CN_NUM = "零壹贰叁肆伍陆柒捌玖"
CN_UNIT_INT = ["", "拾", "佰", "仟"]
CN_SECTION = ["", "万", "亿", "兆"]


def four_digit_to_cn(num: int) -> str:
    result = ""
    zero_flag = False
    digits = [int(x) for x in f"{num:04d}"]

    for i, d in enumerate(digits):
        pos = 3 - i
        if d == 0:
            zero_flag = True
        else:
            if zero_flag and result:
                result += "零"
            result += CN_NUM[d] + CN_UNIT_INT[pos]
            zero_flag = False

    return result


def int_to_cny_upper(num: int) -> str:
    if num == 0:
        return "零元整"

    sections = []
    unit_pos = 0

    while num > 0:
        section = num % 10000
        if section != 0:
            section_str = four_digit_to_cn(section)
            if CN_SECTION[unit_pos]:
                section_str += CN_SECTION[unit_pos]
            sections.insert(0, section_str)
        else:
            if sections and not sections[0].startswith("零"):
                sections.insert(0, "零")
        num //= 10000
        unit_pos += 1

    result = "".join(sections)
    result = re.sub(r"零+", "零", result)
    result = result.rstrip("零")
    return result + "元整"


def format_money(value: str, output_format: str) -> str:
    if not value:
        return ""

    amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if output_format == "plain_number":
        if amount == amount.to_integral():
            return str(int(amount))
        return format(amount, "f")

    if output_format == "with_unit":
        if amount == amount.to_integral():
            return f"{int(amount)}元"
        return f"{format(amount, 'f')}元"

    if output_format == "currency_symbol":
        return f"￥{format(amount, '.2f')}"

    if output_format == "cny_uppercase":
        integer_part = int(amount)
        return int_to_cny_upper(integer_part)

    if amount == amount.to_integral():
        return str(int(amount))
    return format(amount, "f")


def format_date(value: str, output_format: str) -> str:
    if not value:
        return ""

    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if not m:
        return value

    y, mo, d = m.groups()
    mo_i = int(mo)
    d_i = int(d)

    if output_format == "YYYY-MM-DD":
        return f"{y}-{mo_i:02d}-{d_i:02d}"

    if output_format == "YYYY年M月D日":
        return f"{y}年{mo_i}月{d_i}日"

    return f"{y}-{mo_i:02d}-{d_i:02d}"


def format_phone(value: str, output_format: str) -> str:
    if not value:
        return ""
    return value


def format_text(value: str, output_format: str) -> str:
    if not value:
        return ""
    return str(value).strip()


def format_value(value: str, field_type: str, output_format: str) -> str:
    field_type = (field_type or "text").lower()
    output_format = (output_format or "plain").strip()

    if field_type == "money":
        return format_money(value, output_format)
    elif field_type == "date":
        return format_date(value, output_format)
    elif field_type == "phone":
        return format_phone(value, output_format)
    else:
        return format_text(value, output_format)


# =========================
# 7. 按 profile 处理结果
# =========================
def validate_required_fields(final_data: dict, profile: dict):
    task_mode = profile.get("task_mode", "single_record")

    if task_mode == "table_records":
        records = final_data.get("records", [])
        if not records:
            return ["records"]

        missing_items = []
        for row_idx, record in enumerate(records):
            for item in profile.get("fields", []):
                if item.get("required", False):
                    name = item["name"]
                    value = record.get(name, "")
                    if value is None or str(value).strip() == "":
                        missing_items.append({
                            "row_index": row_idx,
                            "field": name
                        })
        return missing_items

    missing = []
    for item in profile.get("fields", []):
        if item.get("required", False):
            name = item["name"]
            value = final_data.get(name, "")
            if value is None or str(value).strip() == "":
                missing.append(name)
    return missing

def retry_missing_required_fields_single(
    text: str,
    profile: dict,
    extracted_raw: dict,
    missing_fields: list[str]
):
    retried = []

    if not missing_fields:
        return extracted_raw, retried

    field_map = {item["name"]: item for item in profile.get("fields", [])}
    field_items = [field_map[name] for name in missing_fields if name in field_map]

    if not field_items:
        return extracted_raw, retried

    retry_prompt = build_missing_fields_prompt(text, field_items)

    try:
        retry_result = call_model(retry_prompt)

        print("=== 二次提取返回结果 ===")
        print(json.dumps(retry_result, ensure_ascii=False, indent=2))

        for field_name in missing_fields:
            new_value = retry_result.get(field_name, "")
            if str(new_value).strip():
                extracted_raw[field_name] = new_value
                retried.append(field_name)

    except Exception as e:
        print(f"[WARN] 批量二次提取失败：{e}")

    return extracted_raw, retried

def retry_missing_required_fields_table(
    retry_text: str,
    profile: dict,
    extracted_raw: dict,
    missing_items: list[dict]
):
    # 表格任务不走通用 LLM 二次补抽，先走规则补齐
    extracted_raw, fill_log = rule_fill_table_records(extracted_raw, profile)

    if fill_log:
        print(f"[INFO] 表格任务已执行规则补齐：{fill_log[:10]}")
    else:
        print("[INFO] 表格任务未触发规则补齐。")

    return extracted_raw, fill_log

def retry_missing_required_fields(
    retry_text: str,
    profile: dict,
    extracted_raw: dict,
    missing_fields
):
    task_mode = profile.get("task_mode", "single_record")

    if task_mode == "table_records":
        return retry_missing_required_fields_table(
            retry_text=retry_text,
            profile=profile,
            extracted_raw=extracted_raw,
            missing_items=missing_fields
        )

    return retry_missing_required_fields_single(
        text=retry_text,
        profile=profile,
        extracted_raw=extracted_raw,
        missing_fields=missing_fields
    )

def build_debug_result(extracted_raw: dict, profile: dict) -> dict:
    task_mode = profile.get("task_mode", "single_record")

    if task_mode == "table_records":
        raw_records = extracted_raw.get("records", [])
        debug_rows = []

        if isinstance(raw_records, list):
            for idx, record in enumerate(raw_records):
                if not isinstance(record, dict):
                    continue

                row_debug = {"_row_index": idx}
                for item in profile.get("fields", []):
                    name = item["name"]
                    field_type = item.get("type", "text")
                    output_format = item.get("output_format", "plain")
                    raw_value = record.get(name, "")

                    internal_value = normalize_internal(raw_value, field_type, field_name=name)
                    final_value = format_value(internal_value, field_type, output_format)
                    row_debug[name] = {
                        "raw": raw_value,
                        "normalized": internal_value,
                        "final": final_value,
                        "status": "ok" if str(final_value).strip() else "empty"
                    }

                debug_rows.append(row_debug)

        return {
            "task_mode": "table_records",
            "row_count": len(debug_rows),
            "rows": debug_rows
        }

    debug_data = {}
    for item in profile.get("fields", []):
        name = item["name"]
        field_type = item.get("type", "text")
        output_format = item.get("output_format", "plain")
        raw_value = extracted_raw.get(name, "")

        if name == "甲方单位":
            raw_value = clean_org_name(raw_value)

        internal_value = normalize_internal(raw_value, field_type, field_name=name)
        final_value = format_value(internal_value, field_type, output_format)
        status = "ok" if str(final_value).strip() else "empty"

        debug_data[name] = {
            "raw": raw_value,
            "normalized": internal_value,
            "final": final_value,
            "status": status
        }

    return debug_data


def build_run_summary(
    profile: dict,
    runtime: dict,
    missing_fields: list[str],
    retried_fields: list[str],
    input_text: str
) -> dict:
    return {
        "report_name": profile.get("report_name", ""),
        "profile_path": profile.get("report_name", ""),
        "input_char_count": len(input_text),
        "missing_required_fields": missing_fields,
        "retried_fields": retried_fields,
        "total_seconds": runtime.get("total_seconds", 0),
        "within_limit_seconds": runtime.get("within_limit_seconds", False),
        "model_inference_seconds": runtime.get("model_inference_seconds", 0)
    }


def _strip_value_unit(value: str, unit: str) -> str:
    """从值的末尾剥离已在字段名中注明的单位，返回纯数值字符串。

    只在去掉单位后字符串是合法数字时才剥离，避免误处理文本字段。
    """
    if not unit or not value:
        return value
    s = str(value).strip()
    cleaned = re.sub(r'\s*' + re.escape(unit) + r'\s*$', '', s).strip()
    # 去掉千分位逗号后检查是否为纯数字（含小数点、负号）
    if re.match(r'^-?[\d,]+(\.\d+)?$', cleaned):
        return cleaned
    return value


_DEDUP_UNIT_RE = re.compile(r'\s*(亿元|万元|千元|百元|元|亿|万|千|百|%|％|‰|万人|千人|人|平方公里|km²|亿美元|万美元|美元)\s*$')


def _normalize_rec_for_dedup(rec: dict) -> str:
    """规范化记录用于去重比对：剥离单位/逗号/空格，保留核心值"""
    normalized = {}
    for k, v in rec.items():
        s = _DEDUP_UNIT_RE.sub('', str(v).strip())
        s = s.replace(',', '').replace(' ', '')
        normalized[k] = s
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _dedup_records(records: list) -> tuple:
    """去重：先规范化值再做精确比对，兼容 LLM 对同一实体输出略有差异的情况。
    返回 (去重后列表, 移除数量)"""
    seen: set = set()
    result = []
    for rec in records:
        key = _normalize_rec_for_dedup(rec)
        if key in seen:
            continue
        seen.add(key)
        result.append(rec)
    return result, len(records) - len(result)


def process_single_record(extracted_raw, profile):
    """单记录模式的后处理：字段名规范化 + 类型清洗 + 格式化"""
    final_data = {}
    fields = profile.get("fields", [])

    # 规范化字段名：将提取结果中的字段名映射到模板规范字段名
    try:
        from src.core.alias import load_alias_map, build_reverse_alias_map
        alias_map = load_alias_map()
        reverse_alias_map = build_reverse_alias_map(alias_map)

        # 创建规范化后的提取结果
        normalized_extracted = {}
        for key, value in extracted_raw.items():
            if key in reverse_alias_map:
                normalized_key = reverse_alias_map[key]
            else:
                normalized_key = key
            normalized_extracted[normalized_key] = value

        print(f'[INFO] 单记录字段名规范化完成: {len(extracted_raw)} -> {len(normalized_extracted)} 个字段')
        extracted_raw = normalized_extracted
    except Exception as e:
        print(f'[WARN] 字段名规范化失败: {e}')

    for item in fields:
        name = item["name"]
        field_type = item.get("type", "text")
        output_format = item.get("output_format", "plain")

        raw_value = extracted_raw.get(name, "")

        if name == "甲方单位":
            raw_value = clean_org_name(raw_value)

        internal_value = normalize_internal(raw_value, field_type, field_name=name)
        final_value = format_value(internal_value, field_type, output_format)

        final_data[name] = final_value

    return final_data


def _flatten_nested_records(raw_records: list, field_names: list) -> list:
    """将 LLM 返回的嵌套 JSON 展平为扁平记录列表。

    处理场景：LLM 返回 {"头部城市": {"北京": {"GDP": 123, ...}}} 这样的嵌套结构，
    需要展平为 [{"城市": "北京", "GDP总量": "123", ...}, ...] 的扁平记录。
    """
    if not raw_records:
        return raw_records

    # 检查是否需要展平：如果记录已经是扁平的（大多数 key 在 field_names 中），直接返回
    def _is_flat_record(rec: dict) -> bool:
        if not isinstance(rec, dict):
            return False
        matching = sum(1 for k in rec if k in field_names)
        return matching >= len(field_names) * 0.3  # 至少 30% 的 key 匹配模板字段

    if all(_is_flat_record(r) for r in raw_records if isinstance(r, dict)):
        return raw_records

    # 递归展平
    flat_records = []

    def _extract_leaf_records(obj, parent_key=""):
        """递归遍历嵌套 dict，在叶子层收集扁平记录"""
        if not isinstance(obj, dict):
            return
        # 检查当前层是否像一条扁平记录
        if _is_flat_record(obj):
            rec = dict(obj)
            if parent_key:
                # 尝试把父级 key 作为第一个文本字段的值（通常是"城市"之类的名称）
                text_fields = [f for f in field_names if f not in rec or not rec[f]]
                first_text = field_names[0] if field_names else None
                if first_text and (first_text not in rec or not rec[first_text]):
                    rec[first_text] = parent_key
            flat_records.append(rec)
            return

        # 当前层的所有 value 都是 dict → 分组层，key 可能是名称（如城市名）
        dict_values = {k: v for k, v in obj.items() if isinstance(v, dict)}
        if dict_values:
            for key, val in dict_values.items():
                _extract_leaf_records(val, parent_key=key)
        # 当前层的所有 value 都是 list → 可能是分组列表
        list_values = {k: v for k, v in obj.items() if isinstance(v, list)}
        for key, val_list in list_values.items():
            for item in val_list:
                if isinstance(item, dict):
                    _extract_leaf_records(item, parent_key="")

    for record in raw_records:
        if isinstance(record, dict):
            _extract_leaf_records(record)

    if flat_records:
        print(f'[INFO] 嵌套JSON展平: {len(raw_records)} 个嵌套对象 → {len(flat_records)} 条扁平记录')
        return flat_records

    return raw_records


def process_table_records(extracted_raw: dict, profile: dict) -> dict:
    fields = profile.get("fields", [])
    raw_records = extracted_raw.get("records", [])

    if not isinstance(raw_records, list):
        raw_records = []

    # 展平嵌套的 LLM 输出
    field_names = [f["name"] for f in fields if isinstance(f, dict)]
    raw_records = _flatten_nested_records(raw_records, field_names)

    # 规范化字段名：将每条记录中的字段名映射到模板规范字段名
    try:
        from src.core.alias import load_alias_map, build_reverse_alias_map
        alias_map = load_alias_map()
        reverse_alias_map = build_reverse_alias_map(alias_map)

        normalized_records = []
        for record in raw_records:
            if not isinstance(record, dict):
                continue
            normalized_record = {}
            for key, value in record.items():
                if key in reverse_alias_map:
                    normalized_key = reverse_alias_map[key]
                else:
                    normalized_key = key
                normalized_record[normalized_key] = value
            normalized_records.append(normalized_record)

        print(f'[INFO] 表格记录字段名规范化完成: {len(raw_records)} 条记录')
        raw_records = normalized_records
    except Exception as e:
        print(f'[WARN] 表格字段名规范化失败: {e}')

    def _format_rows(records):
        final_records = []
        for record in records:
            if not isinstance(record, dict):
                continue
            row = {}
            for item in fields:
                name = item["name"]
                field_type = item.get("type", "text")
                output_format = item.get("output_format", "plain")
                unit = item.get("unit", "")
                raw_value = record.get(name, "")
                # 若字段名带单位（如 人均GDP（元））则先剥离值中的单位后缀
                if unit:
                    raw_value = _strip_value_unit(str(raw_value), unit)
                internal_value = normalize_internal(raw_value, field_type, field_name=name)
                final_value = format_value(internal_value, field_type, output_format)
                row[name] = final_value
            final_records.append(row)
        return final_records

    result = {"records": _format_rows(raw_records)}

    # 规范化去重（处理切片重叠导致的重复记录）
    if result["records"]:
        deduped, removed = _dedup_records(result["records"])
        if removed:
            print(f"[INFO] 规范化去重：{len(result['records'])} → {len(deduped)} 条（移除 {removed} 条重复）")
        result["records"] = deduped

    if isinstance(extracted_raw.get("_table_groups"), list):
        result["_table_groups"] = []
        for group in extracted_raw["_table_groups"]:
            result["_table_groups"].append({
                "table_index": group.get("table_index", 0),
                "table_label": group.get("table_label", ""),
                "conditions": group.get("conditions", {}),
                "records": _format_rows(group.get("records", [])),
            })

    return result


def _apply_filter_rules(records: list, filter_rules: list) -> list:
    """按 profile 中的 filter_rules 对 records 进行过滤

    filter_rules 格式示例：
    [
      {"field": "金额", "op": ">",        "value": 10000},
      {"field": "城市", "op": "contains", "value": "京"},
      {"field": "日期", "op": ">=",       "value": "2025-01-01"}
    ]

    支持的 op：
      >  <  >=  <=  ==  !=  contains  startswith  endswith  not_empty  is_empty
    多条规则为 AND 关系（全部满足才保留）。
    """
    if not filter_rules or not records:
        return records

    def _match(record: dict, rule: dict) -> bool:
        field = rule.get("field", "")
        op = rule.get("op", "==")
        cond_val = rule.get("value", "")
        cell = record.get(field, "")

        try:
            if op == "is_empty":
                return str(cell).strip() == ""
            if op == "not_empty":
                return str(cell).strip() != ""

            if op in (">", "<", ">=", "<="):
                fv = float(str(cell).replace(",", "").strip())
                cv = float(str(cond_val).replace(",", "").strip())
                if op == ">":  return fv > cv
                if op == "<":  return fv < cv
                if op == ">=": return fv >= cv
                if op == "<=": return fv <= cv

            sv, scv = str(cell).strip(), str(cond_val).strip()
            if op == "==":         return sv == scv
            if op == "!=":         return sv != scv
            if op == "contains":   return scv in sv
            if op == "startswith": return sv.startswith(scv)
            if op == "endswith":   return sv.endswith(scv)
        except Exception:
            pass
        return True  # 解析失败则保留该行

    return [r for r in records if all(_match(r, rule) for rule in filter_rules)]


def process_by_profile(extracted_raw: dict, profile: dict):
    task_mode = profile.get("task_mode", "single_record")

    if task_mode == "table_records":
        result = process_table_records(extracted_raw, profile)
    else:
        result = process_single_record(extracted_raw, profile)

    # 应用 filter_rules（只对有 records 列表的结果有效）
    filter_rules = profile.get("filter_rules", [])
    if filter_rules and isinstance(result, dict) and isinstance(result.get("records"), list):
        original_count = len(result["records"])
        result["records"] = _apply_filter_rules(result["records"], filter_rules)
        filtered_count = len(result["records"])
        if "metadata" not in result:
            result["metadata"] = {}
        result["metadata"]["filter_rules_applied"] = len(filter_rules)
        result["metadata"]["filtered_out"] = original_count - filtered_count

    return result