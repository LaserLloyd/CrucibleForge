"""Suite 3.9 runner: split rig rounds, bank rows first, ladder budgets
(EVAL-FABLE M7, STEP4).

STEP4 (2026-10-28): omega's T1 loaded at ctx 73,728 because the round had
code-bank rows (bank_budget 65,536 + pad); the rig then gave 4 parallel slots
(8 and 6 refused, 507) for ~37 rows that need only 40,960. Now the bank rows
run in their own round at 73,728 after the standard rows ran at the floor.
"""
from __future__ import annotations

import json

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


def _cfg(ptype="studioforge", **defaults):
    return {"defaults": {"context_length": 32768, "context_floor": 40960, "bank_budget": 65536,
                         "repeats": {"perf": 1}, **defaults},
            "providers": {"p": {"type": ptype, "base_url": "http://p.invalid/v1"}},
            "judge": {"candidates": []}, "models": []}


ENTRY = {"name": "m", "model_id": "m", "provider": "p", "thinking": True}


def _case(cid, cat="instruct", **kw):
    return {"id": cid, "category": cat, "prompt": f"say ok {cid}", "max_tokens": 64,
            "grader": "contains", "grader_config": {"needles": ["ok"]}, **kw}


CASES = [_case("P1", "perf"), _case("I1"), _case("I2", "math"),
         _case("B1", "codebank"), _case("A1", "codeagent")]


# ------------------------------------------------------------ the plan

def test_rig_run_splits_into_a_floor_round_and_a_bank_round():
    rounds = runner.plan_rounds(_cfg(), ENTRY, CASES)
    assert [(r["name"], r["ctx"]) for r in rounds] == [("A", 40960), ("B", 73728)]
    assert [c["id"] for c in rounds[0]["cases"]] == ["P1", "I1", "I2"]
    assert [c["id"] for c in rounds[1]["cases"]] == ["B1", "A1"]
    assert all(r["only_jobs"] is None for r in rounds)     # never recover mode


def test_api_models_one_rung_rounds_and_a_switch_keep_one_round():
    assert len(runner.plan_rounds(_cfg("openai"), ENTRY, CASES)) == 1
    assert len(runner.plan_rounds(_cfg(), ENTRY, CASES, rung=65536)) == 1
    assert len(runner.plan_rounds(_cfg(run_plan={"split_rig_rounds": False}), ENTRY, CASES)) == 1
    # nothing but bank rows (and perf): one round at the bank context
    r = runner.plan_rounds(_cfg(), ENTRY, [CASES[0], CASES[3]])
    assert [(x["name"], x["ctx"]) for x in r] == [("all", 73728)]


def test_a_jobs_round_that_mixes_bank_and_non_bank_splits_its_jobs():
    jobs = {(None, "I2", 1), (None, "B1", 1)}
    rounds = runner.plan_rounds(_cfg(), ENTRY, CASES, only_jobs=jobs)
    assert [(r["name"], r["ctx"], r["only_jobs"]) for r in rounds] == [
        ("A", 40960, {(None, "I2", 1)}), ("B", 73728, {(None, "B1", 1)})]


def test_bank_rows_are_scheduled_first_within_a_round():
    jobs = [(c, 1, None) for c in [_case("I1"), _case("S", "rp", turns=["a"]),
                                   _case("B1", "codebank"), _case("C1", "coding")]]
    assert [j[0]["id"] for j in runner.schedule_jobs(jobs)] == ["B1", "S", "C1", "I1"]


# ----------------------------------------------------- a whole run, faked

def _fake_rig(monkeypatch):
    seen = {"switch": [], "chat": []}
    slots = {40960: 8, 73728: 4}
    cur = {"ctx": None}

    def switch(self, model_id, ctx=None):
        seen["switch"].append(ctx)
        cur["ctx"] = ctx
        return 1.0

    def chat(self, model_id, messages, **kw):
        seen["chat"].append((cur["ctx"], messages[-1]["content"], kw.get("max_tokens")))
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m",
                          completion_tokens=3, total_s=0.1)

    P = providers.Provider
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", switch)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "live_context", lambda self, mid: None)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: None)
    monkeypatch.setattr(P, "loaded_plan_for",
                        lambda self, mid: {"ctx_size": cur["ctx"], "parallel": slots[cur["ctx"]]})
    monkeypatch.setattr(P, "workers", lambda self, mid: slots[cur["ctx"]])
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())
    return seen


def test_a_rig_run_loads_twice_perf_once_and_finishes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch)
    summary = runner.run_models(_cfg(), [ENTRY], CASES)
    assert seen["switch"] == [40960, 73728]
    # every standard row ran at the floor, every bank row in the wide round
    by_ctx = {}
    for ctx, prompt, _ in seen["chat"]:
        by_ctx.setdefault(ctx, []).append(prompt)
    assert sorted(by_ctx[73728]) == ["say ok A1", "say ok B1"]
    assert "say ok P1" in by_ctx[40960] and "say ok I2" in by_ctx[40960]
    rows = config.load_transcripts("m")
    assert len({r["bench_run_id"] for r in rows}) == 1          # one run, two rounds
    assert {r["case_id"]: r["load_ctx"] for r in rows} == {
        "P1": 40960, "I1": 40960, "I2": 40960, "B1": 73728, "A1": 73728}
    assert sum(1 for r in rows if r["category"] == "perf") == 1     # perf in round A only
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["finished"] and meta["failed"] is False
    assert meta["context_length"] == 40960                          # the standard rows'
    assert [(r["round"], r["ctx"], r["workers"]) for r in meta["rounds"]] == [
        ("A", 40960, 8), ("B", 73728, 4)]
    assert summary["m"]["rows"] == 5 and len(summary["m"]["rounds"]) == 2


# ------------------------------------------------------- ladder budgets

def test_a_ladder_budget_is_the_rows_budget_and_widens_its_round():
    cfg = _cfg(thinking_max_tokens_cap=24576, thinking_max_tokens_factor=4)
    lad = _case("MH22", "math", ladder="work/reason#1", ladder_budget=65536)
    assert runner.fixed_budget(cfg, lad) == 65536
    assert runner.wide_context(cfg, [lad]) == 73728
    rounds = runner.plan_rounds(cfg, ENTRY, [_case("I1"), lad])
    assert [(r["name"], r["ctx"]) for r in rounds] == [("A", 40960), ("B", 73728)]
    ctx = runner._Ctx(cfg, ENTRY, providers.get_provider(cfg, "p"), 73728)
    assert runner.row_budget(ctx, lad, 1000) == 65536
    assert runner.row_budget(ctx, _case("I1"), 1000) is None       # board regime
    ctx.rung = 131072
    assert runner.row_budget(ctx, lad, 1000) is None               # the rung decides
    # a set's context (no budget) is a load floor for its round too
    wide = _case("RD3", "rpdeep", ladder="chat/rp#1", min_context=65536)
    assert runner.wide_context(cfg, [wide]) == 65536


def test_a_ladder_row_is_sent_its_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    seen = _fake_rig(monkeypatch)
    cfg = _cfg(thinking_max_tokens_cap=24576, thinking_max_tokens_factor=4)
    lad = _case("MH22", "math", ladder="work/reason#1", ladder_budget=65536)
    runner.run_models(cfg, [ENTRY], [_case("I1"), lad])
    sent = {p: mt for _, p, mt in seen["chat"]}
    assert sent["say ok MH22"] == 65536
    assert sent["say ok I1"] == 256      # 64 x 4 (thinking) — the board regime
