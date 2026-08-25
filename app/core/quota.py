# app/core/quota.py
"""
两级配额记账：全局池（真实 usage）+ 单用户（粗略分词估算）
超限统一返回 402（由调用方处理）。
"""
from datetime import datetime

from app.core.db import db_op
from app.core.db.db import get_conn
from app.config import POOL_QUOTA_LIMIT, DEFAULT_USER_QUOTA


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------- 全局池 ----------


def get_pool_usage(date: str | None = None) -> dict:
    date = date or _today()
    row = get_conn().execute("SELECT * FROM pool_usage WHERE date = ?", (date,)).fetchone()
    if row:
        return dict(row)
    return {"date": date, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_pool_usage(input_tokens: int, output_tokens: int) -> dict:
    """
    累加全局池用量（真实 usage），UPSERT 当日记录，返回最新记录。
    """
    date = _today()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO pool_usage (date, input_tokens, output_tokens, total_tokens)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            input_tokens = input_tokens + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            total_tokens = total_tokens + excluded.total_tokens
        """,
        (date, input_tokens, output_tokens, input_tokens + output_tokens),
    )
    conn.commit()
    return get_pool_usage(date)


def check_pool_quota() -> bool:
    """
    全局池是否仍有剩余配额。POOL_QUOTA_LIMIT 为 0 表示不限。
    """
    if POOL_QUOTA_LIMIT <= 0:
        return True
    used = get_pool_usage()["total_tokens"]
    return used < POOL_QUOTA_LIMIT


# ---------- 单用户 ----------


def get_user_usage(user_id: int, date: str | None = None) -> dict:
    date = date or _today()
    row = get_conn().execute(
        "SELECT * FROM usage_records WHERE user_id = ? AND date = ?", (user_id, date)
    ).fetchone()
    if row:
        return dict(row)
    return {"user_id": user_id, "date": date, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def add_user_usage(user_id: int, input_tokens: int, output_tokens: int) -> dict:
    """
    累加单用户估算用量，UPSERT 当日记录，返回最新记录。
    """
    date = _today()
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO usage_records (user_id, date, input_tokens, output_tokens, total_tokens)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            input_tokens = input_tokens + excluded.input_tokens,
            output_tokens = output_tokens + excluded.output_tokens,
            total_tokens = total_tokens + excluded.total_tokens
        """,
        (user_id, date, input_tokens, output_tokens, input_tokens + output_tokens),
    )
    conn.commit()
    return get_user_usage(user_id, date)


def get_user_quota_limit(user: dict) -> int:
    """
    用户有效周期配额：quota_override → 组 quota_limit → DEFAULT_USER_QUOTA。
    """
    override = user.get("quota_override")
    if override is not None:
        return override
    group = db_op.get_group_by_id(user["group_id"])
    if group and group.get("quota_limit"):
        return group["quota_limit"]
    return DEFAULT_USER_QUOTA


def check_user_quota(user: dict) -> bool:
    """
    单用户是否仍有剩余配额。limit 为 0 表示不限。
    """
    limit = get_user_quota_limit(user)
    if limit <= 0:
        return True
    used = get_user_usage(user["id"])["total_tokens"]
    return used < limit


# ---------- 进度条数据 ----------


def quota_summary(user: dict) -> dict:
    """
    供注入 JS 轮询的配额概览：全局池 + 单用户，各自 used/limit/remaining。
    """
    pool = get_pool_usage()
    user_usage = get_user_usage(user["id"])
    user_limit = get_user_quota_limit(user)
    return {
        "pool": {
            "used": pool["total_tokens"],
            "limit": POOL_QUOTA_LIMIT,
            "remaining": max(0, POOL_QUOTA_LIMIT - pool["total_tokens"]) if POOL_QUOTA_LIMIT > 0 else None,
        },
        "user": {
            "used": user_usage["total_tokens"],
            "limit": user_limit,
            "remaining": max(0, user_limit - user_usage["total_tokens"]) if user_limit > 0 else None,
        },
    }
