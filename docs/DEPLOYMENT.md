# 部署与抽取路由（生产视角）

## 上线形态（以网页/API 为准）

- **同步抽取**：`api_server.py` → `src/api/direct_extractor.py` → `CoreExtractionService.extract_with_slicing`（与 CLI 共用核心服务）。
- **异步任务**：`POST /api/tasks/create` → `task_manager` 子进程执行 `main.py`（长任务、落盘、日志）。
- **命令行 `main.py`**：便于开发、回归与无 HTTP 环境；功能应对齐 API，但**不以 CLI 为唯一事实来源**。

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

- 任务目录：`storage/tasks/<task_id>/...`（受 `A23_TASK_RETENTION_HOURS` 控制）
- 上传目录：`storage/uploads/<request_id>/...`（受 `A23_UPLOAD_RETENTION_HOURS` 控制）
- 临时导出：`storage/uploads/temp/<filename>`（受 `A23_TEMP_RETENTION_HOURS` 控制）
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
| `A23_WORD_MULTI_LANGEXTRACT` | 多表并行后 LangExtract 补缺：未设=自动；1=强开；0=关 |
| `A23_WORD_MULTI_MERGE_INTERNAL` | 后处理是否用 Docling/表直读合并进 `_table_groups` |

详见 `src/config.py` 与 `HTTP_API_USAGE.md`。
