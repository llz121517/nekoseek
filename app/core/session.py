# app/core/session.py
"""
服务端 Session 管理：会话行存 SQLite cache.db，cookie 只承载 sid。
会话有独立过期时间，后台线程定期清理；创建新会话会踢掉同用户旧会话。
"""
import threading
import time
import uuid

from app.config import SESSION_MAX_AGE, SESSION_CLEANUP_AGE
from app.core.db.db import get_cache_conn


def create_session(user_id: int) -> str | None:
    """
    创建新会话，自动踢掉该用户的旧会话。
    """
    sid = str(uuid.uuid4())
    expire = time.time() + SESSION_MAX_AGE
    conn = get_cache_conn()
    try:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute(
            "INSERT INTO sessions (sid, user_id, expire_ts) VALUES (?, ?, ?)",
            (sid, user_id, expire),
        )
        conn.commit()
        return sid
    except Exception as e:
        print(f"Session creation failed: {e}")
        return None


def get_session_user_id(sid: str) -> int | None:
    """
    验证 session 是否有效且未过期，返回 user_id；否则 None。
    """
    if not sid:
        return None
    row = get_cache_conn().execute(
        "SELECT user_id FROM sessions WHERE sid = ? AND expire_ts > ?",
        (sid, time.time()),
    ).fetchone()
    return row["user_id"] if row else None


def delete_session(sid: str) -> None:
    if not sid:
        return
    conn = get_cache_conn()
    conn.execute("DELETE FROM sessions WHERE sid = ?", (sid,))
    conn.commit()


def cleanup_expired_sessions() -> int:
    conn = get_cache_conn()
    cur = conn.execute("DELETE FROM sessions WHERE expire_ts < ?", (time.time(),))
    conn.commit()
    return cur.rowcount


def start_cleanup_worker() -> None:
    """
    后台守护线程，定期清理过期 session。
    """

    def worker():
        while True:
            try:
                cleanup_expired_sessions()
            except Exception as e:
                print(f"Session cleanup error: {e}")
            time.sleep(SESSION_CLEANUP_AGE)

    threading.Thread(target=worker, daemon=True).start()
