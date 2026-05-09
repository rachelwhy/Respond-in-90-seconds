#!/usr/bin/env python3
"""验证问答检索依赖是否可导入并可完成最小向量运算（随 requirements.txt 一并交付）。

用法：
  python scripts/verify_qna_deps.py
  python scripts/verify_qna_deps.py --load-model   # 额外加载 Hugging Face 嵌入模型（首次会下载）
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify QnA retrieval dependencies")
    parser.add_argument(
        "--load-model",
        action="store_true",
        help="Load SentenceTransformer (downloads model on first run)",
    )
    args = parser.parse_args()

    print("1) rank_bm25 …")
    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi([["hello", "world"], ["other"]])
    assert len(list(bm25.get_scores(["hello"]))) == 2

    print("2) sentence_transformers …")
    import numpy as np
    from sentence_transformers import SentenceTransformer

    print("2b) langchain + chromadb（默认问答路径）…")
    import chromadb  # noqa: F401
    from langchain.chains import ConversationalRetrievalChain  # noqa: F401
    from langchain_community.vectorstores import Chroma  # noqa: F401

    if args.load_model:
        name = os.environ.get(
            "A23_QNA_SENTENCE_TRANSFORMER",
            "paraphrase-multilingual-MiniLM-L12-v2",
        ).strip()
        print(f"   loading SentenceTransformer({name!r}) …")
        model = SentenceTransformer(name)
        v = model.encode("test", normalize_embeddings=True)
        assert isinstance(v, np.ndarray)
        print(f"   embed dim = {v.shape}")
    else:
        print("   (跳过加载嵌入模型；完整自检请加 --load-model)")
        dummy = np.zeros(8, dtype=np.float32)
        assert dummy.shape == (8,)

    print("3) qna_retrieval（类型加权，不触发嵌入模型下载）…")
    from src.api.qna_retrieval import apply_chunk_type_boost

    boosted = apply_chunk_type_boost([0.5, 0.5], [{"type": "text"}, {"type": "table"}])
    assert boosted[1] > boosted[0]

    print("OK: QA 依赖可用。若句向量拉取 huggingface.co 超时，见 HTTP_API_USAGE「句向量模型下载」或运行：")
    print("  HF_ENDPOINT=https://hf-mirror.com python scripts/download_qna_embedding_model.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
