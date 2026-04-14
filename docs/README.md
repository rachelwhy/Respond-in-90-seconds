# 文档索引

| 文档 | 说明 |
|------|------|
| [../README.md](../README.md) | 项目总览、安装与 CLI |
| [../HTTP_API_USAGE.md](../HTTP_API_USAGE.md) | **生产环境**：FastAPI 接口、环境变量 |
| [BACKEND_HANDOFF.md](BACKEND_HANDOFF.md) | 后端对接与交付清单（下载、清理、输出约定） |
| [../A23_TECHNICAL_FLOW.md](../A23_TECHNICAL_FLOW.md) | 技术流程与模块关系 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | 上线形态说明（网页/API 为主）与抽取路由 |
| [RUNTIME_FLOW.md](RUNTIME_FLOW.md) | **全流程与分支**（Mermaid：入口、`extract_with_slicing`、`direct_extract`、CLI 后处理） |
| [ALGORITHM_FULL_CHAIN.md](ALGORITHM_FULL_CHAIN.md) | 算法层全链路文字说明（可直接用于 Mermaid 建模） |

命令行 `main.py` 与 `scripts/` 下的脚本用于本地调试与批测，**不作为内网网页后端的运行方式**；异步任务见 `src/api/task_manager.py`（子进程仍调用 `main.py` 以保持与历史结果一致）。
