"""
Prompt模板模块：集中管理所有prompt
便于优化和版本控制
"""

import json
from typing import List, Optional


def build_extraction_prompt(instruction: str, context: str,
                            field_names: Optional[List[str]] = None) -> str:
    """
    构造字段抽取prompt
    参数：
        instruction: 用户指令
        context: 文档片段
        field_names: 需要提取的字段名列表
    """
    field_list = "\n".join([f"- {name}" for name in field_names]) if field_names else "所有相关字段"

    return f"""
你是一个智能文档理解助手。请分析以下文档片段，提取最重要的字段信息。

用户需求：{instruction}

需要提取的字段：
{field_list}

文档片段：
{context}

【严格要求】
- 只输出一个 JSON 对象，不要包含任何注释、解释或额外文字。
- JSON 必须语法正确且完整。
- 如果没有任何字段，输出 {{"fields": []}}。
- 请提取最重要的 5-10 个字段，避免生成过长的 JSON。
- 输出格式如下：
{{
  "fields": [
    {{
      "name": "字段名",
      "value": "字段值",
      "evidence": {{
        "text": "支撑答案的原文片段",
        "position": "如：片段1 行号X-Y 或 字段:合同金额 片段1"
      }}
    }}
  ]
}}

注意：
- 只从提供的文档片段中提取信息
- 字段名要简洁明了
- 如果某个信息在多个片段中出现，只提取一次
- 证据文本必须来自原文
"""


def build_retry_prompt(missing_fields: List[str], context: str) -> str:
    """
    构造二次提取prompt
    参数：
        missing_fields: 缺失的字段名列表
        context: 文档片段
    """
    missing_list = "\n".join([f"- {name}" for name in missing_fields])
    example = {name: "" for name in missing_fields}

    return f"""
你是一个严格的信息抽取助手。

以下字段在首次抽取中缺失，请只补提取这些字段：

{missing_list}

文档片段：
{context}

【要求】
- 只输出一个 JSON 对象，键名必须与字段名完全一致
- 如果找不到，对应值填空字符串 ""
- 不要输出额外字段

输出示例：
{json.dumps(example, ensure_ascii=False)}
"""


def build_query_expand_prompt(query_keys: List[str]) -> str:
    """
    构造查询扩展prompt
    """
    return f"""
为以下关键词生成语义相关的扩展词（每个3-5个）：
{', '.join(query_keys)}

输出JSON格式：{{"expanded": ["词1", "词2"]}}
"""