# A23 后端交付与对接说明

## 1. 对接目标

后端与算法服务对接时，默认只消费业务产物：

- `result_json`
- `result_xlsx`
- 多输入场景下 `by_input`

调试产物 `report_bundle` 不作为业务接口对外返回。

## 2. 推荐调用顺序（模板任务）

1. `POST /api/tasks/create` 上传模板与输入文件，拿到 `task_id`
2. 轮询 `GET /api/tasks/{task_id}` 或 `GET /api/tasks/{task_id}/stream`
3. 完成后调用 `GET /api/tasks/{task_id}/result` 获取输出路径索引
4. 用 `GET /api/tasks/{task_id}/download/{kind}` 下载业务文件
5. 后端确认导出完成后，调用 `POST /api/tasks/{task_id}/export-complete?cleanup=true`

## 3. 无模板接口对接

- 端点：`POST /api/extract/no-template`
- 返回始终是 JSON
- 若生成结构化文件，会返回：
  - `download_url`
  - `output_file`
  - `metadata.persisted_output = true`

下载后，后端应调用：

- `POST /api/download/temp/{filename}/export-complete`

以触发临时导出文件即时清理。

## 4. 输出字段约定

### `GET /api/tasks/{task_id}/result`

返回结构示例（简化）：

```json
{
  "task_id": "xxxx",
  "status": "succeeded",
  "output_files": {
    "result_json": "...",
    "result_xlsx": "...",
    "by_input": {
      "input_a": {
        "json": "...",
        "excel": "..."
      }
    },
    "multi_input": false
  },
  "report_bundle": null
}
```

说明：

- `output_files` 默认不包含 `report_bundle`
- `report_bundle` 字段在非调试模式固定为 `null`

## 5. 清理策略

自动清理（按环境变量）：

- `A23_TASK_RETENTION_HOURS`
- `A23_UPLOAD_RETENTION_HOURS`
- `A23_TEMP_RETENTION_HOURS`

主动清理（推荐）：

- 任务导出后：`POST /api/tasks/{task_id}/export-complete`
- 临时下载后：`POST /api/download/temp/{filename}/export-complete`

## 6. 关键接口清单

- `POST /api/tasks/create`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/result`
- `GET /api/tasks/{task_id}/download/{kind}`
- `POST /api/tasks/{task_id}/export-complete`
- `POST /api/extract/no-template`
- `GET /api/download/temp/{filename}`
- `POST /api/download/temp/{filename}/export-complete`

## 7. 兼容性与注意事项

- 当 `A23_ENABLE_TASKS=false` 时，`/api/tasks/*` 不可用。
- 若后端只需同步能力，可使用 `/api/extract/direct` 或 `/api/extract/no-template`。
- 模型侧建议由环境变量统一配置，避免请求参数与服务端配置冲突。
