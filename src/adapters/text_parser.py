from pathlib import Path
from typing import Any, Dict, List

from src.adapters.base import BaseParser


def safe_read_text(path: Path) -> str:
    for enc in ["utf-8", "utf-8-sig", "gb18030", "gbk"]:
        try:
            return path.read_text(encoding=enc)
        except Exception:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


class TextParser(BaseParser):
    parser_type = 'text'

    def parse(self, path: Path) -> Dict[str, Any]:
        text = safe_read_text(path)
        return {
            'parser_type': self.parser_type,
            'type': 'text',
            'path': str(path),
            'file_name': path.name,
            'paragraphs': self._split_paragraphs(text),
            'text': text,
        }

    @staticmethod
    def _split_paragraphs(text: str) -> List[str]:
        chunks, current = [], []
        for line in text.splitlines():
            if line.strip():
                current.append(line.strip())
            elif current:
                chunks.append(' '.join(current))
                current = []
        if current:
            chunks.append(' '.join(current))
        return chunks
