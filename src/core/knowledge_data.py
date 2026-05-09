"""从 ``src/knowledge/*.json`` 读取可选启发式数据（默认可为空列表，逻辑侧不内置业务键名）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List

_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"


def load_json_array(filename: str) -> List[Any]:
    path = _KNOWLEDGE_DIR / filename
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []
