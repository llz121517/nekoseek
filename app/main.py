# app/main.py
"""
NekoSeek MVP：透明反代 DSH webui + 全部 API。

路由顺序是关键：
1. /healthz（先于 catch-all）
2. WebSocket catch-all（先于 HTTP catch-all）
3. 根路径 /
4. HTTP catch-all
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.websockets import WebSocket

from app.config import (
    TITLE, VERSION, DESCRIPTION,
    DOCS_URL, REDOC_URL, OPENAPI_URL,
    DSH_AUTOSTART,
)
from app.services import dsh_process
from app.services.dsh_env import sync_dsh_env
from app.services.proxy import proxy_webui, probe_upstream
from app.services.ws_proxy import proxy_ws

logger = logging.getLogger("nekoseek")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sync_dsh_env()
    if DSH_AUTOSTART:
        result = dsh_process.start()
        if not result.get("running"):
            logger.warning("DSH 自动拉起失败: %s", result)
    yield
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


@app.get("/healthz")
async def healthz(request: Request):
    """健康检查：返回网关自身状态与 DSH 上游可达性。"""
    up, latency_ms = await probe_upstream()
    return JSONResponse({
        "gateway": "ok",
        "dsh": {"up": up, "latency_ms": latency_ms},
    })


@app.websocket("/{path:path}")
async def ws_tunnel(websocket: WebSocket, path: str):
    """WebSocket 隧道：任意 Upgrade 路径都透明转发。"""
    await proxy_ws(websocket, "/" + path)


@app.get("/")
async def webui_root(request: Request):
    """根路径：透传给 DSH。"""
    return await proxy_webui(request, "")


@app.api_route(
    "/{path:path}",
    methods=["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def webui_proxy(request: Request, path: str):
    """HTTP catch-all：所有剩余请求透传给 DSH webui。"""
    return await proxy_webui(request, path)
