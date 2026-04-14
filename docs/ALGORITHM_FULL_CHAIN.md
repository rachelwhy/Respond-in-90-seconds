# A23 算法层全链路说明（可直接用于 Mermaid 建模）

## 1. 入口层

算法层有三条入口，最终汇聚到 `src/core`：

1. `POST /api/extract/direct`（同步模板抽取）
2. `POST /api/extract/no-template`（同步无模板抽取）
3. `POST /api/tasks/create`（异步任务，`task_manager` 子进程执行 `main.py`）

## 2. 输入读取与结构化预处理

共同关键过程：

1. `collect_input_bundle(input_dir)`
   - 读取输入文件列表
   - 调用 `src/adapters/parser_factory.py` 选择解析器
   - 聚合 `all_text`、`documents`、`tables`、`chunks`
2. `collect_semantic_chunks_from_bundle(bundle)`
   - 将文档语义块扁平化为统一 chunk 列表
3. 模板侧能力识别：
   - `template_detector.py` 识别模板结构
   - `profile.py` 生成/修正 profile（字段、表格规格、约束）

## 3. 抽取路由判定

由 `src/core/extraction_routing.py` 生成 `pipeline_routing` 元数据，核心判定包括：

- 是否多表 Word 并行路径
- 是否优先语义分块路径
- 是否回退字符切片
- 是否启用 LangExtract 补齐

该元数据写入响应 `metadata`，用于观测与排障。

## 4. 抽取主链路（核心）

主函数：`src/core/extraction_service.py::extract_with_slicing`

主要阶段：

1. 路由元数据构建（`pipeline_routing`）
2. 模型可用性与 `llm_mode` 判定
3. 多表 Word 并行分支（命中时）
   - 按表说明或分段执行模型抽取
   - 形成 `_table_groups`
   - 按策略可再走 LangExtract 合并
4. 通用分块分支（未命中多表并行）
   - 优先语义 chunk 抽取
   - 失败或不适配时回退字符切片
5. 合并输出为统一 `records` / `metadata`

## 5. API 编排层差异

### `direct_extractor.py`

- 强调 HTTP 同步返回
- 在并行抽取路径中会提前做 `process_by_profile`
- 多表场景可做 internal merge（表格直读补缺）

### `main.py`

- CLI/子进程入口
- 会执行更完整的落盘与结果打包
- 可进行缺字段重试与更细的运行日志输出

## 6. 后处理链路

核心模块：`src/core/postprocess.py`

顺序要点：

1. 字段名归一化（alias 映射）
2. 字段值清洗（数字、日期、单位、Series 残留）
3. `FieldInterpreter` 语义解释（否定证据、局部片段、聚合值）
4. 指令过滤（如日期范围过滤）
5. 去重与排序（原文顺序稳定化）
6. 生成最终 `records`

## 7. 写回与输出

核心模块：`src/core/writers.py`

- JSON 输出
- Excel 模板写回
- Word 模板写回（包含多表写入）

任务接口输出目录约定：

- `storage/tasks/<task_id>/output/*_result.json`
- `storage/tasks/<task_id>/output/*_result.xlsx`
- `storage/tasks/<task_id>/output/*_result_report.json`（调试产物）

## 8. 存储与清理链路

### 任务文件

- 创建：`/api/tasks/create`
- 下载：`/api/tasks/{task_id}/download/{kind}`
- 确认导出后清理：`/api/tasks/{task_id}/export-complete`

### 无模板临时导出

- 生成：`/api/extract/no-template` 返回 `download_url`
- 下载：`/api/download/temp/{filename}`
- 确认导出后清理：`/api/download/temp/{filename}/export-complete`

## 9. Mermaid 建模建议节点

可按以下主轴画图（节点名建议）：

1. `Entry(API Direct / API NoTemplate / API Task / CLI)`
2. `InputBundle`
3. `TemplateDetect+Profile`
4. `RoutingMeta(pipeline_routing)`
5. `ExtractWithSlicing`
6. `WordMultiParallel?`
7. `SemanticChunks`
8. `CharSliceFallback`
9. `LangExtractMerge`
10. `Postprocess`
11. `Writer(JSON/Excel/Word)`
12. `TaskOutput`
13. `ExportConfirmCleanup`
