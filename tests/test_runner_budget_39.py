"""Suite 3.9 runner: one output budget per row for every model (EVAL-FABLE
S8) and agent rows that count every turn (S9).

S8: a non-thinking model (or a thinker the auto-detect missed) was sent the
raw per-category max_tokens (coding 6144, tooluse 1024, no floor) while a
thinker got x4 within [16,384, 24,576] — a verbose non-thinker truncated and
failed silently. S9: agent/tool rows kept only the LAST turn's seconds and
tokens (omega F1: 21 s recorded for 17 turns).
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import codeenv, config, providers, runner, stopflag
from crucibleforge.api import ChatResult


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


DEPLOYED = {"regime": "deployed", "thinking_max_tokens_factor": 4,
            "thinking_max_tokens_cap": 24576, "thinking_max_tokens_floor": 16384}


class _P:
    name, type = "fake", "openai"

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def chat(self, model_id, messages, **kw):
        self.calls.append(kw)
        return self.results.pop(0)

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(defaults, thinking=False, prov=None):
    entry = {"name": "m", "model_id": "m", "thinking": thinking}
    return runner._Ctx({"defaults": defaults}, entry, prov or _P(), 40960)


# ------------------------------------------------------------------- S8

def test_a_non_thinking_model_gets_the_same_budget_as_a_thinker():
    non, think = _ctx(DEPLOYED, thinking=False), _ctx(DEPLOYED, thinking=True)
    for mt in (768, 1024, 2000, 6144):
        assert non.budget(mt) == think.budget(mt) == min(24576, max(mt * 4, 16384))
    # the old rule only outside the deployed regime, or switched off
    assert _ctx({**DEPLOYED, "regime": None}).budget(1024) == 1024
    assert _ctx({**DEPLOYED, "run_plan": {"one_budget": False}}).budget(1024) == 1024


def test_an_unknown_auto_model_is_sent_the_board_budget_from_the_first_row():
    ctx = _ctx(DEPLOYED, thinking="auto")
    assert ctx.thinking is None and ctx.budget(1024) == 16384


def test_the_thinking_decision_is_recorded_in_the_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    reasoning = {"on": False}

    def chat(self, model_id, messages, **kw):
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m",
                          reasoning_text="hmm" if reasoning["on"] else "")

    P = providers.Provider
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "list_models", lambda self, refresh=False: {"m"})
    cfg = {"defaults": {"context_length": 4096, "repeats": {}, **DEPLOYED},
           "providers": {"fake": {"type": "openai", "base_url": "http://f.invalid/v1"}},
           "judge": {"candidates": []}, "models": []}
    case = {"id": "I1", "category": "instruct", "prompt": "x", "max_tokens": 8,
            "grader": "contains", "grader_config": {"needles": ["ok"]}}
    runner.run_models(cfg, [{"name": "m", "model_id": "m", "provider": "fake"}], [case])
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["thinking_detect"] == {"thinking": None, "source": "no reasoning seen",
                                       "registry": "auto", "one_budget": True}
    reasoning["on"] = True
    runner.run_models(cfg, [{"name": "m", "model_id": "m", "provider": "fake"}], [case])
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["thinking_detect"]["thinking"] is True
    assert meta["thinking_detect"]["source"] == "probe carried reasoning"


def test_a_non_thinking_row_that_ran_out_is_escalated_under_one_budget():
    row = {"case_id": "C", "repeat": 1, "category": "coding", "finish_reason": "length",
           "thinking": None, "max_tokens_sent": 24576, "grade": "fail"}
    assert runner.escalation_jobs([row], 65536) == set()               # pre-3.9 row
    assert runner.escalation_jobs([{**row, "regime": "deployed"}], 65536) == {(None, "C", 1)}


# ------------------------------------------------------------------- S9

def _r(text="", calls=None, s=2.0, toks=10):
    return ChatResult(response_text=text, finish_reason="stop", served_model="m",
                      tool_calls=calls or [], total_s=s, gen_s=s - 0.5, ttft_s=0.5,
                      prompt_tokens=100, completion_tokens=toks)


def test_tool_script_rows_sum_every_turn():
    script = [{"user": "find it", "expect_tool": "lookup", "tool_result": "{\"x\": 1}"},
              {"expect_tool": "fetch", "tool_result": "{\"y\": 2}"},
              {"answer_contains": ["done"]}]
    prov = _P([_r(calls=[{"name": "lookup", "arguments": "{}"}], s=2, toks=10),
               _r(calls=[{"name": "fetch", "arguments": "{}"}], s=3, toks=20),
               _r("done", s=4, toks=30)])
    ctx = _ctx(DEPLOYED, thinking=True, prov=prov)
    case = {"id": "T", "category": "tooluse", "tool_script": script, "max_tokens": 1024,
            "tools": []}
    row = runner._run_tool_loop(case, {}, ctx, 1024, 0.0, 42)
    m = row["metrics"]
    assert row["n_turns"] == m["n_turns"] == 3
    assert m["total_s"] == 9 and m["completion_tokens"] == 60 and m["prompt_tokens"] == 300
    assert m["gen_s"] == 7.5 and m["tok_per_s"] == 8.0 and m["ttft_s"] == 0.5


def test_agent_env_rows_sum_every_turn(monkeypatch):
    class Env:
        def __init__(self, cfg, tier):
            self.finished, self.decoy_calls, self.test_edits = False, [], []
            self.denied, self.writes = [], []
            self.n = 0

        def call(self, name, args):
            self.n += 1
            if self.n >= 3:
                self.finished = True
            return "ok"

        def grade(self):
            return {"grade": "pass", "detail": "tests pass", "results": [], "rate": 1.0,
                    "score": 1.0}

    monkeypatch.setattr(codeenv, "RepoEnv", Env)
    prov = _P([_r(calls=[{"name": "read_file", "arguments": "{}"}], s=5, toks=50)
               for _ in range(3)])
    ctx = _ctx(DEPLOYED, thinking=True, prov=prov)
    case = {"id": "F1", "category": "codeagent", "system": "s", "prompt": "fix it",
            "max_tokens": 4096, "env": {"tier": "standard"}}
    row = runner._run_env_loop(case, {}, ctx, 4096, 0.0, 42)
    assert row["n_turns"] == row["metrics"]["n_turns"] == 3
    assert row["metrics"]["total_s"] == 15 and row["metrics"]["completion_tokens"] == 150
