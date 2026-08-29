"""usage_meter 的帧解析（_extract_assistant_message）与归属记账（_handle_frame）测试。"""
import json

import pytest

from app.core import attribution, quota
from app.core.db import db_op
from app.services import usage_meter
from app.services.usage_meter import _extract_assistant_message, _handle_frame


def _frame(usage=None, content=None, session_id="s1"):
    data = {}
    if usage is not None:
        data["usage"] = usage
    if content is not None:
        data["message"] = {"content": content}
    return json.dumps({
        "type": "server-request",
        "rpcId": "r1",
        "method": "session/event",
        "payload": {
            "type": "session/event",
            "sessionId": session_id,
            "event": {"type": "assistant/message", "data": data},
        },
    })


class TestExtractAssistantMessage:
    def test_real_usage_camel_case(self):
        # 返回 (session_id, input, output)；input = inputTokens + cacheReadTokens
        frame = _frame(usage={"inputTokens": 120, "outputTokens": 34})
        assert _extract_assistant_message(frame) == ("s1", 120, 34)

    def test_input_includes_cache_read_tokens(self):
        # input 口径含缓存读取的上下文：109 + 8064 = 8173
        frame = _frame(usage={
            "inputTokens": 109,
            "outputTokens": 123,
            "cacheReadTokens": 8064,
            "reasoningTokens": 40,
        })
        assert _extract_assistant_message(frame) == ("s1", 8173, 123)

    def test_input_cache_read_snake_case(self):
        frame = _frame(usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_input_tokens": 90,
        })
        assert _extract_assistant_message(frame) == ("s1", 100, 5)

    def test_missing_usage_falls_back_to_text_estimate(self):
        # 无 usage：对 message.content 文本估算输出（input 计 0）
        frame = _frame(usage=None, content=[{"type": "text", "text": "你好"}])
        assert _extract_assistant_message(frame) == ("s1", 0, 2)

    def test_missing_usage_string_content(self):
        frame = _frame(usage=None, content="hello")
        assert _extract_assistant_message(frame) == ("s1", 0, round(1 * 1.3))

    def test_non_json_returns_none(self):
        assert _extract_assistant_message("not json") is None

    def test_wrong_frame_type_returns_none(self):
        assert _extract_assistant_message(json.dumps({"type": "other"})) is None

    def test_wrong_event_type_returns_none(self):
        frame = json.dumps({
            "type": "server-request",
            "payload": {"type": "session/event", "sessionId": "s1",
                        "event": {"type": "session/idle", "data": {}}},
        })
        assert _extract_assistant_message(frame) is None

    def test_missing_session_id_returns_none(self):
        frame = json.dumps({
            "type": "server-request",
            "payload": {"type": "session/event",
                        "event": {"type": "assistant/message",
                                  "data": {"usage": {"inputTokens": 1}}}},
        })
        assert _extract_assistant_message(frame) is None

    def test_zero_usage_returns_zero(self):
        frame = _frame(usage={"inputTokens": 0, "outputTokens": 0})
        assert _extract_assistant_message(frame) == ("s1", 0, 0)


@pytest.fixture(autouse=True)
def _clean_owners():
    attribution._owners.clear()
    yield
    attribution._owners.clear()


class TestBillFrameAttribution:
    """_handle_frame：归属命中记发起者，未命中只记全局池。"""

    def test_mapped_session_bills_owner(self, make_user):
        user = make_user()
        attribution.record_prompt_session("s1", user["id"])
        _handle_frame(_frame(usage={"inputTokens": 10, "outputTokens": 5}))
        ws = quota.current_window_start("day")
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 15
        assert db_op.get_usage(0, ws)["total_tokens"] == 15

    def test_unmapped_session_bills_global_only(self, make_user):
        stranger = make_user()
        _handle_frame(_frame(usage={"inputTokens": 10, "outputTokens": 5}))
        ws = quota.current_window_start("day")
        # 未归属：全局池记账，任何个人都不计
        assert db_op.get_usage(0, ws)["total_tokens"] == 15
        assert db_op.get_usage(stranger["id"], ws)["total_tokens"] == 0

    def test_disabled_owner_falls_back_to_global(self, make_user):
        user = make_user()
        attribution.record_prompt_session("s1", user["id"])
        db_op.update_user(user["id"], status=0)
        _handle_frame(_frame(usage={"inputTokens": 8, "outputTokens": 2}))
        ws = quota.current_window_start("day")
        assert db_op.get_usage(0, ws)["total_tokens"] == 10
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 0

    def test_zero_usage_skipped(self, make_user):
        user = make_user()
        attribution.record_prompt_session("s1", user["id"])
        _handle_frame(_frame(usage={"inputTokens": 0, "outputTokens": 0}))
        ws = quota.current_window_start("day")
        assert db_op.get_usage(0, ws)["total_tokens"] == 0
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 0


class TestHandleFrame:
    def test_unparseable_frames_ignored(self, make_user):
        user = make_user()
        attribution.record_prompt_session("s1", user["id"])
        for text in ("", "not json", '{"type":"other"}', "null"):
            _handle_frame(text)
        ws = quota.current_window_start("day")
        assert db_op.get_usage(0, ws)["total_tokens"] == 0
        assert db_op.get_usage(user["id"], ws)["total_tokens"] == 0
