"""
进程内复用的 requests.Session（连接池）。

供 model_client、健康检查等所有出站 HTTP 使用，避免每次请求新建 TCP/TLS。
"""

from __future__ import annotations

import threading
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

_pool_connections = 12
_pool_maxsize = 24

_session: Optional[requests.Session] = None
_lock = threading.Lock()


def get_shared_session() -> requests.Session:
    global _session
    with _lock:
        if _session is None:
            s = requests.Session()
            adapter = HTTPAdapter(
                pool_connections=_pool_connections,
                pool_maxsize=_pool_maxsize,
                max_retries=0,
            )
            s.mount("http://", adapter)
            s.mount("https://", adapter)
            _session = s
        return _session
