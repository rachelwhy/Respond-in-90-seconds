# A23 HTTP API 对接说明（后端交付版）

## 对接分工与职责边界（必读）

### 算法服务（本仓库）负责

- **文档抽取与填表核心能力**：多格式解析、语义分块、规则/模型抽取、后处理、模板写回。
- **对外 HTTP 契约**：以本文与 **`/docs` OpenAPI** 为准；**正式推荐对接面**为下方「推荐集成」所列**同步抽取**接口。
- **模型与抽取相关环境变量**：如 `A23_MODEL_TYPE`、`A23_*_API_KEY`、`A23_EXTRACTION_TIMEOUT` 等（见环境变量表）。

### 业务后端 / 网关负责（本服务不内置）

- **鉴权、用户体系、审计、限流、防重放、HTTPS 终止**等传统网关/后端能力。
- **异步任务产品**：排队、重试、优先级、任务列表、与业务 `task_id` 对齐；在 **worker 内调用本服务的同步抽取接口**即可，无需依赖本服务内置任务 API。
- **跨服务编排与入库**：消费算法返回的 `records` / 文件路径后写入业务库。

### 正式对接接口（推荐）

| 优先级 | 端点 | 说明 |
|--------|------|------|
| **主路径** | `POST /api/extract/direct` | 有模板、**同步**、单次请求内完成抽取并返回 JSON（及可选输出路径）。业务后端有自有异步队列时，**在 worker 中调用本接口**。 |
| 可选 | `POST /api/extract/pre-analyze` | 体量/复杂度预估算，供调度参考。 |
| 可选 | `POST /api/extract/no-template` | 无模板结构化抽取。 |
| 可选 | `POST /api/qna/ask` 等 | 见路由总览。 |

**可选能力**：本仓库仍提供 **`/api/tasks/*`**（子进程跑 `main.py`），便于**无业务队列时的本地长任务**；**默认关闭**（`A23_ENABLE_TASKS=false`）。若业务后端已具备异步 HTTP/任务中心，**请保持关闭**，避免两套「任务」模型并存。详见文末「附录：算法内置异步任务」。

---

## 启动

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

- Swagger: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **浏览器 / 网页对话框**：已启用 **CORS**。**同源**部署（反向代理到同域名）不受跨域限制；**不同源**（例：前端 `:5173`、API `:8000`，或局域网 IP 调试）时，默认同时使用 **显式 Origin 列表**与 **`allow_origin_regex`**（localhost / `127.0.0.1` / IPv6 `::1` / 常见私网段任意端口）。生产公网域名请在 `.env` 追加 **`A23_CORS_ORIGINS`**。详见环境变量表。
- **鉴权**：本服务默认**不实现**业务鉴权；由上游 **API 网关或业务后端** 统一管控（令牌校验、租户隔离等）。

---

## 环境变量（对接相关）

> **单一事实来源**：业务对接与部署请以**下表**为准；`README.md`、`docs/DEPLOYMENT.md` 等仅作摘要，避免在各处重复罗列变量。

| 变量 | 作用 |
|------|------|
| `A23_CORS_ORIGINS` | **浏览器跨域**：逗号分隔的 Origin 列表（公网前端域名写在此处）；不设时默认含常见 `localhost` 端口。设为 `*` 则允许任意源（此时 **`A23_CORS_ALLOW_CREDENTIALS`** 自动为 false，且不启用正则） |
| `A23_CORS_ORIGIN_REGEX` | 可选覆盖默认正则；**设为空字符串**表示仅用列表、不用正则（严格白名单） |
| `A23_CORS_ALLOW_CREDENTIALS` | 是否允许浏览器携带 Cookie 等凭证跨域（默认 **true**；与 `A23_CORS_ORIGINS=*` 互斥） |
| `A23_ENABLE_TASKS` | 是否启用算法端内置 **`/api/tasks/*`**（默认 **false**；与业务异步队列二选一时建议保持 false） |
| `A23_EXTRACTION_TIMEOUT` | 同步抽取与 CLI `--total-timeout` 的默认秒数（默认 240） |
| `A23_TARGET_LIMIT_SECONDS` | 内部目标耗时参考秒数（默认 90） |
| `A23_PERSIST_UPLOADS` | 为 true 时同步 `/api/extract/direct` 将上传与输出落在 `storage/uploads/<task_id>/`；为 false 时用系统临时目录 |
| `A23_PERSIST_PROFILES` | 是否持久化自动生成 profile |
| `A23_TASK_RETENTION_HOURS` | **仅** `storage/tasks/` 下**算法内置异步任务**目录保留时长（`/api/tasks/create`，且 `ENABLE_TASKS=true` 时） |
| `A23_UPLOAD_RETENTION_HOURS` | `storage/uploads/` 下各请求子目录保留时长（含同步抽取落盘） |
| `A23_TEMP_RETENTION_HOURS` | 临时导出文件保留时长（`storage/uploads/temp`） |
| `A23_DEBUG` | 调试模式；影响 report 调试信息暴露 |
| `A23_QNA_PERSIST_SESSION` | 默认 **true**：问答会话写入 `storage/sessions/`；设为 **false** 时不落盘（临时目录解析，响应仍含 `session_id` 供关联）；多轮由业务后端存库并在请求中传 `history_json` |
| `A23_QNA_USE_LANGCHAIN` | 默认 **true**：`POST /api/qna/ask` 优先 **LangChain** `ConversationalRetrievalChain` + **Chroma** + **HuggingFaceEmbeddings**；设为 **false** 时仅用混合检索（BM25 + 句向量）+ `call_model` |
| `A23_QNA_MODEL_TYPE` | 问答「生成答案」所用后端（默认 **deepseek**）；与 **`A23_MODEL_TYPE`**（抽取）独立 |
| `A23_QNA_SENTENCE_TRANSFORMER` | 句向量：本地快照目录或 Hub 模型名；未设且存在完整 **`models/qna_embedding`** 时自动走本地，不隐式访问 Hub |
| `HF_ENDPOINT` | Hugging Face Hub 镜像根 URL（国内常用 **`https://hf-mirror.com`**），减轻访问 `huggingface.co` 超时 |
| `HF_HUB_DOWNLOAD_TIMEOUT` | Hub 下载超时秒数（可选加大） |

---

## 运行时双态联调自检

用于确认部署配置是否符合预期（默认关闭算法内置异步任务）：

1. **默认状态（推荐生产）**
   - 不设置 `A23_ENABLE_TASKS`（或显式设为 `false`）启动服务。
   - 调用 `GET /api/tasks`，应返回 `404`，并提示改用 `POST /api/extract/direct`。
2. **联调状态（可选）**
   - 设 `A23_ENABLE_TASKS=true` 启动服务。
   - 调用 `GET /api/tasks`，应返回 `200`（任务列表）。

若与预期不符，先检查运行环境变量是否被部署系统覆盖，再核对本文件与 `/docs` 的路由说明。

---

## 路由总览

### 健康与模型

- `GET /api/health`
- `GET /api/models`
- `POST /api/models/test-connection`
- `POST /api/models/switch`

### 抽取接口（推荐主路径）

- `POST /api/extract/direct`
- `POST /api/extract/no-template`
- `POST /api/extract/pre-analyze`

### 可选：算法内置异步任务（`A23_ENABLE_TASKS=true` 时可用）

- `POST /api/tasks/create`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `GET /api/tasks/{task_id}/log`
- `GET /api/tasks/{task_id}/stream`
- `GET /api/tasks/{task_id}/result`
- `GET /api/tasks/{task_id}/download/{kind}`
- `POST /api/tasks/{task_id}/export-complete`
- `DELETE /api/tasks/{task_id}`

### 临时导出下载

- `GET /api/download/temp/{filename}`
- `POST /api/download/temp/{filename}/export-complete`

### 其他能力

- `POST /api/qna/ask`（表单：`question`；可选 `files`、`session_id`、`top_k`、`model_type`、`history_json`、`persist_session`；检索为 BM25 + 向量混合；详见下文「文档问答」）
- `POST /api/document/operate`
- `POST /api/ingest`
- `POST /api/tasks/{task_id}/ingest`（依赖 `ENABLE_TASKS`）
- `GET /api/ingest/{task_id}/records`
- `GET /api/db/health`

### 文档问答 `POST /api/qna/ask`

- **表单**：`question`（必填）；`files`（可多文件）；`session_id`（可选）；`top_k`（默认 5）；`model_type`（可选，覆盖 **`A23_QNA_MODEL_TYPE`**，默认 **deepseek**，与抽取 `A23_MODEL_TYPE` 独立）；`history_json`（可选，UTF-8 JSON **数组**，每项含 `q`、`a`，可选 `t` 时间戳）；`persist_session`（可选，`true`/`false`，省略则用 `A23_QNA_PERSIST_SESSION`）。
- **默认路径**：**LangChain** `ConversationalRetrievalChain` + **Chroma** + **HuggingFaceEmbeddings**（句向量解析规则见下）；依赖 **chromadb**、**langchain***（见 `requirements.txt`）。链失败或未安装依赖时**自动回退**为 BM25+向量混合检索（`qna_retrieval`）。环境 **`A23_QNA_USE_LANGCHAIN=false`** 时跳过 LangChain，直接使用混合检索。
- **句向量默认离线**：未设置 `A23_QNA_SENTENCE_TRANSFORMER` 时，若项目根下已有 `models/qna_embedding`（含 `config.json`，与预下载脚本一致）则自动使用该目录，**不会**为解析模型名访问 HuggingFace；否则不加句向量（LangChain 跳过、混合检索以 BM25 为主并可选用 Ollama embedding）。只有显式把 `A23_QNA_SENTENCE_TRANSFORMER` 设为 Hub 模型名时才会联网解析。
- **持久化**：`persist_session=true`（默认）时算法侧写入 `storage/sessions/<session_id>/`，可仅凭 `session_id` 复用已上传文件；设为 **false** 或环境 **`A23_QNA_PERSIST_SESSION=false`** 时使用临时目录、**响应结束后删除**，此时**每次请求必须带 `files`**，多轮上下文由业务后端存库并在 **`history_json`** 传入先前问答。
- **返回**：`answer`、`session_id`、`sources`、`persist_session`、`qna_method`（`langchain` / `hybrid` / `none`）。

#### 句向量模型（离线优先）

- **预下载（推荐）**：在项目根执行 `python scripts/download_qna_embedding_model.py`（国内可先设 **`HF_ENDPOINT=https://hf-mirror.com`**）。完成后默认会自动识别 **`models/qna_embedding`**，一般无需再配环境变量。
- **联网解析 Hub 模型名**：仅在 `.env` 中显式设置 **`A23_QNA_SENTENCE_TRANSFORMER=paraphrase-multilingual-MiniLM-L12-v2`**（或其它 Hub id）时才会访问 Hub；镜像仍可通过 **`HF_ENDPOINT`** 配置。
- **超时**：可增大 **`HF_HUB_DOWNLOAD_TIMEOUT`**（秒）。

---

## 1) 推荐集成：同步模板抽取 `POST /api/extract/direct`

- **用途**：业务后端在自有队列/worker 中调用；**一次 HTTP 请求内**完成抽取并返回结果。
- **请求**：`multipart/form-data`（模板文件、输入文件列表等，详见 `/docs`）。
- **返回**：JSON，含 `records`、`metadata`、`routing_info` 等；若生成填表文件则含 `output_file` / `task_id` / `output_dir`（与 `A23_PERSIST_UPLOADS` 相关）。`metadata.profile` 为本次抽取使用的 profile（与 CLI 一致，便于联调）。
- **超时**：表单可提供 `total_timeout`、`max_chunks` 等（以 OpenAPI 为准）；或由服务端按复杂度调整（见实现）。
- **用户要求 / 抽取指令**（与 CLI `--instruction` 及输入目录侧文件对齐）：
  - 表单字段 `instruction`：直接粘贴说明文字；
  - 可选 `instruction_file`：上传 UTF-8 文本；若与 `instruction` 同时提供，则**先表单正文、再文件正文**，中间以空行拼接；
  - 若上述皆空，且保存后的 `inputs/` 目录内存在 `用户要求.txt`（可与数据文件一起作为 `input_files` 上传），服务端会**自动读取**。
  - **多表 Word 分表**：优先解析指令中的「表1：」「表2：」块；若某表仍无筛选条件，会读取该表在 **Word 模板里表格上方的段落**（与表头列名一致的「列名：取值」行，**最后一行**命中列作为子串筛选字段）。二者皆无时，扁平 `records` 仍会全部写入第一张表。

示例（字段名以 `/docs` 为准）：

```bash
curl -sS -X POST "http://127.0.0.1:8000/api/extract/direct" \
  -F "template=@./template.docx" \
  -F "input_files=@./source.xlsx" \
  -F "instruction=表1：…（分表与筛选说明）"
# 或长文本放文件：
curl -sS -X POST "http://127.0.0.1:8000/api/extract/direct" \
  -F "template=@./template.docx" \
  -F "input_files=@./source.xlsx" \
  -F "instruction_file=@./用户要求.txt"
```

---

## 2) 抽取结果输出约定（后端必须知道）

### `output_files` 对外约定

- 对后端返回的 `output_files` **默认不包含** `report_bundle`（调试产物）
- 常用字段：
  - `result_json` / `json`
  - `result_xlsx` / `excel`
  - `by_input`（多文件时按输入文件分组）
  - `multi_input`

### `report_bundle` 规则

- 默认不在 API 响应中暴露
- `download/report_bundle` 在非调试模式返回 404
- 调试时如需读取 report，请在内网运维侧开启 `A23_DEBUG`

---

## 3) 直接抽取补充说明：`/api/extract/direct`

- `routing_info.complexity_analysis` 可提供分流估算信息
- **内部实现口径（对接排障用）**：
  - 模型阶段：`src/core/model_extraction_orchestrator.py`
  - 结果合流：`src/core/extraction_result_harmonizer.py`
  - API 打包：`src/core/result_packager.py`
  - 模板写回：`src/core/output_writer_orchestrator.py`
  - 耗时摘要：`src/core/runtime_observability.py`

---

## 4) 无模板抽取：`/api/extract/no-template`

- `instruction` **可选**；为空时走自动结构分析模式
- 接口始终返回 JSON
- 当生成了结构化输出文件（如 xlsx）时：
  - 返回 `download_url`（用于后端下载）
  - 返回 `output_file`（服务端持久化路径）
  - `metadata.persisted_output=true`

### 临时导出确认清理

- `POST /api/download/temp/{filename}/export-complete`
  - 后端确认文件已接收后调用
  - 触发立即删除临时文件

---

## 5) `pipeline_routing`（排障与观测）

抽取响应中的 `metadata`/`slicing_metadata` 包含 `pipeline_routing`（由 `src/core/extraction_routing.py` 生成）：

- 输入类型（后缀、`input_kind`）
- 主路由（`primary_track`）
- 多表 Word 分支信息
- 阶段标签 `stages`
- 统一编排链路下的阶段元信息（含 scope/slicing 相关摘要）

该字段用于排障与链路观测，不影响业务消费逻辑。

---

## 附录：算法内置异步任务（可选，`A23_ENABLE_TASKS=true`）

仅当业务**没有**自有任务队列、需在算法进程内起长任务时使用。与 **`POST /api/extract/direct` 二选一为主集成方式**，不建议与业务侧异步 HTTP 重复启用。

### 创建任务

- **端点**: `POST /api/tasks/create`
- **请求**: `multipart/form-data`
- **主要参数**（以 `/docs` 为准）:
  - `template`（可选）
  - `input_files`（可多文件）
  - `note`（业务抽取指令）
  - `model_type`（`ollama` / `openai` / `qwen` / `deepseek` / `moonshot` / `zhipu` / `glm` / `baichuan` / `siliconflow` / `doubao`，与 `src.config` 中 `A23_MODEL_TYPE` 一致）
  - `template_mode`（`auto/file/llm`）
  - `template_description`（`template_mode=llm` 时使用）
  - `llm_mode`（`full/off`，`supplement` 自动映射到 `full`）
  - `total_timeout`、`max_chunks`、`quiet`

### 查询与下载

- `GET /api/tasks/{task_id}`：任务状态与输出文件索引
- `GET /api/tasks/{task_id}/result`：任务结果摘要
- `GET /api/tasks/{task_id}/download/{kind}`：下载文件

### 导出后清理

- `POST /api/tasks/{task_id}/export-complete?cleanup=true|false`
  - `cleanup=true`：立即删除任务目录（默认）
  - `cleanup=false`：仅记录确认，不删除

当 `A23_ENABLE_TASKS=false` 时，上述路由返回 **404**，业务侧应仅使用「推荐集成」中的同步接口。
