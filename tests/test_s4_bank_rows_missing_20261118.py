"""S4 (fix round 2026-11-18, AUDIT-HARNESS F4): a provider content-filter stop or an empty / dropped reply on a Predict
(code bank) or agent bank row was scored as the model's zero, because ``scoring45._NO_EMPTY_CHECK`` held every bank
category. Such a job is now MISSING (run health: the model is pending until it is re-run), as on every other
non-adult row. A bank reply with content, with reasoning, cut at its budget, or an agent loop that made tool calls is
unchanged."""
from __future__ import annotations

import pytest

from crucibleforge import scoring45

BANK = ("codebank", "codeagent", "codebank_long", "codeagent_long")


def _r(cat, **kw):
    r = {"case_id": "CB-C6-predict", "repeat": 1, "turn": None, "category": cat, "grade": "fail",
         "response": "", "finish_reason": "stop", "score": 0.0}
    r.update(kw)
    return r


@pytest.mark.parametrize("cat", BANK)
def test_a_filtered_bank_row_is_missing(cat):
    assert scoring45.job_missing_reason([_r(cat, finish_reason="content_filter")]) == "content_filter"


@pytest.mark.parametrize("cat", BANK)
def test_an_empty_bank_reply_with_nothing_at_all_is_missing(cat):
    assert scoring45.job_missing_reason([_r(cat)]) == "empty"


@pytest.mark.parametrize("cat", BANK)
def test_the_models_own_failures_stay_scored(cat):
    assert scoring45.job_missing_reason([_r(cat, response="Q1: 3 | Q2: x")]) is None
    assert scoring45.job_missing_reason([_r(cat, reasoning="let me think")]) is None
    assert scoring45.job_missing_reason([_r(cat, finish_reason="length", truncated=True)]) is None


@pytest.mark.parametrize("cat", ("codeagent", "codeagent_long"))
def test_an_agent_loop_that_worked_through_tools_is_not_empty(cat):
    row = _r(cat, tool_calls=[], agent={"turns": 6, "tool_calls": 9, "end": "finish"})
    assert scoring45.job_missing_reason([row]) is None


def test_perf_rows_are_still_never_checked():
    assert scoring45.job_missing_reason([_r("perf")]) is None
