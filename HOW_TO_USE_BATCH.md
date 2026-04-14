# 批量测试使用说明（新架构版）

## 新架构核心参数
- `--llm-mode {full|off}`: LLM抽取模式（`supplement` 兼容映射到 `full`）
  - `full`: 始终全文抽取（默认，Docling语义分块 + LLM）
  - `supplement`: 兼容别名，等价于 `full`
  - `off`: 仅规则抽取（替代旧的`--use-rules-only`）
- `--total-timeout N`: 总超时时间（秒），默认110秒
- `--max-chunks N`: 最大语义分块数量，默认50
- `--quiet`: 安静模式，禁用控制台进度输出
- `--template-mode {auto|file|llm}`: 模板模式
  - `auto`: 自动选择（默认）
  - `file`: 使用上传的模板文件
  - `llm`: 仅用LLM指令生成模板（需配合`--template-description`）

## 单个基准任务示例
### 完整AI抽取模式（默认）
```bash
python main.py \
  --template "test/assets/模板/03_城市经济百强任务_模板.xlsx" \
  --input-dir "test/assets/任务输入/03_城市经济百强任务" \
  --output-dir "test/results/outputs/03_城市经济百强任务" \
  --llm-mode full \
  --total-timeout 180 \
  --max-chunks 100 \
  --overwrite-output
```

### 纯规则抽取模式
```bash
python main.py \
  --template "test/assets/模板/03_城市经济百强任务_模板.xlsx" \
  --input-dir "test/assets/任务输入/03_城市经济百强任务" \
  --output-dir "test/results/outputs/03_城市经济百强任务" \
  --llm-mode off \
  --overwrite-output
```

### 无模板抽取模式（仅用LLM指令）
```bash
python main.py \
  --input-dir "test/assets/任务输入/03_城市经济百强任务" \
  --output-dir "test/results/outputs/03_城市经济百强任务" \
  --template-mode llm \
  --template-description "提取城市名称、GDP、人口、面积字段" \
  --llm-mode full \
  --overwrite-output
```

## 批量跑全部基准任务
```bash
python scripts/run_batch.py \
  --manifest "test/assets/清单/基准任务清单.json" \
  --main-script main.py \
  --validate \
  --collect-metrics \
  --output-report test/reports/benchmark_report.json
```

## 兼容性参数说明
- `--slice-size` 和 `--overlap`: 作为兼容参数保留，仅在无语义分块时使用
- `--use-rules-only` 和 `--use-unit-aware`: 已废弃，使用 `--llm-mode off` 替代

## 环境变量配置
批量测试前，请确保正确配置环境变量（`.env`文件）：
```bash
# 模型配置（选择一种）
A23_MODEL_TYPE=ollama          # 或 deepseek、openai、qwen
A23_OLLAMA_MODEL=qwen2.5:7b    # Ollama模型名称

# 新架构配置
A23_FUZZY_THRESHOLD=75         # 字段别名模糊匹配阈值
A23_NORMALIZATION_CONFIG=src/knowledge/field_normalization_rules.json  # 字段归一化规则
A23_TARGET_LIMIT_SECONDS=40    # 单次模型调用超时
```
