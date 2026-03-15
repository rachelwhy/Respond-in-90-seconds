"""
意图识别模块：分析用户输入，判断意图类型
支持规则匹配和LLM增强识别
"""

from typing import Optional
import re


class IntentRecognizer:
    """
    意图识别器
    识别用户输入的意图类型，用于分发到不同处理模块
    """

    # 意图类型定义
    INTENT_TYPES = {
        "qa": ["什么", "如何", "为什么", "?", "？", "吗", "是否", "哪个", "哪些", "解释", "说明"],
        "fill": ["填写", "填表", "填入", "填充", "生成表格", "自动填", "根据文档填"],
        "extract": ["提取", "抽取", "找出", "获取", "得到", "抓取", "采集", "捞取"],
        "summarize": ["总结", "概括", "摘要", "归纳", "简述", "概述", "浓缩"],
        "translate": ["翻译", "译成", "转换为", "转为英文", "转为中文"],
        "analyze": ["分析", "统计", "计算", "对比", "比较", "趋势", "分布"],
        "list": ["列出", "列举", "展示", "显示", "罗列"],
        "search": ["搜索", "查找", "查询", "寻找", "检索"]
    }

    def __init__(self, use_llm_fallback: bool = True):
        """
        初始化意图识别器
        :param use_llm_fallback: 当规则无法识别时，是否使用LLM
        """
        self.use_llm_fallback = use_llm_fallback
        self._llm_client = None  # 延迟导入

    def recognize(self, text: str) -> str:
        """
        识别用户输入的意图
        :param text: 用户输入文本
        :return: 意图类型字符串
        """
        if not text or not text.strip():
            return "unknown"

        text_lower = text.lower().strip()

        # 1. 规则匹配
        for intent, keywords in self.INTENT_TYPES.items():
            for keyword in keywords:
                if keyword in text_lower:
                    # 特殊规则：问句优先判断为qa
                    if intent == "qa" and self._is_question(text_lower):
                        return intent
                    # 普通匹配
                    return intent

        # 2. 问句判断（没有匹配到qa关键词但确实是问句）
        if self._is_question(text_lower):
            return "qa"

        # 3. LLM兜底识别
        if self.use_llm_fallback:
            llm_intent = self._recognize_with_llm(text)
            if llm_intent:
                return llm_intent

        return "unknown"

    def _is_question(self, text: str) -> bool:
        """
        判断是否为问句
        """
        # 问号结尾
        if text.endswith(('?', '？')):
            return True

        # 疑问词开头
        question_starters = ['什么', '怎么', '为什么', '如何', '是否', '能否', '哪个', '哪些']
        for starter in question_starters:
            if text.startswith(starter):
                return True

        # 疑问句式
        question_patterns = [
            r'.*吗\s*$',
            r'.*呢\s*$',
            r'.*吧\s*$',
            r'是.*还是.*',
            r'有没有.*',
            r'是不是.*',
            r'能不能.*',
            r'可不可以.*'
        ]
        for pattern in question_patterns:
            if re.search(pattern, text):
                return True

        return False

    def _recognize_with_llm(self, text: str) -> Optional[str]:
        """
        使用LLM识别意图（兜底方案）
        """
        try:
            # 延迟导入，避免循环依赖
            from .llm import llm_client

            prompt = f"""
分析以下用户输入的意图，只返回一个词（qa/fill/extract/summarize/translate/analyze/unknown）：

用户输入：{text}

意图定义：
- qa：问答、咨询、询问信息
- fill：填表、填写数据
- extract：提取、抽取信息
- summarize：总结、概括
- translate：翻译
- analyze：分析、统计
- unknown：无法确定

只返回意图类型，不要有其他内容：
"""
            result = llm_client.request(prompt, is_json=False)
            if result and result.strip() in self.INTENT_TYPES:
                return result.strip()
        except Exception as e:
            print(f"LLM意图识别失败: {e}")

        return None

    def add_custom_intent(self, intent: str, keywords: list):
        """
        添加自定义意图和关键词
        """
        if intent not in self.INTENT_TYPES:
            self.INTENT_TYPES[intent] = []
        self.INTENT_TYPES[intent].extend(keywords)


# 全局单例
intent_recognizer = IntentRecognizer()


# 便捷函数
def recognize_intent(text: str) -> str:
    """快速识别意图"""
    return intent_recognizer.recognize(text)