"""Grok (xAI) client fixes, 2026-11-22.

1. xAI gets a fresh ``x-grok-conv-id`` per REQUEST: on a sticky key the backend that served the previous
   turn puts its hidden reasoning back into the next prompt (reasoning replay the harness never sends).
   ``cache_route_scope: episode`` restores the sticky key; other hosts keep one key per episode.
2. A stream that sends only SSE keep-alive comments is stuck, not slow: KeepaliveStall after
   ``api.keepalive_ceiling`` without a data event (the byte read timeout never fires on it). The ceiling
   scales with the allowance (silent thinking), floor stall_s x 2, capped by the row's wall clock.
3. The spend ledger books the provider's own billed figure (xAI ``cost_in_usd_ticks``) when it is there.
4. An OpenAI-shape cache split carries no made-up ``cache_write_tokens: 0`` (that 0 made the row pricer
   apply Claude's 0.1x read rate to xAI/OpenAI/DeepSeek cache reads).
5. ``run --cases`` under the first-reply rule runs named chains (resuming from their stored attempts)
   instead of silently skipping them.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import types

import pytest

from crucibleforge import api, cli, providers, runner, spend


# ---------------------------------------------------------------- 1. conv-id routing

def test_route_scope_defaults_by_host():
    assert providers.cache_route_scope_for("https://api.x.ai/v1", None) == "call"
    assert providers.cache_route_scope_for("https://api.openai.com/v1", None) == "episode"
    assert providers.cache_route_scope_for("https://openrouter.ai/api/v1", None) == "episode"
    assert providers.cache_route_scope_for("https://api.x.ai/v1", "episode") == "episode"
    assert providers.cache_route_scope_for("https://api.openai.com/v1", "call") == "call"
    assert providers.cache_route_scope_for("https://api.x.ai/v1", "bogus") == "call"


def _headers_of_calls(monkeypatch, base_url, n=3, scope=None, case="FLAT-toolgap_L1-005-r1-a1"):
    seen = []

    def fake(base, key, model, msgs, **kw):
        seen.append(dict(kw.get("headers") or {}))
        return api.ChatResult()

    monkeypatch.setattr(api, "stream_chat_retried", fake)
    p = providers.Provider(name="p", type="openai", base_url=base_url, api_key="k", cache_route_scope=scope)
    spend.set_context(label="grok", case_id=case, repeat=1)
    try:
        for _ in range(n):
            p.chat("grok-4.7", [{"role": "user", "content": "hi"}], max_tokens=8)
    finally:
        spend.clear_context()
    return [h.get("x-grok-conv-id") for h in seen]


def test_every_xai_request_of_an_episode_gets_its_own_conv_id(monkeypatch):
    keys = _headers_of_calls(monkeypatch, "https://api.x.ai/v1")
    assert all(k and k.startswith("cf-") for k in keys)
    assert len(set(keys)) == len(keys)                    # never reused: no server-side state to pick up


def test_episode_scope_restores_the_sticky_conv_id(monkeypatch):
    keys = _headers_of_calls(monkeypatch, "https://api.x.ai/v1", scope="episode")
    assert keys[0] and len(set(keys)) == 1


def test_a_per_call_key_is_sent_even_outside_a_job(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "stream_chat_retried",
                        lambda b, k, m, ms, **kw: seen.append(dict(kw.get("headers") or {})) or api.ChatResult())
    spend.clear_context()
    providers.Provider(name="p", type="openai", base_url="https://api.x.ai/v1", api_key="k").chat(
        "grok-4.7", [{"role": "user", "content": "hi"}], max_tokens=8)
    assert seen[0].get("x-grok-conv-id", "").startswith("cf-")


def test_get_provider_reads_cache_route_scope(monkeypatch):
    monkeypatch.setenv("CC_KEY", "k")
    providers.clear_cache()
    cfg = {"providers": {"p": {"type": "openai", "base_url": "https://api.x.ai/v1", "api_key_env": "CC_KEY",
                               "cache_route_scope": "episode"}},
           "models": [{"name": "m", "provider": "p", "model_id": "m"}]}
    try:
        assert providers.get_provider(cfg, "p").cache_route_scope == "episode"
    finally:
        providers.clear_cache()


# ---------------------------------------------------------------- 2. keep-alive-only stall

class _Resp:
    def __init__(self, chunks):
        self._chunks = chunks
        self.status_code = 200
        self.headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_text(self):
        yield from self._chunks

    def read(self):
        return b""


class _Client:
    def __init__(self, chunks):
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, *a, **k):
        return _Resp(self._chunks)


class _Clock:
    """perf_counter that advances ``step`` seconds on every read."""

    def __init__(self, step):
        self.t, self.step = 1000.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def _data(obj):
    return f"data: {json.dumps(obj)}\n"


def _patch_stream(monkeypatch, chunks, step):
    monkeypatch.setattr(api.httpx, "Client", lambda *a, **k: _Client(chunks))
    monkeypatch.setattr(api.time, "perf_counter", _Clock(step))


def test_keepalive_only_stream_raises_keepalive_stall(monkeypatch):
    # 200 OK, then nothing but ": keepalive" comments (what a stuck xAI request sends every 15 s)
    _patch_stream(monkeypatch, [": keepalive\n"] * 200, step=5.0)
    with pytest.raises(api.KeepaliveStall) as ei:
        api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=64, stall_s=60, verify_model=False)
    assert "keep-alive" in str(ei.value)
    assert isinstance(ei.value, api.StreamStalled)       # a stall: never retried, an errored transport row


def test_keepalives_between_data_events_are_not_a_stall(monkeypatch):
    chunks = []
    for i in range(30):
        chunks += [": keepalive\n", ": keepalive\n",
                   _data({"model": "grok-4.7", "choices": [{"delta": {"content": f"w{i} "}}]})]
    chunks += [_data({"model": "grok-4.7", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
               _data({"model": "grok-4.7", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 30}}),
               "data: [DONE]\n"]
    _patch_stream(monkeypatch, chunks, step=5.0)
    r = api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=64, stall_s=60, verify_model=False)
    assert r.finish_reason == "stop" and r.response_text.startswith("w0")


def test_rig_comment_lines_longer_than_the_ceiling_keep_the_request_alive(monkeypatch):
    # StudioForge sends ": loading" / ": prefilling" comments during a JIT load; not a stall off xAI
    chunks = [": loading m\n", ": prefilling m\n"] * 300 + [
        _data({"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
        _data({"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        "data: [DONE]\n"]
    _patch_stream(monkeypatch, chunks, step=5.0)       # 3000 s of comments, ceiling is 600 s
    r = api.stream_chat("http://100.1.2.3:1234/v1", "k", "m", [{"role": "user", "content": "q"}],
                        max_tokens=64, stall_s=60, verify_model=False)
    assert r.response_text == "ok"


def test_xai_host_comment_stream_is_still_cut(monkeypatch):
    _patch_stream(monkeypatch, [": prefilling m\n"] * 300, step=5.0)
    with pytest.raises(api.KeepaliveStall):
        api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=64, stall_s=60, verify_model=False)


def test_without_stall_s_keepalives_are_never_cut(monkeypatch):
    chunks = [": keepalive\n"] * 50 + [
        _data({"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}),
        _data({"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        "data: [DONE]\n"]
    _patch_stream(monkeypatch, chunks, step=60.0)
    r = api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=8)
    assert r.response_text == "ok"


def test_keepalive_stall_is_not_retried(monkeypatch):
    calls = []

    def fake(*a, **k):
        calls.append(1)
        raise api.KeepaliveStall("stuck")

    monkeypatch.setattr(api, "stream_chat", fake)
    with pytest.raises(api.KeepaliveStall):
        api.stream_chat_retried("u", "k", "m", [], max_tokens=1)
    assert len(calls) == 1


# ---------------------------------------------------------------- 3. provider-reported cost

def test_cost_in_usd_ticks_is_parsed_from_the_stream(monkeypatch):
    usage = {"prompt_tokens": 1192, "completion_tokens": 102, "total_tokens": 2115,
             "prompt_tokens_details": {"cached_tokens": 192},
             "completion_tokens_details": {"reasoning_tokens": 821}, "cost_in_usd_ticks": 76340000}
    chunks = [_data({"model": "grok-4.7", "choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]}),
              _data({"model": "grok-4.7", "choices": [], "usage": usage}), "data: [DONE]\n"]
    _patch_stream(monkeypatch, chunks, step=0.1)
    r = api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=64, verify_model=False)
    assert r.provider_cost_usd == pytest.approx(0.007634)
    assert r.completion_tokens == 923            # reasoning reported beside completion is still added


@pytest.mark.parametrize("bad", [None, "12", True, -5])
def test_bad_ticks_are_ignored(bad):
    assert api.provider_cost_usd({"cost_in_usd_ticks": bad}) is None
    assert api.provider_cost_usd(None) is None


def test_ledger_books_the_provider_figure_over_list_price():
    r = api.ChatResult(prompt_tokens=100_000, completion_tokens=1000, cache_read_tokens=90_000,
                       provider_cost_usd=0.0725)
    assert spend.call_cost({"input": 2, "output": 6}, r) == pytest.approx(0.0725)
    r.provider_cost_usd = None                   # no figure: the token price as before
    assert spend.call_cost({"input": 2, "output": 6}, r) == pytest.approx(
        (100_000 * 2 + 1000 * 6) / 1e6)


def test_billed_extra_tokens_keep_the_token_price():
    # a merged result (discarded attempts folded in) is more than the one call the figure is for
    r = api.ChatResult(prompt_tokens=10, completion_tokens=10, provider_cost_usd=0.001,
                       billed_extra_completion_tokens=500)
    assert spend.call_cost({"input": 2, "output": 6}, r) != pytest.approx(0.001)


# ---------------------------------------------------------------- 4. cache split of OpenAI-shape rows

def test_openai_shape_cache_split_has_no_write_key():
    a = api.ChatResult(prompt_tokens=1000, completion_tokens=10, cache_read_tokens=900)
    assert runner._cache_split([a]) == {"cache_read_tokens": 900}
    m = runner._metrics(a)
    assert "cache_write_tokens" not in m


def test_native_cache_split_keeps_its_write_count():
    a = api.ChatResult(prompt_tokens=1000, completion_tokens=10, cache_read_tokens=800,
                       cache_write_tokens=100)
    assert runner._cache_split([a]) == {"cache_read_tokens": 800, "cache_write_tokens": 100}


def test_xai_row_cache_reads_are_priced_at_the_declared_rate_not_claude_tenth():
    entry = {"price": {"input": 2, "output": 6, "cache_read": 0.5}}
    m = runner._metrics(api.ChatResult(prompt_tokens=100_000, completion_tokens=0, cache_read_tokens=100_000))
    c = providers.cost_usd(entry, m["prompt_tokens"], m["completion_tokens"], m.get("cache_read_tokens"),
                           m.get("cache_write_tokens"), native_cache=m.get("cache_write_tokens") is not None)
    assert c == pytest.approx(0.05)              # 100k x $0.50/M (was 100k x $0.20/M)


# ---------------------------------------------------------------- 5. run --cases with named chains

def _args():
    return types.SimpleNamespace(smoke=False, skip_conformance=False)


def test_named_chains_run_in_a_board_pass_and_standard_units_by_first_reply(monkeypatch, capsys):
    calls = []

    def fake_run_models(cfg, entries, cases, smoke=False, jobs_by_label=None, **kw):
        calls.append(([e["name"] for e in entries], [c["id"] for c in cases], jobs_by_label))
        return {e["name"]: {"rows": len(cases), "cost_usd": 0.5, "failed": False, "load_s": 0,
                            "device": "x"} for e in entries}

    monkeypatch.setattr(cli, "_first_reply_rule", lambda prof: True)
    monkeypatch.setattr(cli, "_first_reply_jobs",
                        lambda label, cfg, cases, smoke=False, **kw: {(None, c["id"], 1) for c in cases
                                                                     if c["id"] == "TZ16"})
    monkeypatch.setattr(cli, "_resume_rows", lambda label, cfg: [])     # the 4.5.1 match rule: test_resume_match_451
    monkeypatch.setattr(cli, "halted", lambda: False)
    cases = [{"id": "TZ16"}, {"id": "IZ02"}, {"id": "FLAT-toolgap_L1-005", "chain_spec": {"x": 1}}]
    summary, ran = cli._run_selected({}, [{"name": "grok"}], cases, {}, _args(), ["TZ16", "IZ02",
                                     "FLAT-toolgap_L1-005"], fake_run_models)
    assert calls[0] == (["grok"], ["TZ16", "IZ02"], {"grok": {(None, "TZ16", 1)}})   # recover-style, owed only
    assert calls[1] == (["grok"], ["FLAT-toolgap_L1-005"], None)                       # board pass: chain driver
    assert summary["grok"]["rows"] == 3 and summary["grok"]["cost_usd"] == 1.0
    assert [e["name"] for e in ran] == ["grok"]
    assert "named chain(s) resume" in capsys.readouterr().out


def test_only_chains_named_and_nothing_standard_owed(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_first_reply_rule", lambda prof: True)
    monkeypatch.setattr(cli, "_first_reply_jobs", lambda *a, **k: set())
    monkeypatch.setattr(cli, "halted", lambda: False)

    def fake_run_models(cfg, entries, cases, **kw):
        calls.append([c["id"] for c in cases])
        return {"grok": {"rows": 1, "failed": False}}

    summary, ran = cli._run_selected({}, [{"name": "grok"}], [{"id": "FLAT-a", "chain_spec": {"x": 1}}], {},
                                     _args(), ["FLAT-a"], fake_run_models)
    assert calls == [["FLAT-a"]] and summary["grok"]["rows"] == 1 and ran == [{"name": "grok"}]


def test_without_the_rule_the_old_path_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_first_reply_rule", lambda prof: False)

    def fake_run_models(cfg, entries, cases, jobs_by_label=None, **kw):
        calls.append(([c["id"] for c in cases], jobs_by_label))
        return {}

    cases = [{"id": "A"}, {"id": "FLAT-a", "chain_spec": {}}]
    cli._run_selected({}, [{"name": "grok"}], cases, {}, _args(), ["A", "FLAT-a"], fake_run_models)
    assert calls == [(["A", "FLAT-a"], None)]


def test_merge_summaries_marks_a_failure_in_either_pass():
    a = {"m": {"rows": 2, "cost_usd": 0.1, "failed": False}}
    b = {"m": {"rows": 1, "cost_usd": 0.2, "failed": True, "error": "boom"}}
    out = cli._merge_summaries(a, b)
    assert out["m"]["rows"] == 3 and out["m"]["failed"] and out["m"]["error"] == "boom"
    assert out["m"]["cost_usd"] == pytest.approx(0.3)
    assert a["m"]["rows"] == 2                  # inputs untouched


# ---------------------------------------------------------------- 6. empty-model guard on tool episodes

def _tool_episode_row():
    return {"finish_reason": "tool_calls", "response": "", "turn": None, "tool_calls": [],
            "tool_conversation": [{"role": "user", "content": "sign Leo up"},
                                  {"role": "assistant", "content": "",
                                   "tool_calls": [{"id": "c0", "type": "function",
                                                   "function": {"name": "list_classes", "arguments": "{}"}}]},
                                  {"role": "tool", "content": "{}"}]}


def test_tool_episodes_ending_on_a_tool_result_are_not_empty_completions():
    # 2026-11-22 redraw: the first three completions were scripted tool cases (TZ20) that end on a tool result;
    # the guard called them empty and dropped the model's remaining jobs
    st = {"sanity_total": 0, "sanity_empty": 0}
    runner._sanity_check(st, [_tool_episode_row() for _ in range(3)], "m")   # must not raise
    assert st["sanity_total"] == 3 and st["sanity_empty"] == 0


def test_a_tool_episode_with_no_model_tool_call_still_counts_as_empty():
    row = _tool_episode_row()
    row["tool_conversation"] = [m for m in row["tool_conversation"] if m["role"] != "assistant"]
    st = {"sanity_total": 0, "sanity_empty": 0}
    with pytest.raises(runner.ModelRunError, match="all empty"):
        runner._sanity_check(st, [row] * 3, "m")


# ---------------------------------------------------------------- 2b. the keep-alive ceiling scales with the allowance

def test_keepalive_ceiling_short_allowance_keeps_the_floor():
    assert api.keepalive_ceiling(300, 4096) == 600
    assert api.keepalive_ceiling(300, None) == 600
    assert api.keepalive_ceiling(None, 65536) is None


def test_keepalive_ceiling_scales_with_the_allowance_and_the_wall_clock_caps_it():
    # 4.5.1 (PLAN-451-LIMITS step 3): 65,536 x 2.5 / 40 = 4,096 s, capped at KEEPALIVE_MAX_S (2,700 s)
    assert api.keepalive_ceiling(300, 65536) == api.KEEPALIVE_MAX_S == 2700.0
    assert api.keepalive_ceiling(300, 24576) == 24576 * 2.5 / 40          # 1,536 s: covers the stored 902 s think
    assert api.keepalive_ceiling(300, 65536, max_wall_s=1800) == 1800
    assert api.keepalive_ceiling(300, 4096, max_wall_s=300) == 300


def _silent_then_answer(silent_s, step=5.0):
    n = int(silent_s / step)
    return ([": keepalive\n"] * n
            + [_data({"model": "grok-4.7", "choices": [{"delta": {"content": "done"}}]}),
               _data({"model": "grok-4.7", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
               _data({"model": "grok-4.7", "choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 9}}),
               "data: [DONE]\n"])


def test_a_65536_allowance_is_not_cut_after_900_s_of_silent_thinking(monkeypatch):
    # PATCHFIX-core-4-1 (65,536) was cut at 602 s twice; stored good siblings sat silent 650-900 s
    _patch_stream(monkeypatch, _silent_then_answer(900), step=5.0)
    r = api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=65536, stall_s=300, verify_model=False)
    assert r.response_text == "done"


def test_a_short_allowance_is_still_cut_at_the_floor(monkeypatch):
    _patch_stream(monkeypatch, _silent_then_answer(900), step=5.0)
    with pytest.raises(api.KeepaliveStall, match="ceiling 600s"):
        api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=4096, stall_s=300, verify_model=False)


def test_silence_past_the_scaled_ceiling_still_raises(monkeypatch):
    _patch_stream(monkeypatch, _silent_then_answer(4300), step=5.0)
    with pytest.raises(api.KeepaliveStall, match="ceiling 2700s"):
        api.stream_chat("https://api.x.ai/v1", "k", "grok-4.7", [{"role": "user", "content": "q"}],
                        max_tokens=65536, stall_s=300, verify_model=False)
