import os
import json

# ── 模型配置 ────────────────────────────────────────────────────────────────
MODEL_TYPE = os.environ.get("A23_MODEL_TYPE", "ollama")  # ollama / openai / deepseek

_MODELS_JSON = os.environ.get(
    "A23_MODELS",
    '[{"type":"ollama","model":"qwen2.5:7b","url":"http://127.0.0.1:11434","priority":1}]',
)
try:
    MODELS = json.loads(_MODELS_JSON)
except (json.JSONDecodeError, TypeError):
    MODELS = [{"type": "ollama", "model": "qwen2.5:7b", "url": "http://127.0.0.1:11434", "priority": 1}]

OLLAMA_URL = os.environ.get("A23_OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OPENAI_BASE_URL = os.environ.get("A23_OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_API_KEY = os.environ.get("A23_OPENAI_API_KEY", "not-needed")

DEEPSEEK_BASE_URL = os.environ.get("A23_DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.environ.get("A23_DEEPSEEK_API_KEY", "")
DEEPSEEK_MODEL = os.environ.get("A23_DEEPSEEK_MODEL", "deepseek-chat")

OLLAMA_MODEL = os.environ.get("A23_OLLAMA_MODEL", "qwen2.5:7b")
OPENAI_MODEL = os.environ.get("A23_OPENAI_MODEL", "Qwen/Qwen2.5-7B-Instruct")
MODEL_NAME = os.environ.get("A23_MODEL_NAME", OLLAMA_MODEL)

TEMPERATURE = float(os.environ.get("A23_TEMPERATURE", "0.5"))
MAX_TOKENS = int(os.environ.get("A23_MAX_TOKENS", "4096"))

# ── 路径配置 ────────────────────────────────────────────────────────────────
INPUT_DIR = os.environ.get("A23_INPUT_DIR", "data/in")
OUTPUT_JSON = os.environ.get("A23_OUTPUT_JSON", "output/result.json")
OUTPUT_XLSX = os.environ.get("A23_OUTPUT_XLSX", "output/result.xlsx")
OUTPUT_REPORT_BUNDLE_JSON = os.environ.get("A23_OUTPUT_REPORT_BUNDLE_JSON", "output/report_bundle.json")

TARGET_LIMIT_SECONDS = int(os.environ.get("A23_TARGET_LIMIT_SECONDS", "40"))

# ── Embedding（供 model_client 使用） ─────────────────────────────────────────
EMBEDDING_URL = os.environ.get("A23_EMBEDDING_URL", "http://127.0.0.1:11434/api/embeddings")
EMBEDDING_MODEL = os.environ.get("A23_EMBEDDING_MODEL", "nomic-embed-text")

# ── 模板配置 ────────────────────────────────────────────────────────────────
TEMPLATE_MODE = os.environ.get("A23_TEMPLATE_MODE", "auto")  # file / llm / auto

# ── OCR（由 Docling 内置处理，此处仅保留开关） ───────────────────────────────
ENABLE_OCR = os.environ.get("A23_ENABLE_OCR", "false").lower() == "true"

# ── 超时 / 重试 ──────────────────────────────────────────────────────────────
EXTRACTION_TIMEOUT = int(os.environ.get("A23_EXTRACTION_TIMEOUT", "120"))
MAX_RETRIES = int(os.environ.get("A23_MAX_RETRIES", "3"))

# ── MySQL 数据库（数据入库，供后端同学使用） ─────────────────────────────────
MYSQL_HOST = os.environ.get("A23_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.environ.get("A23_MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("A23_MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("A23_MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("A23_MYSQL_DATABASE", "a23")
