"""
简化配置模块 - 不使用ConfigManager，直接使用环境变量和默认值

配置优先级：环境变量 > .env文件 > 默认值
环境变量格式：A23_<配置名>，例如 A23_MODEL_TYPE
"""

import os
import json
from typing import Any

def _get_env(key: str, default: str = "") -> str:
    """获取环境变量值，支持A23_前缀"""
    # 首先尝试带A23_前缀
    env_key = f"A23_{key}"
    value = os.environ.get(env_key)
    if value is not None:
        return value
    # 然后尝试不带前缀（向后兼容）
    return os.environ.get(key, default)

def _get_env_int(key: str, default: int = 0) -> int:
    """获取整数环境变量值"""
    value = _get_env(key, str(default))
    try:
        return int(value)
    except ValueError:
        return default

def _get_env_float(key: str, default: float = 0.0) -> float:
    """获取浮点数环境变量值"""
    value = _get_env(key, str(default))
    try:
        return float(value)
    except ValueError:
        return default

def _get_env_bool(key: str, default: bool = False) -> bool:
    """获取布尔值环境变量值"""
    value = _get_env(key, str(default)).lower()
    return value in ("true", "1", "yes", "on", "y")

def _get_env_list(key: str, default: list = None) -> list:
    """获取列表环境变量值（JSON格式）"""
    if default is None:
        default = []
    value = _get_env(key, "")
    if not value:
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return default

# ── 模型配置 ────────────────────────────────────────────────────────────────
MODEL_TYPE = _get_env("MODEL_TYPE", "ollama")  # ollama / openai / deepseek / qwen

MODELS = _get_env_list("MODELS", [{"type": "ollama", "model": "qwen2.5:7b", "url": "http://127.0.0.1:11434", "priority": 1}])

OLLAMA_URL = _get_env("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OPENAI_BASE_URL = _get_env("OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_API_KEY = _get_env("OPENAI_API_KEY", "not-needed")

DEEPSEEK_BASE_URL = _get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = _get_env("DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = _get_env("DEEPSEEK_MODEL", "deepseek-chat")

# Qwen配置（通义千问）
QWEN_BASE_URL = _get_env("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_API_KEY = _get_env("QWEN_API_KEY", "")
QWEN_MODEL = _get_env("QWEN_MODEL", "qwen-plus")

OLLAMA_MODEL = _get_env("OLLAMA_MODEL", "qwen2.5:7b")
OPENAI_MODEL = _get_env("OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")
MODEL_NAME = _get_env("MODEL_NAME", OLLAMA_MODEL)

TEMPERATURE = _get_env_float("TEMPERATURE", 0.5)
MAX_TOKENS = _get_env_int("MAX_TOKENS", 4096)

# ── 路径配置 ────────────────────────────────────────────────────────────────
INPUT_DIR = _get_env("INPUT_DIR", "data/in")
OUTPUT_JSON = _get_env("OUTPUT_JSON", "output/result.json")
OUTPUT_XLSX = _get_env("OUTPUT_XLSX", "output/result.xlsx")
OUTPUT_REPORT_BUNDLE_JSON = _get_env("OUTPUT_REPORT_BUNDLE_JSON", "output/report_bundle.json")

TARGET_LIMIT_SECONDS = _get_env_int("TARGET_LIMIT_SECONDS", 40)

# ── Embedding（供 model_client 使用） ─────────────────────────────────────────
EMBEDDING_URL = _get_env("EMBEDDING_URL", "http://127.0.0.1:11434/api/embeddings")
EMBEDDING_MODEL = _get_env("EMBEDDING_MODEL", "nomic-embed-text")

# ── 模板配置 ────────────────────────────────────────────────────────────────
TEMPLATE_MODE = _get_env("TEMPLATE_MODE", "auto")  # file / llm / auto

# ── OCR（由 Docling 内置处理，此处仅保留开关） ───────────────────────────────
ENABLE_OCR = _get_env_bool("ENABLE_OCR", False)

# ── 超时 / 重试 ──────────────────────────────────────────────────────────────
EXTRACTION_TIMEOUT = _get_env_int("EXTRACTION_TIMEOUT", 120)
MAX_RETRIES = _get_env_int("MAX_RETRIES", 3)

# ── MySQL 数据库（数据入库，供后端同学使用） ─────────────────────────────────
MYSQL_HOST = _get_env("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = _get_env_int("MYSQL_PORT", 3306)
MYSQL_USER = _get_env("MYSQL_USER", "root")
MYSQL_PASSWORD = _get_env("MYSQL_PASSWORD", "")
MYSQL_DATABASE = _get_env("MYSQL_DATABASE", "a23")

# ── 字段别名和归一化配置 ──────────────────────────────────────────────────────
FUZZY_THRESHOLD = _get_env_int("FUZZY_THRESHOLD", 75)
NORMALIZATION_CONFIG = _get_env("NORMALIZATION_CONFIG", "src/knowledge/field_normalization_rules.json")

# ── langextract适配器配置 ─────────────────────────────────────────────────────
LANGEXTRACT_PROVIDER_WORKERS = _get_env_int("LANGEXTRACT_PROVIDER_WORKERS", 1)
LANGEXTRACT_MAX_CONCURRENT = _get_env_int("LANGEXTRACT_MAX_CONCURRENT", 2)

# ── 去重配置 ────────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = _get_env_float("SIMILARITY_THRESHOLD", 0.85)

# ── API 行为开关（生产默认更“轻”）────────────────────────────────────────────
# 是否启用算法端内置的 /api/tasks/* 任务系统（默认关闭：由后端负责任务/异步）
ENABLE_TASKS = _get_env_bool("ENABLE_TASKS", True)
# 是否将同步接口上传/输出持久化到 storage/uploads（默认开启；可按需关闭）
PERSIST_UPLOADS = _get_env_bool("PERSIST_UPLOADS", True)
# 是否将自动生成的 profile 写入磁盘（默认关闭；调试时可开启）
PERSIST_PROFILES = _get_env_bool("PERSIST_PROFILES", False)

# ── 存储清理策略（默认“安全省心”）────────────────────────────────────────────
# uploads 目录中每个请求的持久化目录保留时长（小时）
UPLOAD_RETENTION_HOURS = _get_env_int("UPLOAD_RETENTION_HOURS", 24)
# uploads/temp 临时文件保留时长（小时）
TEMP_RETENTION_HOURS = _get_env_int("TEMP_RETENTION_HOURS", 1)
# tasks 目录每个任务保留时长（小时）
TASK_RETENTION_HOURS = _get_env_int("TASK_RETENTION_HOURS", 24)

# 向后兼容：导出config_manager（简化版本）
class SimpleConfigManager:
    """简化配置管理器，用于向后兼容"""

    def get(self, key: str, default: Any = None) -> Any:
        return _get_env(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return _get_env_int(key, default)

    def get_float(self, key: str, default: float = 0.0) -> float:
        return _get_env_float(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return _get_env_bool(key, default)

    def get_list(self, key: str, default: list = None) -> list:
        if default is None:
            default = []
        return _get_env_list(key, default)

    def load_config_file(self):
        """加载配置文件（简化版本，仅记录）"""
        pass

config_manager = SimpleConfigManager()

# 辅助函数：获取配置（用于向后兼容）
def get_config(key: str = None, default: Any = None) -> Any:
    """获取配置值（简化版本）"""
    if key is None:
        # 返回所有配置的字典
        import sys
        current_module = sys.modules[__name__]
        config_dict = {}
        for name in dir(current_module):
            if not name.startswith('_') and name.isupper():
                config_dict[name] = getattr(current_module, name)
        return config_dict
    else:
        return _get_env(key, default)
