"""WS 下行帧 usage 提取（_extract_assistant_message）的单元测试。"""
import json

from app.services.ws_proxy import _extract_assistant_message


def _frame(usage=None, content=None):
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
            "sessionId": "s1",
            "event": {"type": "assistant/message", "data": data},
        },
    })


class TestExtractAssistantMessage:
    def test_real_usage_camel_case(self):
        # 返回 (input, output)；input = inputTokens + cacheReadTokens
        frame = _frame(usage={"inputTokens": 120, "outputTokens": 34})
        assert _extract_assistant_message(frame) == (120, 34)

    def test_input_includes_cache_read_tokens(self):
        # input 口径含缓存读取的上下文：109 + 8064 = 8173
        frame = _frame(usage={
            "inputTokens": 109,
            "outputTokens": 123,
            "cacheReadTokens": 8064,
            "reasoningTokens": 40,
        })
        assert _extract_assistant_message(frame) == (8173, 123)

    def test_input_cache_read_snake_case(self):
        frame = _frame(usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "cache_read_input_tokens": 90,
        })
        assert _extract_assistant_message(frame) == (100, 5)

    def test_missing_usage_falls_back_to_text_estimate(self):
        # 无 usage：对 message.content 文本估算输出（input 计 0）
        frame = _frame(usage=None, content=[{"type": "text", "text": "你好"}])
        assert _extract_assistant_message(frame) == (0, 2)

    def test_missing_usage_string_content(self):
        frame = _frame(usage=None, content="hello")
        assert _extract_assistant_message(frame) == (0, round(1 * 1.3))

    def test_non_json_returns_none(self):
        assert _extract_assistant_message("not json") is None

    def test_wrong_frame_type_returns_none(self):
        assert _extract_assistant_message(json.dumps({"type": "other"})) is None

    def test_wrong_event_type_returns_none(self):
        frame = json.dumps({
            "type": "server-request",
            "payload": {"type": "session/event", "event": {"type": "session/idle", "data": {}}},
        })
        assert _extract_assistant_message(frame) is None

    def test_zero_usage_returns_zero(self):
        frame = _frame(usage={"inputTokens": 0, "outputTokens": 0})
        assert _extract_assistant_message(frame) == (0, 0)
