"""PLAN-451-LIMITS step 6 (2026-11-25): a thinker's round is sized so every job's prompt and budget fit; a job the
model's OWN maximum context cannot hold is a scored miss with a note ("context N < needed M"), never a missing row
owed forever; a context refusal below the model's maximum stays ctx_short (owed again)."""
from __future__ import annotations

import pytest

from crucibleforge import attempts45, profiles, providers, runner, scoring45
from crucibleforge.api import RequestRejected

B = {"factor": 10, "floor": 32768, "cap": 57344, "thinker_context": 81920}


def _cfg():
    return {"defaults": {"budgets_451": B, "max_tokens": {"coding": 6144, "instruct": 768}}}


def test_the_round_window_holds_prompt_budget_and_slack():
    long_prompt = "x" * 70000                          # ~23k tokens by the cautious count
    cases = [{"id": "C1", "category": "coding", "prompt": long_prompt},
             {"id": "I1", "category": "instruct", "prompt": "short"}]
    w = runner.prompt_window(_cfg(), {"thinking": True}, cases)
    assert w == runner.prompt_tokens_upper(long_prompt) + 63488 + runner.WINDOW_SLACK_451
    assert runner.prompt_window(_cfg(), {"thinking": True, "max_context": 65536}, cases) == 65536
    assert runner.prompt_window({"defaults": {}}, {"thinking": True}, cases) is None     # 4.5.0: unchanged
    ctx = runner.load_context(_cfg(), {"thinking": True, "context_length": 32768}, cases=cases)
    assert ctx == w > 81920
    assert runner.load_context(_cfg(), {"thinking": False, "context_length": 32768}, cases=cases) == 32768


class _P(providers.Provider):
    def __init__(self, trained):
        super().__init__(name="rig", type="openai", base_url="http://192.168.200.9:1234/v1")  # scrub-ok: made-up test address
        self._trained = trained

    def trained_context(self, model_id):
        return self._trained

    def chat(self, model_id, messages, **kw):
        raise RequestRejected(400, "the request exceeds the available context size, try increasing it")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(trained, ctx_len):
    return runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, _P(trained), ctx_len)


def test_a_job_past_the_models_own_maximum_is_a_scored_miss():
    r = _ctx(32768, 32768)._chat([{"role": "user", "content": "x" * 350}], max_tokens=40000)
    assert r.finish_reason == runner.CONTEXT_MODEL_MAX
    assert r.context_model_max.startswith("context 32768 < needed ")
    row = {"finish_reason": r.finish_reason, "response": "", "context_model_max": r.context_model_max,
           "grade": "fail", "score": 0.0}
    assert scoring45._row_empty(row) is None and not attempts45.is_infra(row)


def test_below_the_models_maximum_a_context_refusal_stays_owed():
    with pytest.raises(RequestRejected):
        _ctx(131072, 32768)._chat([{"role": "user", "content": "x"}], max_tokens=40000)
    with pytest.raises(RequestRejected):
        _ctx(None, 32768)._chat([{"role": "user", "content": "x"}], max_tokens=40000)    # unknown maximum


def test_profiles_untouched():
    assert profiles.load_profile("bench45")["budgets_451"]["thinker_context"] == 81920
