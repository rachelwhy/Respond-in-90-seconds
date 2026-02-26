import os
from document_loaders import loader
from core_engine import engine


def api_gateway(file_path, user_instruction, target_template=None):
    """
    安卓 App 调用的唯一入口
    支持格式: .pdf, .docx, .doc, .xlsx, .xls, .txt, .csv
    """
    if not os.path.exists(file_path):
        return {"success": False, "error": "文件不存在"}

    # 获取小写后缀名
    ext = file_path.lower().split('.')[-1]

    try:
        # 1. 路由分发解析 (将不同格式统一转化为文本流)
        if ext == 'pdf':
            text_stream = loader.load_pdf(file_path)
        elif ext in ['docx', 'doc']:
            text_stream = loader.load_docx(file_path)
        elif ext in ['xlsx', 'xls', 'csv']:
            text_stream = loader.load_excel(file_path)
        else:
            # 默认按纯文本处理
            with open(file_path, 'r', encoding='utf-8') as f:
                text_stream = f.read()

        # 2. 调用 AI 核心逻辑
        # 注意：此处必须对应 core_engine.py 中的 UniversalProcessor.process 方法
        final_data = engine.process(text_stream, user_instruction, target_template)

        return {"success": True, "payload": final_data}

    except Exception as e:
        return {"success": False, "error": f"算法层处理失败: {str(e)}"}
