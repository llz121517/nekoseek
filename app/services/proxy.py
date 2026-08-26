# app/services/proxy.py
"""
HTTP 透明反代：DSH webui 全部请求透传。
"""
import logging
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import DSH_UPSTREAM, DSH_ORIGIN

logger = logging.getLogger("nekoseek.proxy")

# 逐跳头，转发时剥离。
# 注意 content-encoding 一并剥离：下方使用 aiter_bytes()（已解码字节）回传，
# Starlette 会自行以 chunked/identity 重新编码，避免 gzip 字节错乱。
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length", "content-encoding",
}

# 长连接流式读取，连接超时 10s
_STREAM_TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=None, pool=None)

_client = httpx.AsyncClient(base_url=DSH_UPSTREAM, timeout=_STREAM_TIMEOUT)


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


def _rewrite_location(headers: dict, request: Request) -> None:
    """
    只重写指向 DSH 上游自身的绝对跳转，防止浏览器绕开网关直连上游。
    """
    key = next((k for k in headers if k.lower() == "location"), None)
    if not key:
        return
    loc = headers[key]
    if loc.startswith(DSH_ORIGIN):
        gateway_origin = str(request.base_url).rstrip("/")
        headers[key] = gateway_origin + loc[len(DSH_ORIGIN):]


async def _body_iterator(response: httpx.Response):
    """
    逐块透传上游已解码字节；finally 中关闭上游响应，确保连接正确释放。
    """
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()


async def probe_upstream() -> tuple[bool, float | None]:
    """
    探测 DSH 上游是否可达。返回 (up, latency_ms)。
    """
    start = time.perf_counter()
    try:
        async with _client.stream("GET", "/", timeout=2.0) as r:
            await r.aread()
            return True, round((time.perf_counter() - start) * 1000, 1)
    except httpx.HTTPError:
        return False, None


async def proxy_webui(request: Request, path: str) -> StreamingResponse:
    """
    将请求转发到 DSH webui 上游。所有响应统一走 StreamingResponse 流式透传。
    上游不可达时返回 502 Bad Gateway。
    """
    url = "/" + path if path else "/"
    if request.url.query:
        url += "?" + request.url.query

    body = await request.body()
    headers = _forward_headers(request)
    # DSH 会校验 Origin 与自身同源，否则返回 403。将浏览器带来的
    # 网关地址 Origin 重写为上游地址，Referer 一并重写保持一致。
    _set_header(headers, "origin", DSH_ORIGIN)
    _set_header(headers, "referer", DSH_ORIGIN + "/")

    try:
        upstream = await _client.send(
            _client.build_request(request.method, url, headers=headers, content=body),
            stream=True,
        )
    except httpx.HTTPError as e:
        logger.warning("DSH 上游不可达 %s: %r", url, e)
        return JSONResponse(
            status_code=502,
            content={"code": 0, "msg": f"DSH upstream unavailable: {e}"},
        )

    resp_headers = _response_headers(upstream)
    _rewrite_location(resp_headers, request)
    return StreamingResponse(
        _body_iterator(upstream),
        status_code=upstream.status_code,
        headers=resp_headers,
    )
