# app/main.py
"""
NekoSeek：透明反代 DSH webui + 网关自身 API。

路由顺序是关键：
1. /healthz（先于 catch-all）
2. 网关自身 API 路由（/api/v1/*）
3. 页面路由（/login、/admin、/favicon.ico）
4. WebSocket catch-all（先于 HTTP catch-all）
5. 根路径 /
6. HTTP catch-all（/api/v1/* 之外的请求才透传给 DSH）
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.websockets import WebSocket

from app.api.v1 import admin as admin_api
from app.api.v1 import auth as auth_api
from app.api.v1 import panel as panel_api
from app.config import (
    TITLE, VERSION, DESCRIPTION,
    DOCS_URL, REDOC_URL, OPENAPI_URL,
    DSH_AUTOSTART,
)
from app.core.db import db_op
from app.core import quota
from app.core.auth import (
    RedirectToLogin,
    _resolve_current_user,
    admin_required,
    get_current_user_or_redirect,
)
from app.core.db.init_db import init_db
from app.core import permguard
from app.core.session import get_session_user_id, start_cleanup_worker
from app.services import dsh_process, usage_meter
from app.services.proxy import proxy_webui, probe_upstream
from app.services.ws_proxy import proxy_ws

logger = logging.getLogger("nekoseek")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Linux 下收紧 .env 与 data/ 权限，必须早于 init_db（建库/落盘前）。
    permguard.harden()
    init_db()
    start_cleanup_worker()
    if DSH_AUTOSTART:
        try:
            result = dsh_process.start()
        except dsh_process.DSHIsolationError as e:
            # 无法以独立账户隔离 DSH：宁可不启动，也不在失去文件隔离的情况下运行。
            logger.error("DSH 隔离不可用，网关中止启动：%s", e)
            raise
        if not result.get("running"):
            logger.warning("DSH 自动拉起失败: %s", result)
    # 常驻用量计量：与 DSH 是否已就绪无关，meter 自带退避重连。
    usage_meter.start()
    yield
    await usage_meter.stop()
    if DSH_AUTOSTART:
        dsh_process.stop()


app = FastAPI(
    title=TITLE,
    version=VERSION,
    description=DESCRIPTION,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
    lifespan=lifespan,
)

# 静态资源（登录页 / admin 样式）
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.exception_handler(RedirectToLogin)
async def redirect_to_login_handler(request: Request, exc: RedirectToLogin):
    return RedirectResponse(url=exc.headers["Location"], status_code=302)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"code": 0, "msg": "操作频繁，请15分钟后重试"},
    )


@app.get("/healthz")
async def healthz(request: Request):
    """健康检查：返回网关自身状态与 DSH 上游可达性。"""
    up, latency_ms = await probe_upstream()
    return JSONResponse({
        "gateway": "ok",
        "dsh": {"up": up, "latency_ms": latency_ms},
    })


app.include_router(auth_api.router)
app.include_router(admin_api.router)
# 面板接口必须在 WS/HTTP catch-all 之前注册，否则会被透传给 DSH。
app.include_router(panel_api.router)
app.state.limiter = auth_api.limiter


@app.get("/login")
async def login_page(request: Request):
    """登录页（公开）。"""
    return FileResponse(FRONTEND_DIR / "login.html")


@app.get("/admin", dependencies=[Depends(admin_required)])
async def admin_page(request: Request):
    """管理后台（仅 admin）。"""
    response = FileResponse(FRONTEND_DIR / "admin.html")
    response.headers["Cache-Control"] = "no-store, private"
    return response


@app.get("/favicon.ico")
async def favicon():
    """站点图标，必须在 catch-all 之前注册。"""
    return FileResponse(STATIC_DIR / "favicon.ico")


# DSH RPC prompt 端点路径后缀：只有这类请求消耗模型配额，超限时才拦截；
# 页面导航、静态资源、其它 API 一律放行。
_PROMPT_PATH_SUFFIXES = ("/api/session.prompt", "/api/subagent.prompt")


def _is_prompt_request(request: Request, path: str) -> bool:
    if request.method != "POST":
        return False
    full = "/" + path if path else "/"
    return full.endswith(_PROMPT_PATH_SUFFIXES)


@app.websocket("/{path:path}")
async def ws_tunnel(websocket: WebSocket, path: str):
    """
    WebSocket 隧道：任意 Upgrade 路径都透明转发，需先鉴权。
    注意：不在此做配额拦截 —— mux/host 事件流是所有页面数据（工作区/历史）
    的下行通道，超限关闭会导致整个前端不可用。超限拦截只针对会消耗配额的
    HTTP RPC prompt 端点（见 webui_proxy）。
    """
    sid = websocket.cookies.get("session_id")
    user_id = get_session_user_id(sid) if sid else None
    if user_id is None:
        await websocket.close(code=4401)
        return
    user = db_op.get_user_by_id(user_id)
    if user is None or not user.get("status"):
        await websocket.close(code=4401)
        return
    await proxy_ws(websocket, "/" + path)


@app.get("/", dependencies=[Depends(get_current_user_or_redirect)])
async def webui_root(request: Request):
    """根路径：透传给 DSH。"""
    return await proxy_webui(request, "")


@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    dependencies=[Depends(get_current_user_or_redirect)],
)
async def webui_proxy(request: Request, path: str):
    """
    HTTP catch-all：所有剩余请求透传给 DSH webui。
    仅 RPC prompt 端点做配额预检，超限返回 429；其余请求一律放行。
    """
    if _is_prompt_request(request, path):
        # get_current_user_or_redirect 已在 dependencies 里跑过，这里再解一次拿 user
        user = _resolve_current_user(request)
        if user and (not quota.check_user_quota(user) or not quota.check_global_quota()):
            return JSONResponse(
                status_code=429,
                content={"code": 0, "msg": "配额已用完，请等待窗口重置或联系管理员"},
            )
        return await proxy_webui(request, path, user)
    return await proxy_webui(request, path)
