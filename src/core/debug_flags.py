from __future__ import annotations

import os

_TRUTHY = {"1", "true", "yes", "on", "y"}


def is_debug_enabled() -> bool:
    v = os.environ.get("A23_DEBUG", "")
    return str(v).strip().lower() in _TRUTHY
