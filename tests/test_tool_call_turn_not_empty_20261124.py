"""A turn that ends in a tool call is an answer, never a missing run.

2026-11-24: a hosted model passed all four runs of a scripted tool dialogue. Its last turn was a tool call with no
text and it reported no reasoning tokens, so ``scoring45._row_empty`` read each run as a dropped stream: the case
became "missing", the Tools segment "pending" and the model was not ranked.
"""
from crucibleforge import scoring45 as S


def _row(**kw):
    return {"category": "tooluse", "response": "", "tool_calls": [], "finish_reason": "stop", **kw}


def test_a_tool_call_turn_without_text_or_reasoning_is_not_empty():
    assert S._row_empty(_row(finish_reason="tool_calls", grade="pass")) is None
    assert S._row_empty(_row(finish_reason="tool_calls", grade="fail")) is None      # a failed one is a failure, not a re-run


def test_a_silent_stop_is_still_a_missing_run():
    assert S._row_empty(_row()) == "empty"
