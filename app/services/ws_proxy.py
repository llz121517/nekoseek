# app/services/ws_proxy.py
"""
WebSocket 隧道：任意 Upgrade 路径透明转发到 DSH 上游。

虽然是 catch-all，但 DSH 的事件流通常是服务器 → 浏览器单向下行；
这里使用双向泵，浏览器上行也会原样转发，保持真正的"透明反代"语义。
"""
import asyncio
import logging

from starlette.websockets import WebSocket
from websockets.asyncio.client import connect as ws_connect

from app.config import DSH_UPSTREAM, DSH_ORIGIN

logger = logging.getLogger("nekoseek.ws_proxy")


def _upstream_ws_url(path: str) -> str:
    """将 http(s) 上游地址换算为 ws(s) 地址。"""
    scheme = "wss" if DSH_UPSTREAM.startswith("https://") else "ws"
    host = DSH_UPSTREAM.removeprefix("http://").removeprefix("https://").rstrip("/")
    return f"{scheme}://{host}{path}"


async def proxy_ws(websocket: WebSocket, path: str) -> None:
    """接受浏览器 WS 连接，连到 DSH 上游对应 WS，双向泵发数据。"""
    await websocket.accept()
    upstream_url = _upstream_ws_url(path)

    try:
        # 必须带上 Origin，否则 DSH 握手阶段可能因缺少 Origin 而 403。
        async with ws_connect(upstream_url, open_timeout=10.0, origin=DSH_ORIGIN) as upstream:
            await _pump(upstream, websocket)
    except Exception as e:
        logger.warning("WS 隧道 %s 异常: %r", path, e)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


async def _pump(upstream, downstream: WebSocket) -> None:
    """双向泵：浏览器 <-> 上游全双工，任一侧结束即清理，避免泄漏。"""

    async def upstream_to_downstream():
        async for message in upstream:
            if isinstance(message, (bytes, bytearray)):
                await downstream.send_bytes(bytes(message))
            else:
                await downstream.send_text(str(message))

    async def downstream_to_upstream():
        while True:
            message = await downstream.receive()
            if message["type"] == "websocket.disconnect":
                return
            if message["type"] == "websocket.receive":
                data = message.get("bytes") or message.get("text")
                await upstream.send(data)

    tasks = {
        asyncio.create_task(upstream_to_downstream()),
        asyncio.create_task(downstream_to_upstream()),
    }
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        exc = task.exception()
        if exc is not None:
            logger.debug("WS 隧道任务结束: %r", exc)
