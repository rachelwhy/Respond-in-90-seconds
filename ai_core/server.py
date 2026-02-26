import os
import shutil
import uvicorn
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from main import api_gateway

app = FastAPI(title="A23 AI Core API")

# 跨域配置：允许安卓端从不同 IP 访问接口
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze")
async def analyze(
        instruction: str = Form(...),
        file: UploadFile = File(...)
):
    """
    安卓端调用入口：
    instruction: 用户的处理指令（如：提取合同金额）
    file: 手机端上传的文档流
    """
    # 1. 生成临时缓存路径
    temp_path = f"cache_{file.filename}"

    try:
        # 保存上传文件到服务器本地缓存
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 2. 调用算法网关 (进入 PDF/Word 表格解析流程)
        result = api_gateway(temp_path, instruction)
        return result

    except Exception as e:
        return {"success": False, "error": f"服务器内部错误: {str(e)}"}

    finally:
        # 3. 始终清理临时文件，释放空间
        if os.path.exists(temp_path):
            os.remove(temp_path)


if __name__ == "__main__":
    # host 0.0.0.0 允许手机在局域网内通过电脑 IP 访问
    print("--- 算法后端已启动，正在监听 8000 端口 ---")
    uvicorn.run(app, host="0.0.0.0", port=8000)