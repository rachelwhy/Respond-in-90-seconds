"""
知识库加载器 — 直接使用文件源，后端可替换为 DatabaseKnowledgeSource
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

from .base import KnowledgeSource

_DEFAULT_DIR = Path(__file__).parent


@dataclass
class KnowledgeBase:
    field_aliases: Dict[str, List[str]]
    city_dict: Dict[str, List[str]]
    pollutant_dict: Dict[str, List[str]]
    station_dict: Dict[str, List[str]]


class FileKnowledgeSource(KnowledgeSource):
    """从 JSON 文件加载知识库（默认实现）

    后端团队若需从数据库加载，继承 KnowledgeSource 并实现所有抽象方法，
    然后替换 load_knowledge_base 中的实例化即可。
    """

    def __init__(self, base_dir: Path = _DEFAULT_DIR):
        self._dir = Path(base_dir)

    def _load(self, filename: str) -> dict:
        path = self._dir / filename
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def get_field_aliases(self) -> Dict[str, List[str]]:
        return self._load("field_aliases.json")

    def get_city_dict(self) -> Dict[str, List[str]]:
        return self._load("city_dict.json")

    def get_pollutant_dict(self) -> Dict[str, List[str]]:
        return self._load("pollutant_dict.json")

    def get_station_dict(self) -> Dict[str, List[str]]:
        return self._load("station_dict.json")

    def get_prompt_template(self, template_name: str, task_mode: str):
        return None


def load_knowledge_base(base_dir: str | Path = _DEFAULT_DIR) -> KnowledgeBase:
    """加载知识库

    Args:
        base_dir: JSON 文件所在目录（文件源默认为 src/knowledge/）

    Returns:
        KnowledgeBase 实例
    """
    source = FileKnowledgeSource(Path(base_dir))
    return KnowledgeBase(
        field_aliases=source.get_field_aliases(),
        city_dict=source.get_city_dict(),
        pollutant_dict=source.get_pollutant_dict(),
        station_dict=source.get_station_dict(),
    )
