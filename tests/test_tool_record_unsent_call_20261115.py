"""The stored tool conversation carries a call the model made where the case wanted prose (scrub 2026-11-15, lead):
without reasoning replay the call is not fed back (the request stays as it was), so the stored assistant turn read
as an empty reply and the call showed only in the grade's elements (DeepSeek flash, TZ16 step 4). The stored turn
now names the call under ``tool_calls_not_sent`` (not ``tool_calls``: stepgrade reads an assistant message without
``tool_calls`` as an answer step, and what was SENT is unchanged)."""
from __future__ import annotations

from crucibleforge import runner, stepgrade
from crucibleforge.api import ChatResult


class _Ctx:
    reasoning_replay = False
    ctx_len = None

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def recover_for(self, case):
        return False

    def budget(self, n):
        return n

    def call(self, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        return self.replies.pop(0)


def _case():
    return {"id": "T1", "category": "tooluse", "max_tokens": 64, "system": "s",
            "tools": [{"type": "function", "function": {"name": "book", "parameters": {"type": "object"}}}],
            "tool_script": [
                {"user": "book it", "expect_tool": "book", "tool_result": "{\"ok\": true}"},
                {"user": "and Joao too?", "expect_no_tool": True, "answer_contains": ["phone"]},
                {"user": "thanks", "expect_no_tool": True, "answer_contains": ["welcome"]},
            ]}


def test_a_call_at_a_prose_step_is_on_the_stored_turn_and_not_sent():
    call = {"id": "c2", "name": "book", "arguments": "{\"name\": \"Joao\"}"}
    ctx = _Ctx([ChatResult(response_text="", tool_calls=[{"id": "c1", "name": "book", "arguments": "{}"}],
                           finish_reason="tool_calls", served_model="m"),
                ChatResult(response_text="", tool_calls=[call], finish_reason="tool_calls", served_model="m"),
                ChatResult(response_text="You are welcome.", finish_reason="stop", served_model="m")])
    # pinned to 4.1 (profile bench): on bench45 the tool revision feeds the call back for every provider
    # (tests/test_tool_revision_20261115.py); real rows always carry their profile
    row = runner._run_tool_loop(_case(), {"profile": "bench"}, ctx, 64, 0.0, 42)
    conv = row["tool_conversation"]
    asst = [m for m in conv if m["role"] == "assistant"]
    prose = asst[1]
    assert "tool_calls" not in prose
    assert prose["tool_calls_not_sent"] == [{"name": "book", "arguments": "{\"name\": \"Joao\"}"}]
    # what went to the model is unchanged: no call on that turn in the last request
    last_req = ctx.sent[-1]
    assert not any(m.get("tool_calls_not_sent") for m in last_req)
    assert [m.get("tool_calls") is None for m in last_req if m["role"] == "assistant"] == [False, True]
    # stepgrade still pairs the two prose steps with their two stored replies (no verdict change)
    assert stepgrade.regrade_tool_row(row, _case()) is row
    assert [e["pass"] for e in row["elements"]["results"]] == [True, False, True]
