# app/api/v1/site.py
"""
站点公开配置接口：无需登录，供登录页等公开页面读取。
只暴露非敏感的展示性配置（如 ICP 备案号），绝不在此输出密钥类配置。
配置存于数据库 settings 表，由管理后台维护。
"""
from fastapi import APIRouter

from app.core.db import db_op

router = APIRouter(prefix="/api/v1/site", tags=["site", "api_v1"])


@router.get("/config")
async def get_site_config():
    """公开站点配置。ICP 备案号未配置时返回空串，前端据此隐藏入口。"""
    icp = (db_op.get_setting("icp_number", "") or "").strip()
    return {
        "code": 1,
        "msg": "ok",
        "data": {"icp_number": icp},
    }
