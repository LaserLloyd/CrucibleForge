"""4.5 empty stop (2026-11-14, lead): a reply that FINISHED (finish=stop) with no
visible content and no tool call is stamped ``empty_stop``, counted as a blank by
the early stop and per set in elemstats / on the board, and goes into a session's
history as an EMPTY assistant turn -- never as the model's answer, never replayed
as reasoning (the reference's turns 7-12 at 9 slots: reasoning cut mid-word)."""
from __future__ import annotations

from crucibleforge import earlystop, elemstats, report, runner
from crucibleforge.api import ChatResult


class _P:
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, True

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, model_id, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        c, r, f, n = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=n,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, rule=True):
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    ctx.thinking_fallback = False
    ctx.empty_stop_rule = rule
    ctx.reasoning_replay = True
    return ctx


CUT = ("", "The user wants me to keep the scene going. Odile picks up the lan", "stop", 636)


def _session(rule):
    # turn 1 cut (the plain 4.0 re-ask comes back cut too), turn 2 answers
    p = _P([CUT, CUT, ("Odile lights the lamp.", "short chain", "stop", 300)])
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, _ctx(p, rule), 64, 0.6, 0.95, 1)
    return p, rows


def test_a_cut_turn_is_stamped_and_goes_into_history_empty():
    p, rows = _session(True)
    es = rows[0]["empty_stop"]
    assert es["history"] == "empty" and es["reasoning_chars"] == len(CUT[1]) and es["completion_tokens"] == 636
    # recorded conversation keeps the empty turn as it happened
    assert rows[1]["conversation"][1] == {"role": "assistant", "content": ""}
    # what was SENT for turn 2: no assistant turn, no replayed draft, user turns joined
    last = p.sent[-1]
    assert [m["role"] for m in last if m["role"] != "system"] == ["user"]
    assert not any("Odile picks up" in (m.get("content") or "") + (m.get("reasoning_content") or "")
                   for m in last)
    assert "empty_stop" not in rows[1]


def test_without_the_rule_the_30cd052_answer_rule_stands():
    p, rows = _session(False)
    assert "empty_stop" not in rows[0]
    assert rows[1]["conversation"][1]["content"].startswith("The user wants me")


def test_single_rows_are_stamped_only_under_the_rule():
    case = {"id": "c", "category": "general", "prompt": "q", "max_tokens": 64}
    row = runner._run_single(case, {}, _ctx(_P([CUT, CUT])), 64, 0.6, 0.95, 1)
    assert row["empty_stop"]["reasoning_chars"] > 0
    row2 = runner._run_single(case, {}, _ctx(_P([CUT, CUT]), rule=False), 64, 0.6, 0.95, 1)
    assert "empty_stop" not in row2


def test_early_stop_counts_an_empty_stop_row_as_blank():
    base = {"finish_reason": "stop", "response": "", "reasoning": "draft cut mid-wo", "tool_calls": []}
    assert earlystop.classify(base)["blank"] is False          # 30cd052: read from reasoning
    assert earlystop.classify({**base, "empty_stop": {"history": "empty"}})["blank"] is True


def test_elemstats_and_the_board_count_empty_stops():
    els = {"e1": {"stratum": "s", "unit": "u1", "score": 0.0, "outcome": "fail", "think": 1.0,
                  "answer": 0.0, "cell": "s", "concept": "-", "band": "-", "rule": "zero",
                  "unit_prompt": 10.0, "unit_cached": 0.0, "unit_cache_known": 0.0, "unit_empty_stop": 2},
           "e2": {"stratum": "s", "unit": "u1", "score": 1.0, "outcome": "pass", "think": 1.0,
                  "answer": 1.0, "cell": "s", "concept": "-", "band": "-", "rule": "zero",
                  "unit_prompt": 10.0, "unit_cached": 0.0, "unit_cache_known": 0.0, "unit_empty_stop": 2}}
    rows = elemstats.table({"m": els})
    assert rows[0]["empty_stop"] == 2                         # per unit, not per element
    assert "| empty stop |" in elemstats.render(rows) and rows and elemstats.render(rows).splitlines()[2].endswith("| 2 |")
    out = report._model_notes("m", {"empty_stop": 3})
    assert any("3 reply(ies) finished with no visible content" in line for line in out)


def test_an_unclosed_fragment_is_never_replayed_as_reasoning():
    capped = ChatResult(response_text="", reasoning_text="cut at the cap mid-wo", finish_reason="length",
                        completion_tokens=16384, prompt_tokens=10, served_model="m")
    assert "reasoning_content" not in runner.assistant_turn(capped, True)
    ok = ChatResult(response_text="Answer.", reasoning_text="closed chain", finish_reason="stop",
                    completion_tokens=50, prompt_tokens=10, served_model="m")
    assert runner.assistant_turn(ok, True)["reasoning_content"] == "closed chain"


def test_on_empty_stop_regenerate_retries_session_turns_with_the_same_settings():
    p = _P([CUT, ("Odile lights the lamp.", "chain", "stop", 300), ("Turn two.", "c", "stop", 100)])
    ctx = runner._Ctx({"defaults": {"on_empty_stop": "regenerate"}},
                      {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    ctx.empty_stop_rule = True
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi", "chain": None}, {}, ctx, 64, 0.6, 0.95, 1)
    r0 = rows[0]
    assert r0["response"] == "Odile lights the lamp." and "empty_stop" not in r0
    assert r0["empty_stop_retry"]["mode"] == "regenerate" and r0["empty_stop_retry"]["retry_visible"] is True
    assert r0["empty_stop_retry"]["first_completion_tokens"] == 636
    assert len(p.sent) == 3 and p.sent[0] == p.sent[1]          # same request, settings unchanged
    assert rows[1]["conversation"][1]["content"] == "Odile lights the lamp."


def test_on_empty_stop_none_and_bad_values():
    import pytest
    p = _P([CUT])
    ctx = runner._Ctx({"defaults": {"on_empty_stop": "none"}}, {"name": "m", "model_id": "m", "thinking": True},
                      p, 32768)
    ctx.recovery_enabled = False
    r = ctx.call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert len(p.sent) == 1 and not getattr(r, "empty_retry", False)
    with pytest.raises(ValueError):
        runner._Ctx({"defaults": {"on_empty_stop": "maybe"}}, {"name": "m", "model_id": "m"}, _P([]), 32768)
