"""
抽取模块：基于证据片段和结构化信息提取字段
从你的代码迁移：extraction_engine.py 核心功能
融合魏的二次提取和缺失检测
"""

from typing import List, Dict, Any, Union, Optional
from .llm import llm_client
from .processor import field_processor
from . import prompts
import json
import re


class Extractor:
    """
    抽取器：从证据中提取字段
    包含：
        - 表格直接解析
        - 列表直接解析（优化命名）
        - LLM抽取
        - 二次提取
        - 字段去重
    """

    def extract(self,
                evidence: Union[List[Dict], Dict[str, List[Dict]]],
                instruction: str,
                filename: str,
                profile: Optional[Dict] = None,
                tables: List[str] = None,
                lists: List[str] = None,
                titles: List = None) -> List[Dict[str, Any]]:
        """
        从证据中提取字段
        参数：
            evidence: 证据片段
            instruction: 用户指令
            filename: 文件名
            profile: 字段配置
            tables: 表格列表
            lists: 列表项
            titles: 标题列表
        返回：
            字段列表
        """
        fields = []

        # 1. 表格直接解析
        if tables:
            for idx, table in enumerate(tables):
                fields.extend(self._parse_table(table, idx, filename))

        # 2. 列表直接解析（优化命名）
        if lists:
            for idx, item in enumerate(lists):
                fields.append(self._parse_list_item(item, idx, filename))

        # 3. 标题处理
        if titles:
            fields.append(self._parse_titles(titles, filename))

        # 4. LLM抽取
        if evidence:
            # 获取需要抽取的字段名
            field_names = []
            if profile and "fields" in profile:
                field_names = [f["name"] for f in profile["fields"]]

            # 构造上下文
            context = self._build_context(evidence)

            # 调用LLM
            prompt = prompts.build_extraction_prompt(instruction, context, field_names)
            result = llm_client.request(prompt, is_json=True)

            if isinstance(result, dict) and "fields" in result:
                for field in result["fields"]:
                    # 匹配证据分数
                    score = self._match_score(field.get("evidence", {}).get("position", ""), evidence)
                    # 分数保留3位小数
                    if score is not None:
                        score = round(score, 3)
                    field["evidence"]["source"] = filename
                    field["retrieval_score"] = score
                    field["method"] = "llm"
                    fields.append(field)

        # 5. 应用规则处理
        if profile:
            fields = self._apply_processing(fields, profile)

            # 6. 检查缺失，二次提取
            missing = self._check_missing(fields, profile)
            if missing:
                retry_fields = self._retry_missing(missing, evidence, instruction, filename, profile)
                if retry_fields:
                    fields.extend(retry_fields)
                    fields = self._apply_processing(fields, profile)

        # 7. 去重
        fields = self._deduplicate(fields)
        return fields

    def _parse_table(self, table_md: str, table_idx: int, filename: str) -> List[Dict]:
        """解析表格"""
        fields = []
        lines = [line.strip() for line in table_md.strip().split('\n') if line.strip()]
        if len(lines) < 3:
            return fields

        # 找分隔线
        sep_idx = -1
        for i, line in enumerate(lines):
            if '---' in line and '|' in line:
                sep_idx = i
                break

        if sep_idx != -1 and sep_idx > 0:
            header = lines[sep_idx - 1]
            data_start = sep_idx + 1
        else:
            header = lines[0]
            data_start = 2

        headers = [h.strip() for h in header.strip('|').split('|')]

        for row_idx, line in enumerate(lines[data_start:]):
            if not line.strip():
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            # 对齐长度
            if len(cells) > len(headers):
                cells = cells[:len(headers)]
            elif len(cells) < len(headers):
                cells += [''] * (len(headers) - len(cells))

            fields.append({
                "name": f"表格{table_idx+1}_行{row_idx+1}",
                "value": dict(zip(headers, cells)),
                "evidence": {
                    "text": line,
                    "source": filename,
                    "position": f"表格{table_idx+1} 第{row_idx+1}行"
                },
                "retrieval_score": 1.0,
                "method": "rule"
            })
        return fields

    def _parse_list_item(self, item: str, list_idx: int, filename: str) -> Dict:
        """
        解析列表项，尝试生成语义化的字段名
        优化：根据内容推断列表项主题
        """
        # 尝试从内容中提取主题关键词
        topic = f"列表_{list_idx+1}"  # 默认名称

        # 常见主题词识别
        topic_keywords = {
            "数据范围": ["包括", "未包括", "香港", "澳门", "台湾", "地区"],
            "数据来源": ["来源", "国家统计局", "人社部", "数据来源于"],
            "数据说明": ["四舍五入", "分项计", "总计", "增量", "增长率"],
            "统计口径": ["口径", "统计范围", "指标解释"],
            "时效说明": ["截至", "截止", "年末", "年底", "全年"]
        }

        for possible_topic, keywords in topic_keywords.items():
            for keyword in keywords:
                if keyword in item:
                    topic = possible_topic
                    break
            if topic != f"列表_{list_idx+1}":
                break

        return {
            "name": topic,
            "value": item,
            "evidence": {
                "text": item,
                "source": filename,
                "position": f"列表项 {list_idx+1}"
            },
            "retrieval_score": 1.0,
            "method": "rule"
        }

    def _parse_titles(self, titles: List, filename: str) -> Dict:
        """解析标题"""
        title_list = [{"level": level, "text": text} for level, text in titles]
        return {
            "name": "文档标题结构",
            "value": title_list,
            "evidence": {
                "text": f"共 {len(titles)} 个标题",
                "source": filename
            },
            "retrieval_score": 1.0,
            "method": "rule"
        }

    def _build_context(self, evidence: Union[List, Dict]) -> str:
        """构建上下文"""
        parts = []
        if isinstance(evidence, list):
            for i, item in enumerate(evidence):
                parts.append(
                    f"[片段{i+1} 行号:{item['start_line']}-{item['end_line']} 相关度:{item['score']:.2f}]\n{item['text']}"
                )
        else:
            for field, ev_list in evidence.items():
                for i, item in enumerate(ev_list):
                    parts.append(
                        f"[字段:{field} 片段{i+1} 行号:{item['start_line']}-{item['end_line']} 相关度:{item['score']:.2f}]\n{item['text']}"
                    )
        return "\n\n".join(parts)

    def _match_score(self, position: str, evidence: Union[List, Dict]) -> Optional[float]:
        """匹配证据分数"""
        if isinstance(evidence, list):
            match = re.search(r'片段(\d+)', position)
            if match:
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(evidence):
                    return evidence[idx]["score"]
            return evidence[0]["score"] if evidence else 0.0
        return 0.5

    def _apply_processing(self, fields: List[Dict], profile: Dict) -> List[Dict]:
        """应用规则处理"""
        field_configs = {f["name"]: f for f in profile.get("fields", [])}
        processed = []

        for field in fields:
            name = field["name"]
            if name in field_configs:
                config = field_configs[name]
                field_type = config.get("type", "text")
                output_format = config.get("output_format", "plain")
                raw = field["value"]

                # 特殊清洗
                if name == "甲方单位":
                    raw = field_processor.clean_org_name(raw)

                # 标准化
                norm = field_processor.normalize(raw, field_type)

                # 格式化
                formatted = field_processor.format(norm, field_type, output_format)

                field["value"] = formatted

            processed.append(field)
        return processed

    def _check_missing(self, fields: List[Dict], profile: Dict) -> List[str]:
        """检查缺失字段"""
        extracted = {f["name"] for f in fields}
        missing = []
        for item in profile.get("fields", []):
            if item.get("required", False) and item["name"] not in extracted:
                missing.append(item["name"])
        return missing

    def _retry_missing(self, missing: List[str], evidence: Union[List, Dict],
                        instruction: str, filename: str, profile: Dict) -> List[Dict]:
        """二次提取缺失字段"""
        if not missing:
            return []

        context = self._build_context(evidence)
        prompt = prompts.build_retry_prompt(missing, context)

        try:
            result = llm_client.request(prompt, is_json=True)
            if isinstance(result, dict):
                retry_fields = []
                for name in missing:
                    value = result.get(name, "")
                    if value and str(value).strip():
                        retry_fields.append({
                            "name": name,
                            "value": value,
                            "evidence": {
                                "text": "二次提取",
                                "source": filename,
                                "position": "二次提取"
                            },
                            "retrieval_score": 0.3,
                            "method": "llm_retry"
                        })
                return retry_fields
        except Exception as e:
            print(f"二次提取失败: {e}")
        return []

    def _deduplicate(self, fields: List[Dict]) -> List[Dict]:
        """字段去重"""
        seen = set()
        unique = []
        for f in fields:
            value = f["value"]
            if isinstance(value, dict):
                value = json.dumps(value, sort_keys=True)
            key = f"{f['name']}_{value}"
            if key not in seen:
                seen.add(key)
                # 分数保留3位小数
                if "retrieval_score" in f and f["retrieval_score"] is not None:
                    f["retrieval_score"] = round(f["retrieval_score"], 3)
                unique.append(f)
        return unique


# 全局单例
extractor = Extractor()