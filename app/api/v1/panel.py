# app/api/v1/panel.py
"""
用户信息面板接口：向注入 EBUI 页面的右下角逐板提供当前用户数据。
"""
from fastapi import APIRouter, Depends

from app.core import quota
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/v1/panel", tags=["panel", "api_v1"])


@router.get("/me")
async def panel_me(user: dict = Depends(get_current_user)):
    """
    返回当前登录用户的面板数据：窗口类型 + 个人用量 + 全局池用量。
    limit 为 0 表示不限；remaining 仅在有限额时给出。
    """
    summary = quota.quota_summary(user)
    return {
        "code": 1,
        "msg": "ok",
        "data": {
            "username": user["username"],
            "window": summary["window"],
            "user": summary["user"],
            "pool": summary["pool"],
        },
    }
