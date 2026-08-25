# app/api/v1/quota.py
"""
配额查询接口（供注入 JS 轮询）
"""
from fastapi import APIRouter, Request

from app.core.auth import get_current_user
from app.core import quota

router = APIRouter(prefix="/api/v1/quota", tags=["quota", "api_v1"])


@router.get("")
async def get_quota(request: Request):
    """
    返回当前登录用户的配额概览（全局池 + 单用户）。
    """
    user = get_current_user(request)
    return {"code": 1, "msg": "ok", "data": quota.quota_summary(user)}
