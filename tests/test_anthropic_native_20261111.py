"""Native Anthropic Messages provider + prompt caching + spend cap (2026-11-11).

Everything runs against an in-process mock of POST /v1/messages on an
ephemeral loopback port (the netguard refuses anything else): no API call of
any kind is made."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from crucibleforge import anthropic_native, api, bench_all, cli, codeenv, preflight, runner, spend
from crucibleforge.api import ProtocolRejected, RequestRejected
from crucibleforge.providers import call_cost_usd, cost_usd, get_provider, clear_cache

MODEL = "claude-fable-5-2"
PRICE = {"input": 10, "output": 50, "cache_write": 12.5, "cache_read": 0.25}
CREDIT = ('{"type":"error","error":{"type":"invalid_request_error","message":'
          '"Your credit balance is too low to access the Anthropic API."}}')


# --------------------------------------------------------------- mock server

def sse(*events) -> bytes:
    out = []
    for ev in events:
        out.append(f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n")
    return "".join(out).encode()


def reply(*, text="", tools=(), stop="end_turn", usage=None, thinking=True, model=MODEL):
    """A Messages SSE stream: [thinking] [text] [tool_use...] + usage."""
    u = {"input_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
         "output_tokens": 1, **(usage or {})}
    evs = [{"type": "message_start", "message": {
        "id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": [],
        "stop_reason": None, "usage": {k: u[k] for k in ("input_tokens", "cache_creation_input_tokens",
                                                          "cache_read_input_tokens")} | {"output_tokens": 1}}}]
    i = 0
    if thinking:
        evs += [{"type": "content_block_start", "index": i,
                 "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
                {"type": "content_block_delta", "index": i,
                 "delta": {"type": "thinking_delta", "thinking": "secret plan"}},
                {"type": "content_block_delta", "index": i,
                 "delta": {"type": "signature_delta", "signature": "c2ln"}},
                {"type": "content_block_stop", "index": i}]
        i += 1
    if text:
        evs += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": text[:3]}},
                {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": text[3:]}},
                {"type": "content_block_stop", "index": i}]
        i += 1
    for tid, name, args in tools:
        js = json.dumps(args)
        evs += [{"type": "content_block_start", "index": i,
                 "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}}},
                {"type": "content_block_delta", "index": i,
                 "delta": {"type": "input_json_delta", "partial_json": js[:5]}},
                {"type": "content_block_delta", "index": i,
                 "delta": {"type": "input_json_delta", "partial_json": js[5:]}},
                {"type": "content_block_stop", "index": i}]
        i += 1
    evs += [{"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None},
             "usage": {"output_tokens": u["output_tokens"]}},
            {"type": "message_stop"}]
    return sse(*evs)


class Mock:
    """POST /v1/messages: records (headers, raw body) and answers from a
    script of (status, bytes) entries, one per request."""

    def __init__(self, script):
        self.script = list(script)
        self.requests: list[tuple[dict, bytes]] = []
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                mock.requests.append(({k.lower(): v for k, v in self.headers.items()}, body))
                status, payload = mock.script.pop(0)
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream" if status == 200
                                 else "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def bodies(self):
        return [json.loads(b) for _, b in self.requests]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def mock():
    made = []

    def make(*script):
        m = Mock(script)
        made.append(m)
        return m
    yield make
    for m in made:
        m.close()


@pytest.fixture(autouse=True)
def _fresh_spend(monkeypatch):
    monkeypatch.delenv("CF_SPEND_CAP_USD", raising=False)
    monkeypatch.delenv("CF_SPEND_JOURNAL", raising=False)
    monkeypatch.setenv("CLAUDE_API_KEY", "test-key-not-real")
    spend.reset()
    spend.clear_context()
    clear_cache()
    yield
    spend.reset()
    clear_cache()


def _cfg(url, price=PRICE):
    return {"providers": {"anthropic": {
                "type": "anthropic", "base_url": url, "api_key_env": "CLAUDE_API_KEY",
                "concurrency": 4,
                "headers": {"anthropic-workspace-id": "wrkspc_test", "anthropic-version": "2023-06-01"}}},
            "models": [{"name": MODEL, "provider": "anthropic", "model_id": MODEL,
                        "price": price}]}


def _prov(m, price=PRICE):
    return get_provider(_cfg(m.url, price), "anthropic")


# --------------------------------------------------------- the agentic episode

class _Ctx:
    """Just enough of runner._Ctx for _run_env_loop, calling the real
    Provider.chat (the bench's request path)."""
    ctx_len = None
    bank_budget = 0

    def __init__(self, prov):
        self.prov = prov

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, *, max_tokens, exact_budget=None, recover=True, **kw):
        return self.prov.chat(MODEL, messages, max_tokens=max_tokens, **kw)


def _agent_case():
    p = Path(__file__).resolve().parent.parent / "cases" / "codeagent.json"
    return {**json.loads(p.read_text(encoding="utf-8"))[0], "category": "codeagent"}


def _episode(mock):
    """3 turns: list_files -> two read_file calls at once -> a final answer."""
    m = mock(
        (200, reply(tools=[("toolu_01", "list_files", {"path": ""})], stop="tool_use",
                    usage={"input_tokens": 3000, "cache_creation_input_tokens": 0,
                           "cache_read_input_tokens": 0, "output_tokens": 40})),
        (200, reply(text="Reading both.", tools=[("toolu_02", "read_file", {"path": "a.py"}),
                                                 ("toolu_03", "read_file", {"path": "b.py"})],
                    stop="tool_use",
                    usage={"input_tokens": 10, "cache_creation_input_tokens": 600,
                           "cache_read_input_tokens": 3000, "output_tokens": 80})),
        (200, reply(text="All done, nothing to change.",
                    usage={"input_tokens": 10, "cache_creation_input_tokens": 900,
                           "cache_read_input_tokens": 3600, "output_tokens": 20})))
    case = _agent_case()
    row = runner._run_env_loop(case, {"case_id": case["id"]}, _Ctx(_prov(m)), 4096, None, 42)
    return m, case, row


def test_three_turn_tool_episode_translates_both_ways(mock):
    m, case, row = _episode(mock)
    assert row["n_turns"] == 3 and row["agent"]["tool_calls"] == 3
    b1, b2, b3 = m.bodies()
    # request side: system hoisted, tools as input_schema, tool_use/tool_result blocks
    assert b1["system"][0]["text"] == case["system"]
    assert b1["messages"] == [{"role": "user", "content": [{"type": "text", "text": case["prompt"]}]}]
    assert [t["name"] for t in b1["tools"]] == [t["function"]["name"] for t in codeenv.TOOLS]
    assert b1["tools"][1]["input_schema"]["properties"]["path"] == {"type": "string"}
    assert b1["max_tokens"] == 4096 and b1["stream"] is True and b1["model"] == MODEL
    asst = b2["messages"][1]
    assert asst == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "toolu_01", "name": "list_files", "input": {"path": ""}}]}
    res = b2["messages"][2]
    assert res["role"] == "user" and res["content"][0]["type"] == "tool_result"
    assert res["content"][0]["tool_use_id"] == "toolu_01"
    # turn 3: the two parallel calls' results share ONE user message
    a2, r2 = b3["messages"][3], b3["messages"][4]
    assert [c["type"] for c in a2["content"]] == ["text", "tool_use", "tool_use"]
    assert [c["id"] for c in a2["content"][1:]] == ["toolu_02", "toolu_03"]
    assert [c["tool_use_id"] for c in r2["content"]] == ["toolu_02", "toolu_03"]
    assert len(b3["messages"]) == 5
    # roles strictly alternate (merged neighbours)
    roles = [x["role"] for x in b3["messages"]]
    assert all(a != b for a, b in zip(roles, roles[1:]))
    # response side: the final text is the row's answer, thinking is nowhere
    assert row["response"] == "All done, nothing to change."
    assert row["reasoning"] == ""
    assert "secret plan" not in json.dumps(row)


def test_no_thinking_sampler_or_seed_is_ever_sent(mock):
    m, _, _ = _episode(mock)
    for raw in (b for _, b in m.requests):
        body = json.loads(raw)
        for k in ("thinking", "temperature", "top_p", "top_k", "seed", "tool_choice",
                  "stream_options", "response_format", "priority", "chat_template_kwargs"):
            assert k not in body, k
        assert b"secret plan" not in raw and b"c2ln" not in raw   # no thinking replay
        assert b"reasoning_content" not in raw


def test_cache_control_is_on_every_request(mock):
    m, _, _ = _episode(mock)
    for body in m.bodies():
        assert body["cache_control"] == {"type": "ephemeral"}
        assert body["system"][-1]["cache_control"] == {"type": "ephemeral"}


def test_turn_n_plus_1_repeats_turn_n_byte_for_byte(mock):
    """The cache is a prefix match: tools, system and every earlier message
    of turn N must serialise to the same bytes in turn N+1."""
    m, _, _ = _episode(mock)
    ser = anthropic_native.serialize
    bodies = m.bodies()
    for prev, nxt in zip(bodies, bodies[1:]):
        assert ser(prev["tools"]) == ser(nxt["tools"])
        assert ser(prev["system"]) == ser(nxt["system"])
        assert ser(prev["messages"]) == ser(nxt["messages"][:len(prev["messages"])])
    # and the raw request bytes up to the end of the shared messages agree
    raw = [b for _, b in m.requests]
    shared = ser({k: bodies[0][k] for k in ("model", "max_tokens", "cache_control", "tools",
                                            "system")})[:-1]
    assert raw[0].startswith(shared) and raw[1].startswith(shared) and raw[2].startswith(shared)


def test_serialisation_is_deterministic():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "t1", "type": "function", "function": {"name": "f", "arguments": '{"b":1,"a":2}'}}]},
            {"role": "tool", "tool_call_id": "t1", "content": "r"}]
    a = anthropic_native.serialize(anthropic_native.build_request(MODEL, msgs, max_tokens=9, tools=codeenv.TOOLS))
    b = anthropic_native.serialize(anthropic_native.build_request(MODEL, [dict(x) for x in msgs],
                                                                  max_tokens=9, tools=codeenv.TOOLS))
    assert a == b
    # tool input keeps the model's own key order (no sorting)
    assert b'"input":{"b":1,"a":2}' in a


def test_translation_edge_cases():
    sys_, msgs = anthropic_native.translate_messages([
        {"role": "system", "content": "one"}, {"role": "developer", "content": "two"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": ""},                       # empty: dropped
        {"role": "user", "content": "again"},                       # merges with "q"
        {"role": "assistant", "content": "x", "reasoning_content": "hidden",
         "tool_calls": [{"id": "c", "function": {"name": "", "arguments": "not json"}}]},
        {"role": "tool", "tool_call_id": "c", "content": "out"},
        {"role": "user", "content": "next"}])                       # joins the tool result turn
    assert [b["text"] for b in sys_] == ["one", "two"]
    assert msgs[0] == {"role": "user", "content": [{"type": "text", "text": "q"},
                                                  {"type": "text", "text": "again"}]}
    assert msgs[1]["content"] == [{"type": "text", "text": "x"},
                                  {"type": "tool_use", "id": "c", "name": "unnamed_tool", "input": {}}]
    assert msgs[2]["content"] == [{"type": "tool_result", "tool_use_id": "c", "content": "out"},
                                  {"type": "text", "text": "next"}]


def test_auth_headers_are_native(mock):
    m = mock((200, reply(text="hello")))
    _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    h, _ = m.requests[0]
    assert h["x-api-key"] == "test-key-not-real"
    assert "authorization" not in h
    assert h["anthropic-workspace-id"] == "wrkspc_test" and h["anthropic-version"] == "2023-06-01"
    assert m.url.endswith("/v1")  # the request went to <base_url>/messages


def test_stop_reasons_map_to_finish_reasons(mock):
    m = mock((200, reply(text="cut", stop="max_tokens")), (200, reply(text="", stop="refusal")),
             (200, reply(text="ok")))
    p = _prov(m)
    msgs = [{"role": "user", "content": "Hi"}]
    assert p.chat(MODEL, msgs, max_tokens=8).finish_reason == "length"
    r = p.chat(MODEL, msgs, max_tokens=8)
    assert r.finish_reason == "content_filter" and r.native_stop_reason == "refusal"
    assert p.chat(MODEL, msgs, max_tokens=8).finish_reason == "stop"


# ------------------------------------------------------------ usage and cost

def test_usage_split_is_recorded_and_priced(mock):
    m, _, row = _episode(mock)
    mt = row["metrics"]
    # prompt_tokens = TOTAL input (uncached + write + read), summed over 3 turns
    assert mt["prompt_tokens"] == 3000 + (10 + 600 + 3000) + (10 + 900 + 3600)
    assert mt["cache_read_tokens"] == 6600 and mt["cache_write_tokens"] == 1500
    assert mt["completion_tokens"] == 140
    entry = {"price": PRICE}
    c = cost_usd(entry, mt["prompt_tokens"], mt["completion_tokens"],
                 mt["cache_read_tokens"], mt["cache_write_tokens"])
    uncached = 3000 + 10 + 10
    want = (uncached * 10 + 1500 * 12.5 + 6600 * 0.25 + 140 * 50) / 1e6
    assert c == pytest.approx(want)
    # what the process accumulator saw is the same money
    assert spend.total() == pytest.approx(want)


def test_cache_price_defaults_when_the_entry_names_none():
    e = {"price": {"input": 4, "output": 20}}
    c = cost_usd(e, 1000, 0, cache_read_tokens=500, cache_write_tokens=300)
    assert c == pytest.approx((200 * 4 + 300 * 4 * 1.25 + 500 * 4 * 0.1) / 1e6)
    # no split reported = every input token at the input price (unchanged)
    assert cost_usd(e, 1000, 10) == pytest.approx((1000 * 4 + 10 * 20) / 1e6)


def test_call_cost_counts_billed_extras():
    r = api.ChatResult(prompt_tokens=100, completion_tokens=10, cache_read_tokens=60,
                       cache_write_tokens=0, billed_extra_prompt_tokens=100,
                       billed_extra_completion_tokens=5, billed_extra_cache_read_tokens=60)
    want = ((80 * 10) + 120 * 0.25 + 15 * 50) / 1e6
    assert call_cost_usd({"price": PRICE}, r) == pytest.approx(want)


# ----------------------------------------------------------- errors / billing

def test_a_credit_400_is_the_billing_path(mock):
    m = mock((400, CREDIT.encode()))
    with pytest.raises(RequestRejected) as ei:
        _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    assert ei.value.status == 400 and preflight.billing_rejection(ei.value)
    assert len(m.requests) == 1                     # never retried


def test_a_credit_400_mid_episode_is_raised_not_graded(mock):
    m = mock((200, reply(tools=[("toolu_01", "list_files", {"path": ""})], stop="tool_use")),
             (400, CREDIT.encode()))
    case = _agent_case()
    with pytest.raises(RequestRejected) as ei:
        runner._run_env_loop(case, {"case_id": case["id"]}, _Ctx(_prov(m)), 4096, None, 42)
    assert runner._rejection_kind(case, ei.value) == "transport"


def test_a_thinking_protocol_400_is_infra_never_a_score(mock):
    body = b'{"type":"error","error":{"type":"invalid_request_error","message":"messages.1: thinking blocks must be passed back"}}'
    m = mock((200, reply(tools=[("toolu_01", "list_files", {"path": ""})], stop="tool_use")),
             (400, body))
    case = _agent_case()
    with pytest.raises(ProtocolRejected):
        runner._run_env_loop(case, {"case_id": case["id"]}, _Ctx(_prov(m)), 4096, None, 42)
    assert len(m.requests) == 2                     # not retried


def test_an_ordinary_400_stays_a_request_rejection(mock):
    m = mock((400, b'{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long"}}'))
    with pytest.raises(RequestRejected) as ei:
        _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    assert not preflight.billing_rejection(ei.value)


def test_overloaded_529_is_retried(mock, monkeypatch):
    monkeypatch.setattr(api, "RETRY_BACKOFF_S", 0.0)
    m = mock((529, b'{"type":"error","error":{"type":"overloaded_error","message":"Overloaded"}}'),
             (200, reply(text="fine")))
    r = _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    assert r.response_text == "fine" and len(m.requests) == 2


def test_an_sse_error_event_is_a_transport_error(mock, monkeypatch):
    monkeypatch.setattr(api, "MAX_TRANSPORT_RETRIES", 1)
    err = sse({"type": "message_start", "message": {"model": MODEL, "usage": {"input_tokens": 1}}},
              {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}})
    m = mock((200, err))
    with pytest.raises(api.TransportError):
        _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)


def test_a_cut_stream_is_a_transport_error(mock, monkeypatch):
    monkeypatch.setattr(api, "MAX_TRANSPORT_RETRIES", 1)
    cut = sse({"type": "message_start", "message": {"model": MODEL, "usage": {"input_tokens": 1}}},
              {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
              {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "half"}})
    m = mock((200, cut))
    with pytest.raises(api.TransportError):
        _prov(m).chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)


# --------------------------------------------------------- spend cap + journal

def test_cap_reached_refuses_the_next_call_as_billing(mock, monkeypatch, tmp_path):
    journal = tmp_path / "spend.jsonl"
    monkeypatch.setenv("CF_SPEND_JOURNAL", str(journal))
    # first call costs (1000*10 + 100*50)/1e6 = $0.015; cap $0.01
    monkeypatch.setenv("CF_SPEND_CAP_USD", "0.01")
    m = mock((200, reply(text="one", usage={"input_tokens": 1000, "output_tokens": 100})),
             (200, reply(text="never sent")))
    p = _prov(m)
    spend.set_context(label=MODEL, case_id="LAG1-potkeeper-yearend", repeat=1)
    p.chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    with pytest.raises(RequestRejected) as ei:
        p.chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    assert len(m.requests) == 1                     # the refused call never left the box
    e = ei.value
    assert e.status == 402 and preflight.billing_rejection(e)
    # stored the way the runner stores a non-case-local rejection: errored
    # transport row -> billing row -> pending, re-queued, never a 0
    case = _agent_case()
    assert runner._rejection_kind(case, e) == "transport"
    row = runner._error_row({"case_id": case["id"], "repeat": 1}, case,
                            f"transport failure: {e}", kind="transport")
    assert row["grade"] == "error" and preflight.billing_error_row(row)
    assert cli.done_jobs([row]) == set()
    assert bench_all.missing_cases([row], [case["id"]]) == [case["id"]]
    lines = [json.loads(x) for x in journal.read_text().splitlines()]
    assert len(lines) == 2
    first, refused = lines
    assert first["case_id"] == "LAG1-potkeeper-yearend" and first["repeat"] == 1
    assert first["model"] == MODEL and first["input_tokens"] == 1000
    assert first["output_tokens"] == 100 and first["cache_read_tokens"] == 0
    assert first["call_usd"] == pytest.approx(0.015) and first["total_usd"] == pytest.approx(0.015)
    assert refused["refused"] is True and refused["cap_usd"] == 0.01


def test_cap_mid_episode_ends_the_episode_as_infra(mock, monkeypatch):
    monkeypatch.setenv("CF_SPEND_CAP_USD", "0.02")
    m = mock((200, reply(tools=[("toolu_01", "list_files", {"path": ""})], stop="tool_use",
                         usage={"input_tokens": 3000, "output_tokens": 10})))
    case = _agent_case()
    with pytest.raises(RequestRejected) as ei:
        runner._run_env_loop(case, {"case_id": case["id"]}, _Ctx(_prov(m)), 4096, None, 42)
    assert ei.value.status == 402 and len(m.requests) == 1


def test_journal_lines_record_the_cache_split(mock, monkeypatch, tmp_path):
    journal = tmp_path / "j.jsonl"
    monkeypatch.setenv("CF_SPEND_JOURNAL", str(journal))
    _episode(mock)
    lines = [json.loads(x) for x in journal.read_text().splitlines()]
    assert [x["cache_read_tokens"] for x in lines] == [0, 3000, 3600]
    assert [x["cache_write_tokens"] for x in lines] == [0, 600, 900]
    assert [x["input_tokens"] for x in lines] == [3000, 10, 10]
    assert lines[-1]["total_usd"] == pytest.approx(sum(x["call_usd"] for x in lines))


def test_unpriced_models_are_not_capped(mock, monkeypatch):
    monkeypatch.setenv("CF_SPEND_CAP_USD", "0")
    m = mock((200, reply(text="free")))
    p = get_provider({**_cfg(m.url), "models": []}, "anthropic")
    assert p.chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8).response_text == "free"


def test_missing_cases_ignores_a_stored_billing_episode_row():
    from test_billing_agentic_20261111 import _episode_fail_row
    assert bench_all.missing_cases([_episode_fail_row("A")], ["A"]) == ["A"]
    real = _episode_fail_row("A", end="turn limit")
    assert bench_all.missing_cases([real], ["A"]) == []


def test_native_and_compat_rows_compare_on_the_board():
    from crucibleforge import conformance, report
    base = {"reasoning_replay": False, "extra_body_keys": [], "sampler": {"source": "default", "params": {}}}
    rows = [{"protocol": {**base, "provider_type": "openai"}},
            {"protocol": {**base, "provider_type": "anthropic"}}]
    assert report._protocol_mix_note(rows, {"provider_type": "anthropic"}, None) is None
    rec = {"verdict": "WARN", "protocol": {**base, "provider_type": "openai"}}
    assert conformance.record_is_current(rec, {**base, "provider_type": "anthropic"})
    rows.append({"protocol": {**base, "provider_type": "openai", "reasoning_replay": True}})
    assert report._protocol_mix_note(rows, {"provider_type": "anthropic"}, None)


def test_credit_preflight_speaks_native(monkeypatch, mock):
    m = mock((400, CREDIT.encode()))
    p = _prov(m)
    monkeypatch.setattr("crucibleforge.conformance._is_hosted", lambda prov: True)
    why = preflight.hosted_credit_problem(p, MODEL)
    assert why and "out of credit" in why
    h, body = m.requests[0]
    assert h["x-api-key"] == "test-key-not-real" and "authorization" not in h
    assert json.loads(body)["max_tokens"] == 1


def test_detach_carries_the_spend_cap_into_the_unit(monkeypatch):
    """A systemd --user unit does not inherit the caller's environment: a
    detached bench would otherwise run with NO cap."""
    import types
    monkeypatch.setattr(cli, "ENV_FILE", None)
    monkeypatch.setenv("CF_SPEND_CAP_USD", "25")
    monkeypatch.setenv("CF_SPEND_JOURNAL", "/var/tmp/x/spend.jsonl")
    args = types.SimpleNamespace(cmd="bench-all", models=None, run_id="r-1")
    _, cmd, _ = cli._detach_argv(args, ["bench-all", "--only", MODEL, "--go", "--detach"])
    assert "--setenv=CF_SPEND_CAP_USD=25" in cmd
    assert "--setenv=CF_SPEND_JOURNAL=/var/tmp/x/spend.jsonl" in cmd
    assert cmd.index("--setenv=CF_SPEND_CAP_USD=25") < cmd.index("/bin/bash")
    monkeypatch.delenv("CF_SPEND_CAP_USD")
    _, cmd2, _ = cli._detach_argv(args, ["bench-all", "--detach"])
    assert not any(a.startswith("--setenv=CF_SPEND_CAP_USD") for a in cmd2)


def test_allow_cases_gates_a_staged_run(mock, monkeypatch, tmp_path):
    """Stage 1 of the recipe: only LAG1 may spend; LAG2/3 are refused unsent
    on the billing path (pending, never a score)."""
    journal = tmp_path / "j.jsonl"
    monkeypatch.setenv("CF_SPEND_JOURNAL", str(journal))
    monkeypatch.setenv("CF_SPEND_ALLOW_CASES", "LAG1-potkeeper-yearend")
    m = mock((200, reply(text="ok")))
    p = _prov(m)
    spend.set_context(label=MODEL, case_id="LAG2-seedbank-units", repeat=1)
    with pytest.raises(RequestRejected) as ei:
        p.chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8)
    assert ei.value.status == 402 and preflight.billing_rejection(ei.value)
    assert m.requests == []
    spend.set_context(label=MODEL, case_id="LAG1-potkeeper-yearend", repeat=1)
    assert p.chat(MODEL, [{"role": "user", "content": "Hi"}], max_tokens=8).response_text == "ok"
    lines = [json.loads(x) for x in journal.read_text().splitlines()]
    assert lines[0]["refused"] and lines[0]["case_id"] == "LAG2-seedbank-units"
    assert lines[1]["case_id"] == "LAG1-potkeeper-yearend" and "refused" not in lines[1]
    import types
    monkeypatch.setattr(cli, "ENV_FILE", None)
    _, cmd, _ = cli._detach_argv(types.SimpleNamespace(cmd="bench-all", models=None, run_id="r"),
                                 ["bench-all", "--detach"])
    assert "--setenv=CF_SPEND_ALLOW_CASES=LAG1-potkeeper-yearend" in cmd
