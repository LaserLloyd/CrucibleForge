"""A stored chain row stands in for an instance only when it answered the instance the CURRENT spec
gives (2026-11-14): pair-3 raised session_state's ceiling 2,500 -> 16,384 under the same chain ids and the
resumed reference run skipped all 12 chains on the old 2,500 rows (usable_row checks a row against its
own chain block only)."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import chains, config, profiles


@pytest.fixture(scope="module")
def state_case():
    mp = pytest.MonkeyPatch()
    mp.setenv(profiles.COMPOSITION_ENV, "pair-3")
    try:
        prof = profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))
    finally:
        mp.undo()
    cases = chains.chain_cases(prof)
    return [c for c in cases if "session_state" in c["id"]][0], chains.profile_index(prof)


def _row(case, ceiling=None):
    ch = copy.deepcopy(chains.instance(case, 1, 1)["chain"])
    if ceiling is not None:
        ch["ceiling"] = ceiling
    return {"chain": ch, "chain_revision": chains.revision(ch)}


def test_current_row_is_used(state_case):
    case, idx = state_case
    d = chains.Driver([case])
    r = _row(case)
    assert r["chain"]["ceiling"] == 16384
    assert d._current(r) and chains.in_profile(r, idx)


def test_row_made_under_another_ceiling_is_not_this_instance(state_case):
    case, idx = state_case
    d = chains.Driver([case])
    r = _row(case, ceiling=2500)
    assert r["chain_revision"] == chains.revision(r["chain"])      # self-consistent: usable_row alone keeps it
    assert not d._current(r)
    assert not chains.in_profile(r, idx)


def test_driver_reruns_a_chain_whose_rows_are_from_an_old_spec(state_case, monkeypatch):
    case, _ = state_case
    monkeypatch.setattr(chains, "usable_row", lambda r: True)
    old = _row(case, ceiling=2500)
    monkeypatch.setattr(chains, "results_from_rows",
                        lambda rows: {case["id"]: {(1, 1): True}} if rows else {})
    monkeypatch.setattr(chains, "starts_from_rows", lambda rows: {case["id"]: 1} if rows else {})
    assert len(chains.Driver([case], prior_rows=[old]).start()) == 1
