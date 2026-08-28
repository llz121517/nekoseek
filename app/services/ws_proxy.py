# app/services/ws_proxy.py
"""
WebSocket 隧道：任意 Upgrade 路径透明转发到 DSH 上游。

虽然是 catch-all，但 DSH 的事件流通常是服务器 → 浏览器单向下行；
这里使用双向泵，浏览器上行也会原样转发，保持真正的"透明反代"语义。

配额计量：DSH 的 mux 下行帧线格式为
{"type":"server-request","rpcId":...,"method":"session/event","payload":{...session/event...}}；
payload.event.type=assistant/message 时，event.data.usage 携带真实
{inputTokens, outputTokens, cacheReadTokens, reasoningTokens}。一轮回复只发
一条该帧（assistant/chunk 流式分片不带 usage），逐帧直接记账。

输入口径 = inputTokens + cacheReadTokens：含本轮新增 prompt 与以缓存命中
的 system prompt / 历史上下文 / 工具结果，是模型实际处理的完整输入。
这是输入计量的唯一准确来源（HTTP 侧请求体估算已废弃）。usage 缺失时
退化为对 message.content 文本粗略分词估算输出（input 计 0）。
"""
import asyncio
import json
import logging

from starlette.websockets import WebSocket
from websockets.asyncio.client import connect as ws_connect

from app.config import DSH_UPSTREAM, DSH_ORIGIN
from app.core import quota, tokenize
from app.core.session import get_session_user_id
from app.core.db import db_op

logger = logging.getLogger("nekoseek.ws_proxy")


def _upstream_ws_url(path: str) -> str:
    """将 http(s) 上游地址换算为 ws(s) 地址。"""
    scheme = "wss" if DSH_UPSTREAM.startswith("https://") else "ws"
    host = DSH_UPSTREAM.removeprefix("http://").removeprefix("https://").rstrip("/")
    return f"{scheme}://{host}{path}"


def _extract_assistant_message(frame_text: str) -> tuple[int, int] | None:
    """
    从 mux 下行文本帧提取 assistant/message 的 (input_tokens, output_tokens)。

    DSH 实际线格式（见 dsh-client-connection/lib/index.js 的 serverRequest/send）：
        {"type":"server-request", "rpcId":"...", "method":"session/event",
         "payload": {"type":"session/event", "sessionId":"...", "event":{...}}}
    event.type == "assistant/message" 时，event.data.usage 为真实 TokenUsage：
        {inputTokens, outputTokens, cacheReadTokens, reasoningTokens, ...}
    实测（DSH webui）：一轮回复只发一条 assistant/message 完整帧，且是唯一带
    usage 的事件（assistant/chunk 流式分片不带 usage），故逐帧直接记账即可，
    无需跨帧增量去重。

    input_tokens 口径 = inputTokens + cacheReadTokens：inputTokens 是本轮新增的
    非缓存输入（用户当条 prompt），cacheReadTokens 是以缓存读取形式命中的
    system prompt / 历史上下文 / 工具结果——两者相加才是模型本轮实际处理的
    完整输入。只取 inputTokens 会漏掉上下文大头（这正是旧版 input 偏小的根因）。

    usage 缺失则对 message.content 文本估算输出（input 计 0）。
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
        # DSH TokenUsage 字段是 camelCase：inputTokens/outputTokens/cacheReadTokens
        # （见 dsh-llm/lib/types/types.d.ts 的 TokenUsage 接口）
        in_tok = (
            (usage.get("inputTokens") or usage.get("prompt_tokens") or 0)
            + (usage.get("cacheReadTokens") or usage.get("cache_read_input_tokens") or 0)
        )
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
    # WS 握手时 Cookie 头固定不变，缓存下来供每帧实时解析当前用户。
    cookie_header = websocket.headers.get("cookie", "")

    try:
        # 必须带上 Origin，否则 DSH 握手阶段可能因缺少 Origin 而 403。
        async with ws_connect(upstream_url, open_timeout=10.0, origin=DSH_ORIGIN) as upstream:
            await _pump(upstream, websocket, cookie_header)
    except Exception as e:
        logger.warning("WS 隧道 %s 异常: %r", path, e)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass


def _sid_from_cookie(cookie_header: str) -> str | None:
    """从 Cookie 头取 session_id（WS 握手后该头在整个连接期不变）。"""
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == "session_id":
            return value or None
    return None


def _make_user_resolver(cookie_header: str):
    """
    返回一个"按当前 session 实时解析用户"的闭包。

    为何不用连接建立时传入的 user 快照：另一条标签页退出并改登其他用户后，
    本连接快照仍指向旧用户，会造成同一次对话的输入（HTTP 按当前 cookie 计）
    与输出（WS 按旧快照计）记到不同用户。改为每帧都按 sid 实时校验 session
    有效性——session 被删除/踢掉后立即返回 None（不再用旧身份记账），使 WS
    输出记账与 HTTP 输入记账的口径一致。user 对象按 user_id 缓存以减少查库。
    """
    sid = _sid_from_cookie(cookie_header)
    cache = {"user_id": None, "user": None}

    def resolve() -> dict | None:
        if not sid:
            return None
        # 每帧校验 session 是否仍有效（主键查询，开销极小）；失效即不再记账。
        user_id = get_session_user_id(sid)
        if user_id is None:
            cache["user_id"] = None
            cache["user"] = None
            return None
        if cache["user_id"] != user_id or cache["user"] is None:
            user = db_op.get_user_by_id(user_id)
            cache["user_id"] = user_id
            cache["user"] = user if (user and user.get("status")) else None
        return cache["user"]

    return resolve


async def _pump(upstream, downstream: WebSocket, cookie_header: str = "") -> None:
    """双向泵：浏览器 <-> 上游全双工，任一侧结束即清理，避免泄漏。"""
    resolve_user = _make_user_resolver(cookie_header)

    async def upstream_to_downstream():
        async for message in upstream:
            if isinstance(message, (bytes, bytearray)):
                await downstream.send_bytes(bytes(message))
            else:
                text = str(message)
                await downstream.send_text(text)
                user = resolve_user()
                if user is not None:
                    usage = _extract_assistant_message(text)
                    if usage is not None:
                        # input/output 都来自真实 usage，逐帧记账（一轮一条）。
                        input_tok, output_tok = usage
                        if input_tok > 0 or output_tok > 0:
                            quota.record_usage(
                                user["id"],
                                input_tokens=input_tok,
                                output_tokens=output_tok,
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
