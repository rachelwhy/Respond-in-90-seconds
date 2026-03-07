import json
import time
import os
import requests
from typing import Any, Optional

class LLMClient:
    """本地 Ollama 驱动引擎（适配 Llama 3.1）"""

    def __init__(self, base_url: Optional[str] = None, model: Optional[str] = None):
        """
        base_url: Ollama 服务地址，默认从环境变量 OLLAMA_URL 读取，否则用 http://localhost:11434
        model: 模型名称，默认从环境变量 OLLAMA_MODEL 读取，否则用 "llama3.1"
        """
        self.base_url = (base_url or os.getenv("OLLAMA_URL", "http://localhost:11434")).rstrip('/')
        self.model = model or os.getenv("OLLAMA_MODEL", "llama3.1")

    def request(self, prompt: str, is_json: bool = True, model: Optional[str] = None) -> Any:
        """
        发送请求到 Ollama，支持 JSON 模式
        返回值：如果 is_json=True，返回解析后的字典；否则返回字符串；失败返回 None
        """
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
                "num_predict": 2048,   # 限制输出长度
            }
        }

        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                content = data["message"]["content"]

                if is_json:
                    # 清理可能的 markdown 代码块标记
                    content = content.strip()
                    if content.startswith("```json"):
                        content = content[7:]
                    if content.endswith("```"):
                        content = content[:-3]
                    content = content.strip()
                    return json.loads(content)
                else:
                    return content

            except requests.exceptions.RequestException as e:
                print(f"请求失败 (attempt {attempt+1}): {e}")
                time.sleep(1 + attempt)
            except json.JSONDecodeError as e:
                print(f"JSON 解析失败 (attempt {attempt+1}): {e}")
                print(f"原始响应: {content}")
                time.sleep(1 + attempt)
            except KeyError as e:
                print(f"响应格式异常，缺少字段 {e} (attempt {attempt+1})")
                time.sleep(1 + attempt)

        return None  # 所有重试失败

# 全局单例
client = LLMClient()