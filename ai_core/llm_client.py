import json
import time
import os
from typing import Any
from openai import OpenAI


class LLMClient:
    """通用大模型驱动引擎 - 已加入环境注入支持"""
    def __init__(self):
        # 从环境变量中读取名为 DASHSCOPE_API_KEY 的变量
        self.api_key = os.getenv("DASHSCOPE_API_KEY")

        if not self.api_key:
            raise ValueError("错误：未检测到环境变量 'DASHSCOPE_API_KEY'。请先配置环境变量。")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
        )

    def request(self, prompt: str, is_json: bool = True, model: str = "qwen-plus") -> Any:
        """原子级请求，内置 3 次指数退避重试"""
        for attempt in range(3):
            try:
                params = {
                    "model": model,
                    "messages": [
                        {"role": "system",
                         "content": "You are a universal data engine. Output strictly in JSON format."},
                        {"role": "user", "content": prompt}
                    ]
                }
                if is_json:
                    params["response_format"] = {"type": "json_object"}

                completion = self.client.chat.completions.create(**params)
                content = completion.choices[0].message.content
                return json.loads(content) if is_json else content
            except Exception:
                time.sleep(1 + attempt)
        return None


# 全局单例
client = LLMClient()