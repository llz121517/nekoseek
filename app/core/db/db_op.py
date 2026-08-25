# app/core/db/db_op.py
"""
业务 CRUD 封装（直接操作 SQLite，无 ORM）
"""
import time
from typing import Any

from app.core.db.db import get_conn
from app.core.security import hash_password, generate_invite_code

# ---------- 权限组 ----------


def get_group_by_id(group_id: int) -> dict | None:
    row = get_conn().execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    return dict(row) if row else None


def get_group_by_name(name: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM groups WHERE name = ?", (name,)).fetchone()
    return dict(row) if row else None


def list_groups() -> list[dict]:
    rows = get_conn().execute("SELECT * FROM groups ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_group(name: str, is_admin: bool = False, quota_limit: int = 0) -> dict:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO groups (name, is_admin, quota_limit) VALUES (?, ?, ?)",
        (name, int(is_admin), quota_limit),
    )
    conn.commit()
    return get_group_by_id(cur.lastrowid)


def update_group(group_id: int, name: str | None = None, is_admin: bool | None = None, quota_limit: int | None = None) -> bool:
    conn = get_conn()
    parts, params = [], []
    if name is not None:
        parts.append("name = ?")
        params.append(name)
    if is_admin is not None:
        parts.append("is_admin = ?")
        params.append(int(is_admin))
    if quota_limit is not None:
        parts.append("quota_limit = ?")
        params.append(quota_limit)
    if not parts:
        return False
    params.append(group_id)
    cur = conn.execute(f"UPDATE groups SET {', '.join(parts)} WHERE id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def delete_group(group_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    return cur.rowcount > 0


# ---------- 用户 ----------


def get_user_by_id(user_id: int) -> dict | None:
    row = get_conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    return dict(row) if row else None


def list_users() -> list[dict]:
    rows = get_conn().execute("SELECT * FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def create_user(username: str, password: str, group_id: int, quota_override: int | None = None) -> dict:
    pwd_hash, salt = hash_password(password)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO users (username, pwd_hash, salt, group_id, quota_override) VALUES (?, ?, ?, ?, ?)",
        (username, pwd_hash, salt, group_id, quota_override),
    )
    conn.commit()
    return get_user_by_id(cur.lastrowid)


def update_user(
    user_id: int,
    password: str | None = None,
    group_id: int | None = None,
    quota_override: int | None = None,
    status: int | None = None,
) -> bool:
    conn = get_conn()
    parts, params = [], []
    if password is not None:
        pwd_hash, salt = hash_password(password)
        parts.append("pwd_hash = ?")
        params.append(pwd_hash)
        parts.append("salt = ?")
        params.append(salt)
    if group_id is not None:
        parts.append("group_id = ?")
        params.append(group_id)
    if quota_override is not None:
        parts.append("quota_override = ?")
        params.append(quota_override)
    if status is not None:
        parts.append("status = ?")
        params.append(status)
    if not parts:
        return False
    params.append(user_id)
    cur = conn.execute(f"UPDATE users SET {', '.join(parts)} WHERE id = ?", params)
    conn.commit()
    return cur.rowcount > 0


def delete_user(user_id: int) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def set_user_quota(user_id: int, quota_override: int | None) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE users SET quota_override = ? WHERE id = ?", (quota_override, user_id))
    conn.commit()
    return cur.rowcount > 0


def get_effective_user_quota(user: dict) -> int:
    """
    用户有效配额：优先 quota_override，其次所属组 quota_limit。
    """
    if user.get("quota_override") is not None:
        return user["quota_override"]
    group = get_group_by_id(user["group_id"])
    return (group or {}).get("quota_limit", 0)


# ---------- 邀请码 ----------


def create_invite(group_id: int, created_by: int | None, max_uses: int = 1, expires_at: str | None = None) -> dict:
    code = generate_invite_code()
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO invite_codes (code, group_id, created_by, max_uses, expires_at) VALUES (?, ?, ?, ?, ?)",
        (code, group_id, created_by, max_uses, expires_at),
    )
    conn.commit()
    return get_invite_by_code(code)


def get_invite_by_code(code: str) -> dict | None:
    row = get_conn().execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
    return dict(row) if row else None


def list_invites() -> list[dict]:
    rows = get_conn().execute("SELECT * FROM invite_codes ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def consume_invite(code: str) -> dict | None:
    """
    校验并消费邀请码：未过期、未超用、状态有效则 used_count+1，返回邀请码记录；否则返回 None。
    """
    conn = get_conn()
    row = conn.execute("SELECT * FROM invite_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        return None
    inv = dict(row)
    if inv["used_count"] >= inv["max_uses"]:
        return None
    if inv.get("expires_at"):
        # expires_at 为 ISO 格式字符串；过期视为无效
        try:
            from datetime import datetime
            expires = datetime.fromisoformat(inv["expires_at"])
            if expires.timestamp() < time.time():
                return None
        except ValueError:
            return None
    conn.execute(
        "UPDATE invite_codes SET used_count = used_count + 1 WHERE code = ?",
        (code,),
    )
    conn.commit()
    inv["used_count"] += 1
    return inv


def delete_invite(code: str) -> bool:
    conn = get_conn()
    cur = conn.execute("DELETE FROM invite_codes WHERE code = ?", (code,))
    conn.commit()
    return cur.rowcount > 0
