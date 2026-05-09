# 部署与抽取路由（生产视角）

## 上线形态（以网页/API 为准）

- **同步抽取**：`api_server.py` → `src/api/direct_extractor.py` → `src/core/model_extraction_orchestrator.py`（统一模型编排）→ `extract_with_slicing`。
- **可选：算法内置异步任务**：`POST /api/tasks/create` → `task_manager` 子进程执行 `main.py`；**默认** `A23_ENABLE_TASKS=false` 关闭路由；本地联调长任务时可设 `true`。
- **命令行 `main.py`**：便于开发、回归与无 HTTP 环境；与 API 共用 `model_extraction_orchestrator` / `extraction_result_harmonizer` / `result_packager` / `output_writer_orchestrator` / `runtime_observability`，但**不以 CLI 为唯一事实来源**。
- **文档问答**：`POST /api/qna/ask` → `qna_service`：默认 **`qna_langchain`**（LangChain + Chroma + HuggingFaceEmbeddings），失败或 **`A23_QNA_USE_LANGCHAIN=false`** 时用 **`qna_retrieval`**（BM25 + 句向量）；生成答案默认 **`A23_QNA_MODEL_TYPE=deepseek`**（与抽取 **`A23_MODEL_TYPE`** 独立）。会话目录 `storage/sessions/` 仅在 **`A23_QNA_PERSIST_SESSION=true`**（默认）时长期保留；业务侧自管会话与文件时设 **`false`** 并每次上传 `files`、可选 `history_json`。详见根目录 `HTTP_API_USAGE.md`。
- **浏览器跨域（网页端调 API）**：`api_server` 已挂 **`CORSMiddleware`**；默认含常见本地 Origin 与私网段正则，生产环境追加 **`A23_CORS_ORIGINS`**。变量与行为以 **`HTTP_API_USAGE.md`** 为准。

## 后端对接关键约定

- 任务结果返回中的 `output_files` 默认不包含 `report_bundle`（调试产物）。
- 后端应消费 `result_json` / `result_xlsx`（多文件场景使用 `by_input`）。
- 无模板抽取若生成结构化文件，会返回 `download_url` 与持久化 `output_file`。

## 导出确认与清理

- 任务导出确认：`POST /api/tasks/{task_id}/export-complete?cleanup=true|false`
  - 默认 `cleanup=true`：立即删除任务目录。
- 临时导出确认：`POST /api/download/temp/{filename}/export-complete`
  - 后端确认文件已接收后调用，立即删除临时文件。

## 存储与保留策略

- 异步任务目录：`storage/tasks/<task_id>/...`（`A23_TASK_RETENTION_HOURS`）
- 上传根 `storage/uploads/`：同步 `/api/extract/direct` 在 `A23_PERSIST_UPLOADS=true` 时为 `<task_id>/inputs`、`output/`；其它请求亦为该根下子目录（`A23_UPLOAD_RETENTION_HOURS`）
- 临时导出：`storage/uploads/temp/<filename>`（受 `A23_TEMP_RETENTION_HOURS` 控制）
- 问答会话：`storage/sessions/<id>/`（仅持久化模式写入；见 `A23_QNA_PERSIST_SESSION`）
- 目录清理时 `storage/uploads/temp` 会按临时文件策略单独管理。

## 输入与路由元数据

解析后的统一结构由 `collect_input_bundle` 产生；语义分块由 `collect_semantic_chunks_from_bundle` 扁平化后传入 `extract_with_slicing(..., chunks=..., routing_bundle=bundle)`。

返回的 `slicing_metadata`（及嵌套字段）中的 **`pipeline_routing`** 由 `src/core/extraction_routing.py` 生成，描述：

- 输入文件类型摘要（后缀、`input_kind`）
- 模板 `template_mode`、主路径 `primary_track`（如多表 Word 并行 / 语义分块栈 / 字符切片）
- 多表 Word 下是否可能执行 LangExtract 补缺及原因、后处理是否走 internal 表合并等

用于排障与监控，**不替代**各模块内部实现。

## 环境变量（节选）

| 变量 | 作用 |
|------|------|
| `A23_WORD_MULTI_PARALLEL` | 是否启用多表 Word 并行 LLM |
| `A23_WORD_MULTI_MERGE_INTERNAL` | 后处理是否用 Docling/表直读合并进 `_table_groups` |

多表 Word 并行之后的 LangExtract 补缺由 `extraction_routing.decide_word_multi_langextract_prefill` 按模板/表头自动决定，**不再提供**单独环境变量开关。

详见 `src/config.py` 与 `HTTP_API_USAGE.md`。
