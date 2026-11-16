"""Longest units first (lead 2026-11-15, ship plan C8): the reference's Main took 106 minutes because its five
code-bank calls (35-65k tokens each) were queued behind every chain/flat first attempt and then ran alone.
Dispatch inside one run is ONE list ordered by expected completion tokens, longest first, chain attempts
and static jobs together: the model's own stored mean for that cell when it has one, else the unit's
output ceiling (a chain cell's per-request ``ceiling``; a bank row's ``bank_budget``)."""
from __future__ import annotations

import pytest

from crucibleforge import runner


def _inst(cell, n, ceiling):
    return {"id": f"FLAT-{cell}-{n:03d}-r1-a1", "chain": {"id": f"FLAT-{cell}-{n:03d}", "ceiling": ceiling}}


def _bank(cid):
    return {"id": cid, "category": "codebank"}


CFG = {"defaults": {"bank_budget": 65536}}


def test_bank_jobs_are_not_queued_behind_every_chain_start():
    starts = [_inst("rc2_state_L10", i, 16384) for i in range(1, 4)] + [_inst("rc2_trace4_L4", 1, 71680)]
    jobs = [(_bank("CB-C5-predict-pyhouse"), r, None) for r in (1, 2)]
    order = runner.longest_first(starts, jobs, CFG, prior={})
    ids = [u[1]["id"] for u in order]
    # unmeasured: by ceiling -- trace 71,680, the bank 65,536 x2, then the 16,384 sessions
    assert ids[:3] == ["FLAT-rc2_trace4_L4-001-r1-a1", "CB-C5-predict-pyhouse", "CB-C5-predict-pyhouse"]
    assert [u[0] for u in order[3:]] == ["chain"] * 3


def test_the_model_s_own_stored_mean_wins_over_the_ceiling():
    starts = [_inst("rc2_trace4_L4", 1, 71680), _inst("rc2_implied1_L1", 1, 65535)]
    jobs = [(_bank("CB-C5-predict-pyhouse"), 1, None)]
    prior = {"cell:FLAT-rc2_trace4_L4": 9000.0, "cell:FLAT-rc2_implied1_L1": 30000.0,
             "case:CB-C5-predict-pyhouse": 48000.0}
    ids = [u[1]["id"] for u in runner.longest_first(starts, jobs, CFG, prior=prior)]
    assert ids == ["CB-C5-predict-pyhouse", "FLAT-rc2_implied1_L1-001-r1-a1", "FLAT-rc2_trace4_L4-001-r1-a1"]


def test_ties_keep_bank_first_then_the_old_order():
    starts = [_inst("a", 1, 65536), _inst("b", 1, 65536)]
    jobs = [(_bank("CB-X"), 1, None)]
    ids = [u[1]["id"] for u in runner.longest_first(starts, jobs, CFG, prior={})]
    assert ids == ["CB-X", "FLAT-a-001-r1-a1", "FLAT-b-001-r1-a1"]


def test_prior_from_rows_sums_a_session_s_turns_and_means_over_units():
    rows = [
        # a 3-turn session unit: 1,000 + 2,000 + 3,000 = 6,000 for that unit
        *({"bench_run_id": "x", "case_id": "FLAT-rc2_state_L10-001-r1-a1", "repeat": 1, "turn": t,
           "chain": {"id": "FLAT-rc2_state_L10-001"}, "metrics": {"completion_tokens": n}}
          for t, n in ((1, 1000), (2, 2000), ("final", 3000))),
        {"bench_run_id": "x", "case_id": "FLAT-rc2_state_L10-002-r1-a1", "repeat": 1, "turn": "final",
         "chain": {"id": "FLAT-rc2_state_L10-002"}, "metrics": {"completion_tokens": 2000}},
        {"bench_run_id": "x", "case_id": "CB-C5-predict-pyhouse", "repeat": 1, "metrics": {"completion_tokens": 40000}},
        {"bench_run_id": "x", "case_id": "CB-C5-predict-pyhouse", "repeat": 2, "metrics": {"completion_tokens": 60000}},
        {"bench_run_id": "x", "case_id": "CB-C5-predict-pyhouse", "repeat": 3, "error": "transport",
         "metrics": {"completion_tokens": 1}},                         # an error row is no estimate
    ]
    prior = runner.tokens_prior(rows)
    assert prior["cell:FLAT-rc2_state_L10"] == pytest.approx(4000.0)
    assert prior["case:CB-C5-predict-pyhouse"] == pytest.approx(50000.0)


def test_makespan_on_the_reference_s_rows():
    """The five CB-C5 calls of tonight's reference (completion tokens as stored) and 60 short units on 20
    slots: queued behind the starts they end the run alone (18,000 + 65,536); longest first the run is as
    long as its longest unit."""
    longs = [54654, 46818, 65536, 43894, 34212]
    starts = [_inst("s", i, 16384) for i in range(60)]
    jobs = [(_bank(f"CB-{k}"), 1, None) for k in range(len(longs))]
    prior = {"cell:FLAT-s": 6000.0, **{f"case:CB-{k}": float(t) for k, t in enumerate(longs)}}
    old = [("chain", s) for s in starts] + [("job", *j) for j in jobs]
    new = runner.longest_first(starts, jobs, CFG, prior=prior)
    dur = lambda u: prior[runner.unit_key(u)]   # noqa: E731
    assert runner.makespan(old, 20, dur) == 18000 + 65536
    assert runner.makespan(new, 20, dur) == 65536
