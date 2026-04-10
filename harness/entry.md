# A23 AI 文档处理系统 — Harness Engineering 框架

## 项目一句话定义

A23 是一个面向比赛的企业级智能文档处理平台，包含**文档智能操作交互**、**非结构化文档信息提取**、**表格自定义数据填写**三大核心模块，采用 Docling + LLM 混合策略实现高精度抽取与自动化操作，为网页端和数据库提供标准化算法接口。

---

## 模块架构

| 模块 | 编号 | 负责方 | 核心组件 |
|------|------|--------|---------|
| 文档智能操作交互 | M1 | AI组 | `api_server.py` 操作类端点 |
| 非结构化文档信息提取 | M2 | AI组 | `universal_extractor.py` + `docling_parser.py` |
| 表格自定义数据填写 | M3 | AI组 | `writers.py` + `alias_resolver.py` |
| 数据存储与管理 | DB | 数据库组 | 调用 AI 组输出接口 |
| 用户界面与交互 | WEB | 网页组 | 调用 `api_server.py` HTTP API |

---

## 团队职责边界

### AI 组（本项目）

**负责**：
- M1/M2/M3 三大模块的算法实现
- `src/` 目录下所有代码
- `api_server.py` 的路由和接口定义（**路由一旦稳定，禁止改动签名**）
- `src/config.py` 和环境变量的配置管理

**不负责**：
- 数据库读写（`src/knowledge/base.py` 中 `DatabaseKnowledgeSource` 由数据库组实现）
- 前端 HTML/JS/CSS
- 测试用例编写（QA 负责）
- 性能监控和日志分析系统

### 数据库组

**接入点**：
- 实现 `src/knowledge/base.py` 中的 `KnowledgeSource` 接口
- 读取 AI 组输出的 `{"records": [...]}` JSON，写入数据库

### 网页组

**接入点**：
- 所有 `POST /api/tasks/*` 和 `POST /api/extract/*` 端点
- SSE 流式进度接口 `GET /api/tasks/{id}/stream`

---

## 硬约束

### 算法约束
- **Docling 是唯一文档解析入口**，禁止回退到旧解析器
- 所有表格数据必须经过 `alias_resolver` 字段别名映射才能成为 records
- 输出 records 中每条记录的字段名必须与模板字段名严格一致
- 模型调用超时：120 秒，最多重试 3 次

### 接口约束
- **`api_server.py` 路由签名冻结**：端点 URL、Form 参数名、响应 JSON 结构不得变更
- 所有公共方法必须有类型注解和 docstring
- 输出格式统一：`{"records": [...], "metadata": {...}}`

### 安全约束
- 不硬编码 API 密钥，一律通过 `.env` / 环境变量传入
- 无需鉴权（根据后端要求，AI 端无鉴权层）

---

## 核心问题列表

**M2 - 信息提取**
1. Docling 表格提取的 DataFrame 列名可能与模板字段名不匹配 → 使用 `alias_resolver` 解决
2. 纯文本文档中 LLM 提取率不稳定 → 表格优先，文本兜底

**M3 - 表格填写**
3. 字段别名覆盖不全导致映射失败 → 更新 `src/knowledge/field_aliases.json`
4. 多表格文档中表格归属问题 → `docling_parser` 按索引区分

**通用**
5. 模型不可用时无优雅降级 → `model_client.py` 三级回退（Ollama → DeepSeek → 返回空）

---

## 质量指标目标

| 指标 | 目标 | 验证方法 |
|------|------|---------|
| 字段提取准确率 | ≥ 80% | 对比标注测试集 |
| 表格填充完整率 | ≥ 90% | 模板字段覆盖率 |
| 单文档处理时间 | ≤ 60s | 计时日志 |
| API 并发支持 | ≥ 10 | 压测 |
