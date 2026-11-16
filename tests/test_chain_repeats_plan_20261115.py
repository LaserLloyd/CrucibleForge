"""An ended 4.5 chain plans nothing, whatever its category's repeat count (2026-11-15).

The runner drives a chain once (every attempt is repeat 1) and the board scores the chain alone,
so a chain in a category with repeats 2 (know3 sits in `reasoning`) must not be presented again by
``bench_all.missing_cases`` once it has ended. Real repeats of ordinary cases are unchanged."""
from __future__ import annotations

import pytest

from crucibleforge import bench_all, chains, config
from crucibleforge.config import repeats_for
from crucibleforge.profiles import apply_profile, load_profile


def _chain_rows(cid: str, K: int, slip: int, held: int) -> list[dict]:
    """A chain that passed rungs 1..held, then (when held < K) failed the next rung slip+1 times."""
    path = [(r, 1, True) for r in range(1, held + 1)]
    if held < K:
        path += [(held + 1, a, False) for a in range(1, slip + 2)]
    return [{"chain": {"id": cid, "rung": r, "attempt": a, "passed": ok, "K": K, "slip_guard": slip},
             "category": "reasoning", "case_id": f"{cid}-r{r}-a{a}", "repeat": 1,
             "grade": "pass" if ok else "fail"} for r, a, ok in path]


def test_an_ended_chain_is_complete_under_a_two_repeat_category():
    rows = _chain_rows("CHAIN-x-001", 4, 1, 4) + _chain_rows("CHAIN-x-002", 4, 1, 2)
    assert chains.ended_from_rows(rows) == {"CHAIN-x-001", "CHAIN-x-002"}
    ids = ["CHAIN-x-001", "CHAIN-x-002"]
    assert bench_all.missing_cases(rows, ids, repeats={i: 2 for i in ids}) == []


def test_a_halted_chain_is_still_missing_under_two_repeats():
    rows = _chain_rows("CHAIN-x-001", 4, 1, 4) + _chain_rows("CHAIN-x-003", 4, 1, 2)[:2]   # rung 3 never tried
    ids = ["CHAIN-x-001", "CHAIN-x-003"]
    assert bench_all.missing_cases(rows, ids, repeats={i: 2 for i in ids}) == ["CHAIN-x-003"]


def test_real_repeats_of_ordinary_cases_are_unchanged():
    rows = _chain_rows("CHAIN-x-001", 4, 1, 4) + [
        {"case_id": "R1", "repeat": 1, "grade": "pass"},
        {"case_id": "R2", "repeat": 1, "grade": "pass"}, {"case_id": "R2", "repeat": 2, "grade": "fail"}]
    reps = {"CHAIN-x-001": 2, "R1": 2, "R2": 2}
    assert bench_all.missing_cases(rows, ["CHAIN-x-001", "R1", "R2"], repeats=reps) == ["R1"]
    # and with no chain rows at all the rule is the old one
    assert bench_all.missing_cases(rows[-3:], ["R1", "R2"], repeats=reps) == ["R1"]


@pytest.mark.shipping_default   # bench45 as shipped (composition `standard`); conftest otherwise pins `default`
@pytest.mark.live_ladder
def test_the_bench45_know3_chain_plans_nothing_once_ended():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    cfg_p, cases = apply_profile(load_profile("bench45", cfg), cfg)
    know3 = [c for c in cases if c["id"].startswith("FLAT-rc2_know3_")]
    assert len(know3) == 3 and all(c.get("chain_spec") for c in know3)
    repeats = {c["id"]: repeats_for(cfg_p, c["category"], case=c) for c in know3}
    assert set(repeats.values()) == {2}   # the category's count: the reason they used to be planned forever
    rows = []
    for c in know3:
        sp = c["chain_spec"]
        rows += _chain_rows(c["id"], int(sp["K"]), int(sp.get("slip_guard") or 0), int(sp["K"]))
    ids = [c["id"] for c in know3]
    assert chains.ended_from_rows(rows) == set(ids)
    assert bench_all.missing_cases(rows, ids, repeats=repeats) == []
