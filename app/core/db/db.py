# app/core/db/db.py
"""
数据库连接管理（双库分离）
- nekoseek.db：用户 / 权限组 / 邀请码
- cache.db：会话
"""
import sqlite3
import threading

from app.config import DATA_DB_PATH, CACHE_DB_PATH

_data_local = threading.local()
_cache_local = threading.local()


def _open_conn(path: str, foreign_keys: bool = True) -> sqlite3.Connection:
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
