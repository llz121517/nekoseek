# app/core/audit.py
"""
操作日志埋点助手：从 FastAPI Request 提取操作者与客户端 IP，写入 op_logs。

设计原则：审计日志绝不阻断业务——任何写库异常都吞掉并仅记 warning，
避免日志故障影响正常接口。
"""
import logging

from fastapi import Request

from app.core.auth import _resolve_current_user
from app.core.db import audit_op

logger = logging.getLogger("nekoseek.audit")


def _client_ip(request: Request | None) -> str:
    """取真实连接 IP（与限流 key 一致，不信 X-Forwarded-For，防伪造）。"""
    if request is None or request.client is None:
        return ""
    return request.client.host or ""


def record(
    request: Request | None,
    action: str,
    detail: str = "",
    level: str = "info",
    username: str | None = None,
) -> None:
    """
    记录一条操作日志。

    username 默认从 request 的登录态解析；显式传入则优先（用于登录失败等
    尚无有效会话的场景）。request 可为 None（系统内部事件），此时 IP 为空。
    """
    try:
        if username is None:
            user = _resolve_current_user(request) if request is not None else None
            username = user["username"] if user else ""
        audit_op.add_op_log(
            action=action,
            detail=detail,
            username=username,
            ip=_client_ip(request),
            level=level,
        )
    except Exception:
        logger.warning("写入操作日志失败 action=%s", action, exc_info=True)
