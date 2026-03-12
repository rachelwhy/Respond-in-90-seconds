import json
import time
import os
import requests
from typing import Any, Optional
from enum import Enum
from dotenv import load_dotenv
import re

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path)
print(f"📁 加载配置文件: {dotenv_path}")

class ModelBackend(str, Enum):
    OLLAMA = "ollama"
    OPENAI = "openai"
    DASHSCOPE = "dashscope"

class LLMClient:
    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        self.backend = os.getenv("MODEL_BACKEND", "ollama")
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip('/')
        self.model = model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.openai_key = os.getenv("OPENAI_API_KEY", "")
        self.dashscope_key = os.getenv("DASHSCOPE_API_KEY", "")
        print(f"🤖 LLM客户端初始化，后端: {self.backend}, 模型: {self.model}")

    def request(self, prompt: str, is_json: bool = True, model: Optional[str] = None) -> Any:
        if self.backend == "ollama":
            return self._request_ollama(prompt, is_json, model)
        elif self.backend == "openai":
            return self._request_openai(prompt, is_json, model)
        elif self.backend == "dashscope":
            return self._request_dashscope(prompt, is_json, model)
        else:
            return self._request_ollama(prompt, is_json, model)

    def _request_ollama(self, prompt: str, is_json: bool, model: Optional[str]) -> Any:
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
                "num_predict": 8192,        # 增加输出长度
                "top_k": 20,
                "top_p": 0.95,
                "repeat_penalty": 1.1,
            }
        }
        last_error = None
        for attempt in range(3):
            try:
                # 超时设为90秒，避免频繁超时重试
                resp = requests.post(url, json=payload, timeout=90)
                resp.raise_for_status()
                data = resp.json()
                content = data["message"]["content"]
                if is_json:
                    content = self._clean_json(content)
                    return json.loads(content)
                return content
            except requests.exceptions.Timeout:
                print(f"⏰ 请求超时 (attempt {attempt+1})")
                last_error = "Timeout"
                time.sleep(1 + attempt)
            except json.JSONDecodeError as e:
                print(f"JSON解析失败 (attempt {attempt+1}): {e}")
                # 尝试多种修复方法
                fixed = (self._extract_first_json(content) or
                         self._extract_last_json(content) or
                         self._fix_json(content))
                if fixed:
                    try:
                        return json.loads(fixed)
                    except:
                        pass
                last_error = e
                time.sleep(1 + attempt)
            except Exception as e:
                print(f"请求失败 (attempt {attempt+1}): {e}")
                last_error = e
                time.sleep(1 + attempt)
        return {
            "error": f"模型调用失败: {last_error}",
            "fields": []
        }

    def _clean_json(self, content: str) -> str:
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        return content.strip()

    def _extract_first_json(self, content: str) -> Optional[str]:
        """提取第一个完整的JSON对象或数组"""
        content = content.strip()
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
        return None

    def _extract_last_json(self, content: str) -> Optional[str]:
        """尝试截取到最后一个 } 或 ]，用于处理截断情况"""
        last_brace = content.rfind('}')
        last_bracket = content.rfind(']')
        if last_brace == -1 and last_bracket == -1:
            return None
        end = max(last_brace, last_bracket) + 1
        candidate = content[:end]
        # 简单补全括号
        open_braces = candidate.count('{') - candidate.count('}')
        open_brackets = candidate.count('[') - candidate.count(']')
        if open_braces > 0:
            candidate += '}' * open_braces
        if open_brackets > 0:
            candidate += ']' * open_brackets
        try:
            json.loads(candidate)
            return candidate
        except:
            return None

    def _fix_json(self, content: str) -> Optional[str]:
        """补全括号（后备方案）"""
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        if open_braces > 0:
            content += '}' * open_braces
        if open_brackets > 0:
            content += ']' * open_brackets
        try:
            json.loads(content)
            return content
        except:
            return None

    def _request_openai(self, prompt: str, is_json: bool, model: Optional[str]) -> Any:
        if not self.openai_key:
            print("未配置OPENAI_API_KEY，回退到Ollama")
            return self._request_ollama(prompt, is_json, model)
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
        if not self.dashscope_key:
            print("未配置DASHSCOPE_API_KEY，回退到Ollama")
            return self._request_ollama(prompt, is_json, model)
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


llm_client = LLMClient()