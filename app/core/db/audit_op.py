# app/core/db/audit_op.py
"""
操作日志（审计）CRUD：data.db 的 op_logs 表。

记录后台管理操作与登录/登出等关键事件，供管理后台「日志」页查询。
只追加、按时间倒序读取；提供 prune 防止无限增长。
"""
import time

from app.core.db.db import get_data_conn

# 日志级别白名单，非法值一律归为 info
LEVELS = ("info", "warning", "error")


def add_op_log(
    action: str,
    detail: str = "",
    username: str = "",
    ip: str = "",
    level: str = "info",
) -> None:
    """追加一条操作日志。"""
    if level not in LEVELS:
        level = "info"
    conn = get_data_conn()
    conn.execute(
        "INSERT INTO op_logs (ts, level, username, action, detail, ip) VALUES (?, ?, ?, ?, ?, ?)",
        (int(time.time()), level, username, action, detail, ip),
    )
    conn.commit()


def list_op_logs(
    limit: int = 50,
    offset: int = 0,
    level: str | None = None,
    keyword: str | None = None,
) -> tuple[list[dict], int]:
    """
    按时间倒序分页查询操作日志，返回 (rows, total)。
    level 过滤级别；keyword 在 username/action/detail/ip 上做模糊匹配。
    """
    where, params = [], []
    if level in LEVELS:
        where.append("level = ?")
        params.append(level)
    if keyword:
        where.append("(username LIKE ? OR action LIKE ? OR detail LIKE ? OR ip LIKE ?)")
        like = f"%{keyword}%"
        params.extend([like, like, like, like])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_data_conn()
    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM op_logs {where_sql}", params
    ).fetchone()["c"]
    rows = conn.execute(
        f"SELECT * FROM op_logs {where_sql} ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [dict(r) for r in rows], total


def prune_op_logs(keep: int = 5000) -> int:
    """只保留最近 keep 条，删除更早的，返回删除行数。"""
    conn = get_data_conn()
    cur = conn.execute(
        "DELETE FROM op_logs WHERE id NOT IN (SELECT id FROM op_logs ORDER BY ts DESC, id DESC LIMIT ?)",
        (keep,),
    )
    conn.commit()
    return cur.rowcount
