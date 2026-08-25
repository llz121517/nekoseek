# app/core/db/db.py
"""
数据库连接管理（原生 sqlite3，单文件 + thread-local + WAL）
"""
import threading
import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent
DB_DIR = ROOT / "data" / "db"
DB_PATH = DB_DIR / "nekoseek.db"

# 每线程独立连接
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """
    获取当前线程的 SQLite 连接（懒创建）。
    启用 WAL 与外键约束，row_factory 使用 sqlite3.Row。
    """
    if not hasattr(_local, "conn"):
        DB_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=True, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        _local.conn = conn
    return _local.conn
