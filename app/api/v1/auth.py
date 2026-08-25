# app/api/v1/auth.py
"""
认证接口：注册（仅邀请制）、登录、登出、状态检查
"""
from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from slowapi import Limiter

from app.core.auth import get_limiter_key, get_current_user
from app.core.db import db_op
from app.core.security import verify_password
from app.core.session import create_session, delete_session
from app.config import (
    SESSION_COOKIE_KEY,
    SESSION_MAX_AGE,
    SESSION_HTTPONLY,
    SESSION_SAMESITE,
    SESSION_SECURE,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth", "api_v1"])
limiter = Limiter(key_func=get_limiter_key)


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    invite_code: str = Field(..., min_length=1, max_length=64)


class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)


@router.post("/register")
async def register(payload: RegisterIn):
    """
    注册（仅邀请制）：校验并消费邀请码，按邀请码所属权限组入组。
    """
    username = payload.username.strip()
    if db_op.get_user_by_username(username):
        return {"code": 0, "msg": "用户名已存在"}

    invite = db_op.consume_invite(payload.invite_code)
    if invite is None:
        return {"code": 0, "msg": "邀请码无效、已过期或已达使用上限"}

    db_op.create_user(username, payload.password, invite["group_id"])
    return {"code": 1, "msg": "注册成功"}


@router.post("/login")
@limiter.limit("5/15minute")
async def login(request: Request, response: Response, payload: LoginIn):
    """
    登录：pbkdf2 校验，成功后写 session + 设 HTTP-only cookie。
    """
    username = payload.username.strip()
    user = db_op.get_user_by_username(username)

    # 固定计算量，防时序侧信道（无论用户是否存在都执行一次哈希）
    if user is None:
        verify_password(payload.password, "0" * 64, "00" * 16)
        return {"code": 0, "msg": "用户名或密码错误"}

    if not verify_password(payload.password, user["pwd_hash"], user["salt"]):
        return {"code": 0, "msg": "用户名或密码错误"}

    if not user.get("status"):
        return {"code": 0, "msg": "账号已停用"}

    sid = create_session(user["id"])
    if sid is None:
        return {"code": 0, "msg": "会话创建失败"}

    response.set_cookie(
        key=SESSION_COOKIE_KEY,
        value=sid,
        httponly=SESSION_HTTPONLY,
        max_age=SESSION_MAX_AGE,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
    )
    return {"code": 1, "msg": "登录成功"}


@router.post("/logout")
async def logout(request: Request, response: Response):
    sid = request.cookies.get(SESSION_COOKIE_KEY)
    if sid:
        delete_session(sid)
    response.delete_cookie(key=SESSION_COOKIE_KEY)
    return {"code": 1, "msg": "已登出"}


@router.get("/check")
async def check(request: Request):
    user = _try_current_user(request)
    if user is None:
        return {"code": 1, "msg": "未登录", "data": {"logged_in": False}}
    return {
        "code": 1,
        "msg": "已登录",
        "data": {
            "logged_in": True,
            "username": user["username"],
            "group_id": user["group_id"],
        },
    }


def _try_current_user(request: Request):
    """登录检查不抛 401，返回 None。"""
    try:
        return get_current_user(request)
    except Exception:
        return None
