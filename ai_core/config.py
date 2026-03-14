"""
配置管理模块：加载和验证profile配置
"""

import json
import os
from typing import Dict, Any, Optional


def load_profile(profile_path: str) -> Dict[str, Any]:
    """
    加载 profile 配置文件
    参数：
        profile_path: profile文件路径
    返回：
        配置字典
    异常：
        FileNotFoundError: 文件不存在
        json.JSONDecodeError: JSON格式错误
    """
    if not os.path.exists(profile_path):
        raise FileNotFoundError(f"找不到 profile 文件：{profile_path}")

    with open(profile_path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_profile(profile: Dict) -> bool:
    """
    验证 profile 格式是否正确
    必需字段：
        - report_name: 报告名称
        - fields: 字段列表
    每个字段必需：
        - name: 字段名
        - type: 字段类型（text/date/money/phone）
        - required: 是否必填
    可选：
        - extract_hint: 提取提示
        - output_format: 输出格式
    """
    required = ["report_name", "fields"]
    for field in required:
        if field not in profile:
            print(f"❌ profile 缺少必需字段: {field}")
            return False

    for idx, field in enumerate(profile["fields"]):
        if "name" not in field:
            print(f"❌ 第 {idx+1} 个字段缺少 'name'")
            return False
        if "type" not in field:
            field["type"] = "text"  # 默认类型
        if "required" not in field:
            field["required"] = False  # 默认非必填

    return True


def get_field_config(profile: Dict, field_name: str) -> Optional[Dict]:
    """
    根据字段名获取字段配置
    """
    for field in profile.get("fields", []):
        if field["name"] == field_name:
            return field
    return None


# 独立测试
if __name__ == "__main__":
    test_profile = {
        "report_name": "合同信息",
        "fields": [
            {"name": "合同金额", "type": "money", "required": True},
            {"name": "签订日期", "type": "date"}
        ]
    }
    print("验证通过" if validate_profile(test_profile) else "验证失败")