"""Provider-gated reasoning replay (MiniMax, 2026-11-02).

MiniMax documents that the reasoning chain must be sent back: the complete
assistant message -- ``reasoning_content`` included -- has to be appended to
the conversation history, or the model loses the thread (measured: 1/10 recall
dropped vs 7/10 replayed).  ``providers.<name>.reasoning_replay: true`` turns
the protocol on for ONE provider:

* every request to that provider carries ``reasoning_split: true``, so the
  thinking arrives in ``reasoning_content`` for every MiniMax model (M3.1-
  Flash, M3, M2.x -- accepted by all six);
* ``_run_multiturn`` replays each turn's reasoning on the assistant message;
* the tool loop keeps its reasoning and answers a tool call the model made at
  a prose step, so the next request stays well-formed.

OFF (the default) every other provider's request bytes are unchanged -- the
golden capture below is the proof: deepseek and studioforge bodies are
byte-identical to the ones the pre-change runner sent.

A provider pointed at MiniMax's OpenAI-compatible host gets the replay by
default (the built-in default; ``reasoning_replay: false`` in models.yaml
turns it off, ``true`` turns it on for any other provider).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import api, runner
from crucibleforge.providers import clear_cache, get_provider

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "fixtures" / "golden_provider_bodies.json"


# ------------------------------------------------------------ fake server

class _FakeResponse:
    def __init__(self, lines):
        self._lines = lines
        self.status_code = 200
        self.headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_text(self):
        for line in self._lines:
            yield line

    def read(self):
        return b"".join(line.encode() for line in self._lines)


class _FakeClient:
    """Records the request bodies and answers with a queued SSE script."""

    queue: list = []
    bodies: list = []

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, **kw):
        _FakeClient.bodies.append(kw.get("json"))
        return _FakeResponse(_FakeClient.queue.pop(0))


def _sse(content: str, reasoning: str = "", model: str = "m") -> list[str]:
    chunks: list[dict] = []
    if reasoning:
        chunks.append({"model": model, "choices": [{"delta": {"reasoning_content": reasoning}}]})
    chunks.append({"model": model,
                   "choices": [{"delta": {"content": content}, "finish_reason": "stop"}]})
    chunks.append({"model": model, "choices": [],
                   "usage": {"prompt_tokens": 7, "completion_tokens": 5,
                             "completion_tokens_details": {"reasoning_tokens": 3}}})
    return [f"data: {json.dumps(c)}\n" for c in chunks] + ["data: [DONE]\n"]


def _tool_sse(name: str, arguments: str, call_id: str = "call_1",
              model: str = "m") -> list[str]:
    chunks = [
        {"model": model, "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": call_id,
             "function": {"name": name, "arguments": arguments}}]}}]},
        {"model": model, "choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"model": model, "choices": [],
         "usage": {"prompt_tokens": 7, "completion_tokens": 5,
                   "completion_tokens_details": {"reasoning_tokens": 3}}},
    ]
    return [f"data: {json.dumps(c)}\n" for c in chunks] + ["data: [DONE]\n"]


# --------------------------------------------------------------- fixtures

PROVIDER_CFGS = {
    "deepseek": {"type": "openai", "base_url": "https://api.deepseek.com/v1",
                 "api_key_env": "DEEPSEEK_API_KEY", "concurrency": 4},
    "studioforge": {"type": "studioforge", "base_url": "http://rig.invalid/v1",
                    "api_key": "k", "concurrency": 1},
    "minimax": {"type": "openai", "base_url": "https://api.minimax.io/v1",
                "api_key_env": "MINIMAX_API_KEY", "concurrency": 8,
                "reasoning_replay": True},
    # the same provider WITHOUT the switch: the built-in default turns the
    # replay on for a MiniMax host
    "minimax_default": {"type": "openai", "base_url": "https://api.minimax.io/v1",
                        "api_key_env": "MINIMAX_API_KEY", "concurrency": 8},
}


def _cfg(provider: str) -> dict:
    return {"providers": {provider: dict(PROVIDER_CFGS[provider])},
            "defaults": {}, "models": [], "judge": {"candidates": []}}


def _ctx(provider: str):
    cfg = _cfg(provider)
    entry = {"name": "m", "model_id": "m", "provider": provider,
             "context_length": 32768}
    prov = get_provider(cfg, provider)
    return cfg, entry, runner._Ctx(cfg, entry, prov, 32768)


def _multi_case() -> dict:
    return {"id": "S1", "category": "rpdeep", "max_tokens": 64,
            "system": "you are a test",
            "turns": ["first", "second"]}


def _tool_case() -> dict:
    """Three steps: a tool step, a prose step, and another prose step. The
    third step is what makes the history after the prose step observable."""
    return {"id": "T1", "category": "tooluse", "max_tokens": 64,
            "system": "you are a test",
            "tools": [{"type": "function", "function": {
                "name": "get_weather", "description": "weather",
                "parameters": {"type": "object", "properties": {}}}}],
            "tool_script": [
                {"user": "weather in tokyo?", "expect_tool": "get_weather",
                 "required_args": {"location": ["tokyo"]}, "tool_result": "sunny"},
                {"expect_no_tool": True, "answer_contains": ["rain"]},
                {"expect_no_tool": True, "answer_contains": ["tomorrow"]},
            ]}


def _drive(ctx, case, tool_calls: bool = False) -> None:
    """Run one multi-turn case (or one tool-loop case) against the fake."""
    if case.get("tool_script"):
        _FakeClient.queue.append(_tool_sse("get_weather", '{"location": "tokyo"}',
                                           call_id="call_1"))
        if tool_calls:
            # the model calls a tool at the PROSE step (expect_no_tool)
            _FakeClient.queue.append(_tool_sse("get_weather", '{"location": "kyoto"}',
                                               call_id="call_2"))
        else:
            _FakeClient.queue.append(_sse("It will rain tomorrow.", "prose?"))
        _FakeClient.queue.append(_sse("Tomorrow it rains.", "prose?"))
        runner._run_tool_loop(case, {}, ctx, 64, 0.0, 42)
        return
    _FakeClient.queue.append(_sse("first reply", "thinking one"))
    _FakeClient.queue.append(_sse("second reply", "thinking two"))
    runner._run_multiturn(case, {}, ctx, 64, 0.0, 1.0, 1)


@pytest.fixture(autouse=True)
def _fake_server(monkeypatch):
    _FakeClient.queue = []
    _FakeClient.bodies = []
    monkeypatch.setattr(api.httpx, "Client", _FakeClient)
    clear_cache()
    yield
    clear_cache()


# --------------------------------------------------------------- capture

def capture_golden(mp) -> dict:
    """The request bodies deepseek / studioforge sent, before and after the
    change, must be identical -- this is the capture the golden file holds."""
    out: dict = {}
    for provider in ("deepseek", "studioforge"):
        _FakeClient.queue = []
        _FakeClient.bodies = []
        cfg, entry, ctx = _ctx(provider)
        mp.setattr(api.httpx, "Client", _FakeClient)
        _drive(ctx, _multi_case())
        _drive(ctx, _tool_case())
        _drive(ctx, _tool_case(), tool_calls=True)
        out[provider] = _FakeClient.bodies
    return out


# ----------------------------------------------------------------- tests

def test_other_providers_send_byte_identical_bodies(monkeypatch):
    """The switch is provider-gated: deepseek and studioforge keep the exact
    request bytes the pre-change runner sent."""
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert capture_golden(monkeypatch) == golden


def test_the_switch_defaults_to_off():
    cfg, _entry, ctx = _ctx("deepseek")
    assert ctx.reasoning_replay is False
    assert get_provider(cfg, "deepseek").reasoning_replay is False


def test_a_minimax_host_gets_the_replay_by_default():
    """The built-in default: a provider pointed at MiniMax's API speaks the
    chain protocol, so it gets the replay without a models.yaml edit."""
    cfg, _entry, ctx = _ctx("minimax_default")
    assert ctx.reasoning_replay is True
    assert get_provider(cfg, "minimax_default").reasoning_replay is True
    # ... and the switch still wins over the default
    from crucibleforge.providers import reasoning_replay_default
    cfg2 = _cfg("deepseek")
    cfg2["providers"]["deepseek"]["reasoning_replay"] = True
    assert reasoning_replay_default(cfg2["providers"]["deepseek"]) is True


def test_minimax_requests_carry_reasoning_split():
    _cfg_m, _entry, ctx = _ctx("minimax")
    _drive(ctx, _multi_case())
    assert _FakeClient.bodies
    for body in _FakeClient.bodies:
        assert body["reasoning_split"] is True


def test_minimax_replays_the_reasoning_on_turn_two():
    _cfg_m, _entry, ctx = _ctx("minimax")
    _drive(ctx, _multi_case())
    turn2 = _FakeClient.bodies[1]
    assistant = [m for m in turn2["messages"] if m["role"] == "assistant"]
    assert assistant, turn2["messages"]
    assert assistant[0]["reasoning_content"] == "thinking one"
    assert assistant[0]["content"] == "first reply"


def test_minimax_replays_the_reasoning_in_the_tool_loop():
    _cfg_m, _entry, ctx = _ctx("minimax")
    _drive(ctx, _tool_case())
    # the third request carries the prose step's assistant turn back, with
    # its reasoning
    assistant = [m for m in _FakeClient.bodies[2]["messages"]
                 if m["role"] == "assistant"]
    assert assistant and assistant[-1]["reasoning_content"] == "prose?"
    assert assistant[-1]["content"] == "It will rain tomorrow."


def test_the_judged_conversation_never_carries_the_reasoning():
    """The judge reads ``row["conversation"]`` -- it must stay content-only
    (the CoT is the model's context, not the answer)."""
    _cfg_m, _entry, ctx = _ctx("minimax")
    _FakeClient.queue.append(_sse("first reply", "thinking one"))
    _FakeClient.queue.append(_sse("second reply", "thinking two"))
    rows = runner._run_multiturn(_multi_case(), {}, ctx, 64, 0.0, 1.0, 1)
    convo = rows[-1]["conversation"]
    assert convo
    assert all("reasoning_content" not in m for m in convo)


def test_the_no_tool_step_keeps_a_well_formed_history():
    """The model called a tool at a prose step: the calls stay on the
    assistant message and EVERY call is answered, so the next request is
    well-formed (an unanswered tool_call is a malformed request)."""
    _cfg_m, _entry, ctx = _ctx("minimax")
    _drive(ctx, _tool_case(), tool_calls=True)
    assert len(_FakeClient.bodies) >= 3
    follow_up = _FakeClient.bodies[2]["messages"]
    # the prose step's call (call_2) survives on the assistant turn ...
    with_calls = [m for m in follow_up if m.get("tool_calls")]
    ids = {c["id"] for m in with_calls for c in m["tool_calls"]}
    assert "call_2" in ids, follow_up
    # ... and is answered
    answered = {m["tool_call_id"] for m in follow_up if m["role"] == "tool"}
    assert "call_2" in answered, follow_up
    for call in with_calls:
        for c in call["tool_calls"]:
            assert c["id"] in answered


def test_the_no_tool_step_is_unchanged_for_other_providers():
    """Without the switch the prose step keeps its old history: the call is
    dropped and the assistant message carries the content only."""
    _cfg_d, _entry, ctx = _ctx("deepseek")
    _drive(ctx, _tool_case(), tool_calls=True)
    follow_up = _FakeClient.bodies[2]["messages"]
    ids = {c["id"] for m in follow_up if m.get("tool_calls")
           for c in m["tool_calls"]}
    assert "call_2" not in ids, follow_up
    answered = {m["tool_call_id"] for m in follow_up if m["role"] == "tool"}
    assert "call_2" not in answered, follow_up
