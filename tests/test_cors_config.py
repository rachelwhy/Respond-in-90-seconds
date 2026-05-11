"""CORS 默认允许常见本地前端开发端口，便于网页对话框直连 API。"""

import re


def test_cors_defaults_allow_vite_and_common_ports():
    from src.config import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGIN_REGEX, CORS_ORIGINS

    assert "http://localhost:5173" in CORS_ORIGINS
    assert "http://127.0.0.1:3000" in CORS_ORIGINS
    assert CORS_ALLOW_CREDENTIALS is True
    assert CORS_ALLOW_ORIGIN_REGEX
    rx = re.compile(CORS_ALLOW_ORIGIN_REGEX)
    assert rx.fullmatch("http://localhost:9999")
    assert rx.fullmatch("http://192.168.1.10:5173")
    assert rx.fullmatch("http://10.0.0.5:8080")
    assert not rx.fullmatch("https://evil.com")


def test_cors_star_pattern_via_subprocess():
    """``A23_CORS_ORIGINS=*`` 时关闭 credentials（避免与浏览器规范冲突）。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    code = """
import os
os.environ["A23_CORS_ORIGINS"] = "*"
# 独立进程内首次加载 config
from src.config import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGIN_REGEX, CORS_ORIGINS
assert CORS_ORIGINS == ["*"], CORS_ORIGINS
assert CORS_ALLOW_CREDENTIALS is False, CORS_ALLOW_CREDENTIALS
assert CORS_ALLOW_ORIGIN_REGEX is None
print("ok")
"""
    env = {**os.environ, "PYTHONPATH": str(root)}
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(root),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr + r.stdout
