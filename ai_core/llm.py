"""
LLM客户端模块：封装不同后端的模型调用
从你的代码迁移：llm_client.py 核心功能
"""

import json
import time
import os
import requests
import re
from typing import Any, Optional
from enum import Enum
from dotenv import load_dotenv

load_dotenv()


class ModelBackend(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    DASHSCOPE = "dashscope"


class LLMClient:
    """
    LLM客户端：支持Ollama、OpenAI、DashScope
    包含JSON修复、重试机制
    """

    def __init__(self, backend: Optional[str] = None,
                 base_url: Optional[str] = None,
                 model: Optional[str] = None):
        self.backend = backend or os.getenv("MODEL_BACKEND", "ollama")
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip('/')
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")
        self.dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
        print(f"🤖 LLM客户端初始化，后端: {self.backend}, 模型: {self.model}")

    def request(self, prompt: str, is_json: bool = True,
                model: Optional[str] = None, timeout: int = 90) -> Any:
        """
        发送请求到LLM
        参数：
            prompt: 提示词
            is_json: 是否期望JSON返回
            model: 指定模型（可选）
            timeout: 超时时间（秒）
        """
        if self.backend == "ollama":
            return self._request_ollama(prompt, is_json, model, timeout)
        elif self.backend == "openai":
            return self._request_openai(prompt, is_json, model)
        elif self.backend == "dashscope":
            return self._request_dashscope(prompt, is_json, model)
        else:
            return self._request_ollama(prompt, is_json, model, timeout)

    def _request_ollama(self, prompt: str, is_json: bool,
                        model: Optional[str], timeout: int) -> Any:
        """Ollama后端"""
        model_name = model or self.model
        url = f"{self.base_url}/api/chat"
        messages = [
            {"role": "system", "content": "You are a universal data engine. Output strictly in JSON format."},
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 8192,
                "top_k": 20,
                "top_p": 0.95,
                "repeat_penalty": 1.1,
            }
        }

        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, timeout=timeout)
                resp.raise_for_status()
                data = resp.json()
                content = data["message"]["content"]

                if is_json:
                    content = self._clean_json(content)
                    return json.loads(content)
                return content

            except requests.exceptions.Timeout:
                print(f"⏰ 请求超时 (attempt {attempt+1})")
                if attempt == 2:  # 最后一次尝试失败
                    return {"error": "超时", "fields": []}
                time.sleep(1 + attempt)
            except json.JSONDecodeError as e:
                print(f"JSON解析失败 (attempt {attempt+1}): {e}")
                fixed = self._extract_json(content)
                if fixed:
                    try:
                        return json.loads(fixed)
                    except:
                        pass
                if attempt == 2:
                    return {"error": "JSON解析失败", "fields": []}
                time.sleep(1 + attempt)
            except Exception as e:
                print(f"请求失败 (attempt {attempt+1}): {e}")
                if attempt == 2:
                    return {"error": str(e), "fields": []}
                time.sleep(1 + attempt)

        return {"error": "模型调用失败", "fields": []}

    def _clean_json(self, content: str) -> str:
        """清理JSON中的markdown标记"""
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()

    def _extract_json(self, content: str) -> Optional[str]:
        """从文本中提取JSON"""
        stack = []
        start = -1
        for i, ch in enumerate(content):
            if ch in '{[':
                if start == -1:
                    start = i
                stack.append(ch)
            elif ch in '}]':
                if stack:
                    top = stack.pop()
                    if (top == '{' and ch == '}') or (top == '[' and ch == ']'):
                        if not stack:
                            candidate = content[start:i+1]
                            try:
                                json.loads(candidate)
                                return candidate
                            except:
                                start = -1

        last_brace = content.rfind('}')
        last_bracket = content.rfind(']')
        if last_brace != -1 or last_bracket != -1:
            end = max(last_brace, last_bracket) + 1
            candidate = content[:end]
            try:
                json.loads(candidate)
                return candidate
            except:
                pass

        return None

    def _request_openai(self, prompt: str, is_json: bool, model: Optional[str]) -> Any:
        """OpenAI后端"""
        if not self.openai_key:
            return self._request_ollama(prompt, is_json, model, 90)

        headers = {
            "Authorization": f"Bearer {self.openai_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": model or "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "You are a universal data engine. Output strictly in JSON format."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.1,
            "max_tokens": 2048
        }
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json=payload,
                timeout=60
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            if is_json:
                return json.loads(content)
            return content
        except Exception as e:
            print(f"OpenAI请求失败: {e}")
            return None

    def _request_dashscope(self, prompt: str, is_json: bool, model: Optional[str]) -> Any:
        """DashScope后端"""
        if not self.dashscope_key:
            return self._request_ollama(prompt, is_json, model, 90)

        import dashscope
        dashscope.api_key = self.dashscope_key
        messages = [
            {"role": "system", "content": "You are a universal data engine. Output strictly in JSON format."},
            {"role": "user", "content": prompt}
        ]
        try:
            response = dashscope.Generation.call(
                model=model or "qwen-turbo",
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
                result_format='message'
            )
            if response.status_code == 200:
                content = response.output.choices[0].message.content
                if is_json:
                    return json.loads(content)
                return content
        except Exception as e:
            print(f"DashScope请求失败: {e}")
            return None


# 全局单例
llm_client = LLMClient()