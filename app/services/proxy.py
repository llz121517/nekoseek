# app/services/proxy.py
"""
HTTP 透明反代：DSH webui 全部请求透传。

对顶层导航的 HTML 响应（sec-fetch-dest=document）缓冲并注入用户信息面板；
其余请求（SSE、JS、CSS、XHR 片段等）一律流式透传。

配额计量：输入与输出统一走网关常驻 mux 消费者（usage_meter.py）里的真实
usage；HTTP 侧不再做任何计量，只在 prompt 端点记录 sessionId → 发起者的
归属映射（attribution.py），供计量帧按 sessionId 找回真正发起者。
"""
import json
import logging
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from app.config import DSH_UPSTREAM, DSH_ORIGIN
from app.core import attribution
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
# 配额预检（main.py）与用量归属捕获（下方 _record_prompt_attribution）都以此识别。
_PROMPT_PATH_SUFFIXES = ("/api/session.prompt", "/api/subagent.prompt")


def _record_prompt_attribution(url: str, body: bytes, user: dict | None) -> None:
    """
    在 prompt 端点上记录 sessionId → 发起者 的归属映射（供 usage_meter 记账）。

    DSH 协议规定 prompt 只走 HTTP（WS 下行是纯推送），这是唯一能捕获"谁发起"
    的位置。body 为 client-request 封套：
      session.prompt:  payload.sessionId
      subagent.prompt: payload.childSessionId（continuable 子代理的
                       assistant/message 帧带的是 childSessionId）
    任何解析异常都静默跳过——归属捕获绝不能阻断转发。
    """
    if user is None or not url.split("?", 1)[0].endswith(_PROMPT_PATH_SUFFIXES):
        return
    try:
        payload = json.loads(body).get("payload") or {}
        for key in ("sessionId", "childSessionId"):
            sid = payload.get(key)
            if isinstance(sid, str) and sid:
                attribution.record_prompt_session(sid, user["id"])
    except Exception:  # noqa: BLE001
        pass

# 局域网/反代访问下 settings mirror 降级的修复（非侵入，仅改线上字节，不动磁盘）。
# DSH 前端的 settings mirror 用 connection.isLoopback 选 host（持久化到服务端）/
# memory（只读，恒 undefined）模式；isLoopback 仅看 location.hostname 是否 loopback。
# 经局域网 IP（http://192.168.x.x）访问时误判为 false → memory 模式 → 模型设置报
# "settings are unavailable in this browser"，内测声明等设置也无法持久化。
# 此处把 dsh-client-connection 浏览器 bundle 里的 isLoopback 判定改写为额外认
# window.__DSH_LOCAL_APP__（由 inject.py 的 polyfill 在 <head> 起始置位）。
# 参考 deepseek-harness-fpk issue #2。
_CONNECTION_BUNDLE_PATH = "/plugins/@deepseek-ai/dsh-client-connection/client.js"
_ISLOOPBACK_ANCHOR = b"isLoopbackHostname(pageLocation.hostname)"
_ISLOOPBACK_PATCHED = (
    b"(isLoopbackHostname(pageLocation.hostname)"
    b" || (typeof window !== 'undefined' && window.__DSH_LOCAL_APP__ === true))"
)


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


async def proxy_webui(request: Request, path: str, user: dict | None = None) -> Response:
    """
    将请求转发到 DSH webui 上游。
    顶层导航 HTML 缓冲后注入用户信息面板；其余响应流式透传。
    上游不可达时返回 502 Bad Gateway。

    输入/输出 token 计量统一由 usage_meter（网关常驻 mux 消费者）负责；
    本函数仅在 prompt 端点记录归属映射，不做任何计量。
    """
    url = "/" + path if path else "/"
    if request.url.query:
        url += "?" + request.url.query

    body = await request.body()
    _record_prompt_attribution(url, body, user)

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

    # dsh-client-connection bundle：缓冲改写 isLoopback 判定（修局域网 settings 降级）。
    # 仅该路径的 GET 200；缓冲约 354KB，一次性改写后整体回传，不走流式。
    if _should_patch_loopback(request, url, upstream.status_code):
        try:
            patched = _patch_loopback_body(await upstream.aread())
        finally:
            await upstream.aclose()
        # 缓冲改写后长度变化，剔除上游的 content-length，由 Starlette 重算。
        resp_headers = {
            k: v for k, v in resp_headers.items() if k.lower() != "content-length"
        }
        return Response(
            content=patched,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/javascript"),
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


def _should_patch_loopback(request: Request, url: str, status_code: int) -> bool:
    """
    是否对 dsh-client-connection 浏览器 bundle 做 isLoopback 改写。

    仅匹配该 bundle 路径的 GET 200 响应；其余（SSE、其它 JS、非 200）一律流式透传。
    """
    return (
        request.method == "GET"
        and status_code == 200
        and url.split("?", 1)[0] == _CONNECTION_BUNDLE_PATH
    )


def _patch_loopback_body(body: bytes) -> bytes:
    """
    把 bundle 内的 isLoopback 判定改写为额外认 window.__DSH_LOCAL_APP__。

    锚点子串预期恰好出现一次：命中则替换；0 次（上游已改版/已自带修复）或多次
    都原样返回并记告警——既不静默失效，也不误改。改写只影响线上字节，不碰磁盘文件。
    """
    count = body.count(_ISLOOPBACK_ANCHOR)
    if count != 1:
        logger.warning(
            "dsh-client-connection bundle 的 isLoopback 锚点出现 %d 次（预期 1 次），"
            "跳过改写；请检查上游版本是否已变更",
            count,
        )
        return body
    return body.replace(_ISLOOPBACK_ANCHOR, _ISLOOPBACK_PATCHED)
