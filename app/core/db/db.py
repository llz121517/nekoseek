# app/core/db/db.py
"""
数据库连接管理（三库分离，线程本地连接）
- data.db：用户 / 权限组 / 邀请码 / 窗口化用量 / 设置（开外键）
- cache.db：会话
- stats.db：小时×用户用量明细
"""
import sqlite3
import threading

from app.config import DATA_DB_PATH, CACHE_DB_PATH, STATS_DB_PATH

_data_local = threading.local()
_cache_local = threading.local()
_stats_local = threading.local()


def _open_conn(path: str, foreign_keys: bool = True) -> sqlite3.Connection:
    # 用 pathlib 惰性建目录，避免模块导入期产生副作用
    path_obj = __import__("pathlib").Path(path)
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path_obj), check_same_thread=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def get_data_conn() -> sqlite3.Connection:
    """获取数据库的线程本地连接（用户/权限组/邀请码）。"""
    if not hasattr(_data_local, "conn"):
        _data_local.conn = _open_conn(str(DATA_DB_PATH), foreign_keys=True)
    return _data_local.conn


def get_cache_conn() -> sqlite3.Connection:
    """获取缓存库的线程本地连接。"""
    if not hasattr(_cache_local, "conn"):
        _cache_local.conn = _open_conn(str(CACHE_DB_PATH), foreign_keys=False)
    return _cache_local.conn


def get_stats_conn() -> sqlite3.Connection:
    """获取统计库（小时×用户用量）的线程本地连接。"""
    if not hasattr(_stats_local, "conn"):
        _stats_local.conn = _open_conn(str(STATS_DB_PATH), foreign_keys=False)
    return _stats_local.conn
