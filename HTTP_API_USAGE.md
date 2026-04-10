# A23 HTTP API 使用说明（企业内网版）

## 启动
```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Swagger 文档：
- http://127.0.0.1:8000/docs

## 认证系统（已移除）

> **注意**: 根据后端要求，AI端不处理鉴权，所有接口均为公开接口。以下认证系统文档仅供参考（历史版本）。

系统使用JWT令牌进行认证，所有API端点（除健康检查和登录外）都需要有效的Bearer Token。

### 1. 登录获取Token
- **端点**: POST `/api/auth/login`
- **请求体**:
  ```json
  {
    "username": "admin",
    "password": "admin123"
  }
  ```
- **响应**:
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1NiIs...",
    "token_type": "bearer",
    "expires_in": 1800
  }
  ```

### 2. 使用Token
在请求头中添加：
```
Authorization: Bearer <access_token>
```

### 3. 用户管理
- **获取当前用户信息**: GET `/api/auth/me` (需要认证)
- **获取所有用户**: GET `/api/auth/users` (仅管理员)
- **注册新用户**: POST `/api/auth/register` (仅管理员)

### 4. 默认账户
首次启动时系统会自动创建默认管理员账户：
- 用户名: `admin`
- 密码: `admin123`

## 核心接口

> **认证要求**: 根据后端要求，AI端不处理鉴权，所有接口均为公开接口。

### 1. 健康检查
- GET `/api/health`

### 2. 模板填表任务

#### 创建任务
- **端点**: POST `/api/tasks/create`
- **认证**: 无需认证（根据后端要求，AI端不处理鉴权）
- **请求格式**: multipart/form-data
- **参数**:
  - `template`: 模板文件（Excel/Word）（可选，template_mode为'file'或'auto'时需要）
  - `input_files`: 输入文件列表（支持多个文件）
  - `note`: 可选，自定义抽取指令
  - `model_type`: 可选，模型类型（`ollama`、`openai`、`qwen`、`deepseek`），默认使用环境变量 `A23_MODEL_TYPE` 配置
  - `template_mode`: 可选，模板模式，可选值：'file'（使用上传的模板文件）、'llm'（仅用LLM指令生成模板）、'auto'（自动选择，默认）
  - `template_description`: 可选，模板描述（当template_mode='llm'时必填）
  - `llm_mode`: 可选，LLM抽取模式，可选值：'full'（始终全文抽取，默认）、'supplement'（仅补充缺失字段）、'off'（仅规则抽取）
  - `total_timeout`: 可选，总超时时间（秒），默认110秒
  - `max_chunks`: 可选，最大语义分块数量，默认50
  - `quiet`: 可选，安静模式，禁用控制台输出，默认False
- **响应**:
  ```json
  {
    "task_id": "任务ID",
    "status": "queued",
    "template_name": "模板文件名",
    "input_files": ["文件1", "文件2"],
    "status_url": "/api/tasks/{task_id}",
    "events_url": "/api/tasks/{task_id}/events",
    "stream_url": "/api/tasks/{task_id}/stream",
    "result_url": "/api/tasks/{task_id}/result"
  }
  ```

#### 其他端点
- GET `/api/tasks/{task_id}` - 获取任务状态
- GET `/api/tasks/{task_id}/events` - 获取任务日志
- GET `/api/tasks/{task_id}/stream` - SSE 实时日志流
- GET `/api/tasks/{task_id}/result` - 获取任务结果摘要
- GET `/api/tasks/{task_id}/download/{kind}` - 下载输出文件（`result_json`、`result_xlsx`、`result_docx`、`report_bundle`）

### 3. 直接抽取API

#### 直接抽取
- **端点**: POST `/api/extract/direct`
- **认证**: 无需认证
- **请求格式**: multipart/form-data
- **参数**:
  - `template`: 模板文件（Excel/Word）
  - `input_files`: 输入文件列表（支持多个文件）
  - `model_type`: 可选，模型类型（`ollama`、`openai`、`qwen`、`deepseek`），默认使用环境变量 `A23_MODEL_TYPE` 配置
  - `instruction`: 可选，自定义抽取指令
  - `llm_mode`: 可选，LLM抽取模式，可选值：'full'（始终全文抽取，默认）、'supplement'（仅补充缺失字段）、'off'（仅规则抽取）（默认 'full'）
  - `enable_unit_aware`: 可选，启用单位感知提取（默认 `true`）
  - `total_timeout`: 可选，总超时时间（秒），默认110秒
  - `max_chunks`: 可选，最大语义分块数量，默认50
  - `quiet`: 可选，安静模式，禁用控制台输出，默认False
- **响应**:
  ```json
  {
    "success": true,
    "data": { ... },  // 抽取结果，格式与任务结果相同
    "metadata": {
      "template_path": "...",
      "input_dir": "...",
      "model_type": "...",
      "instruction": "...",
      "internal_route_used": "...",
      "missing_required_fields": [...],
      "rule_extraction_summary": { ... },
      "unit_aware_extraction": true/false
    },
    "unit_aware_result": { ... }  // 可选，单位感知提取结果
  }
  ```
- **特点**: 直接调用抽取核心，无需创建任务，实时返回结果。适用于快速测试和小规模抽取。

### 4. 无模板抽取API

#### 无模板抽取
- **端点**: POST `/api/extract/no-template`
- **认证**: 无需认证
- **请求格式**: multipart/form-data
- **参数**:
  - `input_files`: 输入文件列表（支持多个文件）
  - `instruction`: 必填，抽取指令（描述要提取的字段信息）
  - `model_type`: 可选，模型类型（`ollama`、`openai`、`qwen`、`deepseek`），默认使用环境变量 `A23_MODEL_TYPE` 配置
  - `llm_mode`: 可选，LLM抽取模式，可选值：'full'（始终全文抽取，默认）、'supplement'（仅补充缺失字段）、'off'（仅规则抽取）（默认 'full'）
  - `enable_unit_aware`: 可选，启用单位感知提取（默认 `true`）
  - `total_timeout`: 可选，总超时时间（秒），默认110秒
  - `max_chunks`: 可选，最大语义分块数量，默认50
  - `quiet`: 可选，安静模式，禁用控制台输出，默认False
- **响应**: 与直接抽取API响应格式相同
- **特点**: 无需模板文件，仅通过指令描述要提取的字段，由LLM自动推断字段结构并抽取。

### 5. 文档 QnA
- POST `/api/qna/ask`

## 基准任务路径（中文命名）
- 基准清单：`test/assets/清单/基准任务清单.json`
- 模板目录：`test/assets/模板`
- 输入目录：`test/assets/任务输入`
- 标准答案：`test/assets/标准答案`
- 批处理输出：`test/results/outputs`
