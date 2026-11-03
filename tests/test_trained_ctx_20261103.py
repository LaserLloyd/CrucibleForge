"""A rig model trained on a window smaller than the bench context.

2026-11-02/03: bench-all asked every rig model for ctx 73,728 (the code-bank
load floor); StudioForge refuses a load past the model's trained window
(HTTP 400 "is trained to 8192 tokens"), so smolvlm-256m, qwen2.5-1.5b,
gemma-2-2b and noromaid-7b failed in seconds, every night. The rig publishes
``n_ctx_train`` in /v1/models: the runner now loads at most that wide, and the
existing narrow-window path skips (n/a) the rows that cannot fit.
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


def test_a_model_trained_narrower_than_the_bench_loads_at_its_trained_window(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch, 8192)
    cases = [_case("P1", "perf"), _case("I1"), _case("W1", min_context=40960)]
    summary = runner.run_models(_cfg(), [ENTRY], cases)
    assert seen["switch"] and set(seen["switch"]) == {8192}     # never asked past 8192
    rows = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert rows["I1"]["grade"] == "pass"                        # what fits runs
    assert rows["W1"]["grade"] == "skipped"                     # what cannot fit is n/a
    assert "40960" in rows["W1"]["grade_detail"]
    assert not summary.get("m", {}).get("failed")


def test_an_unknown_trained_window_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch, None)
    runner.run_models(_cfg(), [ENTRY], [_case("P1", "perf"), _case("I1")])
    assert seen["switch"] == [40960]


def test_trained_context_reads_the_rig_listing(monkeypatch):
    from crucibleforge import studioforge
    monkeypatch.setattr(studioforge, "list_models_full", lambda *a, **k: [
        {"id": "small", "studioforge": {"n_ctx_train": 8192}},
        {"id": "nometa"}])
    cfg = {"providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"},
                         "q": {"type": "openai", "base_url": "http://q.invalid/v1"}}}
    p = providers.get_provider(cfg, "p")
    assert p.trained_context("small") == 8192
    assert p.trained_context("nometa") is None
    assert p.trained_context("absent") is None
    q = providers.get_provider(cfg, "q")
    assert q.trained_context("small") is None


# ------------------------------------------- bench-all --first (same day)

def test_first_names_a_model_with_nothing_to_generate_ladder_goes_first_and_plan_says_so():
    from crucibleforge import bench_all
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": n, "model_id": f"rig/{n}", "provider": "sf"}
                      for n in ("a", "b", "z")]}
    full = [{"case_id": "A", "grade": "pass", "finish_reason": "stop"}]
    p = bench_all.plan(cfg, {"rig/a", "rig/b", "rig/z"}, case_ids=["A"], judge_id="j",
                       pending_for=lambda l: 0, attempts_for=lambda l: {},
                       rows_for=lambda l: [] if l == "b" else full, room_for=lambda l: full,
                       rungs=[], first=["z"],
                       ladder_for=lambda l: {"names": ["nsfw/x#1"], "jobs": 2})
    assert [lp["model"] for lp in p["ladder"]] == ["z", "a"]
    assert p["first_notes"] == [("z", "nothing to generate — ladder packs first in the ladder "
                                      "phase (after every generation group and the judge)")]
    text = bench_all.format_plan(p)
    assert "! --first z: nothing to generate" in text
    assert not any(l.lstrip().startswith("> ") and "--first" in l for l in text.splitlines())
    assert bench_all.first_ahead(["a", "b", "z"], ["z", "q"]) == ["z", "a", "b"]
