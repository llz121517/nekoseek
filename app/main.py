# app/main.py
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from slowapi.errors import RateLimitExceeded
from starlette.websockets import WebSocket

from app.config import (
    ALLOW_ORIGINS,
    ALLOW_CREDENTIALS,
    ALLOW_METHODS,
    ALLOW_HEADERS,
    TITLE, VERSION, DESCRIPTION,
    DEBUG, DOCS_URL, REDOC_URL, OPENAPI_URL,
    DSH_AUTOSTART,
    LLM_PROXY_PATH,
)

# 导入 API 路由
from app.api.v1.auth import router as auth_router
from app.api.v1.admin import router as admin_router
from app.api.v1.quota import router as quota_router

# 导入核心服务
from app.core.auth import get_current_user, get_current_user_or_redirect, RedirectToLogin
from app.core.session import start_cleanup_worker
from app.core.db.init_db import init_db
from app.services import dsh_process
from app.services.proxy import proxy_webui, proxy_llm
from app.services.ws_proxy import proxy_ws

app = FastAPI(
    title=TITLE,
    version=VERSION,
    description=DESCRIPTION,
    debug=DEBUG,
    docs_url=DOCS_URL,
    redoc_url=REDOC_URL,
    openapi_url=OPENAPI_URL,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=ALLOW_CREDENTIALS,
    allow_methods=ALLOW_METHODS,
    allow_headers=ALLOW_HEADERS,
)


# 初始化区
init_db()
start_cleanup_worker()
if DSH_AUTOSTART:
    dsh_process.start()


# 异常捕获处理器
async def custom_429(request, exc):
    return JSONResponse(status_code=429, content={"code": 0, "msg": "操作频繁，请15分钟后重试"})


app.add_exception_handler(RateLimitExceeded, custom_429)


@app.exception_handler(RedirectToLogin)
async def redirect_exception_handler(request, exc: RedirectToLogin):
    return RedirectResponse(url=exc.headers["Location"], status_code=302)


# 挂载 API 端点
app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(quota_router)


# 登录页（公开入口）
@app.get("/login", tags=["page"])
async def login_page():
    return FileResponse("frontend/index.html")


# 管理后台入口（占位，前端骨架见 frontend/）
@app.get("/admin", tags=["page"], dependencies=[Depends(get_current_user)])
async def admin_page():
    return JSONResponse({"code": 0, "msg": "管理后台前端未构建，请访问 /docs 查看 API"})


# LLM 上游反代（DSH 通过 DEEPSEEK_BASE_URL 指向本网关 /llm）
@app.api_route(
    f"{LLM_PROXY_PATH}/{{path:path}}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["proxy"],
)
async def llm_proxy(request: Request, path: str):
    return await proxy_llm(request, path)


# DSH WebSocket 隧道（必须在 catch-all HTTP 路由之前注册，保证 Upgrade 优先匹配）
@app.websocket("/api/events.mux")
async def ws_events_mux(websocket: WebSocket):
    await proxy_ws(websocket, "/api/events.mux")


@app.websocket("/api/events.host")
async def ws_events_host(websocket: WebSocket):
    await proxy_ws(websocket, "/api/events.host")


# DSH webui 反代（含鉴权 + HTML 注入）
@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["proxy"],
)
async def webui_proxy(request: Request, path: str, _: dict = Depends(get_current_user_or_redirect)):
    return await proxy_webui(request, path)


# DSH webui 根路径（"/" 不匹配 {path:path}）
@app.get("/", tags=["proxy"], dependencies=[Depends(get_current_user_or_redirect)])
async def webui_root(request: Request):
    return await proxy_webui(request, "")
