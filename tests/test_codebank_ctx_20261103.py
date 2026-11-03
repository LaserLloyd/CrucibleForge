"""A code-bank row must FIT the model's trained window, or it is skipped (n/a).

2026-11-03: a code-bank row carries NO ``min_context``, so the per-case
over-budget skip test never fired for it: on a narrow model (the rig's
``n_ctx_train``) the row's ``bank_budget`` was clamped down to the small
window and the row could still grade "pass". The runner now asks a bank row
for its WHOLE budget plus the escalation pad (the bank's load floor,
``bank_context``) and skips it (n/a) with the SAME branch every other
over-budget row uses.
"""
from __future__ import annotations

import pytest

from crucibleforge import config, providers, runner, stopflag
from crucibleforge.api import ChatResult


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _cfg():
    return {"defaults": {"context_length": 32768, "context_floor": 40960, "bank_budget": 65536,
                         "repeats": {"perf": 1}},
            "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
            "judge": {"candidates": []}, "models": []}


ENTRY = {"name": "m", "model_id": "m", "provider": "p", "thinking": False}

#: the bank's load floor: the whole bank budget plus the escalation pad
BANK_FLOOR = 65536 + runner.ESCALATION_CTX_PAD      # 73,728


def _case(cid, cat="instruct", **kw):
    return {"id": cid, "category": cat, "prompt": f"say ok {cid}", "max_tokens": 64,
            "grader": "contains", "grader_config": {"needles": ["ok"]}, **kw}


def _fake_rig(monkeypatch, trained):
    seen = {"switch": [], "chat": []}
    cur = {"ctx": None}

    def switch(self, model_id, ctx=None):
        if trained and ctx and ctx > trained:
            raise providers.ProviderError(f"'{model_id}' is trained to {trained} tokens")
        seen["switch"].append(ctx)
        cur["ctx"] = ctx
        return 1.0

    def chat(self, model_id, messages, **kw):
        seen["chat"].append((cur["ctx"], messages[-1]["content"]))
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m",
                          completion_tokens=3, total_s=0.1)

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", switch)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: trained)
    monkeypatch.setattr(P, "live_context", lambda self, mid: cur["ctx"])
    monkeypatch.setattr(P, "loaded_plan_for",
                        lambda self, mid: {"ctx_size": cur["ctx"], "parallel": 2})
    monkeypatch.setattr(P, "workers", lambda self, mid: 2)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return seen


def _bank_case(cid="B1"):
    """A code-bank row: the category is what puts it on the bank's fixed
    budget (bank_budget), so its need is the whole budget, not a case
    ``min_context``."""
    return _case(cid, "codebank", system="You are a senior software engineer.")


# --------------------------------------------- (a) a narrow model: SKIP (n/a)

def test_a_narrow_model_skips_a_code_bank_row_instead_of_grading_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch, 8192)
    cases = [_case("P1", "perf"), _case("I1"), _bank_case("B1")]
    summary = runner.run_models(_cfg(), [ENTRY], cases)

    assert seen["switch"] and set(seen["switch"]) == {8192}      # never asked past 8192
    rows = {r["case_id"]: r for r in config.load_transcripts("m")}
    bank = rows["B1"]
    assert bank["skipped"] is True                               # n/a, not a failure
    assert bank["grade"] == "skipped"
    assert bank["metrics"] == {}
    assert str(BANK_FLOOR) in bank["grade_detail"]              # the need it asked for
    assert "8192" in bank["grade_detail"]                       # the model's window
    assert summary["m"]["skipped"] == 1
    assert not summary.get("m", {}).get("failed")
    assert rows["I1"]["grade"] == "pass"                        # other rows are untouched


# ------------------------------------------- (b) a wide model: RUN and GRADE

def test_a_wide_model_runs_and_grades_a_code_bank_row(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch, 131072)
    cases = [_case("P1", "perf"), _case("I1"), _bank_case("B1")]
    summary = runner.run_models(_cfg(), [ENTRY], cases)

    assert BANK_FLOOR in seen["switch"]                          # the bank round's load floor
    rows = {r["case_id"]: r for r in config.load_transcripts("m")}
    bank = rows["B1"]
    assert not bank.get("skipped")
    assert bank["grade"] == "pass"                               # graded, not skipped
    assert bank["max_tokens_sent"] == 65536                     # the WHOLE bank budget
    assert summary["m"]["skipped"] == 0
    assert not summary.get("m", {}).get("failed")


# ------------------------------------------------- the need is the load floor

def test_a_code_bank_row_needs_the_bank_load_floor():
    cfg = _cfg()
    bank = _bank_case("B1")
    assert runner.fixed_budget(cfg, bank) == 65536
    assert runner.bank_context(cfg, [bank]) == BANK_FLOOR
    assert runner.wide_context(cfg, [bank]) == BANK_FLOOR


# ------------------------------------- only code-bank rows are gated (scope)

def test_a_budget_row_outside_the_bank_is_not_gated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _fake_rig(monkeypatch, 8192)
    cases = [_case("P1", "perf"), _case("I1"),
             _case("C1", "coding", budget=8192), _bank_case("B1")]
    summary = runner.run_models(_cfg(), [ENTRY], cases)

    rows = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert rows["C1"]["grade"] == "pass"          # a non-bank budget row still runs
    assert rows["B1"]["grade"] == "skipped"       # the bank row is the gated one
    assert summary["m"]["skipped"] == 1
