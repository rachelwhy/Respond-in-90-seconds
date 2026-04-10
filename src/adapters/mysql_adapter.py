"""
MySQL 适配器

两张核心表：
  a23_structured_records  — 可结构化的数据（表格、模板提取结果）
  a23_raw_documents       — 难以统一的文本类型（纯文本、扫描件等）

连接参数从 src/config.py 读取，通过 .env 覆盖：
  A23_MYSQL_HOST / A23_MYSQL_PORT / A23_MYSQL_USER / A23_MYSQL_PASSWORD / A23_MYSQL_DATABASE
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── DDL ─────────────────────────────────────────────────────────────────────

_DDL_STRUCTURED = """
CREATE TABLE IF NOT EXISTS a23_structured_records (
    id            INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id       VARCHAR(64)  NOT NULL COMMENT '任务ID',
    source_file   VARCHAR(512) NOT NULL COMMENT '来源文件名',
    template_name VARCHAR(255) NOT NULL DEFAULT '' COMMENT '模板名称',
    record_index  INT          NOT NULL DEFAULT 0 COMMENT '记录在批次中的序号',
    record_data   JSON         NOT NULL COMMENT '字段键值对',
    created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task (task_id),
    INDEX idx_source (source_file(128))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='结构化抽取结果';
"""

_DDL_RAW = """
CREATE TABLE IF NOT EXISTS a23_raw_documents (
    id          INT          NOT NULL AUTO_INCREMENT PRIMARY KEY,
    task_id     VARCHAR(64)  NOT NULL COMMENT '任务ID',
    source_file VARCHAR(512) NOT NULL COMMENT '来源文件名',
    file_type   VARCHAR(50)  NOT NULL DEFAULT '' COMMENT '文件扩展名',
    content     LONGTEXT     NOT NULL COMMENT '文档全文',
    metadata    JSON         NOT NULL COMMENT '元数据（页数、字数等）',
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task (task_id),
    INDEX idx_source (source_file(128))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='待处理的非结构化文档';
"""


class MysqlAdapter:
    """轻量级 MySQL 适配器（pymysql，无 ORM）"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 3306,
        user: str = "root",
        password: str = "",
        database: str = "a23",
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._pymysql = None
        self._check_import()

    def _check_import(self):
        try:
            import pymysql  # noqa: F401
            self._pymysql = pymysql
        except ImportError:
            logger.warning("pymysql 未安装，MySQL 入库功能不可用。运行 pip install pymysql 启用。")

    # ── 连接 ─────────────────────────────────────────────────────────────────

    def get_connection(self):
        """获取一个新连接（调用方负责关闭）"""
        if self._pymysql is None:
            raise RuntimeError("pymysql 未安装")
        return self._pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            autocommit=False,
        )

    def is_available(self) -> bool:
        """测试连接是否可用"""
        if self._pymysql is None:
            return False
        try:
            conn = self.get_connection()
            conn.close()
            return True
        except Exception as e:
            logger.debug(f"MySQL 连接测试失败: {e}")
            return False

    # ── DDL ──────────────────────────────────────────────────────────────────

    def ensure_tables(self):
        """若表不存在则创建（幂等）"""
        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(_DDL_STRUCTURED)
                cur.execute(_DDL_RAW)
            conn.commit()
            logger.info("MySQL 表结构就绪")
        finally:
            conn.close()

    # ── 写入 ─────────────────────────────────────────────────────────────────

    def insert_structured_records(
        self,
        task_id: str,
        source_file: str,
        records: List[Dict[str, Any]],
        template_name: str = "",
    ) -> int:
        """批量插入结构化记录，返回插入行数"""
        if not records:
            return 0

        sql = (
            "INSERT INTO a23_structured_records "
            "(task_id, source_file, template_name, record_index, record_data) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        rows = [
            (task_id, source_file, template_name, idx, json.dumps(rec, ensure_ascii=False))
            for idx, rec in enumerate(records)
        ]

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.executemany(sql, rows)
            conn.commit()
            logger.info(f"结构化入库: task={task_id} file={source_file} 共 {len(rows)} 条")
            return len(rows)
        except Exception as e:
            conn.rollback()
            logger.error(f"结构化入库失败: {e}")
            raise
        finally:
            conn.close()

    def insert_raw_document(
        self,
        task_id: str,
        source_file: str,
        content: str,
        file_type: str = "",
        metadata: Optional[Dict] = None,
    ) -> int:
        """插入一条非结构化文档，返回新行的 id"""
        sql = (
            "INSERT INTO a23_raw_documents "
            "(task_id, source_file, file_type, content, metadata) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        conn = self.get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (task_id, source_file, file_type, content, meta_json))
                new_id = cur.lastrowid
            conn.commit()
            logger.info(f"非结构化入库: task={task_id} file={source_file} id={new_id}")
            return new_id
        except Exception as e:
            conn.rollback()
            logger.error(f"非结构化入库失败: {e}")
            raise
        finally:
            conn.close()

    # ── 查询（供调试 / 后端接口复用） ─────────────────────────────────────────

    def query_structured(
        self, task_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        sql = (
            "SELECT id, source_file, template_name, record_index, record_data, created_at "
            "FROM a23_structured_records WHERE task_id=%s ORDER BY id LIMIT %s"
        )
        conn = self.get_connection()
        try:
            with conn.cursor(self._pymysql.cursors.DictCursor) as cur:
                cur.execute(sql, (task_id, limit))
                rows = cur.fetchall()
            # record_data 反序列化
            for row in rows:
                if isinstance(row.get("record_data"), str):
                    try:
                        row["record_data"] = json.loads(row["record_data"])
                    except Exception:
                        pass
                if row.get("created_at"):
                    row["created_at"] = str(row["created_at"])
            return rows
        finally:
            conn.close()

    def query_raw(self, task_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        sql = (
            "SELECT id, source_file, file_type, "
            "LEFT(content, 500) AS content_preview, metadata, created_at "
            "FROM a23_raw_documents WHERE task_id=%s ORDER BY id LIMIT %s"
        )
        conn = self.get_connection()
        try:
            with conn.cursor(self._pymysql.cursors.DictCursor) as cur:
                cur.execute(sql, (task_id, limit))
                rows = cur.fetchall()
            for row in rows:
                if isinstance(row.get("metadata"), str):
                    try:
                        row["metadata"] = json.loads(row["metadata"])
                    except Exception:
                        pass
                if row.get("created_at"):
                    row["created_at"] = str(row["created_at"])
            return rows
        finally:
            conn.close()


# ── 全局单例（延迟初始化） ────────────────────────────────────────────────────

_adapter: Optional[MysqlAdapter] = None


def get_mysql_adapter() -> MysqlAdapter:
    global _adapter
    if _adapter is None:
        from src.config import (
            MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
        )
        _adapter = MysqlAdapter(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE,
        )
    return _adapter
