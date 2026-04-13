"""
泛化字段后处理框架

通过 JSON 规则文件配置，支持任意字段类型的清洗、单位换算、格式化。
优先级：字段规则 > 类型规则 > 默认规则。
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

# 配置管理 - 简化版本（不使用ConfigManager）
_config = None  # 不再使用ConfigManager，直接使用环境变量

logger = logging.getLogger(__name__)

_DEFAULT_RULES_PATH = Path(__file__).parent.parent / "knowledge" / "field_normalization_rules.json"


class FieldNormalizer:
    """基于 JSON 配置的字段规范化器。

    规则链顺序：
    1. 去空白（默认开启）
    2. 去逗号（若配置）
    3. 提取数字（若配置了正则）
    4. 单位换算（若配置）
    5. 格式化输出（若配置）
    """

    # 默认实例（用于向后兼容，但不是严格的单例）
    _default_instance: Optional["FieldNormalizer"] = None
    _default_lock = None

    def __init__(self, config_path: Optional[str] = None):
        """初始化字段规范化器

        Args:
            config_path: 规则配置文件路径，None时使用默认配置
        """
        if config_path is None:
            # 从环境变量或config.py获取配置路径
            import os
            # 首先尝试环境变量
            env_path = os.environ.get("A23_NORMALIZATION_CONFIG")
            if env_path:
                config_path = env_path
            else:
                # 尝试从config.py获取
                try:
                    import src.config as config_module
                    config_path = config_module.NORMALIZATION_CONFIG
                except (ImportError, AttributeError):
                    # 使用默认路径
                    config_path = str(_DEFAULT_RULES_PATH)

        self._config_path = config_path
        self._rules: Optional[dict] = None
        self._annotation_re = None
        self._load_config()

    def _load_config(self):
        """加载配置规则"""
        self._rules = self._load_rules(self._config_path)
        self._annotation_re = self._build_annotation_re()

    def reload(self, config_path: Optional[str] = None):
        """重新加载配置

        Args:
            config_path: 新的配置文件路径，None时使用当前路径
        """
        if config_path is not None:
            self._config_path = config_path
        self._load_config()

    def _build_annotation_re(self):
        """从已加载的规则中构建标注关键字正则（动态、可配置）"""
        patterns = (self._rules or {}).get("annotation_patterns", [])
        if patterns:
            joined = "|".join(patterns)
            return re.compile(rf'[（(]\s*(?:{joined})\s*[）)]')
        return None

    @classmethod
    def get_default(cls) -> "FieldNormalizer":
        """获取默认实例（用于向后兼容）

        注意：这不是严格的单例，每次调用可能返回新实例。
        推荐在长期运行的应用中创建并复用FieldNormalizer实例。
        """
        if cls._default_instance is None:
            cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def reset_default(cls, config_path: Optional[str] = None):
        """重置默认实例（主要用于测试）"""
        cls._default_instance = None if config_path is None else cls(config_path)

    def _load_rules(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                rules = json.load(f)
            logger.debug(f"字段规范化规则已加载: {path}")
            return rules
        except FileNotFoundError:
            logger.warning(f"规范化规则文件不存在: {path}，使用空规则")
            return {}
        except Exception as e:
            logger.warning(f"加载规范化规则失败: {e}，使用空规则")
            return {}

    def normalize(self, field_name: str, raw_value: str, field_type: Optional[str] = None) -> Optional[str]:
        """规范化字段值。

        Args:
            field_name: 字段名（用于查找字段级规则）
            raw_value: 原始字符串值
            field_type: 字段类型（覆盖字段规则中的 type）

        Returns:
            规范化后的字符串，或 None（无法处理，调用方应回退）
        """
        if not raw_value or not isinstance(raw_value, str):
            return None

        if not self._rules:
            return None

        # ── 1. 查找适用规则（字段级 > 类型级 > 默认）─────────────────────
        field_rule = dict(self._rules.get("fields", {}).get(field_name, {}))
        effective_type = field_type or field_rule.get("type")
        type_rule = dict(self._rules.get("types", {}).get(effective_type, {})) if effective_type else {}
        default_rule = dict(self._rules.get("default", {}))

        # 合并规则（字段级覆盖类型级，类型级覆盖默认）
        merged = {**default_rule, **type_rule, **field_rule}
        # 单位换算：字段规则的 unit_conversions 完全覆盖类型规则
        if "unit_conversions" in field_rule:
            merged["unit_conversions"] = field_rule["unit_conversions"]
        elif "unit_conversions" in type_rule:
            merged["unit_conversions"] = type_rule["unit_conversions"]

        # ── 2. 应用规则链 ──────────────────────────────────────────────────
        value = raw_value

        # 去空白
        if merged.get("strip_whitespace", True):
            value = value.strip()

        # 清理 LLM 附加的括号标注（如（修正值）、（预估）等）——从配置动态加载
        if self._annotation_re is not None:
            value = self._annotation_re.sub('', value).strip()

        # 特殊处理：只保留数字（电话）
        if merged.get("keep_digits_only"):
            value = re.sub(r"[^\d]", "", value)
            return value if value else None

        # 去逗号（千分位）
        if merged.get("remove_commas"):
            value = value.replace(",", "").replace("，", "")

        # 日期规范化
        if effective_type == "date" or merged.get("output_format") in ("YYYY-MM-DD",):
            normalized = self._normalize_date(value, merged)
            if normalized:
                return normalized
            return None

        # 提取数字（含负号）
        number_regex = merged.get("extract_number_regex")
        if number_regex:
            match = re.search(number_regex, value)
            if not match:
                return None
            numeric_str = match.group()

            # 单位换算（从原始值中检测单位）
            unit_conversions = merged.get("unit_conversions", {})
            if unit_conversions:
                factor = self._detect_unit_factor(raw_value, unit_conversions)
                if factor and factor != 1:
                    try:
                        num = float(numeric_str)
                        result = num * factor
                        # 保留合理精度
                        if result == int(result):
                            numeric_str = str(int(result))
                        else:
                            numeric_str = f"{result:.6g}"
                    except ValueError:
                        pass

            # 格式化输出
            output_fmt = merged.get("output_format", "")
            if output_fmt and "{value}" in output_fmt:
                return output_fmt.replace("{value}", numeric_str)
            return numeric_str

        # 无特殊规则：仅返回清洗后的值（有变化才返回，否则返回 None 让调用方处理）
        if value != raw_value.strip():
            return value
        return None

    def _detect_unit_factor(self, text: str, unit_conversions: dict) -> Optional[float]:
        """从文本中检测单位并返回换算系数。

        按单位长度从长到短匹配，避免"亿"匹配到"百亿"中的"亿"。
        """
        for unit in sorted(unit_conversions.keys(), key=len, reverse=True):
            if unit in text:
                return unit_conversions[unit]
        return None

    def _normalize_date(self, value: str, rule: dict) -> Optional[str]:
        """将日期字符串规范化为 YYYY-MM-DD 格式。"""
        separators = rule.get("normalize_separators", ["年", "月", "日", "号", "/"])
        v = value
        # 替换各种分隔符为 -
        for sep in separators:
            v = v.replace(sep, "-")
        v = re.sub(r"\s+", "", v)
        v = v.rstrip("-")

        # 匹配 YYYY-M-D 或 YYYY-MM-DD
        match = re.match(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", v)
        if match:
            year = match.group(1)
            month = match.group(2).zfill(2)
            day = (match.group(3) or "01").zfill(2)
            return f"{year}-{month}-{day}"
        return None
