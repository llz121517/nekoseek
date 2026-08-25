# app/services/proxy.py
"""
反向代理：DSH webui 反代（HTML 注入 + SSE 透传）与 LLM 上游反代（真实 usage 记账）
"""
import json

import httpx
from fastapi import Request, Response
from fastapi.responses import StreamingResponse, JSONResponse

from app.config import (
    DSH_UPSTREAM,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_API_KEY,
    LLM_FORCE_USAGE,
)
from app.services.inject import inject_quota_js
from app.core import quota

# 逐跳头，转发时剥离。
# 注意 content-encoding 一并剥离：下方使用 aiter_bytes()（已解码字节）回传，
# Starlette 会自行以 chunked/identity 重新编码，避免 gzip 字节错乱。
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length", "content-encoding",
}

# 长连接流式读取（SSE 无固定长度），连接超时 10s
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=None, pool=None)

_webui_client = httpx.AsyncClient(base_url=DSH_UPSTREAM, timeout=_STREAM_TIMEOUT)
_llm_client = httpx.AsyncClient(timeout=_STREAM_TIMEOUT)


def _forward_headers(request: Request) -> dict:
    """提取请求头，剥离逐跳头与 host（由 httpx 重新设置）。"""
    return {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


def _set_header(headers: dict, name: str, value: str) -> None:
    """大小写无关地覆盖一个请求头，避免旧键残留。"""
    lower = name.lower()
    for k in list(headers.keys()):
        if k.lower() == lower:
            del headers[k]
    headers[name] = value


def _response_headers(upstream: httpx.Response) -> dict:
    """提取响应头，剥离逐跳头。"""
    return {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }


def _parse_usage_from_event(event: bytes) -> tuple[int, int] | None:
    """
    解析单个 SSE 事件，若含 usage 字段返回 (prompt_tokens, completion_tokens)。
    """
    for line in event.splitlines():
        if not line.startswith(b"data:"):
            continue
        data = line[len(b"data:"):].strip()
        if not data or data == b"[DONE]":
            continue
        try:
            obj = json.loads(data)
        except (ValueError, TypeError):
            continue
        usage = obj.get("usage")
        if isinstance(usage, dict):
            return usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
    return None


async def _body_iterator(response: httpx.Response, on_usage=None):
    """
    逐块透传上游已解码字节，可同时解析 SSE 事件识别 usage；
    在 finally 中关闭上游响应，确保流被消费完或中断后连接正确释放。
    """
    try:
        if on_usage is not None:
            buffer = b""
            async for chunk in response.aiter_bytes():
                yield chunk
                buffer += chunk
                while b"\n\n" in buffer:
                    event, buffer = buffer.split(b"\n\n", 1)
                    usage = _parse_usage_from_event(event)
                    if usage:
                        on_usage(*usage)
            if buffer:
                usage = _parse_usage_from_event(buffer)
                if usage:
                    on_usage(*usage)
        else:
            async for chunk in response.aiter_bytes():
                yield chunk
    finally:
        await response.aclose()


def _ensure_include_usage(body: bytes, path: str) -> bytes:
    """
    对 chat/completions 请求强制 stream_options.include_usage（除非关闭）。
    """
    if not LLM_FORCE_USAGE or "/chat/completions" not in path:
        return body
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return body
    if isinstance(payload, dict) and isinstance(payload.get("stream"), bool):
        so = payload.setdefault("stream_options", {})
        if isinstance(so, dict):
            so.setdefault("include_usage", True)
        return json.dumps(payload).encode("utf-8")
    return body


# ---------- DSH webui 反代 ----------


async def proxy_webui(request: Request, path: str) -> Response:
    """
    将请求转发到 DSH webui 上游；对 text/html 注入配额进度条，其余流式透传（含 SSE）。
    """
    url = "/" + path if path else "/"
    if request.url.query:
        url += "?" + request.url.query

    body = await request.body()
    headers = _forward_headers(request)
    # DSH 会校验 Origin 与自身同源，否则返回 403。这里将浏览器带来的
    # 网关地址 Origin 重写为上游地址，Referer 一并重写保持一致。
    _set_header(headers, "origin", DSH_UPSTREAM)
    _set_header(headers, "referer", DSH_UPSTREAM + "/")

    upstream = await _webui_client.send(
        _webui_client.build_request(request.method, url, headers=headers, content=body),
        stream=True,
    )

    content_type = upstream.headers.get("content-type", "")
    if "text/html" in content_type:
        try:
            text = (await upstream.aread()).decode(
                upstream.charset_encoding or "utf-8", errors="replace"
            )
        finally:
            await upstream.aclose()
        text = inject_quota_js(text)
        return Response(
            content=text,
            status_code=upstream.status_code,
            media_type="text/html",
        )

    return StreamingResponse(
        _body_iterator(upstream),
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
    )


# ---------- LLM 上游反代（真实 usage 记账） ----------


async def proxy_llm(request: Request, path: str) -> Response:
    """
    LLM 上游反代：转发到 DeepSeek，读取真实 usage 累加全局池，超限返回 402。
    """
    # 全局池配额预检
    if not quota.check_pool_quota():
        return JSONResponse(status_code=402, content={"code": 0, "msg": "quota exceeded"})

    url = DEEPSEEK_BASE_URL.rstrip("/") + "/" + path
    if request.url.query:
        url += "?" + request.url.query

    headers = _forward_headers(request)
    if DEEPSEEK_API_KEY and "authorization" not in {k.lower() for k in headers}:
        headers["authorization"] = f"Bearer {DEEPSEEK_API_KEY}"

    body = _ensure_include_usage(await request.body(), path)

    def on_usage(prompt_tokens: int, completion_tokens: int):
        quota.add_pool_usage(prompt_tokens, completion_tokens)

    upstream = await _llm_client.send(
        _llm_client.build_request(request.method, url, headers=headers, content=body),
        stream=True,
    )

    content_type = upstream.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        return StreamingResponse(
            _body_iterator(upstream, on_usage=on_usage),
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
        )

    # 非流式：整体缓冲，尝试解析 usage
    try:
        content = await upstream.aread()
    finally:
        await upstream.aclose()
    usage = _parse_usage_from_event(content)
    if usage:
        on_usage(*usage)
    return Response(
        content=content,
        status_code=upstream.status_code,
        headers=_response_headers(upstream),
    )
