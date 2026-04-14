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
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from queue import Queue, Empty
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any
from src.config import TASK_RETENTION_HOURS

# 导入新的进程内任务执行器
try:
    from src.api.task_executor import get_task_executor, TaskExecutionError, NEW_ARCHITECTURE_AVAILABLE
    IN_PROCESS_EXECUTION_AVAILABLE = True
except ImportError as e:
    import logging
    logging.getLogger(__name__).warning(f"进程内任务执行器不可用: {e}")
    IN_PROCESS_EXECUTION_AVAILABLE = False
    NEW_ARCHITECTURE_AVAILABLE = False

STORAGE_ROOT = Path("storage/tasks")


def _collect_output_files(output_dir: Path) -> Dict[str, Any]:
    """扫描输出目录并返回兼容单文件/多文件的结果映射。"""
    result: Dict[str, Any] = {}
    if not output_dir.exists():
        return result

    excel_files: List[str] = []
    json_files: List[str] = []
    report_files: List[str] = []
    by_input: Dict[str, Dict[str, str]] = {}

    def _ensure_group(name: str) -> Dict[str, str]:
        grp = by_input.get(name)
        if grp is None:
            grp = {}
            by_input[name] = grp
        return grp

    for f in sorted(output_dir.iterdir(), key=lambda p: p.name.lower()):
        if not f.is_file():
            continue
        name = f.name.lower()
        path = str(f)

        if f.suffix == ".xlsx":
            excel_files.append(path)
            if name.endswith("_result.xlsx"):
                base = f.name[:-len("_result.xlsx")]
                _ensure_group(base)["excel"] = path
            continue

        if f.suffix == ".json":
            if "report_bundle" in name or name.endswith("_result_report.json"):
                report_files.append(path)
                if name.endswith("_result_report.json"):
                    base = f.name[:-len("_result_report.json")]
                    _ensure_group(base)["report_bundle"] = path
            elif name.endswith("_result.json"):
                json_files.append(path)
                base = f.name[:-len("_result.json")]
                _ensure_group(base)["json"] = path
            else:
                json_files.append(path)

    # 保持单文件字段兼容性；当存在多文件时，按排序结果选择首个文件作为默认值。
    if excel_files:
        result["excel"] = excel_files[0]
        result["result_xlsx"] = excel_files[0]
        if len(excel_files) > 1:
            result["excel_files"] = excel_files
    if json_files:
        result["json"] = json_files[0]
        result["result_json"] = json_files[0]
        if len(json_files) > 1:
            result["json_files"] = json_files
    if report_files:
        result["report_bundle"] = report_files[0]
        if len(report_files) > 1:
            result["report_bundle_files"] = report_files
    if by_input:
        result["by_input"] = by_input
        result["multi_input"] = len(by_input) > 1
    return result


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
        self._start_cleanup_thread()

    # ── 对外接口 ──────────────────────────────────────────────────────────────

    def create_task_workspace(self, template_name: str, input_files: List[str]) -> TaskInfo:
        """创建任务工作目录，返回 TaskInfo"""
        STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
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

    def get_output_files(self, task_id: str) -> Dict[str, Any]:
        info = self._tasks.get(task_id)
        if not info:
            return {}
        return _collect_output_files(info.task_dir / "output")

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

    def _start_cleanup_thread(self):
        """后台清理线程：删除超过保留时长的任务目录。

        目标：在保留期内自动回收过期任务目录，控制 storage/tasks 增长。
        """
        def _loop():
            while True:
                try:
                    retention = max(1, int(TASK_RETENTION_HOURS))
                    cutoff = time.time() - retention * 3600
                    if STORAGE_ROOT.exists():
                        for task_dir in STORAGE_ROOT.iterdir():
                            if not task_dir.is_dir():
                                continue
                            try:
                                mtime = task_dir.stat().st_mtime
                                if mtime < cutoff:
                                    tid = task_dir.name
                                    # 从内存中移除（若存在）
                                    with self._lock:
                                        self._tasks.pop(tid, None)
                                    import shutil
                                    shutil.rmtree(task_dir, ignore_errors=True)
                            except Exception:
                                pass
                except Exception:
                    pass
                time.sleep(3600)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

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
            instruction = meta.get("note", "")  # 网页端传入的抽取指令
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
            multi_input_mode = len(saved_inputs) > 1

            def _build_main_cmd(input_target: Path, out_basename: str) -> List[str]:
                cmd = [
                    sys.executable, "main.py",
                    "--input-dir", str(input_target),
                    "--output-dir", str(output_dir),
                    "--overwrite-output",
                ]
                if template_path:
                    cmd += ["--template", str(template_path)]
                if template_mode and template_mode != "auto":
                    cmd += ["--template-mode", template_mode]
                if template_description:
                    cmd += ["--template-description", template_description]
                if instruction:
                    cmd += ["--instruction", instruction]
                if out_basename:
                    cmd += ["--output-basename", out_basename]

                if llm_mode and llm_mode != "full":
                    cmd += ["--llm-mode", llm_mode]
                if total_timeout and total_timeout != 110:
                    cmd += ["--total-timeout", str(total_timeout)]
                if max_chunks and max_chunks != 50:
                    cmd += ["--max-chunks", str(max_chunks)]
                if quiet:
                    cmd += ["--quiet"]
                return cmd

            def _run_subprocess_and_stream(cmd: List[str], env: Dict[str, str], timeout_seconds: float) -> tuple[int, bool]:
                log(f"执行命令: {' '.join(cmd)}")
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                )

                q: Queue = Queue()

                def _reader(pipe, prefix: str, q: Queue):
                    try:
                        for raw in iter(pipe.readline, ""):
                            if raw is None:
                                break
                            line = raw.rstrip("\r\n")
                            if line:
                                q.put((prefix, line))
                    except Exception:
                        pass
                    finally:
                        try:
                            pipe.close()
                        except Exception:
                            pass

                t_out = threading.Thread(target=_reader, args=(proc.stdout, "", q), daemon=True)
                t_err = threading.Thread(target=_reader, args=(proc.stderr, "[STDERR] ", q), daemon=True)
                t_out.start()
                t_err.start()

                start = time.time()
                timed_out = False
                try:
                    while True:
                        drained = 0
                        while True:
                            try:
                                prefix, line = q.get_nowait()
                                log(f"{prefix}{line}")
                                drained += 1
                            except Empty:
                                break

                        rc = proc.poll()
                        if rc is not None:
                            for _ in range(2000):
                                try:
                                    prefix, line = q.get_nowait()
                                    log(f"{prefix}{line}")
                                except Empty:
                                    break
                            return rc, timed_out

                        if (time.time() - start) > timeout_seconds:
                            timed_out = True
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            return -9, timed_out

                        if drained == 0:
                            time.sleep(0.2)
                finally:
                    try:
                        if proc.poll() is None:
                            proc.kill()
                    except Exception:
                        pass

            # 检查是否可以使用进程内执行器
            if IN_PROCESS_EXECUTION_AVAILABLE and NEW_ARCHITECTURE_AVAILABLE and not multi_input_mode:
                log("使用进程内任务执行器")

                # 构建任务配置
                task_config = {
                    "input_dir": str(input_dir),
                    "output_dir": str(output_dir),
                    "template_path": str(template_path) if template_path else None,
                    "model_type": model_type,
                    "template_mode": template_mode,
                    "template_description": template_description,
                    "llm_mode": llm_mode,
                    "total_timeout": total_timeout,
                    "max_chunks": max_chunks,
                    "quiet": quiet,
                    "output_basename": output_basename
                }

                # 使用进程内执行器
                executor = get_task_executor()
                # 创建日志回调函数
                def log_callback(msg: str):
                    log(msg)

                # 执行任务
                future = executor.execute_task(task_id, task_config, log_callback)

                try:
                    # 等待任务完成（阻塞当前线程，但这是后台线程，所以没问题）
                    # 设置超时时间，给一些额外缓冲
                    timeout_seconds = total_timeout + 120
                    result = future.result(timeout=timeout_seconds)
                    if result.get("status") == "success":
                        self.update_status(task_id, "succeeded")
                        log(f"任务成功完成，模式: {result.get('mode', 'unknown')}，耗时: {result.get('elapsed_time', 0):.1f}秒")
                    else:
                        self.update_status(task_id, "failed")
                        log(f"任务失败: {result.get('error', '未知错误')}")

                except Exception as e:
                    # 捕获所有异常，包括concurrent.futures.TimeoutError
                    self.update_status(task_id, "failed")
                    if "timeout" in str(e).lower() or isinstance(e, TimeoutError):
                        log("任务超时")
                    else:
                        log(f"任务执行异常: {e}")
                    # 尝试取消任务
                    try:
                        executor.cancel_task(task_id)
                    except:
                        pass
            else:
                # 当进程内执行不可用时，回退到子进程执行路径。
                if multi_input_mode:
                    log("检测到多输入文件：启用同模板逐文件顺序执行（每个文件独立抽取与输出）")
                elif not IN_PROCESS_EXECUTION_AVAILABLE:
                    log("进程内执行器不可用，回退到子进程执行")
                elif not NEW_ARCHITECTURE_AVAILABLE:
                    log("新架构组件不可用，回退到子进程执行")
                else:
                    log("回退到子进程执行")

                # 通过环境变量传递模型类型（main.py 不支持 --model-type 参数）
                env = os.environ.copy()
                if model_type:
                    env["A23_MODEL_TYPE"] = str(model_type).strip()
                # 子进程 stdout/stderr 行缓冲：尽快写入 extraction.log，便于网页端/排查
                env.setdefault("PYTHONUNBUFFERED", "1")

                # 外层 watchdog：必须明显大于 main.py 的 --total-timeout（本地大文档+LLM 可能较慢）
                to = float(total_timeout or 110)
                timeout_seconds = to + 300.0
                if multi_input_mode:
                    for idx, fname in enumerate(saved_inputs, start=1):
                        input_target = input_dir / fname
                        if not input_target.exists():
                            self.update_status(task_id, "failed")
                            log(f"任务失败：输入文件不存在 {input_target}")
                            return
                        out_basename = Path(fname).stem
                        log(f"[{idx}/{len(saved_inputs)}] 开始处理：{fname}")
                        cmd = _build_main_cmd(input_target, out_basename)
                        rc, timed_out = _run_subprocess_and_stream(cmd, env, timeout_seconds)
                        if rc != 0:
                            self.update_status(task_id, "failed")
                            if timed_out:
                                log(f"[{idx}/{len(saved_inputs)}] 处理超时（>{int(timeout_seconds)}s），已终止")
                            else:
                                log(f"[{idx}/{len(saved_inputs)}] 处理失败，退出码: {rc}")
                            return
                    self.update_status(task_id, "succeeded")
                    log("任务成功完成（多输入逐文件执行）")
                else:
                    cmd = _build_main_cmd(input_dir, output_basename)
                    rc, timed_out = _run_subprocess_and_stream(cmd, env, timeout_seconds)
                    if rc == 0:
                        self.update_status(task_id, "succeeded")
                        log("任务成功完成")
                    else:
                        self.update_status(task_id, "failed")
                        if timed_out:
                            log(f"任务超时（>{int(timeout_seconds)}s），已终止子进程")
                        else:
                            log(f"任务失败，退出码: {rc}")

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
