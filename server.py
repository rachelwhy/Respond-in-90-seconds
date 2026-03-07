import os
import shutil
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uuid
from typing import Dict, List, Optional

# 统一导入 - 都使用小写导出名
from .document_loaders import document_loader
from .core_engine import core_engine
from .qa_engine import qa_engine

app = FastAPI(title="A23 AI Core API")

# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 任务状态存储
task_status: Dict[str, dict] = {}


# ==================== 文档解析工具 ====================

def parse_document(file_path: str, filename: str) -> str:
    """解析文档为文本"""
    return document_loader.load(file_path, filename)


# ==================== 抽取接口 ====================

async def process_extract_task(task_id: str, instruction: str, file_path: str, filename: str):
    """后台任务：处理文档抽取"""
    try:
        task_status[task_id] = {"status": "processing", "type": "extract"}

        # 解析文档
        text = parse_document(file_path, filename)

        # 调用抽取引擎
        result = core_engine.process(text, instruction)

        task_status[task_id] = {
            "status": "completed",
            "type": "extract",
            "result": result
        }
    except Exception as e:
        task_status[task_id] = {"status": "failed", "type": "extract", "error": str(e)}
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


@app.post("/api/extract")
async def submit_extract_task(
        background_tasks: BackgroundTasks,
        instruction: str = Form(...),
        file: UploadFile = File(...)
):
    """
    文档抽取任务
    - 上传一个文件
    - 返回结构化数据
    """
    task_id = str(uuid.uuid4())
    temp_path = f"cache_extract_{task_id}_{file.filename}"

    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    task_status[task_id] = {"status": "pending", "type": "extract"}
    background_tasks.add_task(process_extract_task, task_id, instruction, temp_path, file.filename)

    return {"task_id": task_id, "type": "extract"}


# ==================== 问答接口 ====================

async def process_qa_task(task_id: str, question: str, file_paths: List[str], file_names: List[str]):
    """后台任务：处理智能问答"""
    try:
        task_status[task_id] = {"status": "processing", "type": "qa"}

        # 解析所有文件
        documents = []
        doc_sources = []

        for path, name in zip(file_paths, file_names):
            text = parse_document(path, name)
            if text and not text.startswith("不支持的文件类型"):
                documents.append(text)
                doc_sources.append(name)

        if not documents:
            task_status[task_id] = {
                "status": "completed",
                "type": "qa",
                "result": {
                    "data": {"answer": "没有可用的文档内容"},
                    "confidence": 0.0,
                    "needs_human_review": False
                }
            }
            return

        # 调用问答引擎
        result = qa_engine.answer(question, documents, doc_sources)

        task_status[task_id] = {
            "status": "completed",
            "type": "qa",
            "result": result
        }
    except Exception as e:
        task_status[task_id] = {"status": "failed", "type": "qa", "error": str(e)}
    finally:
        for path in file_paths:
            if os.path.exists(path):
                os.remove(path)


@app.post("/api/ask")
async def submit_qa_task(
        background_tasks: BackgroundTasks,
        question: str = Form(...),
        files: List[UploadFile] = File(...)
):
    """
    智能问答任务
    - 上传多个文件作为知识库
    - 提出一个问题
    - 返回答案和证据
    """
    task_id = str(uuid.uuid4())
    file_paths = []
    file_names = []

    # 保存所有上传的文件
    for file in files:
        temp_path = f"cache_qa_{task_id}_{file.filename}"
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        file_paths.append(temp_path)
        file_names.append(file.filename)

    task_status[task_id] = {"status": "pending", "type": "qa"}
    background_tasks.add_task(process_qa_task, task_id, question, file_paths, file_names)

    return {"task_id": task_id, "type": "qa"}


# ==================== 通用任务查询接口 ====================

@app.get("/api/tasks/{task_id}")
async def get_task_detail(task_id: str):
    """查询任何类型的任务详情"""
    if task_id not in task_status:
        return JSONResponse(status_code=404, content={"error": "task not found"})

    info = task_status[task_id]

    if info["status"] == "pending":
        return {"task_id": task_id, "status": "pending", "type": info.get("type")}
    elif info["status"] == "processing":
        return {"task_id": task_id, "status": "processing", "type": info.get("type")}
    elif info["status"] == "failed":
        return {"task_id": task_id, "status": "failed", "type": info.get("type"), "error": info.get("error")}
    else:  # completed
        return {
            "task_id": task_id,
            "status": "completed",
            "type": info.get("type"),
            "result": info.get("result")
        }


@app.get("/api/tasks")
async def get_task_list(type: Optional[str] = None):
    """查询任务列表，可按类型筛选"""
    tasks = []
    for tid, info in task_status.items():
        if type is None or info.get("type") == type:
            tasks.append({
                "task_id": tid,
                "status": info["status"],
                "type": info.get("type")
            })
    return {"tasks": tasks}


if __name__ == "__main__":
    print("--- 算法后端已启动，监听 8000 端口 ---")
    print("文档抽取接口: POST /api/extract")
    print("智能问答接口: POST /api/ask")
    print("任务查询接口: GET /api/tasks/{task_id}")
    print("接口文档: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)