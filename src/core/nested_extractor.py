"""
嵌套结构处理 — 使用LLM识别和抽取嵌套实体

核心功能：
1. 识别文档中的嵌套结构（对象、数组、层次关系）
2. 抽取多级嵌套实体，保持结构完整性
3. 支持递归抽取和结构扁平化
4. 智能合并同类型嵌套实体

使用场景：
- 企业组织架构（部门->团队->员工）
- 产品目录（类别->子类别->产品）
- 项目计划（任务->子任务->工作项）
- 任何具有父子关系或层次结构的数据

依赖：
- 现有的模型调用接口 (src/adapters/model_client.py)
- 可选的实体识别库
"""

import json
import logging
import re
from typing import List, Dict, Any, Optional, Tuple, Union, Callable
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class NestedExtractorConfig:
    """嵌套结构提取器配置"""
    # 模型配置
    model_type: str = "ollama"
    temperature: float = 0.1
    max_tokens: int = 2000
    # 抽取配置
    max_depth: int = 5  # 最大嵌套深度
    min_confidence: float = 0.7  # 最小置信度
    enable_recursive_extraction: bool = True  # 启用递归抽取
    merge_similar_entities: bool = True  # 合并相似实体
    # 实体类型配置
    entity_types: List[str] = field(default_factory=lambda: [
        "organization", "department", "team", "employee",
        "product", "category", "item",
        "project", "task", "subtask",
        "location", "building", "room",
        "document", "section", "paragraph"
    ])
    # 提示词配置
    extraction_prompt_template: str = field(default_factory=lambda: """
请从以下文本中提取结构化信息。文本可能包含嵌套的实体关系。

文本内容：
{text}

需要提取的实体类型：{entity_types}

请按照以下JSON格式输出结果：
{{
  "entities": [
    {{
      "type": "实体类型",
      "name": "实体名称",
      "attributes": {{
        "字段1": "值1",
        "字段2": "值2"
      }},
      "children": [
        // 子实体数组，结构与父实体相同
      ]
    }}
  ],
  "relationships": [
    {{
      "source": "源实体名称",
      "target": "目标实体名称",
      "type": "关系类型"
    }}
  ]
}}

要求：
1. 只提取文本中明确提到的实体和关系
2. 保持嵌套结构的完整性
3. 如果实体有多个属性，请尽量提取完整
4. 对于不确定的信息，可以留空或使用null
5. 请确保输出是有效的JSON格式

请只返回JSON对象，不要返回其他内容。
""")
    # 调试配置
    enable_debug: bool = False


class NestedEntity:
    """嵌套实体类"""
    def __init__(
        self,
        entity_type: str,
        name: str,
        attributes: Optional[Dict[str, Any]] = None,
        children: Optional[List['NestedEntity']] = None,
        confidence: float = 1.0,
        source_text: Optional[str] = None
    ):
        self.type = entity_type
        self.name = name
        self.attributes = attributes or {}
        self.children = children or []
        self.confidence = confidence
        self.source_text = source_text

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "type": self.type,
            "name": self.name,
            "attributes": self.attributes,
            "children": [child.to_dict() for child in self.children],
            "confidence": self.confidence,
            "source_text": self.source_text
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NestedEntity':
        """从字典创建"""
        children = [cls.from_dict(child) for child in data.get("children", [])]
        return cls(
            entity_type=data["type"],
            name=data["name"],
            attributes=data.get("attributes", {}),
            children=children,
            confidence=data.get("confidence", 1.0),
            source_text=data.get("source_text")
        )

    def flatten(self, parent_path: str = "") -> List[Dict[str, Any]]:
        """扁平化嵌套实体"""
        flat_entities = []

        # 当前实体
        flat_entity = {
            "type": self.type,
            "name": self.name,
            "attributes": self.attributes,
            "confidence": self.confidence,
            "path": f"{parent_path}/{self.name}" if parent_path else self.name,
            "depth": parent_path.count('/') if parent_path else 0
        }
        flat_entities.append(flat_entity)

        # 递归处理子实体
        for child in self.children:
            child_path = f"{parent_path}/{self.name}" if parent_path else self.name
            flat_entities.extend(child.flatten(child_path))

        return flat_entities

    def add_child(self, child: 'NestedEntity'):
        """添加子实体"""
        self.children.append(child)

    def find_entities_by_type(self, entity_type: str) -> List['NestedEntity']:
        """查找指定类型的所有实体"""
        entities = []
        if self.type == entity_type:
            entities.append(self)

        for child in self.children:
            entities.extend(child.find_entities_by_type(entity_type))

        return entities


class NestedExtractor:
    """嵌套结构提取器

    使用LLM识别和抽取嵌套实体，支持多级嵌套结构。
    """

    def __init__(self, config: Optional[NestedExtractorConfig] = None):
        self.config = config or NestedExtractorConfig()
        self._entity_cache = {}  # 实体缓存，避免重复处理

    def extract_nested_entities(
        self,
        text: str,
        entity_types: Optional[List[str]] = None,
        model_client=None
    ) -> Dict[str, Any]:
        """从文本中提取嵌套实体

        Args:
            text: 输入文本
            entity_types: 关注的实体类型列表（可选）
            model_client: 模型客户端实例

        Returns:
            包含实体和关系的嵌套结构
        """
        if not text or not text.strip():
            return {"entities": [], "relationships": [], "metadata": {"error": "空文本"}}

        # 使用缓存（基于文本哈希）
        cache_key = self._compute_text_hash(text, entity_types)
        if cache_key in self._entity_cache:
            logger.debug("使用缓存结果")
            return self._entity_cache[cache_key]

        # 确定实体类型
        target_entity_types = entity_types or self.config.entity_types

        # 优先使用模型提取
        extraction_result = None
        if model_client:
            extraction_result = self._extract_with_model(text, target_entity_types, model_client)

        # 如果模型提取失败或未启用，使用规则提取
        if not extraction_result:
            extraction_result = self._extract_with_rules(text, target_entity_types)

        # 后处理：合并相似实体
        if self.config.merge_similar_entities and extraction_result["entities"]:
            extraction_result = self._merge_similar_entities(extraction_result)

        # 缓存结果
        self._entity_cache[cache_key] = extraction_result

        return extraction_result

    def _extract_with_model(
        self,
        text: str,
        entity_types: List[str],
        model_client
    ) -> Optional[Dict[str, Any]]:
        """使用模型提取嵌套实体"""
        try:
            # 准备prompt
            prompt = self.config.extraction_prompt_template.format(
                text=text[:5000],  # 限制文本长度
                entity_types=", ".join(entity_types)
            )

            # 调用模型
            response = model_client.call_model(
                messages=[{"role": "user", "content": prompt}],
                model_type=self.config.model_type,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens
            )

            if response and "content" in response:
                content = response["content"]

                # 尝试解析JSON
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    json_str = json_match.group(0)
                    result = json.loads(json_str)

                    # 验证结果格式
                    if self._validate_extraction_result(result):
                        logger.info(f"模型成功提取 {len(result.get('entities', []))} 个实体")
                        return result
                    else:
                        logger.warning("模型返回结果格式无效")

            logger.warning(f"模型响应无法解析: {response}")

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
        except Exception as e:
            logger.warning(f"模型提取失败: {e}")

        return None

    def _extract_with_rules(self, text: str, entity_types: List[str]) -> Dict[str, Any]:
        """使用规则提取嵌套实体（回退方法）"""
        entities = []
        relationships = []

        # 简单规则：基于关键词和模式匹配
        lines = text.split('\n')
        current_parent = None
        indent_levels = {}

        for i, line in enumerate(lines):
            line = line.strip()
            if not line:
                continue

            # 检测缩进级别（简单实现）
            indent = len(line) - len(line.lstrip())
            level = 0
            for indent_key in sorted(indent_levels.keys(), reverse=True):
                if indent > indent_key:
                    level = indent_levels[indent_key] + 1
                    break

            indent_levels[indent] = level

            # 尝试识别实体
            entity = self._identify_entity_by_rules(line, entity_types)
            if entity:
                entity_obj = NestedEntity(
                    entity_type=entity["type"],
                    name=entity["name"],
                    attributes=entity.get("attributes", {}),
                    source_text=line,
                    confidence=entity.get("confidence", 0.5)
                )

                # 建立父子关系（基于缩进）
                if level > 0 and current_parent:
                    # 查找上一级别的父实体
                    parent_indent = None
                    for indent_key in sorted(indent_levels.keys()):
                        if indent_levels[indent_key] == level - 1:
                            parent_indent = indent_key
                            break

                    # 这里简化处理：将实体添加到最近的父实体
                    current_parent.add_child(entity_obj)
                else:
                    entities.append(entity_obj)
                    current_parent = entity_obj

        # 转换为字典格式
        result = {
            "entities": [e.to_dict() for e in entities],
            "relationships": relationships,
            "metadata": {
                "extraction_method": "rule_based",
                "entity_count": len(entities),
                "max_depth": self._calculate_max_depth(entities)
            }
        }

        return result

    def _identify_entity_by_rules(self, text: str, entity_types: List[str]) -> Optional[Dict[str, Any]]:
        """基于规则识别实体"""
        # 常见模式匹配
        patterns = {
            "organization": [r'(公司|集团|企业|机构)', r'^[A-Za-z]+(?:集团|公司)$'],
            "department": [r'(部门|事业部|中心|处|科|室)'],
            "employee": [r'(员工|职员|工作人员|姓名)[:：]\s*(\S+)', r'^[\u4e00-\u9fa5]{2,4}$'],
            "product": [r'(产品|商品|物品)[:：]\s*(\S+)', r'^[A-Za-z0-9]+-\d+$'],
            "task": [r'(任务|工作|事项)[:：]\s*(\S+)', r'^\d+\.\s+.+$'],
        }

        for entity_type in entity_types:
            if entity_type in patterns:
                for pattern in patterns[entity_type]:
                    match = re.search(pattern, text)
                    if match:
                        # 提取实体名称
                        name = match.group(1) if len(match.groups()) >= 1 else text[:50]

                        return {
                            "type": entity_type,
                            "name": name.strip(),
                            "attributes": {"source": text[:100]},
                            "confidence": 0.6
                        }

        # 如果没有匹配到特定模式，检查是否包含实体类型关键词
        for entity_type in entity_types:
            if entity_type in text.lower():
                # 提取可能的名字（第一个冒号后的内容或前几个词）
                name_match = re.search(r'[:：]\s*(\S+)', text)
                if name_match:
                    name = name_match.group(1)
                else:
                    # 取前几个词作为名称
                    words = text.split()
                    name = ' '.join(words[:3]) if len(words) > 3 else text[:30]

                return {
                    "type": entity_type,
                    "name": name.strip(),
                    "attributes": {},
                    "confidence": 0.4
                }

        return None

    def _validate_extraction_result(self, result: Dict[str, Any]) -> bool:
        """验证提取结果格式"""
        if not isinstance(result, dict):
            return False

        # 检查必需字段
        if "entities" not in result:
            return False

        if not isinstance(result["entities"], list):
            return False

        # 验证实体结构
        for entity in result["entities"]:
            if not isinstance(entity, dict):
                return False
            if "type" not in entity or "name" not in entity:
                return False

        return True

    def _merge_similar_entities(self, extraction_result: Dict[str, Any]) -> Dict[str, Any]:
        """合并相似的实体"""
        entities = extraction_result["entities"]
        if not entities:
            return extraction_result

        # 按类型和名称相似度分组
        entity_groups = defaultdict(list)

        for entity in entities:
            # 生成分组键（类型 + 名称的前几个字符）
            name_key = entity["name"][:20].lower() if entity["name"] else ""
            group_key = f"{entity['type']}:{name_key}"
            entity_groups[group_key].append(entity)

        # 合并每个组内的实体
        merged_entities = []
        for group_key, group_entities in entity_groups.items():
            if len(group_entities) == 1:
                merged_entities.append(group_entities[0])
            else:
                # 合并相似实体
                merged = self._merge_entity_group(group_entities)
                merged_entities.append(merged)

        # 更新结果
        extraction_result["entities"] = merged_entities
        extraction_result["metadata"]["merged_entities"] = len(entities) - len(merged_entities)

        return extraction_result

    def _merge_entity_group(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        """合并一组相似实体"""
        if not entities:
            return {}

        # 使用第一个实体作为基础
        base_entity = entities[0].copy()

        # 合并属性
        all_attributes = {}
        for entity in entities:
            attrs = entity.get("attributes", {})
            for key, value in attrs.items():
                if value and (key not in all_attributes or not all_attributes[key]):
                    all_attributes[key] = value

        base_entity["attributes"] = all_attributes

        # 合并子实体
        all_children = []
        for entity in entities:
            children = entity.get("children", [])
            all_children.extend(children)

        # 去除重复子实体
        if all_children:
            unique_children = []
            seen_children = set()
            for child in all_children:
                child_key = f"{child.get('type', '')}:{child.get('name', '')}"
                if child_key not in seen_children:
                    seen_children.add(child_key)
                    unique_children.append(child)

            base_entity["children"] = unique_children

        # 更新置信度（取平均值）
        confidences = [e.get("confidence", 0.5) for e in entities if "confidence" in e]
        if confidences:
            base_entity["confidence"] = sum(confidences) / len(confidences)

        return base_entity

    def _calculate_max_depth(self, entities: List[Dict[str, Any]]) -> int:
        """计算嵌套最大深度"""
        def get_depth(entity: Dict[str, Any], current_depth: int) -> int:
            max_depth = current_depth
            for child in entity.get("children", []):
                child_depth = get_depth(child, current_depth + 1)
                max_depth = max(max_depth, child_depth)
            return max_depth

        max_depth = 0
        for entity in entities:
            depth = get_depth(entity, 1)
            max_depth = max(max_depth, depth)

        return max_depth

    def _compute_text_hash(self, text: str, entity_types: Optional[List[str]]) -> str:
        """计算文本哈希"""
        import hashlib
        content = text[:1000]  # 只使用前1000字符
        if entity_types:
            content += ":" + ",".join(sorted(entity_types))
        return hashlib.md5(content.encode('utf-8')).hexdigest()

    def flatten_nested_entities(
        self,
        nested_result: Dict[str, Any],
        include_attributes: bool = True
    ) -> List[Dict[str, Any]]:
        """扁平化嵌套实体结果"""
        flat_records = []

        for entity_dict in nested_result.get("entities", []):
            entity = NestedEntity.from_dict(entity_dict)
            flat_entities = entity.flatten()

            for flat_entity in flat_entities:
                record = {
                    "entity_type": flat_entity["type"],
                    "entity_name": flat_entity["name"],
                    "path": flat_entity["path"],
                    "depth": flat_entity["depth"],
                    "confidence": flat_entity.get("confidence", 1.0)
                }

                if include_attributes:
                    record.update(flat_entity["attributes"])

                flat_records.append(record)

        return flat_records

    def extract_and_flatten(
        self,
        text: str,
        entity_types: Optional[List[str]] = None,
        model_client=None
    ) -> List[Dict[str, Any]]:
        """提取嵌套实体并扁平化（快捷方法）"""
        nested_result = self.extract_nested_entities(text, entity_types, model_client)
        return self.flatten_nested_entities(nested_result)


# 快捷函数
def extract_nested_entities(
    text: str,
    entity_types: Optional[List[str]] = None,
    model_client=None,
    config: Optional[NestedExtractorConfig] = None
) -> Dict[str, Any]:
    """提取嵌套实体的快捷函数"""
    extractor = NestedExtractor(config)
    return extractor.extract_nested_entities(text, entity_types, model_client)

def flatten_nested_entities(
    nested_result: Dict[str, Any],
    include_attributes: bool = True
) -> List[Dict[str, Any]]:
    """扁平化嵌套实体的快捷函数"""
    extractor = NestedExtractor()
    return extractor.flatten_nested_entities(nested_result, include_attributes)