"""HTTP：可用模型列举与连通性探测等运维向接口。"""

from __future__ import annotations

import json
from typing import Optional

import requests
from fastapi import APIRouter, Form


router = APIRouter()


@router.get("/api/models")
def get_available_models():
    """返回当前 ``MODEL_TYPE`` 与各后端端点配置摘要（含密钥是否已配置）。"""
    from src.config import (
        MODEL_TYPE,
        OLLAMA_URL,
        OLLAMA_MODEL,
        OPENAI_BASE_URL,
        OPENAI_MODEL,
        DEEPSEEK_BASE_URL,
        DEEPSEEK_MODEL,
        DEEPSEEK_API_KEY,
        QWEN_BASE_URL,
        QWEN_MODEL,
        QWEN_API_KEY,
        MOONSHOT_BASE_URL,
        MOONSHOT_MODEL,
        MOONSHOT_API_KEY,
        ZHIPU_BASE_URL,
        ZHIPU_MODEL,
        ZHIPU_API_KEY,
        BAICHUAN_BASE_URL,
        BAICHUAN_MODEL,
        BAICHUAN_API_KEY,
        SILICONFLOW_BASE_URL,
        SILICONFLOW_MODEL,
        SILICONFLOW_API_KEY,
        DOUBAO_BASE_URL,
        DOUBAO_MODEL,
        DOUBAO_API_KEY,
    )

    def _entry(display: str, typ: str, url: str, model: str, api_key: str) -> dict:
        key = str(api_key or "").strip()
        if typ in ("ollama", "openai"):
            avail = True
        else:
            avail = bool(key)
        return {
            "type": typ,
            "display_name": display,
            "url": url,
            "model": model,
            "is_available": avail,
        }

    available_models = [
        _entry("Ollama (本地)", "ollama", OLLAMA_URL, OLLAMA_MODEL, ""),
        _entry("OpenAI 兼容", "openai", OPENAI_BASE_URL, OPENAI_MODEL, ""),
        _entry("通义千问 (DashScope)", "qwen", QWEN_BASE_URL, QWEN_MODEL, QWEN_API_KEY),
        _entry("DeepSeek", "deepseek", DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, DEEPSEEK_API_KEY),
        _entry("Moonshot (Kimi)", "moonshot", MOONSHOT_BASE_URL, MOONSHOT_MODEL, MOONSHOT_API_KEY),
        _entry("智谱 GLM", "zhipu", ZHIPU_BASE_URL, ZHIPU_MODEL, ZHIPU_API_KEY),
        _entry("百川", "baichuan", BAICHUAN_BASE_URL, BAICHUAN_MODEL, BAICHUAN_API_KEY),
        _entry("SiliconFlow", "siliconflow", SILICONFLOW_BASE_URL, SILICONFLOW_MODEL, SILICONFLOW_API_KEY),
        _entry("豆包 (火山方舟)", "doubao", DOUBAO_BASE_URL, DOUBAO_MODEL, DOUBAO_API_KEY),
    ]

    return {
        "current_model_type": MODEL_TYPE,
        "available_models": available_models,
        "config_source": "environment_variables",
    }


@router.post("/api/models/test-connection")
async def test_model_connection(
    model_type: str = Form(...),
    url: Optional[str] = Form(default=None),
    api_key: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
):
    """测试指定模型的连接性"""
    from src.adapters.openai_compatible_chat import normalize_chat_base_url
    from src.adapters.provider_env import default_chat_provider_dict, is_chat_openai_compatible

    try:
        if model_type == "ollama":
            test_url = url or "http://127.0.0.1:11434/api/generate"
            payload = {"model": model or "qwen2.5:7b", "prompt": "test", "stream": False}
            resp = requests.post(test_url, json=payload, timeout=10)
            resp.raise_for_status()
            return {"success": True, "message": "Ollama连接成功"}

        if is_chat_openai_compatible(model_type):
            cfg = default_chat_provider_dict(model_type)
            raw_base = str(url or base_url or cfg.get("base_url") or "").strip()
            base = normalize_chat_base_url(model_type, raw_base)
            test_url = f"{base.rstrip('/')}/chat/completions"
            ak = str(api_key if api_key is not None else cfg.get("api_key") or "").strip()
            headers = {"Content-Type": "application/json"}
            if ak and ak != "not-needed":
                headers["Authorization"] = f"Bearer {ak}"
            payload = {
                "model": model or cfg.get("model") or "",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10,
            }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=10)
            if model_type == "deepseek" and resp.status_code == 401:
                return {"success": False, "message": "API密钥无效或缺失"}
            resp.raise_for_status()
            return {"success": True, "message": f"{model_type} Chat 兼容端点连接成功"}

        return {"success": False, "message": f"不支持的模型类型: {model_type}"}
    except Exception as e:
        return {"success": False, "message": f"连接测试失败: {str(e)}"}


@router.get("/api/config/runtime")
def get_runtime_config():
    """获取当前运行时配置（从 src/config.py 读取）"""
    from src.config import (
        MODEL_TYPE,
        OLLAMA_MODEL,
        OPENAI_MODEL,
        DEEPSEEK_MODEL,
        TEMPERATURE,
        MAX_TOKENS,
        EXTRACTION_TIMEOUT,
        MAX_RETRIES,
    )

    return {
        "success": True,
        "config": {
            "model_type": MODEL_TYPE,
            "ollama_model": OLLAMA_MODEL,
            "openai_model": OPENAI_MODEL,
            "deepseek_model": DEEPSEEK_MODEL,
            "temperature": TEMPERATURE,
            "max_tokens": MAX_TOKENS,
            "extraction_timeout": EXTRACTION_TIMEOUT,
            "max_retries": MAX_RETRIES,
        },
    }


@router.post("/api/config/runtime")
def update_runtime_config(config_updates: str = Form(...)):
    """运行时配置更新（仅返回确认，实际变量由环境变量控制）"""
    try:
        updates = json.loads(config_updates)
        return {"success": True, "config": updates, "message": "配置已接收（重启生效）"}
    except json.JSONDecodeError:
        return {"success": False, "message": "配置数据必须是有效的JSON"}


@router.post("/api/models/switch")
def switch_model(
    model_type: str = Form(...),
    url: Optional[str] = Form(default=None),
    base_url: Optional[str] = Form(default=None),
    api_key: Optional[str] = Form(default=None),
    model: Optional[str] = Form(default=None),
    temperature: Optional[float] = Form(default=None),
    max_tokens: Optional[int] = Form(default=None),
):
    """记录模型切换请求（实际切换通过环境变量实现，重启生效）"""
    return {
        "success": True,
        "message": f"模型切换请求已记录（model_type={model_type}），请通过环境变量 A23_MODEL_TYPE 生效",
        "requested": {"model_type": model_type, "model": model, "url": url or base_url},
    }
