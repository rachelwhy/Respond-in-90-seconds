"""
Profile 生成器 — 从模板文件或自然语言描述生成提取配置
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, Any

from src.core.template_detector import detect_template_structure
from src.core.alias import resolve_field_names

logger = logging.getLogger(__name__)


# ── 字段类型自动推断 ────────────────────────────────────────────────────────

# 从 field_normalization_rules.json 加载已知字段→类型映射（启动时一次性加载）
_RULES_TYPE_CACHE: Optional[Dict[str, str]] = None


def _load_rules_type_map() -> Dict[str, str]:
    """从 field_normalization_rules.json 读取 fields 段的字段→类型映射"""
    global _RULES_TYPE_CACHE
    if _RULES_TYPE_CACHE is not None:
        return _RULES_TYPE_CACHE
    _RULES_TYPE_CACHE = {}
    try:
        rules_path = Path(__file__).parent.parent / "knowledge" / "field_normalization_rules.json"
        with open(rules_path, "r", encoding="utf-8") as f:
            rules = json.load(f)
        for field_name, field_rule in rules.get("fields", {}).items():
            if isinstance(field_rule, dict) and "type" in field_rule:
                _RULES_TYPE_CACHE[field_name] = field_rule["type"]
    except Exception:
        pass
    return _RULES_TYPE_CACHE


def _infer_field_type(field_name: str, unit: str = "") -> str:
    """根据字段名和单位自动推断字段类型

    推断策略（按优先级）：
    1. 有单位 → 通过单位内容判断（带数量/金额单位=number，带%=percentage）
    2. 字段名在 field_normalization_rules.json 中有定义 → 用定义的类型
    3. 字段名通过别名解析后在规则中有定义 → 继承类型
    4. 兜底 → text
    """
    # 1. 单位推断：有单位的字段几乎一定是数值或百分比
    if unit:
        clean_unit = unit.strip()
        if "%" in clean_unit or "％" in clean_unit or "‰" in clean_unit:
            return "percentage"
        # 有任何非空单位 → 数值类型（通用规则，覆盖元/万/吨/km等所有场景）
        if clean_unit:
            return "number"

    # 2. 查已知字段规则
    rules_map = _load_rules_type_map()
    if field_name in rules_map:
        return rules_map[field_name]

    # 3. 通过别名解析后再查规则
    try:
        from src.core.alias import load_alias_map, resolve_field_name
        alias_map = load_alias_map()
        canonical = resolve_field_name(field_name, alias_map, fuzzy_threshold=80)
        if canonical != field_name and canonical in rules_map:
            return rules_map[canonical]
    except Exception:
        pass

    return "text"


def _enrich_fields(raw_fields: list, resolved_fields: list) -> tuple:
    """批量构建字段定义并自动推断关键字段

    Returns:
        (fields_list, dedup_key_fields)
        关键字段识别策略：表格中第一个 text 类型字段视为去重 key。
    """
    _unit_re = re.compile(r'[（(]([^）)]{1,10})[）)]')
    fields = []
    seen_names = set()
    first_text_field = None

    for raw, name in zip(raw_fields, resolved_fields):
        if name in seen_names:
            logger.warning(f"字段名重复: '{raw}' 解析为 '{name}'（已存在），回退使用原始列名")
            name = raw
        seen_names.add(name)

        m = _unit_re.search(raw)
        unit = m.group(1).strip() if m else ""
        field_type = _infer_field_type(name, unit)

        f: dict = {"name": name, "type": field_type}
        if unit:
            f["unit"] = unit
        fields.append(f)

        if first_text_field is None and field_type == "text":
            first_text_field = name

    # 关键字段启发式：第一个 text 字段作为 dedup key 并标记 required
    dedup_key_fields = []
    if first_text_field:
        for f in fields:
            if f["name"] == first_text_field:
                f["required"] = True
                dedup_key_fields.append(first_text_field)
                break

    return fields, dedup_key_fields


def generate_profile_from_template(
    template_path: str = None,
    use_llm: bool = False,
    mode: str = "auto",
    user_description: str = None,
) -> dict:
    """从模板文件或自然语言描述生成 profile

    优先级：template_path（文件解析） > user_description（LLM） > 默认字段
    """
    if mode in ("file", "auto") and template_path:
        try:
            detected = detect_template_structure(template_path)
            raw_fields = detected.pop("field_names", [])
            resolved = resolve_field_names(raw_fields)

            fields, dedup_key_fields = _enrich_fields(raw_fields, resolved)

            profile = {
                "report_name": Path(template_path).stem,
                "template_path": _rel_path(template_path),
                "instruction": _default_instruction(detected.get("task_mode", "table_records"), resolved),
                "task_mode": detected.get("task_mode", "table_records"),
                "template_mode": detected.get("template_mode", "excel_table"),
                "fields": fields,
            }
            if dedup_key_fields:
                profile["dedup_key_fields"] = dedup_key_fields
            profile.update({k: v for k, v in detected.items() if k not in profile})
            return profile
        except Exception as e:
            logger.warning(f"模板文件解析失败: {e}")
            if mode == "file":
                raise

    if user_description:
        return _profile_from_llm(user_description, template_path)

    return _default_profile(template_path)


def generate_profile_smart(
    template_path: str,
    instruction: str,
    document_sample: str = "",
) -> dict:
    """智能 profile 生成：LLM 分析模板内容 + 用户指令 + 文档样本"""
    template_content = _read_template_text(template_path) if template_path else "（无模板文件）"
    doc_part = f"\n\n【输入文档样本】\n{document_sample[:2000]}" if document_sample.strip() else ""

    prompt = f"""你是数据抽取配置专家。请分析以下模板和用户指令，生成JSON格式的抽取配置。

【模板内容】
{template_content}

【用户指令】
{instruction}{doc_part}

输出JSON，必须包含：
- "task_mode": "table_records" 或 "single_record"
- "template_mode": "excel_table" / "word_table" / "vertical"
- "fields": 字段数组，每个字段包含：
  - "name": 字段名（简洁明确）
  - "type": 字段类型，从以下选择：text（文本）、number（数值）、date（日期）、percentage（百分比/比率）、phone（电话号码）
  - "unit": 单位（可选，如"亿元"、"万人"、"%"）
- "instruction": 详细抽取说明

只输出JSON，不要其他文字："""

    try:
        from src.adapters.model_client import call_model
        result = call_model(prompt)
        if isinstance(result, dict) and result.get("fields"):
            return _normalize_profile(result, template_path, instruction)
    except Exception as e:
        logger.error(f"智能 profile 生成失败: {e}")

    # 回退到规则模式
    try:
        return generate_profile_from_template(template_path=template_path, mode="auto")
    except Exception:
        return _default_profile(template_path)


# ── 内部工具 ─────────────────────────────────────────────────────────────────

def _profile_from_llm(description: str, template_path: Optional[str]) -> dict:
    """使用 LLM 从自然语言描述生成 profile"""
    try:
        from src.adapters.model_client import call_model
        prompt = (
            f"请根据以下描述生成数据抽取字段列表（JSON格式）：\n{description}\n\n"
            f'输出格式：{{"fields": [{{"name": "字段名", "type": "类型", "unit": "单位（可选）"}}]}}\n'
            f'type 可选值：text（文本）、number（数值）、date（日期）、percentage（百分比/比率）、phone（电话号码）\n'
            f'只输出JSON，不要其他文字：'
        )
        result = call_model(prompt)
        if isinstance(result, dict) and result.get("fields"):
            fields = result["fields"]
        else:
            fields = [{"name": "字段1", "type": "text"}, {"name": "字段2", "type": "text"}]
    except Exception as e:
        logger.warning(f"LLM 字段生成失败: {e}")
        fields = [{"name": "字段1", "type": "text"}, {"name": "字段2", "type": "text"}]

    # 二次推断：LLM 返回 text 类型的字段尝试用规则推断为更精确的类型
    enriched_fields = []
    for f in fields:
        if not isinstance(f, dict) or "name" not in f:
            continue
        name = str(f["name"]).strip()
        if not name:
            continue
        unit = str(f.get("unit", "")).strip()
        declared_type = f.get("type", "text")
        if declared_type == "text":
            declared_type = _infer_field_type(name, unit)
        entry = {"name": name, "type": declared_type}
        if unit:
            entry["unit"] = unit
        enriched_fields.append(entry)

    return {
        "report_name": Path(template_path).stem if template_path else "llm_generated",
        "template_path": _rel_path(template_path) if template_path else "llm_generated",
        "instruction": f"从文档中提取：{description}",
        "task_mode": "single_record",
        "template_mode": "llm_generated",
        "fields": enriched_fields if enriched_fields else fields,
    }


def _default_profile(template_path: Optional[str]) -> dict:
    return {
        "report_name": Path(template_path).stem if template_path else "default",
        "template_path": _rel_path(template_path) if template_path else "default",
        "instruction": "从文档中提取关键信息",
        "task_mode": "single_record",
        "template_mode": "default",
        "fields": [
            {"name": "名称", "type": "text"},
            {"name": "数值", "type": "number"},
            {"name": "单位", "type": "text"},
            {"name": "备注", "type": "text"},
        ],
    }


def generate_profile_from_document(
    document_text: str,
    max_sample: int = 3000,
) -> dict:
    """从文档内容自动推断最佳表格结构（无模板、无用户指令时使用）

    内置 LLM prompt，全自动分析文档类型和字段结构：
    - 若文档包含重复结构（多条同类实体/记录）→ table_records 模式，自动推断字段
    - 若文档为单一主题（报告/合同/公文）→ single_record 模式，提取关键信息字段
    - 若文档过于杂乱无结构 → 返回 QA 模式标记，仅做信息入库

    Returns:
        profile dict，额外包含 "_doc_type" 字段：
        - "structured_table": 可制表（多条记录）
        - "structured_single": 可制表（单条记录）
        - "unstructured_qa": 杂乱文档，仅做 QA 入库
    """
    sample = document_text[:max_sample] if document_text else ""
    if not sample.strip():
        return _default_profile(None)

    prompt = f"""你是一个文档结构分析专家。请分析以下文档内容，判断其结构类型并生成最优的数据提取配置。

【文档内容样本】
{sample}

请完成以下分析：

1. **文档类型判断**（三选一）：
   - "structured_table"：文档包含多个同类实体/条目（如城市列表、产品清单、人员名录、统计数据表），每个实体有相似的属性字段，适合提取为多行表格
   - "structured_single"：文档是单一主题（如一份合同、一篇报告、一个项目介绍），包含若干关键信息字段，适合提取为单条记录
   - "unstructured_qa"：文档内容杂乱或叙述性强，无法归纳为固定字段的结构化数据，仅适合作为问答知识库

2. **若为 structured_table 或 structured_single**，请设计最优的提取字段列表：
   - 字段名简洁明确（如"城市名称"、"GDP总量"、"常住人口"）
   - 字段类型标注（text/number/date/money/percentage）
   - 对于数值型字段，标注单位（如"亿元"、"万人"）
   - 字段数量适中：表格模式 5-15 个字段，单记录模式 3-10 个字段
   - 不要太简陋（至少覆盖文档中出现的主要信息维度）
   - 也不要过度拆分（合并可以归类的信息）

输出严格JSON格式：
{{
    "doc_type": "structured_table" 或 "structured_single" 或 "unstructured_qa",
    "task_mode": "table_records" 或 "single_record",
    "fields": [
        {{"name": "字段名", "type": "text/number/date", "unit": "单位（可选）"}}
    ],
    "instruction": "一句话描述提取任务（如：从文档中逐条提取各城市的经济指标数据）",
    "estimated_record_count": 数字（预估记录条数，仅 table_records 模式需要）
}}

只输出JSON，不要其他文字："""

    try:
        from src.adapters.model_client import call_model
        raw = call_model(prompt)

        # 解析LLM输出
        if isinstance(raw, str):
            import re as _re
            match = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if match:
                raw = json.loads(match.group())
            else:
                raise ValueError("LLM 输出中未找到 JSON")

        if not isinstance(raw, dict):
            raise ValueError(f"LLM 输出格式异常: {type(raw)}")

        doc_type = raw.get("doc_type", "unstructured_qa")
        task_mode = raw.get("task_mode", "single_record")
        fields_raw = raw.get("fields", [])
        instruction = raw.get("instruction", "从文档中提取关键信息")

        # QA 模式：返回最小化 profile + 标记
        if doc_type == "unstructured_qa":
            return {
                "report_name": "auto_qa",
                "template_path": "",
                "instruction": instruction or "将文档内容存储为知识库，供智能问答使用",
                "task_mode": "single_record",
                "template_mode": "generic",
                "_doc_type": "unstructured_qa",
                "fields": [
                    {"name": "文档标题", "type": "text"},
                    {"name": "摘要", "type": "text"},
                    {"name": "关键词", "type": "text"},
                    {"name": "主要内容", "type": "text"},
                ],
            }

        # 结构化模式：规范化字段
        fields = []
        seen = set()
        first_text_field = None
        for f in fields_raw:
            if not isinstance(f, dict) or "name" not in f:
                continue
            name = str(f["name"]).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            unit = str(f["unit"]).strip() if f.get("unit") else ""
            # LLM 已给出类型，但若为 text 则尝试推断
            declared_type = f.get("type", "text")
            if declared_type == "text":
                declared_type = _infer_field_type(name, unit)
            entry = {"name": name, "type": declared_type}
            if unit:
                entry["unit"] = unit
            fields.append(entry)
            if first_text_field is None and declared_type == "text":
                first_text_field = name

        # 关键字段启发式：第一个 text 字段
        dedup_key_fields = []
        if first_text_field:
            for f in fields:
                if f["name"] == first_text_field:
                    f["required"] = True
                    dedup_key_fields.append(first_text_field)
                    break

        if not fields:
            raise ValueError("LLM 未生成有效字段")

        profile = {
            "report_name": "auto_generated",
            "template_path": "",
            "instruction": instruction,
            "task_mode": task_mode if doc_type == "structured_table" else "single_record",
            "template_mode": "generic",
            "_doc_type": doc_type,
            "fields": fields,
        }
        if dedup_key_fields:
            profile["dedup_key_fields"] = dedup_key_fields

        estimated = raw.get("estimated_record_count")
        if estimated and isinstance(estimated, (int, float)):
            profile["_estimated_record_count"] = int(estimated)

        logger.info(f"自动文档分析: type={doc_type}, fields={len(fields)}, task_mode={profile['task_mode']}")
        return profile

    except Exception as e:
        logger.warning(f"文档自动分析失败: {e}，使用默认 profile")
        # 回退：尝试用简单启发式分析
        return _heuristic_profile_from_text(sample)


def _heuristic_profile_from_text(text: str) -> dict:
    """简单启发式文档分析（LLM 不可用时的回退）"""
    lines = text.strip().split('\n')
    # 检测是否有表格式结构（多行相似分隔符）
    separator_lines = sum(1 for l in lines if '|' in l or '\t' in l)
    numbered_lines = sum(1 for l in lines if re.match(r'^\s*\d+[\.\、]', l))

    if separator_lines > 3 or numbered_lines > 5:
        # 疑似表格或列表结构
        return {
            "report_name": "auto_generated",
            "template_path": "",
            "instruction": "从文档中提取所有条目的关键信息，每个条目一条记录",
            "task_mode": "table_records",
            "template_mode": "generic",
            "_doc_type": "structured_table",
            "fields": [
                {"name": "序号", "type": "text"},
                {"name": "名称", "type": "text"},
                {"name": "描述", "type": "text"},
                {"name": "数值", "type": "number"},
                {"name": "备注", "type": "text"},
            ],
        }

    return {
        "report_name": "auto_generated",
        "template_path": "",
        "instruction": "从文档中提取关键信息",
        "task_mode": "single_record",
        "template_mode": "generic",
        "_doc_type": "structured_single",
        "fields": [
            {"name": "标题", "type": "text"},
            {"name": "主题", "type": "text"},
            {"name": "关键信息", "type": "text"},
            {"name": "数据摘要", "type": "text"},
            {"name": "备注", "type": "text"},
        ],
    }


def _default_instruction(task_mode: str, fields) -> str:
    names = "、".join(fields[:6]) if fields else "各字段"
    if task_mode == "table_records":
        return f"从文档表格中逐行提取记录，包含字段：{names}"
    return f"从文档中提取以下信息：{names}"


def _rel_path(path: str) -> str:
    try:
        rel = os.path.relpath(path)
        return rel if ".." not in rel and len(rel) <= 100 else Path(path).name
    except Exception:
        return Path(path).name


def _normalize_profile(result: dict, template_path: Optional[str], instruction: str) -> dict:
    """标准化 LLM 返回的 profile，补充类型推断和关键字段识别"""
    fields = []
    seen = set()
    first_text_field = None
    _unit_re = re.compile(r'[（(]([^）)]{1,10})[）)]')
    for f in result.get("fields", []):
        if not isinstance(f, dict) or "name" not in f:
            continue
        name = str(f["name"]).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        # 保留 LLM 推断的类型，若为 text 则尝试自动推断
        unit = f.get("unit", "")
        if not unit:
            m = _unit_re.search(name)
            if m:
                unit = m.group(1).strip()
        declared_type = f.get("type", "text")
        if declared_type == "text":
            declared_type = _infer_field_type(name, unit)
        entry = {"name": name, "type": declared_type}
        if unit:
            entry["unit"] = unit
        if f.get("required"):
            entry["required"] = True
        fields.append(entry)
        if first_text_field is None and declared_type == "text":
            first_text_field = name

    # 关键字段启发式：第一个 text 字段
    dedup_key_fields = []
    if first_text_field:
        for f in fields:
            if f["name"] == first_text_field:
                f["required"] = True
                dedup_key_fields.append(first_text_field)
                break

    profile = {
        "report_name": Path(template_path).stem if template_path else "auto",
        "template_path": _rel_path(template_path) if template_path else "auto",
        "instruction": result.get("instruction") or instruction,
        "task_mode": result.get("task_mode", "table_records"),
        "template_mode": result.get("template_mode", "excel_table"),
        "fields": fields,
        "header_row": result.get("header_row", 1),
        "start_row": result.get("start_row", 2),
    }
    if dedup_key_fields:
        profile["dedup_key_fields"] = dedup_key_fields
    return profile


def _read_template_text(template_path: str) -> str:
    ext = Path(template_path).suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        try:
            from openpyxl import load_workbook
            wb = load_workbook(template_path, read_only=True, data_only=True)
            ws = wb.active
            lines = []
            for r in range(1, min((ws.max_row or 30), 30) + 1):
                row = [str(ws.cell(r, c).value or "").strip()
                       for c in range(1, min((ws.max_column or 20), 20) + 1)]
                row = [v for v in row if v]
                if row:
                    lines.append(" | ".join(row))
            return "\n".join(lines)
        except Exception as e:
            return f"（Excel 解析失败: {e}）"
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(template_path)
            parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()][:20]
            for i, t in enumerate(doc.tables[:3]):
                parts.append(f"【表格{i+1}】")
                for row in t.rows[:3]:
                    parts.append(" | ".join(c.text.strip() for c in row.cells))
            return "\n".join(parts)
        except Exception as e:
            return f"（Word 解析失败: {e}）"
    return f"（不支持格式: {ext}）"
