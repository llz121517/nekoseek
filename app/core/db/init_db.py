# app/core/db/init_db.py
"""
数据库初始化：建表 + 播种默认权限组与管理员
"""
import sqlite3
from pathlib import Path

from app.core.db.db import DB_DIR, DB_PATH
from app.core.security import hash_password
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD

SQL_LIST = [
    # 权限组
    """
    CREATE TABLE IF NOT EXISTS groups (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        is_admin    INTEGER NOT NULL DEFAULT 0,
        quota_limit INTEGER NOT NULL DEFAULT 0,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
    # 用户（租户账户）
    """
    CREATE TABLE IF NOT EXISTS users (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        username       TEXT NOT NULL UNIQUE,
        pwd_hash       TEXT NOT NULL,
        salt           TEXT NOT NULL,
        group_id       INTEGER NOT NULL,
        quota_override INTEGER,
        status         INTEGER NOT NULL DEFAULT 1,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (group_id) REFERENCES groups(id)
    );
    CREATE INDEX IF NOT EXISTS idx_users_group ON users(group_id);
    """,
    # 邀请码
    """
    CREATE TABLE IF NOT EXISTS invite_codes (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT NOT NULL UNIQUE,
        group_id    INTEGER NOT NULL,
        created_by  INTEGER,
        max_uses    INTEGER NOT NULL DEFAULT 1,
        used_count  INTEGER NOT NULL DEFAULT 0,
        expires_at  TEXT,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (group_id) REFERENCES groups(id)
    );
    CREATE INDEX IF NOT EXISTS idx_invite_code ON invite_codes(code);
    """,
    # 登录态
    """
    CREATE TABLE IF NOT EXISTS sessions (
        sid       TEXT PRIMARY KEY,
        user_id   INTEGER NOT NULL,
        expire_ts REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_expire ON sessions(expire_ts);
    CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
    """,
    # 单用户粗略分词记账（按 user+date 聚合）
    """
    CREATE TABLE IF NOT EXISTS usage_records (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         INTEGER NOT NULL,
        date            TEXT NOT NULL,
        input_tokens    INTEGER NOT NULL DEFAULT 0,
        output_tokens   INTEGER NOT NULL DEFAULT 0,
        total_tokens    INTEGER NOT NULL DEFAULT 0,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (user_id, date)
    );
    """,
    # 全局池真实 usage 记账（按 date 聚合）
    """
    CREATE TABLE IF NOT EXISTS pool_usage (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        date            TEXT NOT NULL UNIQUE,
        input_tokens    INTEGER NOT NULL DEFAULT 0,
        output_tokens   INTEGER NOT NULL DEFAULT 0,
        total_tokens    INTEGER NOT NULL DEFAULT 0,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """,
]


def init_db() -> None:
    """
    初始化数据库：建文件夹 + 建表 + 播种默认组与管理员。
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        for sql in SQL_LIST:
            conn.executescript(sql)

        # 播种默认权限组
        cur = conn.execute("SELECT COUNT(*) FROM groups")
        if cur.fetchone()[0] == 0:
            conn.execute(
                "INSERT INTO groups (name, is_admin, quota_limit) VALUES (?, ?, ?)",
                ("admin", 1, 0),
            )
            conn.execute(
                "INSERT INTO groups (name, is_admin, quota_limit) VALUES (?, ?, ?)",
                ("user", 0, 0),
            )

        # 仅当无用户时，从 .env 播种初始管理员
        cur = conn.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            if not ADMIN_PASSWORD:
                raise ValueError(
                    "ADMIN_PASSWORD is empty! Please set it in .env for first-time bootstrap."
                )
            admin_group = conn.execute(
                "SELECT id FROM groups WHERE name = 'admin'"
            ).fetchone()
            pwd_hash, salt = hash_password(ADMIN_PASSWORD)
            conn.execute(
                "INSERT INTO users (username, pwd_hash, salt, group_id) VALUES (?, ?, ?, ?)",
                (ADMIN_USERNAME.strip(), pwd_hash, salt, admin_group["id"]),
            )
            print("Initial admin credential imported from .env into the database.")
