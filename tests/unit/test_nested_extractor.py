"""
嵌套结构提取器测试
"""

import pytest
import sys
import os
import json
from unittest.mock import Mock, patch

# 添加src目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../src'))

from core.nested_extractor import (
    NestedExtractor,
    NestedExtractorConfig,
    NestedEntity,
    extract_nested_entities,
    flatten_nested_entities
)


class TestNestedEntity:
    """嵌套实体类测试"""

    def test_initialization(self):
        """测试初始化"""
        entity = NestedEntity(
            entity_type="department",
            name="技术部",
            attributes={"headcount": 50},
            confidence=0.9
        )

        assert entity.type == "department"
        assert entity.name == "技术部"
        assert entity.attributes["headcount"] == 50
        assert entity.confidence == 0.9
        assert entity.children == []

    def test_to_dict(self):
        """测试转换为字典"""
        child = NestedEntity("team", "前端团队", {"members": 10})
        entity = NestedEntity(
            entity_type="department",
            name="技术部",
            attributes={"headcount": 50},
            children=[child]
        )

        entity_dict = entity.to_dict()

        assert entity_dict["type"] == "department"
        assert entity_dict["name"] == "技术部"
        assert entity_dict["attributes"]["headcount"] == 50
        assert len(entity_dict["children"]) == 1
        assert entity_dict["children"][0]["type"] == "team"

    def test_from_dict(self):
        """测试从字典创建"""
        entity_dict = {
            "type": "department",
            "name": "技术部",
            "attributes": {"headcount": 50},
            "children": [
                {"type": "team", "name": "前端团队", "attributes": {"members": 10}, "children": []}
            ],
            "confidence": 0.9
        }

        entity = NestedEntity.from_dict(entity_dict)

        assert entity.type == "department"
        assert entity.name == "技术部"
        assert entity.attributes["headcount"] == 50
        assert len(entity.children) == 1
        assert entity.children[0].name == "前端团队"

    def test_flatten(self):
        """测试扁平化"""
        child1 = NestedEntity("employee", "张三", {"role": "工程师"})
        child2 = NestedEntity("employee", "李四", {"role": "架构师"})
        team = NestedEntity("team", "前端团队", {"members": 2}, [child1, child2])
        dept = NestedEntity("department", "技术部", {"headcount": 50}, [team])

        flat_entities = dept.flatten()

        assert len(flat_entities) == 4  # 部门 + 团队 + 2个员工
        assert flat_entities[0]["name"] == "技术部"
        assert flat_entities[0]["depth"] == 0
        assert flat_entities[1]["name"] == "前端团队"
        assert flat_entities[1]["depth"] == 1
        assert flat_entities[2]["name"] == "张三"
        assert flat_entities[2]["depth"] == 2

    def test_add_child(self):
        """测试添加子实体"""
        parent = NestedEntity("department", "技术部")
        child = NestedEntity("team", "前端团队")

        parent.add_child(child)

        assert len(parent.children) == 1
        assert parent.children[0].name == "前端团队"

    def test_find_entities_by_type(self):
        """测试按类型查找实体"""
        child1 = NestedEntity("employee", "张三")
        child2 = NestedEntity("employee", "李四")
        team = NestedEntity("team", "前端团队", children=[child1, child2])
        dept = NestedEntity("department", "技术部", children=[team])

        employees = dept.find_entities_by_type("employee")

        assert len(employees) == 2
        assert {e.name for e in employees} == {"张三", "李四"}


class TestNestedExtractor:
    """嵌套结构提取器测试"""

    def setup_method(self):
        """测试初始化"""
        self.config = NestedExtractorConfig(
            max_depth=3,
            min_confidence=0.7,
            enable_debug=True
        )
        self.extractor = NestedExtractor(self.config)

    def test_initialization(self):
        """测试初始化"""
        assert self.extractor.config == self.config
        assert self.extractor._entity_cache == {}

    def test_extract_nested_entities_empty_text(self):
        """测试空文本提取"""
        result = self.extractor.extract_nested_entities("")

        assert "error" in result["metadata"]
        assert result["entities"] == []
        assert result["relationships"] == []

    def test_validate_extraction_result(self):
        """测试提取结果验证"""
        # 有效结果
        valid_result = {
            "entities": [
                {"type": "department", "name": "技术部", "attributes": {}}
            ],
            "relationships": []
        }
        assert self.extractor._validate_extraction_result(valid_result)

        # 无效结果：缺少entities
        invalid_result = {"relationships": []}
        assert not self.extractor._validate_extraction_result(invalid_result)

        # 无效结果：entities不是列表
        invalid_result = {"entities": "not a list"}
        assert not self.extractor._validate_extraction_result(invalid_result)

        # 无效结果：实体缺少必需字段
        invalid_result = {
            "entities": [{"name": "技术部"}]  # 缺少type字段
        }
        assert not self.extractor._validate_extraction_result(invalid_result)

    def test_identify_entity_by_rules(self):
        """测试基于规则的实体识别"""
        # 测试部门识别
        entity = self.extractor._identify_entity_by_rules(
            "技术部门: 研发中心",
            ["department", "team"]
        )
        assert entity is not None
        assert entity["type"] == "department"
        assert "技术" in entity["name"]

        # 测试员工识别
        entity = self.extractor._identify_entity_by_rules(
            "员工: 张三",
            ["employee", "manager"]
        )
        assert entity is not None
        assert entity["type"] == "employee"

        # 测试无匹配
        entity = self.extractor._identify_entity_by_rules(
            "随机文本",
            ["department", "team"]
        )
        assert entity is None

    def test_merge_entity_group(self):
        """测试实体组合并"""
        entities = [
            {
                "type": "department",
                "name": "技术部",
                "attributes": {"headcount": 50},
                "children": [{"type": "team", "name": "团队A"}],
                "confidence": 0.8
            },
            {
                "type": "department",
                "name": "技术部",
                "attributes": {"location": "北京"},
                "children": [{"type": "team", "name": "团队B"}],
                "confidence": 0.9
            }
        ]

        merged = self.extractor._merge_entity_group(entities)

        assert merged["type"] == "department"
        assert merged["name"] == "技术部"
        assert merged["attributes"]["headcount"] == 50
        assert merged["attributes"]["location"] == "北京"
        assert len(merged["children"]) == 2  # 两个团队的子实体应该合并
        assert 0.8 <= merged["confidence"] <= 0.9  # 置信度应该是平均值

    def test_merge_similar_entities(self):
        """测试相似实体合并"""
        extraction_result = {
            "entities": [
                {"type": "department", "name": "技术部", "attributes": {"headcount": 50}},
                {"type": "department", "name": "技术部", "attributes": {"location": "北京"}},
                {"type": "department", "name": "市场部", "attributes": {"headcount": 30}}
            ],
            "relationships": [],
            "metadata": {}
        }

        merged_result = self.extractor._merge_similar_entities(extraction_result)

        # 应该合并为2个部门（技术部合并，市场部单独）
        assert len(merged_result["entities"]) == 2

        # 检查技术部是否合并了属性
        tech_dept = next(e for e in merged_result["entities"] if e["name"] == "技术部")
        assert "headcount" in tech_dept["attributes"]
        assert "location" in tech_dept["attributes"]

    def test_calculate_max_depth(self):
        """测试最大深度计算"""
        entities = [
            {
                "type": "department",
                "name": "技术部",
                "children": [
                    {
                        "type": "team",
                        "name": "前端团队",
                        "children": [
                            {"type": "employee", "name": "张三", "children": []}
                        ]
                    }
                ]
            }
        ]

        depth = self.extractor._calculate_max_depth(entities)
        assert depth == 3  # 部门(1) -> 团队(2) -> 员工(3)

    def test_flatten_nested_entities(self):
        """测试嵌套实体扁平化"""
        nested_result = {
            "entities": [
                {
                    "type": "department",
                    "name": "技术部",
                    "attributes": {"headcount": 50},
                    "children": [
                        {
                            "type": "team",
                            "name": "前端团队",
                            "attributes": {"members": 5},
                            "children": []
                        }
                    ]
                }
            ],
            "relationships": []
        }

        flat_entities = self.extractor.flatten_nested_entities(nested_result)

        assert len(flat_entities) == 2  # 部门 + 团队
        assert flat_entities[0]["entity_type"] == "department"
        assert flat_entities[0]["path"] == "技术部"
        assert flat_entities[0]["depth"] == 0
        assert flat_entities[1]["entity_type"] == "team"
        assert flat_entities[1]["path"] == "技术部/前端团队"
        assert flat_entities[1]["depth"] == 1

    def test_extract_and_flatten(self):
        """测试提取并扁平化"""
        test_text = """
        技术部门:
          前端团队:
            张三 - 工程师
            李四 - 架构师
          后端团队:
            王五 - 工程师
        """

        # 模拟模型提取结果
        mock_result = {
            "entities": [
                {
                    "type": "department",
                    "name": "技术部门",
                    "children": [
                        {
                            "type": "team",
                            "name": "前端团队",
                            "children": [
                                {"type": "employee", "name": "张三", "attributes": {"role": "工程师"}},
                                {"type": "employee", "name": "李四", "attributes": {"role": "架构师"}}
                            ]
                        },
                        {
                            "type": "team",
                            "name": "后端团队",
                            "children": [
                                {"type": "employee", "name": "王五", "attributes": {"role": "工程师"}}
                            ]
                        }
                    ]
                }
            ],
            "relationships": []
        }

        with patch.object(self.extractor, 'extract_nested_entities', return_value=mock_result):
            flat_entities = self.extractor.extract_and_flatten(test_text)

            assert len(flat_entities) == 5  # 1部门 + 2团队 + 2员工
            employee_names = {e["entity_name"] for e in flat_entities if e["entity_type"] == "employee"}
            assert employee_names == {"张三", "李四", "王五"}

    def test_extract_nested_entities_with_cache(self):
        """测试带缓存的实体提取"""
        test_text = "技术部门: 研发中心"
        cache_key = self.extractor._compute_text_hash(test_text, ["department"])

        # 第一次调用，应该计算并缓存
        mock_result = {
            "entities": [{"type": "department", "name": "技术部门"}],
            "relationships": [],
            "metadata": {"extraction_method": "test"}
        }

        with patch.object(self.extractor, '_extract_with_rules', return_value=mock_result):
            result1 = self.extractor.extract_nested_entities(test_text, ["department"])
            assert cache_key in self.extractor._entity_cache

            # 第二次调用，应该使用缓存
            result2 = self.extractor.extract_nested_entities(test_text, ["department"])
            assert result1 == result2

    @patch('core.nested_extractor.re.search')
    @patch('core.nested_extractor.json.loads')
    def test_extract_with_model_success(self, mock_json_loads, mock_re_search):
        """测试模型提取成功"""
        # 模拟模型响应
        mock_response = {
            "content": '{"entities": [{"type": "department", "name": "技术部"}], "relationships": []}'
        }

        mock_re_search.return_value = Mock(group=Mock(return_value=mock_response["content"]))
        mock_json_loads.return_value = {
            "entities": [{"type": "department", "name": "技术部"}],
            "relationships": []
        }

        model_client = Mock()
        model_client.call_model.return_value = mock_response

        result = self.extractor._extract_with_model(
            "技术部门相关信息",
            ["department"],
            model_client
        )

        assert result is not None
        assert len(result["entities"]) == 1
        assert result["entities"][0]["type"] == "department"

    def test_extract_with_rules_basic(self):
        """测试基于规则的基本提取"""
        test_text = """
        部门: 技术部
          团队: 前端团队
            员工: 张三
            员工: 李四
          团队: 后端团队
            员工: 王五
        """

        result = self.extractor._extract_with_rules(test_text, ["department", "team", "employee"])

        assert "entities" in result
        assert result["metadata"]["extraction_method"] == "rule_based"
        # 至少应该提取到一些实体
        assert len(result["entities"]) > 0


class TestHelperFunctions:
    """辅助函数测试"""

    def test_extract_nested_entities_function(self):
        """测试提取嵌套实体快捷函数"""
        test_text = "技术部门: 研发中心"

        with patch('core.nested_extractor.NestedExtractor') as mock_class:
            mock_instance = Mock()
            mock_class.return_value = mock_instance
            mock_instance.extract_nested_entities.return_value = {"entities": []}

            result = extract_nested_entities(test_text)

            mock_class.assert_called_once()
            mock_instance.extract_nested_entities.assert_called_once_with(test_text, None, None)

    def test_flatten_nested_entities_function(self):
        """测试扁平化嵌套实体快捷函数"""
        nested_result = {"entities": []}

        with patch('core.nested_extractor.NestedExtractor') as mock_class:
            mock_instance = Mock()
            mock_class.return_value = mock_instance
            mock_instance.flatten_nested_entities.return_value = []

            result = flatten_nested_entities(nested_result)

            mock_class.assert_called_once()
            mock_instance.flatten_nested_entities.assert_called_once_with(nested_result, True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])