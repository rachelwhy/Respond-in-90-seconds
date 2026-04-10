# Respond in 90 seconds_A23

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

## 目录结构

Respond-in-90-seconds/
├── src/                    # 源代码
│   ├── algorithm/         # 算法接口层
│   ├── pipeline/          # 流程编排器
│   ├── extractors/        # 字段抽取器
│   ├── engine/           # 引擎层（模型、文档、检索）
│   ├── parsers/          # 多格式文档解析器
│   ├── knowledge/        # 领域知识库
│   ├── config.py         # 配置管理
│   └── runtime_config.py # 运行时配置
├── tests/                # 测试用例
├── scripts/             # 工具脚本
├── profiles/            # 模板配置文件
├── storage/             # 上传文件存储
├── requirements.txt     # Python依赖
├── main.py             # 命令行入口
├── api_server.py       # HTTP API入口
├── CLAUDE.md           # Claude代码助手指南
├── HOW_TO_USE_BATCH.md # 批量使用指南
├── HTTP_API_USAGE.md   # HTTP API使用文档
├── install_windows_dependencies.bat  # Windows一键安装脚本
└── start_api_windows.bat             # Windows启动脚本

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

# 补充抽取模式（规则预抽取 + AI补充缺失字段）
python main.py --llm-mode supplement ...

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

| 接口                 | 方法 | 说明             |
| -------------------- | ---- | ---------------- |
| /api/extract         | POST | 提交文档抽取任务 |
| /api/ask             | POST | 提交问答任务     |
| /api/tasks/{task_id} | GET  | 查询任务结果     |
| /api/tasks           | GET  | 查看所有任务     |
| /api/health          | GET  | 健康检查         |

详细API文档请参考 [HTTP_API_USAGE.md](HTTP_API_USAGE.md)

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

## 架构升级说明（v2.0）

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

MIT License © 2026 Respond in 90 seconds_A23 团队