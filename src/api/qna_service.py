"""文档问答：基于上传文档内容检索片段并生成回答；可选 LangChain 链或仓库内混合检索。

会话文件位于 ``storage/sessions``；是否持久化由 ``A23_QNA_PERSIST_SESSION`` 与表单覆盖；LangChain 路径由 ``A23_QNA_USE_LANGCHAIN`` 与依赖可用性决定，不可用时走 ``qna_retrieval``。
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from src.api.qna_retrieval import hybrid_retrieve_chunks
from src.core.reader import collect_input_bundle, collect_semantic_chunks_from_bundle

SESSIONS_ROOT = Path("storage/sessions")
SESSIONS_ROOT.mkdir(parents=True, exist_ok=True)


def parse_conversation_history_json(raw: Optional[str]) -> Optional[List[dict]]:
    """解析表单 ``history_json``。返回 ``None`` 表示未提供或为空；非法 JSON 或类型抛出 ``ValueError``。"""
    if raw is None or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"history_json 不是合法 JSON: {e}") from e
    if not isinstance(data, list):
        raise ValueError("history_json 须为 JSON 数组")
    return normalize_conversation_history(data)


def normalize_conversation_history(items: List[dict]) -> List[dict]:
    """将上游传入的记录规范为与磁盘 ``history.json`` 相同的 ``q`` / ``a`` / 可选 ``t`` 结构。"""
    out: List[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        q = str(item.get("q", "")).strip()
        a = str(item.get("a", "")).strip()
        if not q and not a:
            continue
        row: dict = {"q": q, "a": a}
        if "t" in item:
            try:
                row["t"] = int(item["t"])
            except (TypeError, ValueError):
                row["t"] = int(time.time())
        out.append(row)
    return out


def parse_persist_session_form(raw: Optional[str]) -> Optional[bool]:
    """解析表单 ``persist_session``：空串或未提供返回 ``None``（使用配置默认值）。"""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if s == "":
        return None
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError("persist_session 须为 true / false（或省略以使用 A23_QNA_PERSIST_SESSION）")


def _collect_semantic_chunks(documents: list) -> List[dict]:
    """从 Docling 解析结果提取语义块（表格/段落边界对齐）

    每个 chunk 携带 source_file 字段，直接用于来源标注，无需偏移量反推。
    """
    all_chunks: List[dict] = []
    bundle = {"documents": documents or []}
    base_chunks = collect_semantic_chunks_from_bundle(bundle)
    if not base_chunks:
        return all_chunks
    doc_files: List[str] = []
    for doc in documents or []:
        fname = Path((doc or {}).get("path", "unknown")).name
        count = len((doc or {}).get("chunks") or [])
        doc_files.extend([fname] * max(0, count))
    for idx, chunk in enumerate(base_chunks):
        text = str((chunk or {}).get("text", "")).strip()
        if not text:
            continue
        all_chunks.append(
            {
                "text": text,
                "type": (chunk or {}).get("type", "text"),
                "source_file": doc_files[idx] if idx < len(doc_files) else "unknown",
                "start": -1,
                "end": -1,
            }
        )
    return all_chunks


def _chunk_text(text: str, chunk_size: int = 500, overlap: int = 80) -> List[dict]:
    """将文本切分为带来源位置信息的块"""
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        if end < len(text):
            for sep in ("。", "\n", "；", ".", ";"):
                pos = text.rfind(sep, start + chunk_size // 2, end)
                if pos != -1:
                    end = pos + 1
                    break
        chunks.append(
            {
                "text": text[start:end],
                "start": start,
                "end": end,
            }
        )
        if end >= len(text):
            break
        start = end - overlap
    return chunks


def retrieve_chunks(
    text: str,
    question: str,
    top_k: int = 5,
    chunk_size: int = 500,
    overlap: int = 80,
    session_dir: Optional[Path] = None,
) -> List[dict]:
    """将文本字符分块，按问题相关度返回 top_k 块（无 Docling 语义块时的 fallback）。"""
    chunks = _chunk_text(text, chunk_size=chunk_size, overlap=overlap)
    if not chunks:
        return []
    return _retrieve_from_chunks(chunks, question, top_k=top_k, session_dir=session_dir)


def _retrieve_from_chunks(
    chunks: List[dict],
    question: str,
    top_k: int = 5,
    session_dir: Optional[Path] = None,
) -> List[dict]:
    """对语义块或字符块列表混合检索并返回 top_k。"""
    return hybrid_retrieve_chunks(chunks, question, top_k, session_dir=session_dir)


def _load_history(session_dir: Path) -> List[dict]:
    hist_file = session_dir / "history.json"
    if hist_file.exists():
        try:
            data = json.loads(hist_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return normalize_conversation_history([x for x in data if isinstance(x, dict)])
        except Exception:
            pass
    return []


def _save_history(session_dir: Path, history: List[dict]):
    (session_dir / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _find_source_file(char_offset: int, files: List[Tuple[str, bytes]], input_dir: Path) -> str:
    """根据字符偏移量估算来源文件"""
    try:
        from src.adapters.parser_factory import get_parser

        cumulative = 0
        for name, _ in files:
            fpath = input_dir / name
            if not fpath.exists():
                continue
            parser = get_parser(fpath)
            if parser is None:
                continue
            try:
                result = parser.parse(fpath)
                file_text = result.get("text", "") if isinstance(result, dict) else ""
                if cumulative <= char_offset < cumulative + len(file_text):
                    return name
                cumulative += len(file_text) + 2
            except Exception:
                pass
    except Exception:
        pass
    return files[0][0] if files else "unknown"


def answer_question(
    question: str,
    files: Optional[List[Tuple[str, bytes]]] = None,
    session_id: Optional[str] = None,
    top_k: int = 5,
    model_type: Optional[str] = None,
    *,
    persist_session: Optional[bool] = None,
    conversation_history: Optional[List[dict]] = None,
) -> dict:
    """回答用户关于文档的问题（支持多轮对话 + 分块检索）

    Args:
        question:   用户问题
        files:      [(filename, content_bytes), ...]；可为空（仅当持久化会话且磁盘上已有上传）
        session_id: 会话 ID（可选；关闭持久化时仅作回显关联，不能据此在算法侧加载文件）
        top_k:      检索段落数量（1-20）
        model_type: 覆盖默认 ``A23_QNA_MODEL_TYPE``（默认 deepseek，与抽取 ``A23_MODEL_TYPE`` 独立）
        persist_session: ``None`` 时使用 ``QNA_PERSIST_SESSION``；``False`` 时用临时目录且不写会话文件
        conversation_history: 调用方提供的先前轮次（与 ``history.json`` 条目形状一致）；提供时优先于磁盘历史

    Returns:
        {"answer": str, "session_id": str, "sources": [...], "persist_session": bool, "qna_method": str}
    """
    from src.config import QNA_PERSIST_SESSION, QNA_USE_LANGCHAIN, resolve_qna_chat_model_type

    persist = QNA_PERSIST_SESSION if persist_session is None else bool(persist_session)
    effective_model_type = resolve_qna_chat_model_type(model_type)

    qna_id = session_id or uuid.uuid4().hex[:12]

    cleanup_dir: Optional[str] = None
    if persist:
        work_dir = SESSIONS_ROOT / qna_id
        work_dir.mkdir(parents=True, exist_ok=True)
        input_dir = work_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
    else:
        cleanup_dir = tempfile.mkdtemp(prefix="a23_qna_")
        work_dir = Path(cleanup_dir)
        input_dir = work_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)

    try:
        incoming_files = files or []
        for name, content in incoming_files:
            (input_dir / name).write_bytes(content)

        effective_files: List[Tuple[str, bytes]] = list(incoming_files)
        if not effective_files:
            if persist:
                existing = [p for p in input_dir.iterdir() if p.is_file()]
                for p in existing:
                    try:
                        effective_files.append((p.name, p.read_bytes()))
                    except Exception:
                        continue
            if not effective_files:
                msg = (
                    "未找到可用于问答的文件，请先上传文件或提供有效的 session_id。"
                    if persist
                    else "关闭会话持久化时每次请求须上传 files；算法侧无法仅凭 session_id 复用文件，会话与文件由业务后端保管时可传 history_json。"
                )
                return {
                    "answer": msg,
                    "session_id": qna_id,
                    "sources": [],
                    "persist_session": persist,
                    "qna_method": "none",
                }

        bundle = collect_input_bundle(str(input_dir))
        all_text = bundle.get("all_text", "")

        if not all_text.strip():
            return {
                "answer": "无法从上传文件中提取文本内容",
                "session_id": qna_id,
                "sources": [],
                "persist_session": persist,
                "qna_method": "none",
            }

        top_k_clamped = min(max(top_k, 1), 20)
        semantic_chunks = _collect_semantic_chunks(bundle.get("documents", []))
        all_chunks: List[dict] = semantic_chunks if semantic_chunks else _chunk_text(all_text)

        if conversation_history is not None:
            history = normalize_conversation_history(conversation_history)
        elif persist:
            history = _load_history(work_dir)
        else:
            history = []

        if QNA_USE_LANGCHAIN and all_chunks:
            from src.api.qna_langchain import run_langchain_qna

            lc_out = run_langchain_qna(
                question=question,
                all_chunks=all_chunks,
                work_dir=work_dir,
                history=history,
                top_k=top_k_clamped,
                model_type=effective_model_type,
                persist_session=persist,
            )
            if lc_out:
                answer, sources = lc_out
                history.append(
                    {
                        "q": question,
                        "a": answer if isinstance(answer, str) else str(answer),
                        "t": int(time.time()),
                    }
                )
                if persist:
                    _save_history(work_dir, history)
                return {
                    "answer": answer,
                    "session_id": qna_id,
                    "sources": sources,
                    "persist_session": persist,
                    "qna_method": "langchain",
                }

        if semantic_chunks:
            relevant_chunks = _retrieve_from_chunks(
                semantic_chunks, question, top_k=top_k_clamped, session_dir=work_dir
            )
        else:
            relevant_chunks = retrieve_chunks(all_text, question, top_k=top_k_clamped, session_dir=work_dir)

        context_parts = []
        sources = []
        for i, chunk in enumerate(relevant_chunks, start=1):
            context_parts.append(f"[片段{i}]\n{chunk['text']}")
            if chunk.get("source_file"):
                src_file = chunk["source_file"]
            else:
                src_file = _find_source_file(chunk["start"], effective_files, input_dir)
            sources.append(
                {
                    "file": src_file,
                    "excerpt": chunk["text"][:200].strip(),
                    "score": round(chunk["score"], 3),
                    "method": chunk.get("method", "bm25"),
                    "chunk_type": chunk.get("type", "text"),
                }
            )
        context = "\n\n".join(context_parts)

        history_text = ""
        if history:
            recent = history[-4:]
            history_text = "\n".join([f"用户：{h['q']}\n助手：{h['a']}" for h in recent]) + "\n\n"

        try:
            from src.adapters.model_client import call_model

            prompt = (
                f"{history_text}"
                "请根据以下文档片段回答问题。若文档中没有相关信息，请明确说明'文档中未找到相关信息'。\n\n"
                f"问题：{question}\n\n"
                f"文档片段：\n{context}\n\n"
                f"请给出准确、简洁的回答："
            )
            answer = call_model(prompt, model_type=effective_model_type, plain_text=True)
            if isinstance(answer, dict):
                answer = answer.get("answer") or str(answer)
        except Exception as e:
            answer = f"模型调用失败: {e}"

        history.append(
            {
                "q": question,
                "a": answer if isinstance(answer, str) else str(answer),
                "t": int(time.time()),
            }
        )
        if persist:
            _save_history(work_dir, history)

        out: dict = {
            "answer": answer,
            "session_id": qna_id,
            "sources": sources,
            "persist_session": persist,
            "qna_method": "hybrid",
        }
        return out
    finally:
        if cleanup_dir:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
