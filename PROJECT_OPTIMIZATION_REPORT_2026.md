# A23 项目结构优化探索报告（2026-04）

> **范围说明**：本文档仅做**非功能性**优化建议与文档勘误，**不新增产品功能**、不改变对外 API 行为。供评审与排期参考。

---

## 1. 执行摘要

当前仓库以 **`main.py`（CLI）** 与 **`api_server.py`（FastAPI）** 为双入口，核心逻辑集中在 **`src/core/`**、**`src/adapters/`**、**`src/api/`**。  
文档（`README.md`、`A23_TECHNICAL_FLOW.md`）仍描述部分**已不存在**的目录（如 `src/algorithm`、`src/pipeline`、`src/engine`、`src/parsers`），易造成新成员误读与错误引用。

**建议优先级**：

| 优先级 | 项 | 说明 |
|--------|----|------|
| P0 | 文档与目录对齐 | 更新 README / 技术流文档，避免指向缺失模块 |
| P1 | 仓库卫生 | `tests/`、`scripts/`、运行期缓存/向量库等已默认 **不纳入 Git**（仅保留算法与 API 所需源码） |
| P2 | 测试与质量门 | `tests/unit/` 大量失败为**既有问题**；需单独排期修复或收窄测试范围 |
| P3 | 工程体验 | 重复路径（`src\api` 与 `src/api` 在 Windows 上实为同一树）、IDE 索引噪音 |

---

## 2. 实际目录结构（与文档对照）

### 2.1 顶层（当前）

| 路径 | 作用 |
|------|------|
| `main.py` | CLI 主流程 |
| `api_server.py` | HTTP API |
| `src/adapters/` | 模型、Docling、解析、langextract 适配 |
| `src/api/` | `direct_extractor`、`task_manager`、`qna_service` |
| `src/core/` | 抽取服务、后处理、reader、writer、profile 等 |
| `src/knowledge/` | 别名、归一化规则等 |
| `third_party/` | 内嵌 langextract 等（若存在） |
| `storage/` | 任务与上传持久化（调试/生产策略需约定） |
| `tests/` | 单元与集成测试 |

### 2.2 文档中已不存在的路径（需勘误）

以下在 **当前仓库根目录下不存在**（`README` / `A23_TECHNICAL_FLOW` 仍提及）：

- `src/algorithm/`
- `src/pipeline/`
- `src/engine/`
- `src/parsers/`
- `src/extractors/`

对应能力已**合并**到 `src/core/`、`src/adapters/` 及 `main.py` 流程中，并非“缺文件”。

---

## 3. 当前主流程（简要）

1. **输入**：`reader.collect_input_bundle()` 聚合文本；Docling 解析器产出 `documents`、语义 chunks（若可用）。
2. **结构化捷径**：`try_internal_structured_extract()` — 仅当 Docling 表格能映射到模板字段时命中，否则走模型。
3. **模型抽取**：`src/core/extraction_service.py` + `src/adapters/model_client.py`（Ollama / OpenAI 兼容 / DeepSeek 等）。
4. **后处理**：`src/core/postprocess.py`（归一化、去重、**可选**按原文顺序排序等）。
5. **写出**：`src/core/writers.py` 等；API 任务由 `src/api/task_manager.py` 子进程调用 `main.py`，结果在 `storage/tasks/<id>/output/`。

### 3.1 API 任务（`POST /api/tasks/create`）

- 子进程执行 `main.py`，环境变量传递 `A23_MODEL_TYPE` 等。
- 任务目录、`request_meta.json` 保存元数据（`total_timeout`、`max_chunks`、`llm_mode` 等）。
- **文档与实现需一致**：外层 watchdog 超时为 **`total_timeout + 300` 秒**（缓冲），避免与 `main.py --total-timeout` 混淆。

---

## 4. 非功能性优化建议（不改行为）

### 4.1 文档

- 统一 `README.md` 与 `A23_TECHNICAL_FLOW.md` 的架构图与路径。
- `HTTP_API_USAGE.md` 顶部已说明鉴权移除；**认证相关旧章节**可折叠为「历史」或删除，减少误导。
- 在 `CLAUDE.md` 或 `A23_TECHNICAL_FLOW.md` 增加**单行**：当前权威模块列表 = `src/adapters` + `src/api` + `src/core` + `src/knowledge`。

### 4.2 仓库与忽略规则

- 将 `storage/tasks/**`、`storage/uploads/**`（若存在）按环境决定是否忽略；生产若仅内存/对象存储，可忽略整个 `storage/`。
- `.pytest_cache/`、`*_result*.xlsx` 临时输出、根目录 `uv_err.txt` 等建议纳入 `.gitignore`（若团队不提交本地调试产物）。

### 4.3 代码与依赖（低风险）

- **延迟导入**：Docling / 重型依赖在首次需要时再 import，可缩短冷启动（需基准测试，避免改变异常语义）。
- **单一路径引用**：避免同一仓库内同时出现 `src\foo` 与 `src/foo` 的重复展示（Windows 工具问题），以 `src/` 正斜杠为准。

### 4.4 测试

- 当前 `pytest tests/unit/` 存在**大量失败**（与 chunk_cache、chunk_merger、parallel_processor 等模块不一致），属**技术债**。
- 建议：**修复测试**或 **标记 xfail / 拆分 legacy**，避免 CI 无法作为门禁。

---

## 5. 已知风险与约束（非本次修改）

- **本地小模型**条数与耗时波动属于正常现象；过度叠加「格式约束 / 补漏 / 多轮」可能反噬条数。
- **langextract**：对本地小模型（&lt;14B）默认跳过 langextract 为**有意策略**（性能与稳定性权衡），与文档描述需一致。

---

## 6. 建议的后续动作（排期）

1. **文档 PR**：按本报告更新 `README.md`、`A23_TECHNICAL_FLOW.md`、`HTTP_API_USAGE.md`（已完成一版同步，见同提交）。
2. **`.gitignore` 清理 PR**：仅忽略规则，无逻辑变更。
3. **测试修复 PR**：单独分支，逐模块修复或跳过过时用例。

---

## 7. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-04-13 | 初稿：基于当前仓库结构与文档对照 |
