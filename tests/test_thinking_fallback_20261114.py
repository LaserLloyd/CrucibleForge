"""Thinking fallback (2026-11-14, EXP-20261114-09).

Maintainer: "switch off thinking ... when a reply doesn't exit thinking ...
I only want to grade responses given". A reply with no visible content
(finished inside the thinking block, or ran to the cap without closing it)
is asked once more with the model's own non-thinking switch; the reply that
comes back is graded and the row says so. Same rule for every model."""
from __future__ import annotations

import pytest

from crucibleforge import runner
from crucibleforge.api import ChatResult, RequestRejected

OFF = {"enable_thinking": False}


class _P:
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, False

    def __init__(self, replies, reject_kwargs=False):
        self.replies = list(replies)
        self.kw = []
        self.reject = reject_kwargs

    def chat(self, model_id, messages, **kw):
        self.kw.append(kw)
        if self.reject and (kw.get("extra_body") or {}).get("chat_template_kwargs"):
            raise RequestRejected(400, "unknown field chat_template_kwargs")
        c, r, f, n = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=n,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p):
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True,
                                         "extra_body": {"reasoning_format": "deepseek"}}, p, 32768)
    ctx.recovery_enabled = False        # the bench profiles (reasoning_overflow_recovery: false)
    ctx.thinking_fallback = True        # profile bench45 (thinking_fallback: true)
    return ctx


def _tpl(kw):
    return (kw.get("extra_body") or {}).get("chat_template_kwargs")


def test_misrouted_reply_is_asked_again_with_thinking_off_and_graded():
    p = _P([("", "Odile said hello.", "stop", 450), ("Odile said hello.", "", "stop", 300)])
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert _tpl(p.kw[0]) is None and _tpl(p.kw[1]) == OFF
    assert p.kw[1]["extra_body"]["reasoning_format"] == "deepseek"       # the model's own extras kept
    assert r.response_text == "Odile said hello."
    fb = r.thinking_fallback
    assert fb["first"] == "misrouted" and fb["first_completion_tokens"] == 450
    assert fb["switch"] == "enable_thinking=false" and fb["fallback_visible"] is True
    assert r.billed_extra_completion_tokens == 450 and r.completion_tokens == 300


def test_capped_reply_without_content_gets_the_fallback_capped_with_content_does_not():
    p = _P([("", "word(1) word(2) ...", "length", 16384), ("Reply.", "", "stop", 200)])
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert r.thinking_fallback["first"] == "ran_to_cap" and r.response_text == "Reply."
    p2 = _P([("half an answer", "chain", "length", 16384)])
    r2 = _ctx(p2).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p2.kw) == 1 and getattr(r2, "thinking_fallback", None) is None


def test_a_second_empty_reply_stands_as_blank():
    p = _P([("", "", "stop", 5), ("", "", "stop", 5)])
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 2 and r.response_text == ""
    assert r.thinking_fallback["first"] == "empty" and r.thinking_fallback["fallback_visible"] is False


def test_answered_replies_and_perf_rows_are_single_shot():
    p = _P([("fine", "thought", "stop", 50)])
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 1 and getattr(r, "thinking_fallback", None) is None
    p2 = _P([("", "x", "stop", 50)])
    _ctx(p2).call([{"role": "user", "content": "hi"}], max_tokens=64, recover=False)
    assert len(p2.kw) == 1


def test_a_provider_that_rejects_the_switch_gets_the_plain_re_ask():
    p = _P([("", "x", "stop", 50), ("answer", "", "stop", 20)], reject_kwargs=True)
    ctx = _ctx(p)
    r = ctx.call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert r.response_text == "answer" and r.thinking_fallback["switch"] is None
    assert ctx.no_think_supported is False


@pytest.mark.parametrize("cat", ["rpdeep", "nsfw"])
def test_session_turns_get_the_fallback_and_the_row_stamp(cat):
    p = _P([("", "Turn one reply.", "stop", 400), ("Turn one reply.", "", "stop", 380),
            ("Turn two.", "short chain", "stop", 600)])
    ctx = _ctx(p)
    rows = runner._run_multiturn({"id": "S", "category": cat, "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, ctx, 64, 0.6, 0.95, 1)
    assert rows[0]["thinking_fallback"]["first"] == "misrouted" and rows[0]["response"] == "Turn one reply."
    assert rows[0]["answered_in_reasoning"] is False
    assert "thinking_fallback" not in rows[1]
    # turn 2 is asked with the card default again (thinking on): the fallback is per reply
    assert _tpl(p.kw[2]) is None
    assert rows[1]["conversation"][1] == {"role": "assistant", "content": "Turn one reply."}


def test_without_the_profile_switch_the_4_0_plain_re_ask_stands():
    # bench 4.1 (no thinking_fallback): an empty stop is re-asked unchanged, a capped reply is not
    p = _P([("", "x", "stop", 50), ("answer", "", "stop", 20)])
    ctx = _ctx(p)
    ctx.thinking_fallback = False
    r = ctx.call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert _tpl(p.kw[1]) is None and r.empty_retry and getattr(r, "thinking_fallback", None) is None
    p2 = _P([("", "chain", "length", 16384)])
    ctx2 = _ctx(p2)
    ctx2.thinking_fallback = False
    ctx2.call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p2.kw) == 1


def test_the_template_trap_record_notes_the_defect_and_sets_nothing():
    import re
    from crucibleforge import profiles
    prof = profiles.load_profile("bench45")
    cfg_p, _ = profiles.apply_profile(prof, {"defaults": {}})
    # off in bench45 since 2026-11-14 (lead) until the reference's empty turns are explained
    assert cfg_p["defaults"]["thinking_fallback"] is False
    assert cfg_p["defaults"]["empty_stop_rule"] is True
    assert cfg_p["defaults"]["on_empty_stop"] == "regenerate_then_thinking_off"
    traps = cfg_p["defaults"]["template_traps"]
    nb = next(t for t in traps if t["id"] == "nanbeige4-forced-think")
    assert re.search(nb["match"], "bartowski/Nanbeige_Nanbeige4.3-33B-GGUF/x-Q8_0")
    assert nb["handled_by"].startswith("thinking_fallback") and "settings" not in nb
    assert nb["source"].startswith("https://huggingface.co/Nanbeige/") and "EXP-20261114-09" in nb["evidence"]
