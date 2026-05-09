#!/usr/bin/env python3
"""预下载问答用句向量模型（sentence-transformers），避免运行时访问 huggingface.co 超时。

用法（任选其一）：

1. **国内镜像（推荐）** —— 安装依赖后执行：

   PowerShell::

     $env:HF_ENDPOINT = 'https://hf-mirror.com'
     python scripts/download_qna_embedding_model.py

   或在 ``.env`` 中增加一行 ``HF_ENDPOINT=https://hf-mirror.com`` 再运行本脚本。

2. **指定镜像参数**::

     python scripts/download_qna_embedding_model.py --endpoint https://hf-mirror.com

3. **下载完成后**，在 ``.env`` 把模型改为**本地目录**（绝对路径或相对项目根目录）::

     A23_QNA_SENTENCE_TRANSFORMER=models/qna_embedding

``sentence-transformers`` / LangChain ``HuggingFaceEmbeddings`` 均支持本地文件夹路径。

依赖：``pip install huggingface_hub``（通常已由 ``sentence-transformers`` 引入）。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download QnA embedding model for offline/local use")
    parser.add_argument(
        "--repo-id",
        default=os.environ.get(
            "A23_QNA_EMBEDDING_REPO_ID",
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        ),
        help="Hugging Face 仓库 ID（默认与 A23_QNA_SENTENCE_TRANSFORMER 同名模型）",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=_project_root() / "models" / "qna_embedding",
        help="下载目标目录（默认项目下 models/qna_embedding）",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("HF_ENDPOINT", "").strip() or None,
        help="镜像站点，等价于环境变量 HF_ENDPOINT，例如 https://hf-mirror.com",
    )
    args = parser.parse_args()

    if args.endpoint:
        ep = args.endpoint.rstrip("/")
        os.environ["HF_ENDPOINT"] = ep
        print(f"使用 HF_ENDPOINT={ep}")

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("请先安装: pip install huggingface_hub", file=sys.stderr)
        return 1

    root = _project_root()
    args.local_dir = Path(args.local_dir)
    if not args.local_dir.is_absolute():
        args.local_dir = (root / args.local_dir).resolve()

    args.local_dir.mkdir(parents=True, exist_ok=True)
    print(f"仓库: {args.repo_id}")
    print(f"目标: {args.local_dir}")

    try:
        snapshot_download(repo_id=args.repo_id, local_dir=str(args.local_dir))
    except Exception as e:
        print(f"下载失败: {e}", file=sys.stderr)
        print(
            "\n建议：\n"
            "  1) 设置镜像后再试：  HF_ENDPOINT=https://hf-mirror.com\n"
            "  2) 或浏览器从镜像站手动下载同名仓库文件到上述目录。\n"
            "  3) 延长超时：HF_HUB_DOWNLOAD_TIMEOUT=300",
            file=sys.stderr,
        )
        return 1

    try:
        rel = args.local_dir.relative_to(root)
    except ValueError:
        rel = args.local_dir
    print("\n完成。请在 .env 中设置（指向该目录）：")
    print(f"  A23_QNA_SENTENCE_TRANSFORMER={rel.as_posix()}")
    print("或使用绝对路径：")
    print(f"  A23_QNA_SENTENCE_TRANSFORMER=" + str(args.local_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
