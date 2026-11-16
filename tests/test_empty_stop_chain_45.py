"""on_empty_stop: regenerate_then_thinking_off (lead 2026-11-14). An empty stop is first
regenerated unchanged (a stray end-of-turn mid-reasoning: the reference, the 8B); if the
retry is again empty with the reply left in the reasoning channel, one more attempt with
thinking off (a template that traps the reply in the block: Nanbeige). The first visible
reply is graded and goes into history; every attempt is recorded; still empty after both
-> empty_stop. At most two extra requests per turn."""
from __future__ import annotations

from crucibleforge import runner
from crucibleforge.api import ChatResult, RequestRejected

OFF = {"enable_thinking": False}
CUT = ("", "Odile picks up the lan", "stop", 636)
TRAPPED = ("", "Odile lights the lamp. Fenn: Well, evening.", "stop", 300)


class _P:
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, False

    def __init__(self, fn, reject_kwargs=False):
        self.fn, self.kw, self.reject = fn, [], reject_kwargs

    def chat(self, model_id, messages, **kw):
        self.kw.append(kw)
        off = ((kw.get("extra_body") or {}).get("chat_template_kwargs") or {}) == OFF
        if self.reject and off:
            raise RequestRejected(400, "unknown field chat_template_kwargs")
        c, r, f, n = self.fn(len(self.kw), off)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=n,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, mode="regenerate_then_thinking_off"):
    ctx = runner._Ctx({"defaults": {"on_empty_stop": mode, "empty_stop_rule": True}},
                      {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    return ctx


def _off(kw):
    return ((kw.get("extra_body") or {}).get("chat_template_kwargs") or {}) == OFF


def test_empty_once_then_answers_needs_only_the_regenerate():
    p = _P(lambda n, off: CUT if n == 1 else ("Odile lights the lamp.", "c", "stop", 200))
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 2 and not _off(p.kw[1]) and r.response_text == "Odile lights the lamp."
    esr = r.empty_stop_retry
    assert esr["mode"] == "regenerate_then_thinking_off" and esr["graded"] == 1
    assert [a["mode"] for a in esr["chain"]] == ["first", "regenerate"]
    assert [a["visible"] for a in esr["chain"]] == [False, True]
    assert esr["chain"][0]["completion_tokens"] == 636
    assert r.billed_extra_completion_tokens == 636


def test_empty_until_thinking_is_off_gets_the_third_attempt():
    p = _P(lambda n, off: ("Odile lights the lamp.", "", "stop", 120) if off else TRAPPED)
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 3 and not _off(p.kw[1]) and _off(p.kw[2])
    assert r.response_text == "Odile lights the lamp."
    esr = r.empty_stop_retry
    assert [a["mode"] for a in esr["chain"]] == ["first", "regenerate", "thinking_off"]
    assert esr["graded"] == 2 and esr["chain"][2]["switch"] == "enable_thinking=false"
    assert r.billed_extra_completion_tokens == 600


def test_never_answers_stops_after_two_extra_requests_and_is_stamped_empty_stop():
    p = _P(lambda n, off: TRAPPED)
    ctx = _ctx(p)
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, ctx, 64, 0.6, 0.95, 1)
    assert len(p.kw) == 6                                  # 3 per turn, never more
    r0 = rows[0]
    assert r0["empty_stop_retry"]["graded"] is None
    assert [a["visible"] for a in r0["empty_stop_retry"]["chain"]] == [False, False, False]
    assert r0["empty_stop"]["history"] == "empty"
    assert rows[1]["conversation"][1] == {"role": "assistant", "content": ""}


def test_a_fully_empty_retry_also_gets_the_thinking_off_attempt():
    # Jake 2026-11-14: "regenerate, then thinking off if still empty" -- any empty stop
    p = _P(lambda n, off: ("Reply.", "", "stop", 40) if off else (CUT if n == 1 else ("", "", "stop", 1)))
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 3 and _off(p.kw[2]) and r.response_text == "Reply."
    assert r.empty_stop_retry["answered_thinking_off"] is True


def test_a_provider_without_the_switch_stops_after_the_regenerate():
    p = _P(lambda n, off: TRAPPED, reject_kwargs=True)
    ctx = _ctx(p)
    r = ctx.call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert ctx.no_think_supported is False
    chain = r.empty_stop_retry["chain"]
    assert chain[-1]["mode"] == "thinking_off" and chain[-1]["switch"] is None
    assert chain[-1].get("rejected") is True and len(chain) == 3


def test_plain_regenerate_now_carries_the_chain_too():
    p = _P(lambda n, off: CUT if n == 1 else ("ok", "", "stop", 5))
    r = _ctx(p, "regenerate").call([{"role": "user", "content": "hi"}], max_tokens=64)
    esr = r.empty_stop_retry
    assert esr["mode"] == "regenerate" and esr["retry_visible"] is True and esr["graded"] == 1
    assert [a["mode"] for a in esr["chain"]] == ["first", "regenerate"]


def test_a_looping_regenerate_gets_no_thinking_off_attempt():
    # K's degenerate guard comes first: a degenerate attempt is never retried
    loop = ("", "the the the the " * 200, "stop", 800)
    p = _P(lambda n, off: CUT if n == 1 else loop)
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 2 and r.empty_stop_retry["graded"] is None
    assert runner._degenerate_result(r)


def test_a_looping_first_attempt_is_never_regenerated():
    loop = ("", "ab" * 2000, "stop", 900)
    p = _P(lambda n, off: loop)
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 1 and getattr(r, "empty_stop_retry", None) is None


def test_every_retry_gets_a_fresh_seed_recorded_and_stable_across_reruns():
    def run():
        p = _P(lambda n, off: ("Reply.", "", "stop", 40) if off else TRAPPED)
        r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
        return p, r
    p, r = run()
    seeds = [k.get("seed") for k in p.kw]
    assert seeds == [41, runner.retry_seed(41, 1), runner.retry_seed(41, 2)] and len(set(seeds)) == 3
    esr = r.empty_stop_retry
    assert esr["first_seed"] == 41 and esr["retry_seed"] == seeds[1]
    assert [c["seed"] for c in esr["chain"]] == seeds
    p2, _ = run()
    assert [k.get("seed") for k in p2.kw] == seeds


def test_a_thinking_off_answer_marks_the_row_and_goes_into_history():
    p = _P(lambda n, off: ("Odile lights the lamp.", "", "stop", 120) if off else TRAPPED)
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, _ctx(p), 64, 0.6, 0.95, 1)
    r0 = rows[0]
    assert r0["answered_thinking_off"] is True and "empty_stop" not in r0
    assert r0["empty_stop_retry"]["answered_thinking_off"] is True
    assert rows[1]["conversation"][1] == {"role": "assistant", "content": "Odile lights the lamp."}
    assert "answered_thinking_off" not in rows[1] or rows[1]["answered_thinking_off"] is True


def test_a_regenerated_answer_is_not_marked_thinking_off():
    p = _P(lambda n, off: CUT if n == 1 else ("Fine.", "c", "stop", 50))
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert r.empty_stop_retry["answered_thinking_off"] is False


def test_a_ran_out_reply_is_untouched():
    p = _P(lambda n, off: ("", "word " * 50, "length", 16384))
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.kw) == 1 and getattr(r, "empty_stop_retry", None) is None


def test_bench45_uses_the_chain():
    from crucibleforge import profiles
    cfg, _ = profiles.apply_profile(profiles.load_profile("bench45"), {"defaults": {}})
    assert cfg["defaults"]["on_empty_stop"] == "regenerate_then_thinking_off"
    assert cfg["defaults"]["thinking_fallback"] is False and cfg["defaults"]["empty_stop_rule"] is True


def test_the_board_counts_replies_answered_with_thinking_off():
    from crucibleforge import report
    out = report._model_notes("m", {"answered_thinking_off": 4})
    assert any("4 reply(ies) needed thinking off" in line for line in out)
