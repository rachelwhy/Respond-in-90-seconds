from __future__ import annotations

import time

from fastapi import APIRouter
from fastapi.responses import Response

from src import __version__ as APP_VERSION


router = APIRouter()


@router.get("/")
def root():
    return {
        "service": "a23-ai-demo-http",
        "version": APP_VERSION,
        "docs": "/docs",
        "health": "/api/health",
        "metrics": "/metrics",
    }


@router.get("/api/health")
def health():
    return {"ok": True, "service": "a23-ai-demo-http", "version": APP_VERSION, "time": time.time()}


try:
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    @router.get("/metrics")
    def prometheus_metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
except ImportError:
    pass
