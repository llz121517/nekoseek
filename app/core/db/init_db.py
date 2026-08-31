# app/core/db/init_db.py
"""
数据库初始化：三库建表（data/cache/stats）+ 播种默认权限组与初始管理员
"""
import sqlite3

from app.config import (
    ADMIN_USERNAME, ADMIN_PASSWORD, DATA_DB_PATH, CACHE_DB_PATH, DB_DIR,
    QUOTA_WINDOW, GLOBAL_QUOTA_LIMIT,
)
from app.core.db.db import get_data_conn, get_cache_conn, get_stats_conn
from app.core.security import hash_password

USER_SQL = """
CREATE TABLE IF NOT EXISTS groups (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    is_admin    INTEGER NOT NULL DEFAULT 0,
    quota_limit INTEGER NOT NULL DEFAULT 0,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS invite_code_uses (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    invite_code_id INTEGER NOT NULL,
    user_id        INTEGER NOT NULL,
    used_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invite_code_id) REFERENCES invite_codes(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_invite_code_uses_code ON invite_code_uses(invite_code_id);
CREATE INDEX IF NOT EXISTS idx_invite_code_uses_user ON invite_code_uses(user_id);

-- 窗口化用量记账：user_id=0 表示全局池聚合行
CREATE TABLE IF NOT EXISTS usage_records (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    window_start  INTEGER NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, window_start)
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id, window_start);

-- 运行时可变设置（quota_window / global_quota_limit 等）
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 操作日志（审计）：记录后台管理操作与登录/登出等关键事件
CREATE TABLE IF NOT EXISTS op_logs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       INTEGER NOT NULL,             -- epoch 秒
    level    TEXT NOT NULL DEFAULT 'info', -- info / warning / error
    username TEXT NOT NULL DEFAULT '',     -- 操作者（未登录/系统时为空串）
    action   TEXT NOT NULL,                -- 操作标识，如 user.update / dsh.start
    detail   TEXT NOT NULL DEFAULT '',     -- 细节描述
    ip       TEXT NOT NULL DEFAULT ''      -- 客户端 IP
);
CREATE INDEX IF NOT EXISTS idx_op_logs_ts ON op_logs(ts);
"""

CACHE_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    sid       TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL,
    expire_ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_expire ON sessions(expire_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
"""

STATS_SQL = """
-- 详细用量统计：按 小时 × 用户 聚合的明细，独立于窗口化配额库，永不被配额重置清空
CREATE TABLE IF NOT EXISTS usage_hourly (
    user_id       INTEGER NOT NULL,
    hour_start    INTEGER NOT NULL,          -- 小时桶起点，epoch 秒（%3600 对齐）
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    updated_at    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, hour_start)
);
CREATE INDEX IF NOT EXISTS idx_usage_hourly_time ON usage_hourly(hour_start);
"""


def _migrate_users_drop_used_quota(conn) -> None:
    """
    旧版本 users 表带 used_quota 列（从未被写入）。SQLite 3.35+ 支持 DROP COLUMN，
    用 PRAGMA table_info 守护，存在则删。
    """
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "used_quota" in cols:
        conn.execute("ALTER TABLE users DROP COLUMN used_quota")
        conn.commit()


def _seed_settings(conn) -> None:
    """
    仅在 settings 表为空时，用环境变量播种窗口与全局限额；之后以后台修改为准。
    """
    cur = conn.execute("SELECT COUNT(*) AS c FROM settings")
    if cur.fetchone()["c"] == 0:
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("quota_window", QUOTA_WINDOW))
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("global_quota_limit", str(GLOBAL_QUOTA_LIMIT)))
        conn.commit()


def _init_data_db() -> None:
    conn = get_data_conn()
    conn.executescript(USER_SQL)
    _migrate_users_drop_used_quota(conn)
    _seed_settings(conn)

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
        conn.commit()

    # 仅当无用户时，从 .env 播种初始管理员
    cur = conn.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        if not ADMIN_PASSWORD.strip():
            raise ValueError(
                "首次启动需设置 ADMIN_PASSWORD（.env），用于播种初始管理员；账户创建后可移除。"
            )
        admin_group = conn.execute(
            "SELECT id FROM groups WHERE name = 'admin'"
        ).fetchone()
        pwd_hash, salt = hash_password(ADMIN_PASSWORD)
        conn.execute(
            "INSERT INTO users (username, pwd_hash, salt, group_id) VALUES (?, ?, ?, ?)",
            (ADMIN_USERNAME.strip(), pwd_hash, salt, admin_group["id"]),
        )
        conn.commit()
        print("Initial admin credential imported from .env into the database.")


def _init_cache_db() -> None:
    conn = get_cache_conn()
    conn.executescript(CACHE_SQL)


def _init_stats_db() -> None:
    conn = get_stats_conn()
    conn.executescript(STATS_SQL)


def init_db() -> None:
    """
    初始化数据库：建目录 + 三库建表 + 播种默认权限组与初始管理员。
    """
    DB_DIR.mkdir(parents=True, exist_ok=True)
    _init_data_db()
    _init_cache_db()
    _init_stats_db()
