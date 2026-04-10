from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, Any


class BaseParser(ABC):
    parser_type: str = "base"

    @abstractmethod
    def parse(self, path: Path) -> Dict[str, Any]:
        ...
