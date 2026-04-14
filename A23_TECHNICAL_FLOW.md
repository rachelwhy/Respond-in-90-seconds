# A23 AI 文档处理系统 - 技术实现全流程详解

> **可视化分支图（Mermaid）**：[docs/RUNTIME_FLOW.md](docs/RUNTIME_FLOW.md)（入口、`extract_with_slicing`、`direct_extract`、CLI 后处理）。

## 一、项目概述与架构层次

### 1.1 核心功能定位
A23 AI 文档处理系统是企业内网文档结构化处理工具，核心功能为**模板填表**，可选功能为**文档问答**。系统采用混合策略：规则预抽取提供确定性，AI模型验证提供语义理解，通过三级回退机制保证鲁棒性。

### 1.2 技术架构层次（v2.0，与当前仓库对齐）

> **勘误**：早期文档中的 `src/algorithm`、`src/pipeline`、`src/engine`、`src/parsers`、`src/extractors` 等目录在**当前仓库中不存在**；能力已合并到下列模块。完整优化与对照说明见根目录 [PROJECT_OPTIMIZATION_REPORT_2026.md](PROJECT_OPTIMIZATION_REPORT_2026.md)。

```
应用层
├── main.py（CLI 入口）
├── api_server.py（FastAPI）
└── src/api/
    ├── direct_extractor.py（同步抽取）
    ├── task_manager.py（异步任务：子进程调用 main.py）
    └── qna_service.py（文档问答，可选 LangChain）

核心业务与抽取
├── src/core/extraction_service.py（切片与智能抽取主逻辑）
├── src/core/extraction_routing.py（文件/模板能力摘要 → pipeline_routing，供 meta）
├── src/core/extractor.py / profile.py / postprocess.py
├── src/core/reader.py（collect_input_bundle、collect_semantic_chunks_from_bundle、表格直读 try_internal_structured_extract）
├── src/core/writers.py（Excel/Word 等写回）
└── src/core/interfaces.py（抽取/模型服务接口与注册管理）

适配与解析
├── src/adapters/model_client.py（Ollama / OpenAI 兼容 / DeepSeek 等）
├── src/adapters/docling_adapter.py（语义分块）
├── src/adapters/langextract_adapter.py（结构化提取，按策略启用）
├── src/adapters/parser_factory.py + text_parser 等（多格式解析）

基础设施
├── src/knowledge/（别名、归一化规则 JSON）
├── src/config.py（集中配置，环境变量 A23_*）
└── storage/（API 任务与上传持久化，可按环境配置清理）
```

### 1.3 关键依赖
- **文档解析增强**：docling（语义分块、表格合并单元格处理）
- **结构化提取**：langextract（Google结构化文档提取库，已深度修改）
- **向量检索**：sentence-transformers（语义相似度计算）
- **模型后端**：Ollama（本地）、DeepSeek API、OpenAI兼容API（Qwen等）
- **持久化缓存**：diskcache
- **监控指标**：prometheus-client

## 二、输入处理流程

### 2.1 多格式文档解析
```python
# main.py:750-752
loaded_bundle = collect_input_bundle(args.input_dir)
all_text = loaded_bundle.get('all_text', '')
```

系统通过 `src/core/reader.py` 的 `collect_input_bundle()` 统一处理输入；**解析器由 `parser_factory` 选择**：
- **非纯文本**：优先 **Docling**（`docling_adapter`），产出 `text`、`chunks`、可选表格结构等
- **纯文本 `.txt`**：`TextParser`，按段落生成分块
- **Excel/Word 等**写回模板时仍可用 `openpyxl` / `python-docx`（见 `writers`）
- **PDF/OCR**：可选 Tesseract 等（见配置 `A23_ENABLE_OCR`）

### 2.2 语义分块优先策略
系统优先使用**Docling语义分块**，而非传统的字符切片：
```python
# main.py:327-335
if chunks:
    # 过滤掉表格类型的chunk（表格已通过直读路径处理）
    text_chunks = [c for c in chunks if c.get("type") != "table"]
    # 限制处理数量
    if len(text_chunks) > max_chunks:
        text_chunks = text_chunks[:max_chunks]
```

**语义分块优势**：
1. 保持文档结构完整性（标题/段落/表格边界）
2. 避免跨语义单元切割导致的上下文丢失
3. 支持表格合并单元格展开处理

### 2.3 兼容性参数保留
为保持向后兼容，字符切片参数作为备选方案：
- `--slice-size 3000`：字符切片大小（仅在无语义分块时使用）
- `--overlap 200`：字符切片重叠大小（仅在无语义分块时使用）
- `--max-chunks 50`：语义分块处理上限（默认）

## 三、模板处理与profile生成策略

### 3.1 模板模式（`--template-mode`）
| 模式 | 参数要求 | 适用场景 |
|------|----------|----------|
| **file** | `--template` 模板文件路径 | 已有Excel/Word模板文件 |
| **llm** | `--template-description` 自然语言描述 | 无模板文件，通过自然语言描述生成 |
| **auto** | 自动选择（默认） | 智能判断，有文件用文件，有描述用描述 |

### 3.2 profile生成逻辑（main.py:677-730）
```python
# 判断模板类型
is_generic_template = template_path and Path(template_path).name in ('generic_template.xlsx', 'generic_template.docx')
is_word_template = template_path and template_path.lower().endswith(('.doc', '.docx'))
is_no_template = not template_path
```

**三种profile生成路径**：

1. **完全无模板**（`is_no_template=True`）
   - 有描述指令：`generate_profile_smart()` LLM生成
   - 无描述指令：生成占位profile，文档加载后通过`generate_profile_from_document()`升级

2. **Word模板**（`is_word_template=True`）
   - 强制使用`generate_profile_smart()` LLM分析
   - 支持多表格识别和复杂文档结构

3. **Excel模板**（`is_generic_template=False`）
   - 通用模板：`generate_profile_from_template()` 生成占位profile，文档加载后升级
   - 真实Excel模板：规则模式足够准确且快速

### 3.3 profile数据结构
生成的profile为标准JSON结构：
```json
{
  "report_name": "空气质量监测",
  "template_path": "data/template.xlsx",
  "instruction": "提取环境监测数据",
  "task_mode": "table_records",
  "template_mode": "excel_table",
  "fields": [
    {"name": "城市", "type": "text"},
    {"name": "PM2.5", "type": "numeric"},
    {"name": "监测日期", "type": "date"}
  ],
  "dedup_key_fields": ["城市", "监测日期"]
}
```

## 四、文档规整度判断与提取路径选择

### 4.1 规整度检测
系统通过`try_internal_structured_extract()`函数检测文档规整度：
```python
# main.py:840-843
internal_structured = try_internal_structured_extract(profile, loaded_bundle)
if internal_structured:
    extracted_raw = internal_structured
    skip_model = True  # 跳过AI模型调用
```

**规整文档特征**：
- Excel标准表格结构
- Word规范表格（行列清晰）
- 结构化数据可直接映射到模板字段

### 4.2 提取路径决策树
```
文档输入
    ↓
{ 文档规整度检测 }
    ├── 高度规整 → try_internal_structured_extract() → 直接填充模板
    ├── 半结构化 → 表格直读 + 文本AI抽取（混合策略）
    └── 不规整文本 → 纯AI模型抽取（优先langextract）
```

### 4.3 RAG集成处理（可选）
系统支持RAG中间结果作为输入：
```python
# main.py:830-838
if args.prefer_rag_structured and structured_rag_result:
    extracted_raw = structured_rag_result
    internal_route_used = 'rag_structured'
    skip_model = True  # 优先使用RAG结构化结果
```

## 五、AI模型调用决策体系

### 5.1 LLM抽取模式（`--llm-mode`）
| 模式 | 实现逻辑 | 适用场景 |
|------|----------|----------|
| **full** | 始终全文抽取（默认） | 复杂文档、首次处理、需要深度语义理解 |
| **supplement（兼容）** | 兼容别名，内部映射为 full | 保持历史调用不报错 |
| **off** | 完全不调用模型 | 简单表格、网络受限环境、纯规则验证 |

### 5.2 实现代码路径
**1. direct_extractor.py路径**：
```python
# src/api/direct_extractor.py:280-287
config = {
    "llm_mode": llm_mode,  # 'full'（supplement 兼容映射到 full）
    "total_timeout": total_timeout,
    "max_chunks": max_chunks,
}
extractor = UniversalExtractor(config=config)
result = extractor.extract(text, profile)
```

**2. extractor.py核心逻辑**：
```python
# src/core/extractor.py:127-136
if llm_mode == "off" or not doc_text:
    llm_records = []
elif llm_mode == "full":
    # 始终调用LLM全文抽取
    llm_records = self._extract_from_text(doc_text, field_names, profile)
else:
    # full：统一模型抽取路径（supplement 已映射到 full）
    llm_records = self._extract_from_text(doc_text, field_names, profile)
```

### 5.3 langextract 深度集成与决策逻辑

#### 5.3.1 langextract 是什么
**langextract**是Google开发的结构化文档提取库，能将非结构化文本转换为严格遵循schema的JSON/YAML输出。相比原始prompt方案的优势：
1. **结构化输出保障**：强制模型返回符合profile定义的字段结构
2. **并行批处理**：原生支持多文本块并行提取
3. **智能参数适配**：已深度修改以支持中文AI模型

#### 5.3.2 关键技术修改
| 修改位置 | 解决的问题 | 技术细节 |
|----------|------------|----------|
| `third_party/langextract/prompting.py` | 中文路径/内容编码错误 | `open("rt", encoding='utf-8')`替换默认编码 |
| `third_party/langextract/providers/openai.py` | 支持中文AI模型API | 添加`_is_custom_provider()`方法，智能区分官方OpenAI与兼容API |
| `third_party/langextract/providers/openai.py` | Qwen base_url重复`/v1` | 智能URL规范化，避免`https://.../compatible-mode/v1/v1`错误 |
| `src/adapters/langextract_adapter.py` | 并行稳定性 | 删除环境变量竞争，优化并发策略 |

#### 5.3.3 核心决策逻辑（extract_with_langextract函数）
```python
# src/adapters/langextract_adapter.py:485-531
def extract_with_langextract(text_chunks, profile, ...):
    # 1. 检查模型类型
    model_type = os.environ.get("A23_MODEL_TYPE", "ollama")
    is_cloud = model_type in ("deepseek", "openai", "qwen")
    
    # 2. 本地小模型跳过（7B）→ 回退prompt方案
    if not is_cloud and model_size < 14:
        return None
    
    # 3. 选择并行策略（云API并发2，本地并发1）
    strategy_config = get_optimal_strategy(text_chunks, profile)
    
    # 4. 执行提取
    if strategy == "single":
        records = _extract_with_langextract_direct(...)
    elif strategy == "parallel":
        records = extract_with_langextract_parallel(...)
    
    # 5. 结果对齐与去重
    return _align_records_to_fields(records, field_names)
```

#### 5.3.4 并行策略选择（get_optimal_strategy函数）
```python
# src/adapters/langextract_adapter.py:756-772
is_cloud = model_type in ("deepseek", "openai", "qwen")

if is_cloud:
    if chunk_count <= 1:
        strategy = "single"
        max_workers = 1
    else:
        strategy = "parallel"
        max_workers = min(2, chunk_count)  # 云API最大并发2
else:  # 本地模型
    if chunk_count <= 1:
        strategy = "single"
        max_workers = 1
    else:
        strategy = "parallel"
        max_workers = 1  # 本地模型保守并发1
```

#### 5.3.5 模型后端适配决策
| 模型类型 | langextract支持 | 决策逻辑 | 技术原因 |
|----------|-----------------|----------|----------|
| **DeepSeek/Qwen API** | ✅ 完全支持 | 优先使用langextract | 云API稳定，支持`response_format={'type':'json_object'}` |
| **OpenAI官方API** | ✅ 完全支持 | 优先使用langextract | 原生兼容所有高级参数 |
| **Ollama ≥14B** | ✅ 支持 | 尝试使用langextract | 模型能力足够处理结构化输出 |
| **Ollama 7B** | ❌ 跳过 | 直接回退prompt方案 | 小模型处理结构化输出开销大，基础prompt更高效 |

#### 5.3.6 自定义provider检测逻辑
```python
# third_party/langextract/providers/openai.py:137-154
def _is_custom_provider(self) -> bool:
    """判断是否为非OpenAI官方API（如DeepSeek、Qwen）"""
    if not self.base_url:
        return False
    
    # OpenAI官方域名列表
    official_domains = ["api.openai.com", "openai.azure.com"]
    
    # 如果包含任何官方域名，则为官方API
    return not any(domain in self.base_url for domain in official_domains)
```

#### 5.3.7 参数兼容性处理
```python
# third_party/langextract/providers/openai.py:203-225
if is_custom:
    # 自定义provider（DeepSeek、Qwen等）：支持基础JSON mode
    if self.format_type == data.FormatType.JSON:
        api_params['response_format'] = {'type': 'json_object'}
    
    # 基础参数仍然支持
    if (v := normalized_config.get('max_output_tokens')) is not None:
        api_params['max_tokens'] = v
    
    # 保守支持的参数（大部分兼容API都支持）
    for key in ['frequency_penalty', 'presence_penalty', 'seed', 'stop']:
        if (v := normalized_config.get(key)) is not None:
            api_params[key] = v
    
    # 仍然禁用的高级参数：
    # - reasoning (DeepSeek-R1专用，可能不支持)
    # - logprobs, top_logprobs (可能不支持)
    # - json_schema (比json_object更严格，可能不支持)
```

### 5.4 三级回退机制
1. **一级尝试**：`extract_with_langextract()`（结构化提取）
2. **二级回退**：`call_model()`基础prompt方案（langextract失败时）
3. **三级回退**：纯规则抽取（`--llm-mode off`或模型不可用）

## 六、后处理与输出系统

### 6.1 智能记录合并去重
```python
# src/core/chunk_merger.py
def smart_merge_records(records, key_fields=None, similarity_threshold=0.98):
    """基于关键字段的记录融合去重"""
    # 1. 基于关键字段的合并优先
    # 2. 基于内容相似度的二次合并
    # 3. 保留原文顺序，清理内部标记字段
```

**合并策略**：
- 相同关键字段的记录进行字段级合并
- 新记录的非空值覆盖旧记录的空值
- 所有关键字段均为空的记录保留并标记`_unkeyed=True`

### 6.2 字段归一化框架（FieldNormalizer）
```python
# src/core/field_normalizer.py
class FieldNormalizer:
    """基于JSON配置的通用字段归一化框架"""
    
    def normalize(self, field_name: str, raw_value: Any) -> Any:
        # 规则优先级：字段级规则 > 类型级规则 > 默认规则
```

**支持类型**：numeric、percentage、area、date、money、phone、speed、weight等
**配置文件**：`src/knowledge/field_normalization_rules.json`

### 6.3 模板填充输出
```python
# src/core/writers.py
def fill_excel_table(records, template_path, output_path):
    """Excel表格填充（横向/纵向）"""

def fill_word_table(records, template_path, output_path):
    """Word表格填充"""

def create_excel_from_records(records, output_path):
    """从记录创建Excel文件"""
```

**输出格式**：Excel（.xlsx）、Word（.docx）、JSON（.json）

## 七、关键技术修改与配置更新

### 7.1 最新代码修改（基于git状态）
```
已修改文件：
- src/adapters/langextract_adapter.py     # langextract适配器深度优化
- src/adapters/model_client.py           # 模型客户端更新，支持Qwen配置
- src/adapters/text_parser.py            # 文本解析器改进
- src/api/direct_extractor.py           # 直接提取API支持llm_mode
- src/config.py                         # 新增Qwen等配置项
- src/core/postprocess.py               # 后处理逻辑优化
- src/core/profile.py                   # profile生成增强

新增文件：
- third_party/                          # 修改后的langextract源码
- test_adapter_stability.py             # langextract稳定性测试
- test_deepseek_response_format.py      # DeepSeek API参数兼容性测试
- test_custom_provider.py               # 自定义provider测试
```

### 7.2 新增环境变量配置
```bash
# DeepSeek配置
A23_MODEL_TYPE=deepseek
A23_DEEPSEEK_BASE_URL=https://api.deepseek.com
A23_DEEPSEEK_API_KEY=sk-your-api-key
A23_DEEPSEEK_MODEL=deepseek-chat

# Qwen配置（通义千问）
A23_MODEL_TYPE=qwen
A23_QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
A23_QWEN_API_KEY=your-qwen-api-key
A23_QWEN_MODEL=qwen-plus

# langextract并行控制
A23_LANGEXTRACT_PROVIDER_WORKERS=1  # provider内部并发数（默认1）

# 字段别名匹配阈值
A23_FUZZY_THRESHOLD=75  # 0-100，默认75

# 字段归一化规则路径
A23_NORMALIZATION_CONFIG=src/knowledge/field_normalization_rules.json
```

### 7.3 配置优先级
1. **运行时环境变量**（最高优先级）
2. **.env文件配置**
3. **src/config.py默认值**
4. **硬编码默认值**（最低优先级）

## 八、全流程决策矩阵

| 场景维度 | 模板类型 | 文档规整度 | llm-mode | langextract使用 | 主要技术路径 |
|----------|----------|------------|----------|-----------------|--------------|
| **简单表格提取** | Excel模板 | 高度规整 | off | ❌ 不使用 | 内部结构化抽取 → 规则填充 |
| **复杂报告分析** | LLM生成 | 不规整文本 | full | ✅ 优先使用 | Docling分块 → langextract → 结构化输出 |
| **表格数据补全** | Word模板 | 半结构化 | full（supplement兼容） | ✅ 可能使用 | 规则预抽取 → AI补充缺失字段 |
| **批量文档处理** | 通用模板 | 混合类型 | full | ✅ 批处理 | 语义分块 → 并行langextract → 合并去重 |
| **API服务部署** | 文件模板 | 未知类型 | full | ✅ 云API优先 | 自适应策略 → 三级回退保障 |

## 九、总结：架构演进与技术特点

### 9.1 架构演进（v1.0 → v2.0）
1. **从字符切片到语义分块**：优先使用Docling保持文档结构完整性
2. **从硬编码后处理到可配置框架**：FieldNormalizer支持JSON配置的规则链
3. **从单一模型调用到智能决策体系**：多级回退、场景化langextract使用
4. **从简单抽取到工程鲁棒性**：总超时控制、持久化存储、任务状态管理

### 9.2 关键技术特点
1. **混合策略架构**：规则预抽取提供确定性，AI模型验证提供语义理解
2. **智能并行处理**：云API与本地模型差异化并发策略
3. **中文AI模型适配**：深度修改langextract支持DeepSeek、Qwen等
4. **配置化系统**：环境变量控制字段别名阈值、归一化规则路径等
5. **优雅降级机制**：三级回退保障系统在各类环境下的可用性

### 9.3 适用场景与最佳实践
- **复杂文档分析**：`--llm-mode full --template-mode llm`
- **表格数据补全**：`--llm-mode full --template template.xlsx`（`supplement` 可作为兼容别名）
- **批量处理**：`--llm-mode full --max-chunks 20 --quiet`
- **简单抽取**：`--llm-mode off --template template.xlsx`
- **API服务**：`llm_mode=full, max_chunks=30, A23_LANGEXTRACT_PROVIDER_WORKERS=2`

---
**文档版本**：v2.0  
**最后更新**：2026-04-12  
**对应代码版本**：基于git commit 87a23af (docs: 添加harness/entry.md架构文档)及后续修改