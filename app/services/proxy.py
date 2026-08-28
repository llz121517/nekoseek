# app/services/proxy.py
"""
HTTP 透明反代：DSH webui 全部请求透传。

对顶层导航的 HTML 响应（sec-fetch-dest=document）缓冲并注入用户信息面板；
其余请求（SSE、JS、CSS、XHR 片段等）一律流式透传。

配额计量（输入）：仅拦截 DSH 的 RPC prompt 端点（POST /api/session.prompt
与 /api/subagent.prompt），对 content[].text 粗略分词估算；其余请求不计。
输出计量走 WS 下行帧里的真实 usage（见 ws_proxy.py），HTTP 侧不重复记。
"""
import json
import logging
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import DSH_UPSTREAM, DSH_ORIGIN
from app.core import quota, tokenize
from app.services.inject import inject_panel_tags

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

# DSH RPC prompt 端点路径后缀（见 dsh-client-connection/lib/types/api-path.d.ts
# 与 dsh-host-apiproxy/lib/types/api/rpc-map.d.ts 的 session.prompt / subagent.prompt）。
_PROMPT_PATH_SUFFIXES = ("/api/session.prompt", "/api/subagent.prompt")


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


def _account_prompt_input(user: dict, body: bytes) -> None:
    """
    对 DSH RPC prompt 请求体做输入 token 粗略估算并记账。
    解析失败静默跳过，不影响代理链路。
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return
    if not isinstance(payload, dict):
        return
    # 兼容两种封装：裸 {content:[...]} 与 RPC 信封 {payload:{content:[...]}}
    content = payload.get("content")
    if not isinstance(content, list):
        inner = payload.get("payload")
        if isinstance(inner, dict):
            content = inner.get("content")
    if not isinstance(content, list):
        return
    tokens = tokenize.estimate_prompt_parts(content)
    if tokens > 0:
        quota.record_usage(user["id"], input_tokens=tokens)


async def proxy_webui(request: Request, path: str, user: dict | None = None) -> Response:
    """
    将请求转发到 DSH webui 上游。
    顶层导航 HTML 缓冲后注入用户信息面板；其余响应流式透传。
    上游不可达时返回 502 Bad Gateway。

    user 已登录时，对 RPC prompt 端点估算输入 token 并记账（输出端走 WS 真实 usage）。
    """
    url = "/" + path if path else "/"
    if request.url.query:
        url += "?" + request.url.query

    body = await request.body()
    if user and request.method == "POST" and url.split("?", 1)[0].endswith(_PROMPT_PATH_SUFFIXES):
        _account_prompt_input(user, body)

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

    inject_html = None
    if _should_inject(request, upstream.headers.get("content-type", "")):
        try:
            inject_html = inject_panel_tags(
                (await upstream.aread()).decode(
                    upstream.charset_encoding or "utf-8", errors="replace"
                )
            )
        finally:
            await upstream.aclose()

    # 注入路径（HTML 缓冲改写）与流式路径共用的响应头处理
    resp_headers = _response_headers(upstream)
    _rewrite_location(resp_headers, request)
    if inject_html is not None:
        return Response(
            content=inject_html,
            status_code=upstream.status_code,
            media_type="text/html",
            headers=resp_headers,
        )
    return StreamingResponse(
        _body_iterator(upstream),
        status_code=upstream.status_code,
        headers=resp_headers,
    )


def _should_inject(request: Request, content_type: str) -> bool:
    """
    只对顶层导航的 HTML 文档注入面板脚本。
    DSH 是 SPA，XHR/fetch 拉取的 HTML 片段 sec-fetch-dest 为 empty，
    注入到片段里会导致脚本重复执行，必须排除。
    """
    if request.method != "GET":
        return False
    if "text/html" not in content_type:
        return False
    dest = request.headers.get("sec-fetch-dest")
    if dest is not None:
        return dest == "document"
    # 老浏览器无 sec-fetch-* 头时降级判断
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return False
    return "text/html" in request.headers.get("accept", "")
