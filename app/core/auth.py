# app/core/auth.py
"""
认证依赖：登录态识别 + 权限组校验
"""
from fastapi import HTTPException, Request

from app.core.db import db_op
from app.core.session import get_session_user_id


def get_limiter_key(request: Request) -> str:
    """
    自定义限流 key：真实连接 IP + User-Agent 前缀，防 X-Forwarded-For 伪造。
    """
    ip = request.client.host if request.client else "unknown"
    ua_prefix = (request.headers.get("User-Agent", "") or "")[:20]
    return f"{ip}|{ua_prefix}"


def _resolve_current_user(request: Request) -> dict | None:
    """
    从 cookie 解析当前登录用户，未登录返回 None。
    """
    sid = request.cookies.get("session_id")
    user_id = get_session_user_id(sid) if sid else None
    if user_id is None:
        return None
    return db_op.get_user_by_id(user_id)


def get_current_user(request: Request) -> dict:
    """
    依赖：返回当前登录用户，未登录抛 401。
    """
    user = _resolve_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    if not user.get("status"):
        raise HTTPException(status_code=403, detail="账号已停用")
    return user


def get_current_user_or_redirect(request: Request) -> dict:
    """
    页面依赖：未登录浏览器一律重定向到 /login（仅用于 webui 反代 / 页面路由）。
    """
    user = _resolve_current_user(request)
    if user is None:
        raise RedirectToLogin("/login")
    if not user.get("status"):
        raise HTTPException(status_code=403, detail="账号已停用")
    return user


def get_current_admin(request: Request) -> dict:
    """
    依赖：返回当前登录用户，且必须属于 admin 权限组，否则抛 403。
    """
    user = get_current_user(request)
    group = db_op.get_group_by_id(user["group_id"])
    if group is None or not group.get("is_admin"):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# 别名，便于阅读
login_required = get_current_user
admin_required = get_current_admin


class RedirectToLogin(HTTPException):
    """自定义异常，携带重定向目标"""

    def __init__(self, url: str = "/login"):
        super().__init__(status_code=302, headers={"Location": url})
