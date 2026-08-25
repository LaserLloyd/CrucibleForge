"""Tests for the streaming client's parsing/metrics logic via a fake server.

No real LM Studio: we monkeypatch httpx.Client.stream with a canned SSE body
so the wrong-model guard, usage extraction, and tok/s math are all exercised.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import pytest

from crucibleforge import api


class _FakeResponse:
    def __init__(self, lines, status=200):
        self._lines = lines
        self.status_code = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_text(self):
        for l in self._lines:
            yield l

    def read(self):
        return b"".join(l.encode() for l in self._lines)


class _FakeClient:
    def __init__(self, lines, status=200):
        self._lines = lines
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        return _FakeResponse(self._lines, self._status)


def _sse(*objs):
    return [f"data: {json.dumps(o)}\n" for o in objs] + ["data: [DONE]\n"]


def _patch(monkeypatch, lines, status=200):
    monkeypatch.setattr(api.httpx, "Client", lambda *a, **k: _FakeClient(lines, status))


def test_happy_path_usage_and_model(monkeypatch):
    lines = _sse(
        {"model": "m1", "choices": [{"delta": {"content": "Hello"}}]},
        {"model": "m1", "choices": [{"delta": {"content": " world"}, "finish_reason": "stop"}]},
        {"model": "m1", "choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                                                 "completion_tokens_details": {"reasoning_tokens": 0}}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m1", [{"role": "user", "content": "hi"}], max_tokens=32)
    assert r.response_text == "Hello world"
    assert r.completion_tokens == 5 and r.prompt_tokens == 10
    assert r.finish_reason == "stop"
    assert r.served_model == "m1"
    assert r.tok_per_s is not None and r.tok_per_s > 0


def test_wrong_model_raises(monkeypatch):
    lines = _sse({"model": "SOMETHING-ELSE", "choices": [{"delta": {"content": "hi"}}]},
                 {"model": "SOMETHING-ELSE", "choices": [],
                  "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    _patch(monkeypatch, lines)
    with pytest.raises(api.WrongModelError):
        api.stream_chat("http://x/v1", "k", "requested-model",
                        [{"role": "user", "content": "hi"}], max_tokens=8)


def test_reasoning_channel_captured(monkeypatch):
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"reasoning_content": "thinking..."}}]},
        {"model": "m", "choices": [{"delta": {"content": "42"}, "finish_reason": "stop"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2,
                                                "completion_tokens_details": {"reasoning_tokens": 4}}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=32)
    assert r.reasoning_text == "thinking..."
    assert r.response_text == "42"
    assert r.reasoning_tokens == 4
    assert r.scoreable_text() == "42"


def test_scoreable_falls_back_to_reasoning(monkeypatch):
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"reasoning_content": "the answer is 7"}, "finish_reason": "stop"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 5}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=32)
    assert r.response_text == ""
    assert r.scoreable_text() == "the answer is 7"


def test_tool_call_delta_merge(monkeypatch):
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "abc", "function": {"name": "get_wea", "arguments": '{"loc'}}]}}]},
        {"model": "m", "choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"name": "ther", "arguments": 'ation": "Paris"}'}}]},
            "finish_reason": "tool_calls"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 8}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "weather?"}], max_tokens=64)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0]["name"] == "get_weather"
    assert json.loads(r.tool_calls[0]["arguments"]) == {"location": "Paris"}


def test_http_error_raises_transport(monkeypatch):
    _patch(monkeypatch, ["some error body\n"], status=500)
    with pytest.raises(api.TransportError):
        api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=8)


def test_sse_error_payload_raises(monkeypatch):
    _patch(monkeypatch, _sse({"error": {"message": "model not found"}}))
    with pytest.raises(api.TransportError):
        api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=8)


def test_no_sse_body_raises(monkeypatch):
    _patch(monkeypatch, ["not an sse line\n", "another\n"])
    with pytest.raises(api.TransportError):
        api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=8)


def test_single_token_no_tok_per_s(monkeypatch):
    # 1 completion token => tok/s undefined (was infinity in v1).
    lines = _sse({"model": "m", "choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]},
                 {"model": "m", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 1}})
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert r.tok_per_s is None


def test_fail_closed_on_missing_model(monkeypatch):
    # Real completion but server never told us the model -> must raise.
    lines = _sse({"choices": [{"delta": {"content": "hello"}, "finish_reason": "stop"}]},
                 {"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    _patch(monkeypatch, lines)
    with pytest.raises(api.WrongModelError):
        api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "hi"}], max_tokens=8)


# --------------------------------------- thinking-tag strip at the boundary
def test_thinking_tags_stripped_from_response(monkeypatch):
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"content": "<think>"}}]},
        {"model": "m", "choices": [{"delta": {"content": " hmm"}}]},
        {"model": "m", "choices": [{"delta": {"content": "</think>42"}, "finish_reason": "stop"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 6}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=32)
    assert r.response_text == "42"


def test_thinking_tags_unclosed_drop_to_end(monkeypatch):
    # truncated generation: reasoning opened but never closed -> no answer
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"content": "(think) let me"}}]},
        {"model": "m", "choices": [{"delta": {"content": " keep going"}, "finish_reason": "length"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 6}},
    )
    _patch(monkeypatch, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=32)
    assert r.response_text == ""
    assert r.scoreable_text() == ""


# ------------------------------------- reasoning_format body param (config)
class _CaptureClient:
    """_FakeClient that records the request json body for assertions."""

    def __init__(self, lines, captured, status=200):
        self._lines = lines
        self._captured = captured
        self._status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        self._captured["json"] = kw.get("json")
        return _FakeResponse(self._lines, self._status)


def _patch_capture(monkeypatch, captured, lines, status=200):
    monkeypatch.setattr(api.httpx, "Client",
                        lambda *a, **k: _CaptureClient(lines, captured, status))


def test_reasoning_format_sent_when_configured(monkeypatch):
    captured = {}
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    _patch_capture(monkeypatch, captured, lines)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}],
                        max_tokens=8, reasoning_format="deepseek")
    assert r.response_text == "ok"
    assert captured["json"]["reasoning_format"] == "deepseek"


def test_reasoning_format_absent_when_unset(monkeypatch):
    captured = {}
    lines = _sse(
        {"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
        {"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    _patch_capture(monkeypatch, captured, lines)
    api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=8)
    assert "reasoning_format" not in captured["json"]
