"""M1 early stop (R3, crucibleforge/earlystop.py).

2026-11-13: Nanbeige4.3-33B ran 12.8 slot-hours; 8 of its first 30 answers
ended at the token limit and (before 30cd052) 20 of its first 31 read blank.
Nothing stopped it. Now a model whose first 20..30 answers are mostly blank or
cut off is stopped, its rows kept, the meta says why, and the batch goes on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from crucibleforge import config, earlystop, profiles, providers, runner, stopflag
from crucibleforge.api import ChatResult

FIX = Path(__file__).resolve().parent / "fixtures" / "nanbeige_6b634768_first40.json"


def _row(resp="ok", reasoning="", finish="stop", tools=None, **kw):
    return {"response": resp, "reasoning": reasoning, "finish_reason": finish,
            "tool_calls": tools or [], "truncated": finish == "length", **kw}


def _cfg_es(**es):
    return {"defaults": {"early_stop": {**earlystop.DEFAULTS, **es}}}


# ------------------------------------------------------------ classification

def test_a_finished_answer_in_the_reasoning_channel_is_not_blank_but_is_misrouted():
    c = earlystop.classify(_row(resp="", reasoning="Hello there, traveller."))
    assert c == {"blank": False, "misrouted": True, "token_limit": False, "degenerate": False}


def test_a_cut_off_reasoning_channel_is_blank_and_token_limit_not_misrouted():
    c = earlystop.classify(_row(resp="", reasoning="mm mm mm", finish="length"))
    assert c == {"blank": True, "misrouted": False, "token_limit": True, "degenerate": False}


def test_token_limit_is_the_servers_cut_at_the_limit_in_force_never_a_budget():
    # R23: no per-instance token budget; a very long think that then answers is fine
    long_think = _row(resp="42", reasoning="x" * 200_000, max_tokens_sent=131072)
    long_think["metrics"] = {"completion_tokens": 120_000}
    assert earlystop.token_limited(long_think) is False
    assert earlystop.token_limited(_row(resp="", reasoning="long think")) is False
    # cut at whatever limit was sent — 4k or 128k, the number is never compared
    for limit in (4096, 131072):
        assert earlystop.token_limited({**_row(resp="half an answ"), "finish_reason": "length",
                                        "max_tokens_sent": limit}) is True
    # a wall-clock / turn cut is a different limit
    wall = {**_row(resp=""), "finish_reason": "wall_timeout", "truncated": True}
    assert earlystop.token_limited(wall) is False


def test_the_trip_records_the_limits_in_force_on_the_cut_rows():
    rows = [{**_row(resp="", reasoning="x", finish="length"), "max_tokens_sent": m}
            for m in (65536, 131072)] * 3 + [_row()] * 14
    rec = earlystop.EarlyStop(_cfg_es()).observe(rows)
    assert rec["signal"] == "token_limit" and rec["cut_at_max_tokens_sent"] == [65536, 131072]


def test_many_short_chains_feed_one_window_in_completion_order():
    # 4.5 Main: one instance per rung, a chain stops at its first failure;
    # chains interleave and end early — the counter is per run, not per chain
    rows, cut = [], {"finish": "length", "resp": "", "reasoning": "loop"}
    for rung in range(1, 4):
        for chain in "ABCDEFGH":
            if chain in "AB" and rung > 1:
                continue                                  # A, B stopped at rung 1
            r = _row(**cut) if (chain in "CD" and rung == 2) else _row()
            rows.append({**r, "case_id": f"chain-{chain}-r{rung}"})
    es = earlystop.EarlyStop(_cfg_es())
    assert es.observe(rows) is None and es.n == 20        # 8 + 6 + 6 answers
    assert es.counts["token_limit"] == 2
    looping = [{**_row(**cut), "case_id": f"chain-{c}-r1"} for c in "ABCDEFGHIJ"] + \
              [{**_row(), "case_id": f"chain-{c}-r1"} for c in "KLMNOPQRST"]
    assert earlystop.EarlyStop(_cfg_es()).observe(looping)["after_answers"] == 20


def test_skipped_errored_and_repair_rows_are_not_answers():
    es = earlystop.EarlyStop(_cfg_es(after_answers=2, max_answers=2))
    rows = [_row(resp="", skipped=True), _row(resp="", error="x"),
            _row(resp="", grade="error"), _row(resp="", reasoning_answer_repair={"x": 1})]
    assert es.observe(rows) is None and es.n == 0


# ------------------------------------------------------------ the window

def test_nothing_is_judged_before_the_first_window():
    es = earlystop.EarlyStop(_cfg_es())
    assert es.observe([_row(resp="")] * 19) is None
    assert es.n == 19 and es.record()["note"]
    rec = es.observe([_row(resp="")])
    assert rec["signal"] == "blank" and rec["after_answers"] == 20 and rec["value"] == 1.0
    assert rec["threshold"] == 0.5 and set(rec) >= {"signal", "value", "threshold",
                                                    "after_answers", "counts"}


def test_re_evaluated_up_to_max_answers_then_never_again():
    es = earlystop.EarlyStop(_cfg_es())
    good = [_row()] * 20
    assert es.observe(good) is None
    # cut-off answers after 20 good ones: 5/25 = 0.20 holds, 6/26 > 0.20 trips
    assert es.observe([_row(resp="", reasoning="x", finish="length")] * 5) is None
    rec = es.observe([_row(resp="", reasoning="x", finish="length")])
    assert rec["signal"] == "token_limit" and rec["after_answers"] == 26

    late = earlystop.EarlyStop(_cfg_es())
    late.observe([_row()] * 30)
    assert late.observe([_row(resp="")] * 100) is None      # past the window: never judged
    assert late.n == 30 and late.record()["tripped"] is False


def test_thresholds_come_from_the_profile_block():
    rows = [_row(resp="", reasoning="x", finish="length")] * 5 + [_row()] * 15
    assert earlystop.EarlyStop(_cfg_es()).observe(rows)["signal"] == "token_limit"
    assert earlystop.EarlyStop(_cfg_es(token_limit_rate_gt=0.25)).observe(rows) is None
    assert earlystop.EarlyStop(_cfg_es(enabled=False)).observe(rows) is None
    assert earlystop.settings(_cfg_es())["source"] == "profile"
    assert earlystop.settings({})["source"] == "default"


def test_bench45_profile_declares_the_thresholds_and_apply_profile_copies_them():
    prof = profiles.load_profile("bench45")
    assert prof["early_stop"] == {"enabled": True, "after_answers": 20, "max_answers": 30,
                                  "blank_rate_gt": 0.5, "token_limit_rate_gt": 0.2,
                                  "misrouted_rate_gt": 0.25}
    cfg2, _ = profiles.apply_profile(prof, {"defaults": {}})
    assert earlystop.settings(cfg2)["source"] == "profile"


def test_misrouted_trips_above_a_quarter_and_is_report_only_when_unset():
    rows = [_row(resp="", reasoning="answer")] * 6 + [_row()] * 14
    rec = earlystop.EarlyStop(_cfg_es()).observe(rows)          # 6/20 = 0.30 > 0.25
    assert rec["signal"] == "misrouted" and rec["counts"]["blank"] == 0
    es = earlystop.EarlyStop(_cfg_es(misrouted_rate_gt=None))
    assert es.observe([_row(resp="", reasoning="answer")] * 30) is None
    assert es.record()["counts"] == {"blank": 0, "misrouted": 30, "token_limit": 0, "degenerate": 0}


def test_an_unreadable_row_fails_closed_with_an_error_not_a_pass():
    es = earlystop.EarlyStop(_cfg_es())
    assert es.observe([_row()] * 5 + ["not a row"]) is None
    rec = es.record()
    assert "error" in rec and "answer 6" in rec["error"]
    assert es.observe([_row(resp="")] * 40) is None          # disabled after the error
    assert earlystop.replay([_row()] * 5 + [None])["error"]


# ------------------------------------------------------------ the stored Nanbeige run

def test_the_stored_nanbeige_sequence_trips_inside_the_first_window():
    rows = json.loads(FIX.read_text(encoding="utf-8"))["rows"]
    rec = earlystop.replay(rows)
    assert rec["tripped"] is True
    # the template trap shows first: 11 of the first 20 answers sat in the
    # reasoning channel (finished, so they score — but the run measures the template)
    assert rec["signal"] == "misrouted" and rec["after_answers"] == 20 and rec["value"] > 0.5
    # without the misrouted trip the token-limit signal still catches it
    rec2 = earlystop.EarlyStop(_cfg_es(misrouted_rate_gt=None)).observe(rows)
    assert rec2["signal"] == "token_limit" and rec2["after_answers"] <= 30
    # under the pre-30cd052 rule the blank signal alone would have tripped too
    old = [{**r, "reasoning": ""} for r in rows]
    assert earlystop.replay(old)["signals"][0] == "blank"


# ------------------------------------------------------------ wired into the runner

@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _fake_rig(monkeypatch, bad_model="bad"):
    calls = {"bad": 0, "good": 0}
    cur = {"m": None}

    def switch(self, model_id, ctx=None):
        cur["m"] = model_id
        return 1.0

    def chat(self, model_id, messages, **kw):
        calls[model_id] += 1
        if model_id == bad_model:   # Nanbeige-style: loops in reasoning to the limit
            return ChatResult(response_text="", reasoning_text="mm mm mm", finish_reason="length",
                              served_model=model_id, completion_tokens=64, total_s=0.1)
        return ChatResult(response_text="ok", finish_reason="stop", served_model=model_id,
                          completion_tokens=3, total_s=0.1)

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", switch)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "live_context", lambda self, mid: None)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {"parallel": 1})
    monkeypatch.setattr(P, "workers", lambda self, mid: 1)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return calls


def _case(i):
    return {"id": f"I{i:02d}", "category": "instruct", "prompt": f"say ok {i}", "max_tokens": 64,
            "grader": "contains", "grader_config": {"needles": ["ok"]}}


def _run_two(tmp_path, monkeypatch, **cfg_extra):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _fake_rig(monkeypatch)
    cfg = {"defaults": {"context_length": 32768, "repeats": {"instruct": 1},
                        "early_stop": dict(earlystop.DEFAULTS)},
           "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
           "judge": {"candidates": []}, "models": [], **cfg_extra}
    cases = [_case(i) for i in range(40)]
    entries = [{"name": "bad", "model_id": "bad", "provider": "p"},
               {"name": "good", "model_id": "good", "provider": "p"}]
    return cases, runner.run_models(cfg, entries, cases, skip_conformance=True)


def test_a_targeted_rerun_is_not_judged(tmp_path, monkeypatch):
    # the profile has 60 cases; this run re-runs 40 of them
    _run_two(tmp_path, monkeypatch, _profile_case_ids=[f"I{i:02d}" for i in range(60)])
    bad = json.loads((tmp_path / "meta_bad.json").read_text(encoding="utf-8"))
    assert bad["failed"] is False and bad["finished"]
    assert bad["early_stop"]["checked"] is False
    assert bad["early_stop"]["skipped"] == "targeted rerun: 40 of 60 profile cases"
    assert len(config.load_transcripts("bad")) == 40


def test_a_whole_profile_run_is_judged(tmp_path, monkeypatch):
    _run_two(tmp_path, monkeypatch, _profile_case_ids=[f"I{i:02d}" for i in range(40)])
    bad = json.loads((tmp_path / "meta_bad.json").read_text(encoding="utf-8"))
    assert bad["early_stop"]["signal"] == "blank" and bad["publishable"] is False


def test_a_tripped_model_stops_keeps_its_rows_and_the_batch_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    _fake_rig(monkeypatch)
    cfg = {"defaults": {"context_length": 32768, "repeats": {"instruct": 1},
                        "early_stop": dict(earlystop.DEFAULTS)},
           "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
           "judge": {"candidates": []}, "models": []}
    cases = [_case(i) for i in range(40)]
    entries = [{"name": "bad", "model_id": "bad", "provider": "p"},
               {"name": "good", "model_id": "good", "provider": "p"}]
    summary = runner.run_models(cfg, entries, cases, skip_conformance=True)

    bad = json.loads((tmp_path / "meta_bad.json").read_text(encoding="utf-8"))
    assert bad["failed"] is True and bad["publishable"] is False
    es = bad["early_stop"]
    # every answer is a cut-off loop: blank (checked first) and token limit
    assert es["signal"] == "blank" and es["value"] == 1.0 and es["threshold"] == 0.5
    assert es["signals"] == ["blank", "token_limit"] and es["after_answers"] == 20
    assert es["bench_run_id"] and "early stop" in bad["error"]
    assert summary["bad"]["early_stop"]["signal"] == "blank"
    kept = config.load_transcripts("bad")
    assert 20 <= len(kept) <= 22                    # stopped at the 20th answer, rows kept

    good = json.loads((tmp_path / "meta_good.json").read_text(encoding="utf-8"))
    assert good["failed"] is False and good["finished"]
    assert good["early_stop"]["tripped"] is False and good["early_stop"]["answers_seen"] == 30
    assert "publishable" not in good
    assert len(config.load_transcripts("good")) == 40              # the next model ran in full


# ------------------------------------------- agent rows (tool work in the conversation)
# Code-agent rows keep tool_calls [] at row level; the work lives in
# tool_conversation and the last turn may carry no text (finish=tool_calls).

def _agent_conv(turns=3):
    conv = [{"role": "system", "content": "s"}, {"role": "user", "content": "fix it"}]
    for i in range(turns):
        conv.append({"role": "assistant", "content": "",
                     "tool_calls": [{"id": f"c{i}", "type": "function",
                                     "function": {"name": "run", "arguments": "{}"}}]})
        conv.append({"role": "tool", "tool_call_id": f"c{i}", "content": "ok"})
    return conv


def test_an_agent_row_with_tool_work_and_no_closing_text_is_not_blank():
    c = earlystop.classify(_row(resp="", finish="tool_calls", tool_conversation=_agent_conv()))
    assert c == {"blank": False, "misrouted": False, "token_limit": False, "degenerate": False}


def test_an_agent_row_with_tool_work_and_an_empty_stop_is_not_blank():
    c = earlystop.classify(_row(resp="", finish="stop", tool_conversation=_agent_conv()))
    assert c["blank"] is False


def test_an_empty_finished_reply_with_no_tool_work_is_still_blank():
    assert earlystop.classify(_row(resp=""))["blank"] is True
    # a conversation with no tool call in it is not tool work
    conv = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": ""}]
    assert earlystop.classify(_row(resp="", tool_conversation=conv))["blank"] is True
    assert earlystop.classify(_row(resp="", tool_conversation=[]))["blank"] is True


def test_an_empty_stop_stamp_still_counts_as_blank():
    assert earlystop.classify(_row(resp="", empty_stop=True))["blank"] is True
    assert earlystop.classify(_row(resp="", finish="tool_calls", tool_conversation=_agent_conv(),
                                   empty_stop=True))["blank"] is True


def test_twenty_agent_rows_without_closing_text_do_not_trip_the_blank_rate():
    es = earlystop.EarlyStop(_cfg_es(), label="m")
    rows = [_row(resp="", finish="tool_calls", tool_conversation=_agent_conv(), case_id=f"CB-{i}")
            for i in range(15)] + [_row(case_id=f"x{i}") for i in range(5)]
    assert es.observe(rows) is None
    assert es.counts["blank"] == 0
