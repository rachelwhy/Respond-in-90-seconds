# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# A23 AI 文档处理系统

## 项目概述
企业内网AI文档处理系统，支持模板填表和文档问答。采用混合策略（规则预抽取+AI模型验证）实现文档结构化提取。

**上线形态**：以 **`api_server`（FastAPI）** 为入口，路由在 `src/api/routes/*`；**正式对接以同步** `POST /api/extract/direct` **为主**（业务异步由后端队列 + worker 调算法）。可选 `src/api/task_manager.py`（`/api/tasks/*`）默认 **`A23_ENABLE_TASKS=false`** 关闭，仅本地/无队列长任务联调时开启。`main.py` 与 `scripts/` 用于调试、批测。对接分工见 **`HTTP_API_USAGE.md`** 篇首。抽取路由摘要见 `src/core/extraction_routing.py`（`metadata.pipeline_routing`），详见 `docs/DEPLOYMENT.md`。

以下 **硬约束** 为仓库级契约，**改代码前必读**；违反任一条的 PR 应拒绝合并。

## 变更与逻辑（硬约束）

- **最小修改、禁止赠填**：改动须严格落在需求范围内；**禁止**借机向主路径塞进大量**原本不存在**的分支、隐式策略与「顺便加上的聪明逻辑」。每增加一条行为，都须有通用产品或工程理由，否则视为赠填。
- **禁止依赖回退链**：**不**把「先试 A，不行再 B、再 C」当作默认架构；**不**以层层 fallback 替代对问题的一次性、直接解法。能力缺失或环境差异应通过**显式**配置、能力探测、边界文档或失败语义表达，而不是把回退堆成主流程。
- **必须泛化**：任何改进必须适用于**一类**任务与数据形态；**禁止**为当前某个样例文件、单次任务、某一地区或业务写特判、魔法常量或专用分支。特判堆积将不可维护，评审须拦截。
- **直接做对**：优先在正确抽象上**一次**实现预期行为；若存在历史兼容路径，应计划收敛而非无限叠加平行逻辑。

## 文件处理边界（硬约束）

在「扫描 → 解析 → 抽取 → 模板填写」全链路中：

- **不得**为单次任务把具体文件正文、监测值、地名等写进代码或配置当硬编码业务规则；列名归一、类型推断等须保持**通用**（见 `src/knowledge/*.json`）。
- **不得**默认再产出依赖文件侧大段原文或逐行重复的附属物（例如与 `*_result.json` 重复的全量调试行、RAG 片段预览等）。`main.py` 默认**不写** `*_result_report.json`；调试时设 `A23_WRITE_RESULT_REPORT_BUNDLE=true`。
- 对外契约输出以产品约定为准（如填好的模板、`records` 结构化结果）；其余仅允许指标级元数据（耗时、条数、路由摘要等），避免「第二份全文」。
- Word 多表在**未**使用抽取阶段已给出的 `_table_groups` 时，`table_specs` 的 `filter_value` 仅与单元格值做**显式子串包含**判断；**不得**在填表分组里再对具体文件内容做向量相似度、模糊分或地名启发式。分表语义应由**指令与源列措辞一致**、或由上一步抽取（含并行多表路径）产出结构化分组保证。

## 工程与注释（硬约束）

- **注释与文档字符串**须写成**成熟定稿**：直接描述当前行为、契约、前置条件与不变量；**禁止**渐进式叙述（例如「先…再…」「第一步」「暂」「简化版」「占位」「回头再改」）和面向过程的讲义体。
- 模块/文件头注释若存在，应概括职责与对外边界，与实现一致；**禁止**把注释当作迭代笔记或待办口吻。
- 日志文案与 **logger** 面向运维与排障，同样采用定稿式陈述；**避免**「回退到」等暗示未完成的措辞，改为「改用」「输出为空时」等明确因果。

## 技术栈
- Python 3.11+, FastAPI, Ollama
- **主要依赖**：openpyxl, python-docx, requests, rapidfuzz, openai, python-dotenv
- **文档解析增强**：docling（语义分块、表格合并单元格处理）, langextract
- **向量检索**：sentence-transformers（语义相似度计算）
- **缓存与监控**：diskcache（持久化缓存）, prometheus-client（指标监控）
- **OCR支持**：Tesseract, Pillow, opencv-python, pdf2image
- **数据库**：pymysql（MySQL入库）
- **文档问答（HTTP）**：默认 **LangChain** + **Chroma** + **HuggingFaceEmbeddings**，回退 **`rank-bm25` + `sentence-transformers`**（`qna_retrieval`）；生成答案默认 **`A23_QNA_MODEL_TYPE=deepseek`**（与抽取 **`A23_MODEL_TYPE`** 独立）；句向量默认离线优先 **`models/qna_embedding`**（不设 **`A23_QNA_SENTENCE_TRANSFORMER`** 且不下载快照时不访问 Hub）。**浏览器跨域**：`api_server` 已配置 CORS（默认列表 + 私网/localhost 正则，见 **`HTTP_API_USAGE.md`**）。**CLI 抽取链路**可选 RAG 结构化输入（与 `main.py` 参数相关）
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
# - sentence-transformers（锁定版本见 requirements.txt）：问答句向量与检索
# - diskcache>=5.6.3: 持久化缓存
# - prometheus-client>=0.20.0: 监控指标
# - pymysql>=1.1.0: MySQL数据库支持

# 问答依赖自检（BM25 + sentence-transformers，已含于 requirements.txt）
python scripts/verify_qna_deps.py

# OCR系统依赖（需要单独安装）
# 1. Tesseract OCR: https://github.com/UB-Mannheim/tesseract/wiki
# 2. Poppler工具（PDF转图像）: https://github.com/oschwartz10612/poppler-windows

# 模型准备（默认与本地测试）
# 默认 MODEL_TYPE=deepseek：复制 .env.example 为 .env，填写 A23_DEEPSEEK_API_KEY 即可。
# 备选：Ollama 本地 ollama pull qwen2.5:7b 后设 A23_MODEL_TYPE=ollama；或其它 OpenAI 兼容端点。
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
- `A23_TARGET_LIMIT_SECONDS`: 运行观测/目标耗时参考（秒），**不**直接截断单次 HTTP 请求
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
   - `src/api/qna_service.py` - 文档问答编排；`src/api/qna_langchain.py` - LangChain 默认路径；`src/api/qna_retrieval.py` - 混合检索回退
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
- **超时重试**: 单次 HTTP 默认首包 120s，重试阶梯每次 +60s，最多 3 次；`call_model(..., timeout=秒)` 覆盖单次 HTTP 超时；另支持 `total_deadline`（Unix 时间）总截止时间
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
- **文档问答**: 默认 LangChain 对话检索链；失败或 `A23_QNA_USE_LANGCHAIN=false` 时走 `qna_retrieval`；可选 `A23_QNA_PERSIST_SESSION=false` 由业务库存会话

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
4. 模型 HTTP 超时默认见 `model_client.py`（阶梯重试）；调用方可传 `timeout`；失败自动重试（最多 3 次）
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
2. **运行测试**（`tests/` 与仓库同步）: `python -m pytest tests/ -v`
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