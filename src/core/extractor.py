"""
通用提取器 — 以 Docling 为唯一文档解析入口

主流程：
1. Docling 解析文档（文本 + 表格 DataFrame）
2. 表格数据按模板字段进行别名映射 → records
3. 非表格文本调用 LLM 补充缺失字段
4. 去重合并输出
"""

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Any, Optional

from src.adapters.model_client import call_model
from src.adapters.parser_factory import get_parser

logger = logging.getLogger(__name__)

# 字段别名解析器（懒加载）
_alias_map: Optional[dict] = None


def _get_alias_map() -> dict:
    global _alias_map
    if _alias_map is None:
        try:
            from src.core.alias import load_alias_map, build_reverse_alias_map
            raw = load_alias_map()
            _alias_map = build_reverse_alias_map(raw)
        except Exception as e:
            logger.warning(f"字段别名加载失败: {e}")
            _alias_map = {}
    return _alias_map


def resolve_column(col_name: str) -> str:
    """将表格列名映射到模板规范字段名（含模糊匹配）"""
    try:
        from src.core.alias import resolve_field_name, load_alias_map
        alias_map = load_alias_map()
        return resolve_field_name(col_name, alias_map)
    except Exception:
        return col_name


@dataclass
class UniversalResult:
    """提取结果"""
    records: List[Dict[str, Any]] = field(default_factory=list)
    validation: Dict[str, Any] = field(default_factory=dict)


class UniversalExtractor:
    """通用文档提取器

    Docling 是唯一文档解析入口，表格数据经别名解析后直接转换为 records。
    """

    def __init__(self, kb=None, config: Optional[Dict] = None):
        self.kb = kb
        self.config = config or {}

    # ─────────────────────────────────────────────────────────────────────────
    # 主接口
    # ─────────────────────────────────────────────────────────────────────────

    def extract_from_document(
        self,
        document_path: str,
        profile: Optional[Dict] = None,
        parser_type: Optional[str] = None,
    ) -> UniversalResult:
        """从文件中提取结构化数据（主入口）

        Args:
            document_path: 文档路径
            profile: 模板配置（含 fields 列表）
            parser_type: 忽略，保留向后兼容签名

        Returns:
            UniversalResult
        """
        start = time.time()
        path = Path(document_path)
        template_fields: List[Dict] = []
        if profile and isinstance(profile.get("fields"), list):
            template_fields = profile["fields"]

        result = UniversalResult(validation={
            "document_path": str(path),
            "errors": [],
        })

        # ── 解析文档 ──────────────────────────────────────────────────────
        parser = get_parser(path)
        if parser is None:
            result.validation["errors"].append(f"不支持的文件格式: {path.suffix}")
            return result

        try:
            parse_result = parser.parse(path)
        except Exception as e:
            result.validation["errors"].append(f"解析失败: {e}")
            return result

        if parse_result.get("error"):
            result.validation["warnings"] = [parse_result["error"]]

        # ── 表格 → records（P0 核心逻辑）─────────────────────────────────
        table_records = self._tables_to_records(
            parse_result.get("tables_dataframes", []),
            parse_result.get("tables", []),
            template_fields,
        )
        result.records.extend(table_records)

        # ── LLM 文本抽取模式控制 ──────────────────────────────────────────
        llm_mode = self.config.get("llm_mode", "full")
        field_names = [f["name"] if isinstance(f, dict) else f for f in template_fields]
        doc_text = parse_result.get("text", "")

        if llm_mode == "off" or not doc_text:
            llm_records = []
        elif llm_mode == "full":
            # 始终调用 LLM 全文抽取，再与表格结果融合
            llm_records = self._extract_from_text(doc_text, field_names, profile)
        else:
            # "supplement"：仅在字段不完整时调用 LLM
            missing = self._find_missing_fields(result.records, field_names)
            llm_records = self._extract_from_text(doc_text, field_names, profile) if missing else []

        # ── 融合 table_records + llm_records ──────────────────────────────
        if llm_records:
            if result.records:
                if llm_mode == "full":
                    # 全量融合：合并后去重，优先保留表格数据中的非空值
                    combined = list(result.records) + llm_records
                    key_fields = profile.get("dedup_key_fields") if profile else None
                    result.records = self._merge_by_key(combined, key_fields)
                else:
                    # supplement 模式：只补充缺失字段
                    missing = self._find_missing_fields(result.records, field_names)
                    for rec in result.records:
                        for lr in llm_records:
                            for f in missing:
                                if not rec.get(f) and lr.get(f):
                                    rec[f] = lr[f]
            else:
                result.records = llm_records

        # ── 补全缺失字段为空字符串 ────────────────────────────────────────
        if field_names:
            for rec in result.records:
                for f in field_names:
                    if f not in rec:
                        rec[f] = ""

        # ── 去重 ──────────────────────────────────────────────────────────
        result.records = self._deduplicate(result.records)

        result.validation.update({
            "total_records": len(result.records),
            "table_records": len(table_records),
            "processing_time_ms": int((time.time() - start) * 1000),
        })

        return result

    def extract(self, text: str, profile: Optional[Dict] = None) -> UniversalResult:
        """从文本中提取（纯文本模式，无 Docling）"""
        start = time.time()
        fields = []
        if profile and isinstance(profile.get("fields"), list):
            for f in profile["fields"]:
                fields.append(f["name"] if isinstance(f, dict) else f)

        if not fields:
            return UniversalResult(
                validation={"errors": ["未定义提取字段"], "processing_time_ms": 0}
            )

        records = self._extract_from_text(text, fields, profile)

        # 补全缺失字段
        for rec in records:
            for f in fields:
                if f not in rec:
                    rec[f] = ""

        return UniversalResult(
            records=records,
            validation={
                "total_records": len(records),
                "processing_time_ms": int((time.time() - start) * 1000),
                "errors": [],
            },
        )

    # 向后兼容别名
    def extract_document_with_adapters(
        self,
        document_path: str,
        template_path: Optional[str] = None,
        template_mode: str = "auto",
        template_description: Optional[str] = None,
        output_path: Optional[str] = None,
        use_llm: bool = False,
    ) -> UniversalResult:
        profile = None
        if template_path or template_description:
            try:
                from src.core.profile import generate_profile_from_template
                profile = generate_profile_from_template(
                    template_path=template_path,
                    use_llm=use_llm,
                    mode=template_mode,
                    user_description=template_description,
                )
            except Exception as e:
                logger.warning(f"生成 profile 失败: {e}")

        result = self.extract_from_document(document_path, profile)

        if output_path and template_path and result.records:
            try:
                self._write_to_template(result.records, template_path, output_path)
            except Exception as e:
                logger.warning(f"写入模板失败: {e}")

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 表格转换（P0）
    # ─────────────────────────────────────────────────────────────────────────

    def _tables_to_records(
        self,
        dataframes: list,
        tables_raw: List[Dict],
        template_fields: List[Dict],
    ) -> List[Dict]:
        """将 Docling 提取的表格转换为按模板字段映射的 records"""
        if not template_fields:
            return []

        field_names = [f["name"] if isinstance(f, dict) else f for f in template_fields]
        records = []

        # 优先使用 DataFrame（更可靠）
        if dataframes:
            for df in dataframes:
                recs = self._df_to_records(df, field_names)
                records.extend(recs)

        # 回退：使用 tables_raw 中的 data 列表
        elif tables_raw:
            for table in tables_raw:
                data = table.get("data", [])
                if not data:
                    continue
                import pandas as pd
                try:
                    df = pd.DataFrame(data)
                    recs = self._df_to_records(df, field_names)
                    records.extend(recs)
                except Exception:
                    # 手动映射
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        rec = self._map_row(row, field_names)
                        if rec:
                            records.append(rec)

        return records

    def _df_to_records(self, df, field_names: List[str]) -> List[Dict]:
        """DataFrame → records，列名经别名映射后筛选"""
        if df is None or df.empty:
            return []

        logger.debug(f"表格原始列名: {list(df.columns)}, 模板字段: {field_names}")

        # 构建列名映射：原始列 → 规范字段名
        col_mapping = {}
        for col in df.columns:
            canonical = resolve_column(str(col))
            logger.debug(f"列名映射: '{col}' -> '{canonical}'")
            if canonical in field_names:
                col_mapping[col] = canonical

        # 模糊映射失败时，回退到直接匹配（列名恰好等于字段名）
        if not col_mapping:
            for col in df.columns:
                col_str = str(col).strip()
                if col_str in field_names:
                    col_mapping[col] = col_str
            if col_mapping:
                logger.info(f"模糊映射无结果，使用直接匹配: {col_mapping}")

        if not col_mapping:
            logger.warning(f"表格列名无法匹配任何模板字段，跳过此表格。列名: {list(df.columns)}")
            return []

        logger.info(f"表格列名映射成功: {col_mapping}")
        mapped = df[list(col_mapping.keys())].rename(columns=col_mapping)

        records = []
        for _, row in mapped.iterrows():
            rec = {f: (str(row[f]).strip() if row[f] is not None and str(row[f]) != "nan" else "")
                   for f in field_names if f in mapped.columns}
            # 补全缺失字段
            for f in field_names:
                if f not in rec:
                    rec[f] = ""
            # 只收录至少有一个非空字段的行
            if any(v for v in rec.values()):
                records.append(rec)

        return records

    def _map_row(self, row: Dict, field_names: List[str]) -> Optional[Dict]:
        """将单行字典的键经别名解析映射到模板字段"""
        rec = {}
        for col, val in row.items():
            canonical = resolve_column(str(col))
            if canonical in field_names:
                rec[canonical] = str(val).strip() if val is not None else ""
        for f in field_names:
            if f not in rec:
                rec[f] = ""
        if any(v for v in rec.values()):
            return rec
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # LLM 文本提取（辅助）
    # ─────────────────────────────────────────────────────────────────────────

    def _extract_from_text(
        self, text: str, fields: List[str], profile: Optional[Dict]
    ) -> List[Dict]:
        """调用 LLM 从非结构化文本中提取字段"""
        if not text or not fields:
            return []

        model_type = self.config.get("model_type")
        total_deadline = self.config.get("total_deadline")
        prompt = self._build_prompt(text[:12000], fields)

        try:
            raw = call_model(prompt, model_type=model_type, total_deadline=total_deadline)
            return self._parse_llm_output(raw, fields)
        except Exception as e:
            logger.warning(f"LLM 提取失败: {e}")
            return []

    def _build_prompt(self, text: str, fields: List[str]) -> str:
        fields_str = "、".join(fields)
        return (
            f"请从以下文本中提取结构化信息，只提取这些字段：{fields_str}\n\n"
            f"文本：\n```\n{text}\n```\n\n"
            f"以 JSON 格式返回，格式：\n"
            f'{{"records": [{{"字段名": "值", ...}}]}}\n'
            f"若无法找到某字段，该字段值填空字符串。"
        )

    def _parse_llm_output(self, raw: Any, expected_fields: List[str]) -> List[Dict]:
        if not raw:
            return []
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            # 提取 JSON
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                return []
            try:
                data = json.loads(match.group())
            except Exception:
                return []
        else:
            return []

        records = data.get("records", [])
        if not records and isinstance(data, dict):
            records = [data]

        result = []
        for rec in records:
            if isinstance(rec, dict) and any(v for v in rec.values()):
                result.append(rec)
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 工具方法
    # ─────────────────────────────────────────────────────────────────────────

    def _merge_by_key(self, records: List[Dict], key_fields: Optional[List[str]] = None) -> List[Dict]:
        """基于关键字段融合去重。表格记录（优先）与 LLM 记录合并，相同键取非空值覆盖空值。"""
        if not records:
            return records
        if not key_fields:
            first = next((r for r in records if isinstance(r, dict)), {})
            key_fields = [k for k, v in first.items() if v and not k.startswith("_")]
        if not key_fields:
            return self._deduplicate(records)

        merged: dict = {}
        unkeyed: list = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            key_vals = [str(rec.get(k, "")) for k in key_fields]
            key_str = "|".join(key_vals)
            if not any(v.strip() for v in key_vals):
                rec = dict(rec)
                rec["_unkeyed"] = True
                unkeyed.append(rec)
                continue
            if key_str not in merged:
                merged[key_str] = dict(rec)
            else:
                for f, val in rec.items():
                    if f.startswith("_"):
                        continue
                    if val and not merged[key_str].get(f):
                        merged[key_str][f] = val
        return list(merged.values()) + unkeyed

    def _find_missing_fields(self, records: List[Dict], field_names: List[str]) -> List[str]:
        if not records or not field_names:
            return field_names if not records else []
        covered = set()
        for rec in records:
            for f, v in rec.items():
                if v:
                    covered.add(f)
        return [f for f in field_names if f not in covered]

    def _deduplicate(self, records: List[Dict]) -> List[Dict]:
        seen = set()
        result = []
        for rec in records:
            key = json.dumps(rec, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                result.append(rec)
        return result

    def _write_to_template(self, records: List[Dict], template_path: str, output_path: str):
        from src.core.writers import fill_excel_table, fill_excel_vertical
        if template_path.lower().endswith((".xlsx", ".xls")):
            if len(records) > 1:
                fill_excel_table(records, template_path, output_path)
            else:
                fill_excel_vertical(records[0], template_path, output_path)


# ── 全局实例（向后兼容） ─────────────────────────────────────────────────────
_global_extractor: Optional[UniversalExtractor] = None


def get_global_extractor() -> UniversalExtractor:
    global _global_extractor
    if _global_extractor is None:
        _global_extractor = UniversalExtractor()
    return _global_extractor
