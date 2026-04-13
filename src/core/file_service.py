"""
文件处理服务模块 - 处理文件路径和目录操作

从main.py提取的文件处理函数：
1. ensure_parent_dir: 确保文件父目录存在
2. normalize_input_path: 标准化输入路径（文件转目录）

设计原则：
1. 纯函数：无副作用，可测试
2. 错误处理：明确的异常和错误信息
3. 跨平台：兼容Windows和Linux路径
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

import logging

logger = logging.getLogger(__name__)


def _is_debug_enabled() -> bool:
    v = os.environ.get("A23_DEBUG")
    return str(v).strip().lower() in ("1", "true", "yes", "on", "y")


_DEBUG = _is_debug_enabled()


def ensure_parent_dir(path_str: str) -> None:
    """确保文件父目录存在

    Args:
        path_str: 文件路径

    Raises:
        ValueError: 如果路径为空字符串
    """
    if not path_str:
        raise ValueError("路径不能为空")

    parent = os.path.dirname(path_str)
    if parent:
        os.makedirs(parent, exist_ok=True)


def normalize_input_path(path: str) -> str:
    """标准化输入路径：如果路径是文件，创建临时目录并复制文件；如果是目录，直接返回

    Args:
        path: 输入路径（文件或目录）

    Returns:
        目录路径（确保是目录）

    Raises:
        FileNotFoundError: 如果路径不存在
        ValueError: 如果路径既不是文件也不是目录
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f'路径不存在: {path}')

    if os.path.isfile(path):
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix='a23_input_')
        if _DEBUG:
            logger.info("输入为文件，创建临时目录: %s", temp_dir)

        # 复制文件到临时目录
        file_name = os.path.basename(path)
        dest_path = os.path.join(temp_dir, file_name)
        shutil.copy2(path, dest_path)
        if _DEBUG:
            logger.info("文件已复制到临时目录: %s", dest_path)

        return temp_dir
    elif os.path.isdir(path):
        return path
    else:
        raise ValueError(f'路径既不是文件也不是目录: {path}')


def get_output_file_paths(output_dir: str, base_name: str, template_mode: str) -> Dict[str, str]:
    """根据输出目录、基础名称和模板模式生成输出文件路径

    Args:
        output_dir: 输出目录
        base_name: 基础文件名（不含扩展名）
        template_mode: 模板模式，如 'excel_table', 'word_table', 'vertical', 'generic'

    Returns:
        包含各种输出文件路径的字典，键包括: json, xlsx, docx, report_json
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    output_json = output_dir / f'{base_name}_result.json'
    output_xlsx = output_dir / f'{base_name}_result.xlsx'
    output_docx = output_dir / f'{base_name}_result.docx'
    output_report_bundle_json = output_dir / f'{base_name}_result_report.json'

    result = {
        "json": str(output_json),
        "xlsx": str(output_xlsx),
        "docx": str(output_docx),
        "report_json": str(output_report_bundle_json)
    }

    # 根据模板模式标记哪些文件会实际生成
    result["generate_xlsx"] = template_mode in ['vertical', 'excel_table', 'generic']
    result["generate_docx"] = template_mode == 'word_table'

    return result


def ensure_output_dir_empty(output_dir: str, overwrite: bool = False) -> bool:
    """确保输出目录为空或可覆盖

    Args:
        output_dir: 输出目录路径
        overwrite: 是否允许覆盖

    Returns:
        True: 目录可用；False: 目录不可用（需要用户确认）

    Raises:
        ValueError: 如果目录非空且不允许覆盖
    """
    if not os.path.exists(output_dir):
        return True

    if not os.listdir(output_dir):
        return True

    if overwrite:
        logger = logging.getLogger(__name__)
        logger.warning("输出目录非空: %s，使用 --overwrite-output 参数覆盖", output_dir)
        return True
    else:
        raise ValueError(f'输出目录非空：{output_dir}。请使用 --overwrite-output 参数覆盖，或选择其他目录。')


def create_temp_dir(prefix: str = 'a23_temp_') -> str:
    """创建临时目录

    Args:
        prefix: 目录名前缀

    Returns:
        临时目录路径
    """
    return tempfile.mkdtemp(prefix=prefix)


def copy_file_to_dir(src_file: str, dest_dir: str, new_name: Optional[str] = None) -> str:
    """复制文件到目录

    Args:
        src_file: 源文件路径
        dest_dir: 目标目录
        new_name: 新文件名（可选，默认为原文件名）

    Returns:
        目标文件路径
    """
    if not os.path.exists(src_file):
        raise FileNotFoundError(f'源文件不存在: {src_file}')

    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)

    file_name = new_name or os.path.basename(src_file)
    dest_path = os.path.join(dest_dir, file_name)
    shutil.copy2(src_file, dest_path)

    return dest_path


def get_file_extension(file_path: str) -> str:
    """获取文件扩展名（小写，不含点）

    Args:
        file_path: 文件路径

    Returns:
        文件扩展名，如 'xlsx', 'docx', 'txt'
    """
    return Path(file_path).suffix.lower().lstrip('.')


def is_excel_file(file_path: str) -> bool:
    """判断是否为Excel文件

    Args:
        file_path: 文件路径

    Returns:
        是否为Excel文件
    """
    ext = get_file_extension(file_path)
    return ext in ('xls', 'xlsx', 'xlsm')


def is_word_file(file_path: str) -> bool:
    """判断是否为Word文件

    Args:
        file_path: 文件路径

    Returns:
        是否为Word文件
    """
    ext = get_file_extension(file_path)
    return ext in ('doc', 'docx')


def is_text_file(file_path: str) -> bool:
    """判断是否为文本文件

    Args:
        file_path: 文件路径

    Returns:
        是否为文本文件
    """
    ext = get_file_extension(file_path)
    return ext in ('txt', 'md', 'json', 'csv')


def get_file_info(file_path: str) -> Dict[str, Any]:
    """获取文件信息

    Args:
        file_path: 文件路径

    Returns:
        文件信息字典，包含路径、大小、修改时间、扩展名等
    """
    path = Path(file_path)
    stat = path.stat() if path.exists() else None

    return {
        "path": str(path),
        "name": path.name,
        "stem": path.stem,
        "extension": get_file_extension(file_path),
        "size_bytes": stat.st_size if stat else 0,
        "modified_time": stat.st_mtime if stat else 0,
        "exists": path.exists(),
        "is_file": path.is_file(),
        "is_dir": path.is_dir(),
    }


class FileService:
    """文件服务类（提供面向对象接口）"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """初始化文件服务

        Args:
            config: 配置字典
        """
        self.config = config or {}
        self.temp_dirs = []

    def ensure_parent_dir(self, path_str: str) -> None:
        """确保文件父目录存在"""
        ensure_parent_dir(path_str)

    def normalize_input_path(self, path: str) -> str:
        """标准化输入路径"""
        return normalize_input_path(path)

    def create_temp_dir(self, prefix: str = 'a23_temp_') -> str:
        """创建临时目录并记录（用于后续清理）"""
        temp_dir = create_temp_dir(prefix)
        self.temp_dirs.append(temp_dir)
        return temp_dir

    def cleanup_temp_dirs(self) -> None:
        """清理所有创建的临时目录"""
        for temp_dir in self.temp_dirs:
            try:
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir)
                    if _DEBUG:
                        logger.info("清理临时目录: %s", temp_dir)
            except Exception as e:
                logging.getLogger(__name__).warning("清理临时目录失败 %s: %s", temp_dir, e)
        self.temp_dirs.clear()

    def get_output_paths(self, output_dir: str, base_name: str, template_mode: str) -> Dict[str, str]:
        """获取输出文件路径"""
        return get_output_file_paths(output_dir, base_name, template_mode)

    def ensure_output_dir_ready(self, output_dir: str, overwrite: bool = False) -> bool:
        """确保输出目录准备就绪"""
        return ensure_output_dir_empty(output_dir, overwrite)

    def __enter__(self):
        """上下文管理器入口"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口（自动清理临时目录）"""
        self.cleanup_temp_dirs()


# 便捷函数（用于向后兼容）
def get_file_service(config: Optional[Dict[str, Any]] = None) -> FileService:
    """获取文件服务实例

    Args:
        config: 配置字典

    Returns:
        FileService实例
    """
    return FileService(config)