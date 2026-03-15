"""
交互调度模块：统一处理用户输入，根据意图分发到各功能模块
只负责调度，不包含任何业务逻辑
"""

from typing import List, Dict, Any, Optional
from .intent import intent_recognizer
from .qa import qa_engine
from .core import processor
from .columnreader import columnreader  # 新增导入
from .llm import llm_client
import os


class InteractiveDispatcher:
    """
    交互调度器
    职责：接收用户输入，调用意图识别，分发到具体模块
    完全解耦，不包含业务逻辑
    """

    def dispatch(self,
                 hint: str,
                 documents: List[str] = None,
                 doc_sources: Optional[List[str]] = None,
                 template_path: Optional[str] = None,
                 file_path: Optional[str] = None,
                 session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        分发用户请求
        参数：
            hint: 用户输入
            documents: 文档列表（用于问答）
            doc_sources: 文档来源
            template_path: 模板路径（用于读取列名）
            file_path: 文件路径（用于抽取等）
            session_id: 会话ID（用于多轮问答）
        返回：
            各模块返回的结果
        """
        # 1. 识别意图
        intent = intent_recognizer.recognize(hint)
        print(f"🔍 识别到意图: {intent}")

        # 2. 记录原始请求
        result = {
            "intent": intent,
            "original_hint": hint
        }

        # 3. 根据意图分发
        try:
            if intent == "qa":
                # 问答
                if not documents:
                    result["error"] = "问答需要提供文档"
                else:
                    qa_result = qa_engine.answer(
                        question=hint,
                        documents=documents,
                        doc_sources=doc_sources,
                        session_id=session_id
                    )
                    result.update({
                        "type": "qa",
                        "content": qa_result["answer"],
                        "evidence": qa_result["evidence"],
                        "sources": qa_result["sources"]
                    })
                    if "session_id" in qa_result:
                        result["session_id"] = qa_result["session_id"]

            elif intent == "fill":
                # 填表 - 只读取列名，不写Excel
                if not template_path or not file_path:
                    result["error"] = "填表需要提供模板文件和文档"
                else:
                    try:
                        # 1. 读取列名
                        columns = columnreader.read(template_path)

                        # 2. 用列名指导抽取
                        instruction = f"{hint}，请按照以下列名提取数据：{columns}"
                        extract_result = processor.process(
                            file_path=file_path,
                            instruction=instruction,
                            output_format="list"  # 用现有的list格式
                        )

                        # 3. 返回数据和列名
                        result["data"] = {
                            "columns": columns,
                            "rows": extract_result.get("fields", []),  # fields就是数据行
                            "row_count": len(extract_result.get("fields", []))
                        }

                    except Exception as e:
                        result["error"] = f"处理失败: {str(e)}"

            elif intent == "extract":
                # 抽取
                if not file_path:
                    result["error"] = "抽取需要提供文档"
                else:
                    extract_result = processor.process(
                        file_path=file_path,
                        instruction=hint
                    )
                    result.update(extract_result)

            elif intent in ["summarize", "translate", "analyze"]:
                llm_result = self._handle_with_llm(intent, hint, file_path or documents)
                result.update(llm_result)

            else:
                llm_result = self._fallback_to_llm(hint, file_path or documents)
                result.update(llm_result)

        except Exception as e:
            result["error"] = str(e)
            result["status"] = "failed"

        return result

    def _handle_with_llm(self, intent: str, hint: str, source) -> Dict:
        """用LLM处理总结/翻译/分析"""
        text = self._get_text_from_source(source)

        prompts = {
            "summarize": f"请总结以下内容：\n\n{text}",
            "translate": f"请将以下内容翻译成中文：\n\n{text}",
            "analyze": f"请分析以下内容：\n\n{text}"
        }

        prompt = prompts.get(intent, f"请处理：{hint}\n\n{text}")
        content = llm_client.request(prompt, is_json=False)

        return {
            "type": intent,
            "content": content
        }

    def _fallback_to_llm(self, hint: str, source) -> Dict:
        """兜底：直接LLM回答"""
        text = self._get_text_from_source(source)

        prompt = f"""
用户说：{hint}

相关文档：
{text}

请根据文档内容直接回答用户的问题。
如果文档中没有相关信息，就根据自己的知识回答。
"""
        answer = llm_client.request(prompt, is_json=False)

        return {
            "type": "direct_answer",
            "content": answer
        }

    def _get_text_from_source(self, source) -> str:
        """从source中提取文本"""
        if not source:
            return ""

        if isinstance(source, list):
            source = source[0] if source else ""

        if isinstance(source, str):
            if source.endswith(('.docx', '.txt', '.md')):
                from .loader import document_loader
                doc_info = document_loader.load(source, os.path.basename(source))
                if "error" not in doc_info:
                    if doc_info.get("type") in ["word", "markdown"]:
                        return "\n".join(doc_info.get("paragraphs", []))[:2000]
                    else:
                        return doc_info.get("text", "")[:2000]
            else:
                return source[:2000]

        return ""


# 全局单例
dispatcher = InteractiveDispatcher()