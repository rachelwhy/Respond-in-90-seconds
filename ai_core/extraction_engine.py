from typing import List, Dict, Any, Union
from .llm_client import llm_client
import json
import re

class ExtractionEngine:
    """信息抽取引擎：基于证据片段和结构化信息，提取字段"""

    def extract_fields(self,
                       evidence: Union[List[Dict], Dict[str, List[Dict]]],
                       instruction: str,
                       filename: str,
                       tables: List[str] = None,
                       lists: List[str] = None,
                       titles: List = None) -> List[Dict[str, Any]]:
        """
        从证据和文档结构中提取字段
        参数：
            evidence:
                - 如果是从旧版RAG来的列表，则全局检索，所有字段共用
                - 如果是从新版RAG来的字典，则每个字段有自己的证据列表
            instruction: 用户指令
            filename: 原始文件名
            tables: 文档中的表格（markdown格式）
            lists: 文档中的列表项
            titles: 文档中的标题
        返回：
            fields 列表（每个字段包含 name, value, evidence, retrieval_score, method）
        """
        fields = []

        # 1. 处理表格（直接解析）
        if tables:
            for table_idx, table_md in enumerate(tables):
                table_fields = self._parse_table_to_fields(table_md, table_idx, filename)
                fields.extend(table_fields)

        # 2. 处理列表（增强解析，拆分为更细粒度的字段）
        if lists:
            for list_idx, list_item in enumerate(lists):
                # 尝试拆分为结构化字段
                parsed = self._parse_list_item(list_item, list_idx, filename)
                if parsed:
                    fields.extend(parsed)
                else:
                    # 如果解析失败，保留原始列表项
                    fields.append({
                        "name": f"列表_{list_idx+1}",
                        "value": list_item,
                        "evidence": {
                            "text": list_item,
                            "source": filename,
                            "position": f"列表项 {list_idx+1}"
                        },
                        "retrieval_score": 1.0,
                        "method": "rule"
                    })

        # 3. 处理标题（保留层级信息）
        if titles:
            # titles 格式：[(level, title), ...]
            title_list = [{"level": level, "text": title} for level, title in titles]
            fields.append({
                "name": "文档标题结构",
                "value": title_list,
                "evidence": {
                    "text": f"共 {len(titles)} 个标题",
                    "source": filename
                },
                "retrieval_score": 1.0,
                "method": "rule"
            })

        # 4. 处理证据片段（LLM抽取）
        if evidence:
            if isinstance(evidence, list):
                # 旧模式：全局检索，所有字段共用证据
                fields.extend(self._extract_from_global_evidence(evidence, instruction, filename))
            elif isinstance(evidence, dict):
                # 新模式：每个字段有自己的证据
                fields.extend(self._extract_from_field_evidence(evidence, instruction, filename))
            else:
                print(f"⚠️ evidence 类型异常: {type(evidence)}")

        # 5. 去重
        fields = self._deduplicate_fields(fields)
        return fields

    def _extract_from_global_evidence(self, evidence: List[Dict], instruction: str, filename: str) -> List[Dict]:
        """处理全局证据（所有字段共用）"""
        fields = []
        context_parts = []
        for i, item in enumerate(evidence):
            context_parts.append(
                f"[片段{i+1} 行号:{item['start_line']}-{item['end_line']} 相关度:{item['score']:.2f}]\n{item['text']}"
            )
        context = "\n\n".join(context_parts)

        prompt = f"""
你是一个智能文档理解助手。请分析以下文档片段，提取最重要的字段信息。

用户需求：{instruction}

文档片段：
{context}

【严格要求】
- 只输出一个 JSON 对象，不要包含任何注释、解释或额外文字。
- JSON 必须语法正确且完整。
- 如果没有任何字段，输出 {{"fields": []}}。
- 请提取最重要的 5-10 个字段，避免生成过长的 JSON。
- 字段名应尽可能具体，例如“文化市场经营单位营业收入”而不是简单的“营业收入”。
- 输出格式如下：
{{
  "fields": [
    {{
      "name": "字段名",
      "value": "字段值",
      "evidence": {{
        "text": "支撑答案的原文片段",
        "position": "如：片段1 行号X-Y 或 表格2"
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
        result = llm_client.request(prompt, is_json=True)
        if isinstance(result, dict) and "fields" in result:
            for field in result["fields"]:
                # 从 evidence.position 提取片段编号
                position = field.get("evidence", {}).get("position", "")
                match = re.search(r'片段(\d+)', position)
                if match:
                    idx = int(match.group(1)) - 1
                    if 0 <= idx < len(evidence):
                        score = evidence[idx]["score"]
                    else:
                        score = evidence[0]["score"] if evidence else 0.0
                else:
                    score = evidence[0]["score"] if evidence else 0.0

                field["evidence"]["source"] = filename
                field["retrieval_score"] = score
                field["method"] = "llm"
                fields.append(field)
        else:
            print(f"⚠️ LLM返回格式异常: {result}")
        return fields

    def _extract_from_field_evidence(self, field_evidence: Dict[str, List[Dict]], instruction: str, filename: str) -> List[Dict]:
        """处理按字段检索的证据（每个字段有自己的证据列表）"""
        fields = []
        # 将所有字段的证据合并成一个上下文，但标注来源字段
        context_parts = []
        for field_name, ev_list in field_evidence.items():
            for i, item in enumerate(ev_list):
                context_parts.append(
                    f"[字段:{field_name} 片段{i+1} 行号:{item['start_line']}-{item['end_line']} 相关度:{item['score']:.2f}]\n{item['text']}"
                )
        context = "\n\n".join(context_parts)

        prompt = f"""
你是一个智能文档理解助手。请分析以下文档片段，提取最重要的字段信息。

用户需求：{instruction}

文档片段：
{context}

【严格要求】
- 只输出一个 JSON 对象，不要包含任何注释、解释或额外文字。
- JSON 必须语法正确且完整。
- 如果没有任何字段，输出 {{"fields": []}}。
- 字段名应尽可能具体。
- 输出格式如下：
{{
  "fields": [
    {{
      "name": "字段名",
      "value": "字段值",
      "evidence": {{
        "text": "支撑答案的原文片段",
        "position": "如：字段:合同金额 片段1 行号X-Y"
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
        result = llm_client.request(prompt, is_json=True)
        if isinstance(result, dict) and "fields" in result:
            for field in result["fields"]:
                field["evidence"]["source"] = filename
                # 从 position 提取字段名和片段编号，计算分数（简化处理，取该字段证据的平均分）
                position = field.get("evidence", {}).get("position", "")
                match = re.search(r'字段:([^\s]+)\s+片段(\d+)', position)
                if match:
                    field_name = match.group(1)
                    if field_name in field_evidence:
                        # 取该字段第一个证据的分数（或平均分）
                        score = field_evidence[field_name][0]["score"] if field_evidence[field_name] else 0.5
                    else:
                        score = 0.5
                else:
                    score = 0.5
                field["retrieval_score"] = score
                field["method"] = "llm"
                fields.append(field)
        else:
            print(f"⚠️ LLM返回格式异常: {result}")
        return fields

    def _parse_list_item(self, item: str, list_idx: int, filename: str) -> List[Dict]:
        """
        尝试将列表项解析为多个结构化字段。
        例如："娱乐场所4.5万个，从业人员41.8万人，营业收入589.6亿元，营业利润39.5亿元。"
        拆分为：
        - 娱乐场所数量: 4.5万个
        - 娱乐场所从业人员: 41.8万人
        - 娱乐场所营业收入: 589.6亿元
        - 娱乐场所营业利润: 39.5亿元
        返回字段列表，如果无法解析则返回空列表。
        """
        fields = []
        # 简单启发式：按逗号分割，每个部分尝试提取主体和数值
        parts = re.split(r'[，；]', item)
        if len(parts) < 2:
            return fields  # 不是典型的多指标列表项

        # 尝试提取列表项的主题（通常是开头的几个词）
        first_part = parts[0].strip()
        # 从第一个部分提取主题（连续的汉字）
        topic_match = re.search(r'^([\u4e00-\u9fa5]+)', first_part)
        if not topic_match:
            return fields
        topic = topic_match.group(1)

        for part in parts:
            part = part.strip()
            if not part:
                continue
            # 匹配数值及单位（如4.5万个）
            value_match = re.search(r'(\d+\.?\d*)([万千百十亿多]*[个万人元%]*)', part)
            if not value_match:
                continue
            value = value_match.group(0)  # 包含数值和单位的完整字符串
            # 提取指标名称：从part中移除数值部分，剩下的可能是指标名
            indicator = part.replace(value, '').strip()
            # 如果indicator为空，尝试从上下文中获取（例如第二个部分没有指标名，则沿用主题）
            if not indicator:
                indicator = topic
            else:
                # 如果indicator包含数字或符号，可能不是纯指标，跳过
                if re.search(r'\d', indicator):
                    continue
            # 构造字段名
            field_name = f"{topic}_{indicator}" if indicator != topic else topic
            fields.append({
                "name": field_name,
                "value": value,
                "evidence": {
                    "text": part,
                    "source": filename,
                    "position": f"列表项 {list_idx+1}"
                },
                "retrieval_score": 1.0,
                "method": "rule"
            })
        return fields

    def _parse_table_to_fields(self, table_md: str, table_idx: int, filename: str) -> List[Dict]:
        """
        解析Markdown表格为字段。
        增强逻辑：自动跳过表格前的标题行（如“表1 某某数据”），通过查找分隔线行来定位真正的表头和数据。
        """
        fields = []
        # 按行分割并过滤空行
        lines = [line.strip() for line in table_md.strip().split('\n') if line.strip()]
        if len(lines) < 3:
            return fields

        # 查找分隔线行（包含 --- 的行），定位表格起始
        sep_idx = -1
        for i, line in enumerate(lines):
            if '---' in line and '|' in line:
                sep_idx = i
                break

        if sep_idx != -1 and sep_idx > 0:
            # 找到分隔线，其上一行是表头
            header_line = lines[sep_idx - 1]
            data_start = sep_idx + 1
        else:
            # 未找到分隔线，回退到旧逻辑：第一行作为表头，从第三行开始作为数据
            header_line = lines[0]
            data_start = 2

        # 解析表头
        headers = [h.strip() for h in header_line.strip('|').split('|')]

        # 解析数据行
        for row_idx, line in enumerate(lines[data_start:]):
            if not line.strip():
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            # 确保cells长度与headers一致
            if len(cells) > len(headers):
                cells = cells[:len(headers)]
            elif len(cells) < len(headers):
                cells += [''] * (len(headers) - len(cells))

            row_data = dict(zip(headers, cells))
            fields.append({
                "name": f"表格{table_idx+1}_行{row_idx+1}",
                "value": row_data,
                "evidence": {
                    "text": line,
                    "source": filename,
                    "position": f"表格{table_idx+1} 第{row_idx+1}行"
                },
                "retrieval_score": 1.0,
                "method": "rule"
            })
        return fields

    def _deduplicate_fields(self, fields: List[Dict]) -> List[Dict]:
        """按 (name, value) 去重"""
        seen = set()
        unique = []
        for f in fields:
            value = f["value"]
            if isinstance(value, dict):
                value = json.dumps(value, sort_keys=True)
            key = f"{f['name']}_{value}"
            if key not in seen:
                seen.add(key)
                unique.append(f)
        return unique


extraction_engine = ExtractionEngine()