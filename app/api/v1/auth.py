# app/api/v1/auth.py
"""
认证接口：注册（仅邀请制）、登录、登出、状态检查
"""
import logging
import secrets
import time
from hashlib import pbkdf2_hmac

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field
from slowapi import Limiter

from app.config import (
    SESSION_COOKIE_KEY,
    SESSION_HTTPONLY,
    SESSION_MAX_AGE,
    SESSION_SAMESITE,
    SESSION_SECURE,
)
from app.core.auth import get_current_user, get_limiter_key
from app.core.db import db_op
from app.core.security import PBKDF2_ITERATIONS, verify_password
from app.core.session import create_session, delete_session

logger = logging.getLogger("nekoseek")

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

    user = db_op.create_user(username, payload.password, invite["group_id"])
    db_op.record_invite_use(invite["id"], user["id"])
    return {"code": 1, "msg": "注册成功"}


@router.post("/login")
@limiter.limit("5/15minute")
async def login(request: Request, response: Response, payload: LoginIn):
    """
    登录：pbkdf2 校验，成功后写 session + 设 HTTP-only cookie。
    失败时统一错误信息并加入随机延迟，降低时间侧信道（用户枚举/密码枚举）风险。
    """
    username = payload.username.strip()
    user = db_op.get_user_by_username(username)

    # 用户不存在时用由用户名派生的确定性 dummy 凭据跑同一次 PBKDF2，
    # 使"存在但密码错"与"不存在"两条路径的计算耗时尽量接近，防止通过
    # 时间侧信道枚举用户名。
    if user is None:
        pwd_hash, salt = _derive_dummy_credentials(username)
    else:
        pwd_hash, salt = user["pwd_hash"], user["salt"]

    valid = verify_password(payload.password, pwd_hash, salt)

    # 统一失败：不区分用户不存在、密码错误、账号停用
    if user is None or not valid or not user.get("status"):
        # 随机 50-149ms 延迟，增加统计时间分析难度
        time.sleep(0.05 + secrets.randbelow(100) / 1000)
        return {"code": 0, "msg": "用户名或密码错误"}

    sid = create_session(user["id"])
    if sid is None:
        logger.error("为用户 %s 创建 session 失败", username)
        time.sleep(0.05 + secrets.randbelow(100) / 1000)
        return {"code": 0, "msg": "用户名或密码错误"}

    response.set_cookie(
        key=SESSION_COOKIE_KEY,
        value=sid,
        httponly=SESSION_HTTPONLY,
        max_age=SESSION_MAX_AGE,
        samesite=SESSION_SAMESITE,
        secure=SESSION_SECURE,
    )
    return {"code": 1, "msg": "登录成功"}


def _derive_dummy_credentials(username: str) -> tuple[str, str]:
    """
    为不存在的用户生成确定性 dummy 凭据（64 hex hash + 32 hex salt，与真实凭据格式一致），
    使 PBKDF2 输入长度固定，两条失败路径耗时不可区分。
    """
    digest = pbkdf2_hmac(
        "sha256",
        username.encode("utf-8"),
        b"nekoseek-dummy-salt-seed",
        PBKDF2_ITERATIONS,
    ).hex()
    # 64 hex chars for hash, 32 hex chars for salt
    return digest[:64], digest[32:]


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
    group = db_op.get_group_by_id(user["group_id"])
    return {
        "code": 1,
        "msg": "已登录",
        "data": {
            "logged_in": True,
            "username": user["username"],
            "group_id": user["group_id"],
            "is_admin": bool(group and group.get("is_admin")),
        },
    }


def _try_current_user(request: Request):
    """登录检查不抛 401，返回 None。"""
    try:
        return get_current_user(request)
    except Exception:
        return None
