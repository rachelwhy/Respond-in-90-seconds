"""
自适应输出格式 — 使用LLM分析文档结构选择最佳输出格式

核心功能：
1. 分析文档结构和抽取结果，智能选择输出格式
2. 支持多种输出格式：表格、树形、JSON、Markdown等
3. 基于模型推理的格式选择策略
4. 可配置的格式转换规则

使用场景：
- 复杂非结构化文本难以形成二维表格时
- 嵌套结构、混合实体需要保留层次关系时
- 用户未指定输出格式，需要自动选择时

依赖：
- 现有的模型调用接口 (src/adapters/model_client.py)
"""

import io
import json
import logging
import re
from typing import List, Dict, Any, Optional, Tuple, Union, Literal
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class OutputFormat(Enum):
    """输出格式枚举"""
    TABLE = "table"  # 二维表格 (适合规整数据)
    TREE = "tree"    # 树形结构 (适合层次数据)
    JSON = "json"    # 原生JSON (适合复杂嵌套)
    MARKDOWN = "markdown"  # Markdown表格/列表
    CSV = "csv"      # CSV格式
    EXCEL = "excel"  # Excel文件
    HTML = "html"    # HTML表格


@dataclass
class FormatConfig:
    """输出格式配置"""
    # 格式选择策略
    selection_strategy: str = "model_based"  # 可选: "model_based", "rule_based", "fixed"
    default_format: OutputFormat = OutputFormat.TABLE
    # 模型调用配置
    model_type: str = "ollama"
    format_selection_prompt: str = field(default_factory=lambda: """
请分析以下文档结构和抽取结果，选择最合适的输出格式。

文档结构特征：
{structure_features}

抽取结果特征：
{result_features}

可选的输出格式：
1. table - 二维表格：适合规整的行列数据，字段数量固定，记录间结构一致
2. tree - 树形结构：适合层次化数据，有父子关系或嵌套结构
3. json - 原生JSON：适合复杂嵌套对象，字段值可能是数组或对象
4. markdown - Markdown：适合文档展示，支持表格和列表
5. csv - CSV格式：适合数据交换，简单的行列结构
6. excel - Excel文件：适合复杂表格，支持多Sheet和公式

请根据以下标准选择：
- 数据规整度：字段是否固定且一致
- 结构复杂度：是否有嵌套或层次关系
- 字段多样性：字段类型是否多样（文本、数字、日期等）
- 记录数量：记录数量多少

请只返回格式名称（table/tree/json/markdown/csv/excel），不要返回其他内容。
""")
    # 规则配置
    min_records_for_table: int = 3
    max_nesting_level: int = 3
    # 调试配置
    enable_debug: bool = False


class OutputFormatter:
    """自适应输出格式化器

    使用LLM分析文档结构和抽取结果，选择最佳输出格式。
    """

    def __init__(self, config: Optional[FormatConfig] = None):
        self.config = config or FormatConfig()

    def format_output(
        self,
        records: List[Dict[str, Any]],
        document_structure: Optional[Dict[str, Any]] = None,
        profile: Optional[Dict[str, Any]] = None,
        model_client=None
    ) -> Dict[str, Any]:
        """格式化输出结果

        Args:
            records: 抽取的记录列表
            document_structure: 文档结构信息（可选）
            profile: 模板profile信息（可选）
            model_client: 模型客户端实例（可选）

        Returns:
            格式化后的输出结果，包含格式信息和数据
        """
        if not records:
            return {
                "format": self.config.default_format.value,
                "data": [],
                "metadata": {"record_count": 0, "format_reason": "空记录"}
            }

        # 1. 分析文档结构和抽取结果特征
        structure_features = self._analyze_structure_features(document_structure) if document_structure else {}
        result_features = self._analyze_result_features(records)

        # 2. 选择输出格式
        selected_format = self._select_output_format(
            structure_features, result_features, profile, model_client
        )

        # 3. 根据选定格式转换数据
        formatted_data = self._convert_to_format(records, selected_format, profile)

        # 4. 返回结果
        return {
            "format": selected_format.value,
            "data": formatted_data,
            "metadata": {
                "record_count": len(records),
                "selected_format": selected_format.value,
                "structure_features": structure_features,
                "result_features": result_features,
                "format_reason": self._explain_format_selection(selected_format, structure_features, result_features, profile)
            }
        }

    def _analyze_structure_features(self, document_structure: Dict[str, Any]) -> Dict[str, Any]:
        """分析文档结构特征"""
        features = {
            "has_tables": False,
            "has_lists": False,
            "has_headings": False,
            "has_paragraphs": False,
            "structure_type": "unknown",
            "complexity_score": 0.0
        }

        try:
            # 检查文档结构类型
            doc_type = document_structure.get("type", "")
            if "table" in doc_type.lower():
                features["has_tables"] = True
                features["structure_type"] = "tabular"
                features["complexity_score"] += 0.3
            elif "list" in doc_type.lower():
                features["has_lists"] = True
                features["structure_type"] = "list"
                features["complexity_score"] += 0.2
            else:
                features["structure_type"] = "text"

            # 检查章节结构
            sections = document_structure.get("sections", [])
            if sections:
                features["has_headings"] = True
                features["complexity_score"] += 0.1 * min(len(sections), 5)  # 最多0.5

            # 检查段落
            paragraphs = document_structure.get("paragraphs", [])
            if paragraphs:
                features["has_paragraphs"] = True
                features["complexity_score"] += 0.1

            # 检查嵌套深度
            max_depth = document_structure.get("max_depth", 0)
            if max_depth > 1:
                features["complexity_score"] += 0.1 * min(max_depth, 5)

        except Exception as e:
            logger.warning(f"文档结构特征分析失败: {e}")

        return features

    def _analyze_result_features(self, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """分析抽取结果特征"""
        if not records:
            return {"record_count": 0, "field_count": 0, "is_tabular": False}

        first_record = records[0]
        all_fields = set(first_record.keys())

        # 收集所有记录的字段
        for record in records[1:]:
            all_fields.update(record.keys())

        # 检查字段一致性
        field_consistency = {}
        for field in all_fields:
            field_present = sum(1 for r in records if field in r)
            field_consistency[field] = field_present / len(records)

        # 检查字段类型（简单推断）
        field_types = {}
        for field in all_fields:
            sample_values = [r.get(field) for r in records if field in r][:5]
            if sample_values:
                field_types[field] = self._infer_field_type(sample_values)

        # 检查嵌套结构
        has_nested = any(
            isinstance(r.get(field), (dict, list))
            for r in records
            for field in r.keys()
        )

        # 检查数组字段
        has_arrays = any(
            isinstance(r.get(field), list)
            for r in records
            for field in r.keys()
        )

        # 计算表格适合度
        is_tabular = (
            len(records) >= self.config.min_records_for_table and
            not has_nested and
            max(field_consistency.values(), default=0) > 0.7
        )

        return {
            "record_count": len(records),
            "field_count": len(all_fields),
            "all_fields": list(all_fields),
            "field_consistency": field_consistency,
            "field_types": field_types,
            "has_nested": has_nested,
            "has_arrays": has_arrays,
            "is_tabular": is_tabular,
            "complexity_score": self._calculate_result_complexity(records)
        }

    def _infer_field_type(self, sample_values: List[Any]) -> str:
        """推断字段类型"""
        if not sample_values:
            return "unknown"

        first_value = sample_values[0]

        # 检查是否为None
        if first_value is None:
            # 检查其他样本值
            for value in sample_values[1:]:
                if value is not None:
                    # 使用第一个非None值重新推断
                    return self._infer_field_type([value])
            return "unknown"

        # 检查是否为嵌套结构
        if isinstance(first_value, dict):
            return "object"
        elif isinstance(first_value, list):
            return "array"

        # 检查常见类型
        str_value = str(first_value).strip()
        if not str_value:  # 空字符串
            return "text"

        # 数字类型（支持整数、小数、科学计数法）
        # 匹配：-123, 123.45, -123.45, 1.23e4, -1.23e-4
        number_pattern = r'^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?$'
        if re.match(number_pattern, str_value):
            # 检查是否为科学计数法或包含小数点
            if '.' in str_value or 'e' in str_value.lower():
                return "float"
            else:
                return "integer"

        # 百分比（数字后跟%符号）
        percentage_pattern = r'^[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?\s*%$'
        if re.match(percentage_pattern, str_value):
            return "percentage"

        # 日期类型（更全面的模式匹配）
        date_patterns = [
            # YYYY-MM-DD, YYYY/MM/DD
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}$',
            # DD-MM-YYYY, DD/MM/YYYY
            r'^\d{1,2}[-/]\d{1,2}[-/]\d{4}$',
            # YYYY年MM月DD日
            r'^\d{4}年\d{1,2}月\d{1,2}日$',
            # MM-DD-YYYY (美式)
            r'^\d{1,2}[-/]\d{1,2}[-/]\d{4}$',
            # 带时间的日期
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2} \d{1,2}:\d{2}(?::\d{2})?$',
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$'  # ISO格式
        ]
        for pattern in date_patterns:
            if re.match(pattern, str_value):
                return "date"

        # 布尔类型
        if str_value.lower() in ('true', 'false', 'yes', 'no', '是', '否', '1', '0'):
            return "boolean"

        # 默认为文本
        return "text"

    def _calculate_result_complexity(self, records: List[Dict[str, Any]]) -> float:
        """计算结果复杂度分数"""
        if not records:
            return 0.0

        complexity = 0.0
        first_record = records[0]

        # 字段数量
        field_count = len(first_record.keys())
        complexity += 0.1 * min(field_count / 10, 1.0)  # 最多0.1

        # 记录数量
        record_count = len(records)
        complexity += 0.1 * min(record_count / 100, 1.0)  # 最多0.1

        # 检查嵌套
        for record in records[:5]:  # 采样检查
            for value in record.values():
                if isinstance(value, (dict, list)):
                    complexity += 0.3
                    break

        # 字段值长度差异
        if record_count > 1:
            length_variances = []
            for field in first_record.keys():
                lengths = [len(str(r.get(field, ''))) for r in records if field in r]
                if lengths:
                    avg_len = sum(lengths) / len(lengths)
                    if avg_len > 0:
                        variance = sum((l - avg_len) ** 2 for l in lengths) / len(lengths)
                        length_variances.append(variance / avg_len)

            if length_variances:
                avg_variance = sum(length_variances) / len(length_variances)
                complexity += 0.2 * min(avg_variance, 1.0)

        return min(complexity, 1.0)

    def _select_output_format(
        self,
        structure_features: Dict[str, Any],
        result_features: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None,
        model_client=None
    ) -> OutputFormat:
        """选择输出格式"""
        # 检查profile中是否有指定格式
        if profile and "output_format" in profile:
            try:
                return OutputFormat(profile["output_format"])
            except ValueError:
                logger.warning(f"profile中的输出格式无效: {profile['output_format']}")

        # 根据策略选择格式
        if self.config.selection_strategy == "fixed":
            return self.config.default_format

        elif self.config.selection_strategy == "rule_based":
            return self._select_format_by_rules(structure_features, result_features)

        elif self.config.selection_strategy == "model_based" and model_client:
            return self._select_format_by_model(structure_features, result_features, model_client)

        else:
            # 默认回退到规则选择
            return self._select_format_by_rules(structure_features, result_features)

    def _select_format_by_rules(
        self,
        structure_features: Dict[str, Any],
        result_features: Dict[str, Any]
    ) -> OutputFormat:
        """基于规则选择输出格式"""
        # 规则1：如果有嵌套结构，使用JSON或树形
        if result_features.get("has_nested", False):
            nesting_depth = structure_features.get("max_depth", 1)
            if nesting_depth > 2:
                return OutputFormat.TREE
            else:
                return OutputFormat.JSON

        # 规则2：如果是表格数据，使用表格格式
        if result_features.get("is_tabular", False):
            record_count = result_features.get("record_count", 0)
            if record_count > 50:
                return OutputFormat.CSV  # 大数据集用CSV
            else:
                return OutputFormat.TABLE

        # 规则3：如果有列表结构，使用Markdown
        if structure_features.get("has_lists", False):
            return OutputFormat.MARKDOWN

        # 规则4：默认使用表格
        return OutputFormat.TABLE

    def _select_format_by_model(
        self,
        structure_features: Dict[str, Any],
        result_features: Dict[str, Any],
        model_client
    ) -> OutputFormat:
        """使用模型选择输出格式"""
        try:
            # 准备prompt
            prompt = self.config.format_selection_prompt.format(
                structure_features=json.dumps(structure_features, ensure_ascii=False, indent=2),
                result_features=json.dumps(result_features, ensure_ascii=False, indent=2)
            )

            # 调用模型
            response = model_client.call_model(
                messages=[{"role": "user", "content": prompt}],
                model_type=self.config.model_type,
                temperature=0.1,
                max_tokens=50
            )

            if response and "content" in response:
                format_text = response["content"].strip().lower()

                # 解析模型响应
                for format_enum in OutputFormat:
                    if format_enum.value in format_text:
                        logger.info(f"模型选择的输出格式: {format_enum.value}")
                        return format_enum

            logger.warning(f"模型响应无法解析: {response}")

        except Exception as e:
            logger.warning(f"模型格式选择失败: {e}")

        # 模型失败时回退到规则选择
        return self._select_format_by_rules(structure_features, result_features)

    def _convert_to_format(
        self,
        records: List[Dict[str, Any]],
        output_format: OutputFormat,
        profile: Optional[Dict[str, Any]] = None
    ) -> Any:
        """将记录转换为指定格式"""
        if output_format == OutputFormat.TABLE:
            return self._convert_to_table(records, profile)
        elif output_format == OutputFormat.TREE:
            return self._convert_to_tree(records, profile)
        elif output_format == OutputFormat.JSON:
            return self._convert_to_json(records, profile)
        elif output_format == OutputFormat.MARKDOWN:
            return self._convert_to_markdown(records, profile)
        elif output_format == OutputFormat.CSV:
            return self._convert_to_csv(records, profile)
        elif output_format == OutputFormat.EXCEL:
            return self._convert_to_excel(records, profile)
        elif output_format == OutputFormat.HTML:
            return self._convert_to_html(records, profile)
        else:
            # 默认返回原始记录
            return records

    def _convert_to_table(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> List[List[Any]]:
        """转换为表格格式（二维数组）"""
        if not records:
            return []

        # 获取所有字段
        all_fields = set()
        for record in records:
            all_fields.update(record.keys())

        # 确定字段顺序（优先使用profile中的字段顺序）
        field_order = []
        if profile and "fields" in profile:
            profile_fields = [f.get("name") for f in profile["fields"] if isinstance(f, dict)]
            field_order = [f for f in profile_fields if f in all_fields]

        # 添加未在profile中的字段
        remaining_fields = sorted(all_fields - set(field_order))
        field_order.extend(remaining_fields)

        # 构建表格
        table = [field_order]  # 表头
        for record in records:
            row = [record.get(field, "") for field in field_order]
            table.append(row)

        return table

    def _convert_to_tree(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """转换为树形结构"""
        # 简单实现：按第一个字段分组
        if not records or len(records) < 2:
            return {"root": records}

        # 尝试找到合适的分组字段
        first_record = records[0]
        candidate_fields = []

        for field, value in first_record.items():
            if isinstance(value, str) and len(value) < 50:  # 短文本适合分组
                unique_values = len(set(r.get(field) for r in records if field in r))
                if 1 < unique_values < len(records) / 2:  # 有区分度但不至于太多分组
                    candidate_fields.append((field, unique_values))

        if candidate_fields:
            # 选择唯一值数量适中的字段
            candidate_fields.sort(key=lambda x: abs(x[1] - len(records) / 10))  # 接近记录数/10
            group_field = candidate_fields[0][0]

            # 按字段分组
            tree = {}
            for record in records:
                group_key = record.get(group_field, "其他")
                if group_key not in tree:
                    tree[group_key] = []
                tree[group_key].append(record)

            return {
                "group_by": group_field,
                "tree": tree
            }
        else:
            # 无法分组，返回扁平结构
            return {"root": records}

    def _convert_to_json(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """转换为JSON格式（保持原始结构）"""
        return records

    def _convert_to_markdown(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> str:
        """转换为Markdown格式"""
        if not records:
            return ""

        # 获取所有字段
        all_fields = set()
        for record in records:
            all_fields.update(record.keys())

        # 确定字段顺序（优先使用profile中的字段顺序）
        field_order = []
        if profile and "fields" in profile:
            profile_fields = [f.get("name") for f in profile["fields"] if isinstance(f, dict)]
            field_order = [f for f in profile_fields if f in all_fields]

        # 添加未在profile中的字段（按字母顺序）
        remaining_fields = sorted(all_fields - set(field_order))
        field_order.extend(remaining_fields)

        # 构建Markdown表格
        md_lines = []

        # 表头
        header = "| " + " | ".join(field_order) + " |"
        separator = "| " + " | ".join(["---"] * len(field_order)) + " |"
        md_lines.extend([header, separator])

        # 数据行
        for record in records:
            row_cells = []
            for field in field_order:
                value = record.get(field, "")
                if value is None:
                    value = ""
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                row_cells.append(str(value))
            md_lines.append("| " + " | ".join(row_cells) + " |")

        return "\n".join(md_lines)

    def _convert_to_csv(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> str:
        """转换为CSV格式"""
        if not records:
            return ""

        # 获取所有字段
        all_fields = set()
        for record in records:
            all_fields.update(record.keys())

        # 确定字段顺序（优先使用profile中的字段顺序）
        field_order = []
        if profile and "fields" in profile:
            profile_fields = [f.get("name") for f in profile["fields"] if isinstance(f, dict)]
            field_order = [f for f in profile_fields if f in all_fields]

        # 添加未在profile中的字段（按字母顺序）
        remaining_fields = sorted(all_fields - set(field_order))
        field_order.extend(remaining_fields)

        # 构建CSV
        import csv

        output = io.StringIO()
        writer = csv.writer(output)

        # 写入表头
        writer.writerow(field_order)

        # 写入数据
        for record in records:
            row = []
            for field in field_order:
                value = record.get(field, "")
                if value is None:
                    value = ""
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                row.append(str(value))
            writer.writerow(row)

        return output.getvalue()

    def _convert_to_excel(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> bytes:
        """转换为Excel格式（返回字节）"""
        # 简化的Excel转换，实际应用中可能需要更复杂的逻辑
        try:
            import pandas as pd

            # 获取所有字段并确定顺序（与其他转换方法保持一致）
            all_fields = set()
            for record in records:
                all_fields.update(record.keys())

            # 确定字段顺序（优先使用profile中的字段顺序）
            field_order = []
            if profile and "fields" in profile:
                profile_fields = [f.get("name") for f in profile["fields"] if isinstance(f, dict)]
                field_order = [f for f in profile_fields if f in all_fields]

            # 添加未在profile中的字段（按字母顺序）
            remaining_fields = sorted(all_fields - set(field_order))
            field_order.extend(remaining_fields)

            # 重新排序记录中的字段
            ordered_records = []
            for record in records:
                ordered_record = {field: record.get(field, "") for field in field_order}
                ordered_records.append(ordered_record)

            df = pd.DataFrame(ordered_records)
            output = io.BytesIO()
            df.to_excel(output, index=False)
            return output.getvalue()
        except ImportError:
            logger.warning("pandas未安装，无法生成Excel文件")
            return b""

    def _convert_to_html(self, records: List[Dict[str, Any]], profile: Optional[Dict[str, Any]]) -> str:
        """转换为HTML表格"""
        if not records:
            return "<table></table>"

        # 获取所有字段
        all_fields = set()
        for record in records:
            all_fields.update(record.keys())

        # 确定字段顺序（优先使用profile中的字段顺序）
        field_order = []
        if profile and "fields" in profile:
            profile_fields = [f.get("name") for f in profile["fields"] if isinstance(f, dict)]
            field_order = [f for f in profile_fields if f in all_fields]

        # 添加未在profile中的字段（按字母顺序）
        remaining_fields = sorted(all_fields - set(field_order))
        field_order.extend(remaining_fields)

        # 构建HTML
        html_lines = ['<table border="1" style="border-collapse: collapse;">']

        # 表头
        html_lines.append('<thead><tr>')
        for field in field_order:
            html_lines.append(f'<th>{field}</th>')
        html_lines.append('</tr></thead>')

        # 数据行
        html_lines.append('<tbody>')
        for record in records:
            html_lines.append('<tr>')
            for field in field_order:
                value = record.get(field, "")
                if value is None:
                    value = ""
                elif isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                html_lines.append(f'<td>{value}</td>')
            html_lines.append('</tr>')
        html_lines.append('</tbody></table>')

        return "\n".join(html_lines)

    def _explain_format_selection(
        self,
        selected_format: OutputFormat,
        structure_features: Dict[str, Any],
        result_features: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None
    ) -> str:
        """解释格式选择的原因"""
        reasons = []

        if selected_format == OutputFormat.TABLE:
            reasons.append("数据规整，适合二维表格展示")
            if result_features.get("is_tabular", False):
                reasons.append(f"字段一致性高，记录数量适中 ({result_features.get('record_count', 0)}条)")
        elif selected_format == OutputFormat.TREE:
            reasons.append("数据具有层次结构")
            if result_features.get("has_nested", False):
                reasons.append("存在嵌套字段")
        elif selected_format == OutputFormat.JSON:
            reasons.append("数据结构复杂，需要保留完整信息")
            if structure_features.get("complexity_score", 0) > 0.5:
                reasons.append("文档结构复杂")
        elif selected_format == OutputFormat.MARKDOWN:
            reasons.append("适合文档展示和阅读")
            if structure_features.get("has_lists", False):
                reasons.append("文档包含列表结构")
        elif selected_format == OutputFormat.CSV:
            reasons.append("数据量大，适合数据交换")
            if result_features.get("record_count", 0) > 50:
                reasons.append(f"记录数量多 ({result_features.get('record_count', 0)}条)")
        elif selected_format == OutputFormat.EXCEL:
            reasons.append("需要复杂表格功能")
            if profile and "excel_specific" in profile:
                reasons.append("profile指定需要Excel格式")

        return "; ".join(reasons) if reasons else "基于默认规则选择"


# 快捷函数
def format_output_adaptive(
    records: List[Dict[str, Any]],
    document_structure: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    model_client=None
) -> Dict[str, Any]:
    """自适应格式化输出的快捷函数"""
    formatter = OutputFormatter()
    return formatter.format_output(records, document_structure, profile, model_client)