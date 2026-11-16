"""WP-13a (4.5) runner: ctx_short => retry/error never scored, per-model and
thinker context, judged-first scheduling."""
from __future__ import annotations

import json

import pytest

from crucibleforge import config, providers, runner, stopflag
from crucibleforge.api import ChatResult, RequestRejected


@pytest.fixture(autouse=True)
def _flags(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _cfg(**defaults):
    return {"defaults": {"context_length": 8192, "repeats": {"perf": 1}, **defaults},
            "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
            "judge": {"candidates": []}, "models": []}


def _entry(**kw):
    return {"name": "m", "model_id": "m", "provider": "p", **kw}


def _case(cid, cat="instruct", **kw):
    return {"id": cid, "category": cat, "prompt": f"say ok {cid}", "max_tokens": 4000,
            "grader": "contains", "grader_config": {"needles": ["ok"]}, **kw}


# ----------------------------------------------------------- pure helpers
def test_is_ctx_short_error():
    assert runner.is_ctx_short_error("HTTP 400: the request exceeds the available context size")
    assert runner.is_ctx_short_error("This model's maximum context length is 8192 tokens")
    assert runner.is_ctx_short_error("context_length_exceeded")
    assert not runner.is_ctx_short_error("HTTP 400: unknown parameter top_k")
    assert not runner.is_ctx_short_error("")


def _row(finish="length", p=7000, c=1100, sent=4000):
    return {"finish_reason": finish, "max_tokens_sent": sent,
            "metrics": {"prompt_tokens": p, "completion_tokens": c}}


def test_row_ctx_short():
    assert runner.row_ctx_short(_row(), 8192)                       # window full, budget left
    assert not runner.row_ctx_short(_row(c=4000, p=100), 8192)      # the BUDGET ran out
    assert not runner.row_ctx_short(_row(finish="stop"), 8192)
    assert not runner.row_ctx_short(_row(p=100, c=100), 8192)       # window not full
    assert not runner.row_ctx_short(_row(), None)
    assert not runner.row_ctx_short({"finish_reason": "length", "metrics": {}}, 8192)


# ------------------------------------------------ per-model / thinker context
def test_load_context_per_model_override_and_thinker_default():
    base = _cfg(context_length=32768)
    assert runner.load_context(base, _entry()) == 32768
    assert runner.load_context(base, _entry(context_length=16384)) == 16384
    assert runner.load_context(base, _entry(context=24576, context_length=16384)) == 24576
    assert runner.load_context(base, _entry(load_ctx=20000)) == 20000
    # thinker default only with the profile key, only for a registry thinker
    t = _cfg(context_length=32768, thinker_context=73728)
    assert runner.load_context(t, _entry(thinking=True)) == 73728
    assert runner.load_context(t, _entry(thinking="auto")) == 32768
    assert runner.load_context(t, _entry()) == 32768
    # a per-model value wins over the thinker default (a 40k model says so)
    assert runner.load_context(t, _entry(thinking=True, context=40960)) == 40960
    # without the key a thinker is unchanged (4.1)
    assert runner.load_context(base, _entry(thinking=True)) == 32768


def test_apply_profile_carries_the_new_keys():
    from crucibleforge import profiles
    prof = {"name": "x", "cases": {}, "thinker_context": 73728, "ctx_short_retry": 1,
            "judged_first": True}
    cfg2, _ = profiles.apply_profile(prof, {"defaults": {}})
    d = cfg2["defaults"]
    assert (d["thinker_context"], d["ctx_short_retry"], d["judged_first"]) == (73728, 1, True)


# ---------------------------------------------------------- judged first
def test_judged_first_is_a_stable_partition():
    jobs = [(_case("A"), 1, None), (_case("J1", rubric="rp_single"), 1, None),
            (_case("B"), 1, None), (_case("J2", judge_elements=[{"id": "e", "q": "q"}]), 1, None)]
    assert [j[0]["id"] for j in runner.judged_first(jobs)] == ["J1", "J2", "A", "B"]
    assert [j[0]["id"] for j in runner.judged_first([])] == []


# --------------------------------------------------- a whole run, faked
def _fake_rig(monkeypatch, replies, live=None, ctx=8192):
    """``replies``: a callable (call_no, messages) -> ChatResult | Exception."""
    seen = {"switch": [], "calls": 0, "loaded": {"ctx": ctx}}

    def switch(self, model_id, c=None):
        seen["switch"].append(c)
        seen["loaded"]["ctx"] = c
        return 1.0

    def chat(self, model_id, messages, **kw):
        seen["calls"] += 1
        r = replies(seen["calls"], messages)
        if isinstance(r, Exception):
            raise r
        return r

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", switch)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "live_context",
                        lambda self, mid: live if live is not None else seen["loaded"]["ctx"])
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {"ctx_size": seen["loaded"]["ctx"],
                                                                 "parallel": 1})
    monkeypatch.setattr(P, "workers", lambda self, mid: 1)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return seen


def _cut():
    return ChatResult(response_text="partial", finish_reason="length", served_model="m",
                      prompt_tokens=7000, completion_tokens=1150, total_s=0.1)


def _ok():
    return ChatResult(response_text="ok", finish_reason="stop", served_model="m",
                      prompt_tokens=10, completion_tokens=3, total_s=0.1)


def _case_only(monkeypatch, make):
    """Replies for the case's own requests (``say ok``); everything else (the
    conformance and thinking probes) gets a plain ok."""
    n = {"case": 0}

    def replies(_, m):
        if "say ok" not in m[-1]["content"]:
            return _ok()
        n["case"] += 1
        return make(n["case"])
    return n, replies


def test_a_context_cut_row_is_errored_never_scored(monkeypatch):
    n, replies = _case_only(monkeypatch, lambda k: _cut())
    _fake_rig(monkeypatch, replies)       # live == model ctx: no widening possible
    runner.run_models(_cfg(ctx_short_retry=1), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r["grade"] == "error" and r["error_kind"] == "ctx_short"
    assert "ctx_short" in r["error"] and r["load_ctx"] == 8192
    assert n["case"] == 1                                    # nothing a retry could change


def test_a_context_cut_row_is_retried_once_after_a_widening_reload(monkeypatch):
    # the server comes back narrower than the model's context; after the reload it is right
    n, replies = _case_only(monkeypatch, lambda k: _cut() if k == 1 else _ok())
    seen = _fake_rig(monkeypatch, replies)
    cur = seen["loaded"]
    monkeypatch.setattr(providers.Provider, "live_context",
                        lambda self, mid: 4096 if len(seen["switch"]) < 2 else cur["ctx"])
    runner.run_models(_cfg(ctx_short_retry=1), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r.get("grade") == "pass" and r["load_ctx"] == 8192
    assert n["case"] == 2 and len(seen["switch"]) == 2


def test_a_server_context_error_is_errored_not_a_failure(monkeypatch):
    err = RequestRejected(400, "request exceeds the available context size (8192)")
    _fake_rig(monkeypatch, lambda n, m: err if "say ok" in m[-1]["content"] else _ok())
    runner.run_models(_cfg(ctx_short_retry=1), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r["grade"] == "error" and r["error_kind"] == "ctx_short"


def test_ctx_short_retry_zero_restores_the_old_behaviour(monkeypatch):
    _fake_rig(monkeypatch, lambda n, m: _cut())
    runner.run_models(_cfg(ctx_short_retry=0), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r.get("error_kind") != "ctx_short" and r["finish_reason"] == "length"


def test_a_budget_cut_row_is_still_scored(monkeypatch):
    def replies(n, m):
        return ChatResult(response_text="partial", finish_reason="length", served_model="m",
                          prompt_tokens=100, completion_tokens=4000, total_s=0.1)
    _fake_rig(monkeypatch, replies)
    runner.run_models(_cfg(), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r["grade"] == "fail" and r.get("error_kind") != "ctx_short"


def test_per_model_context_is_loaded_and_logged_on_every_row(monkeypatch):
    seen = _fake_rig(monkeypatch, lambda n, m: _ok())
    runner.run_models(_cfg(), [_entry(context=12288)], [_case("I1"), _case("I2")])
    assert seen["switch"][0] == 12288
    assert {r["load_ctx"] for r in config.load_transcripts("m")} == {12288}
    meta = json.loads((config.RESULTS_DIR / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["context_length"] == 12288


def test_default_profile_has_no_ctx_short_handling(monkeypatch):
    """4.1 byte-for-byte: with no ctx_short_retry key a cut row is the pre-4.5 row."""
    _fake_rig(monkeypatch, lambda n, m: _cut())
    runner.run_models(_cfg(), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r.get("error_kind") != "ctx_short" and r["finish_reason"] == "length"


def test_cut_at_the_models_trained_context_is_ran_out_not_errored(monkeypatch):
    _fake_rig(monkeypatch, lambda n, m: _cut())
    monkeypatch.setattr(providers.Provider, "trained_context", lambda self, mid: 8192)
    runner.run_models(_cfg(ctx_short_retry=1), [_entry()], [_case("I1")])
    r = next(r for r in config.load_transcripts("m") if r["case_id"] == "I1")
    assert r.get("error_kind") != "ctx_short" and r["grade"] != "error"
    assert r["ctx_short_model_limit"] is True and r["finish_reason"] == "length"
