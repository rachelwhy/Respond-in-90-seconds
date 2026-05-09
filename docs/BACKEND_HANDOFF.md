# A23 后端交付与对接说明

权威字段与路由细节以根目录 **[HTTP_API_USAGE.md](../HTTP_API_USAGE.md)** 为准；本文为落地流程摘要。

## 1. 对接分工（权责）

| 侧 | 职责 |
|----|------|
| **算法服务（本仓库）** | 文档解析、抽取、后处理、填表/结构化输出；提供 **同步** `POST /api/extract/direct` 等 HTTP 契约。 |
| **业务后端 / 网关** | 鉴权、限流、审计、异步队列、任务中心、跨服务编排、入库；在 worker 中调用算法 **同步** 接口即可。 |

**默认约定**：算法端内置 **`/api/tasks/*`** 默认 **关闭**（`A23_ENABLE_TASKS=false`），避免与业务自有异步 HTTP 重复。需要本地长任务联调时再打开。

## 2. 对接目标（消费字段）

后端与算法服务对接时，默认只消费业务产物：

- `result_json`
- `result_xlsx`
- 多输入场景下 `by_input`

调试产物 `report_bundle` 不作为业务接口对外返回。

## 3. 推荐调用顺序（生产：业务异步 + 算法同步）

1. 业务后端在自有队列/worker 中准备模板与输入文件（或对象存储 URL 落盘到 worker 可见路径）。
2. 调用 **`POST /api/extract/direct`**（`multipart/form-data`），等待单次抽取完成。
3. 从响应 JSON 读取 `records`、`metadata`、`output_file`（若有）等；按业务规则入库或转发。
4. 若使用无模板接口且返回 `download_url`，下载后调用 **`POST /api/download/temp/{filename}/export-complete`** 触发临时文件清理。

无需调用算法侧 `/api/tasks/*`，除非双方明确约定启用 `A23_ENABLE_TASKS=true`。

## 4. 无模板接口对接

- 端点：`POST /api/extract/no-template`
- 返回始终是 JSON
- 若生成结构化文件，会返回 `download_url`、`output_file`、`metadata.persisted_output=true`
- 下载后调用：`POST /api/download/temp/{filename}/export-complete`

## 5. 输出字段约定（同步 `/api/extract/direct`）

响应中含 `records`、`metadata`、`routing_info`；有落盘时含 `task_id`（本次工作目录名）、`output_dir` 等，以实际 JSON 为准。

`output_files`（若存在）默认**不包含** `report_bundle`。

## 6. 清理策略

- **同步落盘目录**（`storage/uploads/`）：`A23_UPLOAD_RETENTION_HOURS`、`A23_TEMP_RETENTION_HOURS`
- **算法内置任务目录**（`storage/tasks/`，仅 `ENABLE_TASKS=true`）：`A23_TASK_RETENTION_HOURS`
- **问答会话目录**（`storage/sessions/`，仅 `A23_QNA_PERSIST_SESSION=true` 时累积）：业务若自行存会话与文件，建议设 **`A23_QNA_PERSIST_SESSION=false`**，每次请求带 **`files`** + 可选 **`history_json`**，算法侧不落盘（详见 `HTTP_API_USAGE.md`「文档问答」）
- **临时导出确认**：`POST /api/download/temp/{filename}/export-complete`

## 7. 关键接口清单（推荐路径优先）

- `POST /api/extract/direct`（**主路径**）
- `POST /api/extract/pre-analyze`（可选）
- `POST /api/extract/no-template`
- `GET /api/download/temp/{filename}`
- `POST /api/download/temp/{filename}/export-complete`
- `GET /api/health`、`GET /api/models` 等（见 `HTTP_API_USAGE.md`）
- `POST /api/qna/ask`（文档问答：默认 LangChain，见 `A23_QNA_USE_LANGCHAIN` 与 `HTTP_API_USAGE.md`）

可选（仅 `A23_ENABLE_TASKS=true`）：`POST /api/tasks/create`、`GET /api/tasks/{task_id}`、`POST /api/tasks/{task_id}/export-complete` 等。

## 8. 兼容性与注意事项

- **`A23_ENABLE_TASKS=false`（默认）**：`/api/tasks/*` 与依赖任务的 ingest 路由不可用；业务侧用自有队列 + **`/api/extract/direct`**。
- 模型与超时建议由环境变量统一配置，避免与部署环境冲突。

## 9. 浏览器与跨域（网页组）

- 前端页面与算法服务**不同源**（不同端口、主机或协议）时依赖 **CORS**。默认已在算法服务侧配置（本地开发 Origin + 私网正则）；**生产**请将真实前端 **Origin** 写入 **`A23_CORS_ORIGINS`**（逗号分隔），详见 **[HTTP_API_USAGE.md](../HTTP_API_USAGE.md)**。
- **同源**反向代理（同一站点仅路径区分前后端）时，一般不触发跨域限制。
