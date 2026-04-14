# 项目运行全流程与分支（Mermaid）

> 生产以 **HTTP API** 为主；**CLI** 与异步任务子进程共用 `src/core` 核心。  
> 元数据中的 **`pipeline_routing`** 由 `extraction_routing.build_pipeline_routing_meta` 生成，对应下图「抽取核」的静态分支摘要。

---

## 总图（全链路一张图）

下列将 **入口 → 加载 → 抽取核 → 后处理 → 写出** 连成一条主轴；`direct_extract` 与 `main.py` 的差异集中在「抽取核之前/之后的编排」，见图中注释节点。

```mermaid
flowchart TB
  subgraph IN["入口"]
    H1["HTTP: POST /api/extract/direct"]
    H0["HTTP: POST /api/extract/no-template"]
    H2["HTTP: POST /api/tasks/create → 子进程 main.py"]
    H3["CLI: python main.py"]
  end

  subgraph QNA["独立：文档问答"]
    HQ["HTTP: /api/qna/*"] --> QN["qna_service<br/>与填表主链并行产品能力"]
  end

  H1 --> L
  H0 --> L
  H2 --> L
  H3 --> L

  L["① collect_input_bundle<br/>parser_factory → 各文档 text / chunks / tables"]
  L --> P["② profile<br/>模板检测 · table_specs · template_mode"]
  P --> SC["③ collect_semantic_chunks_from_bundle<br/>+ build_word_multi_table_segments（仅 is_word_multi_parallel_enabled）"]

  SC --> NOTE["④ 编排差异见注释<br/>direct: try_internal · 条件并行<br/>main: RAG/内部短路 · context_for_llm"]
  NOTE --> EW

  subgraph CORE["⑤ extract_with_slicing 抽取核"]
    EW["extract_with_slicing<br/>chunks · routing_bundle · word_table_segments"] --> R0["meta.pipeline_routing"]
    R0 --> D0{use_model?}
    D0 -->|否| EM["空结果"]
    D0 -->|是| D1{is_word_multi_parallel_enabled?}
    D1 -->|是| WM["每表并行 call_model<br/>_table_groups"]
    WM --> LX{"LangExtract 补缺?"}
    LX -->|是| ML["merge_langextract → _table_groups"]
    LX -->|否| ER1["records"]
    ML --> ER1
    D1 -->|否| D2{语义 chunks<br/>有文本?}
    D2 -->|是| SE["LangExtract 或<br/>合并块/逐块 prompt"]
    D2 -->|否| CH["字符切片 / 短文本 direct"]
    SE --> ER2["records"]
    CH --> ER2
    ER1 --> EX["extracted_raw + slicing_metadata"]
    ER2 --> EX
    EM --> EX
  end

  EX --> PP["⑥ process_by_profile<br/>main 另有 retry；direct 并行路径或已预跑"]

  PP --> MG{"word_multi_table<br/>且 _table_groups?"}
  MG -->|是| MI["merge_internal_structured<br/>表格直读补缺"]
  MG -->|否| WR
  MI --> WR

  WR["⑦ writers · JSON/Excel/Word<br/>fill_word_table / fill_excel_* / create_excel_*"]
```

**读图要点**

- **⑤ `extract_with_slicing`** 为 CLI / `direct_extract` 共用；**④** 在两者中具体条件不同，但总入口都是 `EW`。  
- **`direct_extract`** 在未走并行时，还会在 ⑤ 之外尝试 `ensure_chunks → LangExtract → UniversalExtractor`（总图从简，未单独画线）。  
- **`main.py`** 在 ⑥ 前后可有 **retry_missing_required_fields**、最终再次 `process_by_profile`（总图合并进 ⑥ 节点语义）。

---

## 1. 入口总览（分图）

```mermaid
flowchart TB
  subgraph HTTP["FastAPI api_server"]
    D["POST /api/extract/direct<br/>direct_extract"]
    T["POST /api/tasks/create<br/>task_manager 后台线程"]
    Q["POST /api/qna/ask<br/>qna_service"]
  end

  subgraph CLI["命令行"]
    M["main.py"]
  end

  T --> SP["子进程执行 main.py<br/>落盘 / extraction.log"]
  D --> B1["collect_input_bundle"]
  M --> B2["collect_input_bundle"]

  B1 --> CORE["src/core 抽取与后处理"]
  B2 --> CORE
  SP --> CORE
  Q --> RAG["检索 + 生成<br/>与填表主链独立"]
```

---

## 2. 抽取核：`extract_with_slicing`（CLI 与 direct 共用）

所有路径返回的 **metadata** 均合并 **`pipeline_routing`**（输入类型、模板模式、`primary_track`、多表 LangExtract 补缺意图、`stages` 等）。

```mermaid
flowchart TB
  IN["输入: text, profile, chunks, routing_bundle, word_table_segments"]

  IN --> RM["build_pipeline_routing_meta<br/>写入 meta.pipeline_routing"]

  RM --> UM{"use_model?"}
  UM -->|否| OFF["返回空结果<br/>mode=model_disabled"]

  UM -->|是| WM{"is_word_multi_parallel_enabled?"}

  WM -->|是| P["并行多表 LLM<br/>_extract_word_multi_table_parallel"]
  P --> LX{"decide_word_multi_langextract_prefill"}
  LX -->|执行| LXM["merge_langextract_into_word_multi_groups"]
  LX -->|跳过| OUT1["返回 + _with_routing"]
  LXM --> OUT1

  WM -->|否| CH{"chunks 存在且<br/>有非 table 文本块?"}

  CH -->|否| CS["字符切片 / 短文本 direct<br/>call_model 按段"]
  CH -->|是| LX2["优先 LangExtract<br/>成功则返回 mode=langextract"]
  LX2 -->|失败或空| CB{"总字符 ≤ 阈值?"}
  CB -->|是| CM["合并块单次 prompt"]
  CB -->|否| LOOP["逐块 prompt +<br/>merge_records_by_key"]

  CS --> OUT2["返回 + _with_routing"]
  CM --> OUT2
  LOOP --> OUT2

  OFF --> OUT0["返回 + _with_routing"]
  OUT1 --> OUT0
  OUT2 --> OUT0
```

---

## 3. HTTP：`direct_extract` 编排（与 `main` 的差异）

并行多表命中时 **一次性** 完成 `extract_with_slicing → process_by_profile → merge_internal_structured`；否则走 **LangExtract → UniversalExtractor** 瀑布。

```mermaid
flowchart TB
  A["collect_input_bundle"] --> B["_load_profile"]
  B --> C["try_internal_structured_extract<br/>结果进入 records_list 候选"]

  B --> D{"llm_mode != off 且<br/>is_word_multi_parallel_enabled?"}

  D -->|是| E["extract_with_slicing<br/>chunks + routing_bundle"]
  E --> F["process_by_profile"]
  F --> G{"word_multi + _table_groups?"}
  G -->|是| H["merge_internal_structured_into_word_multi_groups"]
  G -->|否| I["records_list"]
  H --> I
  I --> J["langextract_used=true<br/>parallel_extracted=true"]
  J --> END1["后续仅组装 records / meta"]

  D -->|否| K["langextract_used=false"]

  K --> L{"langextract_used?"}
  L -->|否| N["ensure_chunks → merge_chunks<br/>extract_with_langextract"]
  N --> O["records 累加"]
  O --> P{"langextract_used?"}

  P -->|否| Q["UniversalExtractor.extract"]
  P -->|是| R["跳过 Q"]

  Q --> S["records_list"]
  R --> S
  S --> T{"parallel_extracted?"}
  T -->|否| U["process_by_profile 标准化"]
  T -->|是| V["已处理，取 processed_bundle"]
  U --> END2["返回 records + metadata"]
  V --> END2
```

---

## 4. CLI：`main.py` 模型路径（摘要）

在 **未** 被 RAG/内部结构化短路时，使用 `context_for_llm` 调 `extract_with_slicing`，再经 **补抽重试**、**最终 process_by_profile**、**多表 internal merge**、**按模板写 Word/Excel**。

```mermaid
flowchart TB
  L["loaded_bundle + profile"] --> C["context_for_llm<br/>可选 RAG 片段"]
  C --> X["extract_with_slicing<br/>routing_bundle=loaded_bundle"]
  X --> Y["temp: process_by_profile<br/>validate_required_fields"]
  Y --> Z{"缺关键字段?"}
  Z -->|是| R["retry_missing_required_fields"]
  Z -->|否| F
  R --> F["final_data = process_by_profile"]
  F --> G{"word_multi_table +<br/>_table_groups?"}
  G -->|是| H["merge_internal_structured_into_word_multi_groups"]
  G -->|否| W
  H --> W["validate + 写 JSON"]
  W --> V["fill_word_table / fill_excel_* /<br/>create_excel_from_records"]
```

---

## 5. 后处理与填表（共用概念）

| 环节 | 模块要点 |
|------|----------|
| 记录格式化 | `process_by_profile` |
| 多表 Word 表格直读补缺 | `merge_internal_structured_into_word_multi_groups`（受 `A23_WORD_MULTI_MERGE_INTERNAL` 影响） |
| Word 多表写入 | `fill_word_table` 读 `_table_groups`，按 `table_index` 写入 |

---

## 相关代码

| 说明 | 路径 |
|------|------|
| 抽取核 | `src/core/extraction_service.py` |
| 路由摘要 | `src/core/extraction_routing.py` |
| 同步 HTTP | `src/api/direct_extractor.py` |
| 异步任务 | `src/api/task_manager.py` |
| CLI | `main.py` |

---

## 6. 导出与清理回路（后端对接）

- 任务导出确认：`POST /api/tasks/{task_id}/export-complete`
- 临时导出确认：`POST /api/download/temp/{filename}/export-complete`
- 对外 `output_files` 默认不暴露 `report_bundle`，后端侧仅消费业务产物（json/xlsx）。
