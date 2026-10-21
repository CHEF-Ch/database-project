"""数据访问层：全应用唯一与 SQL Server 直接交互的地方。

② 骨架阶段提供：
  · 配置驱动的连接串（trusted / sql 两种认证方式）
  · 连接、游标、事务提交/回滚、资源释放的统一入口
  · `/health` 用的结构指纹探针（只读）

③ 阶段在此基础上补齐各表 DAO 方法，全部使用参数化 SQL（禁止拼接）。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

import pyodbc

from app.config import Config
from app.errors import AppError, ErrCode, classify_db_error

logger = logging.getLogger(__name__)

# v1.0 目标结构指纹（第六周声明：111 列 + 17 主码 + 3 UQ + 4 筛选唯一索引 +
# 25 外码 + 60 CHECK + 16 DEFAULT = 236）
V1_FINGERPRINT = 236

_FINGERPRINT_SQL = """
SELECT
      (SELECT COUNT(*) FROM sys.columns c
         JOIN sys.tables t ON c.object_id = t.object_id)            AS columns
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK')   AS pk
    , (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'UQ')   AS uq
    , (SELECT COUNT(*) FROM sys.indexes i
         WHERE i.is_unique = 1 AND i.is_primary_key = 0
           AND i.is_unique_constraint = 0 AND i.is_hypothetical = 0
           AND i.object_id IN (SELECT object_id FROM sys.tables))   AS uq_filtered
    , (SELECT COUNT(*) FROM sys.foreign_keys)                       AS fk
    , (SELECT COUNT(*) FROM sys.check_constraints)                   AS ck
    , (SELECT COUNT(*) FROM sys.default_constraints)                 AS df
"""


def build_connection_string(role: str | None) -> str:
    """按认证方式构造连接串。

    role 为 None 表示"系统级只读探针"（如 /health）：trusted 模式用当前
    Windows 账户；sql 模式借用 manager 登录。
    """
    base = (
        f"DRIVER={{{Config.DB_DRIVER}}};"
        f"SERVER={Config.DB_SERVER};"
        f"DATABASE={Config.DB_NAME};"
        "Encrypt=yes;TrustServerCertificate=yes;"
        f"Connection Timeout={Config.DB_TIMEOUT};"
    )
    if Config.DB_AUTH == "trusted":
        return base + "Trusted_Connection=yes;"

    login = Config.login_for(role or "manager")
    password = Config.password_for(login)
    if not password:
        raise AppError(
            ErrCode.DB_UNAVAILABLE,
            detail=f"缺少环境变量 DB_PWD_{login.upper()}（DB_AUTH=sql 时必须设置）",
        )
    return base + f"UID={login};PWD={password};"


def connect(role: str | None = None) -> pyodbc.Connection:
    try:
        conn = pyodbc.connect(
            build_connection_string(role),
            autocommit=False,
            timeout=Config.DB_TIMEOUT,
        )
        # 锁等待/查询超时：避免一个长事务把 Flask 工作线程无限挂住。
        conn.timeout = Config.DB_QUERY_TIMEOUT
        # XACT_ABORT ON：语句级错误直接 doom 事务，防止“吞异常后继续提交”。
        with conn.cursor() as cur:
            cur.execute("SET XACT_ABORT ON")
        return conn
    except pyodbc.Error as exc:            # 连接失败：统一转友好提示，细节只进日志
        raise classify_db_error(exc) from exc


@contextmanager
def db_cursor(role: str | None = None, *, commit: bool = False) -> Iterator[pyodbc.Cursor]:
    """统一管理连接 / 游标 / 事务 / 释放。

    用法（③ 起所有 DAO 都走这里）::

        with db_cursor("app", commit=True) as cur:
            cur.execute("INSERT ... VALUES (?, ?)", a, b)

    - commit=True：成功提交；
    - commit=False：成功也显式回滚（只读/自检用），不要用它做写操作；
    - 任何异常一律回滚；游标与连接必定关闭。
    """
    conn = connect(role)
    cur = None
    try:
        cur = conn.cursor()
        yield cur
        if commit:
            try:
                conn.commit()
            except pyodbc.Error as exc:
                raise AppError(ErrCode.TX_FAILED, detail=str(exc)) from exc
        else:
            # 显式回滚：不依赖 ODBC 断开时的隐式行为。
            try:
                conn.rollback()
            except pyodbc.Error:
                logger.exception("db_cursor: 显式回滚失败（连接可能已断）")
    except BaseException as exc:
        try:
            conn.rollback()
        except pyodbc.Error:              # 连接已断时回滚会再报错；留痕而不是静默
            logger.exception("db_cursor: 异常路径回滚失败（连接可能已断）")
        if isinstance(exc, pyodbc.Error):
            raise classify_db_error(exc) from exc
        raise
    finally:
        try:
            if cur is not None:
                cur.close()
        finally:
            conn.close()


def probe_database() -> dict:
    """只读探针：返回库名、版本与结构指纹（不返回任何连接信息）。"""
    with db_cursor(None) as cur:
        cur.execute(
            "SELECT DB_NAME() AS database_name, "
            "CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)) AS product_version"
        )
        meta = cur.fetchone()

        cur.execute(_FINGERPRINT_SQL)
        row = cur.fetchone()

    parts = {
        "columns": int(row.columns),
        "pk": int(row.pk),
        "uq": int(row.uq),
        "uq_filtered": int(row.uq_filtered),
        "fk": int(row.fk),
        "check": int(row.ck),
        "default": int(row.df),
    }
    total = sum(parts.values())
    return {
        "ok": True,
        "database": meta.database_name,
        "server_version": meta.product_version,
        "auth_mode": Config.DB_AUTH,
        "fingerprint": {
            "total": total,
            "expected_v1_0": V1_FINGERPRINT,
            "match": total == V1_FINGERPRINT,
            **parts,
        },
    }
