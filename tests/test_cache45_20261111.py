"""Prompt caching for OpenAI-shape providers (MakoBench 4.5, 2026-11-11).

Cached-token parsing, cache-read pricing, append-only agentic history, per-case
routing keys and the spend controls on a non-Anthropic provider. A loopback
mock of POST /v1/chat/completions; no API call of any kind is made."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crucibleforge import api, codeenv, spend
from crucibleforge.api import RequestRejected, extract_cached_tokens
from crucibleforge.providers import (call_cost_usd, clear_cache, cost_usd, get_provider,
                                     cache_route_for)

MODEL = "mock-model"
PRICE = {"input": 1.0, "output": 4.0, "cache_read": 0.1}


class Mock:
    def __init__(self, usages):
        self.usages = list(usages)
        self.requests: list[tuple[dict, bytes]] = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                mock.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
                usage = mock.usages.pop(0) if mock.usages else mock.usages_last
                chunks = [
                    {"id": "c", "model": MODEL, "choices": [{"index": 0, "delta": {"content": "hi"}}]},
                    {"id": "c", "model": MODEL, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                    {"id": "c", "model": MODEL, "choices": [], "usage": usage}]
                payload = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks).encode() + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.usages_last = {"prompt_tokens": 1000, "completion_tokens": 10}
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def mock():
    made = []

    def make(*usages):
        m = Mock(usages)
        made.append(m)
        return m
    yield make
    for m in made:
        m.close()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    for k in ("CF_SPEND_CAP_USD", "CF_SPEND_JOURNAL", "CF_SPEND_ALLOW_CASES"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MOCK_KEY", "k")
    spend.reset()
    spend.clear_context()
    clear_cache()
    yield
    spend.reset()
    spend.clear_context()
    clear_cache()


def _prov(m, price=PRICE, **extra):
    cfg = {"providers": {"mock": {"type": "openai", "base_url": m.url, "api_key_env": "MOCK_KEY", "hosted": True,
                                  **extra}},
           "models": [{"name": MODEL, "provider": "mock", "model_id": MODEL, "price": price}]}
    return get_provider(cfg, "mock")


MSGS = [{"role": "user", "content": "Hi"}]


# ------------------------------------------------------------------ parsing

@pytest.mark.parametrize("usage,want", [
    ({"prompt_tokens": 900, "prompt_tokens_details": {"cached_tokens": 640}}, 640),   # OpenAI/xAI/MiniMax/OpenRouter
    ({"prompt_tokens": 900, "prompt_cache_hit_tokens": 512,
      "prompt_cache_miss_tokens": 388}, 512),                                          # DeepSeek
    ({"input_tokens_details": {"cached_tokens": 7}}, 7),                               # Responses shape
    ({"prompt_tokens": 9, "cached_tokens": 4}, 4),                                     # Moonshot (unverified)
    ({"prompt_tokens": 9, "prompt_tokens_details": {"cached_tokens": 0}}, 0),          # reported zero is a zero
    ({"prompt_tokens": 9, "prompt_tokens_details": None}, None),
    ({"prompt_tokens": 9}, None),
    ({"prompt_cache_hit_tokens": "x"}, None),
    (None, None),
])
def test_extract_cached_tokens(usage, want):
    assert extract_cached_tokens(usage) == want


def test_cached_tokens_land_on_the_result_and_prompt_stays_total(mock):
    m = mock({"prompt_tokens": 1000, "completion_tokens": 10,
              "prompt_tokens_details": {"cached_tokens": 800}})
    r = _prov(m).chat(MODEL, MSGS, max_tokens=16)
    assert r.prompt_tokens == 1000 and r.cache_read_tokens == 800
    assert r.cache_write_tokens is None


def test_deepseek_shape_through_the_wire(mock):
    m = mock({"prompt_tokens": 1000, "completion_tokens": 10,
              "prompt_cache_hit_tokens": 700, "prompt_cache_miss_tokens": 300})
    r = _prov(m).chat(MODEL, MSGS, max_tokens=16)
    assert r.prompt_tokens == 1000 and r.cache_read_tokens == 700


# ------------------------------------------------------------------ pricing

def test_cache_read_is_priced_at_the_declared_rate(mock):
    m = mock({"prompt_tokens": 1000, "completion_tokens": 100,
              "prompt_tokens_details": {"cached_tokens": 800}})
    r = _prov(m).chat(MODEL, MSGS, max_tokens=16)
    want = (200 * 1.0 + 800 * 0.1 + 100 * 4.0) / 1e6
    assert call_cost_usd({"price": PRICE}, r) == pytest.approx(want)
    assert spend.total() == pytest.approx(want)


def test_no_declared_cache_read_prices_at_list_not_claude_defaults(mock):
    price = {"input": 1.0, "output": 4.0}
    m = mock({"prompt_tokens": 1000, "completion_tokens": 100,
              "prompt_tokens_details": {"cached_tokens": 800}})
    r = _prov(m, price).chat(MODEL, MSGS, max_tokens=16)
    assert call_cost_usd({"price": price}, r) == pytest.approx((1000 * 1.0 + 100 * 4.0) / 1e6)
    # the native-Anthropic path (a write count is reported) keeps its defaults
    assert cost_usd({"price": price}, 1000, 0, 500, 300) == pytest.approx(
        (200 + 300 * 1.25 + 500 * 0.1) / 1e6)


def test_a_row_with_no_cache_field_prices_exactly_as_before():
    assert cost_usd({"price": PRICE}, 1000, 100) == pytest.approx((1000 + 400) / 1e6)


# --------------------------------------------------------- append-only history

def _grow(messages, turn, keep):
    """One agentic turn as the runner does it: compact, then (send), then append."""
    codeenv.compact(messages, 6, 10**9, keep_reasoning=keep)
    sent = json.dumps(codeenv.wire(messages), sort_keys=False)
    messages.append({"role": "assistant", "content": f"a{turn}", "reasoning_content": f"think {turn}",
                     "tool_calls": [{"id": f"t{turn}", "type": "function",
                                     "function": {"name": "read_file", "arguments": "{}"}}]})
    messages.append({"role": "tool", "tool_call_id": f"t{turn}", "content": f"out {turn}"})
    return sent


def test_request_n_plus_1_extends_request_n_byte_for_byte():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "task"}]
    sent = [_grow(msgs, i, keep=True) for i in range(5)]
    for a, b in zip(sent, sent[1:]):
        assert b.startswith(a[:-1])          # drop the closing ']' of the shorter list


def test_legacy_compaction_breaks_the_prefix_and_is_still_there():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "task"}]
    sent = [_grow(msgs, i, keep=False) for i in range(4)]
    assert not sent[3].startswith(sent[2][:-1])


def test_overflow_still_rewrites_even_with_keep_reasoning():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "task"}]
    for i in range(10):
        msgs.append({"role": "assistant", "content": "a", "reasoning_content": "r" * 50})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": "x" * 4000})
    codeenv.compact(msgs, 2, 4000, keep_reasoning=True)
    assert any(m.get("_stub") for m in msgs)      # the pressure stub still fires


def test_keep_reasoning_is_off_for_unpriced_rig_rows_and_on_for_metered(monkeypatch):
    from crucibleforge import runner
    assert runner.keeps_reasoning({"price": {"input": 1, "output": 2}}) is True
    assert runner.keeps_reasoning({}) is False


# -------------------------------------------------------------- routing keys

def test_route_defaults_by_host():
    assert cache_route_for("https://api.openai.com/v1", None) == ("body", "prompt_cache_key")
    assert cache_route_for("https://api.x.ai/v1", None) == ("header", "x-grok-conv-id")
    assert cache_route_for("https://openrouter.ai/api/v1", None) == ("body", "session_id")
    assert cache_route_for("https://api.deepseek.com/v1", None) is None
    assert cache_route_for("https://api.deepseek.com/v1", "body:foo") == ("body", "foo")
    assert cache_route_for("https://api.openai.com/v1", "off") is None


def _sent(m, i):
    h, b = m.requests[i]
    return h, json.loads(b)


def test_body_routing_key_is_stable_per_episode_and_distinct_per_case(mock):
    m = mock()
    p = _prov(m, cache_route="body:prompt_cache_key")
    spend.set_context(label="runA", case_id="c1", repeat=0)
    p.chat(MODEL, MSGS, max_tokens=8)
    p.chat(MODEL, MSGS, max_tokens=8)
    spend.set_context(label="runA", case_id="c1", repeat=1)
    p.chat(MODEL, MSGS, max_tokens=8)
    spend.set_context(label="runA", case_id="c2", repeat=0)
    p.chat(MODEL, MSGS, max_tokens=8)
    keys = [_sent(m, i)[1].get("prompt_cache_key") for i in range(4)]
    assert all(keys) and keys[0] == keys[1]
    assert len({keys[0], keys[2], keys[3]}) == 3
    assert all(len(k) <= 64 for k in keys)


def test_header_routing_key(mock):
    m = mock()
    p = _prov(m, cache_route="header:x-grok-conv-id")
    spend.set_context(label="runA", case_id="c1", repeat=0)
    p.chat(MODEL, MSGS, max_tokens=8)
    h, body = _sent(m, 0)
    assert h["x-grok-conv-id"].startswith("cf-") and "prompt_cache_key" not in body


def test_no_context_or_no_route_sends_nothing(mock):
    m = mock()
    _prov(m, cache_route="body:session_id").chat(MODEL, MSGS, max_tokens=8)   # no case context
    assert "session_id" not in _sent(m, 0)[1]
    spend.set_context(label="r", case_id="c", repeat=0)
    clear_cache()
    _prov(m).chat(MODEL, MSGS, max_tokens=8)                                   # loopback: no default route
    assert "session_id" not in _sent(m, 1)[1] and "prompt_cache_key" not in _sent(m, 1)[1]


def test_an_explicit_extra_body_key_wins(mock):
    m = mock()
    p = _prov(m, cache_route="body:session_id", extra_body={"session_id": "mine"})
    spend.set_context(label="r", case_id="c", repeat=0)
    p.chat(MODEL, MSGS, max_tokens=8)
    assert _sent(m, 0)[1]["session_id"] == "mine"


# --------------------------------------------------- spend controls, any provider

def test_cap_applies_to_an_openai_shape_provider(mock, monkeypatch, tmp_path):
    j = tmp_path / "j.jsonl"
    monkeypatch.setenv("CF_SPEND_JOURNAL", str(j))
    monkeypatch.setenv("CF_SPEND_CAP_USD", "0.0005")
    m = mock({"prompt_tokens": 1000, "completion_tokens": 100,
              "prompt_tokens_details": {"cached_tokens": 800}})
    p = _prov(m)
    p.chat(MODEL, MSGS, max_tokens=8)                      # 0.00068 > cap
    with pytest.raises(RequestRejected) as e:
        p.chat(MODEL, MSGS, max_tokens=8)
    assert e.value.status == 402 and len(m.requests) == 1  # refused unsent
    lines = [json.loads(x) for x in j.read_text(encoding="utf-8").splitlines()]
    assert lines[0]["cache_read_tokens"] == 800 and lines[1]["refused"] is True


def test_allow_cases_gates_an_openai_shape_provider(mock, monkeypatch):
    monkeypatch.setenv("CF_SPEND_ALLOW_CASES", "ok1")
    m = mock()
    p = _prov(m)
    spend.set_context(label="r", case_id="other", repeat=0)
    with pytest.raises(RequestRejected):
        p.chat(MODEL, MSGS, max_tokens=8)
    spend.set_context(label="r", case_id="ok1", repeat=0)
    p.chat(MODEL, MSGS, max_tokens=8)
    assert len(m.requests) == 1


def test_unpriced_openai_shape_provider_is_never_capped(mock, monkeypatch):
    # 41051a7 (2026-11-12) made an unpriced hosted call under a cap fail closed unless --allow-unpriced
    # (tests/test_unpriced_hosted_gate.py); this test predates it and asserted the old default. Its claim
    # now holds for an ALLOWED unpriced model: it is sent and never capped (EXP-20261113-19).
    from crucibleforge import providers as _providers
    monkeypatch.setattr(_providers, "ALLOW_UNPRICED", True)
    monkeypatch.setenv("CF_SPEND_CAP_USD", "0")
    m = mock()
    cfg = {"providers": {"mock": {"type": "openai", "base_url": m.url, "api_key_env": "MOCK_KEY", "hosted": True}},
           "models": [{"name": MODEL, "provider": "mock", "model_id": MODEL}]}
    get_provider(cfg, "mock").chat(MODEL, MSGS, max_tokens=8)
    assert len(m.requests) == 1


# ------------------------------------------- the real env loop, both modes

class _LoopCtx:
    ctx_len = None
    bank_budget = 0

    def __init__(self, entry):
        self.entry = entry
        self.sent: list[str] = []
        self.n = 0

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, *, max_tokens, exact_budget=None, recover=True, **kw):
        self.sent.append(json.dumps(messages))
        self.n += 1
        r = api.ChatResult()
        r.reasoning_text = f"think {self.n}"
        r.finish_reason = "stop"
        if self.n < 4:
            r.tool_calls = [{"id": f"t{self.n}", "type": "function",
                             "function": {"name": "list_files", "arguments": '{"path": ""}'}}]
        else:
            r.response_text = "done"
        return r


def _loop(entry):
    from pathlib import Path
    from crucibleforge import runner
    p = Path(__file__).resolve().parent.parent / "cases" / "codeagent.json"
    case = {**json.loads(p.read_text(encoding="utf-8"))[0], "category": "codeagent"}
    ctx = _LoopCtx(entry)
    runner._run_env_loop(case, {"case_id": case["id"]}, ctx, 4096, None, 42)
    return ctx.sent


def test_metered_episode_requests_are_prefix_extensions():
    sent = _loop({"price": {"input": 1, "output": 2}})
    assert len(sent) == 4
    for a, b in zip(sent, sent[1:]):
        assert b.startswith(a[:-1])
    assert "think 1" in sent[3]                    # past reasoning stays in place


def test_unpriced_rig_episode_keeps_the_4_1_history_rewrite():
    sent = _loop({})
    assert "think 1" not in sent[3] and "think 3" in sent[3]   # only the latest turn's reasoning
