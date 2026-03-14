"""
字段处理器：规则引擎
包含：标准化、格式化、清洗、兜底提取
"""

import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any, List


class FieldProcessor:
    """
    字段处理器：所有normalize/format/fallback规则
    从魏嘉华代码迁移的核心规则引擎
    """

    def __init__(self):
        # 中文数字转换（用于金额大写）
        self.cn_num = "零壹贰叁肆伍陆柒捌玖"
        self.cn_unit_int = ["", "拾", "佰", "仟"]
        self.cn_section = ["", "万", "亿", "兆"]

    def normalize(self, value: Any, field_type: str) -> str:
        """
        内部标准化
        参数：
            value: 原始值
            field_type: 字段类型（text/date/money/phone）
        返回：
            标准化后的字符串
        """
        if value is None:
            return ""
        s = str(value).strip()

        if field_type == "phone":
            return re.sub(r"\D", "", s)
        elif field_type == "date":
            return self._normalize_date(s)
        elif field_type == "money":
            return self._normalize_money(s)
        else:
            return s

    def format(self, value: str, field_type: str, output_format: str) -> str:
        """
        输出格式化
        参数：
            value: 标准化后的值
            field_type: 字段类型
            output_format: 输出格式
        返回：
            格式化后的字符串
        """
        if not value:
            return ""

        if field_type == "money":
            return self._format_money(value, output_format)
        elif field_type == "date":
            return self._format_date(value, output_format)
        else:
            return value

    def clean_org_name(self, value: str) -> str:
        """清洗公司名"""
        if not value:
            return ""
        s = str(value).strip()

        # 连接词后面的组织名
        patterns = [
            r'(?:我们|咱们|本次|这次)?(?:是|和|与|跟|同|由)\s*([^\s，。、“”"（）()]{2,60}?(?:有限公司|集团|研究院|中心|学院|大学))'
        ]
        for pat in patterns:
            m = re.search(pat, s)
            if m:
                return m.group(1).strip()

        # 兜底找带后缀的
        suffix = r'(?:信息技术有限公司|科技有限公司|数据服务有限公司|智能设备有限公司|网络科技有限公司|软件有限公司|有限公司|集团|研究院|中心|学院|大学)'
        matches = re.findall(r'([^\s，。、“”"（）()]{2,60}?%s)' % suffix, s)
        if matches:
            return matches[-1].strip()

        return s

    def fallback_extract(self, field_name: str, text: str) -> Optional[str]:
        """
        规则兜底提取
        参数：
            field_name: 字段名
            text: 原文
        返回：
            提取的值或None
        """
        if field_name == "甲方单位" or "公司" in field_name:
            return self._fallback_company(text)
        elif field_name == "项目名称":
            return self._fallback_project(text)
        return None

    # ========== 私有方法 ==========

    def _normalize_date(self, s: str) -> str:
        """日期标准化"""
        s = s.replace("年", "-").replace("月", "-").replace("日", "").replace("号", "")
        s = s.replace("/", "-")
        s = re.sub(r"\s+", "", s)
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            y, mo, d = m.groups()
            return f"{y}-{int(mo):02d}-{int(d):02d}"
        return s

    def _normalize_money(self, s: str) -> str:
        """金额标准化"""
        s = s.replace(",", "")
        m = re.search(r"\d+(?:\.\d+)?", s)
        return m.group(0) if m else ""

    def _format_money(self, value: str, output_format: str) -> str:
        """金额格式化"""
        try:
            amount = Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            if output_format == "plain_number":
                if amount == amount.to_integral():
                    return str(int(amount))
                return format(amount, "f")
            elif output_format == "with_unit":
                if amount == amount.to_integral():
                    return f"{int(amount)}元"
                return f"{format(amount, 'f')}元"
            elif output_format == "currency_symbol":
                return f"￥{format(amount, '.2f')}"
            elif output_format == "cny_uppercase":
                return self._int_to_cny_upper(int(amount))
            else:
                return str(value)
        except:
            return value

    def _int_to_cny_upper(self, num: int) -> str:
        """整数转人民币大写"""
        if num == 0:
            return "零元整"

        sections = []
        unit_pos = 0
        temp = num

        while temp > 0:
            section = temp % 10000
            if section != 0:
                section_str = self._four_digit_to_cn(section)
                if self.cn_section[unit_pos]:
                    section_str += self.cn_section[unit_pos]
                sections.insert(0, section_str)
            else:
                if sections and not sections[0].startswith("零"):
                    sections.insert(0, "零")
            temp //= 10000
            unit_pos += 1

        result = "".join(sections)
        result = re.sub(r"零+", "零", result)
        result = result.rstrip("零")
        return result + "元整"

    def _four_digit_to_cn(self, num: int) -> str:
        """四位数转中文"""
        result = ""
        zero_flag = False
        digits = [int(x) for x in f"{num:04d}"]

        for i, d in enumerate(digits):
            pos = 3 - i
            if d == 0:
                zero_flag = True
            else:
                if zero_flag and result:
                    result += "零"
                result += self.cn_num[d] + self.cn_unit_int[pos]
                zero_flag = False
        return result

    def _format_date(self, value: str, output_format: str) -> str:
        """日期格式化"""
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
        if not m:
            return value

        y, mo, d = m.groups()
        mo_i = int(mo)
        d_i = int(d)

        if output_format == "YYYY-MM-DD":
            return f"{y}-{mo_i:02d}-{d_i:02d}"
        elif output_format == "YYYY年M月D日":
            return f"{y}年{mo_i}月{d_i}日"
        else:
            return value

    def _fallback_company(self, text: str) -> Optional[str]:
        """规则兜底提取公司名"""
        patterns = [
            r'([^\s，。、“”"（）()]{2,40}?信息技术有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?科技有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?数据服务有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?智能设备有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?网络科技有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?软件有限公司)',
            r'([^\s，。、“”"（）()]{2,40}?有限公司)',
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip()
        return None

    def _fallback_project(self, text: str) -> Optional[str]:
        """规则兜底提取项目名"""
        # 引号内容
        m = re.search(r'“([^”]{2,50})”', text)
        if m:
            return m.group(1).strip()

        patterns = [
            r'谈成的是([^，。]{2,50})这个项目',
            r'签的是([^，。]{2,50})这个项目',
            r'对应的(?:是)?([^，。]{2,50})项目',
            r'做的(?:是)?([^，。]{2,50})项目',
        ]
        for pat in patterns:
            m = re.search(pat, text)
            if m:
                return m.group(1).strip().strip('“”"')
        return None


# 全局单例
field_processor = FieldProcessor()