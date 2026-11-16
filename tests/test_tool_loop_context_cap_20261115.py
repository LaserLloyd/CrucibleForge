"""The tool loop ends on the context cap like the codeenv loop (lead 2026-11-15, D's 8B rc2 rows).

Every model is served the same context. The 8B's two TZ19 runs grew the conversation from their own
visible replies (single turns of 34k-58k characters) past the 73,728-token slot; ``_run_tool_loop``
sent anyway, the server refused ("The prompt does not fit the context"), and the row was stored as
an errored (excluded) row with no conversation. Ruling: a run whose own output overflows the served
context is a FAILED run, not an excluded one and not "ran out": before each send the loop checks the
fit; if it no longer fits it ends with the recorded reason "context cap", keeps the conversation and
steps so far, and grades it: grade fail (the pass half is 0), every step never reached counts as
wrong, so the step half is the share right over ALL the case's steps."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import report, runner, stepgrade
from crucibleforge.api import ChatResult, RequestRejected, TransportError

SLOT = 73_728


class _Ctx:
    reasoning_replay = False

    def __init__(self, replies, ctx_len=SLOT):
        self.replies = list(replies)
        self.sent = []
        self.ctx_len = ctx_len

    def recover_for(self, case):
        return False

    def budget(self, n):
        return n

    def call(self, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _case():
    return {"id": "T1", "category": "tooluse", "max_tokens": 4096, "system": "s",
            "tools": [{"type": "function", "function": {"name": "book", "parameters": {"type": "object"}}}],
            "tool_script": [
                {"user": "book it", "expect_tool": "book", "tool_result": "{\"ok\": true}"},
                {"user": "and Joao too?", "expect_no_tool": True, "answer_contains": ["phone"]},
                {"user": "move it", "expect_no_tool": True, "answer_contains": ["cannot"]},
                {"user": "thanks", "expect_no_tool": True, "answer_contains": ["welcome"]},
            ]}


def _call():
    return ChatResult(response_text="", tool_calls=[{"id": "c1", "name": "book", "arguments": "{}"}],
                      finish_reason="tool_calls", served_model="m")


def _say(text, finish="stop"):
    return ChatResult(response_text=text, finish_reason=finish, served_model="m")


#: one degenerate visible reply that alone overflows the slot (chars/3.5 tokens)
FLOOD = "the the the " * 25_000      # 300,000 characters ~ 85,700 tokens


def _strip(row):
    return {k: v for k, v in row.items() if k != "run_id"}


def test_a_normal_run_is_unchanged_by_the_fit_check():
    replies = [_call(), _say("I need his own phone number."), _say("I cannot move it."), _say("You are welcome.")]
    with_window = runner._run_tool_loop(_case(), {}, _Ctx(copy.deepcopy(replies)), 4096, 0.0, 42)
    no_window = runner._run_tool_loop(_case(), {}, _Ctx(copy.deepcopy(replies), ctx_len=None), 4096, 0.0, 42)
    assert _strip(with_window) == _strip(no_window)
    assert with_window["grade"] == "pass" and with_window["finish_reason"] == "stop"
    assert "end_reason" not in with_window


def test_a_ceiling_cut_run_is_unchanged_and_still_ran_out():
    replies = [_call(), _say("I need his own phone number."), _say("I cannot move it."),
               _say("You are welc", finish="length")]
    row = runner._run_tool_loop(_case(), {}, _Ctx(copy.deepcopy(replies)), 4096, 0.0, 42)
    ref = runner._run_tool_loop(_case(), {}, _Ctx(copy.deepcopy(replies), ctx_len=None), 4096, 0.0, 42)
    assert _strip(row) == _strip(ref)
    assert row["finish_reason"] == "length" and "end_reason" not in row
    assert report._row_cut(row)


@pytest.mark.parametrize("flood_finish", ["stop", "length"])
def test_a_run_whose_own_replies_overflow_the_slot_ends_on_the_context_cap(flood_finish):
    ctx = _Ctx([_call(), _say(FLOOD, finish=flood_finish), _say("never sent")])
    row = runner._run_tool_loop(_case(), {}, ctx, 4096, 0.0, 42)
    # the third request was never sent: the conversation no longer fits
    assert len(ctx.sent) == 2
    assert row["end_reason"] == "context cap"
    assert row["grade"] == "fail"                    # never "error" (excluded)
    assert row["finish_reason"] == "context cap" and row["truncated"] is False
    assert not report._row_cut(row)                  # not "ran out"
    els = row["elements"]["results"]
    assert [e["id"] for e in els] == ["step1", "step2", "step3", "step4"]
    assert [e["pass"] for e in els] == [True, False, False, False]
    assert els[2]["detail"].startswith("not reached (context cap)") and els[2]["not_reached"] is True
    assert row["elements"]["rate"] == 0.25           # 1 right over ALL 4 steps
    # the conversation so far is kept on the row (the flood included)
    assert any(m.get("content") == FLOOD for m in row["tool_conversation"])
    assert "context cap" in row["grade_detail"]
    # a report-time re-grade keeps the stored verdicts
    assert stepgrade.regrade_tool_row(row, _case())["elements"]["results"] == els


def test_a_server_refusal_that_the_prompt_does_not_fit_also_ends_on_the_cap():
    """The fit check estimates tokens; a degenerate reply can tokenize denser than the estimate, so the
    server's own refusal ends the run the same way instead of making the row errored."""
    msg = ('server error: {"message": "The prompt does not fit the context \'m\' is loaded with '
           '(73728 tokens per slot). Shorten the prompt"}')
    for exc in (TransportError(msg), RequestRejected(400, msg)):
        ctx = _Ctx([_call(), _say("I need his own phone number."), exc])
        row = runner._run_tool_loop(_case(), {}, ctx, 4096, 0.0, 42)
        assert row["end_reason"] == "context cap" and row["grade"] == "fail"
        assert [e["pass"] for e in row["elements"]["results"]] == [True, True, False, False]
        assert row["elements"]["rate"] == 0.5


def test_any_other_transport_error_still_raises():
    ctx = _Ctx([_call(), TransportError("connection reset")])
    with pytest.raises(TransportError):
        runner._run_tool_loop(_case(), {}, ctx, 4096, 0.0, 42)


def test_a_capped_row_scores_zero_on_the_pass_half_and_its_step_share_on_the_other():
    ctx = _Ctx([_call(), _say(FLOOD)])
    row = {**runner._run_tool_loop(_case(), {}, ctx, 4096, 0.0, 42),
           "category": "tooluse", "case_id": "T1", "model_label": "m", "repeat": 1}
    # the pass half reads grade (fail = 0); the step half reads the elements over all steps
    assert row["grade"] == "fail"
    assert report._element_rate([row]) == 0.25
