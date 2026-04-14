# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# A23 AI 文档处理系统

## 项目概述
企业内网AI文档处理系统，支持模板填表和文档问答。采用混合策略（规则预抽取+AI模型验证）实现文档结构化提取。

**上线形态**：以 **`api_server`（FastAPI）** 与 `src/api/*` 为生产主路径；`main.py` 与 `scripts/` 用于调试、批测与异步任务子进程。抽取路由摘要见 `src/core/extraction_routing.py`（返回 `metadata.pipeline_routing`），详见 `docs/DEPLOYMENT.md`。

## 技术栈
- Python 3.11+, FastAPI, Ollama
- **主要依赖**：openpyxl, python-docx, requests, rapidfuzz, openai, python-dotenv
- **文档解析增强**：docling（语义分块、表格合并单元格处理）, langextract
- **向量检索**：sentence-transformers（语义相似度计算）
- **缓存与监控**：diskcache（持久化缓存）, prometheus-client（指标监控）
- **OCR支持**：Tesseract, Pillow, opencv-python, pdf2image
- **数据库**：pymysql（MySQL入库）
- **可选RAG集成**：langchain, langchain-community, chromadb（自动降级到手写RAG）
- **认证**：python-jose, passlib

## 常用命令

### 环境设置
```bash
# Windows一键安装依赖
install_windows_dependencies.bat  # 自动创建Python虚拟环境并安装所有依赖

# 手动安装
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/macOS
pip install -r requirements.txt

# 新架构依赖（已包含在requirements.txt中）
# - docling>=2.0.0: 文档语义分块与解析
# - langextract>=0.1.0: 语言提取工具
# - sentence-transformers>=2.2.0: 语义相似度计算（可选）
# - diskcache>=5.6.3: 持久化缓存
# - prometheus-client>=0.20.0: 监控指标
# - pymysql>=1.1.0: MySQL数据库支持

# 可选RAG集成（LangChain，需单独安装）
pip install langchain langchain-community chromadb
# 安装后，qna_service.py将自动切换到LangChain ConversationalRetrievalChain

# OCR系统依赖（需要单独安装）
# 1. Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
# 2. Poppler工具（PDF转图像）: https://github.com/oschwartz10612/poppler-windows

# 模型准备（选择一种）
# 1. Ollama本地模型: ollama pull qwen2.5:7b
# 2. DeepSeek云API: 配置 .env 中的 A23_DEEPSEEK_API_KEY
# 3. OpenAI兼容API: 配置本地Qwen服务地址
```

### 运行系统
```bash
# 启动HTTP API服务（Windows）
start_api_windows.bat  # 启动FastAPI服务，监听0.0.0.0:8000

# 手动启动API服务
uvicorn api_server:app --host 0.0.0.0 --port 8000

# 访问API文档
# http://127.0.0.1:8000/docs
```

### 命令行处理
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
python main.py --llm-mode supplement ... # 等价于 full

# 完整AI抽取模式（默认，Docling语义分块 + LLM）
python main.py --llm-mode full ...

# DeepSeek API测试连接
python test_deepseek_connection.py

# 新架构参数说明
# LLM抽取模式
python main.py --llm-mode full ...      # 默认模型抽取
python main.py --llm-mode supplement ... # 兼容别名，等价于 full
python main.py --llm-mode off ...       # 仅规则抽取（替代旧的--use-rules-only）

# 超时控制
python main.py --total-timeout 180 ...  # 设置3分钟总超时

# 语义分块控制
python main.py --max-chunks 100 ...     # 最多处理100个语义块
python main.py --quiet ...              # 安静模式，禁用控制台进度输出

# 兼容性参数（保留向后兼容）
python main.py --slice-size 3000 ...    # 字符切片大小（仅在无语义分块时使用）
python main.py --overlap 200 ...        # 字符切片重叠大小（仅在无语义分块时使用）
```

### 批量处理与测试
```bash
# 生成DeepSeek测试任务清单
python scripts/generate_deepseek_manifest.py

# 运行批量测试
python scripts/run_batch.py \
  --manifest "test/manifests/deepseek_full_test.json" \
  --main-script main.py \
  --validate \
  --collect-metrics \
  --output-report test/reports/benchmark_report.json

# 运行单文件测试示例（当前仓库保留的用例）
python -m pytest tests/test_extraction_routing.py -v
# 运行全部保留的 tests（不含已移除的陈旧集成/单元用例）
python -m pytest tests/ -q
```

### 模板管理
```bash
# 创建通用模板
python scripts/create_generic_template.py
```

### 配置示例
```bash
# .env 文件配置示例
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

**环境变量说明：**
- `A23_MODEL_TYPE`: 模型类型，可选 `deepseek`、`ollama`、`openai`、`qwen`
- `A23_DEEPSEEK_API_KEY`: DeepSeek API密钥（当使用DeepSeek时）
- `A23_OLLAMA_MODEL`: Ollama模型名称，如 `qwen2.5:7b`
- `A23_TARGET_LIMIT_SECONDS`: 单次模型调用超时时间（秒）
- `A23_FUZZY_THRESHOLD`: 字段别名模糊匹配阈值（0-100，默认75）
- `A23_NORMALIZATION_CONFIG`: 字段归一化规则配置文件路径
- `A23_ENABLE_OCR`: 是否启用OCR功能（true/false）

## 高层架构

### 系统层次
1. **应用入口层**:
   - `api_server.py` - FastAPI HTTP 服务（生产主入口）
   - `main.py` - CLI / 批测 / 异步任务子进程入口
2. **API 编排层**:
   - `src/api/direct_extractor.py` - 同步抽取编排
   - `src/api/task_manager.py` - 异步任务管理与子进程执行
   - `src/api/qna_service.py` - 文档问答服务
3. **核心抽取层（src/core）**:
   - `extraction_service.py` - 抽取主链路（切片、模型调用、合并）
   - `extraction_routing.py` - 抽取路由元数据（`pipeline_routing`）
   - `reader.py` - 输入聚合与语义分块
   - `profile.py` / `template_detector.py` - 模板识别与 profile 生成
   - `postprocess.py` / `field_interpreter.py` - 字段清洗、解释与后处理
   - `writers.py` - Excel/Word/JSON 写回
4. **适配层（src/adapters）**:
   - `model_client.py` - 模型后端统一调用（Ollama/OpenAI/Qwen/DeepSeek）
   - `docling_adapter.py` - Docling 解析与语义块
   - `langextract_adapter.py` - LangExtract 结构化抽取适配
   - `parser_factory.py` / `text_parser.py` - 多格式解析入口
5. **基础设施层**:
   - `src/config.py` - 集中配置与环境变量读取
   - `src/knowledge/` - 领域知识与归一化规则
   - `storage/` - 任务、上传、临时导出存储目录

### 核心流程：模板填表
```
文档输入 → 解析器(Docling) → 规则预抽取 → AI模型验证 → 智能合并/去重 → 模板填充 → 输出文件
       ↓               ↓           ↓           ↓           ↓           ↓
     多格式     语义分块/合并单元格  字段别名    混合策略    关键字段合并  Excel/Word/JSON
                     ↓                           ↓           ↓
               优先语义分块            llm_mode控制    归一化后处理
```

### 模型集成策略
- **统一接口**: `call_model()` 支持多种后端（Ollama/OpenAI/Qwen/DeepSeek）
- **超时重试**: 120秒基础超时，最多3次重试，支持`total_deadline`总超时控制
- **混合抽取**: 规则预抽取提供确定性，AI模型提供语义理解
- **三级回退**: AI本地模型 → API云模型 → 纯规则抽取
- **语义分块优先**: 优先使用Docling语义分块，保持文档结构完整性
- **智能合并**: `merge_records_by_key()`基于关键字段的记录去重合并
- **配置化后处理**: `FieldNormalizer`支持JSON配置的字段归一化规则链

### 关键配置文件
- `.env` - 环境变量（API密钥、模型类型、OCR配置、字段别名阈值、归一化规则路径）
- `src/config.py` - 应用配置（超时、权重、开关）
- `src/knowledge/*.json` - 领域知识库（字段别名、城市词典、字段归一化规则等）
  - `field_aliases.json` - 字段别名映射
  - `field_normalization_rules.json` - 字段归一化规则配置（新）
- `test/manifests/*.json` - 批量测试任务清单

## 架构升级说明（v2.0）

系统已完成从"低层次文本切片 + 硬编码后处理"到"深度Docling语义结构 + 可配置通用后处理 + 工程鲁棒性"的架构升级。主要改进包括：

### 1. 语义理解增强
- **Docling语义分块**: 优先使用Docling语义分块（标题/段落/表格边界），保持文档结构完整性
- **合并单元格处理**: `docling_adapter.py`支持表格合并单元格展开，避免数据丢失
- **智能记录合并**: `merge_records_by_key()`基于关键字段的记录去重与合并

### 2. 可配置后处理框架
- **FieldNormalizer**: 通用字段归一化框架，支持JSON配置的规则链
- **规则优先级**: 字段级规则 > 类型级规则 > 默认规则
- **支持类型**: numeric、percentage、area、date、money、phone、speed、weight等
- **可扩展性**: 通过`field_normalization_rules.json`添加新字段类型和后处理规则

### 3. 工程鲁棒性提升
- **总超时控制**: `--total-timeout`参数和`total_deadline`机制，防止无限等待
- **持久化存储**: API上传文件保存到`storage/uploads/`，支持任务重启恢复
- **任务状态管理**: 任务状态持久化，日志文件可通过API查询
- **优雅降级**: LangChain集成可选，失败时自动降级到手写RAG

### 4. 参数体系优化
- **`--llm-mode`参数**: 替代旧的`--use-rules-only`和`--use-unit-aware`
  - `full`: 始终全文抽取（默认）
  - `supplement`: 兼容别名（内部映射为 `full`）
  - `off`: 仅规则抽取
- **`--max-chunks`**: 控制语义分块处理数量
- **`--quiet`**: 安静模式，禁用控制台进度输出

### 5. 向后兼容性
- **兼容参数**: `--slice-size`和`--overlap`作为兼容参数保留
- **环境变量**: 新增`A23_FUZZY_THRESHOLD`（默认75）、`A23_NORMALIZATION_CONFIG`
- **API兼容**: 所有现有API接口保持向后兼容

### 6. 新功能
- **字段别名阈值可配置**: 通过`A23_FUZZY_THRESHOLD`环境变量控制匹配敏感度
- **记录去重合并**: 基于关键字段的智能记录融合
- **语义分块优先**: 文档处理优先保持语义边界
- **持久化任务管理**: 任务状态和文件持久化存储

## 核心约束（AI必须遵守）
1. 所有新增功能必须先写测试（测试文件在 `tests/` 目录）
2. 修改API接口必须更新 `HTTP_API_USAGE.md`
3. 新增依赖必须在 `requirements.txt` 中明确版本
4. 模型调用超时设为120秒，失败有重试机制（见 `model_client.py`）
5. 不要直接修改生产配置，通过环境变量（`.env`）覆盖
6. 维护知识库一致性：修改字段逻辑时更新 `src/knowledge/field_aliases.json`
7. 保持向后兼容性：新增功能不应破坏现有模板填表流程

## AI可以做的事
- 添加新的文档解析适配（遵循 `src/adapters/parser_factory.py` 约定）
- 优化字段抽取逻辑（以 `src/core/extraction_service.py`、`src/core/postprocess.py` 为主）
- 扩展字段归一化规则（更新 `src/knowledge/field_normalization_rules.json` 配置文件）
- 配置字段别名匹配阈值（通过 `A23_FUZZY_THRESHOLD` 环境变量）
- 使用Docling语义分块进行文档结构分析
- 生成测试用例（使用现有测试框架）
- 分析实验日志（日志位于 `test/results/` 各任务目录）
- 扩展知识库（更新 `src/knowledge/` 中的JSON文件：字段别名、归一化规则等）
- 优化模型 prompt（主要位于 `src/core/extraction_service.py` 的 `build_smart_prompt` 等，以实际代码为准）
- 配置记录去重策略（在profile中设置 `dedup_key_fields`）

## AI不能做的事
- 跳过测试直接修改核心逻辑（必须先通过现有测试）
- 修改数据库/文件结构而不更新相关文档
- 删除现有的fallback机制（系统依赖多级回退保证鲁棒性）
- 硬编码敏感信息（API密钥、密码等必须通过环境变量）
- 破坏现有API接口的兼容性（需要维护 `HTTP_API_USAGE.md`）
- 跳过语义分块直接使用字符切片（应优先使用Docling语义分块）
- 破坏新架构的向后兼容性（`--slice-size`/`--overlap`作为兼容参数必须保留）
- 绕过字段归一化框架直接硬编码后处理逻辑（应通过`field_normalization_rules.json`配置）

## 开发工作流
1. **配置环境**: 设置 `.env` 文件，包含正确的模型API密钥
2. **运行测试**（`tests/` 为本地目录，默认不提交到 Git）: `python -m pytest tests/ -v`
3. **增量开发**: 修改特定模块，保持接口稳定
4. **验证结果**: 使用 `scripts/run_batch.py --validate` 验证准确率，并测试新架构功能：
   - 测试字段归一化: `python -c "from src.core.field_normalizer import FieldNormalizer; fn = FieldNormalizer(); print(fn.normalize('增长率', '15.3%'))"`
   - 测试记录合并去重: `python -c "from main import merge_records_by_key; records = [{'城市':'北京','PM2.5':''},{'城市':'北京','PM2.5':'45'}]; print(merge_records_by_key(records, ['城市']))"`
   - 测试语义分块: `python main.py --max-chunks 10 --llm-mode full --template-mode llm --template-description '提取测试字段' --input-dir '测试文件路径' --output-dir 'test_output' --overwrite-output`
   - 测试总超时控制: `python main.py --total-timeout 30 ...`（验证30秒内返回）
   - 测试API持久化: `curl -X POST "http://127.0.0.1:8000/api/extract/direct" ...` 验证文件持久化存储
5. **更新文档**: 修改代码后更新相关使用说明（包括CLAUDE.md和HTTP_API_USAGE.md）

## 测试数据位置
- `test/inputs/` - 所有测试文件（Excel/Word/Markdown/文本）
- `test/assets/` - 基准任务资源（模板、输入、标准答案）
- `test/results/` - 测试输出目录（按任务ID组织）
- `test/reports/` - 验证报告和性能指标

## 文档与仓库结构（当前）
- 权威模块目录：`src/adapters/`、`src/api/`、`src/core/`（含 `extraction_routing.py`）、`src/knowledge/`（根目录下 **无** 独立的 `src/algorithm`、`src/pipeline`、`src/engine`、`src/parsers` 等旧路径）。
- 架构与流程详解：[A23_TECHNICAL_FLOW.md](A23_TECHNICAL_FLOW.md)
- HTTP 接口说明：[HTTP_API_USAGE.md](HTTP_API_USAGE.md)
- 部署与路由说明：[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)
- 文档索引：[docs/README.md](docs/README.md)