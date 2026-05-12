"""生产 HTTP 入口：装配 FastAPI、CORS、上传目录过期清理与各业务路由。

同步抽取主路径为 ``POST /api/extract/direct``；``/api/tasks/*`` 与入库类路由受 ``A23_ENABLE_TASKS`` 等开关约束。
"""

from __future__ import annotations

import threading

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routes.meta import router as meta_router
from src.api.routes.documents import router as documents_router
from src.api.routes.extract import router as extract_router
from src.api.routes.ingest import router as ingest_router
from src.api.routes.qna import router as qna_router
from src.api.routes.system import router as system_router
from src.api.routes.tasks import router as tasks_router
from src import __version__ as APP_VERSION
from src.api.storage_utils import cleanup_old_uploads_loop
from src.core.runtime_env import initialize_runtime_env
from src.config import (
    CORS_ALLOW_CREDENTIALS,
    CORS_ALLOW_ORIGIN_REGEX,
    CORS_ORIGINS,
    PERSIST_UPLOADS,
    UPLOAD_RETENTION_HOURS,
    TEMP_RETENTION_HOURS,
)

initialize_runtime_env(log_dotenv_loaded=False)


if PERSIST_UPLOADS:
    _cleanup_thread = threading.Thread(
        target=cleanup_old_uploads_loop,
        kwargs={
            "upload_retention_hours": int(UPLOAD_RETENTION_HOURS),
            "temp_retention_hours": int(TEMP_RETENTION_HOURS),
            "sleep_seconds": 3600,
        },
        daemon=True,
    )
    _cleanup_thread.start()


app = FastAPI(title="A23 AI Demo HTTP API", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_origin_regex=CORS_ALLOW_ORIGIN_REGEX,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(meta_router)
app.include_router(tasks_router)
app.include_router(qna_router)
app.include_router(extract_router)
app.include_router(system_router)
app.include_router(ingest_router)
app.include_router(documents_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
