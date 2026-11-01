"""A stream the server closes before any finish_reason or usage is a
transport failure (retried, then an ERRORED row), never an answer: on
2026-10-29 a MiniMax CZ13 row came back empty this way and was scored 0."""
import httpx
import pytest

from crucibleforge import api


def _serve(monkeypatch, body: str):
    def handler(request):
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
    real = httpx.Client

    class _C(real):
        def __init__(self, *a, **k):
            k["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **k)
    monkeypatch.setattr(api.httpx, "Client", _C)


def _chunk(**delta):
    import json
    return "data: " + json.dumps({"model": "m", "choices": [{"delta": delta}]}) + "\n\n"


def test_a_stream_dropped_before_any_output_is_a_transport_error(monkeypatch):
    _serve(monkeypatch, ": keep-alive\n\ndata: {\"model\": \"m\", \"choices\": []}\n\n")
    with pytest.raises(api.TransportError, match="dropped"):
        api.stream_chat("http://x", "", "m", [], max_tokens=8)


def test_a_stream_dropped_mid_answer_is_a_transport_error(monkeypatch):
    _serve(monkeypatch, _chunk(reasoning_content="let me think") + _chunk(content="def f("))
    with pytest.raises(api.TransportError, match="dropped"):
        api.stream_chat("http://x", "", "m", [], max_tokens=8)


def test_a_finished_stream_is_an_answer(monkeypatch):
    import json
    done = "data: " + json.dumps({"model": "m", "choices": [{"delta": {}, "finish_reason": "stop"}]})
    _serve(monkeypatch, _chunk(content="42") + done + "\n\ndata: [DONE]\n\n")
    r = api.stream_chat("http://x", "", "m", [], max_tokens=8)
    assert r.response_text == "42" and r.finish_reason == "stop"


def test_usage_without_a_finish_reason_still_counts_as_finished(monkeypatch):
    import json
    tail = "data: " + json.dumps({"model": "m", "choices": [], "usage": {"completion_tokens": 1}})
    _serve(monkeypatch, _chunk(content="42") + tail + "\n\n")
    assert api.stream_chat("http://x", "", "m", [], max_tokens=8).response_text == "42"
