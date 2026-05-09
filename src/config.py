"""
简化配置模块 - 不使用ConfigManager，直接使用环境变量和默认值

配置优先级：环境变量 > .env文件 > 默认值
环境变量格式：A23_<配置名>，例如 A23_MODEL_TYPE
"""

import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Optional, Tuple

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
# 默认 deepseek：与仓库联调及本地测试一致，密钥见 .env 中 A23_DEEPSEEK_API_KEY；无密钥时 main 会探测为不可用并走规则抽取。
MODEL_TYPE = _get_env("MODEL_TYPE", "deepseek")  # deepseek / ollama / openai / qwen

MODELS = _get_env_list("MODELS", [{"type": "deepseek", "model": "deepseek-chat", "priority": 1}])

OLLAMA_URL = _get_env("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OPENAI_BASE_URL = _get_env("OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_API_KEY = _get_env("OPENAI_API_KEY", "not-needed")

DEEPSEEK_BASE_URL = _get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
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

TARGET_LIMIT_SECONDS = _get_env_int("TARGET_LIMIT_SECONDS", 90)

# ── Embedding（供 model_client 使用） ─────────────────────────────────────────
EMBEDDING_URL = _get_env("EMBEDDING_URL", "http://127.0.0.1:11434/api/embeddings")
EMBEDDING_MODEL = _get_env("EMBEDDING_MODEL", "nomic-embed-text")

# ── 模板配置 ────────────────────────────────────────────────────────────────
TEMPLATE_MODE = _get_env("TEMPLATE_MODE", "auto")  # file / llm / auto

# ── OCR（由 Docling 内置处理，此处仅保留开关） ───────────────────────────────
ENABLE_OCR = _get_env_bool("ENABLE_OCR", False)

# ── LLM 路由：可选经 LiteLLM 统一调用（需 pip install litellm）───────────────
USE_LITELLM = _get_env_bool("USE_LITELLM", False)
# 若设置，则覆盖自动拼接的 litellm model id（如 dashscope/qwen-plus）
LITELLM_MODEL = _get_env("LITELLM_MODEL", "")

# ── 超时 / 重试（默认偏宽松，便于长文档跑通）──────────────────────────────────
EXTRACTION_TIMEOUT = _get_env_int("EXTRACTION_TIMEOUT", 240)
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

# ── 去重配置 ────────────────────────────────────────────────────────────────
SIMILARITY_THRESHOLD = _get_env_float("SIMILARITY_THRESHOLD", 0.85)

# ── API 行为开关 ────────────────────────────────────────────────────────────
# 是否启用算法端内置的 /api/tasks/*（默认关闭：生产由业务后端做异步队列，worker 调同步 /api/extract/direct；本地需长任务联调时设 A23_ENABLE_TASKS=true）
ENABLE_TASKS = _get_env_bool("ENABLE_TASKS", False)
# 是否将同步接口上传/输出持久化到 storage/uploads（默认开启；可按需关闭）
PERSIST_UPLOADS = _get_env_bool("PERSIST_UPLOADS", True)
# 是否将自动生成的 profile 写入磁盘（默认关闭；调试时可开启）
PERSIST_PROFILES = _get_env_bool("PERSIST_PROFILES", False)
# 当 instruction 要求日期范围但表头无日期语义列时，可自动追加的列名（ASCII，避免在逻辑中写死业务列名）
DATE_FALLBACK_FIELD_NAME = _get_env("DATE_FALLBACK_FIELD_NAME", "date")
# main 是否写出 *_result_report.json（A23_WRITE_RESULT_REPORT_BUNDLE；含 debug_result 等，默认关闭）
WRITE_RESULT_REPORT_BUNDLE = _get_env_bool("WRITE_RESULT_REPORT_BUNDLE", False)

# ── HTTP API：浏览器跨域（网页端 / SPA 调 FastAPI）──────────────────────────
_DEFAULT_CORS_ORIGINS: List[str] = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]

# 与 ``allow_origins`` 列表并行：匹配到的 Origin 会按请求回显（支持凭证）。覆盖常见本地与私网网段任意端口，便于同源网关部署之外的「前后端不同端口 / 局域网调试」。
_DEFAULT_CORS_ORIGIN_REGEX = (
    r"^(?:https?://localhost(?::\d+)?"
    r"|https?://127\.0\.0\.1(?::\d+)?"
    r"|https?://\[::1\](?::\d+)?"
    r"|https?://192\.168\.\d{1,3}\.\d{1,3}(?::\d+)?"
    r"|https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?::\d+)?"
    r"|https?://172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}(?::\d+)?)$"
)


def _parse_cors_origin_regex(wildcard_mode: bool) -> Optional[str]:
    """未设置环境变量时用默认私网/localhost 正则；显式空字符串表示关闭正则（仅用列表）。"""
    if wildcard_mode:
        return None
    key = "A23_CORS_ORIGIN_REGEX"
    if key not in os.environ:
        return _DEFAULT_CORS_ORIGIN_REGEX
    raw = os.environ.get(key, "").strip()
    return raw if raw else None


def _parse_cors_settings() -> Tuple[List[str], bool, Optional[str]]:
    """同源部署可不依赖 CORS；跨域时列表 + 正则并行匹配。``*`` 与浏览器凭证互斥。"""
    raw = _get_env("A23_CORS_ORIGINS", "").strip()
    if raw == "*":
        return ["*"], False, None
    if raw:
        origins = [x.strip() for x in raw.split(",") if x.strip()]
        if origins:
            cred = _get_env_bool("A23_CORS_ALLOW_CREDENTIALS", True)
            return origins, cred, _parse_cors_origin_regex(False)
    return list(_DEFAULT_CORS_ORIGINS), True, _parse_cors_origin_regex(False)


CORS_ORIGINS, CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGIN_REGEX = _parse_cors_settings()

# ── 问答会话（storage/sessions/）────────────────────────────────────────────
# 为 true 时写入 history.json 并允许仅凭 session_id 复用已上传文件；为 false 时用临时目录、请求结束即删，多轮靠业务后端传 history_json + 每次带 files
QNA_PERSIST_SESSION = _get_env_bool("QNA_PERSIST_SESSION", True)
# 默认 true：优先 LangChain ConversationalRetrievalChain + Chroma + HuggingFaceEmbeddings；为 false 时仅用 hybrid_retrieve_chunks（BM25+向量）
QNA_USE_LANGCHAIN = _get_env_bool("QNA_USE_LANGCHAIN", True)
# 问答「生成答案」所用后端：与抽取 ``MODEL_TYPE`` 独立；未传表单 ``model_type`` 时使用本项（默认 deepseek）
QNA_MODEL_TYPE = _get_env("QNA_MODEL_TYPE", "deepseek")


def resolve_qna_chat_model_type(request_override: Optional[str]) -> str:
    """解析问答生成所用的 ``model_type``：表单/调用参数优先，否则 ``QNA_MODEL_TYPE``（默认 deepseek）。"""
    raw = (request_override or "").strip()
    if raw:
        return raw.lower()
    env_default = (QNA_MODEL_TYPE or "").strip().lower()
    return env_default if env_default else "deepseek"


def _repository_root() -> Path:
    """仓库根目录（与 ``scripts/download_qna_embedding_model.py`` 默认输出 ``models/qna_embedding`` 对齐）。"""
    return Path(__file__).resolve().parents[1]


def _is_sentence_transformer_bundle(path: Path) -> bool:
    """目录是否为可本地加载的 sentence-transformers 快照（配置 + 权重 + 分词器最低集合）。"""
    if not path.is_dir():
        return False
    if not (path / "config.json").is_file():
        return False
    has_weights = bool(list(path.glob("*.safetensors"))) or (path / "pytorch_model.bin").is_file()
    has_tokenizer = (
        (path / "tokenizer.json").is_file()
        or (path / "tokenizer_config.json").is_file()
        or (path / "tokenizer.model").is_file()
        or (path / "vocab.txt").is_file()
    )
    return bool(has_weights and has_tokenizer)


def resolve_qna_sentence_transformer_model() -> str:
    """解析问答句向量：本地快照目录绝对路径、Hub 模型 id，或空字符串。

    **默认离线**：未设置 ``A23_QNA_SENTENCE_TRANSFORMER`` 时，仅当仓库下 ``models/qna_embedding`` 为完整本地快照（``config.json``、权重文件如 ``*.safetensors``、分词器如 ``tokenizer.json`` 等）才使用该路径；否则返回空字符串，**不会**隐式使用 HuggingFace Hub。

    若显式设置 ``A23_QNA_SENTENCE_TRANSFORMER``：路径指向本地快照则返回其绝对路径；否则将整串视为 Hub 模型名（显式允许联网解析）。
    """
    raw = _get_env("QNA_SENTENCE_TRANSFORMER", "").strip()
    root = _repository_root()
    default_local = root / "models" / "qna_embedding"

    if raw:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = (root / candidate).resolve()
        else:
            candidate = candidate.resolve()
        if _is_sentence_transformer_bundle(candidate):
            return str(candidate)
        return raw

    if _is_sentence_transformer_bundle(default_local):
        return str(default_local.resolve())

    return ""


@contextmanager
def qna_sentence_transformer_offline_guard(model_path_or_id: str):
    """本地快照加载时强制 ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE``，避免库侧附带请求 Hub。"""
    p = Path(model_path_or_id)
    use_offline = _is_sentence_transformer_bundle(p)
    if not use_offline:
        yield
        return
    keys = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")
    saved = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        yield
    finally:
        for k in keys:
            v = saved[k]
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
