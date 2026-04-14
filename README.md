# Doc90

A23赛题算法后端：基于RAG的异构文档理解与信息抽取系统

## 项目简介

本项目作为A23赛题的算法后端，通过混合策略（规则预抽取+AI模型验证）实现了对大篇幅异构文档的精准语义理解与结构化数据映射。核心功能包括：

- **多格式文档解析**：支持Word、Excel、Markdown、TXT、PDF、图像（OCR）等多种格式
- **语义分块优先**：基于Docling的语义分块，保持文档结构完整性，支持合并单元格处理
- **混合抽取策略**：规则预抽取 + AI模型验证，兼顾准确性与语义理解
- **可配置后处理**：通用字段归一化框架，支持JSON配置的规则链
- **智能记录合并**：基于关键字段的记录去重与融合
- **多模型支持**：Ollama本地模型、DeepSeek API、OpenAI兼容API、Qwen等
- **RAG集成**：可选LangChain集成，自动降级到手写RAG
- **工程鲁棒性**：总超时控制、持久化缓存、任务状态管理、优雅降级

## 目录结构（与当前仓库一致）

> 详细技术流见 [A23_TECHNICAL_FLOW.md](A23_TECHNICAL_FLOW.md)；**全流程分支图（Mermaid）**见 [docs/RUNTIME_FLOW.md](docs/RUNTIME_FLOW.md)；**上线与接口**见 [HTTP_API_USAGE.md](HTTP_API_USAGE.md)；文档索引见 [docs/README.md](docs/README.md)。

```
Respond in 90 seconds_A23/
├── main.py                 # CLI 调试 / 异步任务子进程入口（生产主路径为 HTTP API）
├── api_server.py           # FastAPI（生产入口）
├── docs/                   # 部署说明与文档索引
├── src/
│   ├── adapters/           # 模型、Docling、解析器工厂、langextract 适配等
│   ├── api/                # direct_extractor、task_manager、qna_service
│   ├── core/               # 抽取服务、extraction_routing、后处理、reader、writers、profile 等
│   ├── knowledge/          # 别名与归一化等知识资源
│   └── config.py
├── third_party/            # 内嵌第三方（如 langextract）
├── tests/                  # 单元 / 集成 — .gitignore 可能忽略，按团队规范
├── scripts/                # 批处理 / 调试脚本
├── profiles/               # 模板 profile 示例
├── storage/                # API 任务与上传持久化
├── requirements.txt
├── CLAUDE.md
├── A23_TECHNICAL_FLOW.md
├── HTTP_API_USAGE.md
├── HOW_TO_USE_BATCH.md
├── install_windows_dependencies.bat
└── start_api_windows.bat
```

## 生产与调试

- **内网网页 / 后端集成**：以 **`api_server`** + `HTTP_API_USAGE.md` 为准；同步抽取走 `direct_extractor`，异步任务走 `task_manager`。
- **命令行 `main.py` 与 `scripts/`**：用于本地调试、批测，**不作为唯一运行形态**；与 API 共用 `src/core` 抽取核心。

## 安装

### 环境要求
- Python 3.11+
- Ollama（可选，用于本地模型服务）
- 8GB+ 内存（推荐16GB）

### 1. 克隆仓库
```bash
git clone https://github.com/rachelwhy/Respond-in-90-seconds.git
cd Respond-in-90-seconds
```

### 2. 安装依赖
```bash
# Windows一键安装
install_windows_dependencies.bat

# 手动安装
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt

# 可选RAG集成（LangChain）
pip install langchain langchain-community chromadb

# OCR系统依赖（需要单独安装）
# 1. Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
# 2. Poppler工具（PDF转图像）: https://github.com/oschwartz10612/poppler-windows
```

### 3. 配置模型
创建 `.env` 文件（参考 `.env.example`）：

```ini
# DeepSeek API配置
A23_MODEL_TYPE=deepseek
A23_DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
A23_DEEPSEEK_API_KEY=sk-your-api-key-here
A23_DEEPSEEK_MODEL=deepseek-chat

# Ollama配置
A23_MODEL_TYPE=ollama
A23_OLLAMA_MODEL=qwen2.5:7b

# 通用配置
A23_TARGET_LIMIT_SECONDS=40
A23_FUZZY_THRESHOLD=75
A23_NORMALIZATION_CONFIG=src/knowledge/field_normalization_rules.json
A23_ENABLE_OCR=false
```

### 4. 准备模型（选择一种）
```bash
# Ollama本地模型
ollama pull qwen2.5:7b

# 或使用DeepSeek API（需配置API密钥）
# 无需本地模型
```

## 快速开始

### 方式1：命令行处理
```bash
# 智能抽取模式（推荐）
python main.py \
  --template "data/template/generic_template.xlsx" \
  --input-dir "test/inputs/Excel/2025山东省环境空气质量监测数据信息.xlsx" \
  --output-dir "test/results/output" \
  --overwrite-output

# 纯规则抽取模式
python main.py --llm-mode off ...

# 兼容模式（supplement 会映射为 full）
python main.py --llm-mode supplement ... # 等价于 --llm-mode full

# 完整AI抽取模式（默认，Docling语义分块 + LLM）
python main.py --llm-mode full ...

# 控制语义分块处理数量
python main.py --max-chunks 100 ...

# 总超时控制（3分钟）
python main.py --total-timeout 180 ...
```

### 方式2：启动HTTP服务
```bash
# Windows
start_api_windows.bat

# 手动启动
uvicorn api_server:app --host 0.0.0.0 --port 8000
```
访问 http://localhost:8000/docs 查看接口文档

## API接口

启动服务后访问：http://localhost:8000/docs

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/health` | GET | 健康检查 |
| `/api/tasks` | GET | 任务列表（受 `A23_ENABLE_TASKS` 控制） |
| `/api/tasks/create` | POST | 创建异步任务（multipart） |
| `/api/tasks/{task_id}` | GET | 任务状态与输出路径 |
| `/api/tasks/{task_id}/result` | GET | 任务结果摘要 |
| `/api/tasks/{task_id}/export-complete` | POST | 后端确认导出后清理任务 |
| `/api/extract/direct` | POST | 同步直接抽取 |
| `/api/extract/no-template` | POST | 无模板抽取 |
| `/api/download/temp/{filename}/export-complete` | POST | 后端确认临时导出已接收并删除 |
| `/api/qna/ask` | POST | 文档问答（可选 LangChain） |

详细说明见 [HTTP_API_USAGE.md](HTTP_API_USAGE.md)

## 行为与环境变量（API / 任务）

- **`A23_ENABLE_TASKS`**：为 `false` 时 `/api/tasks/*` 不可用。
- **`A23_PERSIST_UPLOADS` / `A23_PERSIST_PROFILES`**：是否持久化上传与自动生成 profile。
- **`A23_TASK_RETENTION_HOURS` 等**：任务与上传目录清理策略（小时）。
- **`A23_DEBUG`**：调试模式；`report_bundle` 调试产物仅在调试链路开放。
- 异步任务由子进程执行 `main.py`；子进程设置 **`PYTHONUNBUFFERED=1`** 便于日志落盘。外层 watchdog 约为 **`total_timeout + 300` 秒**（缓冲），与 `main.py --total-timeout` 配合使用。

### 后端对接输出约定

- 对外返回的 `output_files` 默认不包含 `report_bundle`。
- 业务侧应使用 `result_json/result_xlsx`（或 `by_input`）进行消费。
- `/api/extract/no-template` 生成结构化文件时会返回 `download_url`，后端拉取完成后可调用导出确认接口触发清理。

## Profile配置示例

profiles/contract.json：
```json
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
  ],
  "dedup_key_fields": ["合同编号", "签订日期"],
  "prefer_non_empty": true
}
```

系统支持可配置的字段归一化规则，配置文件位于 `src/knowledge/field_normalization_rules.json`：
```json
{
  "rules": [
    {
      "field_name": "增长率",
      "type": "percentage",
      "operations": ["strip_percent", "to_float", "round_decimal:2"]
    }
  ]
}
```

## 测试结果（2026.04.10）

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
```python
import sys
sys.path.insert(0, '项目绝对路径')
```

### Q2: Ollama连接失败
A: 确认Ollama服务已启动：
```bash
ollama serve
ollama list
```

### Q3: 内存不足
A: 可尝试更小的模型：
```bash
ollama pull qwen2.5:3b
修改 .env 中的 OLLAMA_MODEL=qwen2.5:3b
```

### Q4: DeepSeek API调用失败
A: 检查API密钥是否正确，网络是否可访问DeepSeek API。

### Q5: OCR功能无法使用
A: 确认已安装Tesseract和Poppler，并添加到系统PATH。

## 架构升级说明（v3.0）

系统已完成从"低层次文本切片 + 硬编码后处理"到"深度Docling语义结构 + 可配置通用后处理 + 工程鲁棒性"的架构升级，主要改进包括：

1. **语义理解增强**: Docling语义分块，合并单元格处理，智能记录合并
2. **可配置后处理框架**: FieldNormalizer通用字段归一化框架
3. **工程鲁棒性提升**: 总超时控制，持久化存储，任务状态管理
4. **参数体系优化**: `--llm-mode`参数替代旧的`--use-rules-only`
5. **向后兼容性**: 兼容现有API接口和命令行参数

## 团队

| 姓名   | 角色     |
| ------ | -------- |
| 王学涵 | 前端     |
| 林卓均 | UI+管理  |
| 吴启锐 | RAG检索  |
| 诸凯杰 | 后端     |
| 魏嘉华 | 字段抽取 |

## 许可证

MIT License © 2026 Doc90 团队
