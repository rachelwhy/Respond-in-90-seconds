# Respond in 90 seconds_A23

A23赛题算法后端：基于RAG的异构文档理解与信息抽取系统

## 项目简介

本项目作为A23赛题的算法后端，通过RAG（检索增强生成）技术，实现了对大篇幅异构文档的精准语义理解与结构化数据映射。核心功能包括：

- 多格式文档解析：支持Word、Excel、Markdown、TXT等格式
- 智能检索（RAG）：滑动窗口切片 + 向量检索 + 关键词扩展
- 字段抽取：从证据片段中提取结构化字段
- 规则引擎：字段标准化、格式化、兜底规则
- 智能问答：基于文档内容的问答系统
- 异步任务处理：支持大文件后台处理，不阻塞请求

## 目录结构

```
Respond-in-90-seconds/
├── ai_core/                          # AI核心模块
│   ├── __init__.py                    # 模块入口
│   ├── core.py                         # 核心协调器
│   ├── retriever.py                    # RAG检索模块
│   ├── extractor.py                    # 字段抽取模块
│   ├── processor.py                    # 规则引擎
│   ├── llm.py                          # LLM客户端
│   ├── loader.py                       # 文档加载器
│   ├── qa.py                           # 问答引擎
│   ├── prompts.py                      # prompt模板
│   ├── config.py                       # 配置管理
│   ├── utils.py                        # 工具函数
│   └── exceptions.py                   # 自定义异常
│
├── tests/                              # 测试脚本
│   └── batch_test.py                   # 批量测试
├── profiles/                           # profile配置文件
│   └── contract.json                   # 示例配置
├── data/                               # 测试数据目录
│   └── in/                             # 输入数据
├── output/                             # 输出目录
├── .env                                # 环境变量
├── .gitignore                          # Git忽略文件
├── requirements.txt                    # 依赖包
├── README.md                           # 项目说明
├── server.py                           # HTTP服务
└── test_results.csv                    # 测试结果
```

## 安装

### 环境要求
- Python 3.8+
- Ollama（本地模型服务）
- 8GB+ 内存（推荐16GB）

### 1. 克隆仓库
git clone https://github.com/rachelwhy/Respond-in-90-seconds.git
cd Respond-in-90-seconds

### 2. 安装依赖

pip install -r requirements.txt

### 3. 配置Ollama

## 拉取所需模型

ollama pull qwen2.5:7b
ollama pull nomic-embed-text

## 启动Ollama服务

ollama serve

### 4. 配置环境变量
创建 .env 文件：
MODEL_BACKEND=ollama
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
EMBEDDING_MODEL=nomic-embed-text

## 快速开始

### 方式1：直接调用
from ai_core import processor

result = processor.process(
    file_path="data/合同.docx",
    instruction="提取合同金额和签订日期",
    output_format="dict"
)
print(result["data"])

### 方式2：启动HTTP服务
python server.py
访问 http://localhost:8000/docs 查看接口文档

### 方式3：批量测试
python tests/batch_test.py

## API接口

启动服务后访问：http://localhost:8000/docs

| 接口                 | 方法 | 说明             |
| -------------------- | ---- | ---------------- |
| /api/extract         | POST | 提交文档抽取任务 |
| /api/ask             | POST | 提交问答任务     |
| /api/tasks/{task_id} | GET  | 查询任务结果     |
| /api/tasks           | GET  | 查看所有任务     |

## Profile配置示例

profiles/contract.json：
{
  "report_name": "合同信息抽取",
  "instruction": "提取合同中的关键信息",
  "fields": [
    {
      "name": "合同金额",
      "type": "money",
      "required": true,
      "output_format": "cny_uppercase"
    },
    {
      "name": "签订日期",
      "type": "date",
      "required": true,
      "output_format": "YYYY年M月D日"
    }
  ]
}

## 测试结果（2026.03.13）

| 指标        | 数值    |
| ----------- | ------- |
| 总文件数    | 16个    |
| 成功率      | 100%    |
| 平均耗时    | 11.30秒 |
| 最快        | 0.58秒  |
| 最慢        | 28.33秒 |
| 超时 (>90s) | 0个     |

## 常见问题

### Q1: 启动服务时报错“ModuleNotFoundError”
A: 确认在项目根目录运行，或添加：
import sys
sys.path.insert(0, '项目绝对路径')

### Q2: Ollama连接失败
A: 确认Ollama服务已启动：
ollama serve
ollama list

### Q3: 内存不足
A: 可尝试更小的模型：
ollama pull qwen2.5:3b
修改 .env 中的 OLLAMA_MODEL=qwen2.5:3b

## 更新日志

### v2.0.0 (2026.03.13)

- 完成核心模块重构
- 融合RAG检索与规则引擎
- 支持profile配置
- 批量测试100%通过
- 提供完整HTTP API

## 团队

| 姓名   | 角色     |
| ------ | -------- |
| 王学涵 | 前端     |
| 林卓均 | UI+管理  |
| 吴启锐 | RAG检索  |
| 诸凯杰 | 后端     |
| 魏嘉华 | 字段抽取 |

## 许可证

MIT License © 2026 Respond in 90 seconds_A23 团队
