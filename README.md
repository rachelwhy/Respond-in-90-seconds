# A23 - AI Core: 异构文档理解与数据融合内核

![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-v0.100+-green.svg)
![RAG](https://img.shields.io/badge/Architecture-RAG-orange.svg)

## 📖 项目背景

在移动办公场景下，用户经常需要从复杂的 PDF、Word 或 Excel 中提取关键信息（如合同金额、到期日期、报销项目）。本项目作为 A23 赛题的算法后端，通过 **RAG (检索增强生成)** 技术，实现了对大篇幅异构文档的精准语义理解与结构化数据映射。



## ✨ 核心特性

* **多模态解析**：内置 `UniversalLoader`，支持 PDF/Word 中的表格自动还原为 Markdown 语义。
* **智能切片召回**：基于滑动窗口 (Sliding Window) 的文本分割，确保语义在分段时不丢失。
* **动态模式探测**：AI 自动根据用户指令（如“帮我看看合同”）推断需要提取的字段。
* **移动端优化**：针对 Android 低带宽环境，仅传输核心 JSON 数据，接口响应延迟低。
* **生产级安全**：全面采用环境变量隔离敏感秘钥，符合企业级安全标准。

---

## 🛠️ 环境准备与安装

### 1. 克隆项目
```bash
git clone [https://github.com/你的用户名/你的项目名.git](https://github.com/你的用户名/你的项目名.git)
cd ai_core
```

### 2. 安装依赖

建议使用  或  虚拟环境。`venv``conda`

bash

```
pip install -r requirements.txt
```

### 3. 配置 API Key （环境注入）

本系统必须检测到环境变量  才能启动。`DASHSCOPE_API_KEY`

**Windows（临时注入）：**

DOS

```
set DASHSCOPE_API_KEY=sk-xxxx你的真实Key
```

**Linux/Mac （临时注入）：**

bash

```
export DASHSCOPE_API_KEY="sk-你的真实Key"
```

------

## 🚀 启动与运行

启动算法后端服务器：

bash

```
python server.py
```

- **默认地址**：`http://0.0.0.0:8000`
- **API 文档**： 启动后访问 查看 Swagger UI。`http://localhost:8000/docs`

------

## 📡 接口规格说明 （Android 对接参考）

### 核心接口：文档分析

- **终点**：`/analyze`
- **方法**：`POST`
- **内容类型**：`multipart/form-data`

| **参数名**    | **类型** | **必选** | **说明**                          |
| ------------- | -------- | -------- | --------------------------------- |
| `file`        | 二进制   | 是       | 支持，.pdf、.docx、.xlsx、.txt    |
| `instruction` | 字符串   | 是       | 处理指令，如 "提取发票金额、税率" |

### 响应示例

JSON

```
{
  "success": true,
  "payload": {
    "data": {
      "发票金额": "5000.00",
      "税率": "6%"
    },
    "confidence": 0.98,
    "source_context": "..." 
  }
}
```

------

## 🧩 技术架构

1. **数据层**： 负责将异构文件转化为统一文本流。`document_loaders.py`
2. **检索层**： 负责在长文本中定位相关信息片段。`rag_engine.py`
3. **推理层**： 封装了对大模型的原子级调用与重试。`llm_client.py`
4. **接口层**： 提供高性能的 RESTful API 供安卓调用。`server.py`

------

## ⚠️ 注意事项

- **文件限制**：单个文件建议不超过 20MB，以确保移动端上传体验。
- **并发处理**：当前版本为演示版本，生产环境建议配合 Redis 队列。
- **隐私保护**：本系统不会永久存储用户上传的文件，处理完成后立即自动销毁缓存。