"""PLAN-451-LIMITS step 2 (ruling 3, 2026-11-25): a hosted thinker that reasons past the budget sent is recorded
(``over_budget``), never marked cut; ``cut`` needs the provider's own finish ``length``."""
from __future__ import annotations

from crucibleforge import disclosure


def _grok_row(**k):
    # a Grok-shaped usage block: reasoning tokens beyond max_tokens, finish stop
    return {"finish_reason": "stop", "max_tokens_sent": 16384, "response": "answer",
            "metrics": {"completion_tokens": 21000, "reasoning_tokens": 20500, "reasoning_tokens_source": "server"},
            **k}


def test_reasoning_past_the_budget_is_recorded_not_cut():
    r = _grok_row()
    assert disclosure.over_budget(r) == 20500 - 16384
    assert disclosure.row_fields(r)["cut"] is False


def test_a_length_stop_is_cut_and_nothing_is_over_when_within():
    r = _grok_row(finish_reason="length", metrics={"completion_tokens": 16384, "reasoning_tokens": 9000,
                                                   "reasoning_tokens_source": "server"})
    assert disclosure.row_fields(r)["cut"] is True and disclosure.over_budget(r) is None
    est = _grok_row(metrics={"completion_tokens": 21000, "reasoning_tokens": 20500,
                             "reasoning_tokens_source": "estimated"})
    assert disclosure.over_budget(est) is None          # only the server's own count
