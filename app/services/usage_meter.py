# app/services/usage_meter.py
"""
网关常驻 mux 计量消费者：用量记帐的唯一来源。

为何不再在浏览器 WS 隧道里记账（ws_proxy 已退化为纯透传）：
- DSH 的 mux 事件流是广播——所有 session 的 assistant/message 帧推给每一条
  已连接的 webui WS，按"谁的连接收到帧"记账会把他人用量误记到闲置用户头上，
  多开 webui 还会重复计；
- 计量依赖浏览器在线则可被绕过：发完 prompt 立刻关页面，任务在无人观看时
  完成，usage 帧无人接收，个人配额永远扣不到（mux 不重放历史，补不回来）。

网关改为自持一条 mux WS 长连接（/api/events.mux，与浏览器下行同一广播源、
同一帧格式），作为唯一计量消费者：与浏览器是否在线无关，每帧只见一次。

注意：/api/events.mux 虽在 dsh-host-apiproxy 的 toFetchHandler 里有 SSE 变体，
但部署态的 web 层（dsh-client-connection/lib/index.js apply()）对这两个事件
路径的普通 GET 一律回 426 Upgrade Required——只接受 WebSocket，SSE 不可达。

归属：帧里只有 sessionId 没有用户身份；prompt 只走 HTTP，proxy 层在转发
session.prompt / subagent.prompt 时已记录 sessionId → user_id
（app/core/attribution.py），此处按 sessionId 找回真正发起者。无归属的
（agent 自动派生的一次性子代理、网关重启前的会话）只记全局池，不计个人。

帧线格式：
{"type":"server-request","rpcId":...,"method":"session/event","payload":{...}}
payload.event.type=assistant/message 时 event.data.usage 携带真实
{inputTokens, outputTokens, cacheReadTokens, reasoningTokens}。一轮回复只发
一条该帧（assistant/chunk 流式分片不带 usage），逐帧直接记账。

输入口径 = inputTokens + cacheReadTokens：含本轮新增 prompt 与以缓存命中
的 system prompt / 历史上下文 / 工具结果，是模型实际处理的完整输入。
usage 缺失时退化为对 message.content 文本粗略分词估算输出（input 计 0）。

残余缺口：meter 断连重连的窗口内发出的帧会丢（mux 不重放）。该窗口只由
网关/上游故障造成，用户无法操控；如需彻底堵住，另做定期对账（读 DSH 落盘
session 日志累计 usage 补差），不在本模块范围。
"""
import asyncio
import json
import logging
import time

from websockets.asyncio.client import connect as ws_connect

from app.config import DSH_UPSTREAM, DSH_ORIGIN
from app.core import attribution, quota, tokenize
from app.core.db import db_op

logger = logging.getLogger("nekoseek.usage_meter")

# 与浏览器同一个 mux 下行端点；ws(s)  scheme 由上游 http(s) 地址换算。
_MUX_WS_URL = (
    ("wss" if DSH_UPSTREAM.startswith("https://") else "ws")
    + "://"
    + DSH_UPSTREAM.removeprefix("http://").removeprefix("https://").rstrip("/")
    + "/api/events.mux"
)

# 断线重连退避：1s 起步，指数到 30s 封顶。
_RECONNECT_MIN_S = 1.0
_RECONNECT_MAX_S = 30.0

_task: asyncio.Task | None = None


def _extract_assistant_message(frame_text: str) -> tuple[str, int, int] | None:
    """
    从 mux 下行文本帧提取 assistant/message 的 (session_id, input_tokens, output_tokens)。

    input_tokens 口径 = inputTokens + cacheReadTokens：inputTokens 是本轮新增的
    非缓存输入（用户当条 prompt），cacheReadTokens 是以缓存读取形式命中的
    system prompt / 历史上下文 / 工具结果——两者相加才是模型本轮实际处理的
    完整输入。只取 inputTokens 会漏掉上下文大头。

    usage 缺失则对 message.content 文本估算输出（input 计 0）。
    无法识别（非 session/event、非 assistant/message、缺 sessionId）返回 None。
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
    session_id = payload.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
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
        return session_id, int(in_tok), int(out_tok)
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
    return session_id, 0, est


def _handle_frame(frame_text: str) -> None:
    """
    对一帧尝试记账：归属命中记到发起者，否则只记全局池。
    所有异常就地消化——计量故障绝不拖垮消费循环。
    """
    extracted = _extract_assistant_message(frame_text)
    if extracted is None:
        return
    session_id, input_tok, output_tok = extracted
    if input_tok <= 0 and output_tok <= 0:
        return
    try:
        owner_id = attribution.resolve_owner(session_id)
        owner = db_op.get_user_by_id(owner_id) if owner_id is not None else None
        if owner is not None and owner.get("status"):
            quota.record_usage(owner["id"], input_tokens=input_tok, output_tokens=output_tok)
        else:
            # 无归属（子代理内部会话/重启前会话）或发起者已删除/停用：
            # 只记全局池，不冤枉任何个人。
            quota.record_global_usage(input_tokens=input_tok, output_tokens=output_tok)
    except Exception:  # noqa: BLE001
        logger.warning("用量记账失败 session=%s", session_id, exc_info=True)


async def _consume_once() -> None:
    """建立一条 mux WS 连接并消费到结束；返回即代表需要重连。"""
    # 必须带 Origin，否则 DSH 握手阶段可能因缺少 Origin 而 403（与 ws_proxy 相同）。
    async with ws_connect(_MUX_WS_URL, open_timeout=10.0, origin=DSH_ORIGIN) as ws:
        logger.info("mux 计量流已连接 %s", _MUX_WS_URL)
        async for message in ws:
            if isinstance(message, (bytes, bytearray)):
                continue  # mux 下行是文本 JSON 帧；二进制帧不属于本协议，忽略
            _handle_frame(str(message))


async def _run_forever() -> None:
    """常驻循环：断线/异常后退避重连，直到被取消。"""
    backoff = _RECONNECT_MIN_S
    while True:
        started = time.monotonic()
        try:
            await _consume_once()
            logger.warning("mux 计量流意外结束，%.0fs 后重连", backoff)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("mux 计量流断开: %r；%.0fs 后重连", e, backoff)
        # 连接曾稳定维持一段时间（而非立刻失败），视为健康，退避重置回最小值。
        if time.monotonic() - started > 60:
            backoff = _RECONNECT_MIN_S
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _RECONNECT_MAX_S)


def start() -> asyncio.Task:
    """启动常驻计量任务（由 lifespan 调用）。"""
    global _task
    _task = asyncio.create_task(_run_forever(), name="usage-meter")
    return _task


async def stop() -> None:
    """取消常驻任务并等待退出（由 lifespan 关闭阶段调用）。"""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
