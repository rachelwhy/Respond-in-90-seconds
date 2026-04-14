# A23 HTTP API 对接说明（后端交付版）

## 启动
```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

- Swagger: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 所有接口默认无鉴权（由上游网关或后端统一管控）

## 环境变量（对接相关）

| 变量 | 作用 |
|------|------|
| `A23_ENABLE_TASKS` | 是否启用 `/api/tasks/*` |
| `A23_PERSIST_UPLOADS` | 是否持久化上传与临时导出 |
| `A23_PERSIST_PROFILES` | 是否持久化自动生成 profile |
| `A23_TASK_RETENTION_HOURS` | 任务目录保留时长（`storage/tasks`） |
| `A23_UPLOAD_RETENTION_HOURS` | 上传目录保留时长（`storage/uploads/<task_id>`） |
| `A23_TEMP_RETENTION_HOURS` | 临时导出文件保留时长（`storage/uploads/temp`） |
| `A23_DEBUG` | 调试模式；影响 report 调试信息暴露 |

## 路由总览

### 健康与模型
- `GET /api/health`
- `GET /api/models`
- `POST /api/models/test-connection`
- `POST /api/models/switch`

### 抽取接口
- `POST /api/extract/direct`
- `POST /api/extract/no-template`
- `POST /api/extract/pre-analyze`

### 异步任务
- `POST /api/tasks/create`
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
- `POST /api/qna/ask`
- `POST /api/document/operate`
- `POST /api/ingest`
- `POST /api/tasks/{task_id}/ingest`
- `GET /api/ingest/{task_id}/records`
- `GET /api/db/health`

## 1) 模板任务（推荐后端集成方式）

### 创建任务
- **端点**: `POST /api/tasks/create`
- **请求**: `multipart/form-data`
- **主要参数**:
  - `template`（可选，自动/文件模板模式时使用）
  - `input_files`（可多文件）
  - `note`（业务抽取指令）
  - `model_type`（`ollama/openai/qwen/deepseek`）
  - `template_mode`（`auto/file/llm`）
  - `template_description`（`template_mode=llm` 时使用）
  - `llm_mode`（`full/off`，`supplement` 自动映射到 `full`）
  - `total_timeout`、`max_chunks`、`quiet`

### 查询与下载
- `GET /api/tasks/{task_id}`：任务状态与输出文件索引
- `GET /api/tasks/{task_id}/result`：任务结果摘要
- `GET /api/tasks/{task_id}/download/{kind}`：下载文件

### 导出后清理（新增）
- **任务级确认**：`POST /api/tasks/{task_id}/export-complete?cleanup=true|false`
  - `cleanup=true`：立即删除任务目录（默认）
  - `cleanup=false`：仅记录确认，不删除

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

## 3) 直接抽取：`/api/extract/direct`

- **用途**：同步抽取，适合小规模请求
- **返回**：始终 JSON；包含 `metadata` 与 `routing_info`
- `routing_info.complexity_analysis` 提供分流估算信息

## 4) 无模板抽取：`/api/extract/no-template`

- `instruction` **可选**；为空时走自动结构分析模式
- 接口始终返回 JSON
- 当生成了结构化输出文件（如 xlsx）时：
  - 返回 `download_url`（用于后端下载）
  - 返回 `output_file`（服务端持久化路径）
  - `metadata.persisted_output=true`

### 临时导出确认清理（新增）
- `POST /api/download/temp/{filename}/export-complete`
  - 后端确认文件已接收后调用
  - 触发立即删除临时文件

## 5) pipeline_routing（排障与观测）

抽取响应中的 `metadata`/`slicing_metadata` 包含 `pipeline_routing`（由 `src/core/extraction_routing.py` 生成）：
- 输入类型（后缀、`input_kind`）
- 主路由（`primary_track`）
- 多表 Word 分支信息
- 阶段标签 `stages`

该字段用于排障与链路观测，不影响业务消费逻辑。
