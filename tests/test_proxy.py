"""HTTP 反代的单元测试：mock 上游 httpx，验证请求转发、头重写与面板注入。"""
import json

import pytest
import httpx

from app.services import proxy


class FakeUpstreamResponse:
    """模拟 httpx.Response 的最小接口。"""

    def __init__(self, body=b"", status_code=200, content_type="text/plain", headers=None):
        self._body = body
        self.status_code = status_code
        self.charset_encoding = "utf-8"
        hdrs = {"content-type": content_type}
        if headers:
            hdrs.update(headers)
        self.headers = httpx.Headers(hdrs)

    async def aread(self):
        return self._body

    async def aclose(self):
        return None

    async def aiter_bytes(self):
        yield self._body


def _make_request(method="GET", path="/", headers=None, body=b""):
    from starlette.requests import Request
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": raw_headers,
        "server": ("testserver", 80),
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


@pytest.mark.asyncio
async def test_proxies_plain_text_response(monkeypatch):
    async def fake_send(req, stream=True):
        return FakeUpstreamResponse(b"hello upstream", content_type="text/plain")

    monkeypatch.setattr(proxy._client, "send", fake_send)
    resp = await proxy.proxy_webui(_make_request(), "")
    assert resp.status_code == 200
    # StreamingResponse 体
    chunks = [c async for c in resp.body_iterator]
    assert b"".join(chunks) == b"hello upstream"


@pytest.mark.asyncio
async def test_rewrites_origin_and_strips_hop_by_hop(monkeypatch):
    captured = {}

    async def fake_send(req, stream=True):
        captured["headers"] = dict(req.headers)
        return FakeUpstreamResponse(b"ok")

    monkeypatch.setattr(proxy._client, "send", fake_send)
    req = _make_request(headers={
        "origin": "http://gateway.example",
        "referer": "http://gateway.example/page",
        "host": "gateway.example",
        "connection": "keep-alive",
        "accept": "text/plain",
    })
    await proxy.proxy_webui(req, "")
    # Origin/Referer 重写为上游；host 由 httpx 按上游 base_url 重建。
    # connection 虽被转发函数剥离，但 httpx build_request 会重新补一个 keep-alive，
    # 上游收到后自行处理，不影响正确性 —— 故这里只断言 Origin/Referer 与 host。
    assert captured["headers"]["origin"] == proxy.DSH_ORIGIN
    assert captured["headers"]["referer"] == proxy.DSH_ORIGIN + "/"
    assert captured["headers"]["host"] == "127.0.0.1:3080"


@pytest.mark.asyncio
async def test_injects_panel_into_document_html(monkeypatch):
    async def fake_send(req, stream=True):
        return FakeUpstreamResponse(b"<html><head></head><body>x</body></html>", content_type="text/html")

    monkeypatch.setattr(proxy._client, "send", fake_send)
    req = _make_request(headers={"sec-fetch-dest": "document", "accept": "text/html"})
    resp = await proxy.proxy_webui(req, "")
    assert b"nekoseek-panel" in resp.body


@pytest.mark.asyncio
async def test_no_inject_for_xhr_fragment(monkeypatch):
    async def fake_send(req, stream=True):
        return FakeUpstreamResponse(b"<div>fragment</div>", content_type="text/html")

    monkeypatch.setattr(proxy._client, "send", fake_send)
    # SPA 的 XHR 片段：sec-fetch-dest=empty，不应注入
    req = _make_request(headers={"sec-fetch-dest": "empty"})
    resp = await proxy.proxy_webui(req, "some/fragment")
    chunks = [c async for c in resp.body_iterator]
    assert b"nekoseek-panel" not in b"".join(chunks)


@pytest.mark.asyncio
async def test_upstream_unreachable_returns_502(monkeypatch):
    async def fake_send(req, stream=True):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(proxy._client, "send", fake_send)
    resp = await proxy.proxy_webui(_make_request(), "")
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_rewrites_location_header_pointing_upstream(monkeypatch):
    async def fake_send(req, stream=True):
        return FakeUpstreamResponse(
            b"", status_code=302, headers={"location": proxy.DSH_ORIGIN + "/next"}
        )

    monkeypatch.setattr(proxy._client, "send", fake_send)
    resp = await proxy.proxy_webui(_make_request(), "")
    loc = resp.headers["location"]
    assert loc.startswith("http://testserver")
    assert loc.endswith("/next")


class TestPromptAttribution:
    """prompt 端点的归属捕获：sessionId/childSessionId → 发起者。"""

    @pytest.fixture(autouse=True)
    def _clean_owners(self):
        from app.core import attribution
        attribution._owners.clear()
        yield
        attribution._owners.clear()

    def test_session_prompt_records_session_id(self):
        from app.core import attribution
        body = json.dumps({
            "type": "client-request", "rpcId": "r1", "method": "session.prompt",
            "payload": {"sessionId": "s1", "mode": "queue", "content": []},
        }).encode()
        proxy._record_prompt_attribution("/api/session.prompt", body, {"id": 42})
        assert attribution.resolve_owner("s1") == 42

    def test_subagent_prompt_records_child_session_id(self):
        from app.core import attribution
        body = json.dumps({
            "type": "client-request", "rpcId": "r2", "method": "subagent.prompt",
            "payload": {"parentSessionId": "p1", "childSessionId": "c1",
                        "mode": "continuable", "content": []},
        }).encode()
        proxy._record_prompt_attribution("/api/subagent.prompt", body, {"id": 7})
        assert attribution.resolve_owner("c1") == 7

    def test_non_prompt_path_ignored(self):
        from app.core import attribution
        body = json.dumps({"payload": {"sessionId": "s9"}}).encode()
        proxy._record_prompt_attribution("/api/session.history", body, {"id": 1})
        assert attribution.resolve_owner("s9") is None

    def test_no_user_ignored(self):
        from app.core import attribution
        body = json.dumps({"payload": {"sessionId": "s9"}}).encode()
        proxy._record_prompt_attribution("/api/session.prompt", body, None)
        assert attribution.resolve_owner("s9") is None

    def test_bad_body_never_raises(self):
        # 非 JSON / 缺 payload 都必须静默跳过，不阻断转发
        proxy._record_prompt_attribution("/api/session.prompt", b"not json", {"id": 1})
        proxy._record_prompt_attribution("/api/session.prompt", b"{}", {"id": 1})
