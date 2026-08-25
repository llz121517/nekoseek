# app/services/ws_proxy.py
"""
DSH WebSocket 隧道：/api/events.mux 与 /api/events.host

DSH 的事件流（实时状态/事件推送）走这两个 WebSocket 升级端点，且是
纯「服务器 → 浏览器」下行（客户端发消息会触发 1008 关闭）。
"""
import asyncio
import logging

from starlette.websockets import WebSocket
from websockets.asyncio.client import connect as ws_connect

from app.config import DSH_UPSTREAM

logger = logging.getLogger("nekoseek.ws_proxy")


def _upstream_ws_url(path: str) -> str:
    """
    将 http(s) 上游地址换算为 ws(s) 地址。
    """
    scheme = "wss" if DSH_UPSTREAM.startswith("https://") else "ws"
    host = DSH_UPSTREAM.removeprefix("http://").removeprefix("https://").rstrip("/")
    return f"{scheme}://{host}{path}"


async def proxy_ws(websocket: WebSocket, path: str) -> None:
    """
    隧道：接受浏览器 WS 连接，连到 DSH 上游对应 WS，泵发下行数据。
    """
    await websocket.accept()
    upstream_url = _upstream_ws_url(path)

    try:
        async with ws_connect(upstream_url, open_timeout=10.0) as upstream:
            await _pump(upstream, websocket)
    except Exception as e:
        logger.warning("WS 隧道 %s 异常: %r", path, e)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


async def _pump(upstream, downstream: WebSocket) -> None:
    """
    把上游 WS 的每个下行帧转发给浏览器。

    只做「上游 → 浏览器」单向下行：DSH 是 downlink-only，
    浏览器上行会被 DSH 以 1008 关闭，故不转发上行。
    同时监听浏览器断开，及时结束，避免泄漏。
    """
    async def upstream_to_downstream():
        async for message in upstream:
            if isinstance(message, (bytes, bytearray)):
                await downstream.send_bytes(bytes(message))
            else:
                await downstream.send_text(str(message))

    async def watch_disconnect():
        # 仅用于感知浏览器断开；收到断开即结束等待
        await downstream.receive()

    up_task = asyncio.create_task(upstream_to_downstream())
    disc_task = asyncio.create_task(watch_disconnect())

    done, pending = await asyncio.wait(
        {up_task, disc_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # 清理：取消未完成的任务，避免 "never awaited" 与资源泄漏
    for task in pending:
        task.cancel()
    for task in done:
        # 吞掉任务内的异常，只记录，不让其冒泡导致连接清理逻辑中断
        exc = task.exception()
        if exc is not None:
            logger.debug("WS 隧道任务结束: %r", exc)
