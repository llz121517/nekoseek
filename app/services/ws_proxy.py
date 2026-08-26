# app/services/ws_proxy.py
"""
WebSocket 隧道：任意 Upgrade 路径透明转发到 DSH 上游。

虽然是 catch-all，但 DSH 的事件流通常是服务器 → 浏览器单向下行；
这里使用双向泵，浏览器上行也会原样转发，保持真正的"透明反代"语义。

配额计量（输出）：DSH 的 mux 下行帧线格式为
{"type":"server-request","rpcId":...,"method":"session/event","payload":{...session/event...}}；
payload.event.type=assistant/message 时，event.data.usage 携带真实
{prompt_tokens, completion_tokens}；这里解析并累加到配额。
usage 缺失时退化为对 message.content 里的文本粗略分词估算。
"""
import asyncio
import json
import logging

from starlette.websockets import WebSocket
from websockets.asyncio.client import connect as ws_connect

from app.config import DSH_UPSTREAM, DSH_ORIGIN
from app.core import quota, tokenize

logger = logging.getLogger("nekoseek.ws_proxy")


def _upstream_ws_url(path: str) -> str:
    """将 http(s) 上游地址换算为 ws(s) 地址。"""
    scheme = "wss" if DSH_UPSTREAM.startswith("https://") else "ws"
    host = DSH_UPSTREAM.removeprefix("http://").removeprefix("https://").rstrip("/")
    return f"{scheme}://{host}{path}"


def _extract_assistant_message(frame_text: str) -> tuple[int, int] | None:
    """
    从 mux 下行文本帧提取 assistant/message 的 (prompt_tokens, completion_tokens)。

    DSH 实际线格式（见 dsh-client-connection/lib/index.js 的 serverRequest/send）：
        {"type":"server-request", "rpcId":"...", "method":"session/event",
         "payload": {"type":"session/event", "sessionId":"...", "event":{...}}}
    event.type == "assistant/message" 时，event.data.usage 为真实
    {prompt_tokens, completion_tokens}；缺失则对 message.content 文本估算。
    无法识别时返回 None。
    """
    try:
        frame = json.loads(frame_text)
    except (ValueError, TypeError):
        return None
    if not isinstance(frame, dict):
        return None
    if frame.get("type") != "server-request":
        return None
    payload = frame.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "session/event":
        return None
    event = payload.get("event")
    if not isinstance(event, dict) or event.get("type") != "assistant/message":
        return None
    data = event.get("data") or {}
    usage = data.get("usage")
    if isinstance(usage, dict):
        # DSH TokenUsage 字段是 camelCase：inputTokens/outputTokens
        # （见 dsh-llm/lib/types/types.d.ts 的 TokenUsage 接口）
        in_tok = usage.get("inputTokens") or usage.get("prompt_tokens") or 0
        out_tok = usage.get("outputTokens") or usage.get("completion_tokens") or 0
        return int(in_tok), int(out_tok)
    # 无 usage：对 assistant 消息文本估算输出
    message = data.get("message") or {}
    content = message.get("content")
    est = 0
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                if isinstance(text, str):
                    est += tokenize.estimate_tokens(text)
    elif isinstance(content, str):
        est = tokenize.estimate_tokens(content)
    return (0, est)


async def proxy_ws(websocket: WebSocket, path: str, user: dict | None = None) -> None:
    """接受浏览器 WS 连接，连到 DSH 上游对应 WS，双向泵发数据。"""
    await websocket.accept()
    upstream_url = _upstream_ws_url(path)

    try:
        # 必须带上 Origin，否则 DSH 握手阶段可能因缺少 Origin 而 403。
        async with ws_connect(upstream_url, open_timeout=10.0, origin=DSH_ORIGIN) as upstream:
            await _pump(upstream, websocket, user)
    except Exception as e:
        logger.warning("WS 隧道 %s 异常: %r", path, e)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


async def _pump(upstream, downstream: WebSocket, user: dict | None = None) -> None:
    """双向泵：浏览器 <-> 上游全双工，任一侧结束即清理，避免泄漏。"""

    async def upstream_to_downstream():
        async for message in upstream:
            if isinstance(message, (bytes, bytearray)):
                await downstream.send_bytes(bytes(message))
            else:
                text = str(message)
                await downstream.send_text(text)
                if user is not None:
                    usage = _extract_assistant_message(text)
                    if usage is not None:
                        prompt_tok, completion_tok = usage
                        if prompt_tok > 0 or completion_tok > 0:
                            quota.record_usage(
                                user["id"],
                                input_tokens=prompt_tok,
                                output_tokens=completion_tok,
                            )

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
