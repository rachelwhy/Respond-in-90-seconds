from llm_client import client
from rag_engine import rag
from typing import List, Dict, Any, Optional

class UniversalProcessor:
    """通用处理单元：完全解耦"""

    def process(self, text: str, instruction: str, template: Optional[List[str]] = None) -> Dict[str, Any]:
        # 1. 动态模式探测
        discovery_prompt = f"Analyze: '{instruction}'. Detect key attributes to extract. Return JSON: {{'keys': []}}"
        discovery = client.request(discovery_prompt)
        target_keys = discovery.get("keys", [])

        # 2. RAG 检索
        chunks = rag.sliding_window_chunk(text)
        context = rag.retrieve(target_keys, chunks)

        # 3. 结构化抽取
        extract_prompt = f"Context: {context}\nTask: Extract values for {target_keys}. Return JSON: {{\"data\": {{}}, \"confidence\": {{}}}}"
        extracted = client.request(extract_prompt)

        # 4. 语义映射
        aligned_row = None
        if template:
            align_prompt = f"Map {list(extracted['data'].keys())} to {template}. Return JSON mapping: {{\"src\": \"target\"}}"
            mapping = client.request(align_prompt)
            aligned_row = {col: extracted['data'].get(src) for src, col in mapping.items() if col in template}

        return {
            "data": aligned_row if template else extracted.get("data"),
            "confidence": extracted.get("confidence")
        }

engine = UniversalProcessor()