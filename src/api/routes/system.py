from __future__ import annotations

import json
from typing import Optional

import requests
from fastapi import APIRouter, Form


router = APIRouter()


@router.get("/api/models")
def get_available_models():
    """获取可用的模型列表和当前配置"""
    from src.config import (
        MODEL_TYPE,
        OLLAMA_URL,
        OLLAMA_MODEL,
        OPENAI_BASE_URL,
        OPENAI_MODEL,
        DEEPSEEK_BASE_URL,
        DEEPSEEK_MODEL,
        DEEPSEEK_API_KEY,
    )

    available_models = [
        {
            "type": "ollama",
            "display_name": "Ollama (本地)",
            "url": OLLAMA_URL,
            "model": OLLAMA_MODEL,
            "is_available": True,
        },
        {
            "type": "openai",
            "display_name": "OpenAI兼容API",
            "url": OPENAI_BASE_URL,
            "model": OPENAI_MODEL,
            "is_available": True,
        },
        {
            "type": "qwen",
            "display_name": "Qwen (兼容OpenAI)",
            "url": OPENAI_BASE_URL,
            "model": OPENAI_MODEL,
            "is_available": True,
        },
        {
            "type": "deepseek",
            "display_name": "DeepSeek API",
            "url": DEEPSEEK_BASE_URL,
            "model": DEEPSEEK_MODEL,
            "is_available": bool(DEEPSEEK_API_KEY),
        },
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
    try:
        if model_type == "ollama":
            test_url = url or "http://127.0.0.1:11434/api/generate"
            payload = {"model": model or "qwen2.5:7b", "prompt": "test", "stream": False}
            resp = requests.post(test_url, json=payload, timeout=10)
            resp.raise_for_status()
            return {"success": True, "message": "Ollama连接成功"}

        if model_type in ["openai", "qwen"]:
            test_url = (url or "http://localhost:8000/v1") + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
            payload = {
                "model": model or "Qwen/Qwen2.5-7B-Instruct",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10,
            }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            return {"success": True, "message": "OpenAI兼容API连接成功"}

        if model_type == "deepseek":
            test_url = (url or "https://api.deepseek.com") + "/chat/completions"
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key or 'test'}"}
            payload = {
                "model": model or "deepseek-chat",
                "messages": [{"role": "user", "content": "test"}],
                "max_tokens": 10,
            }
            resp = requests.post(test_url, json=payload, headers=headers, timeout=10)
            if resp.status_code == 401:
                return {"success": False, "message": "API密钥无效或缺失"}
            resp.raise_for_status()
            return {"success": True, "message": "DeepSeek API连接成功"}

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
