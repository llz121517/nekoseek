# app/api/v1/panel.py
"""
用户信息面板接口：向注入 EBUI 页面的右下角逐板提供当前用户数据。
"""
from fastapi import APIRouter, Depends

from app.core.auth import get_current_user
from app.core.db import db_op

router = APIRouter(prefix="/api/v1/panel", tags=["panel", "api_v1"])


@router.get("/me")
async def panel_me(user: dict = Depends(get_current_user)):
    """
    返回当前登录用户的面板数据。quota_limit 为 0 表示不限。

    pool 为全局池额度预留字段：当前无任何全局记账逻辑，恒为不限
    （limit=0 / used=0 / remaining=None）。未来接入全局记账后，
    前端在"全局已用完但个人仍有剩余"时会提示用户联系管理员。
    """
    quota_limit = db_op.get_effective_user_quota(user)
    used = user.get("used_quota", 0)
    return {
        "code": 1,
        "msg": "ok",
        "data": {
            "username": user["username"],
            "quota_limit": quota_limit,
            "used_quota": used,
            "remaining": (quota_limit - used) if quota_limit > 0 else None,
            # 全局池预留：尚无记账，恒不限
            "pool": {"limit": 0, "used": 0, "remaining": None},
        },
    }
