"""
任务管理器 — 异步任务队列，供 api_server.py 使用

职责边界：
- 创建/管理任务工作目录
- 异步执行主流程（main.py 逻辑）
- 提供状态查询和日志读取接口
- 不涉及数据库存储（由后端负责）
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

STORAGE_ROOT = Path("storage/tasks")
STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


@dataclass
class TaskInfo:
    task_id: str
    status: str  # queued | running | succeeded | failed
    task_dir: Path
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["task_dir"] = str(self.task_dir)
        return d


class TaskManager:
    """异步任务管理器

    接口供 api_server.py 调用；实现细节对路由层透明。
    """

    def __init__(self):
        self._tasks: Dict[str, TaskInfo] = {}
        self._lock = threading.Lock()
        self._restore_from_disk()

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def create_task_workspace(self, template_name: str, input_files: List[str]) -> TaskInfo:
        """创建任务工作目录，返回 TaskInfo"""
        task_id = uuid.uuid4().hex[:12]
        task_dir = STORAGE_ROOT / task_id
        (task_dir / "uploads" / "template").mkdir(parents=True, exist_ok=True)
        (task_dir / "uploads" / "input").mkdir(parents=True, exist_ok=True)
        (task_dir / "output").mkdir(parents=True, exist_ok=True)

        info = TaskInfo(task_id=task_id, status="created", task_dir=task_dir)
        with self._lock:
            self._tasks[task_id] = info
        self._persist(info)
        return info

    def update_status(self, task_id: str, status: str):
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = status
                self._tasks[task_id].updated_at = time.time()
                self._persist(self._tasks[task_id])

    def start_task(self, task_id: str, template_path: Optional[Path], input_dir: Path):
        """在后台线程中运行提取流程"""
        t = threading.Thread(
            target=self._run_task,
            args=(task_id, template_path, input_dir),
            daemon=True,
        )
        t.start()

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._tasks.get(task_id)

    def list_tasks(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [t.to_dict() for t in sorted(
                self._tasks.values(), key=lambda x: x.created_at, reverse=True
            )]

    def get_output_files(self, task_id: str) -> Dict[str, str]:
        info = self._tasks.get(task_id)
        if not info:
            return {}
        output_dir = info.task_dir / "output"
        result = {}
        for f in output_dir.iterdir():
            if f.suffix == ".json" and "report_bundle" in f.name:
                result["report_bundle"] = str(f)
            elif f.suffix == ".xlsx":
                result["excel"] = str(f)
            elif f.suffix == ".json":
                result["json"] = str(f)
        return result

    def read_log(self, task_id: str, limit: int = 200) -> List[str]:
        info = self._tasks.get(task_id)
        if not info:
            return []
        # 优先读取新版日志文件名，回退到旧版
        for log_name in ("extraction.log", "task.log"):
            log_path = info.task_dir / log_name
            if log_path.exists():
                lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                return lines[-limit:]
        return []

    def delete_task(self, task_id: str) -> bool:
        """删除任务：从内存、磁盘元数据文件和任务目录中移除"""
        with self._lock:
            info = self._tasks.pop(task_id, None)
        if info is None:
            return False
        import shutil
        try:
            shutil.rmtree(info.task_dir, ignore_errors=True)
        except Exception:
            pass
        return True

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _run_task(self, task_id: str, template_path: Optional[Path], input_dir: Path):
        info = self._tasks.get(task_id)
        if not info:
            return
        log_path = info.task_dir / "extraction.log"
        output_dir = info.task_dir / "output"

        def log(msg: str):
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

        self.update_status(task_id, "running")
        log("任务开始")

        try:
            # 读取请求元数据
            meta_path = info.task_dir / "request_meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            model_type = meta.get("model_type", "")
            template_mode = meta.get("template_mode", "auto")
            template_description = meta.get("template_description", "")
            llm_mode = meta.get("llm_mode", "full")
            total_timeout = meta.get("total_timeout", 110)
            max_chunks = meta.get("max_chunks", 50)
            quiet = meta.get("quiet", False)

            # 计算输出basename：使用第一个输入文件的stem（不含扩展名）
            output_basename = ""
            saved_inputs = meta.get("saved_inputs", [])
            if saved_inputs:
                from pathlib import Path
                first_file = saved_inputs[0]
                output_basename = Path(first_file).stem

            # 构建 main.py 调用参数
            cmd = [
                sys.executable, "main.py",
                "--input-dir", str(input_dir),
                "--output-dir", str(output_dir),
                "--overwrite-output",
            ]
            if template_path:
                cmd += ["--template", str(template_path)]
            if model_type:
                cmd += ["--model-type", model_type]
            if template_mode and template_mode != "auto":
                cmd += ["--template-mode", template_mode]
            if template_description:
                cmd += ["--template-description", template_description]
            if output_basename:
                cmd += ["--output-basename", output_basename]

            # 新架构参数
            if llm_mode and llm_mode != "full":  # full是默认值
                cmd += ["--llm-mode", llm_mode]
            if total_timeout and total_timeout != 110:  # 110是默认值
                cmd += ["--total-timeout", str(total_timeout)]
            if max_chunks and max_chunks != 50:  # 50是默认值
                cmd += ["--max-chunks", str(max_chunks)]
            if quiet:  # 布尔值，默认False
                cmd += ["--quiet"]

            log(f"执行命令: {' '.join(cmd)}")

            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )

            for line in proc.stdout.splitlines():
                log(line)
            for line in proc.stderr.splitlines():
                log(f"[STDERR] {line}")

            if proc.returncode == 0:
                self.update_status(task_id, "succeeded")
                log("任务成功完成")
            else:
                self.update_status(task_id, "failed")
                log(f"任务失败，退出码: {proc.returncode}")

        except subprocess.TimeoutExpired:
            self.update_status(task_id, "failed")
            log("任务超时（>300s）")
        except Exception as e:
            self.update_status(task_id, "failed")
            log(f"任务异常: {e}")

    def _persist(self, info: TaskInfo):
        meta = info.task_dir / "task_meta.json"
        meta.write_text(
            json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _restore_from_disk(self):
        """启动时恢复已有任务状态"""
        if not STORAGE_ROOT.exists():
            return
        for task_dir in STORAGE_ROOT.iterdir():
            meta = task_dir / "task_meta.json"
            if not meta.exists():
                continue
            try:
                d = json.loads(meta.read_text(encoding="utf-8"))
                info = TaskInfo(
                    task_id=d["task_id"],
                    status=d.get("status", "unknown"),
                    task_dir=task_dir,
                    created_at=d.get("created_at", 0),
                    updated_at=d.get("updated_at", 0),
                )
                # 若任务中途崩溃（running/queued），重置为 failed
                if info.status in ("running", "queued"):
                    info.status = "failed"
                    info.updated_at = time.time()
                    # 保存更新后的状态
                    try:
                        meta.write_text(
                            json.dumps(info.to_dict(), ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                self._tasks[info.task_id] = info
            except Exception:
                pass


# 全局单例
task_manager = TaskManager()
