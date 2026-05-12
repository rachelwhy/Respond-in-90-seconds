"""HTTP：文档问答表单接口 ``/api/qna/*``。"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.api.qna_service import answer_question, parse_conversation_history_json, parse_persist_session_form
from src.config import QNA_PERSIST_SESSION


router = APIRouter()


@router.post("/api/qna/ask")
async def qna_ask(
    question: str = Form(...),
    files: Optional[List[UploadFile]] = File(default=None),
    session_id: Optional[str] = Form(default=None),
    top_k: int = Form(default=5),
    model_type: Optional[str] = Form(default=None),
    history_json: Optional[str] = Form(default=None),
    persist_session: Optional[str] = Form(default=None),
):
    try:
        persist_override = parse_persist_session_form(persist_session)
        conversation = parse_conversation_history_json(history_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    persist_resolved = QNA_PERSIST_SESSION if persist_override is None else persist_override
    no_files = not files or len(files) == 0

    if no_files and not session_id:
        raise HTTPException(status_code=400, detail="QnA 需要上传文件，或提供可复用的 session_id")
    if no_files and not persist_resolved:
        raise HTTPException(
            status_code=400,
            detail="persist_session=false（或未持久化）时每次请求必须上传 files；会话与文件由业务后端存储时请每次附带文件并可传 history_json。",
        )

    payload_files = []
    for f in (files or []):
        payload_files.append((f.filename or "unknown.txt", await f.read()))
    result = answer_question(
        question=question,
        files=payload_files,
        session_id=session_id,
        top_k=top_k,
        model_type=model_type,
        persist_session=persist_override,
        conversation_history=conversation if conversation is not None else None,
    )
    return result
