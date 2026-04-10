"""
知识源抽象接口

定义统一的 KnowledgeSource 接口，支持不同的数据源（文件、数据库等）。
后端同学实现 DatabaseKnowledgeSource 时需继承此类并实现所有抽象方法。
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class KnowledgeSource(ABC):
    """知识源抽象基类

    所有知识数据（字段别名、实体词典等）的统一访问接口。
    """

    @abstractmethod
    def get_field_aliases(self) -> Dict[str, List[str]]:
        """获取字段别名映射

        Returns:
            字典：规范字段名 -> 别名列表
        """
        pass

    @abstractmethod
    def get_city_dict(self) -> Dict[str, List[str]]:
        """获取城市词典

        Returns:
            字典：规范城市名 -> 别名/简称列表
        """
        pass

    @abstractmethod
    def get_station_dict(self) -> Dict[str, List[str]]:
        """获取站点词典

        Returns:
            字典：规范站点名 -> 别名/简称列表
        """
        pass

    @abstractmethod
    def get_pollutant_dict(self) -> Dict[str, List[str]]:
        """获取污染物词典

        Returns:
            字典：规范污染物名 -> 别名/简称列表
        """
        pass

    @abstractmethod
    def get_prompt_template(self, template_name: str, task_mode: str) -> Optional[str]:
        """获取 Prompt 模板（预留接口，当前返回 None）

        Args:
            template_name: 模板名称
            task_mode: 任务模式（如 'single_record', 'table_records'）

        Returns:
            Prompt 模板字符串，如果不存在则返回 None
        """
        # 当前版本暂不实现，返回 None 保持向后兼容
        return None

    def save_feedback(self, feedback_data: dict) -> bool:
        """保存用户反馈（可选接口，默认返回 False）

        Args:
            feedback_data: 反馈数据字典

        Returns:
            是否保存成功
        """
        # 默认不实现，返回 False
        return False